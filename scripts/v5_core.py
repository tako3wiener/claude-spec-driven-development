"""Shared provenance, digests and operator-recorded approvals.

Gate approvals are ordinary local JSON records, not digital signatures.
Optional R19 gateway receipt verification remains separate.
Local hashes and hooks are not tamper-proof execution attestations.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
from datetime import datetime, timezone
from project_setup import ROOT, KIT_ROOT, CONFIG, EXECUTION_CONTEXT, SetupError

CHANGE_RE = re.compile(r'CHG-[0-9]+\Z')
RUN_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,100}\Z')


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_digest(path):
    return digest(Path(path).read_bytes())


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def safe_file(root, relative):
    p = (Path(root) / relative).resolve()
    if not p.is_relative_to(Path(root).resolve()):
        raise SetupError('File path escapes the selected repository')
    return p


def plan_hashes(change_id):
    if not CHANGE_RE.fullmatch(change_id):
        raise SetupError('Invalid change ID')
    base = ROOT / 'docs' / 'changes' / change_id
    files = {'requirements_hash': base / 'requirements.csv',
             'test_cases_hash': base / 'test_cases.csv',
             'scope_hash': base / 'change-plan.md',
             'project_config_hash': ROOT / '.claude' / 'local' / 'project-env.json'}
    result = {}
    for name, path in files.items():
        if not path.is_file():
            raise SetupError(f'G2 input is missing: {path}')
        result[name] = file_digest(path)
    return result


def snapshot(repo=ROOT):
    repo = Path(repo).resolve()
    def git(*argv, binary=False):
        p = subprocess.run(['git','-C',str(repo),*argv], capture_output=True, check=False)
        if p.returncode:
            raise SetupError(f'Git provenance failed: {argv[0]}')
        return p.stdout if binary else p.stdout.decode('utf-8', 'replace').strip()
    head = git('rev-parse','HEAD')
    branch = git('branch','--show-current')
    diff = git('diff','--binary','HEAD', binary=True)
    untracked = [x.decode('utf-8', 'surrogateescape') for x in git('ls-files','--others','--exclude-standard','-z',binary=True).split(b'\0') if x]
    files = []
    for name in sorted(untracked):
        # Build outputs are not source files; Git ignore rules remain primary.
        if any(part in {'target','build','.gradle','.idea'} for part in Path(name).parts):
            continue
        path = safe_file(repo, name)
        if path.is_symlink() or not path.is_file():
            raise SetupError(f'Untracked symlink/non-file cannot be snapshotted: {name}')
        files.append({'path':name,'sha256':file_digest(path)})
    data = {'head':head,'branch':branch,'diff_sha256':digest(diff),
            'untracked':files,'dirty': bool(diff or files)}
    data['snapshot_id'] = digest(canonical(data))
    return data


def verified_signature(document, signature, public_key):
    """Verify RSA/ECDSA detached SHA-256 signature with OpenSSL, fail closed."""
    for f in (document, signature, public_key):
        if not Path(f).is_file():
            raise SetupError(f'Signature verification input missing: {f}')
    p = subprocess.run(['openssl', 'dgst', '-sha256', '-verify', str(public_key),
                        '-signature', str(signature), str(document)],
                       capture_output=True, text=True, check=False, timeout=10)
    if p.returncode != 0:
        raise SetupError('External approval signature is invalid')
    return json.loads(Path(document).read_text(encoding='utf-8'))


def external_public_key():
    path = os.environ.get('SDD_APPROVAL_PUBLIC_KEY')
    if not path:
        raise SetupError('SDD_APPROVAL_PUBLIC_KEY must point to an operator-managed public key outside the repository')
    resolved = Path(path).resolve()
    if resolved.is_relative_to(ROOT.resolve()):
        raise SetupError('Approval public key must be outside the AI-writable repository')
    return resolved


def group_for_change(change_id, *, required=False):
    """Read group membership in standalone mode, without trusting a caller's cwd."""
    bound=os.environ.get('SDD_GROUP_ID')
    if bound and bound!=change_id:
        raise SetupError('Group change ID is fixed at launch')
    registry=KIT_ROOT/'.sdd-groups.json'
    if not (registry.exists() or registry.is_symlink() or bound or required):
        return None
    try:
        from group_gate import group_for
        group=group_for(KIT_ROOT,change_id)
        if group is None:
            if required or bound:raise SetupError('Group registry is missing; re-approve the group')
            return None
        if EXECUTION_CONTEXT is None or not any(m['codebase']==str(ROOT) and m['execution']==EXECUTION_CONTEXT for m in group['members'].values()):
            raise SetupError('Current codebase/execution context is not a member of this group')
        return group
    except (ImportError,ValueError,OSError,TypeError,KeyError) as ex:
        raise SetupError(f'Invalid group registry: {ex}') from ex


def require_group_g2(change_id, *, required=False):
    group=group_for_change(change_id,required=required)
    if group is not None:
        from group_gate import verify_group
        try:return verify_group(KIT_ROOT,change_id,root=ROOT,execution=EXECUTION_CONTEXT,required=True)
        except (ValueError,OSError,TypeError,KeyError) as ex:raise SetupError(f'Group G2 blocked: {ex}') from ex
    return None


def group_evidence_context(change_id, *, required=False):
    group=group_for_change(change_id,required=required)
    if group is None:return None
    from group_gate import evidence_context
    try:return evidence_context(KIT_ROOT,change_id,ROOT,EXECUTION_CONTEXT,snapshot,required=True)
    except (ValueError,OSError,TypeError,KeyError) as ex:raise SetupError(f'Group evidence blocked: {ex}') from ex


def verify_approval(change_id, gate, *, context=None):
    """Validate a human-recorded local approval; identity is not authenticated."""
    if not CHANGE_RE.fullmatch(change_id) or gate not in ('G2','G6','NET'):
        raise SetupError('Invalid change ID or gate')
    folder = ROOT / '.claude' / 'local' / 'approvals'
    document = folder / f'{change_id}-{gate}.json'
    if not document.is_file():
        raise SetupError(f'{gate}: human approval record missing')
    content = json.loads(document.read_text(encoding='utf-8'))
    result=validate_approval_content(change_id, gate, content, context=context)
    if gate=='G2':
        require_group_g2(change_id,required=bool(content.get('group_id')))
    elif gate=='G6':
        group=require_group_g2(change_id,required=bool(content.get('group_context')))
        if group is not None:
            current=group_evidence_context(change_id,required=True)
            if not context or context.get('group_context')!=current:
                raise SetupError('G6 requires fresh E2E evidence for the entire group')
    return result


def validate_approval_content(change_id, gate, content, *, context=None):
    if not CHANGE_RE.fullmatch(change_id) or gate not in ('G2','G6','NET') or not isinstance(content, dict):
        raise SetupError('Invalid approval record')
    if content.get('change_id') != change_id or content.get('gate') != gate or content.get('decision') != 'APPROVED':
        raise SetupError(f'{gate}: approval does not match change or decision')
    if not isinstance(content.get('approver'), str) or not content['approver'].strip() or not content.get('approved_at'):
        raise SetupError(f'{gate}: human approver / timestamp missing')
    try:
        approved = datetime.fromisoformat(content['approved_at'].replace('Z','+00:00'))
        if approved.tzinfo is None:
            raise ValueError('missing timezone')
    except (TypeError, ValueError, AttributeError) as ex:
        raise SetupError(f'{gate}: approval timestamp invalid') from ex
    expiry=content.get('expires_at')
    if expiry is not None:
        try:
            deadline=datetime.fromisoformat(expiry.replace('Z','+00:00'))
            if deadline.tzinfo is None or deadline <= datetime.now(timezone.utc):
                raise ValueError('expired or missing timezone')
        except (ValueError,TypeError,AttributeError) as ex:
            raise SetupError(f'{gate}: approval expired/invalid expiry') from ex
    if gate == 'G2':
        for key,value in plan_hashes(change_id).items():
            if content.get(key) != value:
                raise SetupError(f'G2 approval is stale: {key}')
    elif gate == 'G6':
        if not context or content.get('run_id') != context.get('run_id') or content.get('snapshot_id') != context.get('snapshot_id') or content.get('summary_sha256') != context.get('summary_sha256'):
            raise SetupError('G6 approval does not bind to this run/snapshot/summary')
        if context.get('group_context')!=content.get('group_context'):
            raise SetupError('G6 approval does not bind to this group evidence context')
    else:
        if not context or content.get('allowed_origin') != context.get('allowed_origin'):
            raise SetupError('NET attestation does not match this origin')
        if EXECUTION_CONTEXT is not None and content.get('project_config_hash') != file_digest(CONFIG):
            raise SetupError('NET approval is stale: project/execution configuration changed')
        if content.get('isolation_mode') not in ('container-egress-policy','network-firewall','dedicated-isolated-host'):
            raise SetupError('NET attestation requires a verified external network isolation mode')
        if content.get('dedicated_test_credentials') is not True:
            raise SetupError('NET attestation requires least-privilege test credentials')
        if content.get('synthetic_test_data') is not True or content.get('trace_storage_approved') is not True:
            raise SetupError('NET attestation requires synthetic test data and permission to store Playwright traces')
    return content


def new_run_id():
    return 'RUN-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + secrets.token_hex(6)


def atomic_json(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + secrets.token_hex(4) + '.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as f:
        json.dump(content, f, ensure_ascii=False, indent=2)
        f.write('\n')
    os.replace(tmp,path)
