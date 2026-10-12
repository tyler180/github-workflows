#!/usr/bin/env python3
"""Pinned secret scanning; repository-controlled suppressions are not loaded."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

VERSION = '8.30.1'


def scan(repository, staged=False):
    env = {k: v for k, v in os.environ.items() if not k.startswith('GITLEAKS_')}
    try:
        result = subprocess.run(['gitleaks', 'version'], env=env, capture_output=True, text=True, check=True)
        if result.stdout.strip().removeprefix('v') != VERSION:
            raise ValueError('Install the documented pinned Gitleaks version ' + VERSION)
        args = ['gitleaks', 'git', '--config', str(Path(__file__).with_name('gitleaks.toml')),
                '--gitleaks-ignore-path', os.devnull, '--ignore-gitleaks-allow',
                '--redact', '--no-banner', '--no-color', '--exit-code', '1']
        if staged:
            args += ['--pre-commit', '--staged']
        else:
            args += ['--log-opts=--all']
        return subprocess.run([*args, str(repository)], env=env, check=False).returncode
    except (OSError, subprocess.CalledProcessError, ValueError) as error:
        print('Secret scanner failed: ' + str(error), file=sys.stderr)
        return 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--staged', action='store_true')
    parser.add_argument('repository', nargs='?', default='.')
    args = parser.parse_args()
    return scan(args.repository, args.staged)


if __name__ == '__main__':
    sys.exit(main())
