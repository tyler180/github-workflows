import hashlib
import importlib.util
import io
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('installer', Path(__file__).resolve().parents[1] / 'gitleaks_install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.binary = b'fixture binary, never executed\n'
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w:gz') as archive:
            member = tarfile.TarInfo('gitleaks')
            member.size = len(self.binary)
            archive.addfile(member, io.BytesIO(self.binary))
        self.archive = output.getvalue()
        self.artifact = ('fixture', hashlib.sha256(self.archive).hexdigest(), hashlib.sha256(self.binary).hexdigest())
        self.cache = patch.dict(os.environ, {'PLATFORM_TOOL_CACHE': self.temp.name})
        self.cache.start()
        self.addCleanup(self.cache.stop)

    def install(self):
        with patch.object(installer, 'artifact', return_value=self.artifact), patch.object(installer, 'download', side_effect=lambda _, path: path.write_bytes(self.archive)) as download:
            binary = installer.ensure()
        return binary, download.call_count

    def test_verified_install_and_offline_reuse(self):
        binary, calls = self.install()
        self.assertEqual(calls, 1)
        self.assertEqual(binary.read_bytes(), self.binary)
        self.assertTrue(os.access(binary, os.X_OK))
        with patch.object(installer, 'artifact', return_value=self.artifact), patch.object(installer, 'download', side_effect=AssertionError('offline')):
            self.assertEqual(installer.ensure(), binary)

    def test_corrupt_cache_fails_without_redownload_or_execution(self):
        binary, _ = self.install()
        binary.write_bytes(b'tampered')
        with patch.object(installer, 'artifact', return_value=self.artifact), patch.object(installer, 'download') as download:
            with self.assertRaisesRegex(ValueError, 'Cached.*checksum'):
                installer.ensure()
            download.assert_not_called()

    def test_archive_checksum_failure_never_publishes_binary(self):
        with patch.object(installer, 'artifact', return_value=self.artifact), patch.object(installer, 'download', side_effect=lambda _, path: path.write_bytes(b'unverified archive')):
            with self.assertRaisesRegex(ValueError, 'archive checksum'):
                installer.ensure()
        self.assertFalse(any(path.name == 'gitleaks' and path.is_file() for path in Path(self.temp.name).rglob('*')))

    def test_binary_checksum_failure_never_publishes_binary(self):
        expected = (*self.artifact[:2], '0' * 64)
        with patch.object(installer, 'artifact', return_value=expected), patch.object(installer, 'download', side_effect=lambda _, path: path.write_bytes(self.archive)):
            with self.assertRaisesRegex(ValueError, 'binary checksum'):
                installer.ensure()
        self.assertFalse(any(path.name == 'gitleaks' and path.is_file() for path in Path(self.temp.name).rglob('*')))

    def test_symlink_cache_is_rejected(self):
        binary, _ = self.install()
        binary.unlink()
        binary.symlink_to(Path(self.temp.name) / 'elsewhere')
        with patch.object(installer, 'artifact', return_value=self.artifact):
            with self.assertRaisesRegex(ValueError, 'symlink'):
                installer.ensure()

    def test_platform_aliases_and_unsupported_platform(self):
        for system, machine, expected in [('Linux', 'aarch64', 'linux_arm64'), ('Darwin', 'arm64', 'darwin_arm64'), ('Linux', 'AMD64', 'linux_x64')]:
            with patch.object(installer.platform, 'system', return_value=system), patch.object(installer.platform, 'machine', return_value=machine):
                self.assertEqual(installer.artifact()[0], expected)
        with patch.object(installer.platform, 'system', return_value='Windows'):
            with self.assertRaises(ValueError):
                installer.artifact()


if __name__ == '__main__':
    unittest.main()
