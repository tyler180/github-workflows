import copy
import unittest
import subprocess
import sys
from unittest.mock import patch
import yaml
import managed_hooks as h
from test_terraform_starter import OverlayAPI


class HookTests(unittest.TestCase):
    def setUp(self):
        self.config = {'hook_ref': h.APPROVED_REF, 'ai_review': True}
        self.repo = {'name': 'pilot', 'stage': 'grant', 'developer_hooks': self.config}
        self.api = OverlayAPI()
        self.resolve = patch.object(h.b, 'resolve_reference', return_value=h.APPROVED_SHA)
        self.resolve.start()
        self.addCleanup(self.resolve.stop)

    def install(self):
        return h.install(self.api, 'owner', 'pilot', self.repo, 'run/1')

    def test_isolated_cli_matches_workflow_invocation(self):
        result = subprocess.run([sys.executable, "-I", h.__file__, "--help"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--inventory", result.stdout)

    def test_preserves_application_and_retry_reuses_pr(self):
        first = self.install()
        writes = len(self.api.writes)
        self.assertEqual(self.install(), first)
        self.assertEqual(len(self.api.writes), writes)
        for path, value in self.api.files['base'].items():
            self.assertEqual(self.api.files['head'][path], value)

    def test_merged_config_is_noop_after_application_edit(self):
        self.install()
        self.api.refs['main'] = 'head'
        self.api.files['head']['main.go'] = b'developer change'
        writes = len(self.api.writes)
        self.assertEqual(self.install()['status'], 'already-current')
        self.assertEqual(len(self.api.writes), writes)

    def test_modified_update_branch_rejected(self):
        self.install()
        self.api.files['head']['main.go'] = b'bad'
        with self.assertRaisesRegex(ValueError, 'Application file'):
            self.install()

    def test_preserves_other_hooks_and_top_level_config(self):
        data = b'default_stages: [pre-commit]\nrepos:\n- repo: local\n  hooks:\n  - id: custom\n    entry: custom\n    language: system\n'
        result = yaml.safe_load(h.transform(data, self.config))
        self.assertEqual(result['default_stages'], ['pre-commit'])
        self.assertEqual(result['repos'][0], yaml.safe_load(data)['repos'][0])
        self.assertEqual(result['repos'][1]['hooks'][-1]['stages'], ['pre-commit'])

    def test_existing_ai_config_is_byte_identical_noop(self):
        data = h.transform(None, self.config)
        self.assertEqual(h.transform(data, self.config), data)

    def test_duplicate_shared_entries_rejected(self):
        data = yaml.safe_dump({'repos': [{'repo': h.SHARED, 'hooks': []}]*2}).encode()
        with self.assertRaisesRegex(ValueError, 'Multiple shared'):
            h.transform(data, self.config)

    def test_disable_removes_only_ai_hook(self):
        data = h.transform(None, self.config)
        result = yaml.safe_load(h.transform(data, {**self.config, 'ai_review': False}))
        self.assertEqual([x['id'] for x in result['repos'][0]['hooks']], ['repository-hygiene', 'secret-scan'])

    def test_adoption_and_invalid_release_rejected(self):
        inventory = {'schema_version': 1, 'repositories': {'pilot': self.repo}}
        self.assertEqual(len(h.selections(inventory, 'owner')), 1)
        self.repo['stage'] = 'adopt'
        with self.assertRaises(ValueError): h.selections(inventory, 'owner')
        self.repo['stage'] = 'grant'
        self.config['hook_ref'] = 'main'
        with self.assertRaises(ValueError): h.selections(inventory, 'owner')

    def test_closed_pr_not_reopened(self):
        self.install()
        self.api.prs[0]['state'] = 'closed'
        with self.assertRaisesRegex(ValueError, 'closed'):
            self.install()

    def test_release_mismatch_prevents_writes(self):
        with patch.object(h.b, 'resolve_reference', return_value='a'*40):
            with self.assertRaisesRegex(ValueError, 'approved commit'):
                self.install()
        self.assertFalse(self.api.writes)


if __name__ == '__main__': unittest.main()
