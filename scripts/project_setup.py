#!/usr/bin/env python3
"""Local repository/test-environment setup for Claude Code. Standard library only.

The selected repository is the directory containing this starter kit. It is intentionally
not possible to point Claude at an unrelated repository from the same running session.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from artifact_paths import (DEFAULT_TEST_RESULTS, DEFAULT_E2E_EVIDENCE, DEFAULT_RETENTION_DAYS,
                            artifact_settings, artifact_root, ensure_untracked_destination, update_gitignore)

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / '.claude' / 'local' / 'project-env.json'
PROFILE = ROOT / '.claude' / 'local' / 'project-profile.md'
ALLOWED_ENV_NAMES = {'local', 'test', 'staging'}
PRODUCTION_PATTERN = re.compile(r'(^|[.\-])(prod|production|live)([.\-]|$)', re.I)

from setup_errors import SetupError


def git_root() -> Path:
    p = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', '--show-toplevel'],
                       capture_output=True, text=True, check=False)
    if p.returncode != 0 or not p.stdout.strip():
        raise SetupError('Gitリポジトリではありません。既存Gitリポジトリのルートにキットを配置してください。')
    result = Path(p.stdout.strip()).resolve()
    if result != ROOT.resolve():
        raise SetupError(f'キットの配置先 {ROOT} がGitルート {result} と異なります。リポジトリのルートで起動してください。')
    return result


def ensure_inside_root(path_text: str, *, directory: bool = True) -> tuple[Path, str]:
    chosen = (ROOT / path_text).resolve()
    try:
        rel = chosen.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise SetupError('コードベースや実行ディレクトリは、このGitリポジトリ内から選んでください。') from exc
    if directory and not chosen.is_dir():
        raise SetupError(f'対象ディレクトリがありません: {chosen}')
    return chosen, rel.as_posix() or '.'


def detect_build(path: Path) -> str:
    if (path / 'pom.xml').exists():
        return 'maven'
    if any((path / x).exists() for x in ('build.gradle', 'build.gradle.kts', 'settings.gradle', 'settings.gradle.kts')):
        return 'gradle'
    raise SetupError(f'pom.xml / build.gradle がありません: {path}。Javaモジュールのディレクトリを選んでください。')


def parse_url(value: str) -> tuple[str, str]:
    try:
        u = urlsplit(value.strip())
        if u.scheme.lower() not in {'http', 'https'} or not u.hostname:
            raise ValueError('HTTP(S)とホスト名が必須です')
        if u.username or u.password or u.query or u.fragment:
            raise ValueError('URLのユーザー情報、クエリ文字列、フラグメントは指定できません')
        # Raise on invalid port / malformed IPv6
        port = u.port
        hostname = u.hostname.lower()
        if PRODUCTION_PATTERN.search(hostname):
            raise ValueError('本番環境らしいホスト名は設定できません')
        origin_host = f'[{hostname}]' if ':' in hostname else hostname
        origin = f'{u.scheme.lower()}://{origin_host}' + (f':{port}' if port is not None else '')
        path = u.path or '/'
        return urlunsplit((u.scheme.lower(), u.netloc.lower(), path, '', '')), origin
    except (ValueError, AttributeError) as exc:
        raise SetupError(f'E2E URLが不正です: {exc}') from exc


def parse_command(value: str, field: str) -> list[str]:
    try:
        args = shlex.split(value)
    except ValueError as exc:
        raise SetupError(f'{field}の引用符が不正です: {exc}') from exc
    if not args:
        raise SetupError(f'{field}を指定してください')
    executable = Path(args[0]).name.lower()
    if executable not in {'mvn', 'mvnw', 'gradle', 'gradlew'}:
        raise SetupError(f'{field}は Maven/Gradle の直接実行のみ設定できます（shellコマンド不可）。')
    if any(re.search(r'[;&|><`$\n\r]', arg) for arg in args):
        raise SetupError(f'{field}にシェルメタ文字を含めないでください。')
    if not any(('test' in arg.lower() or arg in {'verify', 'check'}) for arg in args[1:]):
        raise SetupError(f'{field}にはテストタスク/ゴールを指定してください。')
    return args


def load_config() -> dict:
    if not CONFIG.is_file():
        raise SetupError('初期設定が未完了です。/project-setup または python3 scripts/project_setup.py init を実行してください。')
    try:
        content = json.loads(CONFIG.read_text(encoding='utf-8'))
        if not isinstance(content, dict):
            raise ValueError('JSON root must be an object')
        return content
    except (ValueError, OSError) as exc:
        raise SetupError(f'設定ファイルを読めません: {exc}') from exc


def validate(config: dict, *, cwd: str | None = None) -> dict:
    repo = git_root()
    if config.get('schema_version') != 1 or not config.get('confirmed_codebase') or not config.get('confirmed_isolated'):
        raise SetupError('設定形式または確認フラグが不正です。/project-setup で再設定してください。')
    if Path(config.get('git_root', '/missing')).resolve() != repo:
        raise SetupError('設定されたGitリポジトリと現在のリポジトリが一致しません。再設定してください。')
    code, rel = ensure_inside_root(config.get('codebase', '__missing__'))
    if config.get('codebase_realpath') != str(code) or rel != config.get('codebase'):
        raise SetupError('対象コードベースが設定時と異なります。再設定してください。')
    if config.get('build_tool') != detect_build(code):
        raise SetupError('ビルド構成が設定時と変わっています。再設定してください。')
    if config.get('environment') not in ALLOWED_ENV_NAMES:
        raise SetupError('許可されていないテスト環境です。')
    url, origin = parse_url(str(config.get('base_url', '')))
    if config.get('base_url') != url or config.get('allowed_origin') != origin:
        raise SetupError('E2E URL / 許可originの設定が不一致です。')
    ensure_inside_root(config.get('e2e_workdir', '__missing__'))
    artifact_settings(config)  # v5 configs without storage fields use safe defaults
    artifact_root(repo, config, 'unit')
    artifact_root(repo, config, 'e2e')
    for field in ('unit_test_argv', 'e2e_test_argv'):
        args = config.get(field)
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            raise SetupError(f'{field}が不正です。')
        parse_command(shlex.join(args), field)
    if cwd is not None:
        # Claude Code can move into subdirectories; allow them but not unrelated repos.
        now = Path(cwd).resolve()
        try:
            now.relative_to(repo)
        except ValueError as exc:
            raise SetupError('Claude Codeの作業ディレクトリが設定したリポジトリの外です。') from exc
    return config


def prompt(text: str, default: str = '') -> str:
    postfix = f' [{default}]' if default else ''
    result = input(f'{text}{postfix}: ').strip()
    return result or default


def confirmed(text: str) -> bool:
    return input(f'{text} (yes/no): ').strip().lower() == 'yes'


def default_test_command(code: Path, tool: str) -> str:
    if tool == 'maven':
        return ('./mvnw test' if (code / 'mvnw').exists() else 'mvn test')
    return ('./gradlew test' if (code / 'gradlew').exists() else 'gradle test')


def main_init(args: argparse.Namespace) -> None:
    repo = git_root()
    if CONFIG.exists() and not args.force:
        raise SetupError('設定済みです。変更する場合は /project-setup で明示的に再確認し、init --force を使用してください。')
    non_interactive = args.non_interactive
    previous = load_config() if CONFIG.exists() and args.force else {}
    if non_interactive:
        needed = ['codebase', 'environment', 'base_url', 'unit_command', 'e2e_command', 'e2e_workdir']
        missing = [k for k in needed if getattr(args, k) is None]
        if missing or not args.confirm_codebase or not args.confirm_isolated:
            raise SetupError('非対話セットアップには全項目と --confirm-codebase --confirm-isolated が必須です。不足: '+','.join(missing))
    elif not sys.stdin.isatty():
        raise SetupError('対話端末ではありません。/project-setup経由で値を確認し、--non-interactiveを使ってください。')
    print(f'Gitルート: {repo}')
    code_text = args.codebase if args.codebase is not None else prompt('対象Javaコードベース（Gitルートからの相対パス）', '.')
    code, rel = ensure_inside_root(code_text)
    build = detect_build(code)
    branch = subprocess.run(['git', 'branch', '--show-current'], capture_output=True, text=True, cwd=repo).stdout.strip()
    print(f'検出: codebase={code} / build={build} / branch={branch or "(detached)"}')
    if not (args.confirm_codebase or (not non_interactive and confirmed('このコードベースを開発対象にしますか？'))):
        raise SetupError('コードベースの確認が取れませんでした。')
    env = args.environment if args.environment is not None else prompt('テスト環境（local/test/staging）', 'local')
    if env not in ALLOWED_ENV_NAMES:
        raise SetupError('本番環境は設定できません。local/test/staging のみです。')
    base = args.base_url if args.base_url is not None else prompt('E2Eテスト対象URL（認証情報なし）', 'http://localhost:8080/')
    url, origin = parse_url(base)
    unit = args.unit_command if args.unit_command is not None else prompt('単体テストコマンド', default_test_command(code, build))
    e2e = args.e2e_command if args.e2e_command is not None else prompt('正式なPlaywright for Java E2Eテストコマンド（DEMO_ONLYは最終判定不可）')
    e2e_dir_text = args.e2e_workdir if args.e2e_workdir is not None else prompt('E2Eコマンド実行ディレクトリ（Gitルートからの相対パス）', '.')
    _, e2e_dir = ensure_inside_root(e2e_dir_text)
    unit_argv, e2e_argv = parse_command(unit, '単体テスト'), parse_command(e2e, 'E2Eテスト')
    old_integration = previous.get('integration_test_argv')
    old_integration_cmd = shlex.join(old_integration) if isinstance(old_integration,list) and old_integration else ''
    integration_command = (args.integration_command if args.integration_command is not None else
                           (old_integration_cmd if non_interactive else prompt('結合テストコマンド（なければ空欄）',old_integration_cmd)))
    integration_argv = parse_command(integration_command, '結合テスト') if integration_command else None
    unit_dir = args.unit_results_dir if args.unit_results_dir is not None else (
        (previous.get('unit_integration_results_dir',DEFAULT_TEST_RESULTS) if non_interactive else prompt('単体・結合テスト結果の集約先（Gitルートからの相対パス）', previous.get('unit_integration_results_dir',DEFAULT_TEST_RESULTS))))
    e2e_storage = args.e2e_evidence_dir if args.e2e_evidence_dir is not None else (
        (previous.get('e2e_evidence_dir',DEFAULT_E2E_EVIDENCE) if non_interactive else prompt('Playwright E2Eエビデンス保存先（Gitルートからの相対パス）', previous.get('e2e_evidence_dir',DEFAULT_E2E_EVIDENCE))))
    retention = args.retention_days if args.retention_days is not None else (
        (previous.get('artifact_retention_days',DEFAULT_RETENTION_DAYS) if non_interactive else prompt('エビデンス保持期間（日）', str(previous.get('artifact_retention_days',DEFAULT_RETENTION_DAYS)))))
    try:
        retention = int(retention)
    except (ValueError, TypeError) as ex:
        raise SetupError('保存期間には整数を指定してください。') from ex
    settings = artifact_settings({'unit_integration_results_dir': unit_dir,
                                  'e2e_evidence_dir': e2e_storage,
                                  'artifact_retention_days': retention})
    print('保存先確認: 単体・結合={unit_integration_results_dir}, E2E={e2e_evidence_dir}, 保持期間={artifact_retention_days}日'.format(**settings))
    custom_storage = (settings['unit_integration_results_dir'] != DEFAULT_TEST_RESULTS or
                      settings['e2e_evidence_dir'] != DEFAULT_E2E_EVIDENCE or
                      settings['artifact_retention_days'] != DEFAULT_RETENTION_DAYS)
    if not (args.confirm_artifact_storage or (not non_interactive and confirmed('この保存先・保持期間で設定しますか？'))):
        if not non_interactive or custom_storage:
            raise SetupError('保存先・保持期間が承認されていません。非対話で変更するときは --confirm-artifact-storage が必要です。')
    print(f'テスト環境: {env} / {url} (origin={origin})')
    print('確認: 専用テスト環境であり、本番データ・実ユーザーへの影響がないこと。URLと操作範囲を確認してください。')
    if not (args.confirm_isolated or (not non_interactive and confirmed('この環境で自動E2Eを行うことを承認しますか？'))):
        raise SetupError('テスト環境の承認がありません。')
    doc = {
        'schema_version': 1,
        'git_root': str(repo),
        'codebase': rel,
        'codebase_realpath': str(code),
        'git_branch_at_setup': branch,
        'build_tool': build,
        'environment': env,
        'base_url': url,
        'allowed_origin': origin,
        'unit_test_argv': unit_argv,
        'e2e_test_argv': e2e_argv,
        'e2e_workdir': e2e_dir,
        'integration_test_argv': integration_argv,
        **settings,
        'confirmed_codebase': True,
        'confirmed_isolated': True,
        'configured_at_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
    }
    validate(doc)
    ensure_untracked_destination(repo, doc)
    update_gitignore(repo, doc)
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    # Atomic replacement; file belongs only to current user.
    temporary = CONFIG.with_suffix('.tmp')
    fd = os.open(str(temporary), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)
        f.write('\n')
    os.replace(temporary, CONFIG)
    PROFILE.write_text(
        '# ローカル設定（Git管理対象外）\n\n'
        f'- コードベース: `{rel}`（{code}）\n'
        f'- ビルド方式: {build}\n'
        f'- テスト環境: **{env}**\n'
        f'- URL: `{url}`\n'
        f'- 単体: `{shlex.join(unit_argv)}`\n'
        f'- E2E: `{shlex.join(e2e_argv)}`（作業ディレクトリ `{e2e_dir}`）\n'
        f"- 結合: `{shlex.join(integration_argv) if integration_argv else '未設定'}`\n"
        f"- 単体・結合テスト結果: `{settings['unit_integration_results_dir']}`\n"
        f"- Playwright E2E証跡: `{settings['e2e_evidence_dir']}`\n"
        f"- 保持期間: {settings['artifact_retention_days']}日（削除実行は別途承認）\n"
        f'- 設定日時 (UTC): {doc["configured_at_utc"]}\n\n'
        '資格情報・パスワード・トークンを保存しない。実環境との同一性はネットワーク・アカウント権限でも検証する。\n',
        encoding='utf-8')
    print('SETUP COMPLETE:', CONFIG.relative_to(ROOT))
    print('次に: python3 scripts/project_setup.py check / python3 scripts/project_setup.py probe --confirm-network')


def main_check(args: argparse.Namespace) -> None:
    cfg = validate(load_config(), cwd=args.cwd)
    print(f'READY: codebase={cfg["codebase"]} build={cfg["build_tool"]} '
          f'test_env={cfg["environment"]} origin={cfg["allowed_origin"]} '
          f'e2e_workdir={cfg["e2e_workdir"]} storage={artifact_settings(cfg)}')


def main_session() -> None:
    # Claude Code passes event information on stdin. Avoid contact with any remote environment.
    raw = sys.stdin.read()
    try:
        incoming = json.loads(raw) if raw.strip() else {}
        cwd = incoming.get('cwd')
        if cwd is not None and not isinstance(cwd, str):
            cwd = None
    except json.JSONDecodeError:
        cwd = None
    try:
        cfg = validate(load_config(), cwd=cwd)
        msg = (f'[PROJECT SETUP READY] コードベース={cfg["codebase"]} ({cfg["git_root"]}), '
               f'テスト環境={cfg["environment"]}, 対象URL={cfg["base_url"]}, '
               f'ビルド={cfg["build_tool"]}, 保存先={artifact_settings(cfg)}。'
               ' 開発に着手する前にユーザーへこの対応先を提示し、変更がないか確認する。'
               ' ツール診断は python3 scripts/toolchain.py doctor --phase e2e を実行し、不足時は /tool-setup。'
               ' E2Eは scripts/run_e2e.py 経由でのみ実行する。')
    except SetupError as exc:
        msg = ('[PROJECT SETUP REQUIRED] '+str(exc)+'。'
               ' 仕様解析・実装・テストを開始する前に、対象コードベースとテスト環境についてユーザーに確認し、'
               ' /project-setup を実行してください。設定確定後に /tool-setup でツール診断を行う。設定未完了はPASS扱い禁止。')
    print(json.dumps({'hookSpecificOutput': {'hookEventName': 'SessionStart', 'additionalContext': msg}}, ensure_ascii=False))


class SameOriginRedirect(HTTPRedirectHandler):
    def __init__(self, approved_origin: str):
        self.approved_origin = approved_origin
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _, origin = parse_url(newurl)
        if origin != self.approved_origin:
            raise SetupError('接続確認先が別originへリダイレクトしました。中断します。')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def main_probe(args: argparse.Namespace) -> None:
    cfg = validate(load_config())
    if not args.confirm_network:
        raise SetupError('ネットワーク疎通確認は --confirm-network を明示した場合にのみ実施します。')
    opener = build_opener(SameOriginRedirect(cfg['allowed_origin']))
    try:
        with opener.open(Request(cfg['base_url'], method='GET', headers={'User-Agent':'ClaudeCode-Setup-Healthcheck/1.0'}), timeout=8) as response:
            print(f'CONNECTIVITY: HTTP {response.status} / {cfg["allowed_origin"]}')
    except Exception as exc:
        raise SetupError(f'疎通確認が失敗しました（テスト未実施扱い）: {exc}') from exc


def main() -> int:
    parser = argparse.ArgumentParser(description='Java Webアプリのコードベース・テスト環境セットアップ')
    sub = parser.add_subparsers(dest='action', required=True)
    pinit = sub.add_parser('init', help='初回設定。対話実行または承認値を渡す非対話方式')
    for name in ('codebase','environment','base-url','unit-command','integration-command','e2e-command','e2e-workdir','unit-results-dir','e2e-evidence-dir'):
        pinit.add_argument('--'+name)
    pinit.add_argument('--retention-days',type=int)
    pinit.add_argument('--confirm-artifact-storage', action='store_true')
    pinit.add_argument('--confirm-codebase', action='store_true')
    pinit.add_argument('--confirm-isolated', action='store_true')
    pinit.add_argument('--non-interactive', action='store_true')
    pinit.add_argument('--force', action='store_true')
    pcheck = sub.add_parser('check', help='保存した設定がGitコードベースと一致することを検証')
    pcheck.add_argument('--cwd')
    sub.add_parser('session', help='Claude Code SessionStart hook用')
    probe = sub.add_parser('probe', help='選択したテストURLへの読み取り専用GET確認')
    probe.add_argument('--confirm-network', action='store_true')
    args = parser.parse_args()
    try:
        {'init': main_init, 'check': main_check, 'session': lambda _: main_session(), 'probe': main_probe}[args.action](args)
        return 0
    except SetupError as exc:
        print(f'SETUP BLOCKED: {exc}', file=sys.stderr)
        return 2

if __name__ == '__main__':
    sys.exit(main())
