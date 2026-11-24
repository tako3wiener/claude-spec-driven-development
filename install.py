#!/usr/bin/env python3
"""Install SDD into an existing Git project without overwriting unmanaged files."""
from __future__ import annotations

import argparse
import copy
import re
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

SOURCE = Path(__file__).resolve().parent
STATE = '.claude/sdd-install.json'
FOLDERS = ('.claude/agents', '.claude/skills', 'scripts', 'templates', 'docs/ai', 'examples/playwright-java')
ROOT_FILES = ('CLAUDE.md', '.claude/settings.json', '.gitignore')


class Conflict(Exception):
    pass


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encode_json(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode('utf-8')


def safe_path(root, name):
    rel = Path(name)
    if rel.is_absolute() or '..' in rel.parts:
        raise Conflict(f'Unsafe path: {name}')
    path = root
    for part in rel.parts:
        path = path / part
        if path.is_symlink():
            raise Conflict(f'Symlink refused: {path}')
        if path != root / rel and path.exists() and not path.is_dir():
            raise Conflict(f'Parent is not a directory: {path}')
    if path.exists() and not path.is_file():
        raise Conflict(f'Not a regular file: {path}')
    return path


def read(root, name):
    path = safe_path(root, name)
    return path.read_bytes() if path.exists() else None


def block_merge(old, incoming, marker, previous):
    before, after = (f'<!-- SDD {marker} BEGIN -->', f'<!-- SDD {marker} END -->') if marker == 'RULES' else (f'# SDD {marker} BEGIN', f'# SDD {marker} END')
    body = incoming.decode('utf-8').rstrip('\n') + '\n'
    text = (old or b'').decode('utf-8')
    if before in text or after in text:
        if text.count(before) != 1 or text.count(after) != 1:
            raise Conflict(f'Invalid SDD {marker} block')
        start, end = text.index(before), text.index(after)
        if end < start or not text.startswith(before + '\n', start) or text[end-1:end] != '\n':
            raise Conflict(f'Invalid SDD {marker} block')
        existing = text[start+len(before)+1:end]
        if existing != body and (not previous or sha(existing.encode('utf-8')) != previous):
            raise Conflict(f'SDD {marker} block has local edits; merge manually')
        text = text[:start] + before + '\n' + body + after + text[end+len(after):]
    else:
        if text.rstrip('\n') == body.rstrip('\n'):
            text = ''
        text = (text + ('\n' if text.endswith('\n') else '\n\n') if text else '') + before + '\n' + body + after + '\n'
    return text.encode('utf-8'), sha(body.encode('utf-8'))


def merge_settings(old, incoming, delta=None):
    source = json.loads(incoming)
    target = json.loads(old) if old is not None else {}
    if not isinstance(target, dict) or not isinstance(source, dict):
        raise Conflict('Claude settings must be JSON objects')
    if target.get('disableAllHooks') is True:
        raise Conflict('Existing settings disableAllHooks=true; resolve manually')
    # Keep only the additions supplied by SDD, never copy unrelated settings into state.
    if delta is None:
        delta = {'items': [], 'containers': []}
    def add(dest, src, location, prefix=()):
        for key, value in src.items():
            name = f'{location}.{key}'
            field = prefix + (key,)
            if key not in dest:
                if isinstance(value, (dict, list)):
                    dest[key] = {} if isinstance(value, dict) else []
                    entry = list(field)
                    if entry not in delta['containers']:
                        delta['containers'].append(entry)
                else:
                    dest[key] = copy.deepcopy(value)
                    delta['items'].append({'path': list(field), 'kind': 'value', 'value': value})
                    continue
            if isinstance(value, dict) and isinstance(dest[key], dict):
                add(dest[key], value, name, field)
            elif isinstance(value, list) and isinstance(dest[key], list):
                for item in value:
                    if item in dest[key]:
                        continue
                    if location == 'settings.hooks':
                        def commands(group):
                            return {(h.get('command'), tuple(h.get('args', []))) for h in group.get('hooks', []) if isinstance(h, dict)} if isinstance(group, dict) else set()
                        if any(commands(item) & commands(group) for group in dest[key]):
                            raise Conflict(f'Existing SDD hook differs: {name}; merge manually')
                    dest[key].append(copy.deepcopy(item))
                    record = {'path': list(field), 'kind': 'list', 'value': copy.deepcopy(item)}
                    if record not in delta['items']:
                        delta['items'].append(record)
            elif dest[key] != value:
                raise Conflict(f'Conflicting Claude setting: {name}')
    add(target, source, 'settings')
    return old if old is not None and target == json.loads(old) else encode_json(target)


def source_files(source):
    for folder in FOLDERS:
        directory = source / folder
        if not directory.is_dir():
            raise Conflict(f'Installer package incomplete: {folder}')
        for path in sorted(directory.rglob('*')):
            rel = path.relative_to(source)
            if any(p in {'__pycache__', '.pytest_cache', 'target', 'build', '.gradle', 'node_modules', '.git', 'local'} for p in rel.parts) or path.suffix in {'.pyc', '.pem', '.key', '.sig'}:
                continue
            if path.is_symlink():
                raise Conflict(f'Package symlink refused: {rel}')
            if path.is_file():
                yield rel.as_posix()


def empty_state():
    return {'schema': 2, 'files': {}, 'blocks': {}, 'rules': None,
            'settings': {'items': [], 'containers': [], 'existed': False}}


def valid_managed(name):
    p = Path(name)
    return (not p.is_absolute() and '..' not in p.parts and
            any(p.is_relative_to(Path(folder)) and p != Path(folder) for folder in FOLDERS) and
            not any(x in {'.git', 'local', 'target', 'build', '__pycache__', '.gradle', 'node_modules', '.pytest_cache'} for x in p.parts) and
            p.suffix not in {'.pem', '.key', '.sig', '.pyc'} and
            not any(x == '.env' or x.startswith('.env.') for x in p.parts))


def get_state(raw):
    state = json.loads(raw)
    if (not isinstance(state, dict) or state.get('schema') not in (1, 2) or
            not isinstance(state.get('files'), dict) or not isinstance(state.get('blocks'), dict)):
        raise Conflict('Invalid installer manifest; inspect manually')
    for name, digest in state['files'].items():
        if not valid_managed(name) or not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
            raise Conflict(f'Unsafe managed file in manifest: {name}')
    if state['schema'] == 2 and (not isinstance(state.get('settings'), dict) or 'rules' not in state):
        raise Conflict('Incomplete installer manifest')
    return state


def rules_metadata(old, incoming):
    text = (old or b'').decode('utf-8')
    if '<!-- SDD RULES BEGIN -->' in text or '<!-- SDD RULES END -->' in text:
        raise Conflict('Existing SDD RULES block has no ownership record; merge manually')
    body = incoming.decode('utf-8').rstrip('\n') + '\n'
    same = bool(text) and text.rstrip('\n') == body.rstrip('\n')
    return {'existed': old is not None, 'same_as_body': same,
            'original_sha': sha(old) if old is not None else None,
            'trailing_newlines': len(text) - len(text.rstrip('\n')) if same else 0,
            'separator': '' if not text or same else ('\n' if text.endswith('\n') else '\n\n')}


def git_baseline(target, baseline):
    if not baseline:
        raise Conflict('Legacy v5.3.1 manifest: specify --baseline PRE_INSTALL_COMMIT from a clean pre-install worktree; ownership was not recorded')
    commit = subprocess.run(['git', '-C', str(target), 'rev-parse', '--verify', '--end-of-options', baseline+'^{commit}'],
                            check=True, capture_output=True, text=True).stdout.strip()
    subprocess.run(['git', '-C', str(target), 'merge-base', '--is-ancestor', commit, 'HEAD'], check=True, capture_output=True)
    def original(name):
        r = subprocess.run(['git', '-C', str(target), 'ls-tree', commit, '--', name], check=True, capture_output=True)
        if not r.stdout:
            return None
        if not r.stdout.startswith((b'100644 blob ', b'100755 blob ')):
            raise Conflict(f'Non-regular pre-install file: {name}')
        return subprocess.run(['git', '-C', str(target), 'show', commit+':'+name], check=True, capture_output=True).stdout
    return original


def migrate_legacy(source, target, state, baseline):
    original = git_baseline(target, baseline)
    new = empty_state()
    new['blocks'] = copy.deepcopy(state['blocks'])
    for name, digest in state['files'].items():
        old = original(name)
        if old is None:
            new['files'][name] = digest
        elif sha(old) != digest:
            raise Conflict(f'Legacy file ownership uncertain: {name}; resolve manually')
    rules = read(source, 'CLAUDE.md')
    if sha(rules.decode('utf-8').rstrip('\n').encode('utf-8') + b'\n') != state['blocks'].get('RULES'):
        raise Conflict('Legacy CLAUDE.md differs from this starter revision; use matching version or manual removal')
    new['rules'] = rules_metadata(original('CLAUDE.md'), rules)
    old_settings = original('.claude/settings.json')
    new['settings']['existed'] = old_settings is not None
    merge_settings(old_settings, read(source, '.claude/settings.json'), new['settings'])
    return new


def plan(source, target, baseline=None):
    raw = read(target, STATE)
    state = get_state(raw) if raw is not None else empty_state()
    if state['schema'] == 1:
        state = migrate_legacy(source, target, state, baseline)
    changes, conflicts = {}, []
    new = copy.deepcopy(state)
    def put(name, data, old):
        if old != data:
            changes[name] = (old, data)
    for name in source_files(source):
        if not valid_managed(name):
            raise Conflict(f'Unexpected installer file: {name}')
        data, old = read(source, name), read(target, name)
        if old is not None and old != data and state['files'].get(name) != sha(old):
            conflicts.append(name)
            continue
        put(name, data, old)
        # An identical pre-existing file is shared, not owned by the installer.
        if old is None or name in state['files']:
            new['files'][name] = sha(data)
    if conflicts:
        raise Conflict('Unmanaged or locally modified files; no changes applied:\n  ' + '\n  '.join(conflicts))
    for name, marker in (('CLAUDE.md', 'RULES'), ('.gitignore', 'IGNORE')):
        old = read(target, name)
        data = read(source, 'installer-gitignore.txt' if name == '.gitignore' else name)
        if marker == 'RULES' and new['rules'] is None:
            new['rules'] = rules_metadata(old, data)
        # An original identical CLAUDE.md was collapsed into the managed block.
        # Do not lose the original if a future version changes the block body.
        if marker == 'RULES' and new['rules']['same_as_body'] and state['blocks'].get(marker):
            if sha(data.decode('utf-8').rstrip('\n').encode('utf-8') + b'\n') != state['blocks'][marker]:
                raise Conflict('Pre-existing identical CLAUDE.md would be changed; merge manually')
        merged, digest = block_merge(old, data, marker, state['blocks'].get(marker))
        put(name, merged, old)
        new['blocks'][marker] = digest
    name = '.claude/settings.json'
    old = read(target, name)
    if raw is None:
        new['settings']['existed'] = old is not None
    put(name, merge_settings(old, read(source, name), new['settings']), old)
    put(STATE, encode_json(new), raw)
    return changes


def remove_rules(old, state):
    if old is None:
        return None
    meta = state['rules']
    if not isinstance(meta, dict):
        raise Conflict('Missing CLAUDE.md ownership data')
    text = old.decode('utf-8')
    before, after = '<!-- SDD RULES BEGIN -->', '<!-- SDD RULES END -->'
    if text.count(before) != 1 or text.count(after) != 1:
        raise Conflict('CLAUDE.md managed block missing or ambiguous; inspect manually')
    start, end = text.index(before), text.index(after)
    if end < start or not text.startswith(before+'\n', start) or text[end-1:end] != '\n':
        raise Conflict('Invalid CLAUDE.md managed block')
    body = text[start+len(before)+1:end]
    if sha(body.encode('utf-8')) != state['blocks'].get('RULES'):
        raise Conflict('CLAUDE.md managed block was edited; no removal')
    prefix, suffix = text[:start], text[end+len(after):]
    if suffix.startswith('\n'):
        suffix = suffix[1:]
    if meta['same_as_body']:
        original = body.rstrip('\n') + '\n' * meta['trailing_newlines']
        if sha(original.encode('utf-8')) != meta['original_sha']:
            raise Conflict('Original CLAUDE.md cannot be restored safely')
        result = prefix + original + (('\n' if not original.endswith('\n') else '') + suffix if suffix else '')
    else:
        sep = meta['separator']
        if sep and prefix.endswith(sep):
            prefix = prefix[:-len(sep)]
        if prefix and suffix and not prefix.endswith('\n'):
            prefix += '\n'
        result = prefix + suffix
    return result.encode('utf-8') if result or meta['existed'] else None


def remove_settings(old, delta):
    if old is None:
        return None
    current = json.loads(old)
    if not isinstance(current, dict) or not isinstance(delta, dict):
        raise Conflict('Invalid settings or ownership data')
    original = copy.deepcopy(current)
    def parent(path):
        if not isinstance(path, list) or not path or path[0] not in ('permissions', 'hooks') or not all(isinstance(x, str) and x for x in path):
            raise Conflict('Unsafe settings ownership path')
        node = current
        for key in path[:-1]:
            if key not in node:
                return None, path[-1]
            node = node[key]
            if not isinstance(node, dict):
                raise Conflict('Existing settings structure changed; inspect manually')
        return node, path[-1]
    for item in reversed(delta['items']):
        node, key = parent(item['path'])
        if node is None or key not in node:
            continue
        value = item['value']
        if item['kind'] == 'list':
            if not isinstance(node[key], list):
                raise Conflict('Settings list changed type')
            if value in node[key]:
                node[key].remove(value)
            elif item['path'][0] == 'hooks':
                # A modified hook may still reference SDD scripts. Do not leave it dangling.
                raise Conflict('An installed hook was edited or removed; inspect manually')
        elif item['kind'] == 'value' and node[key] == value:
            del node[key]
        else:
            raise Conflict('An installed setting was edited; inspect manually')
    for path in sorted(delta['containers'], key=len, reverse=True):
        node, key = parent(path)
        if node is not None and key in node and node[key] in ({}, []):
            del node[key]
    if not current and not delta['existed']:
        return None
    return old if current == original else encode_json(current)


def plan_uninstall(source, target, baseline=None):
    raw = read(target, STATE)
    if raw is None:
        return {}
    state = get_state(raw)
    if state['schema'] == 1:
        state = migrate_legacy(source, target, state, baseline)
    changes = {}
    for name, digest in state['files'].items():
        old = read(target, name)
        if old is not None:
            if sha(old) != digest:
                raise Conflict(f'Managed file has local edits: {name}; no changes applied')
            changes[name] = (old, None)
    for name, fn in (('CLAUDE.md', lambda old: remove_rules(old, state)),
                     ('.claude/settings.json', lambda old: remove_settings(old, state['settings']))):
        old = read(target, name)
        new = fn(old)
        if name == '.claude/settings.json' and new is not None:
            hooks = json.dumps(json.loads(new).get('hooks', {}), ensure_ascii=False)
            if any(path in hooks for path in changes):
                raise Conflict('An existing hook still references files scheduled for removal; resolve manually')
        if old != new:
            changes[name] = (old, new)
    # Retain .gitignore (including custom artifact exclusions) to avoid exposing
    # retained local configuration, credentials or evidence in future commits.
    changes[STATE] = (raw, None)
    return changes


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.sdd-install-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
        if path.exists():
            os.chmod(tmp, path.stat().st_mode & 0o777)
        else:
            os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def apply(target, changes):
    # Preflight every destination again; roll back completed writes on I/O errors.
    for name, (old, _) in changes.items():
        if read(target, name) != old:
            raise Conflict(f'File changed during planning: {name}')
    done = []
    try:
        for name, (old, data) in changes.items():
            path = safe_path(target, name)
            if data is None:
                path.unlink()
            else:
                atomic_write(path, data)
            done.append(name)
    except (OSError, Conflict):
        for name in reversed(done):
            path = safe_path(target, name)
            old = changes[name][0]
            if old is None:
                path.unlink()
            else:
                atomic_write(path, old)
        raise


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--target', default='.', help='existing Git project (default: current directory)')
    p.add_argument('--dry-run', action='store_true', help='show planned changes without writing')
    p.add_argument('--uninstall', action='store_true', help='remove only owned SDD files and settings')
    p.add_argument('--baseline', help='clean pre-install Git commit, required for legacy schema 1')
    args = p.parse_args(argv)
    try:
        result = subprocess.run(['git', '-C', str(Path(args.target).resolve()), 'rev-parse', '--show-toplevel'], capture_output=True, text=True, check=True)
        target = Path(result.stdout.strip()).resolve()
        if target == SOURCE or SOURCE.is_relative_to(target) or target.is_relative_to(SOURCE):
            raise Conflict('Use a separate Java Git repository as the target')
        changes = plan_uninstall(SOURCE, target, args.baseline) if args.uninstall else plan(SOURCE, target, args.baseline)
        print(f'Target: {target}')
        for name, (old, data) in changes.items():
            print(('REMOVE ' if data is None else 'ADD ' if old is None else 'UPDATE ') + name)
        if args.dry_run:
            print(f'DRY RUN: {len(changes)} file(s); nothing written')
            return 0
        apply(target, changes)
        if args.uninstall:
            if not changes:
                print('No managed installation found; no files removed. Manual installations require manual removal.')
                return 0
            print(f'Uninstalled: {len(changes)} changed file(s). Local configuration, evidence and .gitignore retained.')
            print('Review git status/diff and restart Claude Code. No commit or push performed.')
        else:
            print(f'Installed: {len(changes)} changed file(s). No commit, push or dependency download performed.')
            print('Review git status/diff. Start Claude Code at the Git root; run /project-setup, then /tool-setup.')
        return 0
    except (Conflict, OSError, ValueError, TypeError, KeyError, subprocess.CalledProcessError) as ex:
        print(f"{'UNINSTALL' if args.uninstall else 'INSTALL'} BLOCKED: {ex}", file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
