# Shared developer hooks

Secret scanning uses Gitleaks 8.30.1 from an automatically managed user cache.
There is no Homebrew/global installation or PATH dependency. Both archive and
binary hashes are pinned in gitleaks_install.py; the cached binary is checked
before every execution. macOS/Linux arm64/x86_64 are supported. First use needs
network access; an existing verified cache works offline. Downloads, integrity
errors, symlinked binaries and unsupported systems fail closed. The scanner
continues to ignore caller suppressions and environment overrides and redacts
findings. No scanner flags or central policy are weakened.

The manual `secret-scan-setup` hook prepares the binary without scanning. New Go
starters call it from `make setup` after installing pre-commit in a project .venv.
Existing configurations using only `secret-scan` still work after their reviewed
hook pin update; the scanner prepares itself on first run.

Default cache: ~/Library/Caches/github-workflows on macOS; $XDG_CACHE_HOME or
~/.cache, under github-workflows, on Linux. PLATFORM_TOOL_CACHE can select a
different user cache. Every path is qualified by version and platform. To recover
from cache corruption, remove only the affected version/platform directory and
retry. Never execute a binary whose checksum fails. Parallel setup uses a lock and
publishes only a fully verified executable.

Upgrade procedure: obtain publisher release checksums, verify supported archives,
derive binary hashes only from those verified archives, update the version/hashes,
run integrity tests and macOS/Linux live install/scan checks, then update consumers
through reviewed immutable pins. Do not fetch a checksum dynamically at hook time.
