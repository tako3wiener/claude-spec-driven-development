"""v5 R04/R07/R10/R14/R16 local execution orchestration integration tests."""
import csv
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import run_e2e
from test_evidence_v5 import png, digest
from project_setup import SetupError


class RunnerTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        self.root=Path(t.name)/'repo';self.root.mkdir()
        (self.root/'module').mkdir()
        self.change=self.root/'docs/changes/CHG-001';self.change.mkdir(parents=True)
        (self.root/'src/test/java/e2e').mkdir(parents=True)
        (self.root/'src/test/java/e2e/ApprovedE2eTest.java').write_text('class ApprovedE2eTest {}')
        with (self.change/'test_cases.csv').open('w',newline='') as f:
            w=csv.writer(f);w.writerow(['tc_id','level','test_file','approval_state'])
            w.writerow(['TC-001','E2E','src/test/java/e2e/ApprovedE2eTest.java','APPROVED'])
        self.cfg={'e2e_test_argv':['mvn','test'],'e2e_workdir':'module',
                  'allowed_origin':'http://localhost:8080','base_url':'http://localhost:8080/',
                  'environment':'test','codebase':'module'}
        self.snapshot={'snapshot_id':'a'*64,'head':'0'*40,'branch':'dev','dirty':False}
        self.args=types.SimpleNamespace(change_id='CHG-001',run_id='RUN-XYZ',mode='final',dry_run=False,timeout=100)
        self.patches=[mock.patch.object(run_e2e,'ROOT',self.root),
                      mock.patch.object(run_e2e,'load_config',return_value=self.cfg),
                      mock.patch.object(run_e2e,'validate',return_value=self.cfg),
                      mock.patch.object(run_e2e,'ensure_inside_root',return_value=(self.root/'module','module')),
                      mock.patch.object(run_e2e,'verify_approval',return_value={'decision':'APPROVED'}),
                      mock.patch.object(run_e2e,'snapshot',return_value=self.snapshot),
                      mock.patch('toolchain.doctor',return_value=0)]
        for p in self.patches:p.start();self.addCleanup(p.stop)

    def fake_run(self,argv,cwd,env,timeout,check):
        folder=Path(env['E2E_JUNIT_DIR']).parent
        tc=folder/'TC-001';tc.mkdir()
        (folder/'junit/TEST-approved.xml').write_text('<testsuite tests="1"><testcase name="TC_001_shouldWork" /></testsuite>')
        shots=[]
        for name in ('01-initial.png','02-result-verified.png'):
            (tc/name).write_bytes(png());shots.append({'step':name[:-4],'file':name,'sha256':digest(png())})
        with zipfile.ZipFile(tc/'trace.zip','w') as z:
            z.writestr('trace.trace','{"type":"context-options"}\n')
        record={'schema_version':2,'change_id':'CHG-001','run_id':env['E2E_RUN_ID'],'tc_id':'TC-001',
                'result':'PASS','snapshot_id':env['E2E_SNAPSHOT_ID'],
                'environment_origin':env['E2E_ALLOWED_ORIGIN'],
                'runner_token_digest':env['E2E_RUN_TOKEN_DIGEST'],'screenshots':shots,
                'trace_file':'trace.zip','trace_sha256':hashlib.sha256((tc/'trace.zip').read_bytes()).hexdigest()}
        (tc/'manifest.json').write_text(json.dumps(record))
        return types.SimpleNamespace(returncode=0)

    def test_full_pipeline_and_duplicate_run_rejected(self):
        with mock.patch.object(run_e2e.subprocess,'run',side_effect=self.fake_run):
            self.assertEqual(run_e2e.run(self.args),0)
        folder=self.root/'reports/e2e/CHG-001/RUN-XYZ'
        self.assertEqual(json.loads((folder/'summary.json').read_text())['status'],'PASS')
        self.assertTrue((folder/'index.md').is_file())
        self.assertTrue((folder/'test_results.csv').is_file())
        with self.assertRaises(SetupError):run_e2e.run(self.args)

    def test_custom_e2e_storage_and_verified_test_source(self):
        self.cfg.update({'unit_integration_results_dir':'artifacts/test-results',
                         'e2e_evidence_dir':'artifacts/screens','artifact_retention_days':90})
        def check_env(argv,cwd,env,timeout,check):
            self.assertEqual(Path(env['E2E_EVIDENCE_ROOT']),self.root/'artifacts/screens')
            return self.fake_run(argv,cwd,env,timeout,check)
        with mock.patch.object(run_e2e.subprocess,'run',side_effect=check_env):
            self.assertEqual(run_e2e.run(self.args),0)
        directory=self.root/'artifacts/screens/CHG-001/RUN-XYZ'
        self.assertEqual(json.loads((directory/'summary.json').read_text())['status'],'PASS')
        self.assertTrue((directory/'TC-001/02-result-verified.png').is_file())
        self.assertEqual(json.loads((directory/'run-context.json').read_text())['repository_root'],str(self.root))

    def test_demo_command_rejected_for_final(self):
        with self.assertRaises(SetupError):run_e2e.approved_command({'e2e_test_argv':['mvn','-f','examples/playwright-java/pom.xml','test']},'final',self.root/'junit')

    def test_skip_flags_rejected_only_for_final(self):
        for argv in (['mvn','-DskipTests=true','test'],['mvn','-Dmaven.test.skip=true','verify'],['gradle','-x','test','check'],['mvn','-Dtest=Something','test']):
            with self.subTest(argv=argv):
                with self.assertRaises(SetupError):run_e2e.approved_command({'e2e_test_argv':argv},'final',self.root/'junit')
        partial=run_e2e.approved_command({'e2e_test_argv':['mvn','-Dtest=SingleTest','test']},'demo',self.root/'junit')
        self.assertIn('test',partial)

    def test_no_junit_is_blocked_and_artifacts_retained(self):
        with mock.patch.object(run_e2e.subprocess,'run',return_value=types.SimpleNamespace(returncode=0)):
            self.assertEqual(run_e2e.run(self.args),2)
        folder=self.root/'reports/e2e/CHG-001/RUN-XYZ'
        self.assertEqual(json.loads((folder/'summary.json').read_text())['status'],'BLOCKED')
        self.assertTrue((folder/'run-context.json').is_file())

    def test_unauthorized_case_rejected_before_run_directory(self):
        path=self.root/'src/test/java/e2e/ApprovedE2eTest.java';path.unlink()
        with self.assertRaises(SetupError):run_e2e.run(self.args)
        self.assertFalse((self.root/'reports/e2e/CHG-001/RUN-XYZ').exists())

if __name__=='__main__':unittest.main()
