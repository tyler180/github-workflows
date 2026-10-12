"""Restricted live create-stage authorizer. Later onboarding stages are rejected."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import importlib.util
_spec = importlib.util.spec_from_file_location("policy", Path(__file__).with_name("policy.py"))
policy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(policy)

BUCKET = '749rmw-tf-backends'
KEY = 'github/github-infra/onboarding/accepted.json'
OWNER = 'tyler180'


def command(args, env=None):
    result = subprocess.run(args, capture_output=True, env=env)
    policy.require(result.returncode == 0, 'Trusted service operation failed')
    return result.stdout


def read_record(directory):
    target = directory / 'accepted.json'
    result = subprocess.run(['aws', 's3api', 'get-object', '--bucket', BUCKET,
                             '--key', KEY, str(target)], capture_output=True)
    if result.returncode:
        policy.require(b'NoSuchKey' in result.stderr, 'Cannot read accepted record')
        return None, None
    metadata = json.loads(result.stdout)
    return policy.load_json(target), metadata['ETag']


def validate_scope(inventory):
    repos = policy.inventories(inventory)
    policy.require(bool(repos), 'Empty live inventory')
    for repo in repos.values():
        policy.require(repo['stage'] == 'create', 'Live adapter supports create stage only')
        policy.require(not repo.get('provisioning_teams') and not repo.get('developer_teams'), 'Personal pilot cannot grant teams')
        policy.require(repo.get('actions_policy') is None and repo.get('workflow_permissions') is None,
                       'Optional Actions policies require later live acceptance')
    return repos


def verify_environment():
    env = json.loads(command(['gh', 'api', 'repos/tyler180/github-infra/environments/production']))
    rules = json.loads(command(['gh', 'api', 'repos/tyler180/github-infra/environments/production/deployment-branch-policies']))
    policy.require(env.get('can_admins_bypass') is False, 'Administrator bypass is enabled')
    reviews = [r for r in env.get('protection_rules', []) if r['type'] == 'required_reviewers']
    policy.require(len(reviews) == 1 and any(r['reviewer']['id'] == 15354990 for r in reviews[0]['reviewers']), 'Required pilot reviewer missing')
    policy.require([(r['name'], r['type']) for r in rules['branch_policies']] == [('main', 'branch')], 'Production is not main-only')


def validate_inputs(candidate, plan, record):
    repos = validate_scope(candidate)
    if record is None:
        base = {'schema_version': 1, 'repositories': {}}
        record = {'schema_version': 1, 'configuration_sha': '0'*40,
                  'inventory_digest': policy.digest(policy.canonical(base)), 'repositories': {}}
    else:
        base = record['inventory']
        validate_scope(base)
    for change in plan.get('resource_changes', []):
        if change.get('type') == 'github_repository':
            key = policy.repo_key(change)
            policy.require(key in base['repositories'] or change['change']['actions'] == ['create'], 'Unrecorded repository cannot be adopted')
    policy.evaluate(base, record, candidate, {'schema_version': 1, 'repositories': {}}, plan, datetime.now(timezone.utc))
    # No candidate provisioners or executable external data are accepted.
    def inspect(value):
        if isinstance(value, dict):
            policy.require(not value.get('provisioners'), 'Provisioners are prohibited')
            for child in value.values():
                inspect(child)
        elif isinstance(value, list):
            for child in value:
                inspect(child)
    inspect(plan.get('configuration', {}))
    return repos


def verify_repositories(repos):
    accepted = {}
    for key, repo in repos.items():
        actual = json.loads(command(['gh', 'api', f'repos/{OWNER}/{repo["name"]}']))
        expected = {
            'name': repo['name'], 'visibility': repo.get('visibility', 'private'),
            'description': repo.get('description', ''), 'homepage': repo.get('homepage_url', ''),
            'archived': False, 'full_name': f'{OWNER}/{repo["name"]}',
            'private': repo.get('visibility', 'private') == 'private', 'fork': False, 'delete_branch_on_merge': repo.get('delete_branch_on_merge', True),
            'allow_auto_merge': repo.get('allow_auto_merge', False),
            'allow_update_branch': repo.get('allow_update_branch', True),
        }
        methods = repo.get('merge_methods', ['squash'])
        expected.update({f'allow_{m}_merge' if m != 'merge' else 'allow_merge_commit': m in methods for m in ('merge', 'squash', 'rebase')})
        features = repo.get('features', {})
        expected.update({'has_'+name: features.get(name, default) for name, default in [('issues', True), ('discussions', False), ('wiki', False), ('projects', False)]})
        mismatches = [k for k,v in expected.items() if (actual.get(k) or '' if k in ('description','homepage') else actual.get(k)) != v]
        policy.require(not mismatches, 'Post-apply settings differ or unavailable: ' + ', '.join(mismatches))
        policy.require(actual['owner']['id'] == 15354990 and actual['default_branch'] == repo.get('default_branch', 'main'), 'Post-apply owner/branch differs')
        policy.require(sorted(actual.get('topics', [])) == sorted(repo.get('topics', [])), 'Post-apply topics differ')
        accepted[key] = {'name': repo['name'], 'stage': 'create', 'repository_id': actual['id'], 'tooling_digest': policy.tooling_digest(repo)}
    return accepted


def main():
    p = argparse.ArgumentParser()
    p.add_argument('mode', choices=['authorize', 'recheck', 'accept'])
    p.add_argument('--configuration', required=True)
    p.add_argument('--sha', required=True)
    p.add_argument('--policy-sha', required=True)
    p.add_argument('--policy-version', required=True)
    p.add_argument('--plan', required=True)
    p.add_argument('--plan-json', required=True)
    p.add_argument('--receipt', required=True)
    args = p.parse_args()
    phase = 'configuration freshness'
    try:
        policy.require(policy.SHA.fullmatch(args.sha) and policy.SHA.fullmatch(args.policy_sha), 'Full configuration revision required')
        latest = command(['gh','api','repos/tyler180/github-infra/commits/main','--jq','.sha']).decode().strip()
        policy.require(latest == args.sha, 'Configuration superseded')
        phase = 'production protection checks'
        verify_environment()
        config = Path(args.configuration).resolve()
        candidate = policy.load_json(config/'repositories.json')
        plan = policy.load_json(args.plan_json)
        receipt_path = Path(args.receipt)
        phase = 'accepted-record read'
        record, etag = read_record(receipt_path.parent)
        phase = 'plan policy'
        repos = validate_inputs(candidate, plan, record)
        binding = {'policy_sha':args.policy_sha, 'policy_version':args.policy_version, 'configuration_sha': args.sha, 'configuration_digest': policy.tree_digest(config),
                   'saved_plan_digest': policy.digest(Path(args.plan).read_bytes()),
                   'plan_digest': policy.digest(policy.canonical(plan)), 'record_etag': etag}
        if args.mode == 'authorize':
            binding.update(mode='live-create', evaluated_at=datetime.now(timezone.utc).isoformat())
            receipt_path.write_text(json.dumps(binding))
        else:
            phase = 'authorization binding'
            receipt = policy.load_json(receipt_path)
            policy.require(receipt.get('mode') == 'live-create' and all(receipt.get(k) == v for k,v in binding.items()), 'Authorization binding changed')
            age = (datetime.now(timezone.utc)-datetime.fromisoformat(receipt['evaluated_at'])).total_seconds()
            policy.require(0 <= age <= 3600, 'Authorization expired')
            if args.mode == 'recheck':
                print('Authorization remains current.')
                return 0
            phase = 'post-apply GitHub verification'
            entries = verify_repositories(repos)
            if record:
                policy.require(all(entries[k]['repository_id'] == v['repository_id'] for k,v in record['repositories'].items()), 'Accepted repository identity changed')
            next_record = {'schema_version':1, 'configuration_sha':args.sha, 'inventory':candidate,
                           'inventory_digest':policy.digest(policy.canonical(candidate)), 'repositories':entries,
                           'accepted_at':datetime.now(timezone.utc).isoformat(), 'authorization':receipt}
            output = receipt_path.parent/'next-record.json'
            output.write_text(json.dumps(next_record))
            phase = 'conditional accepted-record write'
            command(['aws','s3api','put-object','--bucket',BUCKET,'--key',KEY,'--body',str(output),
                     *(['--if-match',etag] if etag else ['--if-none-match','*'])])
        print('Live create-stage '+args.mode+' succeeded.')
        return 0
    except (policy.PolicyError, KeyError, ValueError, OSError, TypeError) as error:
        reason = str(error) if isinstance(error, policy.PolicyError) else type(error).__name__
        print(f'Live create-stage validation failed at {phase}: {reason}; accepted record not advanced.', file=sys.stderr)
        return 1

if __name__ == '__main__':
    sys.exit(main())
