"""Smoke tests for repository/environment selection. No network or Maven execution."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1]

class ProjectSetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'repo'
        (self.root / 'scripts').mkdir(parents=True)
        (self.root / '.claude' / 'local').mkdir(parents=True)
        (self.root / 'module').mkdir(parents=True)
        (self.root / 'module' / 'pom.xml').write_text('<project/>')
        for filename in ['setup_errors.py','artifact_paths.py','project_setup.py','run_e2e.py','guard_test_commands.py','v5_core.py','check_e2e_evidence.py','toolchain.py']:
            shutil.copy2(SOURCE / filename, self.root / 'scripts' / filename)
        subprocess.run(['git','init','-q',str(self.root)], check=True)
        subprocess.run(['git','-C',str(self.root),'config','user.email','test@example.invalid'],check=True)
        subprocess.run(['git','-C',str(self.root),'config','user.name','Tester'],check=True)
        subprocess.run(['git','-C',str(self.root),'add','module/pom.xml'],check=True)
        subprocess.run(['git','-C',str(self.root),'commit','-qm','initial'],check=True)

    def call(self, script, *args, input_text=None, cwd=None):
        return subprocess.run([sys.executable,str(self.root / 'scripts' / script),*args],
                              input=input_text,text=True,capture_output=True,cwd=cwd or self.root)

    def setup_ok(self):
        return self.call('project_setup.py','init','--non-interactive',
                         '--codebase','module','--environment','test',
                         '--base-url','https://qa.example.invalid:8443/web',
                         '--unit-command','mvn test','--e2e-command','mvn -f module/pom.xml test',
                         '--e2e-workdir','.', '--confirm-codebase','--confirm-isolated')

    def test_initial_session_requests_setup(self):
        p = self.call('project_setup.py','session',input_text='{"cwd":"'+str(self.root)+'"}')
        self.assertEqual(p.returncode, 0)
        j=json.loads(p.stdout)
        self.assertIn('PROJECT SETUP REQUIRED',j['hookSpecificOutput']['additionalContext'])

    def test_setup_check_and_e2e_dry_run(self):
        p = self.setup_ok()
        self.assertEqual(p.returncode, 0, p.stderr)
        j=json.loads((self.root / '.claude/local/project-env.json').read_text())
        self.assertEqual(j['codebase'],'module')
        self.assertEqual(j['allowed_origin'],'https://qa.example.invalid:8443')
        self.assertEqual(self.call('project_setup.py','check').returncode,0)
        run = self.call('run_e2e.py','--change-id','CHG-001','--run-id','ci-001','--dry-run')
        self.assertEqual(run.returncode,2,run.stderr)
        self.assertIn('BLOCKED',run.stderr)  # v5: dry-run still requires human G2/NET attestation
        session = self.call('project_setup.py','session',input_text='{"cwd":"'+str(self.root)+'"}')
        self.assertIn('PROJECT SETUP READY',json.loads(session.stdout)['hookSpecificOutput']['additionalContext'])

    def test_production_host_rejected(self):
        p = self.call('project_setup.py','init','--non-interactive',
                      '--codebase','module','--environment','staging',
                      '--base-url','https://prod.example.invalid/',
                      '--unit-command','mvn test','--e2e-command','mvn test',
                      '--e2e-workdir','.', '--confirm-codebase','--confirm-isolated')
        self.assertNotEqual(p.returncode,0)
        self.assertFalse((self.root / '.claude/local/project-env.json').exists())

    def test_external_codebase_rejected(self):
        p = self.call('project_setup.py','init','--non-interactive',
                      '--codebase','../outside','--environment','test',
                      '--base-url','http://localhost:8080/',
                      '--unit-command','mvn test','--e2e-command','mvn test',
                      '--e2e-workdir','.', '--confirm-codebase','--confirm-isolated')
        self.assertNotEqual(p.returncode,0)

    def test_force_required_for_overwrite(self):
        self.assertEqual(self.setup_ok().returncode,0)
        repeat=self.setup_ok()
        self.assertNotEqual(repeat.returncode,0)
        self.assertIn('--force',repeat.stderr)

    def test_config_mismatch_blocks_e2e(self):
        self.assertEqual(self.setup_ok().returncode,0)
        f=self.root / '.claude/local/project-env.json'
        cfg=json.loads(f.read_text())
        cfg['codebase_realpath']='/elsewhere'
        f.write_text(json.dumps(cfg))
        p=self.call('run_e2e.py','--change-id','CHG-001','--dry-run')
        self.assertNotEqual(p.returncode,0)
        self.assertIn('BLOCKED',p.stderr)

    def test_guard_blocks_direct_e2e(self):
        self.assertEqual(self.setup_ok().returncode,0)
        payload={'tool_name':'Bash','tool_input':{'command':'mvn -f examples/playwright-java/pom.xml test'},'cwd':str(self.root)}
        p=self.call('guard_test_commands.py',input_text=json.dumps(payload))
        self.assertEqual(p.returncode,2,p.stderr)
        payload['tool_input']['command']='python3 scripts/run_e2e.py --change-id CHG-001'
        p=self.call('guard_test_commands.py',input_text=json.dumps(payload))
        self.assertEqual(p.returncode,2,p.stderr)  # G2 not yet approved

    def test_guard_blocks_test_before_setup(self):
        payload={'tool_name':'Bash','tool_input':{'command':'mvn test'},'cwd':str(self.root)}
        p=self.call('guard_test_commands.py',input_text=json.dumps(payload))
        self.assertEqual(p.returncode,2,p.stderr)

    def test_probe_needs_permission(self):
        self.assertEqual(self.setup_ok().returncode,0)
        p=self.call('project_setup.py','probe')
        self.assertNotEqual(p.returncode,0)
        self.assertIn('--confirm-network',p.stderr)

if __name__ == '__main__':
    unittest.main()
