"""Create an exact-source Go bootstrap PR; never merge or rewrite application files."""
import argparse
import base64
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

SOURCE_SHA = "6ec3d0adb8de698830e5a40f5bdb1f7cf6372d32"
TOOLING_SHA = "0ecbb0fcaea1deb8a8617a51d719b821500e873f"
MARKER = ".platform-bootstrap.json"
BRANCH = "automation/bootstrap-go"


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


def profiles(inventory, owner):
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", owner), "Invalid owner")
    require(inventory.get("schema_version") == 1, "Unsupported inventory schema")
    result = []
    for key, repo in inventory["repositories"].items():
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
        require(set(config) == {"source_sha", "workflow_sha", "hook_sha", "go_version", "code_owner", "go_module"}, "Go bootstrap requires explicit pins, version, module and owner")
        require(config["source_sha"] == SOURCE_SHA, "Starter source is not approved by this automation version")
        require(config["workflow_sha"] == TOOLING_SHA and config["hook_sha"] == TOOLING_SHA, "Workflow and hook pins are not approved by this automation version")
        require(re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", config["go_version"]), "Explicit Go patch version required")
        require(config["go_module"] == f"github.com/{owner}/{name}", "Go module must match destination repository")
        require(re.fullmatch(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)?", config["code_owner"]), "Invalid code owner")
        require(repo.get("visibility", "private") == "private", "Pilot bootstrap requires a private repository")
        require(repo.get("default_branch") == "main", "Pilot bootstrap requires main")
        result.append((key, repo))
    return result


def render(source, config, destination):
    actual = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    require(actual == SOURCE_SHA, "Starter checkout does not match approved source")
    subprocess.run(["python3", "-I", str(source / "scripts/render-template.py"),
        "--destination", str(destination), "--code-owner", config["code_owner"],
        "--workflow-ref", config["workflow_sha"], "--hook-ref", config["hook_sha"],
        "--go-version", config["go_version"], "--go-module", config["go_module"]], check=True)
    hooks = destination / ".pre-commit-config.yaml"
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
    require(actual["full_name"].lower() == f"{owner}/{repo['name']}".lower() and actual["private"] is True and actual["default_branch"] == "main" and not actual["archived"], "Destination identity/settings mismatch")
    fingerprint = hashlib.sha256(canonical({"repository_id": actual["id"], "profile": "go", "config": repo["bootstrap"],
        "files": {path: {"sha256": hashlib.sha256(value["content"]).hexdigest(), "mode": value["mode"]} for path, value in files.items()}})).hexdigest()
    main = api.call("GET", prefix + "git/ref/heads/main")["object"]["sha"]
    marker_bytes = content(api, prefix, MARKER, main)
    if marker_bytes is not None:
        marker = json.loads(marker_bytes)
        require(marker.get("repository_id") == actual["id"] and marker.get("fingerprint") == fingerprint, "Existing bootstrap differs; use a reviewed upgrade PR")
        print(f"{actual['full_name']}: already bootstrapped; application files preserved")
        return {"repository": actual["full_name"], "status": "already-bootstrapped", "commit": main}
    base_tree = tree(api, prefix, main)
    require(set(base_tree) == {"README.md"}, "Refusing bootstrap: repository already contains application files")
    expected_readme = f"# {repo['name']}\n{repo.get('description', '')}\n".encode()
    require(content(api, prefix, "README.md", main) == expected_readme, "Refusing to overwrite a customized README")
    ref = api.call("GET", prefix + "git/ref/heads/" + BRANCH, missing=True)
    marker = {"schema_version": 1, "repository_id": actual["id"], "inventory_key": key, "profile": "go",
        "fingerprint": fingerprint, "source_sha": SOURCE_SHA, "tooling_sha": TOOLING_SHA,
        "base_sha": main, "source_run": source_run}
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
            "body": f"Adds Go source, pinned shared CI, CODEOWNERS, and local pre-commit hooks.\n\nSource: tyler180/github-module@{SOURCE_SHA}\nShared tooling: tyler180/github-workflows@{TOOLING_SHA}\nBootstrap fingerprint: {fingerprint}\nRun: {source_run}\n\nReview and merge after CI succeeds. Developers run `pre-commit install` locally. Protection and access stages are unchanged."})
    else:
        pr = open_prs[0]
        require(pr["head"]["sha"] == head, "PR head differs from verified bootstrap branch")
    print(pr["html_url"])
    return {"repository": actual["full_name"], "status": "pr-open", "pr_url": pr["html_url"], "head_sha": head}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    api = API(os.environ.get("GH_TOKEN"))
    inventory = json.loads(args.inventory.read_text())
    results = []
    # Validate every selected profile before any remote mutation.
    selected = profiles(inventory, args.owner)
    with tempfile.TemporaryDirectory() as directory:
        prepared = [(key, repo, render(args.source, repo["bootstrap"], Path(directory) / key)) for key, repo in selected]
        for key, repo, files in prepared:
            results.append(bootstrap(api, args.owner, key, repo, files, args.run_url))
    args.report.write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit("Bootstrap failed: " + str(error))
