# Reusable release and GitOps promotion

`.github/workflows/reusable-release.yaml` publishes a GHCR container image and opens or updates an app-scoped GitOps PR. App repositories own their tests and Dockerfile; the shared workflow owns image publication, version selection, and promotion. GitOps repositories own merge and Argo sync policy.

The `v1` release remains available for intentional tags and reviewed deployment PRs. `v2.0.0` adds opt-in automatic patch releases, stale-commit protection, and reuse of already-published image digests. It requires `contents: write` in callers for automatic release tags and `packages: write` for GHCR. It never needs cluster credentials or directly syncs Argo.

## Caller configuration

```yaml
jobs:
  publish-and-propose:
    needs: verify
    permissions:
      contents: write
      packages: write
    uses: tyler180/github-workflows/.github/workflows/reusable-release.yaml@v2.0.0
    with:
      app-name: another-app
      namespace: another-app
      release-ref: ${{ github.sha }}
      automatic-patch: true
      app-id: ${{ vars.GITOPS_APP_ID }}
    secrets:
      gitops-app-private-key: ${{ secrets.GITOPS_APP_PRIVATE_KEY }}
```

The caller's `verify` job must test the exact `release-ref` before invoking the shared workflow. For `workflow_run`, accept only successful default-branch push runs from the same repository and pass `github.event.workflow_run.head_sha`. Never publish based on PR or fork results. Manual/tag releases supply `release-tag` and leave `automatic-patch` false; the tag must exist and its commit must belong to default-branch history.

Automatic releases select the numerically highest semantic version and increment its patch, starting at `v0.1.0` if there are no releases. If the tested commit already has a semantic tag, retries reuse it. A queued commit that no longer matches the current default branch is skipped before publication. Releases for each app/GitOps repository are serialized. The workflow records a new source tag only after image publication succeeds. A previously published image tag is reused by digest; registry failures are errors rather than permission to overwrite a release.

The build publishes SBOM and provenance and deploys `ghcr.io/<caller-repo>:vMAJOR.MINOR.PATCH@sha256:<digest>`. The image tag is readable; the pinned digest identifies the exact content. Major and minor versions remain intentional.

## GitHub and GitOps setup

Create a GitHub App installed only on the target GitOps repository with **Contents: Read and write** and **Pull requests: Read and write**. Put its ID in caller variable `GITOPS_APP_ID`, and its private key in `GITOPS_APP_PRIVATE_KEY`. Never commit the key. The app token is scoped to GitOps checkout and PR creation; the caller's repository token handles its source tags and GHCR.

Publish `v2.0.0` from tested shared-workflow default-branch history before using that reference. Private shared repositories must allow the caller to use their workflows. The GHCR package must be public or have configured image-pull credentials.

The workflow expects `applications/<app>/`, `infrastructure/applications/<app>.yaml`, and `infrastructure/kustomization.yaml`. Initial onboarding copies a plain-YAML Kustomize directory (`deploy` by default) containing exactly one Deployment/container named after the app. Later releases update the existing Deployment image and preserve cluster settings, volumes, resources, routes, and Argo sync policy. Namespace changes or conflicting registrations fail. Initial Argo registration stays manual by default; automatic sync is an explicit GitOps configuration choice.

The target GitOps repository decides whether PRs need human review. Jobtracker's GitOps CI automatically merges only same-repository `automation/jobtracker` PRs from the configured bot, after tests/rendering and an image-only advancing-version check loaded from the trusted base commit. Its merge is locked to the validated SHA and honors branch protections. Other applications are unaffected. Jobtracker's Argo Application automatically syncs merged changes without pruning; the root app retains its current policy.

To retry, run the caller on its default branch with the existing release tag, or opt into automatic selection for its latest tested commit. GitOps history supports deliberate rollback through a normal human PR restoring a previous digest. Automatic Jobtracker promotions reject downgrades and infrastructure changes.

## Validation

```sh
python -m unittest discover -s automation/tests -v
actionlint .github/workflows/validate-gitops-release.yaml .github/workflows/reusable-release.yaml
```

Install PyYAML 6.0.3 in a temporary environment. Tests cover patch selection, retry tag reuse, stale-commit rejection, onboarding, configuration preservation, and agreement between tested helpers and embedded workflow code. Keep embedded helpers in sync with `automation/promote.py` and `automation/release_version.py`. These local checks do not prove GitHub App permissions, registry visibility, or live Argo readiness.

## Optional AI review before committing

The `codex-review` hook reviews the staged Git diff during `git commit`. It does
not include unstaged changes and works on new branches and initial commits.
The hook is advisory: findings and outages do not block commits. You can edit
and amend your commit before pushing. A successful review is not test evidence.

Install/authenticate Codex CLI, then add `codex-review` under the pinned shared
hook release in your pre-commit configuration with `stages: [pre-commit]` and
`verbose: true`. In the consuming repository, run:

```sh
pre-commit install --hook-type pre-commit
git config --local hooks.aiReview true
```

Local opt-in persists; disable with `git config --local hooks.aiReview false`.
Reviews consume your account usage and submit the staged diff to your model
provider. Only enable after approving that submission. Run secret scanning
before AI review. CLI 0.162.1 was checked; required options include
`--ignore-user-config`, `--ignore-rules` and `--ephemeral`.

Manual staged review, from the consuming repository:

```sh
python3 /path/to/github-workflows/scripts/codex-review.py --staged
```

Manual committed review remains available with `--base origin/main`. It compares
that commit directly to HEAD and does not fetch. Each review runs outside the
checkout with user configuration/MCP disabled, a read-only sandbox and no
approval escalation. The prompt treats diff text as untrusted. Sandbox read
access is not a guarantee of diff-only access. Ephemeral mode avoids saved CLI
sessions, not provider retention. The configured CLI default model is used;
manual runs accept `--model`.

Diffs over 200 KB are skipped in full. Reviews time out after 180 seconds;
manual `--timeout` accepts 1 to 600. Operational failures return 2 manually and
success in hook mode with an unavailable-review message. Tests, linting and
secret scanning remain independent. See the
[official non-interactive documentation](https://developers.openai.com/codex/noninteractive).

## Managed repository hook updates

The reusable `repository-developer-hooks.yml` workflow reads `developer_hooks`
from github-infra inventory and opens reviewed PRs that update only
`.pre-commit-config.yaml`. It works with existing applications and generic
repositories without rerunning bootstrap. Example inventory field:

```json
"developer_hooks": {"hook_ref": "v2.3.0-rc.5", "ai_review": true}
```

The approved immutable hook release resolves to a verified commit. Existing
unrelated hooks and top-level configuration are preserved semantically; YAML
comments/formatting may change. Already-current configurations are untouched.
Retries reuse the exact PR and reject edited branches or closed PRs. Each new
base/config combination gets its own branch; obsolete PRs need manual closure.
Adoption-stage repositories and archived/fork destinations are rejected.

This workflow never merges PRs, alters application code, applies Terraform,
installs local hooks, or approves model access. Developers still explicitly
install pre-push hooks and opt in locally after approving code submission.
Setting `ai_review` false removes just the shared AI hook.
