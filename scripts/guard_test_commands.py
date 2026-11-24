#!/usr/bin/env python3
"""v5 PreToolUse guard. Fail-closed on unrecognized pre-G2 execution.

A project hook is advisory defense-in-depth, NOT an OS sandbox. Operator must
protect policy/scripts and isolate the test network outside this repo.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import shlex
import sys
from project_setup import ROOT, KIT_ROOT, SetupError, load_config, validate
from v5_core import verify_approval

READ_GIT={'status','diff','show','log','rev-parse','ls-files','branch'}
READ_BIN={'ls','pwd','cat','head','tail','sed','grep','rg','find','wc'}
SETUP_VERBS={'check','session','init'}
# Skip-free safety policy: do NOT parse compound shell grammar by substring.
SHELL_META=re.compile(r'[;&|<>`$\n\r]')


def reject(message):
    print('CLAUDE CODE v5 BLOCKED: '+message,file=sys.stderr)
    return 2


def active_change():
    env=os.environ.get('SDD_CHANGE_ID','')
    if re.fullmatch(r'CHG-[0-9]+',env): return env
    if os.environ.get('SDD_PROJECT_ROOT'):
        return None  # Standalone change ID must be fixed at launch.
    filename=ROOT/'.claude'/'local'/'active-change.json'
    if filename.is_file():
        data=json.loads(filename.read_text(encoding='utf-8'))
        if re.fullmatch(r'CHG-[0-9]+',str(data.get('change_id',''))):return data['change_id']
    return None


def allow_before_g2(command):
    if not isinstance(command,str) or SHELL_META.search(command):return False
    try:
        argv=shlex.split(command)
    except ValueError:
        return False
    if not argv:return False
    name=Path(argv[0]).name.lower()
    if name in READ_BIN:
        # Only strictly read-only flags: shell metacharacters already refused.
        if name=='sed' and any(x.startswith('-') and x not in ('-n','--quiet','--silent') for x in argv[1:]):return False
        if name=='find' and any(x in ('-delete','-exec','-execdir','-ok','-okdir','-fprint','-fprintf','-fls') for x in argv[1:]):return False
        if name in ('rg','grep') and any(x.startswith(('--pre','--replace','--output')) for x in argv[1:]):return False
        if name=='cat' and any(x.startswith('-') and x not in ('--number','-n','-b') for x in argv[1:]):return False
        return True
    if name=='git' and len(argv)>1 and argv[1] in READ_GIT:
        # git show/diff can use --output=... -> reject output redirect options
        return not any(x.startswith(('--output','--exec-path','-o','--config')) for x in argv[2:])
    if name in ('python3','python') and len(argv)>2:
        script=Path(argv[1]).as_posix()
        if os.environ.get('SDD_PROJECT_ROOT'):
            candidate=(ROOT/Path(argv[1])).resolve()
            if candidate != (KIT_ROOT/'scripts'/Path(argv[1]).name).resolve():return False
        verb=argv[2]
        if script.endswith('scripts/project_setup.py') and verb in SETUP_VERBS:return True
        if script.endswith('scripts/toolchain.py') and verb in {'doctor','plan'}:return True
        if script.endswith('scripts/approval_gate.py') and verb in {'hashes','verify-g2','verify-net'}:return True
        if script.endswith('scripts/check_traceability.py'):return True
        if script.endswith('scripts/progress.py'):return True
    return False


def allowed_docs_edit(request):
    path=request.get('tool_input',{}).get('file_path') or request.get('tool_input',{}).get('notebook_path')
    if not isinstance(path,str):return False
    selected=Path(path).resolve() if Path(path).is_absolute() else (ROOT/path).resolve()
    changes=ROOT/'docs'/'changes'
    # No edit access to policy, approval records, or Java implementation before G2.
    return selected.is_relative_to(changes.resolve()) and selected.suffix.lower() in ('.md','.csv','.json')


def main():
    try:
        data=json.load(sys.stdin)
        tool=data.get('tool_name','')
        inp=data.get('tool_input',{})
        if not isinstance(inp,dict):return reject('Invalid tool parameters')
        cwd=data.get('cwd')
        if cwd:
            current=Path(cwd).resolve()
            if not current.is_relative_to(ROOT.resolve()):return reject('Tool cwd outside the selected codebase')
        if tool=='Bash':
            command=inp.get('command','')
            if os.environ.get('SDD_PROJECT_ROOT') and isinstance(command,str) and re.search(r'\bSDD_(?:PROJECT_ROOT|EXECUTION_CONTEXT|CHANGE_ID)\s*=',command):
                return reject('Session workspace and change ID are fixed at launch')
            if isinstance(command,str) and re.search(r'\brecord-(?:g2|net|g6)\b',command,re.I):
                return reject('Only the human operator may record approval in a separate terminal')
            if allow_before_g2(command):return 0
            if not isinstance(command,str) or SHELL_META.search(command):
                return reject('Compound shell commands require an operator-reviewed execution path (no ; | && $() redirection)')
        if tool in ('Write','Edit','MultiEdit','NotebookEdit'):
            path=inp.get('file_path') or inp.get('notebook_path')
            if isinstance(path,str):
                target=(Path(cwd or ROOT)/Path(path)).resolve()
                if os.environ.get('SDD_PROJECT_ROOT') and not target.is_relative_to(ROOT):
                    return reject('Tool installation and other workspaces are read-only')
                if target.is_relative_to((ROOT/'.claude/local/approvals').resolve()):
                    return reject('Claude may not write human approval records')
        if tool not in ('Bash','Write','Edit','MultiEdit','NotebookEdit'):
            return 0  # unknown/MCP tools must be restricted by managed permissions/sandbox
        try:
            cfg=validate(load_config(),cwd=cwd)
        except SetupError as ex:
            if tool in ('Write','Edit') and allowed_docs_edit(data):return 0
            return reject(f'Project setup missing: {ex}')
        if tool in ('Write','Edit') and allowed_docs_edit(data):return 0
        chg=active_change()
        if not chg:return reject('No active change ID; set SDD_CHANGE_ID=CHG-001 or operator-approved active-change.json')
        try:
            verify_approval(chg,'G2')
        except (SetupError, OSError, ValueError) as ex:
            return reject(f'G2 approval missing or stale: {ex}')
        if tool=='Bash':
            args=shlex.split(inp.get('command',''))
            if not args:return reject('Empty command')
            if any('E2E_BASE_URL' in item or 'E2E_ALLOWED_ORIGIN' in item for item in args):
                return reject('Direct E2E environment overrides forbidden')
            text=' '.join(args).lower()
            if re.search(r'\be2etest\b|examples/playwright-java|playwright\.cli',text) and not any('scripts/run_e2e.py' in x for x in args):
                return reject('E2E must use approved scripts/run_e2e.py')
        return 0
    except (ValueError, TypeError, OSError, KeyError, json.JSONDecodeError) as ex:
        return reject(f'Guard could not validate request: {ex}')

if __name__=='__main__':sys.exit(main())
