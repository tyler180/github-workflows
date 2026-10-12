"""Resolve full SHAs or published immutable semantic-version releases."""
import argparse
import json
from pathlib import Path
import re
import subprocess

SHA = re.compile(r'[0-9a-f]{40}')
VERSION = re.compile(r'v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:-[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*)?')
REPO = 'tyler180/github-workflows'


def run(args):
    return subprocess.check_output(args, stderr=subprocess.DEVNULL).decode().strip()


def resolve(ref, checkout):
    if not (SHA.fullmatch(ref) or VERSION.fullmatch(ref)):
        raise ValueError('Use a full SHA or an exact version release, not a branch or floating tag')
    actual = run(['git', '-C', str(checkout), 'rev-parse', 'HEAD'])
    if not SHA.fullmatch(actual):
        raise ValueError('Invalid checkout revision')
    if SHA.fullmatch(ref):
        if ref != actual:
            raise ValueError('Checkout does not match requested revision')
        return actual
    release = json.loads(run(['gh', 'api', f'repos/{REPO}/releases/tags/{ref}']))
    if release.get('draft') is not False or release.get('immutable') is not True or release.get('tag_name') != ref:
        raise ValueError('Version must be a published immutable release')
    obj = json.loads(run(['gh', 'api', f'repos/{REPO}/git/ref/tags/{ref}']))['object']
    for _ in range(4):
        if obj['type'] != 'tag':
            break
        obj = json.loads(run(['gh', 'api', f'repos/{REPO}/git/tags/{obj["sha"]}']))['object']
    if obj.get('type') != 'commit' or obj.get('sha') != actual:
        raise ValueError('Release tag does not match checked-out policy')
    return actual


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--ref', required=True)
    p.add_argument('--checkout', required=True)
    p.add_argument('--output', required=True)
    args = p.parse_args()
    sha = resolve(args.ref, args.checkout)
    with Path(args.output).open('a') as output:
        output.write(f'sha={sha}\nversion={args.ref}\n')
    print('Trusted policy version resolved to its verified commit.')

if __name__ == '__main__':
    main()
