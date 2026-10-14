"""Open inventory-selected hook update PRs without reinitializing repositories."""
import argparse
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import urllib.parse
import yaml
spec = importlib.util.spec_from_file_location("bootstrap", Path(__file__).with_name("bootstrap.py"))
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)

FILE = '.pre-commit-config.yaml'
SHARED = 'https://github.com/tyler180/github-workflows'
APPROVED_REF = 'v2.3.0-rc.5'
APPROVED_SHA = '9bf2bc8f3d14123ea82d5be3e176760517652ca1'


def selections(inventory, owner):
    b.require(inventory.get('schema_version') == 1, 'Unsupported inventory schema')
    b.require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]*', owner), 'Invalid owner')
    result = []
    for key, repo in inventory['repositories'].items():
        config = repo.get('developer_hooks')
        if config is None:
            continue
        b.require(repo.get('stage') in ('create', 'bootstrap', 'verify', 'protect', 'grant'), 'Adoption must be accepted before hook updates')
        b.require(re.fullmatch(r'[A-Za-z0-9_-]+', key), 'Invalid inventory key')
        b.require(re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', repo.get('name', '')) and repo['name'] not in ('.', '..'), 'Invalid repository name')
        b.require(isinstance(config, dict) and set(config) == {'hook_ref', 'ai_review'} and config['hook_ref'] == APPROVED_REF and type(config['ai_review']) is bool, 'Use approved hook release and a boolean ai_review')
        result.append((key, repo))
    return result


def transform(data, config):
    b.require(data is None or len(data) <= 200_000, 'Hook configuration exceeds size limit')
    document = yaml.safe_load(data) if data is not None else {'repos': []}
    b.require(isinstance(document, dict) and isinstance(document.get('repos'), list), 'Invalid pre-commit configuration')
    before = json.dumps(document, sort_keys=True)
    entries = document['repos']
    b.require(all(isinstance(item, dict) and isinstance(item.get('repo'), str) for item in entries), 'Invalid hook repository entries')
    matches = [item for item in entries if item['repo'].removesuffix('.git').rstrip('/') == SHARED]
    b.require(len(matches) <= 1, 'Multiple shared hook entries require manual reconciliation')
    if matches:
        shared = matches[0]
        b.require(isinstance(shared.get('hooks'), list) and all(isinstance(h, dict) and isinstance(h.get('id'), str) for h in shared['hooks']), 'Invalid shared hooks')
    else:
        shared = {'repo': SHARED, 'rev': config['hook_ref'], 'hooks': [{'id': 'repository-hygiene'}, {'id': 'secret-scan'}]}
        entries.append(shared)
    shared['rev'] = config['hook_ref']
    hooks = shared['hooks']
    matches = [h for h in hooks if h['id'] == 'codex-review']
    b.require(len(matches) <= 1, 'Duplicate AI hooks require manual reconciliation')
    if config['ai_review']:
        if not matches:
            matches = [{'id': 'codex-review'}]
            hooks.append(matches[0])
        matches[0].update(stages=['pre-push'], verbose=True)
    else:
        shared['hooks'] = [h for h in hooks if h['id'] != 'codex-review']
    if data is not None and before == json.dumps(document, sort_keys=True):
        return data
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True).encode()


def install(api, owner, key, repo, run_url):
    b.require(b.resolve_reference(api, 'tyler180/github-workflows', APPROVED_REF) == APPROVED_SHA, 'Hook release differs from approved commit')
    prefix = f"repos/{owner}/{repo['name']}/"
    actual = api.call('GET', prefix.rstrip('/'))
    b.require(actual['full_name'].lower() == f"{owner}/{repo['name']}".lower() and not actual['archived'] and not actual.get('fork'), 'Destination identity mismatch')
    branch = actual['default_branch']
    main_ref = prefix + 'git/ref/heads/' + urllib.parse.quote(branch, safe='')
    main = api.call('GET', main_ref)['object']['sha']
    base_tree = b.tree(api, prefix, main)
    b.require(FILE not in base_tree or base_tree[FILE]['mode'] == '100644', 'Hook configuration must be a regular nonexecutable file')
    original = b.content(api, prefix, FILE, main)
    desired = transform(original, repo['developer_hooks'])
    if desired == original:
        return {'repository': actual['full_name'], 'status': 'already-current', 'commit': main}
    fingerprint = hashlib.sha256(b.canonical({'base': main, 'id': actual['id'], 'content': desired.decode()})).hexdigest()[:16]
    update_branch = 'automation/developer-hooks-' + fingerprint
    ref = api.call('GET', prefix + 'git/ref/heads/' + update_branch, missing=True)
    if ref is None:
        blob = api.call('POST', prefix + 'git/blobs', {'content': base64.b64encode(desired).decode(), 'encoding': 'base64'})
        commit = api.call('GET', prefix + 'git/commits/' + main)
        tree = api.call('POST', prefix + 'git/trees', {'base_tree': commit['tree']['sha'], 'tree': [{'path': FILE, 'mode': '100644', 'type': 'blob', 'sha': blob['sha']}]})
        head = api.call('POST', prefix + 'git/commits', {'message': 'Update shared developer hooks', 'parents': [main], 'tree': tree['sha']})['sha']
        b.require(api.call('GET', main_ref)['object']['sha'] == main, 'Default branch changed; retry')
        api.call('POST', prefix + 'git/refs', {'ref': 'refs/heads/' + update_branch, 'sha': head})
    else:
        head = ref['object']['sha']
    # Verify entire branch before opening or reusing a PR, including untouched files.
    tree = b.tree(api, prefix, head)
    b.require(set(tree) == set(base_tree) | {FILE}, 'Hook update branch contains unexpected files')
    b.require(tree[FILE]['sha'] == b.git_blob(desired) and tree[FILE]['mode'] == '100644', 'Hook update branch was modified')
    for path, item in base_tree.items():
        if path != FILE:
            b.require(tree[path]['sha'] == item['sha'] and tree[path]['mode'] == item['mode'], 'Application file was modified on hook branch')
    b.require(api.call('GET', main_ref)['object']['sha'] == main, 'Default branch changed; retry')
    prs = api.call('GET', prefix + 'pulls?state=all&head=' + urllib.parse.quote(owner + ':' + update_branch, safe='') + '&base=' + urllib.parse.quote(branch, safe='') + '&per_page=100')
    b.require(len(prs) < 100 and len(prs) <= 1, 'Hook PR history requires manual review')
    if prs:
        pr = prs[0]
        b.require(pr['state'] == 'open' and pr['head']['sha'] == head, 'Hook PR was closed or changed; reconcile manually')
    else:
        pr = api.call('POST', prefix + 'pulls', {'title': 'Update shared developer hooks', 'head': update_branch, 'base': branch,
            'body': f"Updates only {FILE} from github-infra inventory key {key}. Shared release: {APPROVED_REF} ({APPROVED_SHA}).\n\nPreserves unrelated hooks and application files. YAML formatting/comments may change. Review and merge after CI passes. No automatic merge or local activation.\n\nFor AI review, each developer must approve code submission to their provider, then run `pre-commit install --hook-type pre-push` and `git config --local hooks.aiReview true`. AI review stays advisory.\n\nRun: {run_url}"})
    return {'repository': actual['full_name'], 'status': 'pr-open', 'pr_url': pr['html_url'], 'head_sha': head}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--owner', required=True)
    parser.add_argument('--run-url', required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    selected = selections(json.loads(args.inventory.read_text()), args.owner)
    api = b.API(os.environ.get('GH_TOKEN'))
    results = [install(api, args.owner, key, repo, args.run_url) for key, repo in selected]
    args.report.write_text(json.dumps(results, indent=2) + '\n')


if __name__ == '__main__':
    main()
