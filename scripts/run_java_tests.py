#!/usr/bin/env python3
"""Run Java unit/integration tests into fresh, configured, run-scoped JUnit evidence.

Works with the existing Maven/Gradle commands selected in /project-setup.
No test is labeled PASS without an actual JUnit XML and zero failures/skips.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET
from project_setup import ROOT, SetupError, load_config, validate, ensure_inside_root
from artifact_paths import artifact_root, artifact_settings
from v5_core import CHANGE_RE, RUN_RE, new_run_id, snapshot, atomic_json, utcnow, verify_approval


def junit_counts(folder: Path) -> dict:
    counts = {'tests':0, 'failures':0, 'errors':0, 'skipped':0, 'files':0}
    for path in sorted(folder.rglob('*.xml')):
        if path.is_symlink() or path.stat().st_size > 20_000_000:
            raise SetupError('JUnit XML is unsafe or too large: ' + str(path))
        try:
            root = ET.parse(path).getroot()
            cases = root.findall('.//testcase')
            if root.tag == 'testcase':cases=[root]
            if root.tag not in ('testsuites','testsuite','testcase'):
                raise ValueError('Unexpected JUnit XML element')
            counts['files'] += 1
            counts['tests'] += len(cases)
            for case in cases:
                counts['failures'] += bool(case.find('failure') is not None)
                counts['errors'] += bool(case.find('error') is not None)
                counts['skipped'] += bool(case.find('skipped') is not None)
        except (ET.ParseError, ValueError) as exc:
            raise SetupError('Invalid JUnit XML: '+str(path)) from exc
    return counts


def build_command(cfg: dict, phase: str, junit: Path) -> list[str]:
    field = 'unit_test_argv' if phase == 'unit' else 'integration_test_argv'
    argv = cfg.get(field)
    if not isinstance(argv, list) or not argv:
        raise SetupError(f'{phase} test command is not configured; run /project-setup')
    argv = argv[:]
    executor = Path(argv[0]).name.lower()
    # Revalidate saved command: never pass shell fragments into execution.
    from project_setup import parse_command
    import shlex
    parse_command(shlex.join(argv), field)
    if executor in ('mvn','mvnw'):
        argv += ['-DfailIfNoTests=true',f'-Dsurefire.reportsDirectory={junit}',
                 f'-Dfailsafe.reportsDirectory={junit}']
    elif executor in ('gradle','gradlew'):
        argv += ['--no-build-cache','--rerun-tasks','-I',
                 str(ROOT/'scripts/gradle/java-junit.init.gradle')]
    else: raise SetupError('Only Maven/Gradle commands are supported')
    return argv


def run(args) -> int:
    cfg = validate(load_config(),cwd=os.getcwd())
    if not CHANGE_RE.fullmatch(args.change_id):raise SetupError('Invalid change ID')
    verify_approval(args.change_id,'G2')
    change_id = args.change_id
    run_id = args.run_id or new_run_id()
    if not RUN_RE.fullmatch(run_id):raise SetupError('Invalid RUN-ID')
    codebase, _ = ensure_inside_root(cfg['codebase'])
    root = artifact_root(ROOT,cfg,'unit')
    run_dir = root/change_id/run_id/args.phase
    if run_dir.exists():raise SetupError('RUN-ID/phase already exists: '+str(run_dir))
    junit = run_dir/'junit'
    argv = build_command(cfg,args.phase,junit)
    if args.dry_run:
        import shlex
        print('DRY RUN:',shlex.join(argv))
        return 0
    try:
        from progress import best_effort
        best_effort(change_id,'G3','running',f'{args.phase} テスト実行中',source='test-runner')
    except ImportError:pass
    before = snapshot()
    run_dir.mkdir(parents=True,exist_ok=False)
    junit.mkdir()
    context = {'schema_version':1,'change_id':change_id,'run_id':run_id,
               'phase':args.phase,'repository_root':str(ROOT),
               'artifact_retention_days':artifact_settings(cfg)['artifact_retention_days'],
               'started_at_utc':utcnow(),'git':before,
               'snapshot_id':before['snapshot_id'],'command':argv,
               'execution_finished':False}
    atomic_json(run_dir/'run-context.json',context)
    env = os.environ.copy()
    env.update({'JAVA_JUNIT_DIR':str(junit),'SDD_TEST_RUN_ID':run_id})
    code = 2
    failure = None
    try:
        result=subprocess.run(argv,cwd=codebase,env=env,timeout=args.timeout,check=False)
        code=result.returncode
    except (OSError,subprocess.TimeoutExpired) as exc:
        failure=type(exc).__name__
    finally:
        context['execution_finished']=True
        context['finished_at_utc']=utcnow()
        context['command_exit_code']=code
        atomic_json(run_dir/'run-context.json',context)
    try:
        counts=junit_counts(junit)
    except SetupError as exc:
        counts={'tests':0,'failures':0,'errors':0,'skipped':0,'files':0}
        failure=str(exc)
    if counts['tests'] < 1:failure=failure or 'No executed JUnit test cases'
    if counts['skipped'] or counts['errors'] or counts['failures']:
        failure=failure or 'JUnit tests failed, errored or were skipped'
    if code != 0:failure=failure or 'Test command returned non-zero'
    try:
        after=snapshot()
        if after['snapshot_id'] != before['snapshot_id']:
            failure=failure or 'Code changed during test execution'
    except SetupError as exc:failure=failure or str(exc)
    status='PASS' if failure is None else ('FAIL' if code not in (0,2) or counts['failures'] or counts['errors'] else 'BLOCKED')
    summary={'schema_version':1,'change_id':change_id,'run_id':run_id,'phase':args.phase,
             'snapshot_id':before['snapshot_id'],'status':status,
             'executor_exit_code':code,'counts':counts,'problem':failure,
             'finished_at_utc':utcnow()}
    atomic_json(run_dir/'summary.json',summary)
    try:
        from progress import best_effort
        best_effort(change_id,'G3','running' if status=='PASS' else 'blocked',
            ('単体・結合テストを確認後、Playwright E2Eへ進む' if status=='PASS' else f'{args.phase} 結果を調査・修正する: {failure}'),source='test-runner')
    except ImportError:pass
    print(f'{args.phase} {status}: {run_dir} (JUnit tests: {counts["tests"]})')
    return 0 if status=='PASS' else (1 if status=='FAIL' else 2)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--phase',choices=('unit','integration'),required=True)
    p.add_argument('--change-id',required=True)
    p.add_argument('--run-id')
    p.add_argument('--dry-run',action='store_true')
    p.add_argument('--timeout',type=int,default=1800)
    args=p.parse_args()
    try:return run(args)
    except (SetupError,OSError,KeyError,ValueError) as exc:
        print('JAVA TEST BLOCKED:',exc,file=sys.stderr);return 2
if __name__=='__main__':sys.exit(main())
