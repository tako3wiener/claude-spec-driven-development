#!/usr/bin/env python3
"""Check REQ-to-test-to-execution references. This is NOT a test runner or approval authority.

Usage: python3 scripts/check_traceability.py docs/changes/CHG-001 [--require-approval] [--require-results]
"""
import argparse
import csv
import re
import sys
from pathlib import Path
from project_setup import ROOT

REQUIRED = {
    'requirements.csv': ('req_id','source','classification','status','priority','requirement','acceptance_criteria'),
    'test_cases.csv': ('tc_id','req_id','level','kind','preconditions','steps','expected_result','test_file','approval_state'),
    'test_results.csv': ('tc_id','result','evidence'),
}

def load(path: Path, fields, problems):
    if not path.is_file():
        problems.append(f'MISSING {path}')
        return []
    try:
        with path.open(encoding='utf-8-sig', newline='') as f:
            reader = csv.DictReader(f)
            columns = reader.fieldnames or []
            missing = set(fields) - set(columns)
            if missing:
                problems.append(f'INVALID {path}: missing columns {sorted(missing)}')
                return []
            return [{k: (v or '').strip() for k,v in row.items() if k is not None} for row in reader]
    except (OSError,UnicodeError,csv.Error) as ex:
        problems.append(f'INVALID {path}: {ex}')
        return []

def check(root: Path, approval: bool, results_required: bool):
    p=[]
    reqs=load(root/'requirements.csv', REQUIRED['requirements.csv'],p)
    tests=load(root/'test_cases.csv', REQUIRED['test_cases.csv'],p)
    results=load(root/'test_results.csv', REQUIRED['test_results.csv'],p) if results_required else []
    req_map={}
    for row in reqs:
        k=row['req_id']
        if not re.fullmatch(r'REQ-\d+', k): p.append(f'BAD REQ ID: {k!r}')
        if k in req_map: p.append(f'DUP REQ: {k}')
        req_map[k]=row
        if row['classification'] not in {'A','B','C','D'}: p.append(f'BAD CLASS: {k}')
        if row['status'] not in {'CONFIRMED','OPEN','BLOCKED'}: p.append(f'BAD STATUS: {k}')
        if not row['source']: p.append(f'NO SOURCE: {k}')
        if row['status']=='CONFIRMED' and (not row['requirement'] or not row['acceptance_criteria']): p.append(f'NO ACCEPTANCE: {k}')
        if row['classification'] in {'C','D'} and row['status']=='CONFIRMED': p.append(f'UNVERIFIED CONFIRMED: {k}')
        if (approval or results_required) and row['status']!='CONFIRMED': p.append(f'UNRESOLVED REQ: {k} [{row["status"]}]')
    tc_map={}
    for row in tests:
        k=row['tc_id']; req=row['req_id']
        if not re.fullmatch(r'TC-\d+', k): p.append(f'BAD TC ID: {k!r}')
        if k in tc_map: p.append(f'DUP TC: {k}')
        tc_map[k]=row
        if req not in req_map: p.append(f'UNKNOWN REQ: {k} -> {req}')
        if not row['expected_result'] or not row['steps']: p.append(f'INCOMPLETE TEST: {k}')
        if results_required:
            test_file=(row['test_file'] or '').split('#',1)[0]
            if not test_file:
                p.append(f'NO TEST CODE REFERENCE: {k}')
            else:
                path=(ROOT/test_file).resolve()
                if not path.is_relative_to(ROOT.resolve()) or not path.is_file():
                    p.append(f'TEST CODE MISSING OR OUTSIDE REPO: {k} {test_file}')
        if row['approval_state'] not in {'DRAFT','APPROVED'}: p.append(f'BAD APPROVAL STATE: {k}')
        if approval and row['approval_state']!='APPROVED': p.append(f'NOT APPROVED: {k}')
    for k,row in req_map.items():
        if row['status']=='CONFIRMED' and not any(t['req_id']==k for t in tests): p.append(f'NO TEST: {k}')
    rmap={}
    for row in results:
        k=row['tc_id']
        if k in rmap: p.append(f'DUP RESULT: {k}')
        if k not in tc_map: p.append(f'UNKNOWN RESULT TC: {k}')
        if row['result'] not in {'PASS','FAIL','BLOCKED','SKIP'}: p.append(f'BAD RESULT: {k} {row["result"]}')
        if row['result']=='PASS':
            evidence=(row['evidence'] or '').strip()
            if not evidence:
                p.append(f'NO EVIDENCE: {k}')
            else:
                evidence_path=(ROOT/evidence).resolve()
                if not evidence_path.is_relative_to(ROOT.resolve()) or not evidence_path.is_file():
                    p.append(f'EVIDENCE FILE MISSING OR OUTSIDE REPO: {k} {evidence}')
        if row['result']!='PASS': p.append(f'NOT PASS: {k} [{row["result"]}]')
        rmap[k]=row
    if results_required:
        for k in tc_map:
            if k not in rmap: p.append(f'NOT EXECUTED: {k}')
    print(f'REQ={len(reqs)} TC={len(tests)} RESULTS={len(results)}')
    for m in p: print('ERROR: '+m)
    if not reqs: p.append('EMPTY REQUIREMENTS')
    if not tests: p.append('EMPTY TESTS')
    if p:
        print('FAIL: traceability checks did not pass. This is not a substitute for executing tests or human sign-off.')
        return 1
    print('PASS: CSV/file-reference consistency only. Verify actual test execution, log authenticity, reviewer and human approvals separately.')
    return 0

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('change_dir', type=Path)
    ap.add_argument('--require-approval', action='store_true')
    ap.add_argument('--require-results', action='store_true')
    a=ap.parse_args()
    sys.exit(check(a.change_dir,a.require_approval,a.require_results))
if __name__=='__main__': main()
