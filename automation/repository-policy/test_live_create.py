"""Live adapter unit tests use synthetic records and no credentials."""
import copy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
from test_policy import fixture

spec = importlib.util.spec_from_file_location('live', Path(__file__).with_name('live_create.py'))
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)

class LiveTests(unittest.TestCase):
    def create_inputs(self):
        args = fixture('create', 'create')
        candidate, plan = args[2], args[-1]
        repo = candidate['repositories']['pilot']
        repo['provisioning_teams'] = {}; repo['developer_teams'] = {}
        allowed = ('terraform_data', 'github_repository')
        plan['resource_changes'][:] = [c for c in plan['resource_changes'] if c['type'] in allowed]
        resources = plan['planned_values']['root_module']['child_modules'][0]['resources']
        resources[:] = [r for r in resources if r['type'] == 'github_repository']
        for c in plan['resource_changes']:
            c['change']['actions'] = ['create']; c['change']['before'] = None
        return candidate, plan

    def test_first_create_is_accepted_without_record(self):
        candidate, plan = self.create_inputs()
        live.validate_inputs(candidate, plan, None)

    def test_real_provider_create_computed_fields_are_allowed(self):
        candidate, plan = self.create_inputs()
        change = next(c for c in plan['resource_changes'] if c['type']=='github_repository')
        change['change']['after_unknown'] = {k:True for k in
            ('full_name','private','fork','source_owner','source_repo','web_commit_signoff_required')}
        live.validate_inputs(candidate, plan, None)

    def test_unknown_configured_visibility_still_rejected(self):
        candidate, plan = self.create_inputs()
        change = next(c for c in plan['resource_changes'] if c['type']=='github_repository')
        change['change']['after_unknown'] = {'visibility':True}
        with self.assertRaisesRegex(live.policy.PolicyError, 'Unknown critical'):
            live.validate_inputs(candidate, plan, None)

    def test_known_fork_creation_still_rejected(self):
        candidate, plan = self.create_inputs()
        change = next(c for c in plan['resource_changes'] if c['type']=='github_repository')
        change['change']['after']['fork'] = 'true'
        with self.assertRaisesRegex(live.policy.PolicyError, 'Fork creation'):
            live.validate_inputs(candidate, plan, None)

    def test_implicit_adoption_is_rejected(self):
        candidate, plan = self.create_inputs()
        next(c for c in plan['resource_changes'] if c['type']=='github_repository')['change']['actions'] = ['no-op']
        with self.assertRaisesRegex(live.policy.PolicyError, 'cannot be adopted'):
            live.validate_inputs(candidate, plan, None)

    def test_provisioner_is_rejected(self):
        candidate, plan = self.create_inputs()
        plan['configuration'] = {'root_module':{'resources':[{'provisioners':[{'type':'local-exec'}]}]}}
        with self.assertRaisesRegex(live.policy.PolicyError, 'Provisioners'):
            live.validate_inputs(candidate, plan, None)

    def test_record_missing_is_distinct_from_access_denied(self):
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            for error, allowed in ((b'NoSuchKey',True),(b'AccessDenied',False)):
                with patch.object(live.subprocess, 'run', return_value=subprocess.CompletedProcess([],1,b'',error)):
                    if allowed: self.assertEqual(live.read_record(Path(directory)),(None,None))
                    else:
                        with self.assertRaises(live.policy.PolicyError): live.read_record(Path(directory))

    def test_changed_plan_blocks_record_advancement(self):
        import json
        import sys
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); config=root/'config';config.mkdir()
            candidate, plan=self.create_inputs()
            (config/'repositories.json').write_text(json.dumps(candidate))
            (root/'plan.json').write_text(json.dumps(plan));(root/'saved').write_bytes(b'original')
            argv=['live_create.py','authorize','--configuration',str(config),'--sha','a'*40,
                  '--policy-sha','b'*40,'--policy-version','v2.1.0-rc.2','--plan',str(root/'saved'),'--plan-json',str(root/'plan.json'),'--receipt',str(root/'receipt.json')]
            with patch.object(sys,'argv',argv), patch.object(live,'verify_environment'), patch.object(live,'read_record',return_value=(None,None)), patch.object(live,'command',return_value=('a'*40).encode()):
                self.assertEqual(live.main(),0)
            (root/'saved').write_bytes(b'changed')
            argv[1]='accept'
            with patch.object(sys,'argv',argv), patch.object(live,'verify_environment'), patch.object(live,'read_record',return_value=(None,None)), patch.object(live,'command',return_value=('a'*40).encode()) as commands, patch.object(live,'verify_repositories') as verify:
                self.assertEqual(live.main(),1)
                verify.assert_not_called()
                self.assertFalse(any(call.args[0][0]=='aws' for call in commands.call_args_list))

    def test_missing_api_settings_fail_with_field_names(self):
        import json
        candidate,plan=self.create_inputs()
        actual=next(c for c in plan['resource_changes'] if c['type']=='github_repository')['change']['after'].copy()
        actual.update(id=123,owner={'id':15354990},full_name='tyler180/offline-fixture',private=True,fork=False,default_branch='main',homepage='')
        with patch.object(live,'command',return_value=json.dumps(actual).encode()):
            self.assertEqual(live.verify_repositories(candidate['repositories'])['pilot']['repository_id'],123)
        del actual['allow_update_branch']
        with patch.object(live,'command',return_value=json.dumps(actual).encode()):
            with self.assertRaisesRegex(live.policy.PolicyError,'allow_update_branch'):
                live.verify_repositories(candidate['repositories'])

    def test_later_stages_are_blocked(self):
        for stage in ('bootstrap', 'verify', 'protect', 'grant'):
            candidate = fixture('create', stage)[2]
            with self.assertRaises(live.policy.PolicyError):
                live.validate_scope(candidate)

    def test_team_grants_are_blocked(self):
        with self.assertRaises(live.policy.PolicyError):
            live.validate_scope(fixture('create', 'create')[2])

    def test_optional_actions_policy_is_blocked(self):
        candidate = fixture('create', 'create')[2]
        repo = candidate['repositories']['pilot']
        repo['provisioning_teams'] = {}; repo['developer_teams'] = {}
        repo['actions_policy'] = {'enabled': True}
        with self.assertRaises(live.policy.PolicyError):
            live.validate_scope(candidate)

    def test_environment_requires_main_only(self):
        import json
        env = {'can_admins_bypass':False, 'protection_rules':[{'type':'required_reviewers','reviewers':[{'reviewer':{'id':15354990}}]}]}
        rules = {'branch_policies':[{'name':'main','type':'branch'}]}
        with patch.object(live, 'command', side_effect=[json.dumps(env).encode(),json.dumps(rules).encode()]):
            live.verify_environment()
        for change in ('bypass','reviewer','branch'):
            badenv = copy.deepcopy(env); badrules = copy.deepcopy(rules)
            if change == 'bypass': badenv['can_admins_bypass'] = True
            if change == 'reviewer': badenv['protection_rules'] = []
            if change == 'branch': badrules['branch_policies'].append({'name':'dev','type':'branch'})
            with patch.object(live, 'command', side_effect=[json.dumps(badenv).encode(),json.dumps(badrules).encode()]):
                with self.assertRaises(live.policy.PolicyError): live.verify_environment()

    def test_service_failures_are_not_accepted(self):
        import subprocess
        with patch.object(live.subprocess, 'run', return_value=subprocess.CompletedProcess([],1,b'',b'failed')):
            with self.assertRaises(live.policy.PolicyError): live.command(['synthetic'])

if __name__ == '__main__': unittest.main()
