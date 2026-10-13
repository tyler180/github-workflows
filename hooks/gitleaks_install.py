"""Checksum-pinned, user-local Gitleaks installation; never depend on PATH."""
import fcntl
import hashlib
import os
from pathlib import Path
import platform
import tarfile
import tempfile
import urllib.request

VERSION = '8.30.1'
# Archive hashes from the publisher's v8.30.1 checksums.txt. Binary hashes were
# derived only after verifying those archives; cached binaries are verified too.
ARTIFACTS = {
    ('Darwin', 'arm64'): ('darwin_arm64', 'b40ab0ae55c505963e365f271a8d3846efbc170aa17f2607f13df610a9aeb6a5', 'ba52fb1bfabbcde42f032afad3d6e0b19dff8ed105229a16e7caa338bbc0e84f'),
    ('Darwin', 'x86_64'): ('darwin_x64', 'dfe101a4db2255fc85120ac7f3d25e4342c3c20cf749f2c20a18081af1952709', 'cee01fea7173f1b779dff188e1c26ecbcb4027d394acc573b23aaf0be260e291'),
    ('Linux', 'arm64'): ('linux_arm64', 'e4a487ee7ccd7d3a7f7ec08657610aa3606637dab924210b3aee62570fb4b080', '00e91bbe655bd7c47753e8cfe61cb76ea1a5d7e7702fe161ee40102b46b3823b'),
    ('Linux', 'x86_64'): ('linux_x64', '551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb', '88f91962aa2f93ac6ab281d553b9e125f5197bbbce38f9f2437f7299c32e5509'),
}


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest() if hasattr(hashlib, 'file_digest') else hashlib.sha256(stream.read()).hexdigest()


def artifact():
    machine = platform.machine().lower()
    machine = {'aarch64': 'arm64', 'amd64': 'x86_64'}.get(machine, machine)
    try:
        return ARTIFACTS[(platform.system(), machine)]
    except KeyError:
        raise ValueError('Automatic Gitleaks supports macOS/Linux on arm64/x86_64') from None


def cache_root():
    if os.environ.get('PLATFORM_TOOL_CACHE'):
        return Path(os.environ['PLATFORM_TOOL_CACHE']).expanduser()
    if platform.system() == 'Darwin':
        return Path.home() / 'Library/Caches/github-workflows'
    return Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache'))) / 'github-workflows'


def download(url, destination):
    with urllib.request.urlopen(url, timeout=60) as response:
        data = response.read(32 * 1024 * 1024 + 1)
    if len(data) > 32 * 1024 * 1024:
        raise ValueError('Gitleaks download exceeded the size limit')
    destination.write_bytes(data)


def ensure():
    target, archive_hash, binary_hash = artifact()
    directory = cache_root() / 'gitleaks' / VERSION / target
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    binary = directory / 'gitleaks'
    # Serialize installs across repositories; publish only a fully verified file.
    with (directory / '.install.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if binary.is_symlink():
            raise ValueError('Refusing a symlinked cached Gitleaks binary')
        if binary.exists():
            if not binary.is_file() or digest(binary) != binary_hash:
                raise ValueError('Cached Gitleaks checksum mismatch; remove this version cache and retry')
            if not os.access(binary, os.X_OK):
                raise ValueError('Cached Gitleaks is not executable; remove this version cache and retry')
            return binary
        with tempfile.TemporaryDirectory(dir=directory) as scratch:
            archive = Path(scratch) / 'gitleaks.tar.gz'
            download(f'https://github.com/gitleaks/gitleaks/releases/download/v{VERSION}/gitleaks_{VERSION}_{target}.tar.gz', archive)
            if digest(archive) != archive_hash:
                raise ValueError('Downloaded Gitleaks archive checksum mismatch')
            with tarfile.open(archive, 'r:gz') as package:
                member = package.getmember('gitleaks')
                if not member.isfile() or member.size > 64 * 1024 * 1024:
                    raise ValueError('Invalid Gitleaks archive member')
                # Read the one named regular file; never extract archive paths.
                temporary_binary = Path(scratch) / 'gitleaks'
                with package.extractfile(member) as stream:
                    temporary_binary.write_bytes(stream.read())
            if digest(temporary_binary) != binary_hash:
                raise ValueError('Downloaded Gitleaks binary checksum mismatch')
            temporary_binary.chmod(0o700)
            os.replace(temporary_binary, binary)
        return binary
