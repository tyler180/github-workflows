"""Prepare an app-scoped GitOps proposal; never deploy or overwrite existing settings."""
import os
import re
import shutil
from pathlib import Path

import yaml


def promote(source, target, app, namespace, image):
    for value in (app, namespace):
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", value):
            raise ValueError("app and namespace must be Kubernetes DNS labels")
    if not re.fullmatch(r"ghcr\.io/[a-z0-9._/-]+:v\d+\.\d+\.\d+@sha256:[a-f0-9]{64}", image):
        raise ValueError("image must include a release version and SHA256 digest")
    source, target = Path(source), Path(target)
    if source.is_symlink() or target.is_symlink():
        raise ValueError("symlink directories are not supported")
    dest = target / "applications" / app
    fresh = not dest.exists()
    if fresh:
        if not (source / "kustomization.yaml").is_file():
            raise ValueError("manifest directory must contain kustomization.yaml")
        # Only plain YAML files can be proposed; no symlinks or embedded scripts.
        for path in source.rglob("*"):
            if path.is_symlink() or (path.is_file() and path.suffix not in (".yaml", ".yml")):
                raise ValueError("manifest directory must contain only plain YAML files")
        shutil.copytree(source, dest)
    if any(p.is_symlink() for p in dest.rglob("*")):
        raise ValueError("symlinks are not supported")
    changed = 0
    for path in sorted(dest.rglob("*")):
        if not path.is_file() or path.suffix not in (".yaml", ".yml"):
            continue
        docs = list(yaml.safe_load_all(path.read_text()))
        modified = False
        for doc in docs:
            if not isinstance(doc, dict) or doc.get("kind") != "Deployment":
                continue
            if doc.get("metadata", {}).get("name") != app:
                continue
            for container in doc["spec"]["template"]["spec"]["containers"]:
                if container.get("name") == app:
                    container["image"] = image
                    changed += 1
                    modified = True
        if modified:
            path.write_text(yaml.safe_dump_all(docs, sort_keys=False))
    if changed != 1:
        raise ValueError("expected exactly one Deployment/container named after the app")
    kustomization = dest / "kustomization.yaml"
    config = yaml.safe_load(kustomization.read_text())
    if fresh:
        if config.get("namespace") != namespace:
            raise ValueError("manifest namespace must match workflow namespace")
    elif config.get("namespace") != namespace:
        raise ValueError("existing namespace differs; refusing to move the app")
    registration = target / "infrastructure" / "applications" / (app + ".yaml")
    application = {
        "apiVersion": "argoproj.io/v1alpha1", "kind": "Application",
        "metadata": {"name": app, "namespace": "argocd"},
        "spec": {"project": "default", "source": {
            "repoURL": "git@github.com:" + os.environ["GITOPS_REPOSITORY"] + ".git",
            "targetRevision": os.environ.get("GITOPS_BRANCH", "main"),
            "path": "applications/" + app},
            "destination": {"server": "https://kubernetes.default.svc", "namespace": namespace},
            "syncPolicy": {"syncOptions": ["CreateNamespace=true"]}}}
    if not registration.exists():
        registration.parent.mkdir(parents=True, exist_ok=True)
        registration.write_text(yaml.safe_dump(application, sort_keys=False))
    else:
        existing = yaml.safe_load(registration.read_text())
        if existing["spec"]["source"] != application["spec"]["source"] or existing["spec"]["destination"] != application["spec"]["destination"]:
            raise ValueError("existing Argo registration differs from requested app")
    root = target / "infrastructure" / "kustomization.yaml"
    root_config = yaml.safe_load(root.read_text())
    if not isinstance(root_config.get("resources"), list):
        raise ValueError("infrastructure kustomization must contain a resources list")
    entry = "applications/" + app + ".yaml"
    if entry not in root_config["resources"]:
        # Preserve existing comments, formatting, and resources.
        with root.open("a") as f:
            f.write("\n  - " + entry + "\n")
        if entry not in yaml.safe_load(root.read_text())["resources"]:
            raise ValueError("root layout requires manual registration; refusing invalid proposal")


if __name__ == "__main__":
    promote(os.environ["MANIFEST_DIR"], os.environ["GITOPS_DIR"],
            os.environ["APP_NAME"], os.environ["APP_NAMESPACE"], os.environ["IMAGE_REFERENCE"])
