# Repository profile bootstrap

`repository-bootstrap.yml` is called after successful repository creation/acceptance,
or by the consuming root's manual resume workflow for already created repositories.
It opens reviewed source PRs; it never merges or changes rulesets/access/state.

Only `profile: "go"` with basic/private/main initialization is supported. No profile
means no source automation. The inventory must explicitly supply source, workflow
and hook SHAs, Go patch version, module path and authorized code owner. This version
approves github-module `6ec3d0adb8de698830e5a40f5bdb1f7cf6372d32` and shared tooling
`0ecbb0fcaea1deb8a8617a51d719b821500e873f`. Updates need a reviewed automation version.

The production environment supplies a separate `TF_GITHUB_BOOTSTRAP_TOKEN` with
Contents, Workflows and Pull requests write; Administration and Actions read. The workflow
uses only main-branch manually dispatched infrastructure configuration, has no
OIDC authority, and never executes destination code with that token. The existing
Go CI runs without bootstrap credentials. The Actions API verifies the exact PR head, eligible PR event, CI workflow path, and successful Go aggregate/all jobs. CI must pass on the observed PR head;
missing/skipped/neutral results are not bootstrap success. Reports do not attest
that branch protection or grants are enabled.

An initial repository must contain only the exact GitHub-generated README. The
bootstrap records repository ID, configuration/content fingerprint, source pins,
base commit and originating run in `.platform-bootstrap.json`. It preserves
executable file modes, reuses an unchanged open PR, and recovers when branch
creation succeeded but PR creation failed. It never force-pushes, overwrites a
custom README/application, reopens a rejected PR, or rewrites a modified bootstrap
branch. Concurrent jobs are serialized. Partial blob/tree/commit creation without
a published ref can leave unreachable Git objects; retry is safe.

After the marker reaches main, retries preserve developer edits. Marker removal,
changed profile pins, a changed base during an open bootstrap, or modified bootstrap
files require human review; later upgrades use a separate PR workflow. A failed
CI run leaves the PR open for diagnosis. A successful job leaves the PR open for
review and merge. Onboarding stays at create until live stage authorization exists.

Developers install pre-commit and Gitleaks 8.30.1 and run `pre-commit install`.
The generated configuration adds the shared hygiene/secret hooks and a local Go
format/vet/race validation hook. GitHub cannot install hooks into developer clones.

Run `python3 -m unittest discover -s automation/repository-bootstrap -p 'test_*.py'`.

## Human-readable versions

Use `bootstrap.source_ref`, `workflow_ref`, and `hook_ref` with an approved full
SHA or exact immutable published semantic-version release (including prereleases).
Legacy `_sha` fields remain supported for full SHAs only. Mixed alias sets fail.
Branches, floating major tags, mutable/draft releases, missing releases and tags
resolving outside the automation's approved commit set fail before rendering or
remote writes. Annotated tags are resolved with a bounded chain. Referencing a
release does not approve new executable sources: promoting a new commit still
requires a reviewed automation update.

Rendering uses resolved SHAs so generated source and the bootstrap fingerprint do
not change when an equivalent release label replaces a SHA. New bootstrap markers
and every run report record both requested refs and resolved source/workflow/hook
SHAs. Existing markers/application files are not rewritten solely to add labels.
The approved starter commit currently has no release; keep its SHA until a reviewed
immutable release exists. The existing workflow/hook release v2.2.0-rc.1 is verified
at runtime. Publishing releases is separate from validating/bootstrap execution.
