import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import standalone as sdd
import group_work as work
from scripts import group_gate as gate


class GroupTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.base=Path(tmp.name);self.kit=self.base/'kit';self.kit.mkdir()
        # Test with real Git repositories and temporary profile/group registries.
        self.patcher=patch.object(sdd,'SOURCE',self.kit);self.patcher.start();self.addCleanup(self.patcher.stop)
        self.members={}
        for i in range(3):self.add_repo(f'app{i}')
        self.cid='CHG-001'

    def add_repo(self,name,execution='devcontainer'):
        root=self.base/name;root.mkdir();subprocess.run(['git','init','-q',str(root)],check=True)
        self.members[name]=root
        sdd.configure(SimpleNamespace(profile=name,codebase=str(root),execution=execution,replace=False))
        return root

    def plan(self,name):
        root=self.members[name];d=root/'docs/changes'/self.cid;d.mkdir(parents=True,exist_ok=True)
        for f in ('requirements.csv','test_cases.csv','change-plan.md'):(d/f).write_text(name+' '+f+'\n')
        d=root/'.claude/local';d.mkdir(parents=True,exist_ok=True);(d/'project-env.json').write_text('{}')

    def create(self,names=None,summary='API contract, implementation order and integration checks'):
        work.create(sdd,SimpleNamespace(change_id=self.cid,profiles=names or list(self.members),summary=summary,plan_file=None))

    def fake_probe(self,api,member,cid,mode):
        return {'setup':'READY','hashes':gate.plan_hashes(Path(member['codebase']),cid),'net':'PENDING_OR_STALE','g6':'PENDING_OR_STALE'}

    def approve(self):
        # The trusted existing project setup validator is mocked for this unit test;
        # the plans, approval files, and hashes are actual files in temporary Git repos.
        with patch.object(work,'probe',side_effect=self.fake_probe),patch('sys.stdin.isatty',return_value=True),patch('sys.stdout.isatty',return_value=True),patch('builtins.input',side_effect=['operator','APPROVE G2 '+self.cid]):
            work.approve(sdd,SimpleNamespace(change_id=self.cid))

    def all_plans(self):
        for n in self.members:self.plan(n)

    def test_three_roots_same_change(self):
        self.create();self.all_plans()
        with self.assertRaises(gate.GroupError):gate.verify_group(self.kit,self.cid,required=True)
        self.approve()
        for root in self.members.values():
            self.assertIsNotNone(gate.verify_group(self.kit,self.cid,root=root,execution='devcontainer'))
            record=gate.decode(gate.read(root,gate.approval_path(self.cid)))
            self.assertEqual(record['group_id'],self.cid)
            self.assertEqual(record['scope_hash'],gate.plan_hashes(root,self.cid)['scope_hash'])

    def test_peer_plan_change_blocks_all(self):
        self.create();self.all_plans();self.approve()
        (self.members['app2']/'docs/changes'/self.cid/'change-plan.md').write_text('changed')
        for root in self.members.values():
            with self.assertRaises(gate.GroupError):gate.verify_group(self.kit,self.cid,root=root,execution='devcontainer')
        self.approve();self.assertIsNotNone(gate.verify_group(self.kit,self.cid))

    def test_missing_plan_blocks_without_writing_approval(self):
        self.create();self.plan('app0');self.plan('app1')
        with self.assertRaises(gate.GroupError):self.approve()
        self.assertFalse((self.members['app0']/gate.approval_path(self.cid)).exists())

    def test_changed_record_blocks_all(self):
        self.create();self.all_plans();self.approve()
        p=self.members['app2']/gate.approval_path(self.cid);p.write_text(p.read_text()+' ')
        with self.assertRaises(gate.GroupError):gate.verify_group(self.kit,self.cid)

    def test_common_plan_change_invalidates_approval(self):
        self.create();self.all_plans();self.approve()
        p=self.base/'new.md';p.write_text('new cross-repo plan')
        work.update_plan(sdd,SimpleNamespace(change_id=self.cid,file=p))
        with self.assertRaises(gate.GroupError):gate.verify_group(self.kit,self.cid)

    def test_profile_replacement_invalidates_group(self):
        self.create();self.all_plans();self.approve()
        root=self.add_repo('other')
        sdd.configure(SimpleNamespace(profile='app2',codebase=str(root),execution='devcontainer',replace=True))
        with self.assertRaises(gate.GroupError):gate.verify_group(self.kit,self.cid)

    def test_duplicate_root_rejected(self):
        sdd.configure(SimpleNamespace(profile='alias',codebase=str(self.members['app0']),execution='devcontainer',replace=False))
        with self.assertRaises(gate.GroupError):self.create(['app0','alias'])

    def test_mixed_execution_rejected(self):
        self.add_repo('local','local')
        with self.assertRaises(gate.GroupError):self.create(['app0','local'])

    def test_ten_roots_supported_eleven_rejected(self):
        for i in range(3,11):self.add_repo(f'app{i}')
        with self.assertRaises(gate.GroupError):self.create()
        self.create(list(self.members)[:10]);self.assertEqual(len(gate.group_for(self.kit,self.cid)['members']),10)

    def test_dry_run_peer_paths_and_nonmember_rejection(self):
        self.create();self.add_repo('other')
        args=SimpleNamespace(profile='app0',change_id=self.cid,dry_run=True,model=None)
        out=io.StringIO()
        with contextlib.redirect_stdout(out):self.assertEqual(sdd.run(args),0)
        data=json.loads(out.getvalue());self.assertEqual(set(data['read_only_peers']),{str(self.members['app1']),str(self.members['app2'])})
        self.assertEqual(data['git_root'],str(self.members['app0']))
        args.profile='other'
        with self.assertRaises(sdd.Conflict):sdd.run(args)

    def test_lock_same_repo_only(self):
        with sdd.repository_lock(self.members['app0']):
            with sdd.repository_lock(self.members['app1']):pass
            with self.assertRaises(sdd.Conflict):
                with sdd.repository_lock(self.members['app0']):pass
        with sdd.repository_lock(self.members['app0']):pass

    def test_launch_group_bundle_includes_context(self):
        self.create();group=gate.group_for(self.kit,self.cid)
        root=self.members['app0'];d=self.base/'bundle';d.mkdir()
        # Minimal copy of the actual package templates, without modifying app files.
        import shutil
        shutil.copytree(ROOT/'.claude',self.kit/'.claude');shutil.copy2(ROOT/'CLAUDE.md',self.kit/'CLAUDE.md')
        settings,rules=sdd.runtime_bundle(d,root,'app0','devcontainer',self.cid,group)
        self.assertEqual(json.loads(settings.read_text())['env']['SDD_GROUP_ID'],self.cid)
        self.assertIn(str(self.members['app2']),rules.read_text())
        self.assertIn('edit ONLY '+str(root),rules.read_text())

    def test_group_status_does_not_claim_whole_system_pass(self):
        self.create();self.all_plans();self.approve();out=io.StringIO()
        with patch.object(work,'probe',side_effect=self.fake_probe),contextlib.redirect_stdout(out):work.status(sdd,SimpleNamespace(change_id=self.cid))
        d=json.loads(out.getvalue());self.assertEqual(d['group_g2'],'VERIFIED');self.assertFalse(d['all_member_acceptances_verified'])

    def test_group_evidence_changes_on_peer_code_or_reapproval(self):
        self.create();self.all_plans();self.approve()
        def fake_snapshot(root):
            path=root/'App.java'
            return {'snapshot_id':gate.digest(path.read_bytes() if path.exists() else b'')}
        first=gate.evidence_context(self.kit,self.cid,self.members['app0'],'devcontainer',fake_snapshot)
        (self.members['app2']/'App.java').write_text('changed')
        second=gate.evidence_context(self.kit,self.cid,self.members['app0'],'devcontainer',fake_snapshot)
        self.assertNotEqual(first,second)
        self.assertEqual(first['group_approval_id'],second['group_approval_id'])
        self.approve()
        third=gate.evidence_context(self.kit,self.cid,self.members['app0'],'devcontainer',fake_snapshot)
        self.assertNotEqual(second['group_approval_id'],third['group_approval_id'])

    def test_group_registry_survives_uninstall_and_reinstall(self):
        self.create();target=self.base/'installed';sdd.apply_changes(target,sdd.install_plan(ROOT,target))
        (target/gate.GROUPS).write_bytes((self.kit/gate.GROUPS).read_bytes())
        sdd.apply_changes(target,sdd.uninstall_plan(target));self.assertTrue((target/gate.GROUPS).is_file())
        sdd.apply_changes(target,sdd.install_plan(ROOT,target));self.assertTrue((target/gate.GROUPS).is_file())

if __name__=='__main__':unittest.main()
