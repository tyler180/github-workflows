import copy
import unittest
import terraform_starter as t
from test_bootstrap import FakeAPI


class OverlayAPI(FakeAPI):
    def __init__(self):
        super().__init__()
        self.files['base']['main.go'] = b'existing application'
        self.files['base']['terraform/main.tf'] = b'existing infrastructure'
        self.modes['base'].update({'main.go': '100644', 'terraform/main.tf': '100644'})

    def call(self, method, path, body=None, missing=False):
        result = super().call(method, path, body, missing)
        if path == 'repos/owner/pilot/git/commits' and method == 'POST':
            self.files['head'] = {**self.files['base'], **self.files['head']}
            self.modes['head'] = {**self.modes['base'], **self.modes['head']}
        return result


class TerraformStarterTests(unittest.TestCase):
    def setUp(self):
        self.api = OverlayAPI()
        self.repo = {'name': 'pilot', 'stage': 'create', 'terraform_starter': {
            'terraform_version': '1.15.0', 'workflow_ref': 'a'*40, 'working_directory': 'terraform'}}
        self.files = {name: {'content': ('caller: '+name).encode(), 'mode': '100644'} for name in t.FILES}

    def install(self, run='run/1'):
        return t.install(self.api, 'owner', 'pilot', self.repo, self.files, run)

    def test_preserves_application_and_terraform(self):
        self.install()
        for name in ('main.go', 'terraform/main.tf', 'README.md'):
            self.assertEqual(self.api.files['head'][name], self.api.files['base'][name])

    def test_retry_with_new_run_reuses_branch_and_pr(self):
        first = self.install()
        writes = len(self.api.writes)
        self.assertEqual(self.install('run/2'), first)
        self.assertEqual(len(self.api.writes), writes)

    def test_modified_application_on_starter_branch_is_rejected(self):
        self.install()
        self.api.files['head']['main.go'] = b'unexpected change'
        writes = len(self.api.writes)
        with self.assertRaisesRegex(ValueError, 'application file was modified'):
            self.install()
        self.assertEqual(len(self.api.writes), writes)

    def test_existing_filename_conflict_is_rejected_before_writes(self):
        name = '.github/workflows/terraform-plan.yml'
        self.api.files['base'][name] = b'existing caller'
        self.api.modes['base'][name] = '100644'
        with self.assertRaisesRegex(ValueError, 'filenames conflict'):
            self.install()
        self.assertFalse(self.api.writes)

    def test_merged_overlay_and_later_source_edits_are_preserved(self):
        self.install()
        self.api.refs['main'] = 'head'
        self.api.files['head']['main.go'] = b'later developer edit'
        writes = len(self.api.writes)
        self.assertEqual(self.install()['status'], 'already-installed')
        self.assertEqual(len(self.api.writes), writes)

    def test_modified_installed_caller_is_not_overwritten(self):
        self.install()
        self.api.refs['main'] = 'head'
        self.api.files['head']['.github/workflows/terraform-plan.yml'] = b'custom caller'
        with self.assertRaisesRegex(ValueError, 'caller was edited'):
            self.install()

    def test_selections_reject_adoption_and_unsafe_configuration(self):
        invalid = [('stage', 'adopt'), ('terraform_version', 'latest'),
                   ('workflow_ref', 'main'), ('working_directory', '../terraform')]
        for field, value in invalid:
            with self.subTest(field=field):
                repo = copy.deepcopy(self.repo)
                target = repo if field == 'stage' else repo['terraform_starter']
                target[field] = value
                with self.assertRaises(ValueError):
                    t.selections({'schema_version': 1, 'repositories': {'pilot': repo}}, 'owner')

    def test_unselected_repository_is_skipped(self):
        self.assertEqual(t.selections({'schema_version': 1, 'repositories': {'pilot': {'name': 'pilot'}}}, 'owner'), [])


if __name__ == '__main__':
    unittest.main()
