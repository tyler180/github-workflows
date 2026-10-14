"""Add disabled Terraform callers through a PR without replacing existing source."""
import argparse
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import urllib.parse

spec = importlib.util.spec_from_file_location("bootstrap", Path(__file__).with_name("bootstrap.py"))
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)

# Updated to the reviewed immutable starter release before publishing automation.
SOURCE_REF = "v0.1.0-rc.5"
SOURCE_SHA = "617b62433ff61c137ce9e05974d01cee6752f5ec"
MARKER = ".platform-terraform-starter.json"
BRANCH = "automation/terraform-starter"
FILES = {".github/workflows/terraform-plan.yml", ".github/workflows/terraform-deploy.yml", "TERRAFORM-STARTER.md"}


def selections(inventory, owner):
    b.require(inventory.get("schema_version") == 1, "Unsupported inventory schema")
    b.require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", owner), "Invalid owner")
    selected = []
    for key, repo in inventory["repositories"].items():
        config = repo.get("terraform_starter")
        if config is None:
            continue
        b.require(repo.get("stage") != "adopt", "Adoption must be accepted before automatic source installation")
        b.require(re.fullmatch(r"[A-Za-z0-9_-]+", key), "Invalid inventory key")
        b.require(re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", repo.get("name", "")) and repo["name"] not in (".", ".."), "Invalid repository name")
        b.require(isinstance(config, dict) and set(config) == {"terraform_version", "workflow_ref", "working_directory"}, "Invalid Terraform starter fields")
        b.require(all(isinstance(value, str) for value in config.values()), "Terraform starter fields must be strings")
        b.require(re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", config["terraform_version"]), "Explicit Terraform patch version required")
        b.require(b.SHA.fullmatch(config["workflow_ref"]), "Terraform workflow requires a full reviewed SHA")
        b.require(re.fullmatch(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*", config["working_directory"]), "Invalid Terraform directory")
        selected.append((key, repo))
    return selected


def render(source, config, destination):
    actual = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    b.require(actual == SOURCE_SHA, "Terraform starter checkout does not match approved source")
    subprocess.run(["python3", "-I", str(source / "scripts/render-template.py"),
        "--destination", str(destination), "--workflows-only",
        "--terraform-version", config["terraform_version"],
        "--terraform-workflow-ref", config["workflow_ref"],
        "--terraform-directory", config["working_directory"]], check=True)
    files = {}
    for path in sorted(destination.rglob("*")):
        b.require(not path.is_symlink(), "Starter symlinks are forbidden")
        if path.is_file():
            data = path.read_bytes()
            b.require(len(data) < 1024 * 1024, "Starter file exceeds limit")
            files[path.relative_to(destination).as_posix()] = {"content": data, "mode": "100644"}
    b.require(set(files) == FILES, "Unexpected Terraform starter files")
    return files


def install(api, owner, key, repo, files, run_url):
    prefix = f"repos/{owner}/{repo['name']}/"
    actual = api.call("GET", prefix.rstrip("/"))
    b.require(actual["full_name"].lower() == f"{owner}/{repo['name']}".lower() and not actual["archived"], "Destination identity mismatch")
    branch = actual["default_branch"]
    main = api.call("GET", prefix + "git/ref/heads/" + urllib.parse.quote(branch, safe=""))["object"]["sha"]
    fingerprint = hashlib.sha256(b.canonical({"repository_id": actual["id"], "source_sha": SOURCE_SHA,
        "config": repo["terraform_starter"], "files": {path: b.git_blob(value["content"]) for path, value in files.items()}})).hexdigest()
    marker_data = b.content(api, prefix, MARKER, main)
    base_tree = b.tree(api, prefix, main)
    if marker_data is not None:
        marker = json.loads(marker_data)
        b.require(marker.get("repository_id") == actual["id"] and marker.get("fingerprint") == fingerprint, "Existing Terraform starter differs; use a reviewed upgrade PR")
        b.require(all(base_tree.get(path, {}).get("sha") == b.git_blob(value["content"]) for path, value in files.items()), "Installed Terraform caller was edited; use a reviewed upgrade PR")
        return {"repository": actual["full_name"], "status": "already-installed", "commit": main}
    b.require(not (set(files) & set(base_tree)), "Existing Terraform caller filenames conflict; reconcile manually")
    marker = {"schema_version": 1, "repository_id": actual["id"], "inventory_key": key,
        "fingerprint": fingerprint, "source_ref": SOURCE_REF, "source_sha": SOURCE_SHA,
        "base_sha": main, "source_run": run_url, "config": repo["terraform_starter"]}
    prepared = {**files, MARKER: {"content": json.dumps(marker, sort_keys=True, indent=2).encode() + b"\n", "mode": "100644"}}
    ref = api.call("GET", prefix + "git/ref/heads/" + BRANCH, missing=True)
    if ref is None:
        entries = []
        for path, value in prepared.items():
            blob = api.call("POST", prefix + "git/blobs", {"content": base64.b64encode(value["content"]).decode(), "encoding": "base64"})
            entries.append({"path": path, "type": "blob", "mode": value["mode"], "sha": blob["sha"]})
        commit = api.call("GET", prefix + "git/commits/" + main)
        new_tree = api.call("POST", prefix + "git/trees", {"base_tree": commit["tree"]["sha"], "tree": entries})
        new_commit = api.call("POST", prefix + "git/commits", {"message": "Add opt-in Terraform plan and manual deployment callers", "parents": [main], "tree": new_tree["sha"]})
        b.require(api.call("GET", prefix + "git/ref/heads/" + urllib.parse.quote(branch, safe=""))["object"]["sha"] == main, "Default branch changed during installation; retry")
        api.call("POST", prefix + "git/refs", {"ref": "refs/heads/" + BRANCH, "sha": new_commit["sha"]})
        head = new_commit["sha"]
    else:
        head = ref["object"]["sha"]
        existing = json.loads(b.content(api, prefix, MARKER, head) or b"{}")
        b.require(existing.get("base_sha") == main and existing.get("fingerprint") == fingerprint and existing.get("repository_id") == actual["id"], "Existing starter branch conflicts; refusing to rewrite")
        tree = b.tree(api, prefix, head)
        b.require(set(tree) == set(base_tree) | set(prepared), "Starter branch has unexpected files")
        for path, value in prepared.items():
            if path == MARKER:
                continue
            b.require(tree[path]["sha"] == b.git_blob(value["content"]) and tree[path]["mode"] == value["mode"], "Starter branch was modified")
        for path, value in base_tree.items():
            b.require(tree[path]["sha"] == value["sha"] and tree[path]["mode"] == value["mode"], "Existing application file was modified")
    prs = api.call("GET", prefix + "pulls?state=all&head=" + urllib.parse.quote(owner + ":" + BRANCH, safe="") + "&base=" + urllib.parse.quote(branch, safe="") + "&per_page=100")
    b.require(len(prs) < 100, "Starter PR history requires manual review")
    opened = [pr for pr in prs if pr["state"] == "open"]
    b.require(len(opened) <= 1, "Multiple starter PRs")
    if not opened:
        b.require(not prs, "Starter PR was closed; review manually")
        pr = api.call("POST", prefix + "pulls", {"title": "Add opt-in Terraform plan and manual deployment", "head": BRANCH, "base": branch,
            "body": f"Adds disabled Terraform callers and setup instructions while preserving existing source and CI.\n\nStarter source: {SOURCE_REF} ({SOURCE_SHA})\nWorkflow revision: {repo['terraform_starter']['workflow_ref']}\nInventory key: {key}\nRun: {run_url}\n\nConfigure OIDC, state access and a protected environment before enabling. Review existing build/dependency/secret requirements and avoid duplicate deployment routes."})
    else:
        pr = opened[0]
        b.require(pr["head"]["sha"] == head, "PR head differs from verified starter branch")
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
    api = b.API(os.environ.get("GH_TOKEN"))
    selected = selections(json.loads(args.inventory.read_text()), args.owner)
    approved = b.resolve_reference(api, "tyler180/github-module", SOURCE_REF)
    b.require(approved == SOURCE_SHA, "Starter release differs from approved source")
    results = []
    with tempfile.TemporaryDirectory() as directory:
        prepared = [(key, repo, render(args.source, repo["terraform_starter"], Path(directory) / key)) for key, repo in selected]
        for key, repo, files in prepared:
            results.append(install(api, args.owner, key, repo, files, args.run_url))
    args.report.write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit("Terraform starter installation failed: " + str(error))
