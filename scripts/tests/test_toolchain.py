"""No-network tests of the tool diagnostics and opt-in acquisition gate."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SRC = Path(__file__).resolve().parents[1]

POM = '''<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion>
<groupId>example</groupId><artifactId>e2e</artifactId><version>1.0</version>
<properties><maven.compiler.release>17</maven.compiler.release><playwright.version>1.64.0</playwright.version></properties>
<dependencies>
<dependency><groupId>com.microsoft.playwright</groupId><artifactId>playwright</artifactId><version>${playwright.version}</version><scope>test</scope></dependency>
<dependency><groupId>org.junit.jupiter</groupId><artifactId>junit-jupiter</artifactId><version>5.11.4</version><scope>test</scope></dependency>
</dependencies></project>'''

class ToolchainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / 'repo'
        (self.repo / 'scripts' / 'gradle').mkdir(parents=True)
        (self.repo / '.claude' / 'local').mkdir(parents=True)
        (self.repo / 'backend').mkdir(parents=True)
        (self.repo / 'e2e').mkdir(parents=True)
        (self.repo / 'backend' / 'pom.xml').write_text(POM, encoding='utf-8')
        (self.repo / 'e2e' / 'pom.xml').write_text(POM, encoding='utf-8')
        for script in ('setup_errors.py','artifact_paths.py','project_setup.py','toolchain.py','v5_core.py'):
            shutil.copy2(SRC / script, self.repo / 'scripts' / script)
        shutil.copy2(SRC / 'gradle' / 'playwright-bootstrap.init.gradle', self.repo / 'scripts' / 'gradle')
        subprocess.run(['git','init','-q',str(self.repo)], check=True)
        subprocess.run(['git','-C',str(self.repo),'config','user.email','test@example.invalid'],check=True)
        subprocess.run(['git','-C',str(self.repo),'config','user.name','Test'],check=True)
        subprocess.run(['git','-C',str(self.repo),'add','backend/pom.xml','e2e/pom.xml'],check=True)
        subprocess.run(['git','-C',str(self.repo),'commit','-qm','init'],check=True)
        # A local fake Maven; scripts will not access the network in this test.
        bindir = Path(self.temp.name) / 'bin'
        bindir.mkdir()
        fake = bindir / 'mvn'
        fake.write_text('#!/bin/sh\nout=\"\"\nfor arg in \"$@\"; do case \"$arg\" in -DoutputFile=*) out=\"${arg#-DoutputFile=}\" ;; esac; done\nif [ -n \"$out\" ]; then echo \"[INFO] +- com.microsoft.playwright:playwright:jar:1.64.0:test\" > \"$out\"; fi\nexit 0\n', encoding='utf-8')
        fake.chmod(0o755)
        self.env = dict(os.environ)
        self.env['PATH'] = str(bindir) + os.pathsep + self.env.get('PATH','')
        self.init = subprocess.run([sys.executable,str(self.repo / 'scripts' / 'project_setup.py'),
                                  'init','--non-interactive','--codebase','backend','--environment','test',
                                  '--base-url','http://localhost:18080/','--unit-command','mvn test',
                                  '--e2e-command','mvn -f e2e/pom.xml test','--e2e-workdir','.',
                                  '--confirm-codebase','--confirm-isolated'],
                                  cwd=self.repo,env=self.env,capture_output=True,text=True)
        self.assertEqual(self.init.returncode,0,self.init.stderr)

    def tool(self,*args):
        return subprocess.run([sys.executable,str(self.repo / 'scripts' / 'toolchain.py'),*args],
                              cwd=self.repo,env=self.env,capture_output=True,text=True)

    def test_doctor_reports_browser_not_bootstrapped(self):
        p=self.tool('doctor','--phase','e2e','--json')
        self.assertNotEqual(p.returncode,0)
        doc=json.loads(p.stdout)
        self.assertEqual(next(x for x in doc['checks'] if x['name']=='Playwright chromium browser')['status'], 'NEEDS_INSTALL')
        self.assertEqual(next(x for x in doc['checks'] if x['name']=='JUnit dependency (E2E)')['status'], 'OK')

    def test_dev_diagnostic_without_e2e_pom(self):
        (self.repo / 'e2e' / 'pom.xml').unlink()
        self.assertEqual(self.tool('doctor','--phase','dev').returncode,0)
        self.assertEqual(self.tool('doctor','--phase','e2e').returncode,2)

    def test_dependency_resolution_requires_permission(self):
        self.assertEqual(self.tool('resolve').returncode,2)
        self.assertFalse((self.repo/'.claude/local/playwright-resolution.json').exists())

    def test_plan_never_installs(self):
        p=self.tool('plan')
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertIn('DRY RUN',p.stdout)
        self.assertIn('install chromium',p.stdout)
        self.assertFalse((self.repo / '.claude/local/toolchain-lock.json').exists())

    def test_install_requires_explicit_approval(self):
        p=self.tool('install','--apply')
        self.assertEqual(p.returncode,2,p.stderr)
        self.assertFalse((self.repo / '.claude/local/toolchain-lock.json').exists())

    @unittest.skipUnless(shutil.which('javac'), 'JDK is needed for the fake Maven install path')
    def test_approved_install_writes_lock_and_change_invalidates(self):
        self.assertEqual(self.tool('resolve','--approve-resolution').returncode,0)
        p=self.tool('install','--apply','--approve-downloads')
        self.assertEqual(p.returncode,0,p.stderr+'\n'+p.stdout)
        lock=self.repo / '.claude/local/toolchain-lock.json'
        self.assertTrue(lock.is_file())
        self.assertEqual(json.loads(lock.read_text())['playwright_version'],'1.64.0')
        self.assertEqual(self.tool('doctor','--phase','e2e').returncode,0)
        pom=self.repo/'e2e/pom.xml'
        pom.write_text(pom.read_text().replace('1.64.0','1.64.1'))
        self.assertEqual(self.tool('doctor','--phase','e2e').returncode,2)

    def test_missing_playwright_dependency_blocks_install(self):
        pom=self.repo/'e2e/pom.xml'
        pom.write_text(pom.read_text().replace('com.microsoft.playwright','x.example.invalid'))
        p=self.tool('install','--apply','--approve-downloads')
        self.assertEqual(p.returncode,2)
        self.assertFalse((self.repo/'.claude/local/toolchain-lock.json').exists())

if __name__=='__main__':
    unittest.main()
