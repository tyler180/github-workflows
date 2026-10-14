import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


def commands():
    result = []
    marker = "      - name: Authenticate Docker with this job's AWS role"
    for name in ('terraform-plan.yml', 'terraform-deploy.yml'):
        source = (ROOT / '.github/workflows' / name).read_text()
        for block in source.split(marker)[1:]:
            run = block.split('        run: |\n', 1)[1].split('      - ', 1)[0]
            result.append('\n'.join(line[10:] for line in run.splitlines() if line.strip()))
    return result


class ECRJobAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'aws').write_text('#!/bin/bash\nset -euo pipefail\ntest "$*" = "ecr get-login-password --region us-west-2"\nprintf "credential:%s\\n" "$AWS_ROLE"\n')
        (self.root / 'docker').write_text('#!/bin/bash\nset -euo pipefail\ntest "$*" = "--config $HOME/.docker login --username AWS --password-stdin $ECR_REGISTRY"\nread -r credential\ntest "$credential" = "credential:$AWS_ROLE"\nprintf "%s" "$AWS_ROLE" > "$AUTH_OBSERVED"\necho "Login succeeded"\n')
        for name in ('aws', 'docker'):
            (self.root / name).chmod(0o755)
        self.env = {**os.environ, 'PATH': str(self.root) + ':' + os.environ['PATH'],
            'ECR_REGISTRY': '138013422358.dkr.ecr.us-west-2.amazonaws.com',
            'ECR_REGION': 'us-west-2', 'AUTH_OBSERVED': str(self.root / 'observed')}

    def test_each_job_uses_its_current_role_without_printing_credentials(self):
        blocks = commands()
        self.assertEqual(len(blocks), 3)
        for command in blocks:
            for role in ('plan', 'apply'):
                with self.subTest(role=role):
                    result = subprocess.run(['bash', '-c', command], env={**self.env, 'AWS_ROLE': role}, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual((self.root / 'observed').read_text(), role)
                    self.assertNotIn('credential:', result.stdout + result.stderr)

    def test_invalid_registry_fails_before_authentication(self):
        for registry in ('example.com', '138013422358.dkr.ecr.us-west-2.amazonaws.com/repo', 'registry;echo injected'):
            for command in commands():
                with self.subTest(registry=registry):
                    result = subprocess.run(['bash', '-c', command], env={**self.env, 'AWS_ROLE': 'apply', 'ECR_REGISTRY': registry}, capture_output=True, text=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse((self.root / 'observed').exists())


if __name__ == '__main__':
    unittest.main()
