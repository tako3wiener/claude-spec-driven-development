#!/usr/bin/env python3
"""Conservative toolchain bootstrap for an existing Java web app.

Standard library only. No network or package installation in doctor/plan modes.
Only an explicit install --apply --approve-downloads invokes pinned project tooling.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

from project_setup import ROOT, CONFIG, SetupError, ensure_inside_root, load_config, validate

LOCK = ROOT / '.claude' / 'local' / 'toolchain-lock.json'
BROWSERS = {'chromium', 'firefox', 'webkit'}
RESOLUTION = ROOT / '.claude' / 'local' / 'playwright-resolution.json'
SEMVER = re.compile(r'\d+\.\d+\.\d+(?:[-.][A-Za-z0-9]+)*\Z')
LINKS = {
    'Claude Code': 'https://code.claude.com/docs/en/setup',
    'Git': 'https://git-scm.com/downloads',
    'Python': 'https://www.python.org/downloads/',
    'JDK': 'https://adoptium.net/temurin/releases/',
    'Maven': 'https://maven.apache.org/install.html',
    'Gradle': 'https://gradle.org/install/',
    'Playwright Java': 'https://playwright.dev/java/docs/browsers',
}


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open('rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def shell_version(argv: list[str]) -> str | None:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=8, check=False)
        combined = (p.stdout + '\n' + p.stderr).strip()
        return combined.splitlines()[0].strip()[:180] if p.returncode == 0 and combined else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def required_jdk(codebase: Path) -> int | None:
    # Inspect metadata only. When unknown, the user must confirm the desired JDK.
    pom = codebase / 'pom.xml'
    if pom.is_file():
        try:
            tree = ET.parse(pom)
            root = tree.getroot()
            for name in ('maven.compiler.release', 'java.version', 'maven.compiler.target', 'maven.compiler.source'):
                elem = root.find('.//{*}' + name)
                if elem is not None and elem.text:
                    m = re.fullmatch(r'\s*(?:1\.)?(\d+)(?:\.\d+)?\s*', elem.text)
                    if m:
                        return int(m.group(1))
        except (ET.ParseError, OSError):
            pass
    for name in ('build.gradle', 'build.gradle.kts'):
        f = codebase / name
        if not f.is_file():
            continue
        txt = f.read_text(encoding='utf-8', errors='replace')
        for pat in (r'JavaLanguageVersion\.of\((\d+)\)', r'JavaVersion\.VERSION_(\d+)', r'(?:sourceCompatibility|targetCompatibility)\s*=\s*["\']?(\d+)'):
            m = re.search(pat, txt)
            if m:
                return int(m.group(1))
    return None


def current_jdk() -> tuple[int | None, str | None]:
    java = shutil.which('java')
    javac = shutil.which('javac')
    if not java or not javac:
        return None, None
    v = shell_version([javac, '-version'])
    if not v:
        return None, None
    m = re.search(r'(?:javac\s+)(?:1\.)?(\d+)', v)
    return (int(m.group(1)) if m else None), v


def look_for_browser() -> bool:
    override = os.environ.get('PLAYWRIGHT_BROWSERS_PATH')
    if override == '0':
        return False  # location is package-local, cannot infer
    if override:
        cache = Path(override).expanduser()
    elif sys.platform == 'darwin':
        cache = Path.home() / 'Library' / 'Caches' / 'ms-playwright'
    elif os.name == 'nt':
        cache = Path(os.environ.get('LOCALAPPDATA', '')) / 'ms-playwright'
    else:
        cache = Path.home() / '.cache' / 'ms-playwright'
    return cache.is_dir() and any(cache.glob('chromium-*'))


def e2e_project(cfg: dict) -> tuple[Path, str, Path, str]:
    """Use configured E2E command, never guess an unrelated project."""
    workdir, _ = ensure_inside_root(cfg['e2e_workdir'])
    argv = cfg['e2e_test_argv']
    executable = Path(argv[0]).name.lower()
    builder = 'maven' if executable in {'mvn', 'mvnw'} else 'gradle'
    if builder == 'maven':
        candidate = 'pom.xml'
        if '-f' in argv:
            i = argv.index('-f')
            if i + 1 >= len(argv):
                raise SetupError('E2Eコマンドの -f の後にpom.xmlが指定されていません。')
            candidate = argv[i + 1]
        elif '--file' in argv:
            i = argv.index('--file')
            if i + 1 >= len(argv):
                raise SetupError('E2Eコマンドの --file が不正です。')
            candidate = argv[i + 1]
        file = (workdir / candidate).resolve()
        if file.is_dir():
            file = file / 'pom.xml'
        if not file.is_file():
            raise SetupError(f'E2Eのpom.xmlが見つかりません: {file}')
        if not file.is_relative_to(ROOT):
            raise SetupError('E2Eのpom.xmlがGitルート外です。')
    else:
        # Gradle: only support a direct Java module. Complex multi-project builds require manual task selection.
        file = next((workdir / n for n in ('build.gradle', 'build.gradle.kts') if (workdir / n).is_file()), None)
        if file is None:
            raise SetupError('Gradle E2E実行ディレクトリにbuild.gradle(.kts)がありません。マルチモジュールは運用担当が設定してください。')
    return file, builder, workdir, executable


def dependency_version(build_file: Path, builder: str) -> str | None:
    text = build_file.read_text(encoding='utf-8', errors='replace')
    if builder == 'maven':
        try:
            tree = ET.parse(build_file)
            root = tree.getroot()
            properties = {}
            prop = root.find('{*}properties')
            if prop is not None:
                properties = {x.tag.split('}')[-1]: (x.text or '').strip() for x in prop}
            for dep in root.findall('.//{*}dependency'):
                group = dep.findtext('{*}groupId')
                artifact = dep.findtext('{*}artifactId')
                if group == 'com.microsoft.playwright' and artifact == 'playwright':
                    v = (dep.findtext('{*}version') or '').strip()
                    if v.startswith('${') and v.endswith('}'):
                        v = properties.get(v[2:-1], '')
                    return v or 'managed-in-parent'
        except ET.ParseError:
            return None
    else:
        m = re.search(r'com\.microsoft\.playwright:playwright:([\w.\-]+)', text)
        if m:
            return m.group(1)
        if 'com.microsoft.playwright:playwright' in text:
            return 'version-catalog-or-platform'
    return None


def graph_fingerprint(build_file):
    """Lock changes in likely local parent/build property files (not external repos)."""
    files={build_file.resolve()}
    node=build_file.resolve().parent
    while node.is_relative_to(ROOT.resolve()):
        for name in ('pom.xml','build.gradle','build.gradle.kts','settings.gradle',
                     'settings.gradle.kts','gradle.properties','gradle/libs.versions.toml'):
            path=node/name
            if path.is_file():files.add(path.resolve())
        if node==ROOT.resolve():break
        node=node.parent
    return hashlib.sha256(json.dumps([(str(f.relative_to(ROOT)),sha256_file(f)) for f in sorted(files)],
                                     separators=(',',':')).encode()).hexdigest()


def resolved_version(build_file, builder):
    # A declared version is not necessarily the *resolved* version (profiles,
    # parent-POM, version catalogs, Gradle resolution strategies may override).
    if RESOLUTION.is_file():
        try:
            recorded=json.loads(RESOLUTION.read_text(encoding='utf-8'))
            if (recorded.get('graph_sha256') == graph_fingerprint(build_file)
                    and recorded.get('build_file') == str(build_file.relative_to(ROOT))
                    and SEMVER.fullmatch(recorded.get('version',''))):
                return recorded['version']
        except (ValueError, OSError):pass
    return None


def resolve_dependency(cfg, approve=False):
    if not approve:
        print('BLOCKED: resolving parent/Gradle dependency graph can download and execute plugins; require --approve-resolution',file=sys.stderr)
        return 2
    file, builder, workdir, executable = e2e_project(cfg)
    runner = build_exe(cfg,builder,workdir,executable)
    if not runner:
        print('BLOCKED: build runner missing',file=sys.stderr)
        return 2
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        if builder == 'maven':
            out=Path(tmp)/'tree.txt'
            cmd=[runner,'-f',str(file),'dependency:tree','-Dincludes=com.microsoft.playwright:playwright','-DoutputType=text',f'-DoutputFile={out}']
        else:
            cmd=[runner,'-p',str(workdir),'-I',str(ROOT/'scripts/gradle/resolve-playwright.init.gradle'),'sddResolvePlaywright']
        try:
            proc=subprocess.run(cmd,cwd=workdir,capture_output=True,text=True,timeout=300,check=False)
        except (OSError,subprocess.TimeoutExpired) as ex:
            print('BLOCKED: '+str(ex),file=sys.stderr);return 2
        output=out.read_text(encoding='utf-8',errors='replace') if builder=='maven' and out.exists() else proc.stdout
        if proc.returncode:
            print('BLOCKED: resolution command failed (output omitted; inspect build logs)',file=sys.stderr);return 2
        if builder=='maven':
            versions=set(re.findall(r'com\.microsoft\.playwright:playwright:(?:jar|pom):('+SEMVER.pattern[:-2]+r'):',output))
        else:
            versions=set(re.findall(r'^SDD_PLAYWRIGHT_VERSION=([^\s]+)$',output,re.M))
        versions={v for v in versions if SEMVER.fullmatch(v)}
        if len(versions)!=1:
            print('BLOCKED: actual resolved Playwright version is unknown/ambiguous',file=sys.stderr);return 2
        from v5_core import atomic_json
        version=next(iter(versions))
        atomic_json(RESOLUTION,{'version':version,'build_file':str(file.relative_to(ROOT)),
            'build_sha256':sha256_file(file),'graph_sha256':graph_fingerprint(file),
            'resolved_at_utc':datetime.now(timezone.utc).isoformat()})
        print('RESOLVED Playwright Java:',version)
        return 0


def build_exe(cfg: dict, builder: str, workdir: Path, executable: str) -> str | None:
    # A wrapper may execute downloaded code; execute only after explicit approval.
    if executable in {'mvnw', 'gradlew'}:
        specified = workdir / ('mvnw' if executable == 'mvnw' else 'gradlew')
        if specified.is_file():
            return str(specified)
        # Existing setup might use codebase wrapper via relative path; fail closed rather than guess.
        return None
    return shutil.which(executable)


def plan(cfg: dict, browser: str) -> dict:
    file, builder, workdir, executable = e2e_project(cfg)
    version = resolved_version(file, builder)
    exe = build_exe(cfg, builder, workdir, executable)
    java_major, java_version = current_jdk()
    min_jdk = required_jdk(ROOT / cfg['codebase'])
    rows = [
        {'name': 'Git', 'status': 'OK' if shutil.which('git') else 'MISSING', 'detail': shell_version(['git','--version']) or LINKS['Git']},
        {'name': 'Python', 'status': 'OK' if sys.version_info >= (3, 9) else 'MISSING', 'detail': sys.version.split()[0]},
        {'name': 'Claude Code CLI', 'status': 'OK' if shutil.which('claude') else 'MISSING', 'detail': LINKS['Claude Code'] + ' (Claude Code内で診断する場合はこの警告を確認)'},
        {'name': 'JDK', 'status': 'OK' if java_major and (not min_jdk or java_major >= min_jdk) else 'MISSING', 'detail': f'{java_version or "not found"}; project required={min_jdk or "not determined"}'},
        {'name': builder, 'status': 'OK' if exe else 'MISSING', 'detail': exe or LINKS[builder.capitalize()]},
        {'name': 'Playwright for Java dependency', 'status': 'OK' if version else 'MISSING', 'detail': f'{version or "not declared"} in {file.relative_to(ROOT)}'},
    ]
    build_text = file.read_text(encoding='utf-8', errors='replace')
    has_junit = (('org.junit.jupiter' in build_text or 'junit-jupiter' in build_text
                  or re.search(r'\bjunit:junit\b', build_text)
                  or 'junit-bom' in build_text or 'junitVersion' in build_text))
    rows.append({'name':'JUnit dependency (E2E)', 'status': 'OK' if has_junit else 'MISSING',
                 'detail': 'declared in E2E project file' if has_junit else 'not detected; check parent build and approve test dependency'})
    # The lock attests bootstrap attempt, not actual browser launch. E2E itself verifies that.
    recorded = {}
    if LOCK.is_file():
        try:
            recorded = json.loads(LOCK.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            pass
    fingerprint = sha256_file(file)
    installed = (recorded.get('project_file') == str(file.relative_to(ROOT))
                 and recorded.get('project_sha256') == fingerprint
                 and recorded.get('browser') == browser and recorded.get('platform') == platform.system()
                 and recorded.get('arch') == platform.machine()
                 and recorded.get('playwright_version') == version
                 and recorded.get('graph_sha256') == graph_fingerprint(file))
    rows.append({'name': f'Playwright {browser} browser',
                 'status': 'BOOTSTRAPPED' if installed else 'NEEDS_INSTALL',
                 'detail': ('approved install recorded; actual launch checked at E2E runtime'
                            if installed else 'matching browser not verified; use install --apply after approval')})
    if builder == 'maven':
        argv = [exe or 'mvn', '-f', str(file), 'test-compile', 'exec:java', '-Dexec.classpathScope=test',
                '-Dexec.mainClass=com.microsoft.playwright.CLI', f'-Dexec.args=install {browser}']
    else:
        argv = [exe or 'gradle', '-p', str(workdir), '-I', str(ROOT / 'scripts' / 'gradle' / 'playwright-bootstrap.init.gradle'),
                'bootstrapPlaywrightBrowser', f'-PpwBrowser={browser}']
    return {'profile': {'codebase': cfg['codebase'], 'test_env': cfg['environment'],
                        'e2e_workdir': cfg['e2e_workdir'], 'project_file': str(file.relative_to(ROOT)),
                        'browser': browser, 'playwright_version': version, 'project_sha256': fingerprint, 'graph_sha256': graph_fingerprint(file)},
            'checks': rows, 'install': {'cwd': str(workdir), 'argv': argv},
            'installable': bool(exe and java_major and (not min_jdk or java_major >= min_jdk) and version),
            'already_bootstrapped': installed, 'build_tool': builder}


def verified_config() -> dict:
    return validate(load_config(), cwd=os.getcwd())


def doctor(cfg: dict, browser: str, phase: str, json_output: bool) -> int:
    # setup/dev MUST NOT inspect e2e pom/build.gradle or invoke Playwright.
    rows = [
       {'name':'Git','status':'OK' if shutil.which('git') else 'MISSING','detail':LINKS['Git']},
       {'name':'Python','status':'OK' if sys.version_info >= (3,9) else 'MISSING','detail':sys.version.split()[0]},
       {'name':'Claude Code CLI','status':'OK' if shutil.which('claude') else 'MISSING','detail':LINKS['Claude Code']},
    ]
    if phase in ('dev','e2e'):
        codebase=ROOT/cfg['codebase']
        builder=cfg['build_tool']
        workdir=codebase
        command=cfg['unit_test_argv']
        exe=build_exe(cfg,builder,workdir,Path(command[0]).name.lower())
        major,version=current_jdk()
        min_jdk=required_jdk(codebase)
        rows += [
            {'name':'JDK','status':'OK' if major and (not min_jdk or major>=min_jdk) else 'MISSING','detail':version or LINKS['JDK']},
            {'name':builder,'status':'OK' if exe else 'MISSING','detail':exe or LINKS[builder.capitalize()]},
        ]
    if phase=='e2e':
        try:
            info=plan(cfg,browser)
            rows += [r for r in info['checks'] if r['name'] in ('Playwright for Java dependency','JUnit dependency (E2E)') or r['name'].startswith('Playwright ')]
        except (SetupError,OSError,ValueError) as ex:
            rows.append({'name':'E2E project','status':'MISSING','detail':str(ex)})
    blocked=[r for r in rows if r['status'] not in ('OK','BOOTSTRAPPED') and r['name']!='Claude Code CLI']
    output={'phase':phase,'ready':not blocked,'checks':rows,
       'note':'Browser BOOTSTRAPPED means recorded acquisition, not successful browser launch.'}
    if json_output:print(json.dumps(output,ensure_ascii=False,indent=2))
    else:
        print(f'TOOLCHAIN {"READY" if not blocked else "BLOCKED"} [{phase}]')
        for r in rows:print(f'  {r["status"]:15} {r["name"]}: {r["detail"]}')
    return 2 if blocked else 0


def perform_install(info: dict, browser: str, apply: bool, approve: bool) -> int:
    print('INSTALL PLAN (network access / package retrieval):')
    print('  Project:', info['profile']['project_file'], 'Playwright:', info['profile']['playwright_version'])
    print('  Browser:', browser, '  CWD:', info['install']['cwd'])
    print('  Command:', json.dumps(info['install']['argv'], ensure_ascii=False))
    print('  Sources: existing Maven/Gradle repository settings + official Playwright browser distribution')
    print('  Risk: Maven/Gradle plugins and build logic may execute code; inspect project and repositories first.')
    print('  No sudo, OS package installations or build file modifications performed by this installer.')
    if not apply:
        print('DRY RUN: no files downloaded. To execute after human approval: install --apply --approve-downloads')
        return 0
    if not approve:
        print('BLOCKED: --approve-downloads is required in addition to --apply.', file=sys.stderr)
        return 2
    if not info['installable']:
        print('BLOCKED: JDK, build tool, or Playwright dependency missing. Install system prerequisite / approve dependency change first.', file=sys.stderr)
        return 2
    if info['already_bootstrapped']:
        print('Already recorded as bootstrapped. For a fresh download, remove local toolchain-lock.json after reviewing it.')
        return 0
    cmd = info['install']['argv']
    if not Path(cmd[0]).exists() and shutil.which(cmd[0]) is None:
        print('BLOCKED: build runner was not found', file=sys.stderr)
        return 2
    # No shell=True, do not touch global package-manager or request privileges.
    try:
        result = subprocess.run(cmd, cwd=info['install']['cwd'], check=False, timeout=900)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f'BLOCKED: tool retrieval failed: {exc}', file=sys.stderr)
        return 2
    if result.returncode != 0:
        print(f'BLOCKED: installer exit status {result.returncode}. No successful install record written.', file=sys.stderr)
        return 2
    record = dict(info['profile'])
    record.update({'schema_version': 1, 'platform': platform.system(), 'arch': platform.machine(),
                   'configured_git_root': str(ROOT.resolve()), 'installed_at_utc': datetime.now(timezone.utc).isoformat()})
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    temporary = LOCK.with_suffix('.tmp')
    fd = os.open(temporary, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(record, f, indent=2, ensure_ascii=False)
        f.write('\n')
    os.replace(temporary, LOCK)
    print('BOOTSTRAP COMPLETE: browser install command succeeded; run E2E to verify actual browser launch.')
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description='Claude Code Java/Playwright toolchain doctor and approved browser bootstrap')
    sub = parser.add_subparsers(dest='action', required=True)
    for action in ('doctor', 'plan', 'install', 'resolve'):
        p = sub.add_parser(action)
        p.add_argument('--browser', choices=sorted(BROWSERS), default='chromium')
        if action == 'doctor':
            p.add_argument('--phase', choices=('setup','dev','e2e'), default='e2e')
            p.add_argument('--json', action='store_true')
        if action == 'resolve':
            p.add_argument('--approve-resolution',action='store_true')
        if action == 'install':
            p.add_argument('--apply', action='store_true')
            p.add_argument('--approve-downloads', action='store_true')
    args = parser.parse_args()
    try:
        cfg = verified_config()
        if args.action == 'doctor':
            return doctor(cfg, args.browser, args.phase, args.json)
        if args.action == 'resolve':
            return resolve_dependency(cfg,args.approve_resolution)
        info = plan(cfg, args.browser)
        if args.action == 'plan':
            return perform_install(info, args.browser, False, False)
        return perform_install(info, args.browser, args.apply, args.approve_downloads)
    except (SetupError, ValueError, OSError) as exc:
        print(f'TOOLCHAIN BLOCKED: {exc}. Run /project-setup first.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
