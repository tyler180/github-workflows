"""Offline adversarial fixtures; no assertion here represents live evidence."""
import copy
import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

spec = importlib.util.spec_from_file_location("policy", Path(__file__).with_name("policy.py"))
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)
NOW = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)


def fixture(old_stage="verify", stage="protect"):
    repo = {
        "name": "offline-fixture", "stage": old_stage,
        "required_checks": {"platform-ci": {}},
        "provisioning_teams": {"fixture-provisioner": "maintain"},
        "developer_teams": {"fixture-developers": "push"},
        "tooling": {"workflow_sha": "a" * 40, "hooks_sha": "b" * 40},
    }
    base = {"schema_version": 1, "repositories": {"pilot": copy.deepcopy(repo)}}
    repo["stage"] = stage
    candidate = {"schema_version": 1, "repositories": {"pilot": repo}}
    record = {"schema_version": 1, "configuration_sha": "1"*40, "inventory_digest": policy.digest(policy.canonical(base)), "repositories": {"pilot": {
        "stage": old_stage, "name": repo["name"], "repository_id": 123,
        "tooling_digest": policy.tooling_digest(repo),
    }}}
    observed = {
        "source": "offline-fixture", "repository_id": 123,
        "head_sha": "c" * 40, "tooling_digest": policy.tooling_digest(repo),
        "observed_at": NOW.isoformat(),
        "bootstrap": {"mode": "template", "source_sha": "d" * 40,
                      "initial_commit": "e" * 40, "merged_commit": "c" * 40},
        "checks": [{"context": "platform-ci", "conclusion": "success",
                    "head_sha": "c" * 40, "run_id": 1, "event": "pull_request"}],
        "negative_validation_failed": True,
        "protection_active": True, "codeowner_review_required": True,
        "force_push_blocked": True, "deletion_blocked": True,
        "dummy_substitution_review_blocked": True, "untrusted_push_blocked": True,
        "effective_required_checks": {"platform-ci": {}},
    }
    evidence = {"schema_version": 1, "repositories": {"pilot": observed}}
    repository = {
        "name": repo["name"], "repo_id": 123, "visibility": "private",
        "description": "", "homepage_url": "", "topics": [],
        "allow_merge_commit": False, "allow_squash_merge": True,
        "allow_rebase_merge": False, "allow_auto_merge": False,
        "allow_update_branch": True, "delete_branch_on_merge": True,
        "archived": False, "archive_on_destroy": True,
        "has_issues": True, "has_discussions": False,
        "has_wiki": False, "has_projects": False,
    }
    ruleset = {
        "repository": repo["name"], "name": "platform-default-branch",
        "target": "branch", "enforcement": "active" if stage in ("protect", "grant") else "disabled",
        "bypass_actors": [],
        "conditions": [{"ref_name": [{"include": ["~DEFAULT_BRANCH"], "exclude": []}]}],
        "rules": [{"deletion": True, "non_fast_forward": True, "pull_request": [{
            "required_approving_review_count": 1, "require_code_owner_review": True,
            "dismiss_stale_reviews_on_push": True, "require_last_push_approval": True,
            "required_review_thread_resolution": True,
        }], "required_status_checks": [{"strict_required_status_checks_policy": True,
                "required_check": [{"context": "platform-ci", "integration_id": None}]}]}],
    }
    prefix = 'module.repositories["pilot"]'
    entries = [("github_repository.this", "github_repository", repository),
               ('github_repository_ruleset.this["managed"]', "github_repository_ruleset", ruleset)]
    teams = dict(repo["provisioning_teams"])
    if stage == "grant":
        teams.update(repo["developer_teams"])
    for team, permission in teams.items():
        entries.append((f'github_team_repository.this["{team}"]', "github_team_repository",
                        {"repository": repo["name"], "team_id": team, "permission": permission}))
    planned = []
    changes = []
    for address, kind, values in entries:
        full = prefix + "." + address
        planned.append({"address": full, "mode": "managed", "type": kind, "values": values, "provider_name": "registry.terraform.io/integrations/github"})
        before = copy.deepcopy(values)
        if kind == "github_repository_ruleset":
            before["enforcement"] = "active" if old_stage in ("protect", "grant") else "disabled"
        changes.append({"address": full, "module_address": prefix, "mode": "managed", "type": kind, "provider_name": "registry.terraform.io/integrations/github",
                        "change": {"actions": ["update"], "before": before, "after": copy.deepcopy(values), "after_unknown": {}}})
    guard = {"address": "terraform_data.contract", "mode": "managed", "type": "terraform_data", "provider_name": "terraform.io/builtin/terraform", "values": {"input": candidate}}
    changes.append({**guard, "change": {"actions": ["update"], "before": {}, "after": guard["values"]}})
    plan = {"format_version": "1.2", "complete": True, "errored": False, "applyable": True,
            "resource_changes": changes,
            "planned_values": {"root_module": {"resources": [guard], "child_modules": [{"address": prefix, "resources": planned}]}}}
    return base, record, candidate, evidence, plan


class PolicyTests(unittest.TestCase):
    def test_unprotected_stage_may_omit_ruleset(self):
        args = fixture("create", "create")
        plan = args[-1]
        plan['resource_changes'] = [c for c in plan['resource_changes'] if c['type'] != 'github_repository_ruleset']
        resources = plan['planned_values']['root_module']['child_modules'][0]['resources']
        resources[:] = [r for r in resources if r['type'] != 'github_repository_ruleset']
        policy.evaluate(*args, NOW)

    def test_protected_stage_cannot_omit_ruleset(self):
        args = fixture()
        plan = args[-1]
        plan['resource_changes'] = [c for c in plan['resource_changes'] if c['type'] != 'github_repository_ruleset']
        resources = plan['planned_values']['root_module']['child_modules'][0]['resources']
        resources[:] = [r for r in resources if r['type'] != 'github_repository_ruleset']
        with self.assertRaisesRegex(policy.PolicyError, 'Protected stages require a ruleset'):
            policy.evaluate(*args, NOW)

    def test_existing_repository_computed_unknown_not_exempted(self):
        args = fixture('create','create')
        change = next(c for c in args[-1]['resource_changes'] if c['type']=='github_repository')
        change['change']['after_unknown'] = {'web_commit_signoff_required':True}
        with self.assertRaisesRegex(policy.PolicyError, 'Unknown critical'):
            policy.evaluate(*args,NOW)

    def assert_rejected(self, mutate, old="verify", stage="protect"):
        args = fixture(old, stage)
        mutate(*args)
        with self.assertRaises(policy.PolicyError):
            policy.evaluate(*args, NOW)

    def test_forward_protection(self):
        policy.evaluate(*fixture(), NOW)

    def test_same_stage_retry(self):
        policy.evaluate(*fixture("verify", "verify"), NOW)

    def test_grant_after_protection(self):
        policy.evaluate(*fixture("protect", "grant"), NOW)

    def test_new_create(self):
        args = fixture("create", "create")
        args[0]["repositories"] = {}
        args[1]["repositories"] = {}
        args[1]["inventory_digest"] = policy.digest(policy.canonical(args[0]))
        args[4]["resource_changes"][0]["change"]["actions"] = ["create"]
        args[4]["resource_changes"][0]["change"]["before"] = None
        policy.evaluate(*args, NOW)

    def test_skip(self):
        self.assert_rejected(lambda *args: None, "bootstrap", "grant")

    def test_backward(self):
        self.assert_rejected(lambda *args: None, "protect", "verify")

    def test_forged_record(self):
        self.assert_rejected(lambda b, r, *rest: r["repositories"]["pilot"].update(stage="grant"))

    def test_missing_record(self):
        self.assert_rejected(lambda b, r, *rest: r.clear())

    def test_approved_inventory_binding(self):
        self.assert_rejected(lambda b, r, *rest: b["repositories"]["pilot"].update(description="unapproved"))

    def test_unknown_after_value(self):
        self.assert_rejected(lambda b, r, c, e, p: p["resource_changes"][0]["change"].update(after_unknown={"visibility": True}))

    def test_plan_sections_disagree(self):
        self.assert_rejected(lambda b, r, c, e, p: p["resource_changes"][0]["change"]["after"].update(visibility="public"))

    def test_no_change_plan(self):
        args = fixture("verify", "verify")
        for item in args[4]["resource_changes"]:
            item["change"]["actions"] = ["no-op"]
        args[4]["applyable"] = False
        policy.evaluate(*args, NOW)

    def test_missing_evidence(self):
        self.assert_rejected(lambda b, r, c, e, p: e["repositories"].clear())

    def test_live_claim_is_not_supported(self):
        self.assert_rejected(lambda b, r, c, e, p: e["repositories"]["pilot"].update(source="github-observation"))

    def test_stale_evidence(self):
        self.assert_rejected(lambda b, r, c, e, p: e["repositories"]["pilot"].update(observed_at=(NOW-timedelta(hours=25)).isoformat()))

    def test_future_evidence(self):
        self.assert_rejected(lambda b, r, c, e, p: e["repositories"]["pilot"].update(observed_at=(NOW+timedelta(seconds=1)).isoformat()))

    def test_pin_change_needs_new_evidence(self):
        self.assert_rejected(lambda b, r, c, e, p: c["repositories"]["pilot"]["tooling"].update(workflow_sha="f"*40), "grant", "grant")

    def test_repository_id_mismatch(self):
        self.assert_rejected(lambda b, r, c, e, p: e["repositories"]["pilot"].update(repository_id=999))

    def test_dummy_substitution_needs_review(self):
        self.assert_rejected(lambda b, r, c, e, p: e["repositories"]["pilot"].update(dummy_substitution_review_blocked=False), "protect", "grant")

    def test_failed_check(self):
        self.assert_rejected(lambda b, r, c, e, p: e["repositories"]["pilot"]["checks"][0].update(conclusion="failure"))

    def test_skipped_check(self):
        self.assert_rejected(lambda b, r, c, e, p: e["repositories"]["pilot"]["checks"][0].update(conclusion="skipped"))

    def test_cancelled_check(self):
        self.assert_rejected(lambda b, r, c, e, p: e["repositories"]["pilot"]["checks"][0].update(conclusion="cancelled"))

    def test_manual_check_not_proof(self):
        self.assert_rejected(lambda b, r, c, e, p: e["repositories"]["pilot"]["checks"][0].update(event="workflow_dispatch"))

    def test_destructive_actions(self):
        for actions in (["delete"], ["delete", "create"], ["create", "delete"]):
            with self.subTest(actions=actions):
                self.assert_rejected(lambda b, r, c, e, p: p["resource_changes"][0]["change"].update(actions=actions))

    def test_partial_recreation(self):
        self.assert_rejected(lambda b, r, c, e, p: p["resource_changes"][0]["change"].update(actions=["create"]))

    def test_incomplete_plan(self):
        self.assert_rejected(lambda b, r, c, e, p: p.update(complete=False))

    def test_deferred_plan(self):
        self.assert_rejected(lambda b, r, c, e, p: p.update(deferred_changes=[{}]))

    def test_drift_requires_reconciliation(self):
        self.assert_rejected(lambda b, r, c, e, p: p.update(resource_drift=[{}]))

    def test_unknown_protection(self):
        self.assert_rejected(lambda b, r, c, e, p: p["planned_values"]["root_module"]["child_modules"][0]["resources"][1]["values"].pop("enforcement"))

    def test_weakened_review(self):
        self.assert_rejected(lambda b, r, c, e, p: p["planned_values"]["root_module"]["child_modules"][0]["resources"][1]["values"]["rules"][0]["pull_request"][0].update(require_code_owner_review=False))

    def test_security_reset(self):
        def mutate(b, r, c, e, p):
            p["resource_changes"][0]["change"]["before"]["security_and_analysis"] = {"secret_scanning": "enabled"}
        self.assert_rejected(mutate)

    def test_premature_grant(self):
        def mutate(b, r, c, e, p):
            items = p["planned_values"]["root_module"]["child_modules"][0]["resources"]
            team = copy.deepcopy(items[-1])
            team["address"] = 'module.repositories["pilot"].github_team_repository.this["fixture-developers"]'
            team["values"].update(team_id="fixture-developers", permission="push")
            items.append(team)
            p["resource_changes"].append({**team, "module_address": 'module.repositories["pilot"]', "change": {"actions": ["create"], "after": team["values"]}})
        self.assert_rejected(mutate)

    def test_unlisted_resource(self):
        self.assert_rejected(lambda b, r, c, e, p: p["resource_changes"][0].update(type="github_actions_secret"))

    def test_wrong_provider(self):
        self.assert_rejected(lambda b, r, c, e, p: p["resource_changes"][0].update(provider_name="example.invalid/custom/github"))

    def test_provider_zero_integration_default(self):
        args = fixture()
        item = args[4]["planned_values"]["root_module"]["child_modules"][0]["resources"][1]
        item["values"]["rules"][0]["required_status_checks"][0]["required_check"][0]["integration_id"] = 0
        args[4]["resource_changes"][1]["change"]["after"] = copy.deepcopy(item["values"])
        policy.evaluate(*args, NOW)

    def test_pin_update_after_grant_with_fresh_evidence(self):
        args = fixture("grant", "grant")
        repo = args[2]["repositories"]["pilot"]
        repo["tooling"]["workflow_sha"] = "f" * 40
        args[3]["repositories"]["pilot"]["tooling_digest"] = policy.tooling_digest(repo)
        policy.evaluate(*args, NOW)

    def test_incomplete_resource_list(self):
        self.assert_rejected(lambda b, r, c, e, p: p["resource_changes"].pop())

    def test_empty_check_overlap(self):
        def mutate(b, r, c, e, p):
            c["repositories"]["pilot"]["required_checks"] = {"new-check": {}}
            e["repositories"]["pilot"]["tooling_digest"] = policy.tooling_digest(c["repositories"]["pilot"])
            e["repositories"]["pilot"]["effective_required_checks"] = {"new-check": {}}
            e["repositories"]["pilot"]["checks"][0]["context"] = "new-check"
        self.assert_rejected(mutate, "grant", "grant")

    def test_add_check_overlap(self):
        args = fixture("grant", "grant")
        repo = args[2]["repositories"]["pilot"]
        repo["required_checks"]["new-check"] = {}
        observed = args[3]["repositories"]["pilot"]
        observed["tooling_digest"] = policy.tooling_digest(repo)
        observed["effective_required_checks"] = copy.deepcopy(args[0]["repositories"]["pilot"]["required_checks"])
        observed["checks"].append({**observed["checks"][0], "context": "new-check", "run_id": 2})
        rs = args[4]["planned_values"]["root_module"]["child_modules"][0]["resources"][1]["values"]
        rs["rules"][0]["required_status_checks"][0]["required_check"].append({"context": "new-check", "integration_id": 0})
        args[4]["resource_changes"][1]["change"]["after"] = copy.deepcopy(rs)
        policy.evaluate(*args, NOW)

    def test_remove_old_after_overlap(self):
        args = fixture("grant", "grant")
        base_repo = args[0]["repositories"]["pilot"]
        base_repo["required_checks"]["new-check"] = {}
        args[1]["inventory_digest"] = policy.digest(policy.canonical(args[0]))
        args[1]["repositories"]["pilot"]["tooling_digest"] = policy.tooling_digest(base_repo)
        repo = args[2]["repositories"]["pilot"]
        repo["required_checks"] = {"new-check": {}}
        observed = args[3]["repositories"]["pilot"]
        observed["tooling_digest"] = policy.tooling_digest(repo)
        observed["effective_required_checks"] = copy.deepcopy(base_repo["required_checks"])
        observed["checks"][0]["context"] = "new-check"
        rs = args[4]["planned_values"]["root_module"]["child_modules"][0]["resources"][1]["values"]
        rs["rules"][0]["required_status_checks"][0]["required_check"][0]["context"] = "new-check"
        args[4]["resource_changes"][1]["change"]["after"] = copy.deepcopy(rs)
        policy.evaluate(*args, NOW)


    def test_live_checks_must_match_accepted_baseline(self):
        self.assert_rejected(lambda b, r, c, e, p: e["repositories"]["pilot"].update(
            effective_required_checks={"unapproved-check": {}}), "grant", "grant")

    def test_changed_integration_is_not_overlap(self):
        args = fixture("grant", "grant")
        repo = args[2]["repositories"]["pilot"]
        repo["required_checks"]["platform-ci"] = {"integration_id": 99}
        observed = args[3]["repositories"]["pilot"]
        observed["tooling_digest"] = policy.tooling_digest(repo)
        observed["checks"][0]["integration_id"] = 99
        with self.assertRaisesRegex(policy.PolicyError, "verified overlap"):
            policy.validate_transition(*args[:4], NOW)

    def allowlist_fixture(self, patterns, planned_patterns):
        args = fixture()
        selected = {"github_owned_allowed": False, "verified_allowed": False,
                    "patterns_allowed": patterns}
        args[2]["repositories"]["pilot"]["actions_policy"] = {
            "enabled": True, "allowed_actions": "selected", "sha_pinning_required": True,
            "selected": selected}
        value = {"repository": "offline-fixture", "enabled": True,
                 "allowed_actions": "selected", "sha_pinning_required": True,
                 "allowed_actions_config": [{**selected, "patterns_allowed": planned_patterns}]}
        item = {"address": 'module.repositories["pilot"].github_actions_repository_permissions.this["managed"]',
                "module_address": 'module.repositories["pilot"]', "mode": "managed",
                "type": "github_actions_repository_permissions",
                "provider_name": "registry.terraform.io/integrations/github", "values": value}
        args[4]["planned_values"]["root_module"]["child_modules"][0]["resources"].append(item)
        args[4]["resource_changes"].append({**item, "change": {
            "actions": ["create"], "before": None, "after": copy.deepcopy(value), "after_unknown": {}}})
        return args

    def test_allowlist_order_is_not_significant(self):
        args = self.allowlist_fixture(["z/*", "a/*"], ["a/*", "z/*"])
        policy.validate_plan(args[2], args[0], args[1], args[4])

    def test_allowlist_changed_pattern_rejected(self):
        args = self.allowlist_fixture(["z/*", "a/*"], ["a/*", "other/*"])
        with self.assertRaisesRegex(policy.PolicyError, "allowlist mismatch"):
            policy.validate_plan(args[2], args[0], args[1], args[4])

    def test_allowlist_changed_boolean_rejected(self):
        args = self.allowlist_fixture(["z/*", "a/*"], ["a/*", "z/*"])
        value = args[4]["planned_values"]["root_module"]["child_modules"][0]["resources"][-1]["values"]
        value["allowed_actions_config"][0]["verified_allowed"] = True
        args[4]["resource_changes"][-1]["change"]["after"] = copy.deepcopy(value)
        with self.assertRaisesRegex(policy.PolicyError, "allowlist mismatch"):
            policy.validate_plan(args[2], args[0], args[1], args[4])

    def test_snapshot_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.tf").write_text("original")
            first = policy.tree_digest(root)
            (root / "main.tf").write_text("substitution")
            self.assertNotEqual(first, policy.tree_digest(root))

    def test_snapshot_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "link").symlink_to("/etc/passwd")
            with self.assertRaises(policy.PolicyError):
                policy.tree_digest(root)

    def test_duplicate_json_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            path.write_text('{"stage":"create","stage":"grant"}')
            with self.assertRaises(policy.PolicyError):
                policy.load_json(path)


if __name__ == "__main__":
    unittest.main()
