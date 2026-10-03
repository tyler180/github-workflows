# Reusable release and GitOps promotion

`tyler180/github-workflows` hosts `.github/workflows/reusable-release.yaml`. Any app repository can call it to publish a GHCR image and propose that app in `tyler180/talos-gitops`. The workflow expects the existing layout: `applications/<app>/`, `infrastructure/applications/<app>.yaml`, and `infrastructure/kustomization.yaml`.

The Jobtracker caller runs its Go checks before publishing. This repository tests the shared promotion logic separately. Release tags must be existing `vMAJOR.MINOR.PATCH` tags whose commits are in the source repository's default-branch history. Releases are intentional; no automatic version increment or cluster sync is configured.

## One-time GitHub setup

1. Create a GitHub App under your account's Developer settings. Give it **Repository contents: Read and write** and **Pull requests: Read and write**. Disable its webhook if you do not need one. This automation does not need cluster, Argo, or Talos credentials.
2. Install it on **only `tyler180/talos-gitops`**. Generate a private key and store it directly in Jobtracker's Actions secret **`GITOPS_APP_PRIVATE_KEY`**. Never commit the key.
3. Set Jobtracker's Actions variable **`GITOPS_APP_ID`** to the App ID. This is the App ID, not the installation ID.
4. Commit and push the reusable workflow, helper, fixtures, and tests in `github-workflows`. After validation passes, publish its `v1` tag. Then commit and push Jobtracker's caller and documentation to its default branch and ensure CI passes.
5. Confirm the source repository permits Actions to publish packages. The caller requests `packages: write`; source checkout and image publication use the repository's `GITHUB_TOKEN`. The GitHub App token is used only for GitOps checkout and PR creation.

## First release

From the tested default-branch commit that includes these workflows:

```sh
git tag -a v0.1.0 -m 'Release Jobtracker v0.1.0'
git push origin v0.1.0
```

Follow **Release Jobtracker** in GitHub Actions. It builds `linux/amd64`, publishes `ghcr.io/tyler180/jobtracker:v0.1.0` with SBOM/provenance, and pins the GitOps deployment to the returned digest.

GHCR packages can start private even when the source repository is public. After the first publish, open the package settings and make Jobtracker's container package **public** before syncing, or configure image-pull credentials separately. The workflow does not change package visibility. Do not sync until an anonymous image pull succeeds or registry credentials are ready.

The proposed PR uses branch `automation/jobtracker` and includes:

- `applications/jobtracker/`: copied YAML from the release's `deploy/` directory, with the image replaced by `version@sha256:digest`.
- `infrastructure/applications/jobtracker.yaml`: the existing Argo layout, using manual sync and `CreateNamespace=true`.
- `infrastructure/kustomization.yaml`: registers the app without duplicating entries.

It renders the application and public infrastructure configurations before opening the PR. It does not render decrypted live overlays or access the cluster. Review the PR's storage, namespace, resources, and image before merging. Manually sync `root` to register the application, then `jobtracker`, with Prune, Force, and Replace unchecked.

Verify the PVC is bound, the pod is ready, a real posting saves successfully, and the saved posting survives pod replacement. NFS write permissions for UID/GID 65532 and directory sync still need live verification. Use port forwarding initially; authentication and a public route are separate changes.

## Later releases and retries

Create the next intentional semantic tag after CI passes. Existing app manifests remain the source of truth for cluster configuration: the proposal changes the image of the Deployment/container named after the app. It preserves resources, volumes, routes, and environment settings. It refuses a namespace move or mismatched Argo registration. Source manifest changes are copied only for initial onboarding; review later infrastructure changes directly in the GitOps repository.

If a proposal PR is already open, the next release updates the same app branch/PR. Do not make manual changes on the generated branch. GitOps settings should be merged into the base branch separately. Releases for one app are serialized; newer queued releases can supersede pending runs according to GitHub's concurrency behavior.

To retry a published release after fixing permissions, use **Run workflow** on the source repository's default branch, enter the existing tag, and run **Release Jobtracker**. The job may republish that version with a different digest; the PR always uses the digest returned by that run. Do not reuse a release tag for different source code.

## Reuse from another app

Place a Dockerfile and a plain YAML Kustomize directory in the other app repository. The manifests must contain exactly one Deployment with a container named after `app-name`, and the Kustomize namespace must match the caller. Run the other app's own tests before the reusable job.

```yaml
jobs:
  # Define verify to test the exact release-tag commit for your app.
  publish-and-propose:
    needs: verify
    permissions:
      contents: read
      packages: write
    uses: tyler180/github-workflows/.github/workflows/reusable-release.yaml@v1
    with:
      app-name: another-app
      namespace: another-app
      release-tag: ${{ github.ref_name }}
      manifest-directory: deploy
      app-id: ${{ vars.GITOPS_APP_ID }}
    secrets:
      gitops-app-private-key: ${{ secrets.GITOPS_APP_PRIVATE_KEY }}
```

Use a published shared-workflow tag, such as `v1`. This is independent of each application's release version. If the shared repository is private, enable reusable-workflow access for the intended caller repositories in its Actions settings. Configure the App ID and secret in each caller repository, or share them with authorized repositories through organization settings. Optional inputs are `gitops-repository`, `gitops-branch`, and `platforms`. The image name comes from the caller's repository name. Other architectures require a builder capable of building those platforms; the default is the existing cluster's amd64 target.

The workflow embeds the tested promotion helper so callers do not need to check out its hosting repository. When changing `automation/promote.py`, update the embedded copy in the **Prepare GitOps proposal** step; the test suite rejects any mismatch.

## Local validation

Install PyYAML 6.0.3 in a temporary virtual environment, then run:

```sh
python -m unittest discover -s automation/tests -v
actionlint .github/workflows/validate-gitops-release.yaml .github/workflows/reusable-release.yaml
```

Tests cover onboarding, repeated proposals, image updates that preserve cluster settings, namespace/path rejection, and equivalence of the embedded workflow helper. These checks do not prove GitHub App permissions, registry visibility, or live Kubernetes readiness.
