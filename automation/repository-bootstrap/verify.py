"""Verify bootstrap CI using Actions read access; no check-rollup permission needed."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import time

spec = importlib.util.spec_from_file_location("bootstrap", Path(__file__).with_name("bootstrap.py"))
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


def observe(api, result):
    repo = result['repository']
    sha = result['head_sha']
    pr_number = result['pr_url'].rsplit('/', 1)[1]
    bootstrap.require(pr_number.isdigit(), 'Invalid bootstrap PR number')
    pr = api.call('GET', f'repos/{repo}/pulls/{pr_number}')
    bootstrap.require(pr['head']['sha'] == sha, 'Bootstrap PR changed during validation')
    response = api.call('GET', f'repos/{repo}/actions/runs?head_sha={sha}&per_page=100')
    bootstrap.require(response['total_count'] < 100, 'Too many candidate workflow runs; review manually')
    runs = [run for run in response['workflow_runs'] if run['event'] == 'pull_request'
        and run['head_sha'] == sha and run['path'].split('@')[0] == '.github/workflows/ci.yml']
    if not runs:
        return False
    run = max(runs, key=lambda item: item['id'])
    if run['status'] != 'completed':
        return False
    bootstrap.require(run['conclusion'] == 'success', 'Bootstrap PR CI failed: ' + run['html_url'])
    jobs = api.call('GET', f"repos/{repo}/actions/runs/{run['id']}/jobs?filter=latest&per_page=100")
    bootstrap.require(jobs['total_count'] < 100 and bool(jobs['jobs']), 'Incomplete CI job observation')
    bootstrap.require(any(job['name'] == 'validation / platform-ci' for job in jobs['jobs']), 'Missing required Go aggregate job')
    bootstrap.require(all(job['status'] == 'completed' and job['conclusion'] == 'success' for job in jobs['jobs']), 'A bootstrap CI job failed, skipped or was neutral')
    bootstrap.require(api.call('GET', f'repos/{repo}/pulls/{pr_number}')['head']['sha'] == sha, 'PR head changed after CI observation')
    print('Verified bootstrap PR Actions run: ' + run['html_url'])
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--timeout-seconds', type=int, default=600)
    args = parser.parse_args()
    api = bootstrap.API(os.environ.get('GH_TOKEN'))
    for result in json.loads(args.report.read_text()):
        if result['status'] != 'pr-open':
            continue
        deadline = time.monotonic() + args.timeout_seconds
        while not observe(api, result):
            bootstrap.require(time.monotonic() < deadline, 'Timed out waiting for bootstrap PR Actions run')
            time.sleep(10)


if __name__ == '__main__':
    try:
        main()
    except ValueError as error:
        raise SystemExit('Bootstrap verification failed: ' + str(error))
