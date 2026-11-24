#!/usr/bin/env python3
"""Install SDD independently of a Java repository and launch it against a selected Git root."""
from __future__ import annotations
import argparse
import copy
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
from install import Conflict, safe_path, read, sha, encode_json, source_files, atomic_write
import group_work
from scripts import group_gate

SOURCE = Path(__file__).resolve().parent
STATE = '.sdd-install.json'
PROFILES = '.sdd-workspaces.json'
DEFAULT_INSTALL = Path.home() / '.local/share/sdd'
EXTRA = ('CLAUDE.md', '.claude/settings.json', 'README.md', 'installer-gitignore.txt',
         'install.py', 'standalone.py', 'group_work.py', 'install.sh', 'uninstall.sh', 'sdd',
         'package.json', 'bin/sdd-install.js', 'bin/sdd-uninstall.js')
PROFILE_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z')
CHANGE_ID = re.compile(r'CHG-[0-9]+\Z')
SCRIPTS = {'project_setup.py', 'toolchain.py', 'approval_gate.py', 'run_e2e.py',
           'run_java_tests.py', 'progress.py', 'check_traceability.py', 'check_e2e_evidence.py',
           'build_e2e_index.py', 'prune_artifacts.py', 'model_probe.py'}


def git(root, *args, check=True):
    return subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True, check=check)


def git_root(path):
    p = Path(path).expanduser().resolve(strict=True)
    if not p.is_dir():
        raise Conflict(f'Not a directory: {p}')
    result = git(p, 'rev-parse', '--show-toplevel')
    return Path(result.stdout.strip()).resolve(strict=True)


def install_root(path):
    p = Path(path).expanduser().absolute()
    if p.is_symlink():
        raise Conflict('Installation directory may not be a symlink')
    p = p.resolve()
    if p == SOURCE or p.is_relative_to(SOURCE) or SOURCE.is_relative_to(p):
        raise Conflict('Choose an independent installation directory, outside the source package')
    if p.exists() and not p.is_dir():
        raise Conflict('Installation path is not a directory')
    existing = p
    while not existing.exists():
        existing = existing.parent
    if git(existing, 'rev-parse', '--is-inside-work-tree', check=False).returncode == 0:
        raise Conflict('Install outside Git working trees; choose a dedicated directory')
    return p


def allowed_name(name):
    from install import FOLDERS
    p = Path(name)
    if p.is_absolute() or '..' in p.parts or any(x in {'.git', 'local', '__pycache__', 'target', 'build', 'node_modules'} for x in p.parts):
        return False
    return name in EXTRA or any(p != Path(f) and p.is_relative_to(Path(f)) for f in FOLDERS)


def load_state(target):
    raw = read(target, STATE)
    if raw is None:
        return None, None
    obj = json.loads(raw)
    if not isinstance(obj, dict) or obj.get('schema') != 1 or obj.get('kind') != 'standalone' or not isinstance(obj.get('files'), dict):
        raise Conflict('Invalid standalone manifest')
    if any(not isinstance(n, str) or not allowed_name(n) or not isinstance(h, str) or not re.fullmatch('[0-9a-f]{64}', h) for n,h in obj['files'].items()):
        raise Conflict('Invalid managed file in manifest')
    return raw, obj


def install_plan(source, target):
    raw, previous = load_state(target)
    if previous is None and target.exists():
        # Reinstall after uninstall: profile and empty directory scaffolding are retained.
        for child in target.rglob('*'):
            if child.is_symlink() or (child.is_file() and child not in (target/PROFILES, target/group_gate.GROUPS)):
                raise Conflict('Installation directory is not empty and has no SDD manifest')
    previous = previous or {'files': {}}
    files = list(source_files(source)) + list(EXTRA)
    changes, owned = {}, dict(previous['files'])
    for name in sorted(set(files)):
        if not allowed_name(name):
            raise Conflict(f'Unexpected source file: {name}')
        data, old = read(source, name), read(target, name)
        if data is None:
            raise Conflict(f'Incomplete installation package: {name}')
        if old is not None and (name not in owned or sha(old) != owned[name]):
            raise Conflict(f'Unmanaged or edited file: {name}; no changes applied')
        if old != data:
            changes[name] = (old, data)
        owned[name] = sha(data)
    manifest = encode_json({'schema':1,'kind':'standalone','files':owned})
    if manifest != raw:
        changes[STATE] = (raw, manifest)
    return changes


def uninstall_plan(target):
    raw, state = load_state(target)
    if state is None:
        raise Conflict('Standalone manifest not found; no files removed')
    changes = {}
    for name, digest in state['files'].items():
        old = read(target, name)
        if old is None:
            continue
        if sha(old) != digest:
            raise Conflict(f'Locally modified file: {name}; no files removed')
        changes[name] = (old, None)
    # Profiles are user data. Never remove them automatically.
    changes[STATE] = (raw, None)
    return changes


def apply_changes(target, changes):
    for name,(old,_) in changes.items():
        if read(target,name) != old:
            raise Conflict(f'Concurrent change: {name}')
    done=[]
    try:
        for name,(old,data) in changes.items():
            path=safe_path(target,name)
            if data is None:
                path.unlink()
            else:
                atomic_write(path,data)
                if name in ('sdd','install.sh','uninstall.sh','bin/sdd-install.js','bin/sdd-uninstall.js'):
                    path.chmod(path.stat().st_mode | 0o100)
            done.append(name)
    except (OSError,Conflict):
        for name in reversed(done):
            path=safe_path(target,name)
            old=changes[name][0]
            if old is None:
                path.unlink()
            else:
                atomic_write(path,old)
        raise


def profiles():
    raw=read(SOURCE,PROFILES)
    data=json.loads(raw) if raw is not None else {'schema':1,'profiles':{}}
    if not isinstance(data,dict) or data.get('schema')!=1 or not isinstance(data.get('profiles'),dict):
        raise Conflict('Invalid workspace profiles')
    return data


def selected(name):
    if not PROFILE_NAME.fullmatch(name):
        raise Conflict('Invalid profile name')
    cfg=profiles()['profiles'].get(name)
    if not isinstance(cfg,dict) or cfg.get('execution') not in ('local','devcontainer') or not isinstance(cfg.get('codebase'),str):
        raise Conflict(f'Unknown profile {name}; run configure first')
    root=git_root(cfg['codebase'])
    if str(root)!=cfg['codebase'] or root==SOURCE or root.is_relative_to(SOURCE) or SOURCE.is_relative_to(root):
        raise Conflict('Workspace changed or overlaps with tool installation')
    return root,cfg


def configure(args):
    if not PROFILE_NAME.fullmatch(args.profile):
        raise Conflict('Invalid profile name')
    root=git_root(args.codebase)
    if root==SOURCE or root.is_relative_to(SOURCE) or SOURCE.is_relative_to(root):
        raise Conflict('Codebase and tool installation must be separate')
    data=profiles()
    if args.profile in data['profiles'] and not args.replace:
        raise Conflict('Profile exists; specify --replace to change it')
    data['profiles'][args.profile]={'codebase':str(root),'execution':args.execution}
    atomic_write(safe_path(SOURCE,PROFILES),encode_json(data))
    print(f'Configured {args.profile}: {root} ({args.execution})')


def protect_local(root):
    # Never write through project-controlled symlinks or already-tracked private data.
    for name in ('.claude/local/project-env.json','.claude/local/approvals/placeholder.json',
                 '.claude/local/progress/placeholder.json','.claude/local/sdd-session.lock'):
        safe_path(root,name)
    if git(root,'ls-files','--','.claude/local').stdout.strip():
        raise Conflict('.claude/local contains tracked files; resolve before use')
    # Use repository-local exclude, not a tracked file, before G0 progress/approvals.
    if git(root,'check-ignore','--no-index','.claude/local/project-env.json',check=False).returncode != 0:
        path=Path(git(root,'rev-parse','--git-path','info/exclude').stdout.strip())
        if not path.is_absolute():path=root/path
        if path.is_symlink():raise Conflict('Git exclude is a symlink')
        old=path.read_bytes() if path.exists() else b''
        atomic_write(path,old.rstrip(b'\n')+b'\n/.claude/local/\n')
        if git(root,'check-ignore','--no-index','.claude/local/project-env.json',check=False).returncode != 0:
            raise Conflict('Could not ignore private state; inspect Git ignore rules')
    for name in ('.claude/local/project-env.json','.claude/local/approvals/placeholder.json','.claude/local/progress/placeholder.json','.claude/local/sdd-session.lock'):
        if git(root,'check-ignore','--no-index',name,check=False).returncode != 0:
            raise Conflict('Private state would not be Git-ignored: '+name)
    if git(root,'ls-files','--others','--exclude-standard','--','.claude/local').stdout.strip():
        raise Conflict('Existing private state is not ignored; inspect ignore rules')


def reject_legacy(root):
    # A legacy in-project install has duplicate skills and hooks. Do not mix modes.
    if (root/'.claude/sdd-install.json').exists() or (root/'scripts/guard_test_commands.py').exists():
        raise Conflict('Legacy SDD files detected in codebase; uninstall the project-local kit first')
    for settings in ('.claude/settings.json','.claude/settings.local.json'):
        data=read(root,settings)
        if data is not None:
            obj=json.loads(data)
            if not isinstance(obj,dict) or obj.get('disableAllHooks') is True:
                raise Conflict(f'Existing settings disable hooks or are invalid: {settings}')
    for d in ('.claude/skills/project-setup','.claude/skills/tool-setup','.claude/skills/spec-change'):
        if (root/d).exists():raise Conflict(f'Conflicting project skill: {d}')
    for fname in ('spec-analyst','test-designer','java-developer','e2e-runner','independent-reviewer'):
        if (root/f'.claude/agents/{fname}.md').exists():raise Conflict(f'Conflicting project agent: {fname}')


def session_env(root, execution, change_id=None):
    env=dict(os.environ,SDD_PROJECT_ROOT=str(root),SDD_EXECUTION_CONTEXT=execution,PYTHONDONTWRITEBYTECODE='1')
    env.pop('SDD_CHANGE_ID',None)
    env.pop('SDD_GROUP_ID',None)
    if change_id:
        if not CHANGE_ID.fullmatch(change_id):raise Conflict('Invalid change ID')
        env['SDD_CHANGE_ID']=change_id
    return env


def rewrite(text):
    # Keep project paths (docs/spec, docs/changes, src, reports) relative to Git root.
    # Quote external Python script arguments for Bash, even if install path contains spaces.
    def script(m):return shlex.quote(str(SOURCE/m.group(0)))
    text=re.sub(r'(?<![\w/])scripts/[A-Za-z0-9_./-]+\.py\b',script,text)
    text=re.sub(r'(?<![\w/])docs/ai/[A-Za-z0-9_./-]+',lambda m:str(SOURCE/m.group(0)),text)
    text=re.sub(r'(?<![\w/])templates/[A-Za-z0-9_./*-]+',lambda m:str(SOURCE/m.group(0)),text)
    text=text.replace('`SDD_CHANGE_ID`を変更IDへ設定する（承認証明ではない）', '変更IDは `sdd run --change-id` の起動時に設定済みであることを確認する（承認証明ではない）')
    return text


def runtime_bundle(directory,root,profile,execution,change_id=None,group=None):
    directory=Path(directory)
    for folder in ('.claude/skills','.claude/agents'):
        for path in sorted((SOURCE/folder).rglob('*.md')):
            dest=directory/path.relative_to(SOURCE)
            dest.parent.mkdir(parents=True,exist_ok=True)
            dest.write_text(rewrite(path.read_text(encoding='utf-8')),encoding='utf-8')
    cfg=json.loads((SOURCE/'.claude/settings.json').read_text(encoding='utf-8'))
    cfg.setdefault('permissions',{})['additionalDirectories']=[str(SOURCE)]
    cfg.setdefault('env',{}).update(SDD_PROJECT_ROOT=str(root),SDD_EXECUTION_CONTEXT=execution,PYTHONDONTWRITEBYTECODE='1')
    cfg['env']['SDD_CHANGE_ID']=change_id or ''
    if group is not None:cfg['env']['SDD_GROUP_ID']=change_id
    for groups in cfg.get('hooks',{}).values():
        for hook_group in groups:
            for hook in hook_group.get('hooks',[]):
                hook['args']=[a.replace('${CLAUDE_PROJECT_DIR}/scripts/',str(SOURCE/'scripts')+'/') for a in hook.get('args',[])]
    settings=directory/'settings.json';settings.write_text(json.dumps(cfg,ensure_ascii=False),encoding='utf-8')
    instructions=directory/'rules.md'
    header=(f'SDD standalone session. Git working directory: {root}. Tool directory: {SOURCE}. '
            f'Profile: {profile}; execution: {execution}. Do not change repository or execution environment. '
            'Tool scripts, templates and docs/ai are in the tool directory, not the Java repository. '
            'Translate relative tool references in documentation into absolute tool paths; keep docs/spec, docs/changes, '
            'local state and test evidence relative to the selected Git root. Do not edit the tool installation. '
            'Start change sessions with --change-id; do not set or override SDD_PROJECT_ROOT in Bash. '
            'Human approvals must use sdd exec in a separate terminal, never Claude.\n\n')
    if group is not None:
        header += ('MULTI-CODEBASE CHANGE '+change_id+'; edit ONLY '+str(root)+'. Other codebases are read-only references. '
                   'Use separate sdd group run sessions for edits in other repositories. All G2 plans must be approved together. '
                   'Tell the human to run sdd group approve-g2; do not run record-g2 per repository. '
                   'Coordinate API/DB contracts and integration order. Do not claim cross-system PASS from individual PASS.\n'
                   +json.dumps(group['members'],ensure_ascii=False)+'\nCommon plan (review current group status after any change):\n'+group['plan']+'\n\n')
    instructions.write_text(header+rewrite((SOURCE/'CLAUDE.md').read_text(encoding='utf-8')),encoding='utf-8')
    return settings,instructions


def session_group(profile, change_id, root, cfg):
    if not change_id:return None
    group=group_gate.group_for(SOURCE,change_id)
    if group is not None:
        if group['members'].get(profile)!=cfg:
            raise Conflict('Profile is not a member of the selected change group')
        for name,member in group['members'].items():
            peer, current=selected(name)
            if current!=member:raise Conflict('Group profile changed: '+name)
            reject_legacy(peer)
    return group


@contextmanager
def repository_lock(root):
    """Only one Claude session per physical Git checkout (WSL/Linux/Windows)."""
    path=safe_path(root,'.claude/local/sdd-session.lock')
    path.parent.mkdir(parents=True,exist_ok=True)
    flags=os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0)
    fd=os.open(path,flags,0o600)
    locked=False
    try:
        if os.name=='nt':
            import msvcrt
            if os.fstat(fd).st_size==0:os.write(fd,b'0')
            os.lseek(fd,0,os.SEEK_SET)
            try:msvcrt.locking(fd,msvcrt.LK_NBLCK,1);locked=True
            except OSError as ex:raise Conflict('Another Claude session is active in this codebase') from ex
        else:
            import fcntl
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);locked=True
            except BlockingIOError as ex:raise Conflict('Another Claude session is active in this codebase') from ex
        yield
    finally:
        if locked:
            if os.name=='nt':
                os.lseek(fd,0,os.SEEK_SET);msvcrt.locking(fd,msvcrt.LK_UNLCK,1)
            else:fcntl.flock(fd,fcntl.LOCK_UN)
        os.close(fd)


def run(args):
    root,cfg=selected(args.profile)
    reject_legacy(root)
    group=session_group(args.profile,args.change_id,root,cfg)
    env=session_env(root,cfg['execution'],args.change_id)
    peers=[]
    if group is not None:
        env['SDD_GROUP_ID']=args.change_id
        peers=[m['codebase'] for n,m in group['members'].items() if n!=args.profile]
    if args.dry_run:
        print(json.dumps({'tool_root':str(SOURCE),'git_root':str(root),'execution':cfg['execution'],
                          'change_id':args.change_id,'group':bool(group),'read_only_peers':peers,
                          'launch':'claude --add-dir <session bundle> <peer paths> --settings <session settings> --append-system-prompt-file <rules>'},ensure_ascii=False,indent=2));return 0
    if not shutil.which('claude'):raise Conflict('Claude Code CLI is not installed in this execution environment')
    protect_local(root)
    with repository_lock(root), tempfile.TemporaryDirectory(prefix='sdd-session-') as tmp:
        settings,rules=runtime_bundle(tmp,root,args.profile,cfg['execution'],args.change_id,group=group)
        cmd=['claude','--add-dir',tmp,*peers,'--settings',str(settings),'--append-system-prompt-file',str(rules)]
        if args.model:cmd+=['--model',args.model]
        return subprocess.run(cmd,cwd=root,env=env,check=False).returncode


def execute(args):
    root,cfg=selected(args.profile)
    reject_legacy(root)
    if args.script not in SCRIPTS:raise Conflict('Unknown SDD script')
    protect_local(root)
    env=session_env(root,cfg['execution'],args.change_id)
    # Approval/test scripts normally receive the change ID after the script name.
    cid=args.change_id
    for i,item in enumerate(args.arguments):
        if item=='--change-id' and i+1<len(args.arguments):
            if cid and cid!=args.arguments[i+1]:raise Conflict('Conflicting change IDs')
            cid=args.arguments[i+1]
        elif item.startswith('--change-id='):
            value=item.split('=',1)[1]
            if cid and cid!=value:raise Conflict('Conflicting change IDs')
            cid=value
    group=session_group(args.profile,cid,root,cfg)
    if group is not None:
        env['SDD_GROUP_ID']=cid
        env['SDD_CHANGE_ID']=cid
        if args.script=='approval_gate.py' and 'record-g2' in args.arguments:
            raise Conflict('Use sdd group approve-g2 for this change')
    return subprocess.run([sys.executable,str(SOURCE/'scripts'/args.script),*args.arguments],cwd=root,env=env,check=False).returncode


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);sp=p.add_subparsers(dest='command',required=True)
    for command in ('install','uninstall'):
        q=sp.add_parser(command);q.add_argument('--install-dir',default=str(DEFAULT_INSTALL));q.add_argument('--dry-run',action='store_true')
    q=sp.add_parser('configure');q.add_argument('--profile',default='default');q.add_argument('--codebase',required=True);q.add_argument('--execution',choices=('local','devcontainer'),default='local');q.add_argument('--replace',action='store_true')
    sp.add_parser('profiles')
    group_work.add_parser(sp)
    for command in ('run','exec'):
        q=sp.add_parser(command);q.add_argument('--profile',default='default');q.add_argument('--change-id')
        if command=='run':q.add_argument('--model');q.add_argument('--dry-run',action='store_true')
        else:q.add_argument('script');q.add_argument('arguments',nargs=argparse.REMAINDER)
    args=p.parse_args(argv)
    try:
        if args.command in ('install','uninstall'):
            target=install_root(args.install_dir)
            changes=install_plan(SOURCE,target) if args.command=='install' else uninstall_plan(target)
            print(f'{args.command}: {target}')
            for name,(old,new) in changes.items():print(('DELETE ' if new is None else 'ADD ' if old is None else 'UPDATE ')+name)
            if not args.dry_run:apply_changes(target,changes)
            print(f'{"DRY RUN" if args.dry_run else "DONE"}: {len(changes)} file(s). Workspace files were not changed.')
            if args.command=='install':print(f'Next: python3 {shlex.quote(str(target/"standalone.py"))} configure --codebase /path/to/java-repo')
            else:print('Workspace profiles, change groups and project data are retained. Empty directories are not removed.')
        elif args.command=='configure':configure(args)
        elif args.command=='profiles':print(json.dumps(profiles(),ensure_ascii=False,indent=2))
        elif args.command=='group':return group_work.dispatch(sys.modules[__name__],args)
        elif args.command=='run':return run(args)
        else:return execute(args)
        return 0
    except (Conflict,OSError,ValueError,TypeError,KeyError,subprocess.SubprocessError) as ex:
        print(f'SDD BLOCKED: {ex}',file=sys.stderr);return 2


if __name__=='__main__':sys.exit(main())
