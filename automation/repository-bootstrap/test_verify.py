import copy
import unittest
import verify


class API:
    def __init__(self):
        self.head = 'a' * 40
        self.run = {'id': 1, 'event': 'pull_request', 'head_sha': self.head,
            'path': '.github/workflows/ci.yml', 'status': 'completed',
            'conclusion': 'success', 'html_url': 'https://github.com/owner/pilot/actions/runs/1'}
        self.jobs = [{'name': 'validation / platform-ci', 'status': 'completed', 'conclusion': 'success'}]

    def call(self, method, path):
        assert method == 'GET'
        if '/pulls/' in path:
            return {'head': {'sha': self.head}}
        if '/jobs?' in path:
            return {'total_count': len(self.jobs), 'jobs': self.jobs}
        return {'total_count': 1, 'workflow_runs': [self.run]}


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.api = API()
        self.result = {'repository': 'owner/pilot', 'head_sha': 'a'*40, 'pr_url': 'https://github.com/owner/pilot/pull/1'}

    def test_exact_head_and_successful_aggregate(self):
        self.assertTrue(verify.observe(self.api, self.result))

    def test_manual_wrong_head_or_wrong_workflow_cannot_satisfy_ci(self):
        for field, value in [('event', 'workflow_dispatch'), ('head_sha', 'b'*40), ('path', '.github/workflows/dummy.yml')]:
            api = API()
            api.run[field] = value
            self.assertFalse(verify.observe(api, self.result))

    def test_failed_skipped_neutral_jobs_are_rejected(self):
        for result in ('failure', 'skipped', 'neutral', 'cancelled'):
            api = API()
            api.jobs[0]['conclusion'] = result
            with self.assertRaises(ValueError):
                verify.observe(api, self.result)

    def test_missing_aggregate_and_changed_head_are_rejected(self):
        self.api.jobs[0]['name'] = 'dummy-success'
        with self.assertRaises(ValueError):
            verify.observe(self.api, self.result)
        self.api.head = 'b'*40
        with self.assertRaises(ValueError):
            verify.observe(self.api, self.result)

    def test_running_waits_and_failed_run_fails(self):
        self.api.run['status'] = 'in_progress'
        self.assertFalse(verify.observe(self.api, self.result))
        self.api.run['status'] = 'completed'
        self.api.run['conclusion'] = 'failure'
        with self.assertRaises(ValueError):
            verify.observe(self.api, self.result)


if __name__ == '__main__':
    unittest.main()
