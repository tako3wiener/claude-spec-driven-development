#!/usr/bin/env python3
"""Record ordinary human approvals in a separate terminal and verify their scope.

No signing key or .sig file is required for G2, NET or G6. Local records are
not proof of identity. Claude must never record approvals on the user's behalf.
"""
import argparse
import json
import os
import sys
from pathlib import Path
from project_setup import ROOT, CONFIG, EXECUTION_CONTEXT, SetupError, load_config, validate
from v5_core import (CHANGE_RE, plan_hashes, verify_approval, validate_approval_content,
                     file_digest, snapshot, atomic_json, utcnow, group_for_change, group_evidence_context)


def g6_context(run_dir, change_id=None):
    if not run_dir:
        raise SetupError('--run-dir required')
    run=json.loads((run_dir/'run-context.json').read_text(encoding='utf-8'))
    summary=json.loads((run_dir/'summary.json').read_text(encoding='utf-8'))
    if summary.get('status')!='PASS' or summary.get('independent_execution_attested') is True:
        raise SetupError('G6 requires a local PASS report with honest provenance flags')
    if snapshot()['snapshot_id'] != run['snapshot_id']:
        raise SetupError('Worktree changed since E2E run')
    if EXECUTION_CONTEXT is not None:
        expected=file_digest(CONFIG)
        if (run.get('repository_root') != str(ROOT) or
                run.get('execution_context') != EXECUTION_CONTEXT or
                run.get('project_config_hash') != expected or
                summary.get('project_config_hash') != expected):
            raise SetupError('E2E run belongs to a different project/execution configuration; rerun E2E')
    context={'run_id':run['run_id'],'snapshot_id':run['snapshot_id'],
             'summary_sha256':file_digest(run_dir/'summary.json')}
    cid=change_id or run.get('change_id')
    if cid:
        group=group_evidence_context(cid,required=bool(run.get('group_context') or summary.get('group_context')))
        if group is not None:
            if run.get('change_id')!=cid or run.get('group_context')!=group or summary.get('group_context')!=group:
                raise SetupError('Group membership, G2 approval generation or peer snapshot changed; rerun formal E2E')
            context['group_context']=group
    elif os.environ.get('SDD_GROUP_ID') or run.get('group_context'):
        raise SetupError('Group run change ID missing')
    return context


def record(gate, change_id, context, isolation_mode):
    if gate=='G2' and group_for_change(change_id) is not None:
        raise SetupError('Use sdd group approve-g2; do not record individual G2 for a group')
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise SetupError('Human approval must be entered in a separate interactive terminal')
    details=plan_hashes(change_id) if gate=='G2' else dict(context)
    if gate=='NET':
        if not isolation_mode:
            raise SetupError('--isolation-mode required for NET')
        details.update(isolation_mode=isolation_mode, dedicated_test_credentials=True,
                       synthetic_test_data=True, trace_storage_approved=True)
    print(json.dumps({'change_id':change_id,'gate':gate,**details},ensure_ascii=False,indent=2))
    if gate=='NET':
        print('Confirm actual external network isolation, least-privilege test credentials, '
              'synthetic data and permission to store Playwright traces.')
    elif gate=='G2':
        print('Review requirements.csv, test_cases.csv, change-plan.md and project-env.json before approval.')
    else:
        print('Review test evidence, code diff, independent review and remaining risks before acceptance.')
    approver=input('Human approver name/ID: ').strip()
    if not approver or input(f'Type APPROVE {gate} {change_id} to confirm: ').strip()!=f'APPROVE {gate} {change_id}':
        raise SetupError('Approval not confirmed')
    payload={'change_id':change_id,'gate':gate,'decision':'APPROVED',
             'approver':approver,'approved_at':utcnow(),**details}
    validate_approval_content(change_id,gate,payload,context=context)
    atomic_json(ROOT/'.claude'/'local'/'approvals'/f'{change_id}-{gate}.json',payload)
    return payload


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=('hashes','verify-g2','verify-g6','verify-net','record-g2','record-net','record-g6'))
    p.add_argument('--change-id',required=True)
    p.add_argument('--run-dir',type=Path)
    p.add_argument('--allowed-origin')
    p.add_argument('--isolation-mode',choices=('container-egress-policy','network-firewall','dedicated-isolated-host'))
    args=p.parse_args()
    try:
        if not CHANGE_RE.fullmatch(args.change_id):
            raise SetupError('Invalid change ID')
        if EXECUTION_CONTEXT is not None:
            validate(load_config(),cwd=os.getcwd())
        if args.action=='hashes':
            result=plan_hashes(args.change_id)
        else:
            operation,gate=args.action.split('-')
            gate=gate.upper()
            context=None
            if gate=='NET':
                configured=load_config()['allowed_origin']
                if not args.allowed_origin or args.allowed_origin!=configured:
                    raise SetupError('--allowed-origin must match project configuration')
                context={'allowed_origin':configured}
                if EXECUTION_CONTEXT is not None:
                    context['project_config_hash']=file_digest(CONFIG)
            elif gate=='G6':
                context=g6_context(args.run_dir,args.change_id)
            if operation=='record':
                result=record(gate,args.change_id,context,args.isolation_mode)
            else:
                result=verify_approval(args.change_id,gate,context=context)
            if gate in ('G2','G6'):
                try:
                    from progress import best_effort
                    if gate=='G2':
                        best_effort(args.change_id,'G2','done','承認済みの計画に従ってJava実装・単体/結合テストを進める',source='approval-check')
                    else:
                        best_effort(args.change_id,'G6','done','人の受入承認記録を確認済み。マージ・デプロイは別手続きで判断',source='approval-check')
                except ImportError:pass
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0
    except (SetupError,ValueError,KeyError,OSError,EOFError,TypeError) as e:
        print('BLOCKED:',e,file=sys.stderr)
        return 2
if __name__=='__main__':sys.exit(main())
