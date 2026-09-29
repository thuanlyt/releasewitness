from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import tomllib

import relwit.cli as relwit


class RelWitCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_root = relwit.ROOT
        self.original_registry = relwit.REGISTRY
        self.original_config = relwit.CONFIG
        self.original_lock = relwit.LOCK
        relwit.ROOT = Path(self.temp_dir.name)
        relwit.REGISTRY = relwit.ROOT / "work" / "registry.json"
        relwit.CONFIG = relwit.ROOT / "relwit.config.json"
        relwit.LOCK = relwit.ROOT / "work" / ".state.lock"
        relwit.ensure_layout()
        (relwit.ROOT / "knowledge" / "INDEX.md").write_text("# ReleaseWitness context index\ncompact", encoding="utf-8")
        (relwit.ROOT / "knowledge" / "project-map.md").write_text("# project map\nsmall", encoding="utf-8")

    def tearDown(self) -> None:
        relwit.ROOT = self.original_root
        relwit.REGISTRY = self.original_registry
        relwit.CONFIG = self.original_config
        relwit.LOCK = self.original_lock
        self.temp_dir.cleanup()

    def invoke(self, *arguments: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = relwit.main(list(arguments))
        return code, stdout.getvalue(), stderr.getvalue()

    def new_task(self, title: str, scope: str, *dependencies: str) -> str:
        arguments = [
            "task",
            "new",
            "--title",
            title,
            "--level",
            "L1",
            "--owner",
            "worker",
            "--scope",
            scope,
            "--acceptance",
            "behavior passes",
        ]
        if dependencies:
            arguments.extend(["--depends-on", *dependencies])
        code, output, error = self.invoke(*arguments)
        self.assertEqual((code, error), (0, ""))
        return output.strip()

    def register_worker(self, agent_id: str = "frontend", scope: str = "src/frontend") -> None:
        code, _, error = self.invoke("agent", "register", "--id", agent_id, "--role", "worker", "--scope", scope)
        self.assertEqual((code, error), (0, ""))

    def register_reviewer(self, agent_id: str = "reviewer", scope: str = ".") -> None:
        code, _, error = self.invoke("agent", "register", "--id", agent_id, "--role", "reviewer", "--scope", scope)
        self.assertEqual((code, error), (0, ""))

    def test_takeover_lineage_preserves_failure_and_scope(self) -> None:
        self.register_worker("blocked-worker", "src")
        predecessor = self.new_task("Preserve the failed task", "src/old.py")
        code, _, error = self.invoke("task", "claim", predecessor, "--agent", "blocked-worker")
        self.assertEqual((code, error), (0, ""))
        code, _, error = self.invoke(
            "task",
            "report",
            predecessor,
            "--agent",
            "blocked-worker",
            "--result",
            "blocked",
            "--summary",
            "The first attempt hit a preserved blocker",
            "--next-action",
            "Create a bounded takeover after review",
            "--file",
            "src/old.py",
            "--check",
            "blocker recorded",
        )
        self.assertEqual((code, error), (0, ""))

        code, output, error = self.invoke(
            "task",
            "new",
            "--title",
            "Take over the failed task",
            "--level",
            "L2",
            "--owner",
            "supervisor",
            "--scope",
            "src/recovery.py",
            "--scope",
            "tests/recovery.py",
            "--acceptance",
            "recovery behavior passes",
            "--supersedes",
            predecessor,
            "--takeover-reason",
            "Quota fallback after preserved failure",
        )
        self.assertEqual((code, error), (0, ""))
        successor = output.strip()
        registry = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        predecessor_item = registry["items"][predecessor]
        successor_item = registry["items"][successor]
        self.assertEqual(predecessor_item["status"], "blocked")
        self.assertEqual(predecessor_item["superseded_by"], successor)
        self.assertEqual(successor_item["supersedes"], predecessor)
        self.assertEqual(successor_item["takeover_reason"], "Quota fallback after preserved failure")
        self.assertEqual(successor_item["scope"], ["src/recovery.py", "tests/recovery.py"])
        self.assertIsNone(successor_item["superseded_by"])
        predecessor_text = (relwit.ROOT / "work" / "items" / f"{predecessor}.md").read_text(encoding="utf-8")
        successor_text = (relwit.ROOT / "work" / "items" / f"{successor}.md").read_text(encoding="utf-8")
        self.assertIn(f"superseded_by: \"{successor}\"", predecessor_text)
        self.assertIn(f"supersedes: \"{predecessor}\"", successor_text)
        self.assertIn('takeover_reason: "Quota fallback after preserved failure"', successor_text)
        self.assertIn(f"superseded by {successor}: Quota fallback after preserved failure", predecessor_text)

        code, _, error = self.invoke("task", "claim", predecessor, "--agent", "blocked-worker")
        self.assertEqual(code, 2)
        self.assertIn(f"was superseded by {successor}", error)
        code, _, error = self.invoke("task", "update", predecessor, "--status", "planned", "--agent", "supervisor")
        self.assertEqual(code, 2)
        self.assertIn(f"was superseded by {successor}", error)
        unchanged = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))["items"][predecessor]
        self.assertEqual(unchanged["status"], "blocked")
        self.assertEqual(unchanged["superseded_by"], successor)

    def test_takeover_lineage_rejects_unsafe_predecessors_atomically(self) -> None:
        self.register_worker("lineage-worker", ".")
        active = self.new_task("Active predecessor", "src/active.py")
        code, _, error = self.invoke("task", "claim", active, "--agent", "lineage-worker")
        self.assertEqual((code, error), (0, ""))
        before = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        code, _, error = self.invoke(
            "task",
            "new",
            "--title",
            "Must reject active takeover",
            "--level",
            "L1",
            "--owner",
            "supervisor",
            "--scope",
            "src/active-recovery.py",
            "--acceptance",
            "active safety passes",
            "--supersedes",
            active,
            "--takeover-reason",
            "Do not fork active work",
        )
        self.assertEqual(code, 2)
        self.assertIn("only blocked or cancelled", error)
        after = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(after["items"], before["items"])

        self.register_worker("done-worker", ".")
        self.register_reviewer("lineage-reviewer")
        done = self.new_task("Done predecessor", "src/done.py")
        code, _, error = self.invoke("task", "claim", done, "--agent", "done-worker")
        self.assertEqual((code, error), (0, ""))
        code, _, error = self.invoke(
            "task",
            "report",
            done,
            "--agent",
            "done-worker",
            "--result",
            "completed",
            "--summary",
            "Done predecessor implementation",
            "--next-action",
            "Review",
            "--file",
            "src/done.py",
            "--check",
            "unit test: pass",
        )
        self.assertEqual((code, error), (0, ""))
        code, _, error = self.invoke("task", "update", done, "--status", "needs_review", "--agent", "lineage-reviewer")
        self.assertEqual((code, error), (0, ""))
        code, _, error = self.invoke(
            "task",
            "evidence",
            done,
            "--kind",
            "review",
            "--agent",
            "lineage-reviewer",
            "--value",
            "Done predecessor reviewed",
        )
        self.assertEqual((code, error), (0, ""))
        code, _, error = self.invoke("task", "update", done, "--status", "done", "--agent", "lineage-reviewer")
        self.assertEqual((code, error), (0, ""))
        before = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        code, _, error = self.invoke(
            "task",
            "new",
            "--title",
            "Must reject done takeover",
            "--level",
            "L1",
            "--owner",
            "supervisor",
            "--scope",
            "src/done-recovery.py",
            "--acceptance",
            "done safety passes",
            "--supersedes",
            done,
            "--takeover-reason",
            "Do not reopen done work",
        )
        self.assertEqual(code, 2)
        self.assertIn("only blocked or cancelled", error)
        after = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(after["items"], before["items"])

        before = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        code, _, error = self.invoke(
            "task",
            "new",
            "--title",
            "Must reject missing predecessor",
            "--level",
            "L1",
            "--owner",
            "supervisor",
            "--scope",
            "src/missing-recovery.py",
            "--acceptance",
            "missing safety passes",
            "--supersedes",
            "RW-9999",
            "--takeover-reason",
            "No unknown predecessor",
        )
        self.assertEqual(code, 2)
        self.assertIn("unknown superseded task", error)
        after = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(after["items"], before["items"])

        blocked = self.new_task("Missing reason predecessor", "src/missing-reason.py")
        data = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        data["items"][blocked]["status"] = "blocked"
        relwit.save_registry(data)
        before = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        code, _, error = self.invoke(
            "task",
            "new",
            "--title",
            "Must reject missing reason",
            "--level",
            "L1",
            "--owner",
            "supervisor",
            "--scope",
            "src/missing-reason-recovery.py",
            "--acceptance",
            "reason safety passes",
            "--supersedes",
            blocked,
        )
        self.assertEqual(code, 2)
        self.assertIn("--takeover-reason must be non-empty", error)
        after = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(after["items"], before["items"])

    def test_takeover_lineage_validator_and_legacy_compatibility(self) -> None:
        legacy = self.new_task("Legacy task", "src/legacy.py")
        data = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        for field in ("supersedes", "superseded_by", "takeover_reason"):
            data["items"][legacy].pop(field, None)
        legacy_errors: list[str] = []
        relwit.validate_registry(data, legacy_errors)
        self.assertFalse([error for error in legacy_errors if "supersed" in error or "takeover" in error])

        data["items"][legacy]["takeover_reason"] = "orphan reason"
        malformed_reason_errors: list[str] = []
        relwit.validate_registry(data, malformed_reason_errors)
        self.assertIn(f"{legacy} takeover_reason requires supersedes", malformed_reason_errors)

        predecessor = self.new_task("Validator predecessor", "src/predecessor.py")
        data = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        data["items"][predecessor]["status"] = "blocked"
        relwit.save_registry(data)
        code, output, error = self.invoke(
            "task",
            "new",
            "--title",
            "Validator successor",
            "--level",
            "L1",
            "--owner",
            "supervisor",
            "--scope",
            "src/successor.py",
            "--acceptance",
            "lineage validates",
            "--supersedes",
            predecessor,
            "--takeover-reason",
            "Recovery lineage is explicit",
        )
        self.assertEqual((code, error), (0, ""))
        successor = output.strip()
        valid = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        valid_errors: list[str] = []
        relwit.validate_registry(valid, valid_errors)
        self.assertFalse([error for error in valid_errors if "supersed" in error or "takeover" in error])

        broken = copy.deepcopy(valid)
        broken["items"][successor]["supersedes"] = "RW-9999"
        errors: list[str] = []
        relwit.validate_registry(broken, errors)
        self.assertIn(f"{successor} references missing superseded task RW-9999", errors)

        broken = copy.deepcopy(valid)
        broken["items"][predecessor]["superseded_by"] = None
        errors = []
        relwit.validate_registry(broken, errors)
        self.assertIn(f"{successor} supersedes {predecessor} without reciprocal superseded_by", errors)

        broken = copy.deepcopy(valid)
        broken["items"][successor]["takeover_reason"] = "line one\nline two"
        errors = []
        relwit.validate_registry(broken, errors)
        self.assertIn(f"{successor} takeover_reason must be a single line", errors)

    def test_dependency_and_done_requirements(self) -> None:
        self.register_worker("worker-1", ".")
        self.register_worker("worker-2", ".")
        self.register_reviewer()
        first = self.new_task("First", "src/first.py")
        second = self.new_task("Second", "src/second.py", first)

        code, _, error = self.invoke("task", "claim", second, "--agent", "worker-2")
        self.assertEqual(code, 2)
        self.assertIn("unfinished dependencies", error)

        code, _, error = self.invoke("task", "claim", first, "--agent", "worker-1")
        self.assertEqual((code, error), (0, ""))
        item_text = (relwit.ROOT / "work" / "items" / f"{first}.md").read_text(encoding="utf-8")
        self.assertIn("status: in_progress", item_text)
        self.assertIn("assigned_to: worker-1", item_text)
        code, _, error = self.invoke("task", "update", first, "--status", "done", "--agent", "worker-1")
        self.assertEqual(code, 2)
        self.assertIn("not authorized for review actions", error)

        code, _, error = self.invoke("task", "evidence", first, "--kind", "test", "--value", "unit test: pass")
        self.assertEqual((code, error), (0, ""))
        code, _, error = self.invoke(
            "task",
            "report",
            first,
            "--agent",
            "worker-1",
            "--result",
            "completed",
            "--summary",
            "Implemented first task",
            "--next-action",
            "Review and QA",
            "--file",
            "src/first.py",
            "--check",
            "unit test: pass",
        )
        self.assertEqual((code, error), (0, ""))
        code, _, error = self.invoke("task", "update", first, "--status", "needs_review", "--agent", "worker-1")
        self.assertEqual(code, 2)
        self.assertIn("not authorized for review actions", error)
        code, _, error = self.invoke(
            "task", "evidence", first, "--kind", "review", "--agent", "worker-1", "--value", "worker self-review"
        )
        self.assertEqual(code, 2)
        self.assertIn("not authorized for review actions", error)
        code, _, error = self.invoke(
            "task", "evidence", first, "--kind", "review", "--agent", "reviewer", "--value", "review pass"
        )
        self.assertEqual((code, error), (0, ""))
        code, _, error = self.invoke("task", "update", first, "--status", "needs_review", "--agent", "reviewer")
        self.assertEqual((code, error), (0, ""))
        code, _, error = self.invoke("task", "update", first, "--status", "done", "--agent", "reviewer")
        self.assertEqual((code, error), (0, ""))
        item_text = (relwit.ROOT / "work" / "items" / f"{first}.md").read_text(encoding="utf-8")
        self.assertIn("status: done", item_text)
        code, _, error = self.invoke("task", "claim", second, "--agent", "worker-2")
        self.assertEqual((code, error), (0, ""))

    def test_overlapping_active_writer_is_rejected(self) -> None:
        self.register_worker("worker-1", ".")
        self.register_worker("worker-2", ".")
        first = self.new_task("Parent scope", "src")
        second = self.new_task("Nested scope", "src/feature.py")
        code, _, error = self.invoke("task", "claim", first, "--agent", "worker-1")
        self.assertEqual((code, error), (0, ""))
        code, _, error = self.invoke("task", "claim", second, "--agent", "worker-2")
        self.assertEqual(code, 2)
        self.assertIn("scope conflicts", error)

    def test_repeated_task_scopes_are_preserved(self) -> None:
        code, output, error = self.invoke(
            "task",
            "new",
            "--title",
            "Multiple owned paths",
            "--level",
            "L2",
            "--owner",
            "worker",
            "--scope",
            "src/api.py",
            "--scope",
            "tests/test_api.py",
            "--acceptance",
            "both paths are covered",
        )
        self.assertEqual((code, error), (0, ""))
        registry = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(
            registry["items"][output.strip()]["scope"],
            ["src/api.py", "tests/test_api.py"],
        )

    def test_task_scope_can_be_extended_through_update(self) -> None:
        self.register_worker("worker-1", ".")
        task_id = self.new_task("Extend owned paths", "src/api.py")
        code, _, error = self.invoke(
            "task",
            "claim",
            task_id,
            "--agent",
            "worker-1",
        )
        self.assertEqual((code, error), (0, ""))
        code, _, error = self.invoke(
            "task",
            "update",
            task_id,
            "--status",
            "in_progress",
            "--agent",
            "worker-1",
            "--scope",
            "tests/test_api.py",
        )
        self.assertEqual((code, error), (0, ""))
        registry = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(
            registry["items"][task_id]["scope"],
            ["src/api.py", "tests/test_api.py"],
        )

    def test_scope_overlap_uses_boundaries_and_windows_case_rules(self) -> None:
        self.assertFalse(relwit.scope_overlaps("src/api.py", "src/web.py"))
        self.assertTrue(relwit.scope_overlaps("src", "src/web.py"))
        expected_case_match = os.path.normcase("src") == os.path.normcase("SRC")
        self.assertEqual(relwit.scope_overlaps("src", "SRC"), expected_case_match)

    def test_task_claim_requires_registered_agent(self) -> None:
        task_id = self.new_task("Registered worker only", "src/claim.py")
        code, _, error = self.invoke("task", "claim", task_id, "--agent", "unregistered")
        self.assertEqual(code, 2)
        self.assertIn("unknown registered agent", error)

        self.register_worker("worker-1", ".")
        code, _, error = self.invoke("task", "claim", task_id, "--agent", "worker-1")
        self.assertEqual((code, error), (0, ""))

    def test_task_claim_requires_available_agent_and_capacity(self) -> None:
        self.register_worker("worker-1", ".")
        task_id = self.new_task("Claim availability", "src/claim-availability.py")

        for status in ("paused", "offline", "busy"):
            code, _, error = self.invoke("agent", "status", "worker-1", "--status", status)
            self.assertEqual((code, error), (0, ""))
            registry_before = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
            code, _, error = self.invoke("task", "claim", task_id, "--agent", "worker-1")
            self.assertEqual(code, 2)
            self.assertIn("expected available", error)
            registry_after = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
            self.assertEqual(registry_after["items"][task_id], registry_before["items"][task_id])

        code, _, error = self.invoke("agent", "status", "worker-1", "--status", "available")
        self.assertEqual((code, error), (0, ""))
        code, _, error = self.invoke("task", "claim", task_id, "--agent", "worker-1")
        self.assertEqual((code, error), (0, ""))

        capacity_id = self.new_task("Claim capacity", "src/claim-capacity.py")
        registry_before = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        code, _, error = self.invoke("task", "claim", capacity_id, "--agent", "worker-1")
        self.assertEqual(code, 2)
        self.assertIn("max_active capacity", error)
        registry_after = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(registry_after["items"][capacity_id], registry_before["items"][capacity_id])

    def test_worker_pull_requires_available_agent(self) -> None:
        self.register_worker("worker-1", "src")
        task_id = self.new_task("Pull availability", "src/pull-availability.py")
        code, _, error = self.invoke("supervisor", "dispatch")
        self.assertEqual((code, error), (0, ""))

        code, _, error = self.invoke("agent", "status", "worker-1", "--status", "paused")
        self.assertEqual((code, error), (0, ""))
        registry_before = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        code, _, error = self.invoke("worker", "pull", "--agent", "worker-1")
        self.assertEqual(code, 2)
        self.assertIn("expected available", error)
        registry_after = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(registry_after["items"][task_id], registry_before["items"][task_id])

        code, _, error = self.invoke("agent", "status", "worker-1", "--status", "available")
        self.assertEqual((code, error), (0, ""))
        code, _, error = self.invoke("worker", "pull", "--agent", "worker-1")
        self.assertEqual((code, error), (0, ""))
        registry = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(registry["items"][task_id]["status"], "in_progress")

    def test_task_claim_rejects_scope_and_capability_mismatch(self) -> None:
        self.register_worker("scoped-worker", "src/allowed")
        outside_id = self.new_task("Claim scope mismatch", "src/other/task.py")
        code, _, error = self.invoke("task", "claim", outside_id, "--agent", "scoped-worker")
        self.assertEqual(code, 2)
        self.assertIn("outside the agent scope", error)
        registry = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(registry["items"][outside_id]["status"], "planned")

        self.register_worker("capability-worker", ".")
        code, output, error = self.invoke(
            "task",
            "new",
            "--title",
            "Claim capability mismatch",
            "--level",
            "L1",
            "--owner",
            "worker",
            "--scope",
            "src/capability.py",
            "--capability",
            "browser-qa",
            "--acceptance",
            "capability is present",
        )
        self.assertEqual((code, error), (0, ""))
        capability_id = output.strip()
        code, _, error = self.invoke("task", "claim", capability_id, "--agent", "capability-worker")
        self.assertEqual(code, 2)
        self.assertIn("lacks required capabilities", error)
        registry = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(registry["items"][capability_id]["status"], "planned")

    def test_agent_role_boundaries_are_enforced(self) -> None:
        self.register_reviewer()
        task_id = self.new_task("Review role cannot claim", "src/role-boundary.py")
        code, _, error = self.invoke("task", "claim", task_id, "--agent", "reviewer")
        self.assertEqual(code, 2)
        self.assertIn("not authorized to claim work", error)
        registry = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(registry["items"][task_id]["status"], "planned")

        code, _, error = self.invoke("agent", "register", "--id", "unknown-role", "--role", "rogue", "--scope", ".")
        self.assertEqual(code, 2)
        self.assertIn("invalid agent role", error)

        config = relwit.load_config()
        config["agents"][0]["role"] = "rogue"
        relwit.save_config(config)
        code, output, error = self.invoke("validate")
        self.assertEqual(code, 1)
        self.assertIn("invalid role for agent reviewer", output)

    def test_scope_traversal_and_out_of_scope_reports_are_rejected(self) -> None:
        code, _, error = self.invoke(
            "task",
            "new",
            "--title",
            "Reject traversal",
            "--level",
            "L1",
            "--owner",
            "worker",
            "--scope",
            "../outside",
            "--acceptance",
            "safe scope",
        )
        self.assertEqual(code, 2)
        self.assertIn("parent traversal", error)

        self.register_worker("worker-1", "src")
        task_id = self.new_task("Report only owned files", "src")
        code, _, error = self.invoke("task", "claim", task_id, "--agent", "worker-1")
        self.assertEqual((code, error), (0, ""))
        code, _, error = self.invoke(
            "task",
            "report",
            task_id,
            "--agent",
            "worker-1",
            "--result",
            "completed",
            "--summary",
            "attempted report",
            "--next-action",
            "review",
            "--file",
            "tests/secret.py",
        )
        self.assertEqual(code, 2)
        self.assertIn("outside task scope", error)
        registry = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(registry["items"][task_id]["status"], "in_progress")
        self.assertEqual(registry["items"][task_id]["reports"], [])

    def test_worker_pull_rejects_unsafe_assignment_without_state_change(self) -> None:
        self.register_worker("frontend", "src/frontend")
        task_id = self.new_task("Safe assignment path", "src/frontend/app.py")
        code, _, error = self.invoke("supervisor", "dispatch")
        self.assertEqual((code, error), (0, ""))
        registry = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        registry["items"][task_id]["assignment_path"] = "../outside.md"
        relwit.REGISTRY.write_text(json.dumps(registry), encoding="utf-8")

        code, _, error = self.invoke("worker", "pull", "--agent", "frontend")
        self.assertEqual(code, 2)
        self.assertIn("leaves project root", error)
        registry = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(registry["items"][task_id]["status"], "assigned")

    def test_done_requires_explicit_review_gate(self) -> None:
        self.register_worker("worker-1", ".")
        self.register_reviewer()
        task_id = self.new_task("Review before done", "src/review.py")
        code, _, error = self.invoke("task", "claim", task_id, "--agent", "worker-1")
        self.assertEqual((code, error), (0, ""))
        code, _, error = self.invoke("task", "evidence", task_id, "--kind", "test", "--value", "pass")
        self.assertEqual((code, error), (0, ""))
        code, _, error = self.invoke("task", "update", task_id, "--status", "done", "--agent", "worker-1")
        self.assertEqual(code, 2)
        self.assertIn("not authorized for review actions", error)
        code, _, error = self.invoke("task", "update", task_id, "--status", "needs_review", "--agent", "worker-1")
        self.assertEqual(code, 2)
        self.assertIn("not authorized for review actions", error)
        code, _, error = self.invoke(
            "task", "evidence", task_id, "--kind", "review", "--agent", "worker-1", "--value", "worker self-review"
        )
        self.assertEqual(code, 2)
        self.assertIn("not authorized for review actions", error)
        code, _, error = self.invoke(
            "task",
            "report",
            task_id,
            "--agent",
            "worker-1",
            "--result",
            "completed",
            "--summary",
            "Implementation complete",
            "--next-action",
            "Review",
            "--file",
            "src/review.py",
            "--check",
            "unit test: pass",
        )
        self.assertEqual((code, error), (0, ""))
        code, _, error = self.invoke("task", "update", task_id, "--status", "needs_review", "--agent", "reviewer")
        self.assertEqual((code, error), (0, ""))
        code, _, error = self.invoke("task", "update", task_id, "--status", "done", "--agent", "reviewer")
        self.assertEqual(code, 2)
        self.assertIn("non-empty review evidence", error)
        code, _, error = self.invoke(
            "task", "evidence", task_id, "--kind", "review", "--agent", "reviewer", "--value", "review pass"
        )
        self.assertEqual((code, error), (0, ""))
        code, _, error = self.invoke("task", "update", task_id, "--status", "done", "--agent", "reviewer")
        self.assertEqual((code, error), (0, ""))
        registry_before = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        for status in ("planned", "blocked", "cancelled"):
            code, _, error = self.invoke("task", "update", task_id, "--status", status, "--agent", "worker-1")
            self.assertEqual(code, 2)
            self.assertIn("tasks are terminal", error)
            registry = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
            self.assertEqual(registry["items"][task_id], registry_before["items"][task_id])

        cancelled_id = self.new_task("Terminal cancellation", "src/cancelled.py")
        code, _, error = self.invoke("task", "update", cancelled_id, "--status", "cancelled", "--agent", "reviewer")
        self.assertEqual((code, error), (0, ""))
        code, _, error = self.invoke("task", "update", cancelled_id, "--status", "planned", "--agent", "reviewer")
        self.assertEqual(code, 2)
        self.assertIn("tasks are terminal", error)
        registry = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(registry["items"][cancelled_id]["status"], "cancelled")

    def test_empty_review_evidence_is_rejected(self) -> None:
        self.register_worker("worker-1", ".")
        self.register_reviewer()
        task_id = self.new_task("Reject empty review", "src/empty-review.py")
        code, _, error = self.invoke("task", "claim", task_id, "--agent", "worker-1")
        self.assertEqual((code, error), (0, ""))
        code, _, error = self.invoke(
            "task",
            "report",
            task_id,
            "--agent",
            "worker-1",
            "--result",
            "completed",
            "--summary",
            "Implementation complete",
            "--next-action",
            "Review",
            "--file",
            "src/empty-review.py",
            "--check",
            "unit test: pass",
        )
        self.assertEqual((code, error), (0, ""))
        code, _, error = self.invoke(
            "task", "evidence", task_id, "--kind", "review", "--agent", "reviewer", "--value", "   "
        )
        self.assertEqual(code, 2)
        self.assertIn("evidence kind and value cannot be empty", error)
        registry = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(registry["items"][task_id]["status"], "reported")
        self.assertEqual(
            [entry for entry in registry["items"][task_id]["evidence"] if entry.get("kind") == "review"], []
        )

    def test_review_requires_worker_report(self) -> None:
        self.register_worker("worker-1", ".")
        self.register_reviewer()
        task_id = self.new_task("Report before review", "src/report-before-review.py")
        code, _, error = self.invoke("task", "claim", task_id, "--agent", "worker-1")
        self.assertEqual((code, error), (0, ""))
        code, _, error = self.invoke("task", "evidence", task_id, "--kind", "test", "--value", "unit test: pass")
        self.assertEqual((code, error), (0, ""))

        code, _, error = self.invoke("task", "update", task_id, "--status", "needs_review", "--agent", "reviewer")
        self.assertEqual(code, 2)
        self.assertIn("a task must be reported before review", error)
        registry = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(registry["items"][task_id]["status"], "in_progress")
        self.assertEqual(
            [entry for entry in registry["items"][task_id]["evidence"] if entry.get("kind") == "review"], []
        )

        code, _, error = self.invoke(
            "task",
            "report",
            task_id,
            "--agent",
            "worker-1",
            "--result",
            "completed",
            "--summary",
            "Implementation complete",
            "--next-action",
            "Review",
            "--file",
            "src/report-before-review.py",
            "--check",
            "unit test: pass",
        )
        self.assertEqual((code, error), (0, ""))
        code, _, error = self.invoke("task", "update", task_id, "--status", "needs_review", "--agent", "reviewer")
        self.assertEqual((code, error), (0, ""))

    def test_supervisor_ingest_authenticates_reports_and_filters_files(self) -> None:
        self.register_worker("worker-1", "src")
        task_id = self.new_task("Authenticate worker report", "src")
        code, _, error = self.invoke("task", "claim", task_id, "--agent", "worker-1")
        self.assertEqual((code, error), (0, ""))

        reports_dir = relwit.ROOT / "work" / "reports" / "inbox"
        spoofed = reports_dir / "spoofed.md"
        spoofed.write_text(
            "---\n"
            "type: relwit-worker-report\n"
            f"task_id: {task_id}\n"
            "agent: attacker\n"
            "result: completed\n"
            "files: [\"src/owned.py\"]\n"
            "---\n\nspoofed\n",
            encoding="utf-8",
        )
        code, output, error = self.invoke("supervisor", "ingest")
        self.assertEqual((code, error), (0, ""))
        self.assertEqual(output.strip(), "no new reports")
        registry = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(registry["items"][task_id]["status"], "in_progress")
        self.assertEqual(registry["items"][task_id]["reports"], [])
        spoofed.unlink()

        valid = reports_dir / "valid.md"
        valid.write_text(
            "---\n"
            "type: relwit-worker-report\n"
            f"task_id: {task_id}\n"
            "agent: worker-1\n"
            "result: completed\n"
            "files: [\"src/owned.py\", \"tests/secret.py\", \"../outside.py\"]\n"
            "---\n\nvalid\n",
            encoding="utf-8",
        )
        code, _, error = self.invoke("supervisor", "ingest")
        self.assertEqual((code, error), (0, ""))
        registry = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        item = registry["items"][task_id]
        self.assertEqual(item["status"], "reported")
        self.assertEqual(item["files"], ["src/owned.py"])
        self.assertTrue(any(entry["kind"] == "warning" for entry in item["evidence"]))
        legacy_report = next(entry for entry in item["evidence"] if entry["kind"] == "worker-report")
        self.assertEqual(legacy_report["provenance"], "legacy")
        self.assertEqual(legacy_report["source"], "work/reports/inbox/valid.md")

    def test_supervisor_ingest_ignores_unreadable_reports_without_state_change(self) -> None:
        self.register_worker("worker-1", "src")
        task_id = self.new_task("Ignore unreadable report", "src")
        code, _, error = self.invoke("task", "claim", task_id, "--agent", "worker-1")
        self.assertEqual((code, error), (0, ""))
        unreadable = relwit.ROOT / "work" / "reports" / "inbox" / "unreadable.md"
        unreadable.write_bytes(b"\xff\xfe\xfa")
        code, output, error = self.invoke("supervisor", "ingest")
        self.assertEqual((code, error), (0, ""))
        self.assertEqual(output.strip(), "no new reports")
        registry = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(registry["items"][task_id]["status"], "in_progress")
        self.assertEqual(registry["items"][task_id]["reports"], [])

    def test_agent_list_skips_malformed_roster_entry_without_traceback(self) -> None:
        self.register_worker("worker-1", ".")
        config = relwit.load_config()
        config["agents"].insert(0, None)
        relwit.save_config(config)
        code, output, error = self.invoke("agent", "list")
        self.assertEqual((code, error), (0, ""))
        self.assertIn("worker-1", output)
        self.assertNotIn("Traceback", output + error)

    def test_worker_pull_rejects_non_string_assignment_without_state_change(self) -> None:
        self.register_worker("worker-1", "src")
        task_id = self.new_task("Typed assignment path", "src/assignment.py")
        code, _, error = self.invoke("supervisor", "dispatch")
        self.assertEqual((code, error), (0, ""))
        registry = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        registry["items"][task_id]["assignment_path"] = ["invalid"]
        relwit.REGISTRY.write_text(json.dumps(registry), encoding="utf-8")

        code, _, error = self.invoke("worker", "pull", "--agent", "worker-1")
        self.assertEqual(code, 2)
        self.assertIn("invalid assignment path", error)
        registry = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(registry["items"][task_id]["status"], "assigned")

    def test_worker_pull_rejects_unreadable_assignment_without_state_change(self) -> None:
        self.register_worker("worker-1", "src")
        task_id = self.new_task("Readable assignment", "src/readable.py")
        code, _, error = self.invoke("supervisor", "dispatch")
        self.assertEqual((code, error), (0, ""))
        registry = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        assignment = relwit.ROOT / registry["items"][task_id]["assignment_path"]
        assignment.write_bytes(b"\xff\xfe\xfa")

        code, _, error = self.invoke("worker", "pull", "--agent", "worker-1")
        self.assertEqual(code, 2)
        self.assertIn("assignment file cannot be read", error)
        registry = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(registry["items"][task_id]["status"], "assigned")

    def reporting_runner(self, exit_code: int = 0) -> list[str]:
        runner_code = (
            "import pathlib, subprocess, sys\n"
            "if int(sys.argv[6]): raise SystemExit(int(sys.argv[6]))\n"
            "cli = sys.argv[3]\n"
            "cmd = [sys.executable, cli, '--root', str(pathlib.Path.cwd()), 'task', 'report', sys.argv[1], '--agent', sys.argv[2], '--result', 'completed', '--summary', 'automatic runner report', '--next-action', 'review', '--file', sys.argv[4], '--check', 'automatic runner report: pass']\n"
            "raise SystemExit(subprocess.run(cmd, check=False).returncode)\n"
        )
        return [
            sys.executable,
            "-c",
            runner_code,
            "{task_id}",
            "{agent_id}",
            str(Path(relwit.__file__).resolve()),
            "src/runner.py",
            "{assignment_path}",
            str(exit_code),
        ]

    def configure_runner(
        self,
        command: list[str],
        timeout_seconds: int = 30,
        preflight_command: list[str] | None = None,
        preflight_timeout_seconds: int = 30,
    ) -> None:
        config = relwit.load_config()
        config["agents"][0]["runner"] = {
            "command": command,
            "timeout_seconds": timeout_seconds,
        }
        if preflight_command is not None:
            config["agents"][0]["runner"]["preflight"] = {
                "command": preflight_command,
                "timeout_seconds": preflight_timeout_seconds,
            }
        relwit.save_config(config)

    def preflight_command(self, payload: object, exit_code: int = 0) -> list[str]:
        code = f"import json; print(json.dumps({payload!r})); raise SystemExit({exit_code})"
        return [sys.executable, "-c", code, "{task_id}", "{agent_id}", "{assignment_path}"]

    def qa_python(self, code: str, *arguments: str) -> dict:
        return {"mode": "argv", "argv": [sys.executable, "-c", code, *arguments]}

    def configure_qa(self, command: dict[str, object] | None = None) -> dict:
        config = relwit.load_config()
        config["supervisor"]["qa_commands"] = [command or self.qa_python("print('qa-pass')")]
        relwit.save_config(config)
        return config

    def successful_qa(self, command: dict[str, object] | None = None) -> tuple[dict, dict]:
        config = self.configure_qa(command)
        result = relwit.run_qa(config, "source-state-test")
        self.assertEqual(result["status"], "pass")
        self.assertTrue(result["source_fingerprint"])
        return config, result

    def release_data_with_done_task(self) -> dict:
        data = relwit.load_registry()
        data["items"]["RW-9999"] = {"status": "done"}
        return data

    def initialize_git_baseline(self) -> None:
        commands = [
            ["git", "init", "-q"],
            ["git", "config", "user.email", "relwit-tests@example.test"],
            ["git", "config", "user.name", "RelWit Tests"],
            ["git", "add", "-A"],
            ["git", "commit", "-qm", "test baseline"],
        ]
        for command in commands:
            result = subprocess.run(command, cwd=relwit.ROOT, capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)

    def git_output(self, *arguments: str, input_text: str | None = None) -> str:
        result = subprocess.run(
            ["git", *arguments],
            cwd=relwit.ROOT,
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def configure_tracking_ref(self, branch: str, commit: str) -> None:
        self.git_output("remote", "add", "origin", str(relwit.ROOT))
        self.git_output("update-ref", f"refs/remotes/origin/{branch}", commit)
        self.git_output("config", f"branch.{branch}.remote", "origin")
        self.git_output("config", f"branch.{branch}.merge", f"refs/heads/{branch}")

    def test_worker_run_invokes_runner_and_accepts_automatic_report(self) -> None:
        self.register_worker("worker-1", "src")
        self.configure_runner(self.reporting_runner())
        task_id = self.new_task("Automatic worker run", "src/runner.py")
        code, _, error = self.invoke("supervisor", "dispatch")
        self.assertEqual((code, error), (0, ""))

        code, output, error = self.invoke("worker", "run", "--agent", "worker-1")
        self.assertEqual((code, error), (0, ""))
        self.assertIn(f"{task_id} runner_status=reported result=completed", output)
        registry = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        item = registry["items"][task_id]
        self.assertEqual(item["status"], "reported")
        self.assertEqual(item["last_result"], "completed")
        self.assertTrue(item["reports"])
        self.assertTrue(any(entry["kind"] == "runner" for entry in item["evidence"]))

    def test_dispatch_skips_configured_runner_with_unavailable_executable(self) -> None:
        self.register_worker("worker-1", "src")
        self.configure_runner(["relwit-runtime-that-does-not-exist", "{assignment_path}"])
        task_id = self.new_task("Unavailable runtime", "src/unavailable.py")

        code, output, error = self.invoke("supervisor", "dispatch")
        self.assertEqual((code, error), (0, ""))
        self.assertEqual(output.strip(), "no task dispatched")
        registry = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(registry["items"][task_id]["status"], "planned")
        self.assertEqual(relwit.static_runner_readiness(relwit.load_config()["agents"][0])["state"], "unavailable")

    def test_preflight_ready_is_bounded_and_preserves_existing_runner_flow(self) -> None:
        self.register_worker("worker-1", "src")
        self.configure_runner(
            self.reporting_runner(),
            preflight_command=self.preflight_command({"relwit_preflight": 1, "state": "ready", "reason": "local adapter ready"}),
        )
        task_id = self.new_task("Ready runtime", "src/runner.py")
        code, _, error = self.invoke("supervisor", "dispatch")
        self.assertEqual((code, error), (0, ""))

        code, output, error = self.invoke("worker", "run", "--agent", "worker-1")
        self.assertEqual((code, error), (0, ""))
        self.assertIn(f"{task_id} runner_status=reported result=completed", output)
        preflight_spools = list((relwit.ROOT / "work" / ".runtime-output").glob(f"preflight-worker-1-{task_id}-*.md"))
        self.assertEqual(len(preflight_spools), 1)
        self.assertIn("execution_mode: `argv`", preflight_spools[0].read_text(encoding="utf-8"))

    def test_preflight_unavailable_preserves_assignment_and_records_disposition(self) -> None:
        self.register_worker("worker-1", "src")
        self.configure_runner(
            self.reporting_runner(),
            preflight_command=self.preflight_command(
                {"relwit_preflight": 1, "state": "unavailable", "reason": "runtime is offline", "disposition": "reassign"}
            ),
        )
        task_id = self.new_task("Unavailable after preflight", "src/preflight.py")
        code, _, error = self.invoke("supervisor", "dispatch")
        self.assertEqual((code, error), (0, ""))

        code, output, error = self.invoke("worker", "run", "--agent", "worker-1")
        self.assertEqual(code, 2)
        self.assertEqual(error, "")
        self.assertIn(f"{task_id} runtime_state=unavailable failure_class=unavailable disposition=reassign", output)
        registry = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        item = registry["items"][task_id]
        self.assertEqual(item["status"], "assigned")
        self.assertEqual(item["reports"], [])
        readiness = [entry for entry in item["evidence"] if entry["kind"] == "runtime-readiness"]
        self.assertEqual(len(readiness), 1)
        self.assertIn("local_spool=work/.runtime-output/", readiness[0]["value"])

    def test_preflight_no_target_and_malformed_state_are_safe(self) -> None:
        self.register_worker("worker-1", "src")
        self.configure_runner(
            self.reporting_runner(),
            preflight_command=self.preflight_command(
                {"relwit_preflight": 1, "state": "no_target", "reason": "no selected runtime", "disposition": "needs_input"}
            ),
        )
        task_id = self.new_task("No runtime target", "src/no-target.py")
        code, _, error = self.invoke("supervisor", "dispatch")
        self.assertEqual((code, error), (0, ""))
        code, output, error = self.invoke("worker", "run", "--agent", "worker-1")
        self.assertEqual((code, error), (2, ""))
        self.assertIn("runtime_state=no_target", output)
        self.assertEqual(json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))["items"][task_id]["status"], "assigned")

    def test_preflight_output_is_redacted_in_local_spool_and_task_evidence(self) -> None:
        self.register_worker("worker-1", "src")
        secret = "preflight-secret-123456789"
        self.configure_runner(
            self.reporting_runner(),
            preflight_command=self.preflight_command(
                {"relwit_preflight": 1, "state": "unavailable", "reason": f"API_KEY={secret}", "disposition": "reassign"}
            ),
        )
        task_id = self.new_task("Secret-safe preflight", "src/runner.py")
        self.invoke("supervisor", "dispatch")
        code, _, error = self.invoke("worker", "run", "--agent", "worker-1")
        self.assertEqual((code, error), (2, ""))
        registry_text = relwit.REGISTRY.read_text(encoding="utf-8")
        self.assertNotIn(secret, registry_text)
        spool = next((relwit.ROOT / "work" / ".runtime-output").glob(f"preflight-worker-1-{task_id}-*.md"))
        self.assertNotIn(secret, spool.read_text(encoding="utf-8"))

    def test_preflight_timeout_is_unknown_and_does_not_claim_ownership(self) -> None:
        self.register_worker("worker-1", "src")
        slow_preflight = [sys.executable, "-c", "import time; time.sleep(2)"]
        self.configure_runner(self.reporting_runner(), preflight_command=slow_preflight, preflight_timeout_seconds=1)
        task_id = self.new_task("Timed out preflight", "src/runner.py")
        self.invoke("supervisor", "dispatch")
        code, output, error = self.invoke("worker", "run", "--agent", "worker-1")
        self.assertEqual((code, error), (2, ""))
        self.assertIn(f"{task_id} runtime_state=unknown failure_class=timeout disposition=retry", output)
        item = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))["items"][task_id]
        self.assertEqual(item["status"], "assigned")
        self.assertEqual(item["reports"], [])

    def test_ambiguous_preflight_is_unknown_and_does_not_pull(self) -> None:
        self.register_worker("worker-1", "src")
        ambiguous_preflight = [sys.executable, "-c", "print('runtime maybe available')"]
        self.configure_runner(self.reporting_runner(), preflight_command=ambiguous_preflight)
        task_id = self.new_task("Ambiguous preflight", "src/runner.py")
        self.invoke("supervisor", "dispatch")
        code, output, error = self.invoke("worker", "run", "--agent", "worker-1")
        self.assertEqual((code, error), (2, ""))
        self.assertIn(f"{task_id} runtime_state=unknown failure_class=unknown disposition=needs_input", output)
        item = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))["items"][task_id]
        self.assertEqual(item["status"], "assigned")
        self.assertEqual(item["reports"], [])

    def test_runtime_readiness_writes_do_not_stale_source_bound_qa(self) -> None:
        self.register_worker("worker-1", "src")
        self.configure_runner(
            self.reporting_runner(),
            preflight_command=self.preflight_command(
                {"relwit_preflight": 1, "state": "unavailable", "reason": "offline", "disposition": "reassign"}
            ),
        )
        task_id = self.new_task("Volatile readiness event", "src/runner.py")
        self.invoke("supervisor", "dispatch")
        config = self.configure_qa()
        qa_result = relwit.run_qa(config, "readiness-qa-test")
        before = relwit.release_source_fingerprint(config)["fingerprint"]
        code, _, error = self.invoke("worker", "run", "--agent", "worker-1")
        self.assertEqual((code, error), (2, ""))
        after = relwit.release_source_fingerprint(config)["fingerprint"]
        self.assertEqual(before, after)
        self.assertEqual(relwit.validate_qa_source(config, {"last_qa": qa_result})["status"], "valid")
        self.assertEqual(json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))["items"][task_id]["status"], "assigned")

    def test_ambiguous_runtime_text_never_proves_quota_or_auth(self) -> None:
        failure = relwit.classify_runner_failure(9, stderr="quota exhausted; authentication failed")
        self.assertEqual(failure["failure_class"], "runtime_error")
        self.assertNotEqual(failure["failure_class"], "quota_limited")
        self.assertNotEqual(failure["failure_class"], "auth_error")
        self.assertFalse(failure["authoritative"])

    def test_authoritative_auth_failure_requires_contract_evidence(self) -> None:
        failure = relwit.classify_runner_failure(
            7,
            stderr=json.dumps(
                {
                    "relwit_runtime_result": 1,
                    "failure_class": "auth_error",
                    "authoritative": True,
                    "disposition": "needs_input",
                }
            ),
        )
        self.assertEqual(failure["failure_class"], "auth_error")
        self.assertEqual(failure["disposition"], "needs_input")
        self.assertTrue(failure["authoritative"])

    def test_authoritative_quota_failure_is_classified_and_reported(self) -> None:
        self.register_worker("worker-1", "src")
        runner_code = (
            "import json; print(json.dumps({'relwit_runtime_result': 1, 'failure_class': 'quota_limited', "
            "'authoritative': True, 'disposition': 'needs_input', 'reason': 'provider contract quota'})); raise SystemExit(9)"
        )
        self.configure_runner([sys.executable, "-c", runner_code, "{assignment_path}"])
        task_id = self.new_task("Authoritative quota", "src/quota.py")
        code, _, error = self.invoke("supervisor", "dispatch")
        self.assertEqual((code, error), (0, ""))
        code, output, error = self.invoke("worker", "run", "--agent", "worker-1")
        self.assertEqual((code, error), (1, ""))
        self.assertIn(f"{task_id} runner_status=reported result=failed", output)
        registry = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        item = registry["items"][task_id]
        evidence = next(entry for entry in item["evidence"] if entry["kind"] == "runner")
        self.assertIn("failure_class=quota_limited", evidence["value"])
        evidence_path = relwit.ROOT / evidence["value"].split(" ", 1)[0]
        evidence_text = evidence_path.read_text(encoding="utf-8")
        self.assertIn("failure_class: `quota_limited`", evidence_text)
        self.assertIn("authoritative: `true`", evidence_text)

    def test_non_authoritative_quota_envelope_is_downgraded_to_unknown(self) -> None:
        failure = relwit.classify_runner_failure(
            9,
            stdout=json.dumps(
                {
                    "relwit_runtime_result": 1,
                    "failure_class": "quota_limited",
                    "authoritative": False,
                }
            ),
        )
        self.assertEqual(failure["failure_class"], "unknown")
        self.assertFalse(failure["authoritative"])

    def test_worker_run_requires_configured_runner_without_claiming(self) -> None:
        self.register_worker("worker-1", "src")
        task_id = self.new_task("Runner is opt in", "src/manual.py")
        code, _, error = self.invoke("supervisor", "dispatch")
        self.assertEqual((code, error), (0, ""))

        code, output, error = self.invoke("worker", "run", "--agent", "worker-1")
        self.assertEqual(code, 2)
        self.assertEqual(output, "")
        self.assertIn("no configured runner", error)
        registry = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(registry["items"][task_id]["status"], "assigned")

    def test_worker_run_records_failure_when_runner_omits_report(self) -> None:
        self.register_worker("worker-1", "src")
        self.configure_runner(
            [
                sys.executable,
                "-c",
                "import sys; raise SystemExit(9)",
                "{assignment_path}",
            ]
        )
        task_id = self.new_task("Runner failure", "src/failure.py")
        code, _, error = self.invoke("supervisor", "dispatch")
        self.assertEqual((code, error), (0, ""))

        code, output, error = self.invoke("worker", "run", "--agent", "worker-1")
        self.assertEqual(code, 1)
        self.assertEqual(error, "")
        self.assertIn(f"{task_id} runner_status=reported result=failed", output)
        registry = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        item = registry["items"][task_id]
        self.assertEqual(item["status"], "reported")
        self.assertEqual(item["last_result"], "failed")
        self.assertEqual(len(item["reports"]), 1)
        self.assertTrue(any(entry["kind"] == "runner" for entry in item["evidence"]))

    def test_runner_output_is_sanitized_bounded_and_spooled_locally(self) -> None:
        self.register_worker("worker-1", "src")
        runner_code = (
            "import sys\n"
            "print('API_KEY=runner-secret-123456789')\n"
            "print('Authorization: Bearer runner-token-123456789')\n"
            "print('postgresql://runner:runner-password@db.example.test/app')\n"
            "print('{\"password\":\"json-password-123456789\",\"token\":\"json-token-123456789\"}')\n"
            "print('-----BEGIN RSA PRIVATE KEY-----')\n"
            "print('MII_RUNNER_PRIVATE_KEY_MATERIAL_123456789')\n"
            "print('-----END RSA PRIVATE KEY-----')\n"
            f"print('O' * {relwit.MAX_DURABLE_OUTPUT_CHARS + 500})\n"
            "print('Cookie: session=runner-cookie-123456789', file=sys.stderr)\n"
            f"print('E' * {relwit.MAX_DURABLE_OUTPUT_CHARS + 500}, file=sys.stderr)\n"
            "raise SystemExit(9)\n"
        )
        self.configure_runner([sys.executable, "-c", runner_code, "{assignment_path}"])
        task_id = self.new_task("Sanitized runner output", "src/safe-runner.py")
        code, _, error = self.invoke("supervisor", "dispatch")
        self.assertEqual((code, error), (0, ""))

        code, _, error = self.invoke("worker", "run", "--agent", "worker-1")
        self.assertEqual((code, error), (1, ""))
        registry = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        runner_entry = next(entry for entry in registry["items"][task_id]["evidence"] if entry["kind"] == "runner")
        evidence_path = relwit.ROOT / runner_entry["value"].split(" ", 1)[0]
        evidence = evidence_path.read_text(encoding="utf-8")
        for secret in (
            "runner-secret-123456789",
            "runner-token-123456789",
            "runner-password",
            "runner-cookie-123456789",
            "json-password-123456789",
            "json-token-123456789",
            "MII_RUNNER_PRIVATE_KEY_MATERIAL_123456789",
        ):
            self.assertNotIn(secret, evidence)
        self.assertIn("provenance: `local`", evidence)
        self.assertIn(f"budget_chars: `{relwit.MAX_DURABLE_OUTPUT_CHARS}`", evidence)
        self.assertIn("truncated: `true`", evidence)
        preview_chars = int(
            next(line.split("`", 2)[1] for line in evidence.splitlines() if line.startswith("- preview_chars:"))
        )
        self.assertLessEqual(preview_chars, relwit.MAX_DURABLE_OUTPUT_CHARS)
        spool_rel = next(line.split("`", 2)[1] for line in evidence.splitlines() if line.startswith("- local_spool:"))
        self.assertTrue(spool_rel.startswith("work/.runtime-output/"))
        spool = (relwit.ROOT / spool_rel).read_text(encoding="utf-8")
        for secret in (
            "runner-secret-123456789",
            "runner-token-123456789",
            "runner-password",
            "runner-cookie-123456789",
            "json-password-123456789",
            "json-token-123456789",
            "MII_RUNNER_PRIVATE_KEY_MATERIAL_123456789",
        ):
            self.assertNotIn(secret, spool)
        ignore_check = subprocess.run(
            ["git", "check-ignore", "--no-index", "work/.runtime-output/example.md"],
            cwd=self.original_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(ignore_check.returncode, 0, ignore_check.stderr)

    def test_runtime_spool_cannot_overlap_committable_evidence(self) -> None:
        config = relwit.load_config()
        config["paths"]["runtime_spool"] = config["paths"]["evidence"]
        errors: list[str] = []
        relwit.validate_config(config, errors)
        self.assertIn("config.paths.runtime_spool must not overlap config.paths.evidence", errors)

    def test_worker_run_times_out_and_records_failure_evidence(self) -> None:
        self.register_worker("worker-1", "src")
        self.configure_runner(
            [
                sys.executable,
                "-c",
                "import time; time.sleep(2)",
                "{assignment_path}",
            ],
            timeout_seconds=1,
        )
        task_id = self.new_task("Runner timeout", "src/timeout.py")
        code, _, error = self.invoke("supervisor", "dispatch")
        self.assertEqual((code, error), (0, ""))

        code, output, error = self.invoke("worker", "run", "--agent", "worker-1")
        self.assertEqual(code, 1)
        self.assertEqual(error, "")
        self.assertIn(f"{task_id} runner_status=reported result=failed", output)
        registry = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        item = registry["items"][task_id]
        self.assertEqual(item["last_result"], "failed")
        runner_evidence = [entry for entry in item["evidence"] if entry["kind"] == "runner"]
        self.assertEqual(len(runner_evidence), 1)
        evidence_path = relwit.ROOT / runner_evidence[0]["value"].split(" ", 1)[0]
        self.assertIn("returncode: `124`", evidence_path.read_text(encoding="utf-8"))

    def test_worker_run_wait_is_bounded_and_returns_no_task(self) -> None:
        self.register_worker("worker-1", "src")
        self.configure_runner(self.reporting_runner())
        code, output, error = self.invoke(
            "worker",
            "run",
            "--agent",
            "worker-1",
            "--wait-seconds",
            "0.1",
            "--poll-seconds",
            "0.1",
        )
        self.assertEqual((code, error), (0, ""))
        self.assertEqual(output.strip(), "NO_TASK")

    def test_agent_register_persists_argv_runner_contract(self) -> None:
        code, output, error = self.invoke(
            "agent",
            "register",
            "--id",
            "runner-worker",
            "--role",
            "worker",
            "--scope",
            "src",
            "--runner-arg",
            sys.executable,
            "--runner-arg=-c",
            "--runner-arg",
            "print('runner')",
            "--runner-arg",
            "{assignment_path}",
        )
        self.assertEqual((code, error), (0, ""))
        self.assertIn("runner-worker", output)
        config = relwit.load_config()
        runner = config["agents"][0]["runner"]
        self.assertEqual(runner["command"][-1], "{assignment_path}")
        self.assertEqual(runner["timeout_seconds"], relwit.DEFAULT_RUNNER_TIMEOUT_SECONDS)

    def test_validator_rejects_unsafe_runner_shape(self) -> None:
        self.register_worker("worker-1", "src")
        config = relwit.load_config()
        config["agents"][0]["runner"] = {
            "command": ["python", "adapter.py"],
            "timeout_seconds": 0,
        }
        errors: list[str] = []
        relwit.validate_config(config, errors)
        self.assertTrue(any("must include {assignment_path}" in error for error in errors))
        config["agents"][0]["runner"]["command"].append("{assignment_path}")
        errors = []
        relwit.validate_config(config, errors)
        self.assertTrue(any("between 1 and" in error for error in errors))
        config["agents"][0]["runner"]["preflight"] = {
            "command": "python",
            "timeout_seconds": 1,
        }
        config["agents"][0]["runner"]["timeout_seconds"] = 1
        errors = []
        relwit.validate_config(config, errors)
        self.assertTrue(any("preflight.command must be" in error for error in errors))

    def test_validator_reports_malformed_config_without_traceback(self) -> None:
        config = relwit.load_config()
        config["supervisor"]["qa_timeout_seconds"] = 0
        config["agents"] = [
            {
                "role": "worker",
                "status": "available",
                "scope": [],
                "max_active": 0,
                "inbox": 123,
            }
        ]
        errors: list[str] = []
        relwit.validate_config(config, errors)
        self.assertTrue(any("qa_timeout_seconds" in error for error in errors))
        self.assertTrue(any("invalid or duplicate agent id" in error for error in errors))
        self.assertTrue(any("agent needs an id" in error for error in errors))

    def test_validator_reports_malformed_config_sections_without_traceback(self) -> None:
        relwit.CONFIG.write_text(
            json.dumps({"paths": None, "supervisor": [], "agents": []}),
            encoding="utf-8",
        )
        code, output, error = self.invoke("validate")
        self.assertEqual(code, 1)
        self.assertIn("config.paths must be an object", output)
        self.assertIn("config.supervisor must be an object", output)
        self.assertNotIn("Traceback", output + error)

    def test_validator_rejects_malformed_agent_capabilities(self) -> None:
        self.register_worker("worker-1", ".")
        config = relwit.load_config()
        config["agents"][0]["capabilities"] = [["nested"]]
        errors: list[str] = []
        relwit.validate_config(config, errors)
        self.assertIn(
            "capabilities must be an array of non-empty strings for agent worker-1",
            errors,
        )

    def test_validator_reports_malformed_registry_arrays_without_traceback(self) -> None:
        task_id = self.new_task("Registry array validation", "src/registry.py")
        data = relwit.load_registry()
        data["items"][task_id]["depends_on"] = None
        data["items"][task_id]["files"] = {"bad": "shape"}
        data["items"][task_id]["reports"] = "not-an-array"
        data["items"][task_id]["status"] = "done"
        data["items"][task_id]["evidence"] = 1
        errors: list[str] = []
        relwit.validate_registry(data, errors)
        self.assertIn(f"{task_id} depends_on must be an array", errors)
        self.assertIn(f"{task_id} files must be an array", errors)
        self.assertIn(f"{task_id} reports must be an array", errors)
        self.assertIn(f"{task_id} evidence must be an array", errors)

    def test_task_update_rejects_unsafe_or_out_of_scope_recorded_files(self) -> None:
        self.register_worker("worker-1", "src")
        task_id = self.new_task("Safe recorded file", "src/owned.py")
        code, _, error = self.invoke("task", "claim", task_id, "--agent", "worker-1")
        self.assertEqual((code, error), (0, ""))
        code, _, error = self.invoke(
            "task",
            "update",
            task_id,
            "--status",
            "in_progress",
            "--agent",
            "worker-1",
            "--file",
            "../outside.py",
        )
        self.assertEqual(code, 2)
        self.assertIn("parent traversal", error)
        code, _, error = self.invoke(
            "task",
            "update",
            task_id,
            "--status",
            "in_progress",
            "--agent",
            "worker-1",
            "--file",
            "tests/secret.py",
        )
        self.assertEqual(code, 2)
        self.assertIn("outside task scope", error)
        registry = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(registry["items"][task_id]["files"], [])

    def test_done_registry_requires_review_evidence(self) -> None:
        task_id = self.new_task("Review evidence is required", "src/review-gate.py")
        data = relwit.load_registry()
        data["items"][task_id]["status"] = "done"
        data["items"][task_id]["evidence"] = [{"kind": "test", "value": "pass"}]
        errors: list[str] = []
        relwit.validate_registry(data, errors)
        self.assertIn(f"{task_id} is done without non-empty review evidence", errors)

    def test_update_cannot_bypass_claim(self) -> None:
        task_id = self.new_task("Claimed only through the CLI", "src/claim.py")
        code, _, error = self.invoke("task", "update", task_id, "--status", "in_progress", "--agent", "worker-1")
        self.assertEqual(code, 2)
        self.assertIn("task claim", error)

    def test_assigned_task_requires_claim_before_activation(self) -> None:
        self.register_worker("worker-1", ".")
        task_id = self.new_task("Claim before activation", "src/activation.py")

        code, _, error = self.invoke("supervisor", "dispatch")
        self.assertEqual((code, error), (0, ""))
        registry_before = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(registry_before["items"][task_id]["status"], "assigned")
        self.assertEqual(registry_before["items"][task_id]["assigned_to"], "worker-1")

        code, _, error = self.invoke(
            "task", "update", task_id, "--status", "in_progress", "--agent", "worker-1"
        )
        self.assertEqual(code, 2)
        self.assertIn("task claim", error)
        registry_after = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(registry_after["items"][task_id], registry_before["items"][task_id])

        code, _, error = self.invoke("worker", "pull", "--agent", "worker-1")
        self.assertEqual((code, error), (0, ""))
        registry = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(registry["items"][task_id]["status"], "in_progress")

    def test_task_report_requires_activation(self) -> None:
        self.register_worker("worker-1", "src")
        task_id = self.new_task("Report after activation", "src/report-activation.py")
        code, _, error = self.invoke("supervisor", "dispatch")
        self.assertEqual((code, error), (0, ""))
        registry_before = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        report_paths_before = sorted((relwit.ROOT / "work" / "reports" / "inbox").glob("*.md"))

        code, _, error = self.invoke(
            "task",
            "report",
            task_id,
            "--agent",
            "worker-1",
            "--result",
            "completed",
            "--summary",
            "Must not bypass activation",
            "--next-action",
            "Pull the assignment first",
            "--file",
            "src/report-activation.py",
            "--check",
            "should not be accepted",
        )
        self.assertEqual(code, 2)
        self.assertIn("worker must claim or pull before reporting", error)
        registry_after = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(registry_after["items"][task_id], registry_before["items"][task_id])
        self.assertEqual(sorted((relwit.ROOT / "work" / "reports" / "inbox").glob("*.md")), report_paths_before)

    def test_supervisor_ingest_ignores_report_for_unactivated_task(self) -> None:
        self.register_worker("worker-1", "src")
        task_id = self.new_task("Ignore unactivated report", "src/unactivated-report.py")
        code, _, error = self.invoke("supervisor", "dispatch")
        self.assertEqual((code, error), (0, ""))
        registry_before = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        fake_report = relwit.ROOT / "work" / "reports" / "inbox" / f"{task_id}-unactivated.md"
        fake_report.write_text(
            "\n".join(
                [
                    "---",
                    "type: relwit-worker-report",
                    f"task_id: {task_id}",
                    "agent: worker-1",
                    "result: completed",
                    "files: [\"src/unactivated-report.py\"]",
                    "checks: [\"forged\"]",
                    "---",
                    "",
                    "# Forged report",
                    "",
                    "This must not enter the ledger before activation.",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        code, output, error = self.invoke("supervisor", "ingest")
        self.assertEqual((code, error), (0, ""))
        self.assertEqual(output.strip(), "no new reports")
        registry_after = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(registry_after["items"][task_id], registry_before["items"][task_id])
        state = relwit.load_supervisor_state(relwit.load_config())
        self.assertNotIn(relwit.rel(fake_report), state.get("ingested_reports", []))

    def test_administrative_transitions_require_review_role(self) -> None:
        self.register_worker("worker-1", ".")
        self.register_reviewer()
        task_id = self.new_task("Administrative lifecycle", "src/administrative.py")
        registry_before = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))

        for status in ("blocked", "cancelled"):
            code, _, error = self.invoke(
                "task", "update", task_id, "--status", status, "--agent", "worker-1"
            )
            self.assertEqual(code, 2)
            self.assertIn("not authorized for review actions", error)
            registry = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
            self.assertEqual(registry["items"][task_id], registry_before["items"][task_id])

        code, _, error = self.invoke(
            "task", "update", task_id, "--status", "blocked", "--agent", "reviewer"
        )
        self.assertEqual((code, error), (0, ""))
        code, _, error = self.invoke(
            "task", "update", task_id, "--status", "planned", "--agent", "reviewer"
        )
        self.assertEqual((code, error), (0, ""))
        code, _, error = self.invoke(
            "task", "update", task_id, "--status", "cancelled", "--agent", "reviewer"
        )
        self.assertEqual((code, error), (0, ""))

        blocked_id = self.new_task("Worker blocked handover", "src/blocked.py")
        code, _, error = self.invoke("task", "claim", blocked_id, "--agent", "worker-1")
        self.assertEqual((code, error), (0, ""))
        code, _, error = self.invoke(
            "task",
            "report",
            blocked_id,
            "--agent",
            "worker-1",
            "--result",
            "blocked",
            "--summary",
            "Waiting for an external dependency",
            "--next-action",
            "Review blocker and decide whether to retry",
            "--file",
            "src/blocked.py",
            "--check",
            "reproduction recorded",
        )
        self.assertEqual((code, error), (0, ""))
        registry = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(registry["items"][blocked_id]["status"], "blocked")
        self.assertEqual(len(registry["items"][blocked_id]["reports"]), 1)

    def test_reported_status_requires_worker_report(self) -> None:
        self.register_worker("worker-1", ".")
        task_id = self.new_task("Report-only completion", "src/report-only.py")
        code, _, error = self.invoke("task", "claim", task_id, "--agent", "worker-1")
        self.assertEqual((code, error), (0, ""))

        code, _, error = self.invoke("task", "update", task_id, "--status", "reported", "--agent", "worker-1")
        self.assertEqual(code, 2)
        self.assertIn("use task report for a worker completion", error)
        registry = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(registry["items"][task_id]["status"], "in_progress")
        self.assertEqual(registry["items"][task_id]["reports"], [])

        code, _, error = self.invoke(
            "task",
            "report",
            task_id,
            "--agent",
            "worker-1",
            "--result",
            "completed",
            "--summary",
            "Implementation complete",
            "--next-action",
            "Review",
            "--file",
            "src/report-only.py",
            "--check",
            "unit test: pass",
        )
        self.assertEqual((code, error), (0, ""))
        registry = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(registry["items"][task_id]["status"], "reported")
        self.assertEqual(len(registry["items"][task_id]["reports"]), 1)

    def test_evidence_provenance_is_typed_and_worker_reports_preserve_it(self) -> None:
        evidence_task = self.new_task("Typed evidence", "docs/evidence.md")
        code, _, error = self.invoke(
            "task",
            "evidence",
            evidence_task,
            "--kind",
            "smoke",
            "--value",
            "Named production endpoint returned 200",
            "--provenance",
            "live",
            "--source",
            "https://example.invalid/health",
        )
        self.assertEqual((code, error), (0, ""))
        registry = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        entry = registry["items"][evidence_task]["evidence"][-1]
        self.assertEqual(entry["provenance"], "live")
        self.assertEqual(entry["source"], "https://example.invalid/health")
        self.assertTrue(entry["recorded_at"])

        evidence_before = list(registry["items"][evidence_task]["evidence"])
        code, _, error = self.invoke(
            "task",
            "evidence",
            evidence_task,
            "--kind",
            "smoke",
            "--value",
            "Unknown provenance must fail",
            "--provenance",
            "invented",
            "--source",
            "test",
        )
        self.assertEqual(code, 2)
        self.assertIn("invalid evidence provenance", error)
        registry_after = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(registry_after["items"][evidence_task]["evidence"], evidence_before)
        code, _, error = self.invoke(
            "task",
            "evidence",
            evidence_task,
            "--kind",
            "smoke",
            "--value",
            "Reserved legacy label must fail for new CLI evidence",
            "--provenance",
            "legacy",
            "--source",
            "test",
        )
        self.assertEqual(code, 2)
        self.assertIn("invalid evidence provenance", error)
        malformed_registry = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        malformed_registry["items"][evidence_task]["evidence"].append(
            {"kind": "bad", "value": "untrusted", "provenance": "invented", "source": "test"}
        )
        validation_errors: list[str] = []
        relwit.validate_registry(malformed_registry, validation_errors)
        self.assertTrue(any("invalid evidence provenance" in value for value in validation_errors))

        self.register_worker("provenance-worker", "src")
        report_task = self.new_task("Provenance report", "src/provenance.py")
        code, _, error = self.invoke("task", "claim", report_task, "--agent", "provenance-worker")
        self.assertEqual((code, error), (0, ""))
        code, _, error = self.invoke(
            "task",
            "report",
            report_task,
            "--agent",
            "provenance-worker",
            "--result",
            "completed",
            "--summary",
            "Replay completed without external provider execution",
            "--next-action",
            "Review the simulation evidence",
            "--provenance",
            "simulation",
            "--source",
            "examples/multi-runtime-conformance/run_conformance.py",
            "--file",
            "src/provenance.py",
            "--check",
            "replay: pass",
        )
        self.assertEqual((code, error), (0, ""))
        registry = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        entries = registry["items"][report_task]["evidence"]
        report_entry = next(entry for entry in entries if entry["kind"] == "worker-report")
        self.assertEqual(report_entry["provenance"], "simulation")
        self.assertEqual(report_entry["source"], "examples/multi-runtime-conformance/run_conformance.py")
        check_entry = next(entry for entry in entries if entry["kind"] == "check")
        self.assertEqual(check_entry["provenance"], "simulation")
        report_path = relwit.ROOT / registry["items"][report_task]["reports"][0]
        report_text = report_path.read_text(encoding="utf-8")
        self.assertIn("provenance: simulation", report_text)
        self.assertIn("source: examples/multi-runtime-conformance/run_conformance.py", report_text)

    def test_malformed_external_report_provenance_is_ignored_safely(self) -> None:
        self.register_worker("malformed-provenance", "src")
        task_id = self.new_task("Reject malformed provenance", "src/malformed.py")
        code, _, error = self.invoke("task", "claim", task_id, "--agent", "malformed-provenance")
        self.assertEqual((code, error), (0, ""))
        report_path = relwit.ROOT / "work" / "reports" / "inbox" / f"{task_id}-malformed-provenance.md"
        report_path.write_text(
            "---\n"
            "type: relwit-worker-report\n"
            f"task_id: {task_id}\n"
            "agent: malformed-provenance\n"
            "result: completed\n"
            "provenance: invented\n"
            "source: external-runner\n"
            "files: [\"src/malformed.py\"]\n"
            "---\n\ninvalid provenance\n",
            encoding="utf-8",
        )
        code, output, error = self.invoke("supervisor", "ingest")
        self.assertEqual((code, error), (0, ""))
        self.assertEqual(output.strip(), "no new reports")
        registry = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        item = registry["items"][task_id]
        self.assertEqual(item["status"], "in_progress")
        self.assertEqual(item["reports"], [])

    def test_dispatch_pull_report_and_supervisor_cycle(self) -> None:
        self.register_worker()
        self.register_reviewer()
        task_id = self.new_task("Build frontend", "src/frontend/app.py")
        code, output, error = self.invoke("supervisor", "cycle")
        self.assertEqual((code, error), (0, ""))
        self.assertIn("next=Workers pull", output)

        registry = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        item = registry["items"][task_id]
        self.assertEqual(item["status"], "assigned")
        self.assertEqual(item["assigned_to"], "frontend")
        assignment = relwit.ROOT / item["assignment_path"]
        self.assertTrue(assignment.exists())
        self.assertTrue((relwit.ROOT / "work" / "outbox" / f"{task_id}-to-frontend.md").exists())
        self.assertIn(task_id, (relwit.ROOT / "work" / "agents" / "frontend" / "INBOX.md").read_text(encoding="utf-8"))

        code, assignment_output, error = self.invoke("worker", "pull", "--agent", "frontend")
        self.assertEqual((code, error), (0, ""))
        self.assertIn(f"Assignment {task_id}", assignment_output)
        code, _, error = self.invoke(
            "task",
            "report",
            task_id,
            "--agent",
            "frontend",
            "--result",
            "completed",
            "--summary",
            "Implemented the feature",
            "--next-action",
            "Review and QA",
            "--file",
            "src/frontend/app.py",
            "--check",
            "unit test: pass",
        )
        self.assertEqual((code, error), (0, ""))
        registry = json.loads(relwit.REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(registry["items"][task_id]["status"], "reported")
        self.assertIn(task_id, (relwit.ROOT / "work" / "completed" / "COMPLETED.md").read_text(encoding="utf-8"))
        self.assertIn(task_id, (relwit.ROOT / "work" / "agents" / "frontend" / "REPORT.md").read_text(encoding="utf-8"))
        self.assertFalse((relwit.ROOT / "work" / "agents" / "frontend" / "REPORT.md").read_text(encoding="utf-8").endswith("\n\n"))

        code, _, error = self.invoke("supervisor", "cycle")
        self.assertEqual((code, error), (0, ""))
        supervisor_report = (relwit.ROOT / "work" / "SUPERVISOR_REPORT.md").read_text(encoding="utf-8")
        self.assertIn("awaiting review", supervisor_report)
        self.assertIn("worker report", supervisor_report)

        code, _, error = self.invoke(
            "task", "evidence", task_id, "--kind", "review", "--agent", "reviewer", "--value", "review pass"
        )
        self.assertEqual((code, error), (0, ""))
        code, _, error = self.invoke("task", "update", task_id, "--status", "needs_review", "--agent", "reviewer")
        self.assertEqual((code, error), (0, ""))
        code, _, error = self.invoke("task", "update", task_id, "--status", "done", "--agent", "reviewer")
        self.assertEqual((code, error), (0, ""))
        code, _, error = self.invoke("supervisor", "report")
        self.assertEqual((code, error), (0, ""))
        supervisor_report = (relwit.ROOT / "work" / "SUPERVISOR_REPORT.md").read_text(encoding="utf-8")
        self.assertIn("Completed tasks", supervisor_report)

    def test_supervisor_report_freshness_marker_and_safe_check(self) -> None:
        self.new_task("Freshness marker", "docs/freshness.md")
        code, _, error = self.invoke("supervisor", "report")
        self.assertEqual((code, error), (0, ""))
        report_path = relwit.ROOT / "work" / "SUPERVISOR_REPORT.md"
        report = report_path.read_text(encoding="utf-8")
        self.assertRegex(report, r"(?m)^<!-- relwit-report: registry_sha256=[0-9a-f]{64} -->$")
        self.assertIn("Registry revision", report)

        code, output, error = self.invoke("supervisor", "report", "--check")
        self.assertEqual((code, error), (0, ""))
        self.assertIn("freshness=fresh", output)

        self.new_task("Make report stale", "docs/stale.md")
        code, output, error = self.invoke("supervisor", "report", "--check")
        self.assertEqual(code, 1)
        self.assertEqual(error, "")
        self.assertIn("freshness=stale", output)
        code, output, error = self.invoke("context", "--max-chars", "10000")
        self.assertEqual((code, error), (0, ""))
        self.assertIn("Freshness:** stale", output)
        self.assertIn("not current", output)

        report_path.unlink()
        code, output, error = self.invoke("supervisor", "report", "--check")
        self.assertEqual(code, 1)
        self.assertEqual(error, "")
        self.assertIn("freshness=missing", output)

        report_path.write_text("# Missing marker\n", encoding="utf-8")
        code, output, error = self.invoke("supervisor", "report", "--check")
        self.assertEqual(code, 1)
        self.assertEqual(error, "")
        self.assertIn("freshness=unknown", output)
        self.assertIn("missing or malformed", output)

        report_path.write_text("<!-- relwit-report: registry_sha256=not-a-revision -->\n", encoding="utf-8")
        code, output, error = self.invoke("supervisor", "report", "--check")
        self.assertEqual(code, 1)
        self.assertEqual(error, "")
        self.assertIn("freshness=unknown", output)

    def test_supervisor_report_uses_safe_repository_relative_configured_path(self) -> None:
        config = relwit.load_config()
        config["paths"]["supervisor_report"] = "work/reports/custom-supervisor.md"
        relwit.save_config(config)
        self.new_task("Custom report path", "docs/custom-report.md")

        code, output, error = self.invoke("supervisor", "report")
        self.assertEqual((code, error), (0, ""))
        self.assertEqual(output.strip(), "work/reports/custom-supervisor.md")
        self.assertTrue((relwit.ROOT / "work" / "reports" / "custom-supervisor.md").is_file())

        code, output, error = self.invoke("supervisor", "report", "--check")
        self.assertEqual((code, error), (0, ""))
        self.assertIn("freshness=fresh", output)

        config["paths"]["supervisor_report"] = "../outside.md"
        relwit.save_config(config)
        code, _, error = self.invoke("supervisor", "report", "--check")
        self.assertEqual(code, 2)
        self.assertIn("leaves project root", error)

    def test_worker_can_use_explicit_markdown_paths(self) -> None:
        code, _, error = self.invoke(
            "agent",
            "register",
            "--id",
            "qaagent",
            "--directory",
            "work/custom/qaagent",
            "--inbox-file",
            "work/mail/qa-inbox.md",
            "--report-file",
            "work/mail/qa-report.md",
            "--completed-file",
            "work/mail/qa-completed.md",
        )
        self.assertEqual((code, error), (0, ""))
        for path in ("work/mail/qa-inbox.md", "work/mail/qa-report.md", "work/mail/qa-completed.md"):
            self.assertTrue((relwit.ROOT / path).exists())

    def test_explicit_root_switches_runtime_state(self) -> None:
        target_root = Path(self.temp_dir.name) / "selected-project"
        target_root.mkdir()

        code, _, error = self.invoke("--root", str(target_root), "init")
        self.assertEqual((code, error), (0, ""))
        self.assertEqual(relwit.ROOT, target_root.resolve())
        self.assertTrue((target_root / "work" / "registry.json").exists())
        self.assertTrue((target_root / "relwit.config.json").exists())
        self.assertTrue((target_root / "work" / "INDEX.md").exists())
        self.assertTrue((target_root / "work" / "completed" / "COMPLETED.md").exists())
        self.assertEqual(list((target_root / "work" / "items").glob("*.md")), [])
        self.assertEqual(list((target_root / "work" / "evidence").iterdir()), [])

        code, task_id, error = self.invoke(
            "--root",
            str(target_root),
            "task",
            "new",
            "--title",
            "Target-root task",
            "--level",
            "L1",
            "--owner",
            "worker",
            "--scope",
            "src/app.py",
            "--acceptance",
            "target root is used",
        )
        self.assertEqual((code, error), (0, ""))
        task_id = task_id.strip()
        self.assertTrue((target_root / "work" / "items" / f"{task_id}.md").exists())
        self.assertFalse((Path(self.temp_dir.name) / "work" / "items" / f"{task_id}.md").exists())

    def test_init_scaffolds_configured_agent_mailbox_without_history(self) -> None:
        target_root = Path(self.temp_dir.name) / "prepared-project"
        target_root.mkdir()
        config = copy.deepcopy(relwit.DEFAULT_CONFIG)
        config["agents"] = [
            {
                "id": "supervisor",
                "role": "supervisor",
                "status": "available",
                "directory": "work/agents/supervisor",
                "scope": ["."],
                "capabilities": ["orchestration"],
                "max_active": 1,
            }
        ]
        (target_root / "relwit.config.json").write_text(json.dumps(config), encoding="utf-8")

        code, _, error = self.invoke("--root", str(target_root), "init")
        self.assertEqual((code, error), (0, ""))
        supervisor = target_root / "work" / "agents" / "supervisor"
        self.assertTrue((target_root / "work" / "INDEX.md").exists())
        self.assertTrue((supervisor / "INBOX.md").exists())
        self.assertTrue((supervisor / "REPORT.md").exists())
        self.assertTrue((supervisor / "COMPLETED.md").exists())
        self.assertTrue((supervisor / "inbox").is_dir())
        self.assertEqual(list((target_root / "work" / "items").glob("*.md")), [])
        self.assertEqual(list((target_root / "work" / "evidence").iterdir()), [])

    def test_explicit_root_rejects_paths_outside_selected_root(self) -> None:
        target_root = Path(self.temp_dir.name) / "selected-project"
        target_root.mkdir()
        code, _, error = self.invoke("--root", str(target_root), "init")
        self.assertEqual((code, error), (0, ""))

        with self.assertRaises(relwit.RelWitError):
            relwit.safe_repo_path("../outside")

        missing_root = target_root / "does-not-exist"
        code, _, error = self.invoke("--root", str(missing_root), "validate")
        self.assertEqual(code, 2)
        self.assertIn("project root does not exist", error)

    def test_package_metadata_declares_supported_cli(self) -> None:
        metadata = tomllib.loads((self.original_root / "pyproject.toml").read_text(encoding="utf-8"))
        project = metadata["project"]
        self.assertEqual(project["name"], "relwit")
        self.assertEqual(project["license"], {"file": "LICENSE"})
        self.assertEqual(project["requires-python"], ">=3.11")
        self.assertEqual(project["scripts"]["relwit"], "relwit.cli:main")
        self.assertEqual(metadata["tool"]["setuptools"]["packages"], ["relwit"])

    def test_supervisor_prioritizes_review_gate_before_new_work(self) -> None:
        config = relwit.load_config()
        data = {"items": {"RW-9000": {"id": "RW-9000", "title": "Review this change", "status": "needs_review"}}}
        action = relwit.choose_next_action(data, [], config, {"status": "pass"})
        self.assertIn("Complete the review gate for RW-9000", action)

        data["items"]["RW-9000"]["status"] = "reported"
        action = relwit.choose_next_action(data, [], config, {"status": "pass"})
        self.assertIn("Review worker report for RW-9000", action)

    def test_qa_command_is_captured_as_evidence(self) -> None:
        config = relwit.load_config()
        config["supervisor"]["qa_commands"] = [self.qa_python("print('qa-ok')")]
        relwit.save_config(config)
        code, output, error = self.invoke("supervisor", "qa")
        self.assertEqual((code, error), (0, ""))
        self.assertIn('"status": "pass"', output)
        state = json.loads((relwit.ROOT / "work" / "supervisor" / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["last_qa"]["status"], "pass")
        self.assertTrue(state["last_qa"]["source_fingerprint"])
        self.assertEqual(state["last_qa"]["source_dirty_state"], "unknown")
        self.assertTrue(state["last_qa"]["qa_config_fingerprint"])
        self.assertEqual(len(state["last_qa"]["executed_checks"]), 1)
        self.assertTrue((relwit.ROOT / state["last_qa"]["evidence"]).exists())

        config["supervisor"]["qa_commands"] = [self.qa_python("raise SystemExit(1)")]
        relwit.save_config(config)
        code, output, error = self.invoke("supervisor", "cycle", "--run-qa")
        self.assertEqual((code, error), (0, ""))
        self.assertIn("create a scoped debug task", output)

    def test_qa_output_is_sanitized_bounded_and_spooled_locally(self) -> None:
        qa_script = relwit.ROOT / "qa-output.py"
        qa_script.write_text(
            "import sys\n"
            "print('API_KEY=qa-secret-123456789')\n"
            f"print('Q' * {relwit.MAX_DURABLE_OUTPUT_CHARS + 500})\n"
            "print('Cookie: session=qa-cookie-123456789', file=sys.stderr)\n"
            f"print('W' * {relwit.MAX_DURABLE_OUTPUT_CHARS + 500}, file=sys.stderr)\n",
            encoding="utf-8",
        )
        config = relwit.load_config()
        config["supervisor"]["qa_commands"] = [
            {"mode": "argv", "argv": [sys.executable, str(qa_script)]}
        ]
        relwit.save_config(config)

        code, output, error = self.invoke("supervisor", "qa")
        self.assertEqual((code, error), (0, ""))
        result = json.loads(output)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["provenance"], "local")
        self.assertEqual(result["source"], "configured supervisor.qa_commands")
        command_result = result["commands"][0]
        self.assertGreater(command_result["stdout"]["captured_chars"], relwit.MAX_DURABLE_OUTPUT_CHARS)
        self.assertTrue(command_result["stdout"]["truncated"])
        self.assertLessEqual(command_result["stdout"]["preview_chars"], relwit.MAX_DURABLE_OUTPUT_CHARS)
        self.assertGreater(command_result["stderr"]["captured_chars"], relwit.MAX_DURABLE_OUTPUT_CHARS)
        self.assertTrue(command_result["stderr"]["truncated"])
        self.assertLessEqual(command_result["stderr"]["preview_chars"], relwit.MAX_DURABLE_OUTPUT_CHARS)
        for secret in ("qa-secret-123456789", "qa-cookie-123456789"):
            self.assertNotIn(secret, output)

        evidence_path = relwit.ROOT / result["evidence"]
        evidence = evidence_path.read_text(encoding="utf-8")
        self.assertIn("provenance: `local`", evidence)
        self.assertIn("output_budget_chars", evidence)
        for secret in ("qa-secret-123456789", "qa-cookie-123456789"):
            self.assertNotIn(secret, evidence)
        spool_rel = result["local_spool"]
        self.assertTrue(spool_rel.startswith("work/.runtime-output/"))
        self.assertTrue((relwit.ROOT / spool_rel).is_file())
        self.assertNotIn("qa-secret-123456789", (relwit.ROOT / spool_rel).read_text(encoding="utf-8"))

    def test_qa_argv_mode_is_shell_false_and_preserves_real_arguments(self) -> None:
        argument_log = relwit.ROOT / "qa-argv-arguments.json"
        shell_side_effect = relwit.ROOT / "qa-shell-side-effect.txt"
        malicious = f"; echo injected > {shell_side_effect}"
        script = (
            "import json, pathlib, sys\n"
            "pathlib.Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:], ensure_ascii=False), encoding='utf-8')\n"
        )
        config = self.configure_qa(
            self.qa_python(script, str(argument_log), malicious, "a path with spaces/✓")
        )
        with mock.patch.object(relwit.subprocess, "run", wraps=subprocess.run) as run_mock:
            result = relwit.run_qa(config, "argv-arguments-test")

        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["commands"][0]["execution_mode"], "argv")
        self.assertTrue(any(call.kwargs.get("shell") is False for call in run_mock.call_args_list))
        self.assertEqual(json.loads(argument_log.read_text(encoding="utf-8")), [malicious, "a path with spaces/✓"])
        self.assertFalse(shell_side_effect.exists())

    def test_qa_argv_failure_preserves_nonzero_exit(self) -> None:
        config = self.configure_qa(self.qa_python("import sys; raise SystemExit(7)"))
        result = relwit.run_qa(config, "argv-failure-test")

        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["commands"][0]["returncode"], 7)
        self.assertEqual(result["commands"][0]["execution_mode"], "argv")

    def test_qa_timeout_preserves_bounded_failure_evidence(self) -> None:
        config = self.configure_qa(self.qa_python("import time; time.sleep(2)"))
        config["supervisor"]["qa_timeout_seconds"] = 1
        relwit.save_config(config)
        result = relwit.run_qa(config, "argv-timeout-test")

        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["commands"][0]["returncode"], 124)
        self.assertEqual(result["commands"][0]["execution_mode"], "argv")
        self.assertTrue(result["local_spool"].startswith("work/.runtime-output/"))

    def test_qa_shell_execution_requires_explicit_opt_in_and_records_provenance(self) -> None:
        shell_spec = {
            "mode": "shell",
            "command": f'"{sys.executable}" -c "print(\'shell-opt-in\')"',
        }
        config = self.configure_qa(shell_spec)
        with mock.patch.object(relwit.subprocess, "run", wraps=subprocess.run) as run_mock:
            result = relwit.run_qa(config, "shell-opt-in-test")

        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["execution_modes"], ["shell"])
        self.assertTrue(any(call.kwargs.get("shell") is True for call in run_mock.call_args_list))
        self.assertIn("execution_mode: `shell`", (relwit.ROOT / result["evidence"]).read_text(encoding="utf-8"))

    def test_qa_validator_rejects_legacy_and_ambiguous_command_shapes(self) -> None:
        invalid_specs = [
            "python -m unittest",
            {"mode": "argv", "argv": ["python"], "command": "python"},
            {"mode": "shell", "argv": ["python"]},
            {"mode": "unknown", "argv": ["python"]},
            {"mode": [], "argv": ["python"]},
            {"mode": "argv", "argv": "python"},
            {"mode": "argv", "argv": ["python"], "unexpected": True},
            {1: "not-a-field", "mode": "argv", "argv": ["python"]},
        ]
        legacy_errors: list[str] = []
        for spec in invalid_specs:
            config = relwit.load_config()
            config["supervisor"]["qa_commands"] = [spec]
            errors: list[str] = []
            relwit.validate_config(config, errors)
            self.assertTrue(errors, spec)
            if isinstance(spec, str):
                legacy_errors = errors
        self.assertTrue(legacy_errors)
        self.assertIn("legacy command strings are rejected", legacy_errors[0])

    def test_qa_execution_mode_change_stales_existing_source_bound_qa(self) -> None:
        config, result = self.successful_qa()
        changed_config = copy.deepcopy(config)
        changed_config["supervisor"]["qa_commands"] = [
            {"mode": "shell", "command": f'"{sys.executable}" -c "print(\'changed\')"'}
        ]

        self.assertEqual(relwit.validate_qa_source(config, {"last_qa": result})["status"], "valid")
        self.assertEqual(relwit.validate_qa_source(changed_config, {"last_qa": result})["status"], "QA_STALE")

    def test_repository_qa_configuration_uses_explicit_argv_specs(self) -> None:
        repository_config = json.loads(
            (self.original_root / "relwit.config.json").read_text(encoding="utf-8")
        )
        commands = repository_config["supervisor"]["qa_commands"]
        self.assertTrue(commands)
        self.assertTrue(all(command.get("mode") == "argv" for command in commands))
        self.assertTrue(all(isinstance(command.get("argv"), list) for command in commands))

    def test_qa_source_fingerprint_is_valid_when_source_is_unchanged(self) -> None:
        config, result = self.successful_qa()
        gates, _ = relwit.production_snapshot(config, self.release_data_with_done_task(), {"last_qa": result})
        self.assertEqual(dict(gates)["qa"], "pass")
        self.assertEqual(dict(gates)["qa_source_state"], "pass")

    def test_qa_source_fingerprint_stales_when_untracked_source_changes(self) -> None:
        config, result = self.successful_qa()
        source = relwit.ROOT / "src" / "article.py"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("version one", encoding="utf-8")
        self.assertEqual(relwit.validate_qa_source(config, {"last_qa": result})["status"], "QA_STALE")
        gates, ready = relwit.production_snapshot(config, self.release_data_with_done_task(), {"last_qa": result})
        self.assertEqual(dict(gates)["qa_source_state"], "QA_STALE")
        self.assertEqual(dict(gates)["qa"], "fail")
        self.assertFalse(ready)

    def test_qa_source_fingerprint_stales_when_test_file_changes(self) -> None:
        config, result = self.successful_qa()
        test_file = relwit.ROOT / "tests" / "test_article.py"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text("assert True", encoding="utf-8")
        self.assertEqual(relwit.validate_qa_source(config, {"last_qa": result})["status"], "QA_STALE")

    def test_qa_source_fingerprint_stales_when_qa_contract_changes(self) -> None:
        config, result = self.successful_qa()
        changed_config = self.configure_qa(self.qa_python("print('qa-contract-changed')"))
        self.assertNotEqual(config["supervisor"]["qa_commands"], changed_config["supervisor"]["qa_commands"])
        self.assertEqual(relwit.validate_qa_source(changed_config, {"last_qa": result})["status"], "QA_STALE")

    def test_qa_fingerprint_records_dirty_git_source_state(self) -> None:
        source = relwit.ROOT / "src" / "dirty.py"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("baseline", encoding="utf-8")
        config = self.configure_qa()
        self.initialize_git_baseline()
        source.write_text("working tree change", encoding="utf-8")

        result = relwit.run_qa(config, "dirty-source-test")

        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["source_vcs"], "git")
        self.assertEqual(result["source_dirty_state"], "dirty")
        self.assertTrue(result["source_dirty"])
        self.assertEqual(len(result["source_head_sha"]), 40)
        self.assertGreater(result["source_dirty_path_count"], 0)
        source.write_text("second working tree change", encoding="utf-8")
        self.assertEqual(relwit.validate_qa_source(config, {"last_qa": result})["status"], "QA_STALE")

    def test_clean_qa_becomes_stale_when_git_dirty_state_changes(self) -> None:
        source = relwit.ROOT / "src" / "clean.py"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("baseline", encoding="utf-8")
        config = self.configure_qa()
        self.initialize_git_baseline()

        result = relwit.run_qa(config, "clean-source-test")

        self.assertEqual(result["source_vcs"], "git")
        self.assertEqual(result["source_dirty_state"], "clean")
        source.write_text("now dirty", encoding="utf-8")
        self.assertEqual(relwit.validate_qa_source(config, {"last_qa": result})["status"], "QA_STALE")

    def test_volatile_control_plane_updates_do_not_stale_qa(self) -> None:
        config, result = self.successful_qa()
        generated_paths = (
            "work/registry.json",
            "work/items/generated.md",
            "work/reports/generated.md",
            "work/checkpoints/generated.md",
            "work/evidence/generated.md",
            "work/.runtime-output/generated.md",
            "work/SUPERVISOR_REPORT.md",
            "work/supervisor/generated.json",
        )
        for relative in generated_paths:
            path = relwit.ROOT / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("control-plane update", encoding="utf-8")
        self.assertEqual(relwit.validate_qa_source(config, {"last_qa": result})["status"], "valid")

    def test_qa_rerun_on_new_source_state_restores_validity(self) -> None:
        config, first = self.successful_qa()
        source = relwit.ROOT / "src" / "rerun.py"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("new source state", encoding="utf-8")
        self.assertEqual(relwit.validate_qa_source(config, {"last_qa": first})["status"], "QA_STALE")

        second = relwit.run_qa(config, "rerun-source-test")

        self.assertNotEqual(first["source_fingerprint"], second["source_fingerprint"])
        self.assertEqual(relwit.validate_qa_source(config, {"last_qa": second})["status"], "valid")

    def test_release_durability_passes_for_clean_committed_git_source(self) -> None:
        config = self.configure_qa()
        self.initialize_git_baseline()
        result = relwit.run_qa(config, "durability-clean-test")

        snapshot = relwit.release_durability_snapshot(config, {"last_qa": result})

        self.assertEqual(snapshot["status"], "pass")
        self.assertEqual(snapshot["vcs"], "git")
        self.assertEqual(snapshot["dirty_state"], "clean")
        self.assertEqual(snapshot["untracked_path_count"], 0)
        self.assertEqual(snapshot["qa_source_state"], "valid")
        self.assertEqual(snapshot["upstream_state"], "none")
        self.assertEqual(snapshot["upstream_relation"], "none")

    def test_dirty_tracked_source_keeps_qa_valid_but_fails_release_durability(self) -> None:
        source = relwit.ROOT / "src" / "dirty-release.py"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("baseline", encoding="utf-8")
        config = self.configure_qa()
        self.initialize_git_baseline()
        source.write_text("dirty", encoding="utf-8")
        result = relwit.run_qa(config, "durability-dirty-test")

        snapshot = relwit.release_durability_snapshot(config, {"last_qa": result})

        self.assertEqual(relwit.validate_qa_source(config, {"last_qa": result})["status"], "valid")
        self.assertEqual(snapshot["status"], "fail")
        self.assertEqual(snapshot["dirty_state"], "dirty")
        self.assertEqual(snapshot["qa_source_state"], "valid")

    def test_staged_source_change_fails_release_durability(self) -> None:
        source = relwit.ROOT / "src" / "staged-release.py"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("baseline", encoding="utf-8")
        config = self.configure_qa()
        self.initialize_git_baseline()
        source.write_text("staged", encoding="utf-8")
        self.git_output("add", "src/staged-release.py")
        result = relwit.run_qa(config, "durability-staged-test")

        snapshot = relwit.release_durability_snapshot(config, {"last_qa": result})

        self.assertEqual(snapshot["status"], "fail")
        self.assertEqual(snapshot["dirty_state"], "dirty")
        self.assertGreater(snapshot["dirty_path_count"], 0)

    def test_nonignored_untracked_source_fails_release_durability(self) -> None:
        config = self.configure_qa()
        self.initialize_git_baseline()
        source = relwit.ROOT / "src" / "untracked-release.py"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("untracked", encoding="utf-8")
        result = relwit.run_qa(config, "durability-untracked-test")

        snapshot = relwit.release_durability_snapshot(config, {"last_qa": result})

        self.assertEqual(snapshot["status"], "fail")
        self.assertEqual(snapshot["untracked_path_count"], 1)

    def test_volatile_control_plane_writes_do_not_fail_release_durability(self) -> None:
        config = self.configure_qa()
        self.initialize_git_baseline()
        result = relwit.run_qa(config, "durability-volatile-test")
        volatile = relwit.ROOT / "work" / "evidence" / "volatile-only.md"
        volatile.write_text("runtime evidence", encoding="utf-8")

        snapshot = relwit.release_durability_snapshot(config, {"last_qa": result})

        self.assertEqual(snapshot["status"], "pass")
        self.assertEqual(snapshot["dirty_state"], "clean")
        self.assertEqual(snapshot["untracked_path_count"], 0)

    def test_convenience_supervisor_report_is_volatile_for_release_durability(self) -> None:
        config = self.configure_qa()
        self.initialize_git_baseline()
        result = relwit.run_qa(config, "durability-report-write-test")
        report_path = relwit.ROOT / "work" / "SUPERVISOR_REPORT.md"
        report_path.write_text("generated convenience report", encoding="utf-8")

        snapshot = relwit.release_durability_snapshot(config, {"last_qa": result})

        self.assertEqual(snapshot["status"], "pass")
        self.assertEqual(snapshot["dirty_state"], "clean")
        self.assertEqual(snapshot["untracked_path_count"], 0)

    def test_committing_only_volatile_evidence_keeps_qa_valid(self) -> None:
        # Redundancy study s6.2: committing QA evidence must not invalidate that QA.
        config = self.configure_qa()
        self.initialize_git_baseline()
        first = relwit.run_qa(config, "durability-commit-transition-test")
        head_before = self.git_output("rev-parse", "HEAD").strip()

        marker = relwit.ROOT / "work" / "evidence" / "post-qa-commit.md"
        marker.write_text("post QA bookkeeping", encoding="utf-8")
        self.git_output("add", "-f", "work/evidence/post-qa-commit.md")
        self.git_output("commit", "-qm", "post QA bookkeeping")
        snapshot = relwit.release_durability_snapshot(config, {"last_qa": first})

        self.assertNotEqual(self.git_output("rev-parse", "HEAD").strip(), head_before)
        self.assertEqual(snapshot["qa_source_state"], "valid")
        self.assertEqual(snapshot["status"], "pass")
        self.assertEqual(first["source_fingerprint"], snapshot["fingerprint"])

    def test_message_only_amend_keeps_qa_valid(self) -> None:
        config = self.configure_qa()
        self.initialize_git_baseline()
        result = relwit.run_qa(config, "amend-message-test")
        self.git_output("commit", "-q", "--amend", "-m", "reworded baseline")
        self.assertEqual(relwit.validate_qa_source(config, {"last_qa": result})["status"], "valid")

    def test_qa_on_dirty_tree_stays_valid_after_committing_identical_content(self) -> None:
        source = relwit.ROOT / "src" / "commit-after-qa.py"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("baseline", encoding="utf-8")
        config = self.configure_qa()
        self.initialize_git_baseline()
        source.write_text("verified content", encoding="utf-8")
        result = relwit.run_qa(config, "dirty-then-commit-test")
        self.assertEqual(result["source_dirty_state"], "dirty")

        self.git_output("commit", "-qam", "commit exactly what QA verified")
        snapshot = relwit.release_durability_snapshot(config, {"last_qa": result})

        self.assertEqual(snapshot["qa_source_state"], "valid")
        self.assertEqual(snapshot["status"], "pass")

    def test_gate_fails_without_a_passing_qa_record(self) -> None:
        self.configure_qa()
        code, stdout, stderr = self.invoke("gate")
        self.assertEqual(code, 1)
        self.assertIn("GATE FAIL: no passing QA record", stderr)
        self.assertNotIn("GATE PASS", stdout)

    def test_gate_passes_for_qa_bound_to_current_source_and_fails_after_drift(self) -> None:
        self.configure_qa()
        code, _, _ = self.invoke("qa")
        self.assertEqual(code, 0)
        code, stdout, _ = self.invoke("gate")
        self.assertEqual(code, 0)
        self.assertIn("GATE PASS", stdout)

        source = relwit.ROOT / "src" / "drift.py"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("changed after QA", encoding="utf-8")
        code, _, stderr = self.invoke("gate")
        self.assertEqual(code, 1)
        self.assertIn("GATE FAIL: QA_STALE", stderr)

    def test_gate_require_clean_adds_git_durability(self) -> None:
        source = relwit.ROOT / "src" / "gate-clean.py"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("baseline", encoding="utf-8")
        self.configure_qa()
        self.initialize_git_baseline()
        source.write_text("uncommitted but verified", encoding="utf-8")
        self.assertEqual(self.invoke("qa")[0], 0)

        self.assertEqual(self.invoke("gate")[0], 0)
        code, _, stderr = self.invoke("gate", "--require-clean")
        self.assertEqual(code, 1)
        self.assertIn("durability fail", stderr)

        self.git_output("commit", "-qam", "commit verified content")
        code, stdout, _ = self.invoke("gate", "--require-clean", "--json")
        self.assertEqual(code, 0)
        payload = json.loads(stdout)
        self.assertEqual(payload["status"], "pass")
        self.assertEqual(payload["durability"], "pass")
        self.assertEqual(payload["qa_source_state"], "valid")

    def test_gate_works_in_assurance_only_project_without_supervision_control_plane(self) -> None:
        with tempfile.TemporaryDirectory() as external:
            root = Path(external)
            (root / "app.py").write_text("print('app')\n", encoding="utf-8")
            config = copy.deepcopy(relwit.DEFAULT_CONFIG)
            config["supervisor"]["qa_commands"] = [self.qa_python("print('qa-pass')")]
            (root / "relwit.config.json").write_text(json.dumps(config), encoding="utf-8")
            for command in (
                ["git", "init", "-q"],
                ["git", "config", "user.email", "relwit-tests@example.test"],
                ["git", "config", "user.name", "RelWit Tests"],
                ["git", "add", "-A"],
                ["git", "commit", "-qm", "external baseline"],
            ):
                subprocess.run(command, cwd=root, capture_output=True, check=True)

            self.assertEqual(self.invoke("--root", external, "qa")[0], 0)
            code, stdout, stderr = self.invoke("--root", external, "gate", "--require-clean")

            self.assertEqual(code, 0, stderr)
            self.assertIn("GATE PASS", stdout)
            self.assertFalse((root / "AGENTS.md").exists())
            self.assertFalse((root / "work" / "registry.json").exists())

    @unittest.skipIf(os.name == "nt", "POSIX file modes")
    def test_file_mode_change_after_qa_is_stale(self) -> None:
        # Independent review of 939aba2, P1: content-only hashing must not ignore modes.
        script = relwit.ROOT / "run.sh"
        script.write_text("#!/bin/sh\necho run\n", encoding="utf-8")
        script.chmod(0o755)
        config = self.configure_qa()
        self.initialize_git_baseline()
        result = relwit.run_qa(config, "mode-test")
        script.chmod(0o644)
        self.git_output("commit", "-qam", "drop executable bit")
        self.assertEqual(relwit.validate_qa_source(config, {"last_qa": result})["status"], "QA_STALE")

    @unittest.skipIf(os.name == "nt", "POSIX symlinks")
    def test_symlink_swap_after_qa_is_stale(self) -> None:
        (relwit.ROOT / "data.txt").write_text("same bytes", encoding="utf-8")
        (relwit.ROOT / "copy.txt").write_text("same bytes", encoding="utf-8")
        config = self.configure_qa()
        self.initialize_git_baseline()
        result = relwit.run_qa(config, "symlink-test")
        (relwit.ROOT / "copy.txt").unlink()
        os.symlink("data.txt", relwit.ROOT / "copy.txt")
        self.assertEqual(relwit.validate_qa_source(config, {"last_qa": result})["status"], "QA_STALE")

    def test_committing_a_verified_deletion_keeps_qa_valid(self) -> None:
        # Review P2: QA ran with the file already deleted; committing that deletion is the same source.
        (relwit.ROOT / "old.txt").write_text("obsolete", encoding="utf-8")
        config = self.configure_qa()
        self.initialize_git_baseline()
        (relwit.ROOT / "old.txt").unlink()
        result = relwit.run_qa(config, "deletion-test")
        self.git_output("add", "-A", ".")
        self.git_output("commit", "-qm", "delete obsolete file")
        snapshot = relwit.release_durability_snapshot(config, {"last_qa": result})
        self.assertEqual(snapshot["qa_source_state"], "valid")

    def test_local_only_ignore_is_never_written_outside_declared_volatile_paths(self) -> None:
        for spool in (".", "src"):
            config = relwit.load_config()
            config["paths"]["runtime_spool"] = spool
            relwit.save_config(config)
            relwit.ensure_layout()
            ignore = relwit.ROOT / spool / ".gitignore"
            self.assertFalse(ignore.exists() and ignore.read_text(encoding="utf-8") == relwit.LOCAL_ONLY_IGNORE, spool)
            errors: list[str] = []
            relwit.validate_config(config, errors)
            self.assertIn("config.paths.runtime_spool must be inside config.release_source.volatile_paths", errors)

    def test_telemetry_save_marks_its_directory_local_only(self) -> None:
        config = relwit.load_config()
        ignore = relwit.telemetry_store_path(config).parent / ".gitignore"
        if ignore.exists():
            ignore.unlink()
        relwit.save_telemetry(config, {"events": []})
        self.assertEqual(ignore.read_text(encoding="utf-8"), relwit.LOCAL_ONLY_IGNORE)

    def test_gate_output_cannot_be_forged_by_state_values(self) -> None:
        config = self.configure_qa()
        state = relwit.load_supervisor_state(config)
        state["last_qa"] = {"status": "x\nGATE PASS\x1b[2K", "source_fingerprint": "0" * 64}
        relwit.save_supervisor_state(config, state)
        code, stdout, stderr = self.invoke("gate")
        self.assertEqual(code, 1)
        self.assertNotIn("\nGATE PASS", "\n" + stdout + stderr)

    def test_record_from_earlier_fingerprint_algorithm_is_named_as_such(self) -> None:
        config, result = self.successful_qa()
        legacy = {key: value for key, value in result.items() if key != "source_fingerprint_algorithm"}
        legacy["source_fingerprint"] = "f" * 64
        verdict = relwit.validate_qa_source(config, {"last_qa": legacy})
        self.assertEqual(verdict["status"], "QA_STALE")
        self.assertIn("earlier source-fingerprint algorithm", verdict["reason"])

    def test_external_init_keeps_raw_spool_and_telemetry_out_of_git(self) -> None:
        with tempfile.TemporaryDirectory() as external:
            root = Path(external)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            self.assertEqual(self.invoke("--root", external, "init")[0], 0)
            config = json.loads((root / "relwit.config.json").read_text(encoding="utf-8"))
            config["supervisor"]["qa_commands"] = [self.qa_python("print('token=abc')")]
            (root / "relwit.config.json").write_text(json.dumps(config), encoding="utf-8")
            (root / "app.py").write_text("print('app')\n", encoding="utf-8")
            for command in (
                ["git", "config", "user.email", "relwit-tests@example.test"],
                ["git", "config", "user.name", "RelWit Tests"],
                ["git", "add", "app.py", "relwit.config.json"],
                ["git", "commit", "-qm", "external baseline"],
            ):
                subprocess.run(command, cwd=root, capture_output=True, check=True)
            self.assertEqual(self.invoke("--root", external, "supervisor", "qa")[0], 0)

            spool_files = [p for p in (root / "work" / ".runtime-output").iterdir() if p.name != ".gitignore"]
            self.assertTrue(spool_files)
            untracked = subprocess.run(
                ["git", "ls-files", "--others", "--exclude-standard"],
                cwd=root, capture_output=True, text=True, check=True,
            ).stdout.splitlines()
            self.assertFalse([path for path in untracked if path.startswith("work/.runtime-output/")])
            self.assertFalse([path for path in untracked if path.startswith("work/telemetry/")])
            self.assertTrue([path for path in untracked if path.startswith("work/evidence/")])

    def test_commit_changing_source_after_qa_requires_rerun(self) -> None:
        source = relwit.ROOT / "src" / "changed-after-qa.py"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("baseline", encoding="utf-8")
        config = self.configure_qa()
        self.initialize_git_baseline()
        first = relwit.run_qa(config, "source-commit-transition-test")

        source.write_text("changed after QA", encoding="utf-8")
        self.git_output("commit", "-qam", "change source after QA")
        stale = relwit.release_durability_snapshot(config, {"last_qa": first})
        self.assertEqual(stale["qa_source_state"], "QA_STALE")
        self.assertEqual(stale["status"], "fail")

        second = relwit.run_qa(config, "source-commit-rerun-test")
        second_snapshot = relwit.release_durability_snapshot(config, {"last_qa": second})
        self.assertEqual(second_snapshot["status"], "pass")
        self.assertEqual(second_snapshot["qa_source_state"], "valid")

    def test_non_git_workspace_is_explicitly_degraded(self) -> None:
        config, result = self.successful_qa()

        snapshot = relwit.release_durability_snapshot(config, {"last_qa": result})
        gates, ready = relwit.production_snapshot(config, self.release_data_with_done_task(), {"last_qa": result})

        self.assertEqual(snapshot["vcs"], "filesystem")
        self.assertEqual(snapshot["status"], "manual")
        self.assertEqual(snapshot["upstream_state"], "not_applicable")
        self.assertEqual(dict(gates)["release_source_durability"], "manual")
        self.assertFalse(ready)

    def test_supervisor_report_records_release_provenance(self) -> None:
        config = self.configure_qa()
        self.initialize_git_baseline()
        result = relwit.run_qa(config, "durability-report-test")
        data = self.release_data_with_done_task()
        data["items"]["RW-9999"] = {
            "id": "RW-9999",
            "title": "Durability report fixture",
            "status": "done",
            "evidence": [],
            "reports": [],
        }
        report, _ = relwit.build_supervisor_report(
            config,
            data,
            {"last_qa": result},
            "durability-report-cycle",
            [],
            [],
            result,
        )

        self.assertIn("## Release source", report)
        self.assertIn(f"- source_fingerprint: `{result['source_fingerprint']}`", report)
        self.assertIn(f"- qa_source_fingerprint: `{result['source_fingerprint']}`", report)
        self.assertIn(f"- qa_config_fingerprint: `{result['qa_config_fingerprint']}`", report)
        self.assertIn("- local_durability: `pass`", report)
        self.assertIn("- upstream_relation: `none`", report)
        self.assertIn("`release_source_durability`: `pass`", report)

    def test_upstream_metadata_is_local_and_does_not_require_origin_sync(self) -> None:
        config = self.configure_qa()
        self.initialize_git_baseline()
        branch = self.git_output("symbolic-ref", "--quiet", "--short", "HEAD")
        previous = self.git_output("rev-parse", "HEAD")
        source = relwit.ROOT / "src" / "ahead.py"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("ahead", encoding="utf-8")
        self.git_output("add", "src/ahead.py")
        self.git_output("commit", "-qm", "ahead commit")
        self.configure_tracking_ref(branch, previous)
        result = relwit.run_qa(config, "durability-ahead-test")

        snapshot = relwit.release_durability_snapshot(config, {"last_qa": result})

        self.assertEqual(snapshot["status"], "pass")
        self.assertEqual(snapshot["upstream_state"], "known")
        self.assertEqual(snapshot["upstream_relation"], "ahead")
        self.assertEqual(snapshot["ahead"], 1)
        self.assertEqual(snapshot["behind"], 0)

    def test_configured_but_unavailable_upstream_is_explicitly_unknown(self) -> None:
        config = self.configure_qa()
        self.initialize_git_baseline()
        branch = self.git_output("symbolic-ref", "--quiet", "--short", "HEAD")
        self.git_output("remote", "add", "origin", str(relwit.ROOT))
        self.git_output("config", f"branch.{branch}.remote", "origin")
        self.git_output("config", f"branch.{branch}.merge", f"refs/heads/{branch}")
        result = relwit.run_qa(config, "durability-unknown-upstream-test")

        snapshot = relwit.release_durability_snapshot(config, {"last_qa": result})

        self.assertEqual(snapshot["status"], "pass")
        self.assertEqual(snapshot["upstream_state"], "unknown")
        self.assertEqual(snapshot["upstream_relation"], "unknown")
        self.assertIsNone(snapshot["ahead"])
        self.assertIsNone(snapshot["behind"])

    def test_upstream_metadata_reports_behind_and_diverged_without_failing_local_durability(self) -> None:
        config = self.configure_qa()
        self.initialize_git_baseline()
        branch = self.git_output("symbolic-ref", "--quiet", "--short", "HEAD")
        base = self.git_output("rev-parse", "HEAD")
        base_tree = self.git_output("rev-parse", "HEAD^{tree}")
        remote_only = self.git_output("commit-tree", base_tree, "-p", base, "-m", "remote-only")
        self.configure_tracking_ref(branch, remote_only)
        behind_result = relwit.run_qa(config, "durability-behind-test")
        behind = relwit.release_durability_snapshot(config, {"last_qa": behind_result})

        self.assertEqual(behind["status"], "pass")
        self.assertEqual(behind["upstream_relation"], "behind")
        self.assertEqual(behind["ahead"], 0)
        self.assertEqual(behind["behind"], 1)

        source = relwit.ROOT / "src" / "diverged.py"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("local-only", encoding="utf-8")
        self.git_output("add", "src/diverged.py")
        self.git_output("commit", "-qm", "local-only")
        local_base = self.git_output("rev-parse", "HEAD~1")
        local_base_tree = self.git_output("rev-parse", "HEAD~1^{tree}")
        remote_diverged = self.git_output("commit-tree", local_base_tree, "-p", local_base, "-m", "remote-diverged")
        self.git_output("update-ref", f"refs/remotes/origin/{branch}", remote_diverged)
        diverged_result = relwit.run_qa(config, "durability-diverged-test")
        diverged = relwit.release_durability_snapshot(config, {"last_qa": diverged_result})

        self.assertEqual(diverged["status"], "pass")
        self.assertEqual(diverged["upstream_relation"], "diverged")
        self.assertEqual(diverged["ahead"], 1)
        self.assertEqual(diverged["behind"], 1)

    def test_legacy_pass_without_source_fingerprint_is_stale(self) -> None:
        config = self.configure_qa()
        gates, ready = relwit.production_snapshot(
            config,
            self.release_data_with_done_task(),
            {"last_qa": {"status": "pass"}},
        )
        self.assertEqual(dict(gates)["qa_source_state"], "QA_STALE")
        self.assertEqual(dict(gates)["qa"], "fail")
        self.assertFalse(ready)

    def test_production_snapshot_checks_operational_readiness_files(self) -> None:
        (relwit.ROOT / "docs").mkdir(parents=True, exist_ok=True)
        (relwit.ROOT / "docs" / "operations.md").write_text("Operational runbook", encoding="utf-8")
        (relwit.ROOT / "docs" / "rollback.md").write_text("Rollback plan", encoding="utf-8")
        config = relwit.load_config()
        config["supervisor"]["operational_readiness_files"] = ["docs/operations.md", "docs/rollback.md"]
        config["supervisor"]["qa_commands"] = [self.qa_python("print('qa-pass')")]
        relwit.save_config(config)
        qa_result = relwit.run_qa(config, "readiness-test")
        data = relwit.load_registry()
        data["items"]["RW-9999"] = {"status": "done"}
        state = {"last_qa": qa_result}
        gates, ready = relwit.production_snapshot(config, data, state)
        self.assertEqual(dict(gates)["operational_rollback_notes"], "pass")
        self.assertEqual(dict(gates)["release_source_durability"], "manual")
        self.assertFalse(ready)

        config["supervisor"]["operational_readiness_files"] = ["docs/missing.md"]
        gates, ready = relwit.production_snapshot(config, data, state)
        self.assertEqual(dict(gates)["operational_rollback_notes"], "manual")
        self.assertFalse(ready)

    def test_bounded_context_and_checkpoint(self) -> None:
        task_id = self.new_task("Context task", "docs/context.md")
        code, output, error = self.invoke(
            "checkpoint",
            "create",
            "--name",
            "cycle one",
            "--status",
            "active",
            "--summary",
            "Created the first task",
            "--next-action",
            "Claim the task",
            "--task",
            task_id,
        )
        self.assertEqual((code, error), (0, ""))
        self.assertTrue((relwit.ROOT / output.strip()).exists())
        state = json.loads((relwit.ROOT / "work" / "supervisor" / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["last_checkpoint"], output.strip())
        code, output, error = self.invoke("context", "--task", task_id, "--max-chars", "3000")
        self.assertEqual((code, error), (0, ""))
        self.assertIn("knowledge/INDEX.md", output)
        self.assertIn(task_id, output)
        self.assertLessEqual(len(output), 3000)

    def test_project_validator_accepts_real_layout(self) -> None:
        result = subprocess.run(
            [sys.executable, "relwit/cli.py", "validate"],
            cwd=self.original_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "VALID")

    def test_context_command_runs_in_a_windows_console(self) -> None:
        result = subprocess.run(
            [sys.executable, "relwit/cli.py", "context", "--max-chars", "1200"],
            cwd=self.original_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ReleaseWitness context index", result.stdout)


if __name__ == "__main__":
    unittest.main()
