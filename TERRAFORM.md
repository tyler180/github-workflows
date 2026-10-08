# Reusable Terraform workflows

`terraform-plan.yml` runs fmt, init, validate and a preview plan on same-repository pull requests. Fork PRs are skipped; do not use `pull_request_target` to execute submitted Terraform with credentials. Same-repository contributors able to change Terraform must also be trusted with the plan role and provider credentials.

`terraform-deploy.yml` accepts default-branch push or manual runs. It creates a fresh saved plan, archives the complete initialized working directory, then applies that bundle in a separate environment-protected job. No changes means no apply job. The artifact is scoped to the run and planning attempt, retained for one day, and includes provider/module selections. Failed-job reruns reuse the planning job's artifact output. If it expires, rerun the entire workflow to create and approve a fresh plan.

Review the deployment plan in the planning job's logs before approving the environment. Saved plans, bundled configuration and logs can expose sensitive information; restrict repository Actions access appropriately. Terraform sensitive attributes are normally redacted in human-readable output, but arbitrary provider output may still disclose values.

## Caller contract

Call these workflows at job level with `contents: read` and `id-token: write`. Pin `uses` to a reviewed commit SHA. Checkout resolves the caller's repository at its triggering SHA.

Required inputs: `terraform-version`, `plan-role-arn`, `state-id`. Optional inputs: `working-directory` (default `.`), `aws-region` (default `us-west-2`). Deployment also requires `apply-role-arn` and `environment`.

Optional secrets `terraform-github-token` and (deployment only) `apply-terraform-github-token` populate `GITHUB_TOKEN` for the Terraform GitHub provider. Supply appropriate read and write credentials when using that provider; the caller's automatic workflow token is only used for checkout and a branch freshness check.

Configure variables and backend files in the caller's Terraform directory. These workflows deliberately do not accept arbitrary shell commands or unstructured CLI arguments. Add typed file-selection inputs when a caller actually needs them.

## Credentials and approvals

Bootstrap GitHub OIDC and the IAM roles separately before enabling a caller. Use a read-oriented plan role and a resource-scoped apply role. Even the plan role needs backend state reads and S3 lock-file create/read/delete access. The apply role also needs state writes. Add KMS access if the backend uses a customer-managed key.

IAM trusts must match the caller repository's actual OIDC subject, audience `sts.amazonaws.com`, and allowed trigger. The plan job has no environment, so it needs PR and default-branch subjects. The apply job uses an environment subject. New repositories may include immutable owner/repository IDs: do not copy an old trust string without checking the actual format. Avoid a repository-wide subject wildcard.

Create the deployment environment in each caller repo, restrict it to its default branch and configure required reviewers where supported. The workflow does not create approval rules: an unprotected environment will apply immediately. Check the GitHub plan supports the required protection for your repository visibility. Keep automatic deployment disabled until this is verified. For a solo account with self-approval prevented, another reviewer is required.

## State and concurrency

Use a remote backend with locking. Deployment runs serialize per `state-id` within the caller repo and do not cancel an active apply. Do not point different repos at the same state; GitHub concurrency does not serialize across repos. PR previews use independent concurrency and backend locking.

After environment approval, reject a deployment if the default branch has advanced. This check cannot prevent a commit arriving immediately afterward; the apply still uses the exact planned commit. Terraform rejects plans made stale by state changes; generate a fresh plan rather than force-unlocking or silently replanning during apply.

Commit `.terraform.lock.hcl` in callers for reproducible provider selection. Pin remote module versions. The deployment bundle preserves the plan's selections across jobs even when onboarding a repo without a committed lock file.

## Validation

Run `actionlint` against both reusable workflows and the caller. Run `terraform fmt -check -recursive` and `terraform init -backend=false` followed by `terraform validate` in the caller. Live planning additionally verifies OIDC trust, backend access and provider credentials. Local YAML/shell checks do not establish those permissions. Never run an apply merely to test the pipeline.
