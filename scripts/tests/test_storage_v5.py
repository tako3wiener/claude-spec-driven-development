"""Storage location, compatibility, safety and retention tests. No network/Java binary needed."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SRC=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SRC))
from artifact_paths import artifact_settings, artifact_root, DEFAULT_TEST_RESULTS, DEFAULT_E2E_EVIDENCE
from project_setup import SetupError
import prune_artifacts
import run_java_tests


class StorageSetupTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.repo=Path(tmp.name)/'repo';(self.repo/'scripts').mkdir(parents=True)
        (self.repo/'module').mkdir()
        (self.repo/'module/pom.xml').write_text('<project/>')
        for src in ['setup_errors.py','artifact_paths.py','project_setup.py','run_e2e.py','guard_test_commands.py',
                    'v5_core.py','check_e2e_evidence.py','toolchain.py','prune_artifacts.py']:
            shutil.copy2(SRC/src,self.repo/'scripts'/src)
        subprocess.run(['git','init','-q',str(self.repo)],check=True)
        subprocess.run(['git','-C',str(self.repo),'config','user.email','test@example.invalid'],check=True)
        subprocess.run(['git','-C',str(self.repo),'config','user.name','test'],check=True)
        subprocess.run(['git','-C',str(self.repo),'add','module/pom.xml'],check=True)
        subprocess.run(['git','-C',str(self.repo),'commit','-qm','init'],check=True)

    def init(self,*extra):
        return subprocess.run([sys.executable,str(self.repo/'scripts/project_setup.py'),'init',
                '--non-interactive','--codebase','module','--environment','test',
                '--base-url','http://localhost:18080/','--unit-command','mvn test',
                '--e2e-command','mvn test','--e2e-workdir','.',
                '--confirm-codebase','--confirm-isolated',*extra],capture_output=True,text=True,cwd=self.repo)

    def config(self):return json.loads((self.repo/'.claude/local/project-env.json').read_text())

    def test_default_storage_and_gitignore(self):
        p=self.init();self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual(artifact_settings(self.config()),{
            'unit_integration_results_dir':DEFAULT_TEST_RESULTS,
            'e2e_evidence_dir':DEFAULT_E2E_EVIDENCE,
            'artifact_retention_days':90})
        ignore=(self.repo/'.gitignore').read_text()
        self.assertIn('/reports/test-results/',ignore)
        self.assertIn('/reports/e2e/',ignore)
        self.assertIn('未設定', (self.repo/'.claude/local/project-profile.md').read_text())
        self.assertEqual(subprocess.run([sys.executable,str(self.repo/'scripts/project_setup.py'),'check'],cwd=self.repo).returncode,0)

    def test_custom_requires_confirm_then_succeeds(self):
        flags=['--unit-results-dir','artifacts/junit','--e2e-evidence-dir','artifacts/screens',
               '--retention-days','90','--integration-command','mvn verify']
        reject=self.init(*flags);self.assertEqual(reject.returncode,2)
        self.assertFalse((self.repo/'.claude/local/project-env.json').exists())
        accept=self.init(*flags,'--confirm-artifact-storage');self.assertEqual(accept.returncode,0,accept.stderr)
        cfg=self.config();self.assertEqual(cfg['e2e_evidence_dir'],'artifacts/screens')
        self.assertEqual(cfg['integration_test_argv'],['mvn','verify'])
        ignored=(self.repo/'.gitignore').read_text()
        self.assertIn('/artifacts/screens/',ignored)
        self.assertIn('/artifacts/junit/',ignored)
        self.assertEqual(subprocess.run([sys.executable,str(self.repo/'scripts/project_setup.py'),'check'],cwd=self.repo).returncode,0)
        self.assertEqual(subprocess.run(['git','check-ignore','-q','artifacts/screens/CHG-001/RUN-1/test.png'],cwd=self.repo).returncode,0)

    def test_legacy_v5_without_fields_accepted(self):
        self.assertEqual(self.init().returncode,0)
        cfg=self.config()
        for key in ['unit_integration_results_dir','e2e_evidence_dir','artifact_retention_days','integration_test_argv']:
            cfg.pop(key,None)
        (self.repo/'.claude/local/project-env.json').write_text(json.dumps(cfg))
        self.assertEqual(artifact_settings(cfg)['artifact_retention_days'],90)
        self.assertEqual(subprocess.run([sys.executable,str(self.repo/'scripts/project_setup.py'),'check'],cwd=self.repo).returncode,0)

    def test_custom_paths_are_not_allowed_outside_or_in_source(self):
        for bad in ['../other','/tmp/other','src/test','docs/test','reports/../outside','.claude/local','reports','C:/tmp','reports/e2e/../../src']:
            with self.subTest(bad=bad):
                p=self.init('--unit-results-dir',bad,'--confirm-artifact-storage')
                self.assertNotEqual(p.returncode,0)
                self.assertFalse((self.repo/'.claude/local/project-env.json').exists())

    def test_overlap_rejected(self):
        p=self.init('--unit-results-dir','reports/out', '--e2e-evidence-dir','reports/out/e2e','--confirm-artifact-storage')
        self.assertEqual(p.returncode,2)

    def test_symlink_rejected(self):
        (self.repo/'artifacts').symlink_to(Path(self.repo).parent,target_is_directory=True)
        p=self.init('--unit-results-dir','artifacts/out','--confirm-artifact-storage')
        self.assertEqual(p.returncode,2)

    def test_tracked_destination_rejected(self):
        dest=self.repo/'artifacts/junit';dest.mkdir(parents=True)
        (dest/'important.txt').write_text('tracked')
        subprocess.run(['git','-C',str(self.repo),'add','artifacts/junit/important.txt'],check=True)
        p=self.init('--unit-results-dir','artifacts/junit','--confirm-artifact-storage')
        self.assertEqual(p.returncode,2)
        self.assertIn('Git',p.stderr)

    def test_retention_deletion_needs_explicit_operator_flags(self):
        self.assertEqual(self.init().returncode,0)
        folder=self.repo/'reports/e2e/CHG-001/RUN-OLD'
        folder.mkdir(parents=True)
        old=(datetime.now(timezone.utc)-timedelta(days=91)).isoformat()
        (folder/'summary.json').write_text(json.dumps({'finished_at_utc':old,'change_id':'CHG-001',
                                                           'run_id':'RUN-OLD','status':'PASS'}))
        (folder/'run-context.json').write_text(json.dumps({'change_id':'CHG-001',
                                     'run_id':'RUN-OLD','execution_finished':True}))
        base=[sys.executable,str(self.repo/'scripts/prune_artifacts.py')]
        dry=subprocess.run(base,cwd=self.repo,capture_output=True,text=True)
        self.assertEqual(dry.returncode,0,dry.stderr)
        self.assertTrue(folder.is_dir())
        no_approval=subprocess.run(base+['--apply'],cwd=self.repo,capture_output=True,text=True)
        self.assertEqual(no_approval.returncode,2)
        self.assertTrue(folder.is_dir())
        approved=subprocess.run(base+['--apply','--confirm-delete'],cwd=self.repo,capture_output=True,text=True)
        self.assertEqual(approved.returncode,0,approved.stderr)
        self.assertFalse(folder.exists())

    def test_reconfigure_preserves_old_evidence_ignore_and_storage_defaults(self):
        self.assertEqual(self.init('--unit-results-dir','artifacts/junit',
            '--e2e-evidence-dir','artifacts/screens','--confirm-artifact-storage').returncode,0)
        # Force with no new storage fields should keep the chosen destinations.
        rerun=self.init('--force','--confirm-artifact-storage')
        self.assertEqual(rerun.returncode,0,rerun.stderr)
        self.assertEqual(self.config()['e2e_evidence_dir'],'artifacts/screens')
        # Changing location preserves old output ignore rules so screenshots remain private.
        next_run=self.init('--force','--e2e-evidence-dir','artifacts/new-screens','--confirm-artifact-storage')
        self.assertEqual(next_run.returncode,0,next_run.stderr)
        ignore=(self.repo/'.gitignore').read_text()
        self.assertIn('/artifacts/screens/',ignore)
        self.assertIn('/artifacts/new-screens/',ignore)
        self.assertIn('/artifacts/junit/',ignore)

    def test_gitignore_idempotent_and_preserves_user_rules(self):
        (self.repo/'.gitignore').write_text('my-custom-rule\n')
        self.assertEqual(self.init().returncode,0)
        before=(self.repo/'.gitignore').read_text()
        self.assertEqual(self.init('--force').returncode,0)
        self.assertEqual((self.repo/'.gitignore').read_text(),before)
        self.assertIn('my-custom-rule',before)


class UnitResultsTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        self.root=Path(t.name)/'repo';(self.root/'module').mkdir(parents=True)
        self.cfg={'unit_test_argv':['mvn','test'], 'integration_test_argv':['mvn','verify'],
                  'codebase':'module','unit_integration_results_dir':'custom/test-results',
                  'e2e_evidence_dir':'custom/e2e', 'artifact_retention_days':90}
        self.args=type('Args',(),{'phase':'unit','change_id':'CHG-001','run_id':'RUN-1',
                                  'dry_run':False,'timeout':100})()
        self.snap={'snapshot_id':'a'*64}
        self.patches=[mock.patch.object(run_java_tests,'ROOT',self.root),
                      mock.patch.object(run_java_tests,'load_config',return_value=self.cfg),
                      mock.patch.object(run_java_tests,'validate',return_value=self.cfg),
                      mock.patch.object(run_java_tests,'ensure_inside_root',return_value=(self.root/'module','module')),
                      mock.patch.object(run_java_tests,'verify_approval',return_value={}),
                      mock.patch.object(run_java_tests,'snapshot',return_value=self.snap)]
        for patch in self.patches:patch.start();self.addCleanup(patch.stop)

    def fake(self,argv,cwd,env,timeout,check):
        self.assertIn('custom/test-results',env['JAVA_JUNIT_DIR'])
        dest=Path(env['JAVA_JUNIT_DIR'])
        dest.mkdir(parents=True,exist_ok=True)
        (dest/'TEST-Example.xml').write_text('<testsuite tests="1"><testcase name="testOne" /></testsuite>')
        return type('Proc',(),{'returncode':0})()

    def test_run_and_save_to_custom_directory(self):
        with mock.patch.object(run_java_tests.subprocess,'run',side_effect=self.fake):
            self.assertEqual(run_java_tests.run(self.args),0)
        summary=self.root/'custom/test-results/CHG-001/RUN-1/unit/summary.json'
        self.assertTrue(summary.is_file())
        self.assertEqual(json.loads(summary.read_text())['status'],'PASS')
        with self.assertRaises(SetupError):run_java_tests.run(self.args)

    def test_integration_command_and_separate_phase(self):
        self.args.phase='integration'
        with mock.patch.object(run_java_tests.subprocess,'run',side_effect=self.fake):
            self.assertEqual(run_java_tests.run(self.args),0)
        self.assertTrue((self.root/'custom/test-results/CHG-001/RUN-1/integration/junit/TEST-Example.xml').exists())

    def test_stale_junit_cannot_pass(self):
        with mock.patch.object(run_java_tests.subprocess,'run',return_value=type('Proc',(),{'returncode':0})()):
            self.assertEqual(run_java_tests.run(self.args),2)
        summary=json.loads((self.root/'custom/test-results/CHG-001/RUN-1/unit/summary.json').read_text())
        self.assertEqual(summary['status'],'BLOCKED')

    def test_skipped_junit_not_pass(self):
        def fake(argv,cwd,env,timeout,check):
            dest=Path(env['JAVA_JUNIT_DIR'])
            (dest/'TEST-Example.xml').write_text('<testsuite><testcase name="x"><skipped/></testcase></testsuite>')
            return type('Proc',(),{'returncode':0})()
        with mock.patch.object(run_java_tests.subprocess,'run',side_effect=fake):
            self.assertEqual(run_java_tests.run(self.args),2)


class RetentionTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        self.root=Path(t.name)
        self.cfg={'unit_integration_results_dir':'reports/test-results',
                  'e2e_evidence_dir':'reports/e2e','artifact_retention_days':90}
        self.now=datetime(2026,11,24,tzinfo=timezone.utc)

    def create_run(self,age:int,kind='e2e',finished=True):
        base=artifact_root(self.root,self.cfg,'e2e' if kind=='e2e' else 'unit')
        run=base/'CHG-001'/f'RUN-{age}'
        target=run if kind=='e2e' else run/'unit';target.mkdir(parents=True)
        if finished:
            timestamp=(self.now-timedelta(days=age)).isoformat()
            (target/'summary.json').write_text(json.dumps({'run_id':run.name,'change_id':'CHG-001','status':'PASS','finished_at_utc':timestamp}))
            (target/'run-context.json').write_text(json.dumps({'run_id':run.name,'change_id':'CHG-001','execution_finished':True}))
        return run

    def test_retention_only_completed_runs_older_than_90_days(self):
        old=self.create_run(91)
        recent=self.create_run(89)
        incomplete=self.create_run(200,kind='unit',finished=False)
        old_unit=self.create_run(100,kind='unit')
        self.assertEqual(prune_artifacts.expired_runs(self.root,self.cfg,self.now),sorted([old,old_unit]))
        self.assertTrue(recent.exists());self.assertTrue(incomplete.exists())

    def test_reject_symlink_root(self):
        (self.root/'outside').mkdir()
        (self.root/'reports').symlink_to(self.root/'outside',target_is_directory=True)
        with self.assertRaises(SetupError):prune_artifacts.expired_runs(self.root,self.cfg,self.now)

if __name__=='__main__':unittest.main()
