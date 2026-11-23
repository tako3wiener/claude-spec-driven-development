#!/usr/bin/env python3
"""v5: Validate artifacts, current-run provenance and JUnit assertions.

Does not establish cryptographic provenance of the test process or UI correctness.
No CI attestation is asserted.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
import zlib

PNG_MAGIC=b'\x89PNG\r\n\x1a\n'


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def valid_png(path):
    """Strict PNG structural check plus bounded IDAT pixel-data decompression."""
    try:
        data=Path(path).read_bytes()
        if not data.startswith(PNG_MAGIC) or len(data)>80_000_000:return False
        off=8; w=h=None; ctype=None; depth=None; interlace=None; idat=[]; ended=False
        while off+12<=len(data):
            ln=int.from_bytes(data[off:off+4],'big');typ=data[off+4:off+8]
            if ln>80_000_000 or off+12+ln>len(data):return False
            payload=data[off+8:off+8+ln]; crc=int.from_bytes(data[off+8+ln:off+12+ln],'big')
            if zlib.crc32(typ+payload)&0xffffffff!=crc:return False
            off+=12+ln
            if typ==b'IHDR':
                if len(payload)!=13 or w is not None:return False
                w=int.from_bytes(payload[:4],'big');h=int.from_bytes(payload[4:8],'big')
                depth,ctype,compression,filter_method,interlace=payload[8:13]
                if not (1<=w<=12000 and 1<=h<=12000 and depth==8 and ctype in (2,6) and compression==filter_method==interlace==0):return False
            elif typ==b'IDAT':idat.append(payload)
            elif typ==b'IEND':
                ended=True
                if ln!=0 or off!=len(data):return False
                break
        if not ended or not idat or w is None:return False
        expected=(w*(4 if ctype==6 else 3)+1)*h
        if expected>160_000_000:return False
        obj=zlib.decompressobj()
        decoded=obj.decompress(b''.join(idat),expected+1)
        if len(decoded)!=expected or not obj.eof or obj.unused_data or obj.unconsumed_tail:return False
        stride=w*(4 if ctype==6 else 3)+1
        if any(decoded[i*stride]>4 for i in range(h)):return False
        return True
    except (OSError,ValueError,zlib.error,OverflowError):return False


def valid_trace(path):
    try:
        if not zipfile.is_zipfile(path):return False
        with zipfile.ZipFile(path) as z:
            names=z.namelist()
            if len(names)>10000:return False
            if any(n.startswith('/') or '..' in Path(n).parts for n in names):return False
            traces=[n for n in names if n.endswith('.trace')]
            if not traces:return False
            for name in traces:
                if z.getinfo(name).file_size>80_000_000:return False
                with z.open(name) as file:
                    lines=[line for _,line in zip(range(25),file) if line.strip()]
                parsed=[json.loads(line) for line in lines]
                if any(isinstance(v,dict) and v.get('type') in ('context-options','before','after','event','action') for v in parsed):
                    return True
        return False
    except (OSError,ValueError,json.JSONDecodeError,zipfile.BadZipFile,RuntimeError):return False


def rows(path, columns, errors):
    if not Path(path).is_file():
        errors.append(f'Missing CSV: {path}');return []
    try:
        with Path(path).open(encoding='utf-8-sig',newline='') as f:
            reader=csv.DictReader(f)
            if not set(columns).issubset(reader.fieldnames or []):
                errors.append(f'Invalid CSV columns: {path}');return []
            return list(reader)
    except (OSError,UnicodeError,csv.Error) as ex:
        errors.append(f'Invalid CSV {path}: {ex}');return []


def junit_parse(folder, errors):
    folder=Path(folder)
    xmls=sorted(folder.rglob('*.xml')) if folder.is_dir() else ([folder] if folder.is_file() else [])
    if not xmls:errors.append('No CURRENT run JUnit XML found');return {},0
    found={};count=0
    for file in xmls:
        try:root=ET.parse(file).getroot()
        except (ET.ParseError,OSError) as ex:
            errors.append(f'Invalid JUnit XML {file}: {ex}');continue
        cases=list(root.iter('testcase'))
        if not cases:errors.append(f'JUnit XML without testcase: {file}')
        count+=len(cases)
        for case in cases:
            matches=re.findall(r'(?<![A-Za-z0-9])TC[_-](\d+)(?!\d)', case.get('name',''))
            bad=any(el.tag.split('}')[-1] in ('skipped','error','failure') for el in case)
            if bad:errors.append(f'JUnit failure/skip: {case.get("name", "?")}')
            if not matches:
                errors.append(f'Unmapped JUnit testcase (no TC ID): {case.get("name", "?")}')
            for number in matches:
                found.setdefault('TC-'+number,[]).append(not bad)
    return found,count


def verify(change_dir,run_dir,require_pass=True,junit_xml=None,repo_root=None):
    change_dir=Path(change_dir);run_dir=Path(run_dir)
    errs=[]
    run_file=run_dir/'run-context.json'
    try:run=json.loads(run_file.read_text(encoding='utf-8'))
    except (OSError,ValueError) as ex:
        return ['Missing/invalid run-context.json: '+str(ex)]
    chg=change_dir.name
    if run.get('schema_version')!=2:errs.append('Run schema version is not v5')
    if run.get('change_id')!=chg or run.get('run_id')!=run_dir.name or run_dir.parent.name!=chg:errs.append('Run identity/path mismatch')
    if run.get('mode')!='final':errs.append('DEMO_ONLY or non-final run is not eligible for PASS')
    if not re.fullmatch(r'[0-9a-f]{64}',str(run.get('snapshot_id',''))):errs.append('Invalid code snapshot id')
    if not re.fullmatch(r'[0-9a-f]{64}',str(run.get('runner_token_digest',''))):errs.append('Missing runner token digest')
    if not run.get('environment_origin'):errs.append('Missing origin')
    if run.get('execution_finished') is not True:errs.append('No recorded execution completion')
    if run.get('command_exit_code')!=0:errs.append('Test runner returned non-zero exit')
    cases=rows(change_dir/'test_cases.csv',('tc_id','level','test_file','approval_state'),errs)
    e2e=[c for c in cases if c['level'].upper()=='E2E']
    if not e2e:errs.append('No approved formal E2E cases')
    if len({c['tc_id'] for c in e2e})!=len(e2e):errs.append('Duplicate TC IDs')
    junit,count=junit_parse(junit_xml or (run_dir/'junit'),errs)
    if count<1:errs.append('Zero tests executed')
    manifest_results={}
    for c in e2e:
        tc=c['tc_id'];tcdir=run_dir/tc
        if not re.fullmatch(r'TC-\d+',tc):errs.append('Invalid TC ID '+tc);continue
        if c['approval_state']!='APPROVED':errs.append(tc+': not approved')
        testref=(c.get('test_file') or '').split('#',1)[0].strip()
        repo=Path(repo_root).resolve() if repo_root is not None else (Path(run.get('repository_root')).resolve() if run.get('repository_root') else (run_dir.parents[3] if len(run_dir.parents)>3 else run_dir))
        source=(repo/testref).resolve()
        if not (testref.endswith('.java') and source.is_relative_to(repo.resolve()) and source.is_file()):
            errs.append(tc+': missing formal Java test source')
        if testref.startswith('examples/') or 'SmokeE2eTest' in testref:errs.append(tc+': DEMO_ONLY source')
        mp=tcdir/'manifest.json'
        try:m=json.loads(mp.read_text(encoding='utf-8'))
        except (OSError,ValueError):errs.append(tc+': missing/invalid manifest');continue
        manifest_results[tc]=m.get('result')
        for field,expected in (('change_id',chg),('run_id',run_dir.name),('tc_id',tc),('snapshot_id',run.get('snapshot_id')),('environment_origin',run.get('environment_origin')),('runner_token_digest',run.get('runner_token_digest'))):
            if m.get(field)!=expected:errs.append(f'{tc}: {field} mismatch')
        if m.get('schema_version')!=2:errs.append(tc+': not v5 manifest')
        if m.get('result')!='PASS':errs.append(tc+': not PASS')
        if len(junit.get(tc,[]))!=1 or not all(junit[tc]):errs.append(tc+': must have exactly one passing JUnit test')
        shots=m.get('screenshots',[]);verified=False
        if not isinstance(shots,list) or len(shots)<2:errs.append(tc+': missing before/verified screenshots');shots=[]
        seen=set()
        for shot in shots:
            if not isinstance(shot,dict):errs.append(tc+': malformed screenshot entry');continue
            name=shot.get('file','')
            if not isinstance(name,str) or not re.fullmatch(r'\d{2}-[A-Za-z0-9_-]+\.png',name):errs.append(tc+': unsafe screenshot filename');continue
            if name in seen:errs.append(tc+': duplicate screenshot')
            seen.add(name)
            if shot.get('step')!=name[:-4]:errs.append(tc+': screenshot step differs')
            path=tcdir/name
            if not path.is_file() or not valid_png(path):errs.append(tc+': invalid PNG '+name);continue
            if sha(path)!=shot.get('sha256'):errs.append(tc+': screenshot SHA mismatch')
            if name.endswith('-result-verified.png'):verified=True
        if not verified:errs.append(tc+': no verified-result screenshot')
        t=tcdir/'trace.zip'
        if m.get('trace_file')!='trace.zip' or not t.is_file() or not valid_trace(t):errs.append(tc+': invalid Playwright trace')
        elif sha(t)!=m.get('trace_sha256'):errs.append(tc+': trace SHA mismatch')
    for tc in junit:
        if tc not in {c['tc_id'] for c in e2e}:errs.append(tc+': unapproved JUnit TC executed')
    return errs


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('change_dir',type=Path)
    ap.add_argument('run_dir',type=Path)
    ap.add_argument('--require-pass',action='store_true')
    ap.add_argument('--junit-xml',type=Path)
    ap.add_argument('--repo-root',type=Path,help='Repository root (required for arbitrary/custom storage layouts without run-context repository_root)')
    args=ap.parse_args()
    errors=verify(args.change_dir,args.run_dir,True,args.junit_xml,repo_root=args.repo_root)
    for e in errors:print('ERROR:',e)
    print(('FAIL' if errors else 'PASS')+': v5 evidence consistency (not independent execution attestation)')
    return 1 if errors else 0
if __name__=='__main__':sys.exit(main())
