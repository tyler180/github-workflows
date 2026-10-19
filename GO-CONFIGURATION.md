# Go application onboarding

Go validation checks formatting, vet, race tests and `go build ./...`, regardless
of whether commands live at the module root or under `cmd/`. Shared CI does not
call the application or depend on an AI service.

Use `scripts/configure-go.py` once after importing application source. Run it
from the trusted shared tooling checkout, with the application as data:

```sh
python3 -I scripts/configure-go.py --repository /path/to/application
# Review discovery/build results, then write the proposal:
python3 -I scripts/configure-go.py --repository /path/to/application --write
```

The tool uses `go list -mod=readonly -json ./...` to find `main` packages in one
module, then builds the selected executable and all packages without running the
application. A missing `func main`, unresolved dependencies or compilation error
prevents configuration writes. Go must already be installed at a compatible
version. Workspaces and nested modules require separate invocations at each module
root. Discovery respects the current host's GOOS/GOARCH and default build tags;
platform-specific commands need a separate build design.

One candidate is selected without AI. For several candidates, either specify
`--main-package ./cmd/server`, or opt in to selection using OpenAI:

```sh
# Supply OPENAI_API_KEY securely in the environment and choose a model that
# supports Responses API Structured Outputs.
python3 -I scripts/configure-go.py --repository /path/to/application \
  --ai --model YOUR_MODEL --write
```

AI sees only candidate package paths and at most 12,000 characters each from
README.md, README.MD and Makefile; those files may contain private project details.
No source, environment files or credentials are included. Symlinked documentation
is excluded. The API uses `store: false`, no tools and a strict schema restricting
selection to discovered paths or null. A refusal, incomplete/invalid response,
network failure or ambiguous choice stops without configuration writes. There is
no guessed fallback and no AI call for a single candidate or explicit selection.
The integration follows the [Structured Outputs documentation](https://developers.openai.com/api/docs/guides/structured-outputs).

The proposal records `GO_MAIN` in a tracked `.go-main.mk`, includes it from the
Makefile, and replaces the standard `go run .` recipe with `go run $(GO_MAIN)`.
A missing Makefile gets simple run/build targets. A known starter validation
script is updated to generic checks, removing the old greeting assertion if
present. Custom run recipes or validation scripts stop for manual reconciliation;
application source, tests, go.mod and imports are never rewritten. Review and
commit the files. Subsequent builds consume that configuration without discovery
or AI. Re-running with the same selection produces the same files.

## Reviewed configuration PR

`repository-go-configure.yml` is a reusable workflow for a manual caller on its
default branch in the protected `production` environment. It checks out the target repository's default branch under the
caller's owner, verifies the reviewed tooling SHA/immutable release, configures
and builds, then opens a PR on `codex/configure-go-main`. It never merges or
replaces an existing proposal branch. Target source must already be present.
Private dependency authentication is not forwarded to build subprocesses; projects
requiring it need separately scoped dependency access before using this workflow.

The caller needs `TF_GITHUB_BOOTSTRAP_TOKEN` with target repository contents/PR
write permissions (including source-PR CI permission as required by your GitHub
App/token setup). `OPENAI_API_KEY` is needed only for AI selection. Do not expose
this workflow to PR events or fork code. Candidate builds execute compiler/CGO
work in the manual runner; the tool strips AI and GitHub credentials from Go
subprocess environments. AI suggestions do not replace tests or required checks.

In `github-infra`, `scripts/render-go-configuration.py` stages an inventory-based,
pinned manual caller. Publish/review this shared tooling first, then render with
its actual immutable release or full commit SHA. Existing bootstrap and source
pins are not silently advanced.

```sh
python3 -m unittest discover -s scripts/tests -p test_configure_go.py
```
