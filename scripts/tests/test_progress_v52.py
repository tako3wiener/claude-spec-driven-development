import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        (self.root/'scripts').mkdir()
        source=Path(__file__).parents[1]/'progress.py'
        target=self.root/'scripts/progress.py';target.write_text(source.read_text(encoding='utf-8'),encoding='utf-8')
        subprocess.run(['git','init','-q',str(self.root)],check=True)
        spec=importlib.util.spec_from_file_location('progress_test_copy',str(target));self.module=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.module)
    def tearDown(self):self.tmp.cleanup()
    def test_writes_snapshot_and_history_without_dashboard(self):
        result=self.module.record('CHG-100','G1','running','仕様を確認')
        self.assertEqual(result['branch'],self.module._git('branch','--show-current') or '(detached)')
        folder=self.root/'.claude/local/progress'
        self.assertEqual(json.loads((folder/'CHG-100.json').read_text())['next_action'],'仕様を確認')
        self.module.record('CHG-100','G2','waiting','承認待ち')
        self.assertEqual(len((folder/'CHG-100.jsonl').read_text().splitlines()),2)
    def test_invalid_id_and_gate(self):
        for change,gate in [('../private','G1'),('CHG-001','G9')]:
            with self.assertRaises(ValueError):self.module.record(change,gate,'running')
    def test_best_effort_logs_exception_and_does_not_stop(self):
        self.assertIsNone(self.module.best_effort('bad-id','G1','running'))

if __name__=='__main__':unittest.main()
