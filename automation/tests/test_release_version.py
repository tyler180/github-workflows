import importlib.util
import tempfile
import subprocess
import os
import unittest
from pathlib import Path

import yaml

BASE = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('release_version', BASE / 'automation/release_version.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ReleaseVersionTests(unittest.TestCase):
    def test_versions_are_numeric_and_only_patch_increments(self):
        self.assertEqual(module.select_version(['v0.2.9', 'v0.2.10', 'junk'], []), ('v0.2.11', True))
        self.assertEqual(module.select_version(['v1.0.0', 'v0.9.99'], []), ('v1.0.1', True))
        self.assertEqual(module.select_version([], []), ('v0.1.0', True))

    def test_retry_reuses_the_commit_tag(self):
        self.assertEqual(module.select_version(['v0.2.4', 'v0.2.5'], ['v0.2.4']), ('v0.2.4', False))
        self.assertEqual(module.select_version(['v0.2.4', 'v0.2.5'], ['v0.2.4', 'v0.2.5']), ('v0.2.5', False))

    def test_stale_main_commit_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            previous = os.getcwd()
            try:
                os.chdir(tmp)
                subprocess.run(['git', 'init', '-q'], check=True)
                subprocess.run(['git', '-c', 'user.name=Test', '-c', 'user.email=test@example.com',
                                'commit', '-q', '--allow-empty', '-m', 'one'], check=True)
                old = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
                subprocess.run(['git', '-c', 'user.name=Test', '-c', 'user.email=test@example.com',
                                'commit', '-q', '--allow-empty', '-m', 'two'], check=True)
                subprocess.run(['git', 'update-ref', 'refs/remotes/origin/main', 'HEAD'], check=True)
                self.assertEqual(module.resolve('', True, 'main')['tag'], 'v0.1.0')
                subprocess.run(['git', 'checkout', '-q', old], check=True)
                self.assertEqual(module.resolve('', True, 'main'), {'skip': 'true'})
            finally:
                os.chdir(previous)

    def test_embedded_selector_matches_tested_script(self):
        workflow = yaml.load((BASE / '.github/workflows/reusable-release.yaml').read_text(), Loader=yaml.BaseLoader)
        step = next(s for s in workflow['jobs']['release']['steps'] if s.get('name') == 'Validate release and resolve image')
        embedded = step['run'].split("python3 - <<'PY'\n", 1)[1].split('\n# Workflow inputs', 1)[0]
        self.assertEqual(embedded.rstrip(), (BASE / 'automation/release_version.py').read_text().rstrip())


if __name__ == '__main__':
    unittest.main()
