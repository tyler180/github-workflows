"""Create an exact-source Go bootstrap PR; never merge or rewrite application files."""
import argparse
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request

SOURCE_SHA = "b9c4a58af31b40aad1232ae7f7cb9b49bcc0b51c"
LEGACY_SOURCE_SHA = "6ec3d0adb8de698830e5a40f5bdb1f7cf6372d32"
TOOLING_SHA = "e4486c2c255b0f27a7b3ae4b59938dab1f516b4b"
LEGACY_TOOLING_SHA = "0ecbb0fcaea1deb8a8617a51d719b821500e873f"
MARKER = ".platform-bootstrap.json"
BRANCH = "automation/bootstrap-go"
SHA = re.compile(r"[0-9a-f]{40}")
VERSION = re.compile(r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:-[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*)?")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


class API:
    def __init__(self, token):
        require(bool(token), "Missing bootstrap credential")
        self.token = token

    def call(self, method, path, body=None, missing=False):
        request = urllib.request.Request("https://api.github.com/" + path,
            data=None if body is None else canonical(body), method=method,
            headers={"Authorization": "Bearer " + self.token,
                     "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if missing and error.code == 404:
                return None
            # Never print request headers, token, or arbitrary API response bodies.
            raise ValueError(f"GitHub {method} {path.split('?')[0]} failed (HTTP {error.code})") from None


def resolve_reference(api, repository, ref):
    require(isinstance(ref, str), "Bootstrap references must be strings")
    if SHA.fullmatch(ref):
        return ref
    require(VERSION.fullmatch(ref), "Use a full SHA or exact version release; branches/floating tags are prohibited")
    require(api is not None, "Release references require independent GitHub resolution")
    prefix = f"repos/{repository}/"
    release = api.call("GET", prefix + "releases/tags/" + ref)
    require(release.get("tag_name") == ref and release.get("draft") is False and release.get("immutable") is True,
            "Bootstrap version must be a published immutable release")
    obj = api.call("GET", prefix + "git/ref/tags/" + ref)["object"]
    for _ in range(4):
        if obj.get("type") != "tag":
            break
        require(SHA.fullmatch(obj.get("sha", "")), "Invalid annotated tag object")
        obj = api.call("GET", prefix + "git/tags/" + obj["sha"])["object"]
    require(obj.get("type") == "commit" and SHA.fullmatch(obj.get("sha", "")), "Release does not resolve to a commit")
    return obj["sha"]


def profiles(inventory, owner, api=None):
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", owner), "Invalid owner")
    require(inventory.get("schema_version") == 1, "Unsupported inventory schema")
    result = []
    for key, repo in inventory["repositories"].items():
        repo = copy.deepcopy(repo)
        require(re.fullmatch(r"[A-Za-z0-9_-]+", key), "Invalid inventory key")
        profile = repo.get("profile")
        require(profile in (None, "go"), "Unsupported repository profile")
        if profile is None:
            continue
        require(repo.get("stage") == "create", "Automatic bootstrap currently supports create stage only")
        require(repo.get("initialization", "basic") == "basic", "Go bootstrap requires basic initialization")
        name = repo["name"]
        require(re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", name) and name not in (".", ".."), "Invalid repository name")
        config = repo.get("bootstrap", {})
        common = {"go_version", "code_owner", "go_module"}
        legacy = {"source_sha", "workflow_sha", "hook_sha"}
        current = {"source_ref", "workflow_ref", "hook_ref"}
        require(set(config) in (common | legacy, common | current), "Provide exactly source_ref/workflow_ref/hook_ref (or legacy SHA fields); do not mix aliases")
        refs = {name: config[name + ("_ref" if set(config) == common | current else "_sha")] for name in ("source", "workflow", "hook")}
        if set(config) == common | legacy:
            require(all(isinstance(value, str) and SHA.fullmatch(value) for value in refs.values()), "Legacy _sha fields accept full SHAs only")
        resolved = {name + "_sha": resolve_reference(api, "tyler180/github-module" if name == "source" else "tyler180/github-workflows", ref) for name, ref in refs.items()}
        repo["bootstrap"] = config = {**{name: config[name] for name in common}, **resolved}
        repo["_bootstrap_refs"] = {name + "_ref": value for name, value in refs.items()}
        require(config["source_sha"] in (SOURCE_SHA, LEGACY_SOURCE_SHA), "Starter source is not approved by this automation version")
        expected_tooling = TOOLING_SHA if config["source_sha"] == SOURCE_SHA else LEGACY_TOOLING_SHA
        require(config["workflow_sha"] == expected_tooling and config["hook_sha"] == expected_tooling, "Workflow and hook pins are not approved by this automation version")
        require(re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", config["go_version"]), "Explicit Go patch version required")
        require(config["go_module"] == f"github.com/{owner}/{name}", "Go module must match destination repository")
        require(re.fullmatch(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)?", config["code_owner"]), "Invalid code owner")
        require(repo.get("visibility", "private") in ("private", "public"), "Go bootstrap requires private or public visibility")
        require(repo.get("default_branch") == "main", "Pilot bootstrap requires main")
        result.append((key, repo))
    return result


def render(source, config, destination):
    actual = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    require(actual == config["source_sha"] and actual in (SOURCE_SHA, LEGACY_SOURCE_SHA), "Starter checkout does not match approved source")
    subprocess.run(["python3", "-I", str(source / "scripts/render-template.py"),
        "--destination", str(destination), "--code-owner", config["code_owner"],
        "--workflow-ref", config["workflow_sha"], "--hook-ref", config["hook_sha"],
        "--go-version", config["go_version"], "--go-module", config["go_module"]], check=True)
    hooks = destination / ".pre-commit-config.yaml"
    # Preserve legacy rendering byte-for-byte so retries keep their fingerprints.
    if config["source_sha"] == LEGACY_SOURCE_SHA:
        with hooks.open("a") as stream:
            stream.write("  - repo: local\n    hooks:\n      - id: go-validation\n        name: Go formatting, vet and race tests\n        entry: bash scripts/validate.sh\n        language: system\n        pass_filenames: false\n        always_run: true\n")
    files = {}
    for path in sorted(destination.rglob("*")):
        require(not path.is_symlink(), "Starter symlinks are forbidden")
        if path.is_file():
            relative = path.relative_to(destination).as_posix()
            require(not relative.startswith("terraform/") and relative != MARKER, "Unexpected starter file")
            content = path.read_bytes()
            require(len(content) < 1024 * 1024, "Starter file exceeds limit")
            files[relative] = {"content": content, "mode": "100755" if path.stat().st_mode & 0o111 else "100644"}
    return files


def content(api, prefix, path, ref):
    data = api.call("GET", prefix + "contents/" + path + "?ref=" + urllib.parse.quote(ref, safe=""), missing=True)
    if data is None:
        return None
    require(data.get("type") == "file" and data.get("encoding") == "base64", "Unexpected repository content")
    return base64.b64decode(data["content"])


def git_blob(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def tree(api, prefix, sha):
    response = api.call("GET", prefix + "git/trees/" + sha + "?recursive=1")
    require(not response.get("truncated"), "Repository tree is truncated")
    return {item["path"]: item for item in response["tree"] if item["type"] != "tree"}


def bootstrap(api, owner, key, repo, files, source_run):
    prefix = f"repos/{owner}/{repo['name']}/"
    actual = api.call("GET", prefix.rstrip("/"))
    visibility = repo.get("visibility", "private")
    require(visibility in ("private", "public"), "Go bootstrap requires private or public visibility")
    require(actual.get("visibility") == visibility and actual.get("private") is (visibility == "private"), "Destination visibility differs from inventory")
    require(actual["full_name"].lower() == f"{owner}/{repo['name']}".lower() and actual["default_branch"] == "main" and not actual["archived"], "Destination identity/settings mismatch")
    fingerprint = hashlib.sha256(canonical({"repository_id": actual["id"], "profile": "go", "config": repo["bootstrap"],
        "files": {path: {"sha256": hashlib.sha256(value["content"]).hexdigest(), "mode": value["mode"]} for path, value in files.items()}})).hexdigest()
    provenance = {"requested_refs": repo.get("_bootstrap_refs", {}),
                  "resolved_refs": {name: repo["bootstrap"][name] for name in ("source_sha", "workflow_sha", "hook_sha")}}
    main = api.call("GET", prefix + "git/ref/heads/main")["object"]["sha"]
    marker_bytes = content(api, prefix, MARKER, main)
    if marker_bytes is not None:
        marker = json.loads(marker_bytes)
        require(marker.get("repository_id") == actual["id"] and marker.get("fingerprint") == fingerprint, "Existing bootstrap differs; use a reviewed upgrade PR")
        print(f"{actual['full_name']}: already bootstrapped; application files preserved")
        return {"repository": actual["full_name"], "status": "already-bootstrapped", "commit": main, **provenance}
    base_tree = tree(api, prefix, main)
    require(set(base_tree) == {"README.md"}, "Refusing bootstrap: repository already contains application files")
    expected_readme = f"# {repo['name']}\n{repo.get('description', '')}\n".encode()
    require(content(api, prefix, "README.md", main) == expected_readme, "Refusing to overwrite a customized README")
    ref = api.call("GET", prefix + "git/ref/heads/" + BRANCH, missing=True)
    marker = {"schema_version": 1, "repository_id": actual["id"], "inventory_key": key, "profile": "go",
        "fingerprint": fingerprint, "source_sha": repo["bootstrap"]["source_sha"], "tooling_sha": repo["bootstrap"]["workflow_sha"],
        "base_sha": main, "source_run": source_run, **provenance}
    if ref is None:
        files = {**files, MARKER: {"content": json.dumps(marker, indent=2, sort_keys=True).encode() + b"\n", "mode": "100644"}}
        entries = []
        for path, value in files.items():
            blob = api.call("POST", prefix + "git/blobs", {"content": base64.b64encode(value["content"]).decode(), "encoding": "base64"})
            entries.append({"path": path, "mode": value["mode"], "type": "blob", "sha": blob["sha"]})
        base_commit = api.call("GET", prefix + "git/commits/" + main)
        new_tree = api.call("POST", prefix + "git/trees", {"base_tree": base_commit["tree"]["sha"], "tree": entries})
        commit = api.call("POST", prefix + "git/commits", {"message": "Bootstrap Go profile from approved source", "tree": new_tree["sha"], "parents": [main]})
        require(api.call("GET", prefix + "git/ref/heads/main")["object"]["sha"] == main, "Default branch changed during bootstrap; retry")
        api.call("POST", prefix + "git/refs", {"ref": "refs/heads/" + BRANCH, "sha": commit["sha"]})
        head = commit["sha"]
    else:
        head = ref["object"]["sha"]
        existing = json.loads(content(api, prefix, MARKER, head) or b"{}")
        require(existing.get("fingerprint") == fingerprint and existing.get("base_sha") == main and existing.get("repository_id") == actual["id"], "Existing bootstrap branch conflicts; refusing to rewrite")
        entries = tree(api, prefix, head)
        require(set(entries) == set(files) | {MARKER}, "Existing bootstrap branch has unexpected files")
        for path, value in files.items():
            require(entries[path]["sha"] == git_blob(value["content"]) and entries[path]["mode"] == value["mode"], "Bootstrap branch was modified; review manually")
    prs = api.call("GET", prefix + "pulls?state=all&head=" + urllib.parse.quote(owner + ":" + BRANCH, safe="") + "&base=main&per_page=100")
    require(len(prs) < 100, "Bootstrap PR history needs manual review")
    open_prs = [pr for pr in prs if pr["state"] == "open"]
    require(len(open_prs) <= 1, "Multiple bootstrap PRs")
    if not open_prs:
        require(not prs, "Bootstrap PR was closed; do not reopen without review")
        pr = api.call("POST", prefix + "pulls", {"title": "Bootstrap Go repository profile", "head": BRANCH, "base": "main",
            "body": f"Adds Go source, pinned shared CI, CODEOWNERS, and local pre-commit hooks.\n\nRequested references: {json.dumps(repo.get('_bootstrap_refs', {}), sort_keys=True)}\nSource: tyler180/github-module@{repo['bootstrap']['source_sha']}\nShared tooling: tyler180/github-workflows@{repo['bootstrap']['workflow_sha']}\nBootstrap fingerprint: {fingerprint}\nRun: {source_run}\n\nReview and merge after CI succeeds. Developers run `pre-commit install` locally. Protection and access stages are unchanged."})
    else:
        pr = open_prs[0]
        require(pr["head"]["sha"] == head, "PR head differs from verified bootstrap branch")
    print(pr["html_url"])
    return {"repository": actual["full_name"], "status": "pr-open", "pr_url": pr["html_url"], "head_sha": head, **provenance}


def source_for(config, source, legacy_source):
    if config["source_sha"] == SOURCE_SHA:
        return source
    require(config["source_sha"] == LEGACY_SOURCE_SHA and legacy_source is not None,
            "Legacy bootstrap profile requires the approved --legacy-source checkout")
    return legacy_source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--legacy-source", type=Path, help="reviewed original starter for already-pinned profiles")
    parser.add_argument("--owner", required=True)
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    api = API(os.environ.get("GH_TOKEN"))
    inventory = json.loads(args.inventory.read_text())
    results = []
    # Validate every selected profile before any remote mutation.
    selected = profiles(inventory, args.owner, api)
    with tempfile.TemporaryDirectory() as directory:
        prepared = [(key, repo, render(source_for(repo["bootstrap"], args.source, args.legacy_source), repo["bootstrap"], Path(directory) / key)) for key, repo in selected]
        for key, repo, files in prepared:
            results.append(bootstrap(api, args.owner, key, repo, files, args.run_url))
    args.report.write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit("Bootstrap failed: " + str(error))
