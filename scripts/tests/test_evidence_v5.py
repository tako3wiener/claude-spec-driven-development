"""v5 R07/R09/R10/R14/R16: corrupt/stale/unauthorized evidence tests."""
import csv
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zipfile
import zlib

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from check_e2e_evidence import verify, valid_png, valid_trace


def png():
    def chunk(name,data):
        return struct.pack('>I',len(data))+name+data+struct.pack('>I',zlib.crc32(name+data)&0xffffffff)
    # 2x2 RGBA image, scanline filter 0
    raw=(b'\x00'+b'\xff\x00\x00\xff'*2)*2
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',2,2,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b'')


def digest(data):return hashlib.sha256(data).hexdigest()


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        self.root=Path(t.name)/'repo';self.root.mkdir()
        self.change=self.root/'docs/changes/CHG-001';self.change.mkdir(parents=True)
        self.run=self.root/'reports/e2e/CHG-001/RUN-001';self.run.mkdir(parents=True)
        source=self.root/'src/test/java/e2e/ApprovedE2eTest.java';source.parent.mkdir(parents=True)
        source.write_text('public class ApprovedE2eTest {}')
        with (self.change/'test_cases.csv').open('w',newline='') as f:
            w=csv.writer(f);w.writerow(['tc_id','level','test_file','approval_state'])
            w.writerow(['TC-001','E2E','src/test/java/e2e/ApprovedE2eTest.java','APPROVED'])
        self.ctx={'schema_version':2,'change_id':'CHG-001','run_id':'RUN-001','mode':'final',
                  'environment_origin':'http://localhost:8080','snapshot_id':'a'*64,
                  'runner_token_digest':'b'*64,'execution_finished':True,'command_exit_code':0}
        self.write(self.run/'run-context.json',self.ctx)
        self.case=self.run/'TC-001';self.case.mkdir()
        images=[]
        for name in ('01-initial.png','02-result-verified.png'):
            (self.case/name).write_bytes(png())
            images.append({'file':name,'step':name[:-4],'sha256':digest(png())})
        with zipfile.ZipFile(self.case/'trace.zip','w') as z:
            z.writestr('trace.trace',json.dumps({'type':'context-options'})+'\n')
        self.manifest={'schema_version':2,'change_id':'CHG-001','run_id':'RUN-001',
              'tc_id':'TC-001','result':'PASS','snapshot_id':'a'*64,
              'environment_origin':'http://localhost:8080','runner_token_digest':'b'*64,
              'screenshots':images,'trace_file':'trace.zip','trace_sha256':hashlib.sha256((self.case/'trace.zip').read_bytes()).hexdigest()}
        self.write(self.case/'manifest.json',self.manifest)
        junit=self.run/'junit';junit.mkdir()
        (junit/'TEST-ApprovedE2eTest.xml').write_text('<testsuite tests="1"><testcase classname="ApprovedE2eTest" name="TC_001_shouldWork" /></testsuite>')

    @staticmethod
    def write(path,obj):path.write_text(json.dumps(obj),encoding='utf-8')
    def errors(self):return verify(self.change,self.run)
    def test_good_artifact_consistency(self):self.assertEqual(self.errors(),[])
    def test_png_is_actually_decoded(self):
        self.assertTrue(valid_png(self.case/'01-initial.png'))
        (self.case/'01-initial.png').write_bytes(b'\x89PNG\r\n\x1a\n'+b'x'*80)
        self.assertFalse(valid_png(self.case/'01-initial.png'))
        self.assertTrue(any('invalid PNG' in x for x in self.errors()))
    def test_mutated_png_hash(self):
        self.manifest['screenshots'][0]['sha256']='0'*64
        self.write(self.case/'manifest.json',self.manifest)
        self.assertTrue(any('SHA mismatch' in x for x in self.errors()))
    def test_fake_trace_rejected(self):
        with zipfile.ZipFile(self.case/'trace.zip','w') as z:z.writestr('trace.trace','not json')
        self.manifest['trace_sha256']=digest((self.case/'trace.zip').read_bytes())
        self.write(self.case/'manifest.json',self.manifest)
        self.assertFalse(valid_trace(self.case/'trace.zip'))
        self.assertTrue(any('invalid Playwright trace' in x for x in self.errors()))
    def test_run_id_mismatch(self):
        self.manifest['run_id']='RUN-OLD';self.write(self.case/'manifest.json',self.manifest)
        self.assertTrue(any('run_id mismatch' in x for x in self.errors()))
    def test_snapshot_mismatch(self):
        self.manifest['snapshot_id']='d'*64;self.write(self.case/'manifest.json',self.manifest)
        self.assertTrue(any('snapshot_id mismatch' in x for x in self.errors()))
    def test_zero_junit_tests(self):
        (self.run/'junit/TEST-ApprovedE2eTest.xml').write_text('<testsuite tests="0" />')
        self.assertTrue(any('Zero tests' in x for x in self.errors()))
    def test_junit_skipped_rejected(self):
        (self.run/'junit/TEST-ApprovedE2eTest.xml').write_text('<testsuite tests="1"><testcase name="TC_001_shouldWork"><skipped /></testcase></testsuite>')
        self.assertTrue(any('skip' in x.lower() for x in self.errors()))
    def test_demo_run_rejected(self):
        self.ctx['mode']='demo';self.write(self.run/'run-context.json',self.ctx)
        self.assertTrue(any('DEMO_ONLY' in x for x in self.errors()))
    def test_missing_java_source_rejected(self):
        (self.root/'src/test/java/e2e/ApprovedE2eTest.java').unlink()
        self.assertTrue(any('missing formal Java test source' in x for x in self.errors()))
    def test_runner_failure_rejected(self):
        self.ctx['command_exit_code']=1;self.write(self.run/'run-context.json',self.ctx)
        self.assertTrue(any('non-zero' in x for x in self.errors()))
    def test_junit_without_tc_id_rejected(self):
        f=self.run/'junit/TEST-ApprovedE2eTest.xml'
        f.write_text('<testsuite tests="2"><testcase name="TC_001_shouldWork" />'
                     '<testcase name="unexpectedTest" /></testsuite>')
        self.assertTrue(any('Unmapped JUnit testcase' in x for x in self.errors()))

    def test_java_source_traversal_rejected(self):
        file=self.change/'test_cases.csv'
        file.write_text('tc_id,level,test_file,approval_state\n'
                        'TC-001,E2E,../../../../outside/Test.java,APPROVED\n')
        self.assertTrue(any('missing formal Java test source' in x for x in self.errors()))

    def test_stale_report_no_current_run_xml(self):
        (self.run/'junit/TEST-ApprovedE2eTest.xml').unlink()
        self.assertTrue(any('CURRENT run JUnit XML' in x for x in self.errors()))

if __name__=='__main__':unittest.main()
