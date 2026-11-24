"""Read-only multi-repository G2 barrier (no Claude or dashboard dependency)."""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import re

GROUPS = '.sdd-groups.json'
PROFILES = '.sdd-workspaces.json'
CHG = re.compile(r'CHG-[0-9]+\Z')
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z')
HEX = re.compile(r'[0-9a-f]{64}\Z')

class GroupError(ValueError):
    pass


def safe(root, relative):
    root = Path(root)
    p = root
    for part in Path(relative).parts:
        if part in ('..', '/', '.') or Path(part).is_absolute():
            raise GroupError('Unsafe group path')
        p = p / part
        if p.is_symlink():
            raise GroupError(f'Group file path is a symlink: {p}')
    return p


def read(root, relative):
    p = safe(root, relative)
    if not p.exists():
        return None
    if not p.is_file():
        raise GroupError(f'Not a regular file: {p}')
    return p.read_bytes()


def decode(raw):
    obj = json.loads(raw)
    if not isinstance(obj, dict):
        raise GroupError('Invalid group JSON object')
    return obj


def digest(data):
    return hashlib.sha256(data).hexdigest()


def groups(kit):
    raw = read(kit, GROUPS)
    obj = decode(raw) if raw is not None else {'schema': 1, 'groups': {}}
    if obj.get('schema') != 1 or not isinstance(obj.get('groups'), dict):
        raise GroupError('Invalid group registry')
    return raw, obj


def group_for(kit, change_id):
    if not CHG.fullmatch(change_id):
        raise GroupError('Invalid change ID')
    _, registry = groups(kit)
    group = registry['groups'].get(change_id)
    if group is None:
        return None
    if not isinstance(group, dict) or not isinstance(group.get('members'), dict) or not 2 <= len(group['members']) <= 10 or not isinstance(group.get('plan'), str):
        raise GroupError('Invalid group membership/plan')
    profile_raw = read(kit, PROFILES)
    if profile_raw is None:
        raise GroupError('Group profile registry missing')
    profiles = decode(profile_raw).get('profiles', {})
    roots = []
    contexts = set()
    for name, member in group['members'].items():
        if not NAME.fullmatch(name) or not isinstance(member, dict) or profiles.get(name) != member:
            raise GroupError(f'Group profile changed: {name}; original membership must be restored')
        path = member.get('codebase')
        execution = member.get('execution')
        if not isinstance(path, str) or execution not in ('local','devcontainer'):
            raise GroupError(f'Invalid profile: {name}')
        root = Path(path)
        if not root.is_absolute() or not root.is_dir() or root.resolve() != root:
            raise GroupError(f'Group root unavailable: {name}')
        if any(root.is_relative_to(x) or x.is_relative_to(root) for x in roots):
            raise GroupError('Duplicate or nested repositories are not supported in a group')
        roots.append(root); contexts.add(execution)
    if len(contexts) != 1:
        raise GroupError('All group members must use the same execution context')
    return group


def plan_hashes(root, change_id):
    if not CHG.fullmatch(change_id):
        raise GroupError('Invalid change ID')
    base = f'docs/changes/{change_id}/'
    files = {'requirements_hash': base+'requirements.csv', 'test_cases_hash': base+'test_cases.csv',
             'scope_hash': base+'change-plan.md', 'project_config_hash': '.claude/local/project-env.json'}
    out = {}
    for name, rel in files.items():
        data = read(root, rel)
        if data is None:
            raise GroupError(f'G2 input missing: {root}/{rel}')
        out[name] = digest(data)
    return out


def approval_path(change_id):
    if not CHG.fullmatch(change_id):
        raise GroupError('Invalid change ID')
    return f'.claude/local/approvals/{change_id}-G2.json'


def validate_time(payload):
    try:
        approved = datetime.fromisoformat(payload['approved_at'].replace('Z','+00:00'))
        if approved.tzinfo is None:
            raise ValueError('missing timezone')
        if payload.get('expires_at') is not None:
            expiry = datetime.fromisoformat(payload['expires_at'].replace('Z','+00:00'))
            if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
                raise ValueError('expired')
    except (KeyError, TypeError, ValueError, AttributeError) as ex:
        raise GroupError('Invalid or expired G2 approval timestamp') from ex


def verify_group(kit, change_id, *, root=None, execution=None, required=False):
    """Validate ALL members before any member may pass G2. No network/Git writes."""
    group = group_for(kit, change_id)
    if group is None:
        if required:
            raise GroupError('Group record is missing; re-create/approve the group')
        return None
    if root is not None:
        if not any(m['codebase']==str(root) and m['execution']==execution for m in group['members'].values()):
            raise GroupError('Selected codebase is not a member of this change group')
    approval = group.get('g2')
    if not isinstance(approval, dict) or not isinstance(approval.get('records'), dict):
        raise GroupError('Group G2 is pending: approve all member plans together')
    plan_hash = digest(group['plan'].encode('utf-8'))
    if not group['plan'].strip() or approval.get('plan_hash') != plan_hash or set(approval['records']) != set(group['members']):
        raise GroupError('Group G2 plan/membership changed; re-approval required')
    validate_time(approval)
    if not isinstance(approval.get('approval_id'),str) or not re.fullmatch(r'[0-9a-f]{32}',approval['approval_id']):
        raise GroupError('Invalid group approval generation')
    if not isinstance(approval.get('approver'), str) or not approval['approver'].strip():
        raise GroupError('Group G2 approver missing')
    for name, member in group['members'].items():
        member_root = Path(member['codebase'])
        try:
            raw = read(member_root, approval_path(change_id))
            if raw is None or digest(raw) != approval['records'][name]:
                raise GroupError('G2 record missing or changed')
            obj = decode(raw)
            if (obj.get('change_id') != change_id or obj.get('gate') != 'G2' or obj.get('decision') != 'APPROVED'
                    or obj.get('group_id') != change_id or obj.get('group_plan_hash') != plan_hash
                    or obj.get('approver') != approval['approver'] or obj.get('approved_at') != approval['approved_at']
                    or obj.get('group_approval_id') != approval.get('approval_id')):
                raise GroupError('G2 record does not match group approval')
            validate_time(obj)
            for key, value in plan_hashes(member_root, change_id).items():
                if obj.get(key) != value:
                    raise GroupError(f'G2 input changed: {key}')
        except (OSError, ValueError) as ex:
            raise GroupError(f'{name}: {ex}; group G2 blocked') from ex
    return group


def evidence_context(kit, change_id, root, execution, snapshot, *, required=False):
    group=verify_group(kit,change_id,root=root,execution=execution,required=required)
    if group is None:return None
    return {'group_id':change_id,'group_approval_id':group['g2']['approval_id'],
            'snapshots':{name:snapshot(Path(m['codebase']))['snapshot_id'] for name,m in group['members'].items()}}
