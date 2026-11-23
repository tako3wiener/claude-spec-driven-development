import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('sdd_installer', ROOT / 'install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.target = Path(tmp.name) / 'app'
        self.target.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.target)], check=True)
        (self.target / 'pom.xml').write_text('<project/>', encoding='utf-8')
        self.source = Path(tmp.name) / 'starter'
        self.source.mkdir()
        for folder in installer.FOLDERS:
            (self.source / folder).mkdir(parents=True, exist_ok=True)
            (self.source / folder / 'fixture.txt').write_text('original\n')
        (self.source / 'CLAUDE.md').write_text('# Starter\n')
        (self.source / 'installer-gitignore.txt').write_text('.claude/local/\nreports/e2e/\n')
        (self.source / '.claude/settings.json').write_text(json.dumps({'permissions': {'deny': ['Read(./.env)']}, 'hooks': {'PreToolUse': [{'matcher': 'Bash', 'hooks': [{'type': 'command', 'command': 'python3', 'args': ['guard.py']}]}]}}))

    def install(self):
        changes = installer.plan(self.source, self.target)
        installer.apply(self.target, changes)
        return changes

    def test_fresh_install_and_idempotence(self):
        self.assertTrue(self.install())
        self.assertTrue((self.target / installer.STATE).is_file())
        self.assertEqual(self.install(), {})
        self.assertEqual((self.target / 'pom.xml').read_text(), '<project/>')

    def test_preserves_existing_settings_and_local_data(self):
        (self.target / '.claude/local').mkdir(parents=True)
        (self.target / '.claude/local/project-env.json').write_text('local data')
        (self.target / 'CLAUDE.md').write_text('# App rules\n')
        (self.target / '.gitignore').write_text('app.tmp\n')
        (self.target / '.claude/settings.json').write_text(json.dumps({'model': 'existing', 'permissions': {'allow': ['Read'], 'deny': ['Bash(foo)']}, 'hooks': {'PreToolUse': [{'hooks': [{'command': 'existing'}]}]}}))
        self.install()
        cfg = json.loads((self.target / '.claude/settings.json').read_text())
        self.assertEqual(cfg['model'], 'existing')
        self.assertEqual(cfg['permissions']['allow'], ['Read'])
        self.assertIn('Bash(foo)', cfg['permissions']['deny'])
        self.assertIn('Read(./.env)', cfg['permissions']['deny'])
        self.assertEqual(len(cfg['hooks']['PreToolUse']), 2)
        self.assertIn('# App rules', (self.target / 'CLAUDE.md').read_text())
        self.assertIn('app.tmp', (self.target / '.gitignore').read_text())
        self.assertEqual((self.target / '.claude/local/project-env.json').read_text(), 'local data')
        self.assertEqual(self.install(), {})

    def test_conflict_has_no_writes(self):
        (self.target / 'scripts').mkdir()
        (self.target / 'scripts/fixture.txt').write_text('user-owned')
        with self.assertRaises(installer.Conflict): self.install()
        self.assertFalse((self.target / 'CLAUDE.md').exists())
        self.assertEqual((self.target / 'scripts/fixture.txt').read_text(), 'user-owned')

    def test_managed_update_and_local_edit_refusal(self):
        self.install()
        (self.source / 'scripts/fixture.txt').write_text('new')
        self.install()
        self.assertEqual((self.target / 'scripts/fixture.txt').read_text(), 'new')
        (self.target / 'scripts/fixture.txt').write_text('edited by human')
        (self.source / 'scripts/fixture.txt').write_text('newer')
        with self.assertRaises(installer.Conflict): self.install()
        self.assertEqual((self.target / 'scripts/fixture.txt').read_text(), 'edited by human')

    def test_managed_block_edit_refusal(self):
        self.install()
        p = self.target / 'CLAUDE.md'
        p.write_text(p.read_text().replace('# Starter', '# Human edit'))
        with self.assertRaises(installer.Conflict): self.install()

    def test_symlink_refused(self):
        outside = self.target.parent / 'outside'
        outside.mkdir()
        (self.target / 'scripts').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(installer.Conflict): self.install()
        self.assertEqual(list(outside.iterdir()), [])

    def test_dry_run_does_not_write(self):
        changes = installer.plan(self.source, self.target)
        self.assertTrue(changes)
        self.assertEqual(sorted(x.name for x in self.target.iterdir()), ['.git', 'pom.xml'])

    def test_disabled_hooks_refused(self):
        (self.target / '.claude').mkdir()
        (self.target / '.claude/settings.json').write_text('{"disableAllHooks": true}')
        with self.assertRaises(installer.Conflict): self.install()
        self.assertFalse((self.target / 'CLAUDE.md').exists())

    def test_mismatched_hook_refused(self):
        (self.target / '.claude').mkdir()
        (self.target / '.claude/settings.json').write_text(json.dumps({'hooks': {'PreToolUse': [{'matcher': 'Write', 'hooks': [{'type': 'command', 'command': 'python3', 'args': ['guard.py']}]}]}}))
        with self.assertRaises(installer.Conflict): self.install()

    def test_preflight_detects_concurrent_change(self):
        changes = installer.plan(self.source, self.target)
        (self.target / 'CLAUDE.md').write_text('concurrent')
        with self.assertRaises(installer.Conflict): installer.apply(self.target, changes)
        self.assertEqual((self.target / 'CLAUDE.md').read_text(), 'concurrent')
        self.assertFalse((self.target / 'scripts').exists())


if __name__ == '__main__': unittest.main()
