#!/usr/bin/env python3
"""Pinned secret scanning; repository-controlled suppressions are not loaded."""
import argparse
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

VERSION = '8.30.1'
_spec = importlib.util.spec_from_file_location('gitleaks_install', Path(__file__).with_name('gitleaks_install.py'))
installer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(installer)


def scan(repository, staged=False):
    env = {k: v for k, v in os.environ.items() if not k.startswith('GITLEAKS_')}
    try:
        binary = str(installer.ensure())
        result = subprocess.run([binary, 'version'], env=env, capture_output=True, text=True, check=True)
        if result.stdout.strip().removeprefix('v') != VERSION:
            raise ValueError('Cached Gitleaks reported an unexpected version; expected ' + VERSION)
        args = [binary, 'git', '--config', str(Path(__file__).with_name('gitleaks.toml')),
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
    parser.add_argument('--install-only', action='store_true', help='Verify/download the pinned scanner without scanning')
    parser.add_argument('repository', nargs='?', default='.')
    args = parser.parse_args()
    if args.install_only:
        try:
            installer.ensure()
            print('Pinned Gitleaks is ready in the user cache.')
            return 0
        except (OSError, ValueError) as error:
            print('Secret scanner setup failed: ' + str(error), file=sys.stderr)
            return 2
    return scan(args.repository, args.staged)


if __name__ == '__main__':
    sys.exit(main())
