import os
from pathlib import Path
import subprocess
import unittest


class AutomationInputTests(unittest.TestCase):
    def test_version_sha_and_alias_contract(self):
        workflow = Path(__file__).resolve().parents[2] / '.github/workflows/repository-bootstrap.yml'
        block = workflow.read_text().split('- name: Validate approved automation pin', 1)[1].split('run: |', 1)[1].split('      - uses:', 1)[0]
        command = '\n'.join(line[10:] for line in block.splitlines() if line.strip())
        for ref, sha, allowed in [('v2.3.0-rc.1', '', True), ('a'*40, '', True), ('', 'b'*40, True), ('main', '', False), ('v2', '', False), ('', '', False), ('v2.3.0', 'b'*40, False)]:
            with self.subTest(ref=ref, sha=sha):
                result = subprocess.run(['bash', '-c', command], env={**os.environ, 'AUTOMATION_REF': ref, 'LEGACY_SHA': sha}, capture_output=True)
                self.assertEqual(result.returncode == 0, allowed)
