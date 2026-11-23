"""R09/R16 tests of actual local code/evidence references, not just CSV cells."""
import csv
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import check_traceability

class TraceabilityTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        self.root=Path(t.name)/'repo';self.root.mkdir()
        self.changedir=self.root/'docs/changes/CHG-001';self.changedir.mkdir(parents=True)
        self.testfile=self.root/'src/test/java/ExampleTest.java';self.testfile.parent.mkdir(parents=True)
        self.testfile.write_text('class ExampleTest {}')
        self.evidence=self.root/'reports/e2e/CHG-001/RUN-1/TC-001/manifest.json';self.evidence.parent.mkdir(parents=True)
        self.evidence.write_text('{}')
        for name,header,row in [
          ('requirements.csv',['req_id','source','classification','status','priority','requirement','acceptance_criteria'],['REQ-001','spec.pdf#p1','A','CONFIRMED','P1','Req','Expected']),
          ('test_cases.csv',['tc_id','req_id','level','kind','preconditions','steps','expected_result','test_file','approval_state'],['TC-001','REQ-001','E2E','normal','fixture','step','expected','src/test/java/ExampleTest.java','APPROVED']),
          ('test_results.csv',['tc_id','result','evidence'],['TC-001','PASS','reports/e2e/CHG-001/RUN-1/TC-001/manifest.json'])]:
            with (self.changedir/name).open('w',newline='') as f:
                w=csv.writer(f);w.writerow(header);w.writerow(row)
        self.patch=mock.patch.object(check_traceability,'ROOT',self.root);self.patch.start();self.addCleanup(self.patch.stop)
    def test_pass_with_existing_files(self):self.assertEqual(check_traceability.check(self.changedir,True,True),0)
    def test_missing_test_file_fails(self):
        self.testfile.unlink();self.assertEqual(check_traceability.check(self.changedir,True,True),1)
    def test_missing_evidence_fails(self):
        self.evidence.unlink();self.assertEqual(check_traceability.check(self.changedir,True,True),1)
if __name__=='__main__':unittest.main()
