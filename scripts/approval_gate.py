#!/usr/bin/env python3
"""Read-only G2/G6/NET signature verification and document hash display.

Requires an external operator-managed signing private key, never available to Claude.
"""
import argparse
import json
import sys
from pathlib import Path
from project_setup import SetupError
from v5_core import plan_hashes, verify_approval, file_digest, snapshot


def main():
    p=argparse.ArgumentParser()
    p.add_argument('action',choices=('hashes','verify-g2','verify-g6','verify-net'))
    p.add_argument('--change-id',required=True)
    p.add_argument('--run-dir',type=Path)
    p.add_argument('--allowed-origin')
    args=p.parse_args()
    try:
        if args.action=='hashes':
            result=plan_hashes(args.change_id)
        elif args.action=='verify-g2':
            result=verify_approval(args.change_id,'G2')
        elif args.action=='verify-net':
            if not args.allowed_origin: raise SetupError('--allowed-origin required')
            result=verify_approval(args.change_id,'NET',context={'allowed_origin':args.allowed_origin})
        else:
            if not args.run_dir: raise SetupError('--run-dir required')
            run=json.loads((args.run_dir/'run-context.json').read_text(encoding='utf-8'))
            summary=json.loads((args.run_dir/'summary.json').read_text(encoding='utf-8'))
            if summary.get('status')!='PASS' or summary.get('independent_execution_attested') is True:
                # v5 does not claim an external independent executor (R12 deferred).
                raise SetupError('G6 requires a local PASS report with honest provenance flags')
            if snapshot()['snapshot_id'] != run['snapshot_id']:
                raise SetupError('Worktree changed since E2E run')
            result=verify_approval(args.change_id,'G6',context={
                'run_id':run['run_id'],'snapshot_id':run['snapshot_id'],
                'summary_sha256':file_digest(args.run_dir/'summary.json')})
        if args.action in ('verify-g2','verify-g6'):
            try:
                from progress import best_effort
                if args.action=='verify-g2':
                    best_effort(args.change_id,'G2','done','承認済みの計画に従ってJava実装・単体/結合テストを進める',source='approval-check')
                else:
                    best_effort(args.change_id,'G6','done','署名による受入確認済み。マージ・デプロイは別手続きで判断',source='approval-check')
            except ImportError:pass
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0
    except (SetupError, ValueError, KeyError, OSError) as e:
        print('BLOCKED:',e,file=sys.stderr)
        return 2
if __name__=='__main__':sys.exit(main())
