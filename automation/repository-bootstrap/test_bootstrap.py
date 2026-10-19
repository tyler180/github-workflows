import copy
import json
import unittest
import bootstrap as b


def repository():
    return {"name": "pilot", "description": "Pilot.", "profile": "go", "stage": "create",
        "visibility": "private", "initialization": "basic", "default_branch": "main",
        "bootstrap": {"source_sha": b.SOURCE_SHA, "workflow_sha": b.TOOLING_SHA,
            "hook_sha": b.TOOLING_SHA, "go_version": "1.26.5", "code_owner": "owner",
            "go_module": "github.com/owner/pilot"}}


class FakeAPI:
    def __init__(self):
        self.visibility = "private"
        self.refs = {"main": "base"}
        self.files = {"base": {"README.md": b"# pilot\nPilot.\n"}}
        self.modes = {"base": {"README.md": "100644"}}
        self.blobs = {}
        self.trees = {}
        self.prs = []
        self.writes = []
        self.fail_pr_once = False

    def call(self, method, path, body=None, missing=False):
        tail = path.removeprefix("repos/owner/pilot/")
        if method != "GET":
            self.writes.append((method, tail))
        if path == "repos/owner/pilot":
            return {"full_name": "owner/pilot", "id": 42, "visibility": self.visibility, "private": self.visibility == "private", "default_branch": "main", "archived": False}
        if tail.startswith("git/ref/heads/"):
            ref = tail.removeprefix("git/ref/heads/")
            return {"object": {"sha": self.refs[ref]}} if ref in self.refs else None
        if tail.startswith("contents/"):
            from urllib.parse import unquote
            name, ref = tail.removeprefix("contents/").split("?ref=")
            content = self.files[unquote(ref)].get(name)
            return None if content is None else {"type": "file", "encoding": "base64", "content": b.base64.b64encode(content).decode()}
        if tail.startswith("git/trees/"):
            sha = tail.split("git/trees/", 1)[1].split("?")[0]
            return {"truncated": False, "tree": [{"path": name, "type": "blob", "mode": self.modes[sha][name], "sha": b.git_blob(content)} for name, content in self.files[sha].items()]}
        if tail == "git/blobs":
            content = b.base64.b64decode(body["content"])
            sha = b.git_blob(content)
            self.blobs[sha] = content
            return {"sha": sha}
        if tail.startswith("git/commits/"):
            return {"tree": {"sha": "base-tree"}}
        if tail == "git/trees":
            assert body['base_tree'] == 'base-tree'
            self.trees["tree"] = body["tree"]
            return {"sha": "tree"}
        if tail == "git/commits":
            self.files["head"] = {item["path"]: self.blobs[item["sha"]] for item in self.trees["tree"]}
            self.modes["head"] = {item["path"]: item["mode"] for item in self.trees["tree"]}
            return {"sha": "head"}
        if tail == "git/refs":
            self.refs[body["ref"].removeprefix("refs/heads/")] = body["sha"]
            return {}
        if tail.startswith("pulls?"):
            return self.prs
        if tail == "pulls":
            if self.fail_pr_once:
                self.fail_pr_once = False
                raise ValueError("Synthetic PR API failure")
            pr = {"state": "open", "head": {"sha": "head"}, "html_url": "https://github.com/owner/pilot/pull/1"}
            self.prs.append(pr)
            return pr
        raise AssertionError((method, path, body))


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeAPI()
        self.repo = repository()
        self.files = {"README.md": {"content": b"Go starter\n", "mode": "100644"},
            "scripts/validate.sh": {"content": b"#!/bin/sh\nexit 0\n", "mode": "100755"}}

    def run_bootstrap(self):
        return b.bootstrap(self.api, "owner", "pilot", self.repo, self.files, "https://github.com/owner/infra/actions/runs/1")

    def test_legacy_profile_keeps_selected_source_and_provenance(self):
        self.repo['bootstrap'].update(source_sha=b.LEGACY_SOURCE_SHA,
                                      workflow_sha=b.LEGACY_TOOLING_SHA,
                                      hook_sha=b.LEGACY_TOOLING_SHA)
        b.profiles({'schema_version': 1, 'repositories': {'pilot': self.repo}}, 'owner')
        self.run_bootstrap()
        marker = json.loads(self.api.files['head'][b.MARKER])
        self.assertEqual(marker['source_sha'], b.LEGACY_SOURCE_SHA)
        self.assertEqual(marker['tooling_sha'], b.LEGACY_TOOLING_SHA)
        self.api.refs['main'] = 'head'
        writes = len(self.api.writes)
        self.assertEqual(self.run_bootstrap()['status'], 'already-bootstrapped')
        self.assertEqual(len(self.api.writes), writes)

    def test_source_selection_requires_exact_legacy_checkout(self):
        self.assertEqual(b.source_for({'source_sha': b.SOURCE_SHA}, 'new', 'old'), 'new')
        self.assertEqual(b.source_for({'source_sha': b.LEGACY_SOURCE_SHA}, 'new', 'old'), 'old')
        with self.assertRaisesRegex(ValueError, 'legacy-source'):
            b.source_for({'source_sha': b.LEGACY_SOURCE_SHA}, 'new', None)

    def test_source_and_tooling_generations_cannot_be_mixed(self):
        for source, tooling in ((b.SOURCE_SHA, b.LEGACY_TOOLING_SHA),
                                (b.LEGACY_SOURCE_SHA, b.TOOLING_SHA)):
            repo = repository()
            repo['bootstrap'].update(source_sha=source, workflow_sha=tooling, hook_sha=tooling)
            with self.assertRaisesRegex(ValueError, 'not approved'):
                b.profiles({'schema_version': 1, 'repositories': {'pilot': repo}}, 'owner')

    def test_public_profile_and_bootstrap_are_supported(self):
        self.repo['visibility'] = self.api.visibility = 'public'
        profiles = b.profiles({'schema_version': 1, 'repositories': {'pilot': self.repo}}, 'owner')
        self.assertEqual(profiles[0][1]['visibility'], 'public')
        first = self.run_bootstrap()
        self.assertEqual(first['status'], 'pr-open')
        writes = len(self.api.writes)
        self.assertEqual(self.run_bootstrap(), first)
        self.assertEqual(len(self.api.writes), writes)

    def test_visibility_mismatch_rejected_before_writes(self):
        for intended, observed in [('private', 'public'), ('public', 'private')]:
            with self.subTest(intended=intended):
                self.repo['visibility'] = intended
                self.api.visibility = observed
                with self.assertRaisesRegex(ValueError, 'visibility differs'):
                    self.run_bootstrap()
                self.assertFalse(self.api.writes)

    def test_internal_visibility_is_rejected(self):
        self.repo['visibility'] = 'internal'
        with self.assertRaisesRegex(ValueError, 'private or public'):
            b.profiles({'schema_version': 1, 'repositories': {'pilot': self.repo}}, 'owner')

    def test_retry_reuses_pr_and_preserves_executable_mode(self):
        first = self.run_bootstrap()
        writes = len(self.api.writes)
        self.assertEqual(self.run_bootstrap(), first)
        self.assertEqual(len(self.api.writes), writes)
        self.assertEqual(self.api.modes["head"]["scripts/validate.sh"], "100755")

    def test_retry_recovers_branch_after_pr_creation_failure(self):
        self.api.fail_pr_once = True
        with self.assertRaisesRegex(ValueError, "Synthetic"):
            self.run_bootstrap()
        refs_before = sum(path == "git/refs" for _, path in self.api.writes)
        self.assertEqual(self.run_bootstrap()["status"], "pr-open")
        self.assertEqual(sum(path == "git/refs" for _, path in self.api.writes), refs_before)

    def test_existing_application_or_custom_readme_is_never_overwritten(self):
        for files in ({"README.md": b"# custom\n"}, {"README.md": b"# pilot\nPilot.\n", "main.go": b"app"}):
            with self.subTest(files=files):
                self.api = FakeAPI()
                self.api.files["base"] = files
                self.api.modes["base"] = {key: "100644" for key in files}
                with self.assertRaisesRegex(ValueError, "Refusing"):
                    self.run_bootstrap()
                self.assertFalse(self.api.writes)

    def test_modified_branch_is_not_rewritten(self):
        self.run_bootstrap()
        self.api.files["head"]["README.md"] = b"modified"
        writes = len(self.api.writes)
        with self.assertRaisesRegex(ValueError, "modified"):
            self.run_bootstrap()
        self.assertEqual(len(self.api.writes), writes)

    def test_merged_bootstrap_preserves_later_developer_changes(self):
        self.run_bootstrap()
        self.api.refs["main"] = "head"
        self.api.files["head"]["README.md"] = b"developer edits"
        writes = len(self.api.writes)
        self.assertEqual(self.run_bootstrap()["status"], "already-bootstrapped")
        self.assertEqual(len(self.api.writes), writes)
        self.assertEqual(self.api.files["head"]["README.md"], b"developer edits")

    def test_closed_pr_requires_manual_review(self):
        self.run_bootstrap()
        self.api.prs[0]["state"] = "closed"
        with self.assertRaisesRegex(ValueError, "closed"):
            self.run_bootstrap()

    def test_changed_pins_or_profile_rejected_before_writes(self):
        for changes in ({"profile": "terraform"}, {"stage": "grant"}, {"initialization": "template"}):
            repo = {**repository(), **changes}
            with self.assertRaises(ValueError):
                b.profiles({"schema_version": 1, "repositories": {"pilot": repo}}, "owner")
        repo = repository()
        repo["bootstrap"]["source_sha"] = "a" * 40
        with self.assertRaises(ValueError):
            b.profiles({"schema_version": 1, "repositories": {"pilot": repo}}, "owner")

    def test_repository_identity_bound_to_marker(self):
        self.run_bootstrap()
        marker = json.loads(self.api.files["head"][b.MARKER])
        self.assertEqual(marker["repository_id"], 42)
        self.assertEqual(marker["base_sha"], "base")
        self.assertEqual(marker["source_sha"], b.SOURCE_SHA)


if __name__ == "__main__":
    unittest.main()
