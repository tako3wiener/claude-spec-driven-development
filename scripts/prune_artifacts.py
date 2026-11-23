#!/usr/bin/env python3
"""List (default) or explicitly delete completed runs older than retention policy.

No deletion happens on setup/check/test execution. Operator-controlled --apply is required.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import re
import shutil
import sys
from project_setup import ROOT, SetupError, load_config, validate
from artifact_paths import artifact_root, artifact_settings

CHG_RE = re.compile(r'CHG-[0-9]+\Z')
RUN_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,100}\Z')


def expired_runs(repo: Path, cfg: dict, now: datetime | None = None) -> list[Path]:
    now = now or datetime.now(timezone.utc)
    horizon = now - timedelta(days=artifact_settings(cfg)['artifact_retention_days'])
    result = []
    for kind in ('unit', 'e2e'):
        base = artifact_root(repo,cfg,kind)
        if not base.is_dir():continue
        for change in base.iterdir():
            if change.is_symlink() or not change.is_dir() or not CHG_RE.fullmatch(change.name):continue
            for run in change.iterdir():
                if run.is_symlink() or not run.is_dir() or not RUN_RE.fullmatch(run.name):continue
                # Every phase must be completed and old; a partially written run is never deleted.
                phases = [run] if kind=='e2e' else [run/x for x in ('unit','integration') if (run/x).is_dir() and not (run/x).is_symlink()]
                if not phases:continue
                eligible = True
                for part in phases:
                    try:
                        summary=json.loads((part/'summary.json').read_text(encoding='utf-8'))
                        context=json.loads((part/'run-context.json').read_text(encoding='utf-8'))
                        stamp=summary['finished_at_utc']
                        finished=datetime.fromisoformat(stamp.replace('Z','+00:00'))
                        if (finished.tzinfo is None or finished>=horizon or finished>now or
                            context.get('execution_finished') is not True or
                            context.get('run_id') != run.name or context.get('change_id') != change.name or
                            summary.get('run_id')!=run.name or summary.get('change_id')!=change.name or
                            summary.get('status') not in ('PASS','FAIL','BLOCKED')):
                            eligible=False
                            break
                    except (OSError,ValueError,TypeError,KeyError):eligible=False;break
                if eligible:result.append(run)
    return sorted(result)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--apply',action='store_true',help='Actually delete eligible completed runs')
    p.add_argument('--confirm-delete',action='store_true',help='Explicit operator approval of deletion')
    args=p.parse_args()
    try:
        cfg=validate(load_config())
        days=artifact_settings(cfg)['artifact_retention_days']
        targets=expired_runs(ROOT,cfg)
        print(f'COMPLETED RUNS older than {days} days: {len(targets)}')
        for path in targets:print('  '+str(path))
        if args.apply:
            if not args.confirm_delete:
                raise SetupError('Deletion requires --apply --confirm-delete after reviewing dry-run')
            for target in targets:
                if target.is_symlink():raise SetupError('Refusing to delete symlink')
                shutil.rmtree(target)
            print('DELETED:',len(targets))
        else:
            print('DRY RUN: nothing was deleted')
        return 0
    except (SetupError,OSError) as exc:
        print('RETENTION BLOCKED:',exc,file=sys.stderr);return 2
if __name__=='__main__':sys.exit(main())
