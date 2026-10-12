#!/usr/bin/env python3
"""Read-only, dependency-free policy evaluation. Never executes candidate code.

Offline receipts are integrity bindings, not deployment authorization. A future
live collector must independently verify evidence and plan/JSON correspondence.
"""

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

STAGES = ("create", "bootstrap", "verify", "protect", "grant")
SHA = re.compile(r"^[0-9a-f]{40}$")
MODULE = re.compile(r'^module\.repositories\[("(?:[^"\\]|\\.)*")\]$')
ALLOWED = {
    "github_repository", "github_branch_default", "github_team_repository",
    "github_actions_repository_permissions", "github_workflow_repository_permissions",
    "github_repository_ruleset",
}
FRESHNESS = timedelta(hours=24)


class PolicyError(ValueError):
    """A mandatory safeguard failed."""


def require(condition, message):
    if not condition:
        raise PolicyError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def load_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "Duplicate JSON key")
            result[key] = value
        return result
    path = Path(path)
    require(not path.is_symlink() and path.is_file(), "Input must be a regular file")
    require(path.stat().st_size < 32 * 1024 * 1024, "Input exceeds size limit")
    return json.loads(path.read_text(), object_pairs_hook=unique)


def tree_digest(directory):
    """Hash all snapshot bytes; only VCS/provider caches are excluded.

    Artifacts must be outside the configuration snapshot. Configuration symlinks
    are rejected; VCS metadata and downloaded provider caches are not inputs.
    """
    directory = Path(directory)
    require(directory.is_dir() and not directory.is_symlink(), "Invalid snapshot directory")
    entries = []
    for path in sorted(directory.rglob("*")):
        relative = path.relative_to(directory)
        if relative.parts[0] in (".git", ".terraform"):
            continue
        require(not path.is_symlink(), "Configuration symlinks are unsupported")
        if path.is_file():
            require(path.stat().st_size < 32 * 1024 * 1024, "Snapshot file exceeds size limit")
            entries.append([relative.as_posix(), digest(path.read_bytes())])
    require(entries, "Empty configuration snapshot")
    return digest(canonical(entries))


def tooling_digest(repo):
    return digest(canonical({
        "tooling": repo.get("tooling", {}),
        "required_checks": normalize_checks(repo.get("required_checks", {})),
    }))


def normalize_checks(checks):
    return {key: {"integration_id": value["integration_id"]} if value.get("integration_id") else {} for key, value in checks.items()}


def inventories(value):
    require(isinstance(value, dict) and value.get("schema_version") == 1, "Unsupported inventory schema")
    repos = value.get("repositories")
    require(isinstance(repos, dict), "Missing repository inventory")
    names = []
    for key, repo in repos.items():
        require(isinstance(repo, dict), "Repository inventory entry must be an object")
        require(re.fullmatch(r"[A-Za-z0-9_-]+", key), "Invalid stable inventory key")
        require(repo.get("stage") in STAGES, "Invalid stage")
        require(re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", repo.get("name", "")), "Invalid repository name")
        names.append(repo["name"].lower())
        provisioners = repo.get("provisioning_teams", {})
        developers = repo.get("developer_teams", {})
        require(isinstance(provisioners, dict) and isinstance(developers, dict), "Invalid team maps")
        require(not set(provisioners) & set(developers), "Team classifications overlap")
        require(all(p in ("pull", "triage", "push", "maintain", "admin") for p in [*provisioners.values(), *developers.values()]), "Invalid team permission")
        checks = repo.get("required_checks", {})
        require(isinstance(checks, dict), "Check contract must be a map")
        require(all(isinstance(v, dict) and k.strip() for k, v in checks.items()), "Invalid check contract")
        require(all(set(v) <= {"integration_id"} and (v.get("integration_id") is None or (type(v["integration_id"]) is int and v["integration_id"] > 0)) for v in checks.values()), "Explicit integration IDs must be positive integers")
        if repo["stage"] in ("protect", "grant"):
            require(checks, "Protected stage needs deterministic checks")
    require(len(names) == len(set(names)), "Duplicate repository names")
    return repos


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(parsed.tzinfo is not None, "Timestamp requires timezone")
    return parsed


def observations(repo, prior, observed, now, needs_protection, protected_checks=None):
    require(isinstance(observed, dict), "Missing independently supplied observation")
    require(observed.get("source") == "offline-fixture", "Only clearly marked offline observations are supported locally")
    require(SHA.fullmatch(observed.get("head_sha", "")), "Missing observed content commit")
    require(observed.get("repository_id") == prior.get("repository_id") and type(prior.get("repository_id")) is int and prior["repository_id"] > 0, "Repository identity mismatch")
    require(observed.get("tooling_digest") == tooling_digest(repo), "Tooling/check evidence is stale")
    age = now - timestamp(observed.get("observed_at", ""))
    require(timedelta(0) <= age <= FRESHNESS, "Observation is stale or in the future")
    bootstrap = observed.get("bootstrap", {})
    require(bootstrap.get("mode") in ("template", "exact-source"), "Missing bootstrap provenance")
    require(all(SHA.fullmatch(bootstrap.get(k, "")) for k in ("source_sha", "initial_commit", "merged_commit")), "Incomplete bootstrap commit provenance")
    if STAGES.index(repo["stage"]) >= STAGES.index("verify"):
        checks = observed.get("checks", [])
        for context, contract in repo.get("required_checks", {}).items():
            require(any(c.get("context") == context and c.get("conclusion") == "success" and c.get("head_sha") == observed["head_sha"] and c.get("event") in ("pull_request", "push", "merge_group") and type(c.get("run_id")) is int and c["run_id"] > 0 and (contract.get("integration_id") is None or c.get("integration_id") == contract["integration_id"]) for c in checks), "Missing successful eligible check observation")
        require(observed.get("negative_validation_failed") is True, "Negative validation not demonstrated")
    if needs_protection:
        for flag in ("protection_active", "codeowner_review_required", "force_push_blocked", "deletion_blocked", "dummy_substitution_review_blocked", "untrusted_push_blocked"):
            require(observed.get(flag) is True, "Protection observation failed: " + flag)
        require(normalize_checks(observed.get("effective_required_checks", {})) == normalize_checks(repo.get("required_checks", {}) if protected_checks is None else protected_checks), "Effective checks differ from accepted protection baseline")


def validate_transition(base, record, candidate, evidence, now):
    previous, proposed = inventories(base), inventories(candidate)
    require(record.get("schema_version") == 1 and isinstance(record.get("repositories"), dict), "Missing accepted deployment record")
    require(SHA.fullmatch(record.get("configuration_sha", "")), "Accepted record needs an immutable base revision")
    require(record.get("inventory_digest") == digest(canonical(base)), "Approved inventory differs from accepted baseline")
    require(evidence.get("schema_version") == 1, "Unsupported evidence schema")
    require(set(previous) <= set(proposed), "Ordinary inventory removal is prohibited")
    require(set(record["repositories"]) == set(previous), "Approved base and accepted record inventory disagree")
    for key, repo in proposed.items():
        if key not in previous:
            require(repo["stage"] == "create", "New repository must start at create")
            continue
        old = previous[key]
        accepted = record["repositories"][key]
        require(accepted.get("stage") == old["stage"] and accepted.get("name") == old["name"], "Approved base differs from accepted deployment record")
        require(accepted.get("tooling_digest") == tooling_digest(old), "Accepted tooling baseline mismatch")
        step = STAGES.index(repo["stage"]) - STAGES.index(old["stage"])
        require(step in (0, 1), "Skipped or backward stage transition")
        require(repo["name"] == old["name"], "Rename requires advanced migration")
        require(repo.get("provisioning_teams", {}) == old.get("provisioning_teams", {}), "Provisioning grants require a separate access review")
        require(all(p in ("pull", "triage", "push") for p in repo.get("developer_teams", {}).values()), "Developer stage does not grant administrative roles")
        require(repo.get("initialization", "basic") == old.get("initialization", "basic") and repo.get("template") == old.get("template"), "Creation intent cannot change")
        changed = tooling_digest(repo) != tooling_digest(old)
        if repo["stage"] != "create":
            # Before apply, live enforcement must match the accepted baseline.
            # Candidate checks are validated above; post-apply collection must
            # verify the candidate contract before accepting the new record.
            observations(repo, accepted, evidence.get("repositories", {}).get(key), now,
                         needs_protection=(old["stage"] in ("protect", "grant")),
                         protected_checks=old.get("required_checks", {}))
        if old["stage"] in ("protect", "grant"):
            old_checks = normalize_checks(old.get("required_checks", {}))
            new_checks = normalize_checks(repo.get("required_checks", {}))
            if old_checks != new_checks:
                retained = [context for context in old_checks
                            if context in new_checks and old_checks[context] == new_checks[context]]
                require(retained, "Check migration requires verified overlap")
        if changed and old["stage"] == "grant":
            require(repo["stage"] == "grant", "Tooling updates must retain onboarding stage")


def resources(module):
    for item in module.get("resources", []):
        # planned_values resources inherit their module address from the
        # enclosing child_modules entry; resource_changes carries it directly.
        yield {**item, "module_address": module.get("address", "")}
    for child in module.get("child_modules", []):
        yield from resources(child)


def repo_key(resource):
    match = MODULE.fullmatch(resource.get("module_address", ""))
    require(match is not None, "Unexpected resource module address")
    return json.loads(match.group(1))


def one(blocks, label):
    require(isinstance(blocks, list) and len(blocks) == 1, "Expected one " + label)
    return blocks[0]


def has_unknown(value):
    if isinstance(value, dict):
        return any(has_unknown(v) for v in value.values())
    if isinstance(value, list):
        return any(has_unknown(v) for v in value)
    return value is True


def validate_repository(repo, value):
    require(value.get("name") == repo["name"], "Planned repository identity mismatch")
    methods = repo.get("merge_methods", ["squash"])
    expected = {
        "visibility": repo.get("visibility", "private"),
        "description": repo.get("description", ""),
        "homepage_url": repo.get("homepage_url", ""),
        "delete_branch_on_merge": repo.get("delete_branch_on_merge", True),
        "allow_auto_merge": repo.get("allow_auto_merge", False),
        "allow_update_branch": repo.get("allow_update_branch", True),
        "archived": repo.get("archived", False),
        "archive_on_destroy": True,
        "allow_merge_commit": "merge" in methods,
        "allow_squash_merge": "squash" in methods,
        "allow_rebase_merge": "rebase" in methods,
    }
    features = repo.get("features", {})
    expected.update({"has_" + name: features.get(key, default) for name, key, default in (
        ("issues", "issues", True), ("discussions", "discussions", False),
        ("wiki", "wiki", False), ("projects", "projects", False))})
    require(all(value.get(k) == v for k, v in expected.items()), "Repository settings differ from inventory or are unknown")
    require(sorted(value.get("topics", [])) == sorted(repo.get("topics", [])), "Planned topics differ")


def validate_plan(candidate, base, record, plan):
    repos, previous = inventories(candidate), inventories(base)
    require(plan.get("format_version") == "1.2" and plan.get("complete") is True and plan.get("errored") is False, "Only complete, error-free format 1.2 plans are accepted")
    require(not plan.get("deferred_changes"), "Deferred changes are unsupported")
    require(not plan.get("resource_drift"), "Resolve remote drift before ordinary deployment")
    changes = plan.get("resource_changes")
    require(isinstance(changes, list), "Missing complete resource change list")
    require(plan.get("applyable") is True or (plan.get("applyable") is False and all(item.get("change", {}).get("actions") == ["no-op"] for item in changes)), "Non-applyable plan contains changes")
    for item in changes:
        change = item.get("change", {})
        actions = change.get("actions")
        require(actions in (["no-op"], ["create"], ["update"]), "Destructive, replacement, or unsupported plan action")
        require("importing" not in change, "Use the separately reviewed adoption procedure")
        if item.get("type") == "terraform_data":
            require(item.get("address") == "terraform_data.contract", "Unexpected root guard")
            require(item.get("provider_name") == "terraform.io/builtin/terraform", "Unexpected guard provider")
            continue
        require(item.get("type") in ALLOWED and item.get("mode") == "managed", "Unexpected resource type or data-source execution")
        require(item.get("provider_name") == "registry.terraform.io/integrations/github", "Unexpected GitHub provider")
        key = repo_key(item)
        require(key in repos, "Resource has no inventory owner")
        before, after = change.get("before") or {}, change.get("after") or {}
        unknown = change.get("after_unknown") or {}
        harmless_computed = {"id", "repo_id", "repository_id", "node_id", "ruleset_id", "etag", "html_url", "ssh_clone_url", "http_clone_url", "git_clone_url", "svn_url", "primary_language"}
        if item["type"] == "github_repository" and key not in previous and actions == ["create"]:
            # Newly created external/computed configuration is inspected after
            # creation. It must not be guessed in a pre-creation plan.
            require(after.get("fork") in (None, "", "false", False), "Fork creation is unsupported")
            require(all(after.get(k) in (None, "") for k in ("source_owner", "source_repo")), "Fork source is unsupported")
            harmless_computed.update({"security_and_analysis", "allow_forking", "vulnerability_alerts", "default_branch", "pages",
                                      "full_name", "private", "fork", "source_owner", "source_repo",
                                      "web_commit_signoff_required"})
        require(not has_unknown({k: v for k, v in unknown.items() if k not in harmless_computed}), "Unknown critical policy value")
        if item["type"] == "github_repository":
            if key in previous:
                require(actions != ["create"], "Existing repository cannot be recreated")
                require(before.get("repo_id") == record["repositories"][key].get("repository_id"), "Plan repository ID differs from accepted record")
            require(after.get("archived") is not True, "Archival requires retirement procedure")
            if before:
                require(before.get("visibility") == after.get("visibility"), "Visibility change requires separate security review")
            for field in ("security_and_analysis", "allow_forking", "is_template", "web_commit_signoff_required", "vulnerability_alerts", "default_branch", "pages"):
                if before:
                    require(before.get(field) == after.get(field), "Unmanaged repository field changed: " + field)
        elif item["type"] == "github_repository_ruleset" and before.get("enforcement") == "active":
            require(after.get("enforcement") == "active", "Protection cannot be disabled")
        elif item["type"] in ("github_actions_repository_permissions", "github_workflow_repository_permissions") and before:
            # Ordinary ownership release and policy relaxation need a separate
            # tested migration. V1 allows initial management, then only no-op.
            require(actions == ["no-op"], "Actions policy updates require explicit ownership/security review")
        elif item["type"] == "github_branch_default" and before:
            require(before.get("branch") == after.get("branch"), "Branch rename requires migration")

    root = plan.get("planned_values", {}).get("root_module")
    require(isinstance(root, dict), "Missing complete planned values")
    grouped = {key: [] for key in repos}
    addresses = set()
    guards = []
    for item in resources(root):
        require(item.get("address") not in addresses, "Duplicate planned address")
        addresses.add(item.get("address"))
        if item.get("type") == "terraform_data":
            require(item.get("address") == "terraform_data.contract", "Unexpected root resource")
            require(item.get("provider_name") == "terraform.io/builtin/terraform", "Unexpected guard provider")
            guards.append(item)
            require(item.get("values", {}).get("input") == candidate, "Root plan is not bound to candidate inventory")
            continue
        require(item.get("type") in ALLOWED and item.get("mode") == "managed", "Unexpected planned resource")
        require(item.get("provider_name") == "registry.terraform.io/integrations/github", "Unexpected planned provider")
        key = repo_key(item)
        require(key in grouped, "Unknown planned module")
        grouped[key].append(item)
    require(len(guards) == 1, "Missing root contract")
    require(addresses == {c.get("address") for c in changes}, "Plan changes and planned values do not cover the same resources")
    require(len(changes) == len(addresses), "Duplicate resource change address")
    changes_by_address = {c["address"]: c for c in changes}
    for item in resources(root):
        after = changes_by_address[item["address"]].get("change", {}).get("after") or {}
        require(all(after.get(k) == v for k, v in item.get("values", {}).items()), "Plan change and planned value disagree")
    for key, repo in repos.items():
        items = grouped[key]
        repositories = [r for r in items if r["type"] == "github_repository"]
        validate_repository(repo, one(repositories, "repository")["values"])
        teams = [r for r in items if r["type"] == "github_team_repository"]
        expected_teams = dict(repo.get("provisioning_teams", {}))
        if repo["stage"] == "grant":
            expected_teams.update(repo.get("developer_teams", {}))
        actual_teams = {str(r["values"].get("team_id")): r["values"].get("permission") for r in teams}
        require(len(actual_teams) == len(teams) and actual_teams == expected_teams, "Premature or undeclared team grant")
        for r in teams:
            require(r["values"].get("repository") == repo["name"], "Team grant targets wrong repository")
        rulesets = [r for r in items if r["type"] == "github_repository_ruleset"]
        require(rulesets or repo["stage"] not in ("protect", "grant"), "Protected stages require a ruleset")
        if rulesets:
            rs = one(rulesets, "ruleset")["values"]
            require(rs.get("repository") == repo["name"] and rs.get("target") == "branch", "Wrong ruleset target")
            require(not rs.get("bypass_actors"), "Root baseline does not permit bypass actors")
            condition = one(one(rs.get("conditions"), "conditions").get("ref_name"), "ref_name")
            require(condition.get("include") == ["~DEFAULT_BRANCH"] and condition.get("exclude") == [], "Protection scope changed")
            require(rs.get("enforcement") == ("active" if repo["stage"] in ("protect", "grant") else "disabled"), "Wrong ruleset enforcement")
            rules = one(rs.get("rules"), "rules")
            require(rules.get("deletion") is True and rules.get("non_fast_forward") is True, "Missing deletion/force-push protection")
            pr = one(rules.get("pull_request"), "pull request policy")
            require(pr.get("required_approving_review_count") == 1 and all(pr.get(flag) is True for flag in ("require_code_owner_review", "dismiss_stale_reviews_on_push", "require_last_push_approval", "required_review_thread_resolution")), "Review policy weakened or unknown")
            checks = repo.get("required_checks", {})
            status = rules.get("required_status_checks") or []
            if checks:
                status = one(status, "status policy")
                require(status.get("strict_required_status_checks_policy") is True, "Strict check policy missing")
                # SDK defaults omitted integration IDs to 0; this means no source
                # restriction, not an invented GitHub integration identity.
                actual = normalize_checks({c["context"]: {"integration_id": c.get("integration_id")} for c in status.get("required_check", [])})
                require(actual == normalize_checks(checks), "Planned check contract differs")
            else:
                require(not status, "Unexpected status policy")
        for kind, field in (("github_actions_repository_permissions", "actions_policy"), ("github_workflow_repository_permissions", "workflow_permissions"), ("github_branch_default", "default_branch")):
            matches = [r for r in items if r["type"] == kind]
            configured = repo.get(field)
            require(len(matches) == (1 if configured is not None else 0), "Optional component ownership mismatch")
            if matches:
                value = matches[0]["values"]
                require(value.get("repository") == repo["name"], "Optional policy targets wrong repository")
                if field == "default_branch":
                    require(value.get("branch") == configured and value.get("rename") == repo.get("rename_default_branch", False), "Branch policy mismatch")
                elif field == "workflow_permissions":
                    require(all(value.get(k) == configured[k] for k in ("default_workflow_permissions", "can_approve_pull_request_reviews")), "Workflow policy mismatch")
                else:
                    require(all(value.get(k) == configured[k] for k in ("enabled", "allowed_actions", "sha_pinning_required")), "Actions policy mismatch")
                    actual_config = value.get("allowed_actions_config") or []
                    expected_config = [configured["selected"]] if configured.get("selected") is not None else []
                    # Provider and module schemas model patterns as a set.
                    def normalize_allowlist(blocks):
                        return [{**block, "patterns_allowed": sorted(set(block.get("patterns_allowed", [])))}
                                for block in blocks]
                    require(normalize_allowlist(actual_config) == normalize_allowlist(expected_config), "Actions allowlist mismatch")


def evaluate(base, record, candidate, evidence, plan, now):
    validate_transition(base, record, candidate, evidence, now)
    validate_plan(candidate, base, record, plan)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("base", "base-sha", "record", "candidate", "evidence", "plan-json", "saved-plan", "configuration", "receipt", "configuration-sha", "policy-sha"):
        parser.add_argument("--" + option, required=True)
    args = parser.parse_args()
    try:
        require(all(SHA.fullmatch(value) for value in (args.configuration_sha, args.policy_sha, args.base_sha)), "Commit arguments must be full SHAs")
        config = Path(args.configuration).resolve()
        candidate_path = Path(args.candidate).resolve()
        require(candidate_path == config / "repositories.json", "Candidate inventory must be inside the bound snapshot")
        for artifact in (args.plan_json, args.saved_plan, args.receipt):
            require(not Path(artifact).resolve().is_relative_to(config), "Artifacts must be outside configuration snapshot")
        inputs = {key: load_json(getattr(args, key.replace("-", "_"))) for key in ("base", "record", "candidate", "evidence", "plan-json")}
        require(inputs["record"].get("configuration_sha") == args.base_sha, "Record does not match trusted base revision")
        saved = Path(args.saved_plan)
        require(saved.is_file() and not saved.is_symlink(), "Saved plan must be a regular file")
        now = datetime.now(timezone.utc)
        evaluate(inputs["base"], inputs["record"], inputs["candidate"], inputs["evidence"], inputs["plan-json"], now)
        receipt = {
            "schema_version": 1, "result": "pass", "mode": "offline-fixture",
            "deployment_authorized": False,
            "configuration_sha": args.configuration_sha, "policy_sha": args.policy_sha,
            "base_sha": args.base_sha,
            "policy_implementation_digest": digest(Path(__file__).read_bytes()),
            "configuration_digest": tree_digest(config),
            "saved_plan_digest": digest(saved.read_bytes()),
            "plan_json_digest": digest(Path(args.plan_json).read_bytes()),
            "input_digests": {key: digest(canonical(value)) for key, value in inputs.items()},
            "evaluated_at": now.isoformat(),
        }
        Path(args.receipt).write_text(json.dumps(receipt, indent=2) + "\n")
        print("Offline policy passed; receipt is NOT deployment authorization.")
        return 0
    except (PolicyError, ValueError, TypeError, KeyError, OSError) as error:
        # Never print candidate data, plan values, or exception details from JSON.
        print("Policy validation failed: " + (str(error) if isinstance(error, PolicyError) else type(error).__name__), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
