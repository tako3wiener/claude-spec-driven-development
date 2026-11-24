import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import standalone as sdd


class StandaloneTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.base=Path(tmp.name)
        self.kit=self.base/'SDD install with spaces'
        self.repo=self.base/'Java app with spaces'
        self.repo.mkdir(); subprocess.run(['git','init','-q',str(self.repo)],check=True)
        (self.repo/'pom.xml').write_text('<project/>',encoding='utf-8')
        self.env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        for name in ('SDD_PROJECT_ROOT','SDD_EXECUTION_CONTEXT','SDD_CHANGE_ID'):
            self.env.pop(name,None)

    def cmd(self,*args, installed=True, env=None):
        script=(self.kit if installed else ROOT)/'standalone.py'
        return subprocess.run([sys.executable,str(script),*args],cwd=self.repo,env=env or self.env,text=True,capture_output=True)

    def ok(self,*args,**kwargs):
        p=self.cmd(*args,**kwargs);self.assertEqual(p.returncode,0,p.stdout+'\n'+p.stderr);return p

    def install(self):
        self.ok('install','--install-dir',str(self.kit),installed=False)

    def configure(self,name='app',execution='local',replace=False):
        args=['configure','--profile',name,'--codebase',str(self.repo),'--execution',execution]
        if replace:args+=['--replace']
        self.ok(*args)

    def test_independent_install_and_idempotent_reinstall(self):
        self.install()
        self.assertEqual(sorted(p.name for p in self.repo.iterdir()),['.git','pom.xml'])
        self.assertTrue((self.kit/'scripts/run_e2e.py').is_file())
        self.assertTrue((self.kit/'sdd').stat().st_mode & 0o100)
        self.assertIn('0 file(s)',self.ok('install','--install-dir',str(self.kit),installed=False).stdout)

    def test_profiles_and_dry_run_select_actual_git_root(self):
        self.install();self.configure()
        self.configure('container','devcontainer')
        self.assertIn('devcontainer',self.ok('profiles').stdout)
        p=self.ok('run','--profile','container','--change-id','CHG-007','--dry-run')
        self.assertEqual(json.loads(p.stdout)['git_root'],str(self.repo))
        self.assertEqual(json.loads(p.stdout)['execution'],'devcontainer')
        self.assertFalse((self.repo/'.claude').exists())

    def test_external_project_setup_and_progress(self):
        self.install();self.configure()
        self.ok('exec','--profile','app','project_setup.py','init','--non-interactive','--codebase','.',
                '--environment','test','--base-url','http://localhost:8080/','--unit-command','mvn test',
                '--e2e-command','mvn test','--e2e-workdir','.','--confirm-codebase','--confirm-isolated')
        self.ok('exec','--profile','app','project_setup.py','check')
        self.ok('exec','--profile','app','progress.py','--change-id','CHG-001','--gate','G1','--state','running')
        config=json.loads((self.repo/'.claude/local/project-env.json').read_text())
        self.assertEqual(config['git_root'],str(self.repo));self.assertEqual(config['execution_context'],'local')
        self.assertTrue((self.repo/'.claude/local/progress/CHG-001.json').is_file())
        self.assertFalse((self.kit/'.claude/local').exists())
        self.assertEqual(subprocess.run(['git','-C',str(self.repo),'check-ignore','--no-index','.claude/local/project-env.json'],capture_output=True).returncode,0)
        self.configure('container','devcontainer')
        self.assertNotEqual(self.cmd('exec','--profile','container','project_setup.py','check').returncode,0)

    def test_launcher_passes_real_cwd_hooks_and_absolute_scripts(self):
        self.install();self.configure('app','devcontainer')
        fake=self.base/'bin';fake.mkdir();capture=self.base/'capture.json'
        claude=fake/'claude'
        claude.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
a=sys.argv[1:]; d=Path(a[a.index('--add-dir')+1]); settings=json.loads(Path(a[a.index('--settings')+1]).read_text()); rules=Path(a[a.index('--append-system-prompt-file')+1]).read_text()
Path(os.environ['CAPTURE']).write_text(json.dumps({'cwd':os.getcwd(),'root':os.environ['SDD_PROJECT_ROOT'],'context':os.environ['SDD_EXECUTION_CONTEXT'],'change':os.environ['SDD_CHANGE_ID'],'settings':settings,'rules':rules,'skill':(d/'.claude/skills/spec-change/SKILL.md').read_text()}))
''');claude.chmod(0o755)
        env=dict(self.env,PATH=str(fake)+os.pathsep+self.env['PATH'],CAPTURE=str(capture))
        self.ok('run','--profile','app','--change-id','CHG-001',env=env)
        data=json.loads(capture.read_text());self.assertEqual(data['cwd'],str(self.repo));self.assertEqual(data['root'],str(self.repo));self.assertEqual(data['context'],'devcontainer');self.assertEqual(data['change'],'CHG-001')
        self.assertIn(str(self.kit/'scripts/project_setup.py'),data['settings']['hooks']['SessionStart'][0]['hooks'][0]['args'])
        self.assertIn(str(self.kit/'scripts/run_e2e.py'),data['skill']);self.assertIn(str(self.repo),data['rules'])
        self.assertFalse((self.repo/'.claude/skills').exists());self.assertFalse((self.repo/'CLAUDE.md').exists())

    def test_uninstall_preserves_profiles_and_project(self):
        self.install();self.configure()
        self.ok('uninstall','--install-dir',str(self.kit),'--dry-run',installed=False)
        self.assertTrue((self.kit/'standalone.py').is_file())
        self.ok('uninstall','--install-dir',str(self.kit),installed=False)
        self.assertFalse((self.kit/'standalone.py').exists());self.assertTrue((self.kit/sdd.PROFILES).is_file());self.assertTrue((self.repo/'pom.xml').is_file())
        self.install();self.assertIn('app',self.ok('profiles').stdout)

    def test_uninstall_stops_on_locally_edited_file(self):
        self.install();(self.kit/'CLAUDE.md').write_text('edited')
        p=self.cmd('uninstall','--install-dir',str(self.kit),installed=False)
        self.assertEqual(p.returncode,2);self.assertTrue((self.kit/'standalone.py').exists())

    def test_install_refuses_inside_project(self):
        p=self.cmd('install','--install-dir',str(self.repo/'tools/sdd'),installed=False)
        self.assertEqual(p.returncode,2);self.assertFalse((self.repo/'tools').exists())

    def test_install_refuses_unmanaged_directory(self):
        self.kit.mkdir();(self.kit/'user.txt').write_text('keep')
        self.assertEqual(self.cmd('install','--install-dir',str(self.kit),installed=False).returncode,2)
        self.assertEqual((self.kit/'user.txt').read_text(),'keep')

    def test_legacy_project_refused(self):
        self.install();self.configure();(self.repo/'.claude').mkdir();(self.repo/'.claude/sdd-install.json').write_text('{}')
        self.assertEqual(self.cmd('run','--profile','app','--dry-run').returncode,2)

    def test_profile_path_disappears_no_fallback(self):
        self.install();self.configure();self.repo.rename(self.base/'moved')
        p=subprocess.run([sys.executable,str(self.kit/'standalone.py'),'run','--profile','app','--dry-run'],env=self.env,capture_output=True,text=True)
        self.assertEqual(p.returncode,2)

    def test_external_gradle_helpers_resolve_to_kit(self):
        self.install()
        env=dict(self.env,SDD_PROJECT_ROOT=str(self.repo),SDD_EXECUTION_CONTEXT='local')
        cmd=[sys.executable,'-c',f"import sys;sys.path.insert(0,{str(self.kit/'scripts')!r});import project_setup,run_e2e;print(project_setup.ROOT);print(project_setup.KIT_ROOT);print(run_e2e.approved_command({{'e2e_test_argv':['gradle','test']}},'final',project_setup.ROOT/'junit'))"]
        p=subprocess.run(cmd,cwd=self.repo,env=env,capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr);self.assertIn(str(self.kit/'scripts/gradle/e2e-junit.init.gradle'),p.stdout)


if __name__=='__main__':unittest.main()
