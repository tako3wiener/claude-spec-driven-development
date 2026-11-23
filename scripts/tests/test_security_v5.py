"""v5 R01/R02/R03/R08/R16 approval-record and hook regression tests."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SRC=Path(__file__).resolve().parents[1]

class SecurityTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        self.tmp=Path(t.name);self.repo=self.tmp/'repo'
        (self.repo/'scripts').mkdir(parents=True)
        for name in ['setup_errors.py','artifact_paths.py','project_setup.py','v5_core.py','approval_gate.py','guard_test_commands.py']:
            shutil.copy2(SRC/name,self.repo/'scripts'/name)
        (self.repo/'module').mkdir()
        (self.repo/'module/pom.xml').write_text('<project/>')
        (self.repo/'docs/changes/CHG-001').mkdir(parents=True)
        (self.repo/'docs/changes/CHG-001/requirements.csv').write_text('req_id,requirement\nREQ-001,value\n')
        (self.repo/'docs/changes/CHG-001/test_cases.csv').write_text('tc_id,req_id\nTC-001,REQ-001\n')
        (self.repo/'docs/changes/CHG-001/change-plan.md').write_text('# Design\n')
        (self.repo/'.claude/local/approvals').mkdir(parents=True)
        (self.repo/'.gitignore').write_text('.claude/local/\n')
        subprocess.run(['git','init','-q',str(self.repo)],check=True)
        subprocess.run(['git','-C',str(self.repo),'config','user.email','test@example.invalid'],check=True)
        subprocess.run(['git','-C',str(self.repo),'config','user.name','Tester'],check=True)
        subprocess.run(['git','-C',str(self.repo),'add','.'],check=True)
        subprocess.run(['git','-C',str(self.repo),'commit','-qm','init'],check=True)
        self.env=dict(os.environ,SDD_CHANGE_ID='CHG-001')
        self.env.pop('SDD_APPROVAL_PUBLIC_KEY',None)
        self.init()

    def call(self,name,*args,input_text=None):
        return subprocess.run([sys.executable,str(self.repo/'scripts'/name),*args],
                    input=input_text,cwd=self.repo,capture_output=True,text=True,env=self.env)

    def init(self):
        p=self.call('project_setup.py','init','--non-interactive','--codebase','module','--environment','test',
                    '--base-url','http://localhost:18080/','--unit-command','mvn test',
                    '--e2e-command','mvn -f module/pom.xml test','--e2e-workdir','.',
                    '--confirm-codebase','--confirm-isolated')
        self.assertEqual(p.returncode,0,p.stderr)

    def approve(self,gate,values):
        folder=self.repo/'.claude/local/approvals'
        doc=folder/f'CHG-001-{gate}.json'
        doc.write_text(json.dumps({'change_id':'CHG-001','gate':gate,'approver':'operator',
            'approved_at':'2026-11-23T11:00:00+09:00','decision':'APPROVED',**values}),encoding='utf-8')
        return doc

    def approve_g2(self):
        p=self.call('approval_gate.py','hashes','--change-id','CHG-001')
        self.assertEqual(p.returncode,0,p.stderr)
        return self.approve('G2',json.loads(p.stdout))

    def hook(self,tool,tool_input):
        return self.call('guard_test_commands.py',input_text=json.dumps({'tool_name':tool,'tool_input':tool_input,'cwd':str(self.repo)}))

    def test_unsigned_g2_approves_then_stale_hash_invalidates(self):
        doc=self.approve_g2()
        p=self.call('approval_gate.py','verify-g2','--change-id','CHG-001')
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual(self.hook('Write',{'file_path':str(self.repo/'src/Main.java'),'content':'test'}).returncode,0)
        (self.repo/'docs/changes/CHG-001/test_cases.csv').write_text('tc_id,req_id\nTC-002,REQ-001\n')
        p=self.call('approval_gate.py','verify-g2','--change-id','CHG-001')
        self.assertEqual(p.returncode,2)
        self.assertEqual(self.hook('Write',{'file_path':str(self.repo/'src/Main.java'),'content':'test'}).returncode,2)

    def test_missing_approval_is_rejected(self):
        self.assertEqual(self.call('approval_gate.py','verify-g2','--change-id','CHG-001').returncode,2)

    def test_unsigned_json_does_not_require_key_or_signature(self):
        doc=self.approve_g2()
        self.assertFalse(doc.with_suffix('.sig').exists())
        self.assertEqual(self.call('approval_gate.py','verify-g2','--change-id','CHG-001').returncode,0)

    def test_ai_cannot_record_approval_through_hook(self):
        self.approve_g2()
        self.assertEqual(self.hook('Bash',{'command':'python3 scripts/approval_gate.py record-g2 --change-id CHG-001'}).returncode,2)
        self.assertEqual(self.hook('Write',{'file_path':str(self.repo/'.claude/local/approvals/CHG-001-G2.json'),'content':'{}'}).returncode,2)

    def test_record_requires_interactive_terminal(self):
        self.assertEqual(self.call('approval_gate.py','record-g2','--change-id','CHG-001',input_text='operator\nAPPROVE G2 CHG-001\n').returncode,2)

    def test_shell_compound_bypass_refused(self):
        for cmd in ['echo project_setup.py check; mvn test','echo project_setup.py init && mvn test',
                    'python3 scripts/project_setup.py check | mvn test','find . -exec touch /tmp/owned ;',
                    'sed -i s/x/y/ src/App.java']:
            with self.subTest(command=cmd):self.assertEqual(self.hook('Bash',{'command':cmd}).returncode,2)

    def test_change_docs_can_be_written_before_g2_but_code_cannot(self):
        self.assertEqual(self.hook('Write',{'file_path':str(self.repo/'docs/changes/CHG-001/analysis.md'),'content':'ok'}).returncode,0)
        self.assertEqual(self.hook('Edit',{'file_path':str(self.repo/'module/Config.java')}).returncode,2)
        self.assertEqual(self.hook('Bash',{'command':'git status --short'}).returncode,0)

    def test_network_approval_rejects_wrong_origin(self):
        self.approve('NET',{'allowed_origin':'http://localhost:18080','isolation_mode':'container-egress-policy',
             'dedicated_test_credentials':True,'synthetic_test_data':True,'trace_storage_approved':True})
        good=self.call('approval_gate.py','verify-net','--change-id','CHG-001','--allowed-origin','http://localhost:18080')
        self.assertEqual(good.returncode,0,good.stderr)
        bad=self.call('approval_gate.py','verify-net','--change-id','CHG-001','--allowed-origin','https://production.example')
        self.assertEqual(bad.returncode,2)

    def test_missing_network_approval_rejected(self):
        self.assertEqual(self.call('approval_gate.py','verify-net','--change-id','CHG-001','--allowed-origin','http://localhost:18080').returncode,2)

    def test_git_snapshot_changes_with_untracked(self):
        # Import from a subprocess so ROOT resolves to the fixture repo.
        command=[sys.executable,'-c',"import sys,json;sys.path.insert(0,'scripts');from v5_core import snapshot;print(json.dumps(snapshot()))"]
        def snap():
            p=subprocess.run(command,cwd=self.repo,env=self.env,capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stderr);return json.loads(p.stdout)
        initial=snap()
        target=self.repo/'module/NewTest.java';target.write_text('first')
        changed=snap()
        self.assertNotEqual(initial['snapshot_id'],changed['snapshot_id'])
        target.write_text('second')
        self.assertNotEqual(changed['snapshot_id'],snap()['snapshot_id'])

if __name__=='__main__':unittest.main()
