#!/usr/bin/env python3
"""Install SDD into an existing Git project without overwriting unmanaged files."""
from __future__ import annotations

import argparse
import copy
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


def merge_settings(old, incoming):
    source = json.loads(incoming)
    target = json.loads(old) if old is not None else {}
    if not isinstance(target, dict) or not isinstance(source, dict):
        raise Conflict('Claude settings must be JSON objects')
    if target.get('disableAllHooks') is True:
        raise Conflict('Existing settings disableAllHooks=true; resolve manually')
    # Add rules and hook groups only. Existing values and ordering stay intact.
    def add(dest, src, location):
        for key, value in src.items():
            name = f'{location}.{key}'
            if key not in dest:
                dest[key] = copy.deepcopy(value)
            elif isinstance(value, dict) and isinstance(dest[key], dict):
                add(dest[key], value, name)
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


def plan(source, target):
    raw = read(target, STATE)
    state = json.loads(raw) if raw is not None else {'schema': 1, 'files': {}, 'blocks': {}}
    if not isinstance(state, dict) or state.get('schema') != 1 or not isinstance(state.get('files'), dict) or not isinstance(state.get('blocks'), dict):
        raise Conflict('Invalid installer manifest; inspect manually')
    changes, conflicts = {}, []
    new = copy.deepcopy(state)
    def put(name, data, old):
        if old != data:
            changes[name] = (old, data)
    for name in source_files(source):
        data, old = read(source, name), read(target, name)
        if old is not None and old != data and state['files'].get(name) != sha(old):
            conflicts.append(name)
            continue
        put(name, data, old)
        new['files'][name] = sha(data)
    if conflicts:
        raise Conflict('Unmanaged or locally modified files; no changes applied:\n  ' + '\n  '.join(conflicts))
    for name, marker in (('CLAUDE.md', 'RULES'), ('.gitignore', 'IGNORE')):
        old = read(target, name)
        data = read(source, 'installer-gitignore.txt' if name == '.gitignore' else name)
        merged, digest = block_merge(old, data, marker, state['blocks'].get(marker))
        put(name, merged, old)
        new['blocks'][marker] = digest
    name = '.claude/settings.json'
    old = read(target, name)
    put(name, merge_settings(old, read(source, name)), old)
    put(STATE, encode_json(new), raw)
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
            atomic_write(safe_path(target, name), data)
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
    args = p.parse_args(argv)
    try:
        result = subprocess.run(['git', '-C', str(Path(args.target).resolve()), 'rev-parse', '--show-toplevel'], capture_output=True, text=True, check=True)
        target = Path(result.stdout.strip()).resolve()
        if target == SOURCE or SOURCE.is_relative_to(target) or target.is_relative_to(SOURCE):
            raise Conflict('Use a separate Java Git repository as the target')
        changes = plan(SOURCE, target)
        print(f'Target: {target}')
        for name, (old, _) in changes.items():
            print(('ADD ' if old is None else 'UPDATE ') + name)
        if args.dry_run:
            print(f'DRY RUN: {len(changes)} file(s); nothing written')
            return 0
        apply(target, changes)
        print(f'Installed: {len(changes)} changed file(s). No commit, push or dependency download performed.')
        print('Review git status/diff. Start Claude Code at the Git root; run /project-setup, then /tool-setup.')
        return 0
    except (Conflict, OSError, ValueError, TypeError, KeyError, subprocess.CalledProcessError) as ex:
        print(f'INSTALL BLOCKED: {ex}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
