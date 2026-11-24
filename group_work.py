"""Coordinate one change ID across 2–10 distinct Git roots in the same environment."""
from __future__ import annotations
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import uuid
from scripts import group_gate as gate


def add_parser(subparsers):
    p=subparsers.add_parser('group',help='coordinate a change across multiple codebases')
    commands=p.add_subparsers(dest='group_command',required=True)
    q=commands.add_parser('create');q.add_argument('--change-id',required=True);q.add_argument('--profiles',nargs='+',required=True);q.add_argument('--summary',default='');q.add_argument('--plan-file',type=Path)
    q=commands.add_parser('plan');q.add_argument('--change-id',required=True);q.add_argument('--file',type=Path,required=True)
    q=commands.add_parser('status');q.add_argument('--change-id',required=True)
    q=commands.add_parser('approve-g2');q.add_argument('--change-id',required=True)
    q=commands.add_parser('run');q.add_argument('--change-id',required=True);q.add_argument('--profile',required=True);q.add_argument('--model');q.add_argument('--dry-run',action='store_true')
    q=commands.add_parser('list')


def read_plan(path):
    if not path.is_file() or path.stat().st_size>1_000_000:
        raise gate.GroupError('Plan must be an existing text file (<=1 MB)')
    text=path.read_text(encoding='utf-8')
    if not text.strip():raise gate.GroupError('Common plan is empty')
    return text


def get_group(api, change_id):
    obj=gate.group_for(api.SOURCE,change_id)
    if obj is None:raise gate.GroupError('Unknown change group; run group create')
    # Confirm actual Git roots, not just path existence.
    for name, m in obj['members'].items():
        root,cfg=api.selected(name)
        if str(root)!=m['codebase'] or cfg!=m:raise gate.GroupError(f'Group profile changed: {name}')
    return obj


def save(api, raw, obj):
    if gate.read(api.SOURCE,gate.GROUPS)!=raw:raise gate.GroupError('Group registry changed concurrently; retry')
    api.atomic_write(api.safe_path(api.SOURCE,gate.GROUPS),api.encode_json(obj))


def create(api,args):
    if not gate.CHG.fullmatch(args.change_id):raise gate.GroupError('Invalid change ID')
    if not 2<=len(args.profiles)<=10 or len(set(args.profiles))!=len(args.profiles):
        raise gate.GroupError('Select 2–10 distinct profiles')
    raw,registry=gate.groups(api.SOURCE)
    if args.change_id in registry['groups']:raise gate.GroupError('Change group already exists; membership is fixed')
    members={};roots=[];contexts=set()
    for name in args.profiles:
        root,cfg=api.selected(name);api.reject_legacy(root)
        if any(root.is_relative_to(p) or p.is_relative_to(root) for p in roots):raise gate.GroupError('Duplicate or nested codebases')
        roots.append(root);contexts.add(cfg['execution']);members[name]=dict(cfg)
    if len(contexts)!=1:raise gate.GroupError('Use profiles from the same execution environment')
    # Existing local G2 must be replaced by a fresh group approval, not reused silently.
    text=read_plan(args.plan_file) if args.plan_file else args.summary
    registry['groups'][args.change_id]={'members':members,'plan':text,'g2':None}
    save(api,raw,registry)
    print(f'Created {args.change_id}: {", ".join(members)}. Prepare G1 plans in each repository, then group approve-g2.')


def update_plan(api,args):
    raw,registry=gate.groups(api.SOURCE);get_group(api,args.change_id)
    group=registry['groups'][args.change_id];new=read_plan(args.file)
    if new!=group['plan']:
        group['plan']=new;group['g2']=None;save(api,raw,registry)
        print('Common plan updated; group G2 approval invalidated.')
    else:print('Plan unchanged.')


# Run existing, read-only validators with each repository as cwd; never import a repository's own Python code.
PROBE = r'''
import json,sys
from pathlib import Path
from project_setup import ROOT,CONFIG,EXECUTION_CONTEXT,load_config,validate
from v5_core import plan_hashes,verify_approval,file_digest,RUN_RE
from approval_gate import g6_context
from artifact_paths import artifact_root
change=sys.argv[1]; mode=sys.argv[2]; out={}
try:
    cfg=validate(load_config());out['setup']='READY'
except Exception as e:
    print(json.dumps({'setup':'BLOCKED','error':str(e)}));sys.exit(0)
try:
    out['hashes']=plan_hashes(change)
except Exception as e:out['plan_error']=str(e)
if mode=='status':
    try:
        verify_approval(change,'NET',context={'allowed_origin':cfg['allowed_origin'],'project_config_hash':file_digest(CONFIG)});out['net']='VERIFIED'
    except Exception as e:out['net']='PENDING_OR_STALE';out['net_reason']=str(e)
    try:
        from group_gate import read
        raw=read(ROOT,f'.claude/local/approvals/{change}-G6.json');d=json.loads(raw);run=d.get('run_id','')
        if not isinstance(run,str) or not RUN_RE.fullmatch(run):raise ValueError('Invalid run ID')
        rd=artifact_root(ROOT,cfg,'e2e')/change/run
        verify_approval(change,'G6',context=g6_context(rd));out['g6']='VERIFIED'
    except Exception as e:out['g6']='PENDING_OR_STALE';out['g6_reason']=str(e)
print(json.dumps(out,ensure_ascii=False))
'''


def probe(api, member, change_id, mode):
    root=Path(member['codebase']);env=api.session_env(root,member['execution'],change_id)
    # -I prevents cwd/PYTHONPATH from replacing trusted tool modules.
    code=f'import sys;sys.path.insert(0,{str(api.SOURCE/"scripts")!r});\n'+PROBE
    p=subprocess.run([sys.executable,'-I','-B','-c',code,change_id,mode],cwd=root,env=env,capture_output=True,text=True,check=True,timeout=30)
    return json.loads(p.stdout)


def prepare(api,group,change_id):
    out={}
    for name,m in group['members'].items():
        root=Path(m['codebase']);api.reject_legacy(root);api.protect_local(root)
        data=probe(api,m,change_id,'plan')
        hashes=gate.plan_hashes(root,change_id)
        if data.get('setup')!='READY' or data.get('hashes')!=hashes:
            raise gate.GroupError(f'{name}: setup/plan not ready: {data.get("error",data.get("plan_error","changed during read"))}')
        out[name]=hashes
    return out


def apply_batch(api,changes):
    # Changes are fully preflighted, then written. Roll back already-written records on error.
    for root,name,old,new in changes:
        if api.read(root,name)!=old:raise gate.GroupError(f'File changed during approval: {root}/{name}')
    done=[]
    try:
        for root,name,old,new in changes:
            api.atomic_write(api.safe_path(root,name),new);done.append((root,name,old,new))
    except (OSError,ValueError,api.Conflict):
        for root,name,old,new in reversed(done):
            if api.read(root,name)!=new:continue  # never clobber another writer
            path=api.safe_path(root,name)
            if old is None:path.unlink()
            else:api.atomic_write(path,old)
        raise


def approve(api,args):
    cid=args.change_id;raw,registry=gate.groups(api.SOURCE);group=get_group(api,cid)
    if not group['plan'].strip():raise gate.GroupError('Set common plan via group plan --file first')
    if not sys.stdin.isatty() or not sys.stdout.isatty():raise gate.GroupError('Human group approval requires an interactive terminal')
    hashes=prepare(api,group,cid)
    print(f'Common plan for {cid}:\n{group["plan"]}')
    for name,member in group['members'].items():
        print(f'\n{name}: {member["codebase"]}/docs/changes/{cid}/');print(json.dumps(hashes[name],indent=2))
    print('Review common dependencies, change order, integration tests and ALL member requirements, test cases, plans and project settings.')
    approver=input('Human approver name/ID: ').strip()
    if not approver or input(f'Type APPROVE G2 {cid} to confirm all members: ').strip()!=f'APPROVE G2 {cid}':raise gate.GroupError('Group approval not confirmed')
    if gate.read(api.SOURCE,gate.GROUPS)!=raw or get_group(api,cid)!=group or prepare(api,group,cid)!=hashes:
        raise gate.GroupError('Group/plan changed during review; approve again')
    now=datetime.now(timezone.utc).isoformat(timespec='seconds');approval_id=uuid.uuid4().hex;plan_hash=gate.digest(group['plan'].encode('utf-8'));records={};changes=[]
    for name,member in group['members'].items():
        root=Path(member['codebase']);rel=gate.approval_path(cid)
        obj={'change_id':cid,'gate':'G2','decision':'APPROVED','approver':approver,'approved_at':now,
             'group_id':cid,'group_approval_id':approval_id,'group_plan_hash':plan_hash,**hashes[name]}
        data=api.encode_json(obj);records[name]=gate.digest(data);changes.append((root,rel,api.read(root,rel),data))
    registry['groups'][cid]['g2']={'approver':approver,'approved_at':now,'approval_id':approval_id,'plan_hash':plan_hash,'records':records}
    changes.append((api.SOURCE,gate.GROUPS,raw,api.encode_json(registry)))
    apply_batch(api,changes)
    gate.verify_group(api.SOURCE,cid,required=True)
    print(f'Group G2 approved: {len(records)} repositories. Existing G1 sessions may continue after verification.')


def status(api,args):
    cid=args.change_id;group=get_group(api,cid)
    try:gate.verify_group(api.SOURCE,cid,required=True);g2='VERIFIED';reason=None
    except (ValueError,OSError) as ex:g2='PENDING_OR_STALE';reason=str(ex)
    members={}
    for name,m in group['members'].items():
        try:
            data=probe(api,m,cid,'status')
        except (ValueError,OSError,subprocess.SubprocessError) as ex:data={'setup':'BLOCKED','error':str(ex)}
        data['plan']='READY' if 'hashes' in data else 'PENDING_OR_STALE';data.pop('hashes',None)
        data['g2']=g2
        root=Path(m['codebase']);data['codebase']=str(root)
        try:
            raw=gate.read(root,f'.claude/local/progress/{cid}.json')
            if raw:
                progress=gate.decode(raw);branch=api.git(root,'branch','--show-current').stdout.strip()
                if progress.get('branch')==branch:data['reported_progress']={k:progress.get(k) for k in ('gate','state','next_action','branch')}
                else:data['reported_progress']='BRANCH_MISMATCH'
        except (ValueError,OSError):data['reported_progress']='UNREADABLE'
        members[name]=data
    ready=g2=='VERIFIED' and all(m.get('setup')=='READY' and m.get('net')=='VERIFIED' and m.get('g6')=='VERIFIED' for m in members.values())
    print(json.dumps({'change_id':cid,'group_g2':g2,'g2_reason':reason,'members':members,
                      'all_member_acceptances_verified':ready,'notice':'Individual gate records only; cross-system acceptance and coordinated deployment still require human review.'},ensure_ascii=False,indent=2))


def dispatch(api,args):
    cmd=args.group_command
    if cmd=='create':create(api,args)
    elif cmd=='plan':update_plan(api,args)
    elif cmd=='approve-g2':approve(api,args)
    elif cmd=='status':status(api,args)
    elif cmd=='list':print(json.dumps(gate.groups(api.SOURCE)[1],ensure_ascii=False,indent=2))
    elif cmd=='run':
        get_group(api,args.change_id)
        return api.run(args)
    return 0
