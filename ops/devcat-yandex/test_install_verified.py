import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


MODULE_PATH = Path(__file__).with_name('install-verified.py')
SPEC = importlib.util.spec_from_file_location('install_verified', MODULE_PATH)
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class VerifyManifestTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.base = self.root / 'host'
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.release = self.base / 'releases' / ('a' * 40)
        self.release.mkdir(parents=True)
        (self.release / 'yacli').write_bytes(b'previous')
        (self.base / 'current').symlink_to(self.release)
        self.artifact = self.repo / 'target/devcat-delivery/candidate'
        self.artifact.parent.mkdir(parents=True)
        self.artifact.write_bytes(b'candidate')
        self.report = self.repo / 'candidate-report.json'
        self.report_data = {
            'state': 'PASS',
            'scope': 'candidate_build',
            'source_head': 'b' * 40,
            'binary_sha256': self.digest(self.artifact),
            'checks': {
                'fmt': 'PASS',
                'clippy': 'PASS',
                'locked_tests': 'PASS',
                'release_build': 'PASS',
                'smoke': 'PASS',
            },
        }
        self.write_report()
        self.manifest = {
            'source_head': 'b' * 40,
            'artifact': 'target/devcat-delivery/candidate',
            'binary_sha256': self.digest(self.artifact),
            'verification_report': 'candidate-report.json',
            'verification_sha256': self.digest(self.report),
            'expected_previous_target': str(self.release),
            'expected_previous_sha256': self.digest(self.release / 'yacli'),
        }
        self.original_repo = installer.REPO
        self.original_base = installer.BASE
        installer.REPO = self.repo
        installer.BASE = self.base
        self.git_patch = patch.object(installer, 'git', side_effect=self.fake_git)
        self.git_patch.start()

    def tearDown(self):
        self.git_patch.stop()
        installer.REPO = self.original_repo
        installer.BASE = self.original_base
        self.temporary.cleanup()

    @staticmethod
    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def write_report(self):
        self.report.write_text(json.dumps(self.report_data))

    @staticmethod
    def fake_git(*args):
        if args[0] == 'status':
            return ''
        if args[0] in ('cat-file', 'merge-base'):
            return ''
        raise AssertionError(args)

    def assert_rejected(self, message):
        with self.assertRaisesRegex(RuntimeError, message):
            installer.verify_manifest(self.manifest)

    def test_accepts_exact_candidate_and_live_baseline(self):
        source, binary, previous = installer.verify_manifest(self.manifest)
        self.assertEqual(source, 'b' * 40)
        self.assertEqual(binary, self.artifact)
        self.assertEqual(previous, str(self.release))

    def test_rejects_manifest_baseline_target_mismatch(self):
        self.manifest['expected_previous_target'] = str(
            self.base / 'releases' / ('c' * 40)
        )
        self.assert_rejected('deployed HEAD mismatch')

    def test_rejects_live_baseline_hash_mismatch(self):
        self.manifest['expected_previous_sha256'] = '0' * 64
        self.assert_rejected('deployed binary hash mismatch')

    def test_rejects_artifact_hash_mismatch(self):
        self.manifest['binary_sha256'] = '0' * 64
        self.assert_rejected('artifact hash mismatch')

    def test_rejects_report_hash_mismatch(self):
        self.manifest['verification_sha256'] = '0' * 64
        self.assert_rejected('verification report changed')

    def test_rejects_report_with_different_artifact_hash(self):
        self.report_data['binary_sha256'] = '0' * 64
        self.write_report()
        self.manifest['verification_sha256'] = self.digest(self.report)
        self.assert_rejected('verified binary mismatch')

    def test_rejects_report_for_different_source(self):
        self.report_data['source_head'] = 'c' * 40
        self.write_report()
        self.manifest['verification_sha256'] = self.digest(self.report)
        self.assert_rejected('verification source mismatch')


if __name__ == '__main__':
    unittest.main()
