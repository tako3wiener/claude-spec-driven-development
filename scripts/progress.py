#!/usr/bin/env python3
"""Optional spec-change progress journal (not an approval or quality gate).

Writes only to .claude/local/progress; never imports or depends on a dashboard.
A journal write failure must not change the outcome of development/test commands.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
CHANGE = re.compile(r'CHG-[0-9]+\Z')
GATES = {f'G{i}' for i in range(7)}
STATES = {'running', 'waiting', 'blocked', 'done'}

def _git(*args):
    try:
        p=subprocess.run(['git','-C',str(ROOT),*args],capture_output=True,text=True,timeout=5,check=False)
        return p.stdout.strip() if p.returncode==0 else ''
    except Exception:return ''  # Optional telemetry must tolerate test runner stubs and git failures

def record(change_id, gate, state, next_action='', note='', source='workflow'):
    """Best-effort status hint; only signed approval_gate verification authorizes G2/G6."""
    if not CHANGE.fullmatch(change_id):raise ValueError('Invalid change ID')
    if gate not in GATES or state not in STATES:raise ValueError('Invalid gate/state')
    if source not in ('workflow','test-runner','approval-check'):
        raise ValueError('Invalid source')
    if len(next_action)>500 or len(note)>1000:raise ValueError('Progress note too long')
    now=datetime.now(timezone.utc).isoformat(timespec='seconds')
    payload={'schema_version':1,'change_id':change_id,'gate':gate,'state':state,
             'next_action':next_action,'note':note,'reported_at':now,
             'branch':_git('branch','--show-current') or '(detached)',
             'commit':_git('rev-parse','HEAD'), 'source':source,
             'is_approval':False,'is_quality_attestation':False}
    root=ROOT/'.claude'/'local'/'progress'
    root.mkdir(parents=True,exist_ok=True)
    target=root/f'{change_id}.json'
    temp=root/f'.{change_id}.{secrets.token_hex(8)}.tmp'
    try:
        fd=os.open(temp,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as file:
            json.dump(payload,file,ensure_ascii=False,indent=2);file.write('\n')
        os.replace(temp,target)
        with (root/f'{change_id}.jsonl').open('a',encoding='utf-8') as file:
            file.write(json.dumps(payload,ensure_ascii=False,separators=(',',':'))+'\n')
    finally:
        if temp.exists():temp.unlink()
    return payload

def best_effort(change_id, gate, state, next_action='', note='', source='test-runner'):
    try:return record(change_id,gate,state,next_action,note,source)
    except Exception as exc:
        print('WARNING: optional progress journal unavailable:',type(exc).__name__,file=sys.stderr)
        return None

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--change-id',required=True)
    p.add_argument('--gate',required=True,choices=sorted(GATES))
    p.add_argument('--state',required=True,choices=sorted(STATES))
    p.add_argument('--next-action',default='')
    p.add_argument('--note',default='')
    args=p.parse_args()
    try:
        print(json.dumps(record(args.change_id,args.gate,args.state,args.next_action,args.note),ensure_ascii=False,indent=2))
        return 0
    except (ValueError,OSError) as exc:
        print('PROGRESS:',exc,file=sys.stderr);return 2

if __name__=='__main__':sys.exit(main())
