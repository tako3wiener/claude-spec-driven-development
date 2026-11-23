"""Safe repository-relative artifact storage configuration (v5 compatible).

The repository must own all output paths. Paths are never interpreted by a shell.
"""
from __future__ import annotations
from pathlib import Path, PurePosixPath
import re
import subprocess

DEFAULT_TEST_RESULTS = 'reports/test-results'
DEFAULT_E2E_EVIDENCE = 'reports/e2e'
DEFAULT_RETENTION_DAYS = 90
PROTECTED_PARTS = {'.git', '.claude', 'src', 'docs', 'scripts', 'templates', 'examples',
                   'target', 'build', '.gradle', 'node_modules', '.idea'}


def artifact_settings(config: dict) -> dict:
    from setup_errors import SetupError
    unit = validate_relative(config.get('unit_integration_results_dir', DEFAULT_TEST_RESULTS))
    e2e = validate_relative(config.get('e2e_evidence_dir', DEFAULT_E2E_EVIDENCE))
    days = config.get('artifact_retention_days', DEFAULT_RETENTION_DAYS)
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 3650:
        raise SetupError('保存期間は1～3650日の整数で指定してください。')
    if unit == e2e or unit.startswith(e2e + '/') or e2e.startswith(unit + '/'):
        raise SetupError('単体・結合テストとE2Eエビデンスの保存先は重複・包含できません。')
    return {'unit_integration_results_dir': unit, 'e2e_evidence_dir': e2e,
            'artifact_retention_days': days}


def validate_relative(value: str) -> str:
    from setup_errors import SetupError
    if not isinstance(value, str) or not value.strip():
        raise SetupError('保存先が空です。')
    if (value != value.strip() or '\\' in value or '\x00' in value or
            any(ord(c) < 32 for c in value) or re.match(r'^[A-Za-z]:', value)):
        raise SetupError('保存先に無効な文字またはWindows絶対パスが含まれています。')
    value = value.rstrip('/')
    p = PurePosixPath(value)
    if p.is_absolute() or len(p.parts) < 2 or any(part in ('.', '..') for part in value.split('/')):
        raise SetupError('保存先はリポジトリ内の2階層以上の相対パスで、.. や絶対パスは使えません。')
    if any(part in PROTECTED_PARTS or part.startswith('.') for part in p.parts):
        raise SetupError('保存先にソース・設定・隠しディレクトリを指定できません。')
    if not all(re.fullmatch(r'[A-Za-z0-9_-]+', part) for part in p.parts):
        raise SetupError('保存先は英数字・ハイフン・アンダースコアのディレクトリ名のみ使用できます。')
    return p.as_posix()


def artifact_root(repo: Path, config: dict, kind: str) -> Path:
    from setup_errors import SetupError
    key = {'unit': 'unit_integration_results_dir', 'e2e': 'e2e_evidence_dir'}.get(kind)
    if key is None:
        raise SetupError('不正な証跡種別です。')
    rel = artifact_settings(config)[key]
    repo = Path(repo).resolve()
    candidate = repo / rel
    # Deny symlinks at any ancestor or artifact directory itself, including dangling links.
    walk = repo
    for part in PurePosixPath(rel).parts:
        walk = walk / part
        if walk.is_symlink():
            raise SetupError('保存先にシンボリックリンクは使用できません。')
    resolved = candidate.resolve()
    if not resolved.is_relative_to(repo):
        raise SetupError('保存先がリポジトリの外を指しています。')
    return candidate


def ensure_untracked_destination(repo: Path, config: dict) -> None:
    """Reject ignoring files that are already tracked by Git."""
    from setup_errors import SetupError
    for kind in ('unit', 'e2e'):
        path = artifact_root(repo, config, kind)
        rel = path.relative_to(repo).as_posix()
        p = subprocess.run(['git', '-C', str(repo), 'ls-files', '--', rel],
                           capture_output=True, text=True, check=False)
        if p.returncode or p.stdout.strip():
            raise SetupError(f'保存先にGit管理ファイルが含まれるか検査できません: {rel}')


MANAGED_START = '# BEGIN Claude SDD generated artifact ignores'
MANAGED_END = '# END Claude SDD generated artifact ignores'


def update_gitignore(repo: Path, config: dict) -> None:
    """Update a bounded section; preserve users' existing .gitignore rules."""
    from setup_errors import SetupError
    f = Path(repo) / '.gitignore'
    if f.is_symlink():
        raise SetupError('.gitignore にシンボリックリンクは使用できません。')
    old = f.read_text(encoding='utf-8') if f.exists() else ''
    if old.count(MANAGED_START) != old.count(MANAGED_END) or old.count(MANAGED_START) > 1:
        raise SetupError('.gitignore の管理ブロックが不正です。手動確認してください。')
    storage = artifact_settings(config)
    preserved = set()
    if MANAGED_START in old:
        prefix, rest = old.split(MANAGED_START, 1)
        old_block, suffix = rest.split(MANAGED_END, 1)
        for line in old_block.splitlines():
            rule=line.strip()
            if rule.startswith('/') and rule.endswith('/'):
                try:preserved.add(validate_relative(rule[1:-1]))
                except SetupError:raise SetupError('既存の管理ブロックに安全でないGit除外ルールがあります。')
    preserved.update([storage['unit_integration_results_dir'],storage['e2e_evidence_dir']])
    # Preserve previously selected destinations so old evidence never becomes trackable.
    block = '\n'.join([MANAGED_START] + [f'/{item}/' for item in sorted(preserved)] + [MANAGED_END])
    if MANAGED_START in old:
        new = prefix + block + suffix
    else:
        new = old.rstrip('\n') + '\n\n' + block + '\n'
    if new != old:
        f.write_text(new, encoding='utf-8')
