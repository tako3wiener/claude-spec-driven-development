#!/usr/bin/env python3
"""v5 orchestrator: preflight -> execute -> collect -> verify -> report.

No privileged network sandbox is created here. A recorded human approval and
external firewall/container policy are required for formal E2E execution.
"""
from __future__ import annotations
import argparse
import csv
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import secrets
from datetime import datetime, timezone

from project_setup import ROOT, KIT_ROOT, CONFIG, EXECUTION_CONTEXT, SetupError, load_config, validate, ensure_inside_root
from v5_core import (CHANGE_RE, RUN_RE, new_run_id, snapshot, verify_approval,
                     atomic_json, digest, file_digest, utcnow)
from check_e2e_evidence import verify
from artifact_paths import artifact_root, artifact_settings


def read_e2e_cases(change_id):
    file=ROOT/'docs'/'changes'/change_id/'test_cases.csv'
    if not file.is_file():raise SetupError('G2 test_cases.csv missing')
    with file.open(encoding='utf-8-sig',newline='') as f:
        rows=list(csv.DictReader(f))
    selected=[r for r in rows if (r.get('level') or '').strip().upper()=='E2E']
    if not selected:raise SetupError('No formal E2E cases in approved test_cases.csv')
    for row in selected:
        ref=(row.get('test_file') or '').split('#',1)[0]
        path=(ROOT/ref).resolve()
        if (not path.is_relative_to(ROOT) or not path.is_file() or path.suffix!='.java'
                or ref.startswith('examples/') or 'SmokeE2eTest' in ref or row.get('approval_state')!='APPROVED'):
            raise SetupError('Formal E2E test source is missing/DEMO_ONLY/unapproved: '+repr(ref))
    return selected


def approved_command(cfg,mode,junit):
    argv=cfg['e2e_test_argv'][:]
    source=' '.join(argv).lower()
    forbidden=('-dskiptests','-dmaven.test.skip','-x','--exclude-task','--tests', '-dtest=', '-dit.test=')
    if mode=='final' and any(x in source for x in forbidden):
        raise SetupError('Final E2E command contains skip or selective test flags')
    if mode=='final' and any('examples/playwright-java' in arg for arg in argv):
        raise SetupError('DEMO_ONLY module cannot be used for formal G4')
    runner=Path(argv[0]).name.lower()
    if runner in ('mvn','mvnw'):
        argv += [f'-Dsurefire.reportsDirectory={junit}',f'-Dfailsafe.reportsDirectory={junit}',
                 '-DfailIfNoTests=true','-Dsurefire.failIfNoSpecifiedTests=true']
    elif runner in ('gradle','gradlew'):
        argv += ['--no-build-cache','--rerun-tasks','-I',str(KIT_ROOT/'scripts/gradle/e2e-junit.init.gradle')]
    else:raise SetupError('Unsupported build runner')
    return argv


def make_index(run_dir):
    lines=[f'# E2E evidence: {run_dir.parent.name} / {run_dir.name}','',
           '> 自動収集したローカル証跡です。独立したCI署名や業務要件の充足を保証しません。','',
           '| TC | Status | Screenshots | Trace |','|---|---|---|---|']
    for mfile in sorted(run_dir.glob('TC-*/manifest.json')):
        m=json.loads(mfile.read_text(encoding='utf-8'))
        tc=mfile.parent.name
        shots=', '.join(f'[{x["file"]}]({tc}/{x["file"]})' for x in m.get('screenshots',[]) if re.fullmatch(r'\d{2}-[A-Za-z0-9_-]+\.png',x.get('file','')))
        trace=f'[trace.zip]({tc}/trace.zip)' if (mfile.parent/'trace.zip').exists() else '—'
        lines.append(f'| {tc} | {m.get("result","UNKNOWN")} | {shots or "—"} | {trace} |')
    (run_dir/'index.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')


def run(args):
    cfg=validate(load_config(),cwd=os.getcwd())
    if not CHANGE_RE.fullmatch(args.change_id):raise SetupError('Invalid change ID')
    working,_=ensure_inside_root(cfg['e2e_workdir'])
    runid=args.run_id or new_run_id()
    if not RUN_RE.fullmatch(runid):raise SetupError('Invalid run ID')
    # Preflight must happen before even a healthcheck / navigation.
    verify_approval(args.change_id,'G2')
    verify_approval(args.change_id,'NET',context={'allowed_origin':cfg['allowed_origin']})
    if args.mode=='final':read_e2e_cases(args.change_id)
    repo_snapshot=snapshot()
    rundir=artifact_root(ROOT,cfg,'e2e')/args.change_id/runid
    if rundir.exists():raise SetupError('RUN-ID already exists: '+str(rundir))
    argv=approved_command(cfg,args.mode,rundir/'junit')
    if args.dry_run:
        print('DRY RUN (no browser/URL accessed):',shlex.join(argv))
        print('Approved environment:',cfg['environment'],cfg['allowed_origin'])
        return 0
    from toolchain import doctor
    if doctor(cfg,'chromium','e2e',False)!=0:
        raise SetupError('Playwright/JDK toolchain is not ready; run /tool-setup')
    try:
        from progress import best_effort
        best_effort(args.change_id,'G4','running','Playwright E2Eを実行して証跡を記録する',source='test-runner')
    except ImportError:pass
    rundir.mkdir(parents=True,exist_ok=False)
    (rundir/'junit').mkdir()
    token=secrets.token_hex(32)
    ctx={'schema_version':2,'change_id':args.change_id,'run_id':runid,'mode':args.mode,
         'environment':cfg['environment'],'environment_origin':cfg['allowed_origin'],
         'repository_root':str(ROOT),
         'artifact_retention_days':artifact_settings(cfg)['artifact_retention_days'],
         'base_url':cfg['base_url'],'started_at_utc':utcnow(),
         'snapshot_id':repo_snapshot['snapshot_id'],'git':repo_snapshot,
         'runner_token_digest':digest(token.encode()),'command':argv,
         'execution_finished':False,'command_exit_code':None,'provenance_level':'local-unattested'}
    if EXECUTION_CONTEXT is not None:
        ctx.update(execution_context=EXECUTION_CONTEXT,project_config_hash=file_digest(CONFIG))
    atomic_json(rundir/'run-context.json',ctx)
    env=os.environ.copy()
    env.update({'E2E_BASE_URL':cfg['base_url'],'E2E_ALLOWED_ORIGIN':cfg['allowed_origin'],
                'E2E_ENVIRONMENT':cfg['environment'],'E2E_CHANGE_ID':args.change_id,
                'E2E_RUN_ID':runid,'E2E_EVIDENCE_ROOT':str(artifact_root(ROOT,cfg,'e2e')),
                'E2E_GIT_COMMIT':repo_snapshot['head'],'E2E_SNAPSHOT_ID':repo_snapshot['snapshot_id'],
                'E2E_RUN_TOKEN_DIGEST':ctx['runner_token_digest'], 'E2E_JUNIT_DIR':str(rundir/'junit'),
                'E2E_MODE':args.mode})
    print('E2E target:',cfg['environment'],cfg['allowed_origin'],'run:',runid,flush=True)
    code=2
    try:
        proc=subprocess.run(argv,cwd=working,env=env,timeout=args.timeout,check=False)
        code=proc.returncode
    except (OSError,subprocess.TimeoutExpired) as ex:
        print('E2E execution error:',type(ex).__name__,file=sys.stderr)
    finally:
        ctx['execution_finished']=True
        ctx['finished_at_utc']=utcnow()
        ctx['command_exit_code']=code
        atomic_json(rundir/'run-context.json',ctx)
    errs=verify(ROOT/'docs'/'changes'/args.change_id,rundir,True,rundir/'junit',repo_root=ROOT) if args.mode=='final' else ['DEMO_ONLY; not eligible for formal G4']
    try:
        after=snapshot()
        if after['snapshot_id']!=repo_snapshot['snapshot_id']:
            errs.append('Code snapshot changed while E2E was running')
    except SetupError as ex:
        errs.append('Post-run git snapshot failed: '+str(ex))
    if EXECUTION_CONTEXT is not None:
        try:
            if file_digest(CONFIG)!=ctx['project_config_hash']:
                errs.append('Project/execution configuration changed during E2E')
        except OSError:
            errs.append('Project/execution configuration disappeared during E2E')
    make_index(rundir)
    outcome='PASS' if not errs else ('FAIL' if code not in (0,2) else 'BLOCKED')
    result={'schema_version':2,'change_id':args.change_id,'run_id':runid,
       'snapshot_id':repo_snapshot['snapshot_id'],'status':outcome,
       'executor_exit_code':code,'errors':errs,'finished_at_utc':utcnow(),
       'independent_execution_attested':False}
    if EXECUTION_CONTEXT is not None:
        result.update(execution_context=EXECUTION_CONTEXT,project_config_hash=ctx['project_config_hash'])
    atomic_json(rundir/'verification.json',{'errors':errs,'format_and_crosscheck_passed':not errs})
    atomic_json(rundir/'summary.json',result)
    with (rundir/'test_results.csv').open('w',encoding='utf-8',newline='') as file:
        w=csv.writer(file);w.writerow(['tc_id','result','evidence','run_id','snapshot_id'])
        for m in sorted(rundir.glob('TC-*/manifest.json')):
            try:status=json.loads(m.read_text(encoding='utf-8')).get('result','BLOCKED')
            except (OSError,ValueError):status='BLOCKED'
            w.writerow([m.parent.name,status,str(m.relative_to(ROOT)),runid,repo_snapshot['snapshot_id']])
    try:
        from progress import best_effort
        best_effort(args.change_id,'G4','done' if outcome=='PASS' else 'blocked',
                    'E2E証跡を確認して独立レビューを実施する' if outcome=='PASS' else
                    'E2E実行と証跡の問題を調査し、再実行する',source='test-runner')
    except ImportError:pass
    print('E2E',outcome,'run=',rundir,'problems=',len(errs))
    return 0 if outcome=='PASS' else (1 if outcome=='FAIL' else 2)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--change-id',required=True)
    ap.add_argument('--run-id')
    ap.add_argument('--mode',choices=('final','demo'),default='final')
    ap.add_argument('--dry-run',action='store_true')
    ap.add_argument('--timeout',type=int,default=1800)
    args=ap.parse_args()
    try:return run(args)
    except (SetupError,OSError,KeyError,ValueError) as ex:
        print('E2E BLOCKED:',ex,file=sys.stderr);return 2
if __name__=='__main__':sys.exit(main())
