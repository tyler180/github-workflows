"""Select a stable patch release for a tested default-branch commit."""
import re
import subprocess

PATTERN = re.compile(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def select_version(tags, exact_tags):
    versions = {tag: tuple(map(int, PATTERN.fullmatch(tag).groups()))
                for tag in tags if PATTERN.fullmatch(tag)}
    exact = [tag for tag in exact_tags if tag in versions]
    if exact:
        return max(exact, key=versions.get), False
    major, minor, patch = max(versions.values(), default=(0, 1, -1))
    return f"v{major}.{minor}.{patch + 1}", True


def resolve(tag, automatic, default_branch):
    sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    default_sha = subprocess.check_output(
        ['git', 'rev-parse', f'refs/remotes/origin/{default_branch}'], text=True).strip()
    if automatic:
        if sha != default_sha:
            return {'skip': 'true'}
        tags = subprocess.check_output(['git', 'tag', '--list'], text=True).splitlines()
        exact = subprocess.check_output(['git', 'tag', '--points-at', 'HEAD'], text=True).splitlines()
        tag, create = select_version(tags, exact)
    else:
        if not PATTERN.fullmatch(tag):
            raise ValueError('Use an existing vMAJOR.MINOR.PATCH release tag')
        tagged_sha = subprocess.check_output(
            ['git', 'rev-parse', f'refs/tags/{tag}^{{commit}}'], text=True).strip()
        if tagged_sha != sha:
            raise ValueError('Checked-out source does not match the release tag')
        subprocess.run(['git', 'merge-base', '--is-ancestor', 'HEAD',
                        f'refs/remotes/origin/{default_branch}'], check=True)
        create = False
    return {'skip': 'false', 'tag': tag, 'sha': sha, 'create-tag': str(create).lower()}
