#!/usr/bin/env python3
"""ReleaseWitness (RelWit) supervisor control plane.

The CLI is deliberately small and dependency-free. It provides a durable
file protocol for a supervisor model and worker agents:

    supervisor -> assignment .md -> worker mailbox
    worker -> report .md -> supervisor report/completed log

JSON is the machine-readable state; Markdown is the human- and model-readable
handoff surface. The CLI never edits application code and never grants deploy
authority.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator



def default_root() -> Path:
    """Choose a useful project root for source-checkout and installed use."""

    source_root = Path(__file__).resolve().parents[1]
    if (source_root / "AGENTS.md").exists() and (source_root / "knowledge" / "INDEX.md").exists():
        return source_root
    return Path.cwd().resolve()


ROOT = default_root()
REGISTRY = ROOT / "work" / "registry.json"
CONFIG = ROOT / "relwit.config.json"
LOCK = ROOT / "work" / ".state.lock"

ACTIVE_WRITER_STATUSES = {"assigned", "in_progress"}
VALID_STATUSES = {
    "planned",
    "assigned",
    "in_progress",
    "reported",
    "needs_review",
    "done",
    "blocked",
    "cancelled",
}
TERMINAL_STATUSES = {"done", "cancelled"}
VALID_LEVELS = {"L0", "L1", "L2", "L3", "L4"}
VALID_AGENT_STATUSES = {"available", "busy", "paused", "offline"}
VALID_AGENT_ROLES = {"supervisor", "explorer", "planner", "worker", "reviewer", "release_gate"}
CLAIM_ROLES = {"supervisor", "explorer", "planner", "worker"}
REVIEW_ROLES = {"supervisor", "reviewer", "release_gate"}
VALID_EVIDENCE_PROVENANCES = {"local", "live", "simulation", "blocked", "operator-confirmed", "legacy"}
DEFAULT_RUNNER_TIMEOUT_SECONDS = 3600
MAX_RUNNER_TIMEOUT_SECONDS = 86400
MAX_RUNNER_WAIT_SECONDS = 86400
DEFAULT_PREFLIGHT_TIMEOUT_SECONDS = 30
MAX_PREFLIGHT_TIMEOUT_SECONDS = 300
MAX_DURABLE_OUTPUT_CHARS = 4000
MAX_LOCAL_OUTPUT_CHARS = 100000
QA_EXECUTION_MODES = {"argv", "shell"}
RUNTIME_READINESS_STATES = {"ready", "unavailable", "misconfigured", "no_target", "unknown"}
RUNTIME_FAILURE_CLASSES = {
    "unavailable",
    "no_target",
    "misconfigured",
    "auth_error",
    "quota_limited",
    "timeout",
    "runtime_error",
    "unknown",
}
RUNTIME_DISPOSITIONS = {"retry", "reassign", "takeover", "needs_input"}
PREFLIGHT_RESULT_MARKER = "relwit_preflight"
RUNTIME_RESULT_MARKER = "relwit_runtime_result"
USAGE_MARKER = "relwit_usage"
TELEMETRY_SCHEMA_VERSION = 1
TELEMETRY_PROVENANCE = {"authoritative", "measured", "estimated", "unavailable"}
TELEMETRY_KINDS = {"task", "cycle", "phase", "project"}
TELEMETRY_OUTCOMES = {"assigned", "in_progress", "completed", "failed", "blocked", "cancelled", "unknown"}
SAFE_TELEMETRY_METADATA = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")

RUNTIME_REDACTION_PATTERNS = (
    (
        re.compile(
            r"(?i)((?:https?|postgres(?:ql)?)://)[^/\s:@]+:[^@\s]+@"
        ),
        r"\1[REDACTED]@",
    ),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer [REDACTED]"),
    (
        re.compile(
            r"(?i)((?:\\)?[\"']?\b(?:api[_-]?key|access[_-]?token|auth[_-]?secret|client[_-]?secret|token|"
            r"database[_-]?url|password|passwd|session[_-]?token|cookie|authorization|"
            r"private[_-]?key)\b(?:\\)?[\"']?\s*[:=]\s*(?:\\)?[\"']?)([^\\\"'\s,;)}\]]+)"
        ),
        r"\1[REDACTED]",
    ),
    (
        re.compile(r"(?i)\b(?:ghp_|github_pat_|sk-|xoxb-|xoxp-|vercel_|npm_)[A-Za-z0-9_-]{10,}"),
        "[REDACTED_TOKEN]",
    ),
    (
        re.compile(
            r"(?is)-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----.*?"
            r"(?:-----END (?:[A-Z0-9]+ )*PRIVATE KEY-----|\Z)"
        ),
        "-----BEGIN PRIVATE KEY [REDACTED]-----",
    ),
)


DEFAULT_CONFIG: dict[str, Any] = {
    "version": 2,
    "paths": {
        "agent_root": "work/agents",
        "reports_inbox": "work/reports/inbox",
        "reports_archive": "work/reports/archive",
        "reports_index": "work/reports/REPORTS.md",
        "outbox": "work/outbox",
        "completed_tasks": "work/completed/COMPLETED.md",
        "supervisor_report": "work/SUPERVISOR_REPORT.md",
        "supervisor_cycle": "work/supervisor/LATEST_CYCLE.md",
        "supervisor_state": "work/supervisor/state.json",
        "checkpoints": "work/checkpoints",
        "evidence": "work/evidence",
        "runtime_spool": "work/.runtime-output",
        "telemetry": "work/telemetry",
    },
    "release_source": {
        "version": 1,
        "volatile_paths": [
            "work/registry.json",
            "work/items",
            "work/agents",
            "work/reports",
            "work/completed",
            "work/checkpoints",
            "work/evidence",
        "work/outbox",
        "work/SUPERVISOR_REPORT.md",
        "work/supervisor",
            "work/.runtime-output",
            "work/telemetry",
            "work/.state.lock",
        ],
    },
    "supervisor": {
        "max_assignments_per_cycle": 4,
        "run_qa_each_cycle": False,
        "auto_dispatch": True,
        "qa_timeout_seconds": 900,
        "qa_commands": [],
        "operational_readiness_files": ["docs/operations.md", "docs/autopilot.md"],
        "production_gates": [
            "All acceptance criteria are evidenced",
            "Focused and integration tests pass",
            "No open P0/P1 review finding",
            "Operational and rollback notes exist",
        ],
    },
    "agents": [],
}


class RelWitError(RuntimeError):
    """Expected user-facing error from the control plane."""


def configure_root(value: str | Path) -> None:
    """Point this invocation at an existing project root.

    The CLI normally derives its root from the checkout containing this file.
    A central RelWit checkout can operate on another prepared repository by
    passing ``--root``. All state/config paths are rebound together so a
    process cannot accidentally mix two project ledgers.
    """

    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    candidate = candidate.resolve()
    if not candidate.exists():
        raise RelWitError(f"project root does not exist: {candidate}")
    if not candidate.is_dir():
        raise RelWitError(f"project root is not a directory: {candidate}")

    global ROOT, REGISTRY, CONFIG, LOCK
    ROOT = candidate
    REGISTRY = ROOT / "work" / "registry.json"
    CONFIG = ROOT / "relwit.config.json"
    LOCK = ROOT / "work" / ".state.lock"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def rel(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        temp.write_text(content, encoding="utf-8", newline="\n")
        temp.replace(path)
    finally:
        if temp.exists():
            temp.unlink()


@contextmanager
def state_lock(timeout: float = 30.0) -> Iterator[None]:
    """Acquire a short-lived exclusive lock using create-if-absent semantics."""

    LOCK.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    payload = json.dumps({"pid": os.getpid(), "created_at": now_iso()})
    while True:
        try:
            fd = os.open(str(LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, payload.encode("utf-8"))
            finally:
                os.close(fd)
            break
        except FileExistsError:
            if time.monotonic() - started >= timeout:
                try:
                    owner = LOCK.read_text(encoding="utf-8")
                except OSError:
                    owner = "unknown owner"
                raise RelWitError(f"state lock is busy: {owner}")
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            LOCK.unlink()
        except FileNotFoundError:
            pass


def safe_repo_path(value: str | Path) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = ROOT / candidate
    candidate = candidate.resolve()
    try:
        candidate.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise RelWitError(f"configured path leaves project root: {value}") from exc
    return candidate


def load_config() -> dict[str, Any]:
    if not CONFIG.exists():
        return copy.deepcopy(DEFAULT_CONFIG)
    try:
        value = json.loads(CONFIG.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RelWitError(f"invalid JSON in {rel(CONFIG)}: {exc}") from exc
    if not isinstance(value, dict):
        raise RelWitError(f"{rel(CONFIG)} must contain a JSON object")
    merged = copy.deepcopy(DEFAULT_CONFIG)
    merged.update({key: value[key] for key in value if key not in {"paths", "supervisor"}})
    raw_paths = value.get("paths", {})
    if isinstance(raw_paths, dict):
        merged["paths"].update(raw_paths)
    elif "paths" in value:
        merged["paths"] = raw_paths
    raw_supervisor = value.get("supervisor", {})
    if isinstance(raw_supervisor, dict):
        merged["supervisor"].update(raw_supervisor)
    elif "supervisor" in value:
        merged["supervisor"] = raw_supervisor
    if not isinstance(merged.get("agents"), list):
        raise RelWitError("config.agents must be an array")
    return merged


def save_config(config: dict[str, Any]) -> None:
    atomic_write(CONFIG, json.dumps(config, indent=2, ensure_ascii=False) + "\n")


def path_for(config: dict[str, Any], key: str) -> Path:
    paths = config.get("paths", {})
    if not isinstance(paths, dict):
        raise RelWitError("config.paths must be an object")
    value = paths.get(key, DEFAULT_CONFIG["paths"].get(key))
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise RelWitError(f"missing configured path: {key}")
    return safe_repo_path(value)


def telemetry_store_path(config: dict[str, Any]) -> Path:
    """Return the ignored local event store used for execution telemetry."""

    return path_for(config, "telemetry") / "events.json"


def load_telemetry(config: dict[str, Any]) -> dict[str, Any]:
    path = telemetry_store_path(config)
    if not path.exists():
        return {"version": TELEMETRY_SCHEMA_VERSION, "events": {}}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RelWitError(f"invalid telemetry store: {exc}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("events"), dict):
        raise RelWitError("telemetry store must contain an object named events")
    return value


def save_telemetry(config: dict[str, Any], telemetry: dict[str, Any]) -> None:
    telemetry["version"] = TELEMETRY_SCHEMA_VERSION
    telemetry["updated_at"] = now_iso()
    ensure_local_only_dir(config, telemetry_store_path(config).parent)
    atomic_write(
        telemetry_store_path(config),
        json.dumps(telemetry, indent=2, ensure_ascii=False) + "\n",
    )


def safe_telemetry_metadata(value: Any) -> str | None:
    """Allow only small identifier-like metadata; never persist free-form output."""

    if not isinstance(value, str) or not SAFE_TELEMETRY_METADATA.fullmatch(value):
        return None
    if "://" in value or "@" in value:
        return None
    return value


def unavailable_usage(reason: str, source: str = "runtime") -> dict[str, Any]:
    return {
        "status": "unavailable",
        "provenance": "unavailable",
        "source": safe_telemetry_metadata(source) or "runtime",
        "reason": "usage unavailable: " + ("invalid or missing machine-readable usage" if reason else "not exposed"),
    }


def normalize_usage_envelope(value: Any, *, source: str = "adapter") -> dict[str, Any]:
    """Normalize an explicit JSON usage envelope; prose is never interpreted."""

    if not isinstance(value, dict) or value.get(USAGE_MARKER) != 1:
        return unavailable_usage("missing", source)
    numeric_fields = (
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "total_tokens",
    )
    numbers: dict[str, int] = {}
    for field in numeric_fields:
        raw = value.get(field)
        if raw is None:
            continue
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
            return unavailable_usage("invalid", source)
        numbers[field] = raw
    if not numbers:
        return unavailable_usage("missing", source)
    if (
        "total_tokens" in numbers
        and "input_tokens" in numbers
        and "output_tokens" in numbers
        and numbers["total_tokens"] != numbers["input_tokens"] + numbers["output_tokens"]
    ):
        return unavailable_usage("ambiguous", source)
    if value.get("authoritative") is True:
        provenance = "authoritative"
    else:
        raw_provenance = value.get("provenance")
        provenance = raw_provenance if isinstance(raw_provenance, str) else "unavailable"
        if provenance not in TELEMETRY_PROVENANCE or provenance == "unavailable":
            return unavailable_usage("missing provenance", source)
    normalized: dict[str, Any] = {
        "status": "available",
        "provenance": provenance,
        "source": safe_telemetry_metadata(source) or "adapter",
    }
    for field, number in numbers.items():
        normalized[field] = number
    for field in ("provider", "runtime", "model", "usage_id"):
        metadata = safe_telemetry_metadata(value.get(field))
        if metadata is not None:
            normalized[field] = metadata
    return normalized


def usage_from_runtime_streams(stdout: Any = "", stderr: Any = "") -> dict[str, Any]:
    """Accept usage only from a complete marker-bearing JSON adapter response."""

    for stream in (stdout, stderr):
        payload = parse_machine_runtime_result(stream, USAGE_MARKER)
        if payload is not None:
            return normalize_usage_envelope(payload, source="adapter")
    return unavailable_usage("missing", "adapter")


def _parse_iso_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return None


def _duration_ms_from_timestamps(started_at: Any, completed_at: Any) -> int | None:
    started = _parse_iso_timestamp(started_at)
    completed = _parse_iso_timestamp(completed_at)
    if started is None or completed is None or completed < started:
        return None
    return max(0, round((completed - started).total_seconds() * 1000))


def telemetry_event_id(kind: str, entity_id: str, attempt: int | None = None) -> str:
    suffix = f":attempt:{attempt}" if attempt is not None else ""
    return f"{kind}:{entity_id}{suffix}"


def sanitize_telemetry_usage(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("status") != "available":
        source = value.get("source") if isinstance(value, dict) else "runtime"
        return unavailable_usage("invalid", source if isinstance(source, str) else "runtime")
    provenance = value.get("provenance")
    if provenance not in TELEMETRY_PROVENANCE - {"unavailable"}:
        return unavailable_usage("invalid", "runtime")
    safe: dict[str, Any] = {
        "status": "available",
        "provenance": provenance,
        "source": safe_telemetry_metadata(value.get("source")) or "runtime",
    }
    for field in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens", "total_tokens"):
        raw = value.get(field)
        if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0:
            safe[field] = raw
    if (
        "total_tokens" in safe
        and "input_tokens" in safe
        and "output_tokens" in safe
        and safe["total_tokens"] != safe["input_tokens"] + safe["output_tokens"]
    ):
        return unavailable_usage("ambiguous", "runtime")
    for field in ("provider", "runtime", "model", "usage_id"):
        metadata = safe_telemetry_metadata(value.get(field))
        if metadata is not None:
            safe[field] = metadata
    if not any(field in safe for field in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens", "total_tokens")):
        return unavailable_usage("missing", safe["source"])
    return safe


def sanitize_telemetry_event_fields(fields: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "kind", "entity_id", "task_id", "task_ids", "outcome", "agent_id", "role",
        "provider", "runtime", "model", "attempt", "retry", "takeover", "supersedes",
        "assigned_at", "started_at", "completed_at", "duration_ms", "duration_provenance",
        "execution_duration_ms", "execution_duration_provenance", "source", "usage",
    }
    safe: dict[str, Any] = {}
    for key in allowed:
        value = fields.get(key)
        if value is None:
            continue
        if key == "usage":
            safe[key] = sanitize_telemetry_usage(value)
        elif key == "task_ids" and isinstance(value, list):
            safe[key] = sorted({metadata for item in value if (metadata := safe_telemetry_metadata(item)) is not None})
        elif key in {"retry", "takeover"} and isinstance(value, bool):
            safe[key] = value
        elif key in {"attempt", "duration_ms", "execution_duration_ms"} and isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            safe[key] = value
        elif key in {"kind", "outcome", "duration_provenance", "execution_duration_provenance"} and isinstance(value, str) and value in (TELEMETRY_KINDS | TELEMETRY_OUTCOMES | TELEMETRY_PROVENANCE | {"measured", "unavailable"}):
            safe[key] = value
        elif key in {"assigned_at", "started_at", "completed_at"} and _parse_iso_timestamp(value) is not None:
            safe[key] = value
        elif isinstance(value, str):
            metadata = safe_telemetry_metadata(value)
            if metadata is not None:
                safe[key] = metadata
    return safe


def upsert_telemetry_event_locked(
    config: dict[str, Any], event_id: str, fields: dict[str, Any]
) -> dict[str, Any]:
    telemetry = load_telemetry(config)
    events = telemetry.setdefault("events", {})
    existing = events.get(event_id)
    event = dict(existing) if isinstance(existing, dict) else {}
    event.update(sanitize_telemetry_event_fields(fields))
    event["event_id"] = event_id
    event["schema_version"] = TELEMETRY_SCHEMA_VERSION
    events[event_id] = event
    save_telemetry(config, telemetry)
    return event


def _agent_telemetry_metadata(agent: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "agent_id": safe_telemetry_metadata(agent.get("id")) or "unknown",
        "role": safe_telemetry_metadata(agent.get("role")) or "worker",
    }
    for key in ("provider", "runtime", "model"):
        value = safe_telemetry_metadata(agent.get(key))
        if value is not None:
            metadata[key] = value
    return metadata


def record_assignment_telemetry_locked(
    config: dict[str, Any], item: dict[str, Any], agent: dict[str, Any]
) -> None:
    dispatched_at = item.get("dispatched_at") or now_iso()
    event_id = f"assignment:{item['id']}:{agent['id']}:{dispatched_at}"
    fields = {
        "kind": "task",
        "task_id": item["id"],
        "entity_id": item["id"],
        "outcome": "assigned",
        "assigned_at": dispatched_at,
        "attempt": int(item.get("attempts", 0)) + 1,
        "source": "relwit.dispatch",
        **_agent_telemetry_metadata(agent),
    }
    upsert_telemetry_event_locked(config, event_id, fields)


def record_task_telemetry_locked(
    config: dict[str, Any],
    item: dict[str, Any],
    agent: dict[str, Any],
    outcome: str,
    *,
    completed_at: str | None = None,
    execution_duration_ms: int | None = None,
    usage: dict[str, Any] | None = None,
    source: str = "relwit.lifecycle",
) -> None:
    attempt = max(1, int(item.get("attempts", 1)))
    event_id = telemetry_event_id("task", item["id"], attempt)
    fields: dict[str, Any] = {
        "kind": "task",
        "task_id": item["id"],
        "entity_id": item["id"],
        "outcome": outcome if outcome in TELEMETRY_OUTCOMES else "unknown",
        "attempt": attempt,
        "retry": attempt > 1,
        "takeover": bool(item.get("supersedes")),
        "supersedes": item.get("supersedes"),
        "started_at": item.get("started_at"),
        "execution_duration_ms": execution_duration_ms,
        "execution_duration_provenance": "measured" if execution_duration_ms is not None else "unavailable",
        "source": safe_telemetry_metadata(source) or "relwit.lifecycle",
        **_agent_telemetry_metadata(agent),
    }
    if completed_at is not None:
        fields["completed_at"] = completed_at
        fields["duration_ms"] = _duration_ms_from_timestamps(item.get("started_at"), completed_at)
        fields["duration_provenance"] = "measured" if item.get("started_at") else "unavailable"
    if execution_duration_ms is not None:
        fields["execution_duration_ms"] = execution_duration_ms
    if usage is not None:
        fields["usage"] = usage
    elif completed_at is not None:
        fields["usage"] = unavailable_usage("missing", "runtime")
    upsert_telemetry_event_locked(config, event_id, fields)


def record_cycle_telemetry_locked(
    config: dict[str, Any],
    cycle_id: str,
    started_at: str,
    completed_at: str,
    duration_ms: int,
) -> None:
    upsert_telemetry_event_locked(
        config,
        telemetry_event_id("cycle", cycle_id),
        {
            "kind": "cycle",
            "entity_id": cycle_id,
            "outcome": "completed",
            "agent_id": "supervisor",
            "role": "supervisor",
            "started_at": started_at,
            "completed_at": completed_at,
            "duration_ms": duration_ms,
            "duration_provenance": "measured",
            "usage": unavailable_usage("host runtime did not expose usage", "supervisor-runtime"),
            "source": "relwit.supervisor_cycle",
        },
    )


def aggregate_telemetry(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate event identities already de-duplicated by the event store."""

    task_events = [
        event
        for event in events
        if event.get("kind") == "task"
        and event.get("attempt") is not None
        and event.get("outcome") != "assigned"
    ]
    worker_runtime_values = [
        int(event["execution_duration_ms"])
        for event in task_events
        if isinstance(event.get("execution_duration_ms"), int) and event["execution_duration_ms"] >= 0
    ]
    worker_runtime = sum(
        worker_runtime_values
    )
    completed = sum(event.get("outcome") == "completed" for event in task_events)
    failed = sum(event.get("outcome") == "failed" for event in task_events)
    retries = sum(bool(event.get("retry")) for event in task_events)
    takeovers = sum(bool(event.get("takeover")) for event in task_events)
    participants: dict[str, dict[str, Any]] = {}
    for event in events:
        agent_id = event.get("agent_id")
        if not isinstance(agent_id, str) or not agent_id:
            continue
        participant = participants.setdefault(
            agent_id,
            {
                "agent_id": agent_id,
                "role": event.get("role") or "unknown",
                "tasks": set(),
                "events": 0,
            },
        )
        participant["events"] += 1
        task_id = event.get("task_id")
        if isinstance(task_id, str):
            participant["tasks"].add(task_id)
        task_ids = event.get("task_ids")
        if isinstance(task_ids, list):
            participant["tasks"].update(value for value in task_ids if isinstance(value, str))
        for key in ("provider", "runtime", "model"):
            if key in event and key not in participant:
                participant[key] = event[key]
    task_usage_available = any(
        isinstance(event.get("usage"), dict) and event["usage"].get("status") == "available"
        for event in task_events
    )
    usage_events: list[tuple[str, dict[str, Any]]] = []
    for event in events:
        usage = event.get("usage")
        if not isinstance(usage, dict):
            continue
        if event.get("kind") in {"phase", "project"} and task_usage_available:
            continue
        if event.get("kind") == "cycle" and task_usage_available and event.get("agent_id") != "supervisor":
            continue
        usage_events.append((str(event.get("event_id", "usage")), usage))
    unique_usage_events: list[dict[str, Any]] = []
    seen_usage_ids: set[str] = set()
    for event_id, usage in usage_events:
        usage_id = usage.get("usage_id") if isinstance(usage.get("usage_id"), str) else event_id
        if usage_id in seen_usage_ids:
            continue
        seen_usage_ids.add(usage_id)
        unique_usage_events.append(usage)
    known_total = 0
    known_events = 0
    authoritative_total = 0
    authoritative_events = 0
    unavailable_events = 0
    partial_events = 0
    for usage in unique_usage_events:
        provenance = usage.get("provenance")
        total = usage.get("total_tokens")
        if isinstance(total, int) and total >= 0:
            known_total += total
            known_events += 1
            if provenance == "authoritative":
                authoritative_total += total
                authoritative_events += 1
        elif any(isinstance(usage.get(field), int) for field in ("input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens")):
            partial_events += 1
            if isinstance(usage.get("input_tokens"), int) and isinstance(usage.get("output_tokens"), int):
                known_total += usage["input_tokens"] + usage["output_tokens"]
                known_events += 1
        else:
            unavailable_events += 1
    if not unique_usage_events:
        token_status = "unavailable"
    elif known_total == 0 and unavailable_events == len(unique_usage_events):
        token_status = "unavailable"
    elif unavailable_events or partial_events or known_events != len(unique_usage_events):
        token_status = "partial"
    else:
        token_status = "total"
    starts = [
        parsed for event in events if (parsed := _parse_iso_timestamp(event.get("started_at"))) is not None
    ]
    completions = [
        parsed for event in events if (parsed := _parse_iso_timestamp(event.get("completed_at"))) is not None
    ]
    wall_time_ms = None
    if starts and completions:
        wall_time_ms = max(0, round((max(completions) - min(starts)).total_seconds() * 1000))
        measured_cycle_durations = [
            event["duration_ms"]
            for event in events
            if event.get("kind") == "cycle"
            and isinstance(event.get("duration_ms"), int)
            and event["duration_ms"] >= 0
        ]
        if wall_time_ms == 0 and measured_cycle_durations:
            wall_time_ms = max(measured_cycle_durations)
    else:
        durations = [
            event["duration_ms"]
            for event in events
            if isinstance(event.get("duration_ms"), int) and event["duration_ms"] >= 0
        ]
        if len(durations) == 1:
            wall_time_ms = durations[0]
    return {
        "wall_time_ms": wall_time_ms,
        "worker_runtime_ms": worker_runtime if worker_runtime_values else None,
        "wall_time_provenance": "measured" if wall_time_ms is not None else "unavailable",
        "worker_runtime_provenance": "measured" if worker_runtime_values else "unavailable",
        "tasks_completed": completed,
        "attempts": len(task_events),
        "failed_attempts": failed,
        "retries": retries,
        "takeovers": takeovers,
        "participants": participants,
        "tokens": known_total if known_events else None,
        "token_status": token_status,
        "token_provenance": "authoritative" if authoritative_events == known_events and known_events else "mixed" if authoritative_events else "unavailable",
        "authoritative_tokens": authoritative_total if authoritative_events else None,
        "unavailable_usage_events": unavailable_events,
    }


def load_telemetry_summary(config: dict[str, Any]) -> dict[str, Any]:
    telemetry = load_telemetry(config)
    events = [event for event in telemetry.get("events", {}).values() if isinstance(event, dict)]
    return aggregate_telemetry(events)


def format_duration_ms(value: Any) -> str:
    if not isinstance(value, int) or value < 0:
        return "unavailable"
    seconds = value // 1000
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {seconds:02d}s"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def render_usage_section(summary: dict[str, Any]) -> list[str]:
    participants = summary.get("participants", {})
    participant_names = []
    if isinstance(participants, dict):
        for agent_id in sorted(participants):
            tasks = participants[agent_id].get("tasks", set())
            participant_names.append(f"{agent_id} ({len(tasks)} task{'s' if len(tasks) != 1 else ''})")
    token_status = summary.get("token_status", "unavailable")
    if token_status == "total":
        token_line = f"total — {summary['tokens']} ({summary.get('token_provenance', 'unknown')})"
    elif token_status == "partial":
        token_line = f"partial — {summary.get('tokens') or 0} known ({summary.get('token_provenance', 'mixed')})"
    else:
        token_line = "unavailable (runtime did not expose authoritative usage)"
    wall_provenance = summary.get("wall_time_provenance", "unavailable")
    worker_provenance = summary.get("worker_runtime_provenance", "unavailable")
    return [
        "## Usage",
        "",
        f"- wall_time: `{format_duration_ms(summary.get('wall_time_ms'))}` ({wall_provenance})",
        f"- aggregate_worker_runtime: `{format_duration_ms(summary.get('worker_runtime_ms'))}` ({worker_provenance})",
        f"- tokens: `{token_line}`",
        f"- participants: `{', '.join(participant_names) or 'none recorded'}`",
        f"- tasks_completed: `{summary.get('tasks_completed', 0)}`; attempts: `{summary.get('attempts', 0)}`; failed_attempts: `{summary.get('failed_attempts', 0)}`",
        f"- retries: `{summary.get('retries', 0)}`; takeovers: `{summary.get('takeovers', 0)}`",
        "- privacy: prompts, responses, credentials and raw provider logs are not stored in telemetry",
        "",
    ]


def release_source_settings(config: dict[str, Any]) -> tuple[int, list[str]]:
    settings = config.get("release_source", {})
    if not isinstance(settings, dict):
        raise RelWitError("config.release_source must be an object")
    version = settings.get("version", DEFAULT_CONFIG["release_source"]["version"])
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise RelWitError("config.release_source.version must be a positive integer")
    raw_paths = settings.get("volatile_paths", DEFAULT_CONFIG["release_source"]["volatile_paths"])
    if not isinstance(raw_paths, list) or any(not isinstance(value, str) or not value.strip() for value in raw_paths):
        raise RelWitError("config.release_source.volatile_paths must be an array of non-empty strings")
    paths: list[str] = []
    for value in raw_paths:
        normalized = validate_relative_scope(value, "config.release_source.volatile_paths")
        if normalized == ".":
            raise RelWitError("config.release_source.volatile_paths must not contain the project root")
        paths.append(normalized)
    configured_paths = config.get("paths")
    if not isinstance(configured_paths, dict):
        configured_paths = {}
    telemetry_relative = normalize_scope(
        str(configured_paths.get("telemetry", DEFAULT_CONFIG["paths"]["telemetry"]))
    )
    if telemetry_relative not in paths:
        paths.append(telemetry_relative)
    return version, paths


def release_path_is_volatile(path: str, volatile_paths: list[str]) -> bool:
    normalized = path.replace("\\", "/").strip("/").casefold()
    return any(
        normalized == value.casefold() or normalized.startswith(f"{value.casefold().rstrip('/')}/")
        for value in volatile_paths
    )


def _git_nul_paths(arguments: list[str]) -> list[str]:
    result = subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RelWitError("git could not enumerate release source paths")
    return [value.decode("utf-8", errors="replace") for value in result.stdout.split(b"\0") if value]


def _filesystem_source_paths(volatile_paths: list[str]) -> list[str]:
    paths: list[str] = []
    for directory, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [
            name
            for name in dirnames
            if name not in {".git", "__pycache__", ".pytest_cache", "node_modules", "dist", "build"}
        ]
        for name in filenames:
            path = Path(directory) / name
            relative = path.relative_to(ROOT).as_posix()
            if path.suffix == ".pyc" or release_path_is_volatile(relative, volatile_paths):
                continue
            paths.append(relative)
    return sorted(set(paths))


def _sha256_file(path: Path) -> tuple[str, int, str]:
    try:
        resolved = path.resolve()
        resolved.relative_to(ROOT.resolve())
        content = resolved.read_bytes()
    except (OSError, ValueError) as exc:
        if not path.exists():
            return "<missing>", 0, "missing"
        raise RelWitError(f"cannot fingerprint release source file: {rel(path)}") from exc
    return hashlib.sha256(content).hexdigest(), len(content), "present"


def _qa_release_config_fingerprint(config: dict[str, Any]) -> str:
    supervisor = config.get("supervisor", {})
    if not isinstance(supervisor, dict):
        supervisor = {}
    payload = {
        "config_version": config.get("version"),
        "release_source": config.get("release_source"),
        "qa_commands": supervisor.get("qa_commands"),
        "qa_timeout_seconds": supervisor.get("qa_timeout_seconds"),
        "run_qa_each_cycle": supervisor.get("run_qa_each_cycle"),
        "operational_readiness_files": supervisor.get("operational_readiness_files"),
        "production_gates": supervisor.get("production_gates"),
    }
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


SOURCE_FINGERPRINT_ALGORITHM = "content-v2"


def release_source_fingerprint(config: dict[str, Any]) -> dict[str, Any]:
    release_version, volatile_paths = release_source_settings(config)
    try:
        probe = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        probe = None
    git_available = probe is not None and probe.returncode == 0 and bool(probe.stdout.strip())
    head_sha = "unavailable"
    dirty_paths: list[str] = []
    untracked_paths: list[str] = []
    if git_available:
        try:
            git_root = Path(probe.stdout.strip()).resolve()
            if git_root != ROOT.resolve():
                raise RelWitError("git repository root does not match the configured project root")
            head_result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            if head_result.returncode != 0 or not head_result.stdout.strip():
                raise RelWitError("git HEAD is unavailable for release fingerprint")
            head_sha = head_result.stdout.strip()
            tracked_paths = _git_nul_paths(["ls-files", "--cached", "-z"])
            untracked_paths = _git_nul_paths(["ls-files", "--others", "--exclude-standard", "-z"])
            dirty_candidates: set[str] = set()
            for arguments in (
                ["diff", "--name-only", "-z"],
                ["diff", "--cached", "--name-only", "-z"],
                ["ls-files", "--others", "--exclude-standard", "-z"],
            ):
                dirty_candidates.update(_git_nul_paths(arguments))
            dirty_paths = sorted(
                path for path in dirty_candidates if not release_path_is_volatile(path, volatile_paths)
            )
            source_paths = sorted(
                {
                    path
                    for path in [*tracked_paths, *untracked_paths]
                    if not release_path_is_volatile(path, volatile_paths)
                }
            )
            tracked_count = sum(not release_path_is_volatile(path, volatile_paths) for path in tracked_paths)
            untracked_count = sum(not release_path_is_volatile(path, volatile_paths) for path in untracked_paths)
        except RelWitError:
            raise
    else:
        source_paths = _filesystem_source_paths(volatile_paths)
        tracked_count = None
        untracked_count = None

    manifest: list[dict[str, Any]] = []
    for relative in source_paths:
        path = ROOT / Path(relative)
        if path.is_symlink():
            # A link's identity is its target, not the bytes it points at.
            manifest.append({"path": relative, "kind": "symlink", "target": os.readlink(path)})
            continue
        digest, byte_count, file_state = _sha256_file(path)
        if file_state == "missing":
            # A deleted file is absent from the source, whether or not the
            # deletion has been staged or committed yet.
            continue
        kind = "exec" if path.stat().st_mode & 0o111 else "file"
        manifest.append({"path": relative, "sha256": digest, "bytes": byte_count, "kind": kind})
    dirty_state = "dirty" if dirty_paths else "clean"
    if not git_available:
        dirty_state = "unknown"
    qa_config_fingerprint = _qa_release_config_fingerprint(config)
    # The fingerprint identifies what QA verified: release-source bytes plus the
    # QA contract. HEAD and dirty state are recorded as metadata but are not
    # hashed, so committing only volatile evidence or rewording a commit does
    # not invalidate QA for identical source. Durability still requires a clean
    # committed tree separately.
    payload = {
        "algorithm": SOURCE_FINGERPRINT_ALGORITHM,
        "version": release_version,
        "vcs": "git" if git_available else "filesystem",
        "manifest": manifest,
        "qa_config_fingerprint": qa_config_fingerprint,
    }
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return {
        "fingerprint": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        "algorithm": SOURCE_FINGERPRINT_ALGORITHM,
        "version": release_version,
        "vcs": payload["vcs"],
        "head_sha": head_sha,
        "dirty": dirty_state == "dirty",
        "dirty_state": dirty_state,
        "dirty_path_count": len(dirty_paths),
        "source_file_count": len(manifest),
        "tracked_path_count": tracked_count,
        "untracked_path_count": untracked_count,
        "qa_config_version": config.get("version"),
        "qa_config_fingerprint": qa_config_fingerprint,
        "volatile_paths": volatile_paths,
    }


def _git_single_line(arguments: list[str]) -> str | None:
    """Return one safe local Git metadata value, or None when unavailable."""

    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    if not value or "\n" in value or "\r" in value:
        return None
    return value


def git_upstream_snapshot() -> dict[str, Any]:
    """Read branch/upstream metadata without contacting or mutating a remote."""

    branch = _git_single_line(["symbolic-ref", "--quiet", "--short", "HEAD"])
    if branch is None:
        return {
            "branch": None,
            "branch_state": "detached",
            "upstream": None,
            "upstream_state": "not_applicable",
            "upstream_relation": "not_applicable",
            "ahead": None,
            "behind": None,
        }
    upstream = _git_single_line(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"])
    if upstream is None:
        tracking_remote = _git_single_line(["config", "--get", f"branch.{branch}.remote"])
        tracking_merge = _git_single_line(["config", "--get", f"branch.{branch}.merge"])
        if tracking_remote and tracking_merge:
            return {
                "branch": branch,
                "branch_state": "attached",
                "upstream": None,
                "upstream_state": "unknown",
                "upstream_relation": "unknown",
                "ahead": None,
                "behind": None,
            }
        return {
            "branch": branch,
            "branch_state": "attached",
            "upstream": None,
            "upstream_state": "none",
            "upstream_relation": "none",
            "ahead": None,
            "behind": None,
        }
    counts = _git_single_line(["rev-list", "--left-right", "--count", "HEAD...@{upstream}"])
    if counts is None:
        return {
            "branch": branch,
            "branch_state": "attached",
            "upstream": upstream,
            "upstream_state": "unknown",
            "upstream_relation": "unknown",
            "ahead": None,
            "behind": None,
        }
    parts = counts.split()
    if len(parts) != 2 or any(not part.isdigit() for part in parts):
        return {
            "branch": branch,
            "branch_state": "attached",
            "upstream": upstream,
            "upstream_state": "unknown",
            "upstream_relation": "unknown",
            "ahead": None,
            "behind": None,
        }
    ahead, behind = (int(part) for part in parts)
    if ahead == 0 and behind == 0:
        relation = "up_to_date"
    elif ahead > 0 and behind == 0:
        relation = "ahead"
    elif ahead == 0 and behind > 0:
        relation = "behind"
    else:
        relation = "diverged"
    return {
        "branch": branch,
        "branch_state": "attached",
        "upstream": upstream,
        "upstream_state": "known",
        "upstream_relation": relation,
        "ahead": ahead,
        "behind": behind,
    }


def release_durability_snapshot(
    config: dict[str, Any], state: dict[str, Any], source: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Evaluate strong local release durability independently from task completion."""

    source = source if source is not None else release_source_fingerprint(config)
    last_qa = state.get("last_qa") if isinstance(state, dict) else None
    last_qa = last_qa if isinstance(last_qa, dict) else {}
    qa_source = validate_qa_source(config, state, source)
    if source["vcs"] == "git":
        upstream = git_upstream_snapshot()
    else:
        upstream = {
            "branch": None,
            "branch_state": "not_applicable",
            "upstream": None,
            "upstream_state": "not_applicable",
            "upstream_relation": "not_applicable",
            "ahead": None,
            "behind": None,
        }

    status = "pass"
    reason = "release source is committed, clean and QA-bound"
    if source["vcs"] != "git":
        status = "manual"
        reason = "strong Git durability is unavailable without a Git repository"
    elif source["head_sha"] == "unavailable":
        status = "fail"
        reason = "Git HEAD is unavailable"
    elif source["dirty_state"] != "clean":
        status = "fail"
        reason = "release-source working tree contains nonvolatile changes"
    elif source["untracked_path_count"] != 0:
        status = "fail"
        reason = "non-ignored untracked release-source files are present"
    elif qa_source["status"] != "valid":
        status = "fail"
        reason = f"QA source state is {qa_source['status']}"

    return {
        **source,
        **upstream,
        "status": status,
        "reason": reason,
        "qa_source_state": qa_source["status"],
        "qa_source_reason": qa_source.get("reason"),
        "qa_source_fingerprint": last_qa.get("source_fingerprint"),
        "qa_recorded_head_sha": last_qa.get("source_head_sha"),
    }


LOCAL_ONLY_IGNORE = "# Local-only RelWit runtime data: never commit.\n*\n"


def ensure_local_only_dir(config: dict[str, Any], path: Path) -> None:
    """Create a local-only directory that Git ignores.

    `init` in an external project writes no root ignore rule, so raw diagnostics
    would otherwise be picked up by `git add -A` (audit/redundancy-2026-09-30, 6.6).
    The ignore file is only written inside a declared volatile path and never at
    the project root, so a misconfigured path cannot hide release source.
    """

    path.mkdir(parents=True, exist_ok=True)
    try:
        relative = path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return
    settings = config.get("release_source")
    declared = settings.get("volatile_paths") if isinstance(settings, dict) else None
    if declared is None:
        declared = DEFAULT_CONFIG["release_source"]["volatile_paths"]
    declared = [normalize_scope(str(value)) for value in declared] if isinstance(declared, list) else []
    if relative in {"", "."} or not release_path_is_volatile(relative, declared):
        return
    ignore = path / ".gitignore"
    if not ignore.exists():
        atomic_write(ignore, LOCAL_ONLY_IGNORE)


def ensure_text_file(path: Path, content: str) -> None:
    """Create a required empty scaffold file without overwriting runtime data."""

    if not path.exists():
        atomic_write(path, content)


def ensure_layout() -> None:
    for directory in (
        ROOT / ".agents" / "skills",
        ROOT / ".codex" / "agents",
        ROOT / "knowledge",
        ROOT / "work" / "items",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    config = load_config()
    for key in ("agent_root", "reports_inbox", "reports_archive", "outbox", "checkpoints", "evidence"):
        path_for(config, key).mkdir(parents=True, exist_ok=True)
    for key in ("runtime_spool", "telemetry"):
        ensure_local_only_dir(config, path_for(config, key))
    for key in ("completed_tasks", "reports_index", "supervisor_report", "supervisor_cycle", "supervisor_state"):
        path_for(config, key).parent.mkdir(parents=True, exist_ok=True)
    if not REGISTRY.exists():
        atomic_write(REGISTRY, json.dumps({"version": 1, "updated_at": None, "items": {}}, indent=2) + "\n")
    if not CONFIG.exists():
        save_config(config)

    ensure_text_file(
        ROOT / "work" / "INDEX.md",
        (
            "# Work ledger\n\n"
            "`work/registry.json` is the machine-readable task ledger. Generated\n"
            "state in this directory is local to the project and starts empty after\n"
            "`init`; maintainer task history is never inherited from the RelWit\n"
            "source checkout.\n\n"
            "Use `context`, `task`, `worker` and `supervisor` commands to opt into\n"
            "the coordination workflow. Evidence and checkpoints are runtime\n"
            "artifacts; review and release gates remain explicit.\n"
        ),
    )
    ensure_text_file(path_for(config, "completed_tasks"), "# Completed tasks\n\n")
    ensure_text_file(path_for(config, "reports_index"), "# Reports\n\n")
    ensure_text_file(
        path_for(config, "supervisor_report"),
        "# Supervisor report\n\nNo supervisor cycle has run.\n",
    )
    ensure_text_file(
        path_for(config, "supervisor_cycle"),
        "# Latest supervisor cycle\n\nNo supervisor cycle has run.\n",
    )
    ensure_text_file(
        path_for(config, "supervisor_state"),
        json.dumps({"version": 1, "cycle": 0, "ingested_reports": [], "last_qa": None}, indent=2) + "\n",
    )

    configured_agents = config.get("agents", [])
    if isinstance(configured_agents, list):
        for agent in configured_agents:
            if not isinstance(agent, dict):
                continue
            try:
                paths = agent_paths(config, agent)
            except (TypeError, ValueError, RelWitError):
                continue
            paths["directory"].mkdir(parents=True, exist_ok=True)
            paths["inbox_dir"].mkdir(parents=True, exist_ok=True)
            agent_id = str(agent.get("id", "agent"))
            ensure_text_file(paths["inbox"], f"# INBOX - {agent_id}\n\n")
            ensure_text_file(paths["report"], f"# REPORTS - {agent_id}\n\n")
            ensure_text_file(paths["completed"], f"# COMPLETED - {agent_id}\n\n")


def load_registry() -> dict[str, Any]:
    if not REGISTRY.exists():
        raise RelWitError(f"missing {rel(REGISTRY)}; run init first")
    try:
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RelWitError(f"invalid JSON in {rel(REGISTRY)}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("items"), dict):
        raise RelWitError(f"{rel(REGISTRY)} must contain an object named items")
    return data


def save_registry(data: dict[str, Any]) -> None:
    data["updated_at"] = now_iso()
    atomic_write(REGISTRY, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def registry_revision(data: dict[str, Any]) -> str:
    """Return a deterministic revision for the registry snapshot in memory."""

    payload = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def item_path(task_id: str) -> Path:
    return ROOT / "work" / "items" / f"{task_id}.md"


def normalize_scope(value: str) -> str:
    value = value.replace("\\", "/").strip()
    value = re.sub(r"/+", "/", value)
    value = re.sub(r"/+$", "", value)
    return value or "."


def validate_relative_scope(value: str | Path, label: str = "scope") -> str:
    """Normalize a repository-relative scope and reject traversal/absolute paths."""

    normalized = normalize_scope(str(value))
    if normalized.startswith("/") or re.fullmatch(r"[A-Za-z]:/.*", normalized):
        raise RelWitError(f"{label} must be repository-relative: {value}")
    if ".." in normalized.split("/"):
        raise RelWitError(f"{label} must not contain parent traversal: {value}")
    return normalized


def scope_parts(value: str) -> list[str]:
    return [os.path.normcase(part) for part in normalize_scope(value).split("/") if part not in ("", ".")]


def scope_overlaps(left: str, right: str) -> bool:
    left_parts = scope_parts(left)
    right_parts = scope_parts(right)
    if not left_parts or not right_parts:
        return True
    return left_parts[: len(right_parts)] == right_parts or right_parts[: len(left_parts)] == left_parts


def scope_within(scope: str, allowed: str) -> bool:
    allowed_parts = scope_parts(allowed)
    candidate_parts = scope_parts(scope)
    return not allowed_parts or candidate_parts[: len(allowed_parts)] == allowed_parts


def active_scope_conflict(data: dict[str, Any], candidate: dict[str, Any], ignore_id: str | None = None) -> dict[str, Any] | None:
    for other_id, other in data["items"].items():
        if not isinstance(other, dict):
            continue
        if other_id == ignore_id or other.get("status") not in ACTIVE_WRITER_STATUSES:
            continue
        candidate_scopes = candidate.get("scope", [])
        other_scopes = other.get("scope", [])
        if not isinstance(candidate_scopes, list) or not isinstance(other_scopes, list):
            continue
        if any(
            scope_overlaps(left, right)
            for left in candidate_scopes
            for right in other_scopes
            if isinstance(left, str) and isinstance(right, str)
        ):
            return other
    return None


def next_id(items: dict[str, Any]) -> str:
    numbers = []
    for key in items:
        match = re.fullmatch(r"RW-(\d{4,})", key)
        if match:
            numbers.append(int(match.group(1)))
    return f"RW-{max(numbers, default=0) + 1:04d}"


def get_item(data: dict[str, Any], task_id: str) -> dict[str, Any]:
    item = data["items"].get(task_id)
    if not isinstance(item, dict):
        raise RelWitError(f"unknown task: {task_id}")
    return item


def dependencies_done(data: dict[str, Any], item: dict[str, Any]) -> bool:
    dependencies = item.get("depends_on", [])
    if not isinstance(dependencies, list):
        return False
    for dependency in dependencies:
        dependency_item = data["items"].get(dependency)
        if not isinstance(dependency_item, dict) or dependency_item.get("status") != "done":
            return False
    return True


def agent_config(config: dict[str, Any], agent_id: str) -> dict[str, Any]:
    agents = config.get("agents", [])
    if not isinstance(agents, list):
        raise RelWitError("config.agents must be an array")
    for agent in agents:
        if isinstance(agent, dict) and agent.get("id") == agent_id:
            return agent
    raise RelWitError(f"unknown registered agent: {agent_id}")


def claim_agent(config: dict[str, Any], agent_id: str | None) -> dict[str, Any]:
    if not agent_id:
        raise RelWitError("claim action requires --agent <supervisor|explorer|planner|worker>")
    agent = agent_config(config, agent_id)
    if agent.get("role") not in CLAIM_ROLES:
        raise RelWitError(
            f"agent {agent_id} is not authorized to claim work; role must be supervisor, explorer, planner or worker"
        )
    return agent


def review_agent(config: dict[str, Any], agent_id: str | None) -> dict[str, Any]:
    if not agent_id:
        raise RelWitError("review action requires --agent <supervisor|reviewer|release_gate>")
    agent = agent_config(config, agent_id)
    if agent.get("role") not in REVIEW_ROLES:
        raise RelWitError(
            f"agent {agent_id} is not authorized for review actions; role must be supervisor, reviewer or release_gate"
        )
    return agent


def agent_paths(config: dict[str, Any], agent: dict[str, Any]) -> dict[str, Path]:
    directory = agent.get("directory")
    if directory is None:
        agent_id = agent.get("id")
        if not isinstance(agent_id, str) or not agent_id:
            raise RelWitError("agent needs an id before mailbox paths can be resolved")
        paths_config = config.get("paths")
        if not isinstance(paths_config, dict):
            raise RelWitError("config.paths must be an object before mailbox paths can be resolved")
        agent_root = paths_config.get("agent_root")
        if not isinstance(agent_root, (str, Path)) or not str(agent_root).strip():
            raise RelWitError("config.paths.agent_root must be a non-empty path before mailbox paths can be resolved")
        directory = str(Path(agent_root) / agent_id)
    base = safe_repo_path(directory)
    return {
        "directory": base,
        "inbox": safe_repo_path(agent.get("inbox", base / "INBOX.md")),
        "report": safe_repo_path(agent.get("report", base / "REPORT.md")),
        "completed": safe_repo_path(agent.get("completed", base / "COMPLETED.md")),
        "inbox_dir": safe_repo_path(agent.get("inbox_dir", base / "inbox")),
    }


def runner_settings(agent: dict[str, Any]) -> tuple[list[str], int] | None:
    """Return a validated, argv-only runner definition for a worker."""

    raw_runner = agent.get("runner")
    if raw_runner is None:
        return None
    if not isinstance(raw_runner, dict):
        raise RelWitError(f"runner must be an object for agent {agent.get('id')}")
    command = raw_runner.get("command")
    if not isinstance(command, list) or not command or any(
        not isinstance(argument, str) or not argument.strip() for argument in command
    ):
        raise RelWitError(
            f"runner.command must be a non-empty array of strings for agent {agent.get('id')}"
        )
    if not any("{assignment_path}" in argument for argument in command):
        raise RelWitError(
            f"runner.command for agent {agent.get('id')} must include {{assignment_path}}"
        )
    timeout = raw_runner.get("timeout_seconds", DEFAULT_RUNNER_TIMEOUT_SECONDS)
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= MAX_RUNNER_TIMEOUT_SECONDS:
        raise RelWitError(
            f"runner.timeout_seconds for agent {agent.get('id')} must be between 1 and {MAX_RUNNER_TIMEOUT_SECONDS}"
        )
    runner_preflight_settings(agent)
    return command, timeout


def runner_preflight_settings(agent: dict[str, Any]) -> tuple[list[str], int] | None:
    """Return an optional bounded argv-only readiness probe definition."""

    raw_runner = agent.get("runner")
    if raw_runner is None:
        return None
    if not isinstance(raw_runner, dict):
        raise RelWitError(f"runner must be an object for agent {agent.get('id')}")
    raw_preflight = raw_runner.get("preflight")
    if raw_preflight is None:
        return None
    if not isinstance(raw_preflight, dict):
        raise RelWitError(f"runner.preflight must be an object for agent {agent.get('id')}")
    unknown = [key for key in raw_preflight if key not in {"command", "timeout_seconds"}]
    if unknown:
        raise RelWitError(
            f"runner.preflight has unsupported fields for agent {agent.get('id')}: {', '.join(map(str, unknown))}"
        )
    command = raw_preflight.get("command")
    if not isinstance(command, list) or not command or any(
        not isinstance(argument, str) or not argument.strip() or "\x00" in argument for argument in command
    ):
        raise RelWitError(
            f"runner.preflight.command must be a non-empty array of strings without NUL for agent {agent.get('id')}"
        )
    timeout = raw_preflight.get("timeout_seconds", DEFAULT_PREFLIGHT_TIMEOUT_SECONDS)
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= MAX_PREFLIGHT_TIMEOUT_SECONDS:
        raise RelWitError(
            f"runner.preflight.timeout_seconds for agent {agent.get('id')} must be between 1 and {MAX_PREFLIGHT_TIMEOUT_SECONDS}"
        )
    return list(command), timeout


def render_command(
    command: list[str],
    agent: dict[str, Any],
    item: dict[str, Any],
    assignment_path: Path,
) -> list[str]:
    values = {
        "{assignment_path}": rel(assignment_path),
        "{task_id}": str(item["id"]),
        "{agent_id}": str(agent["id"]),
    }
    return [
        argument.replace("{assignment_path}", values["{assignment_path}"])
        .replace("{task_id}", values["{task_id}"])
        .replace("{agent_id}", values["{agent_id}"])
        for argument in command
    ]


def render_runner_command(agent: dict[str, Any], item: dict[str, Any], assignment_path: Path) -> tuple[list[str], int]:
    settings = runner_settings(agent)
    if settings is None:
        raise RelWitError(
            f"agent {agent.get('id')} has no configured runner; use worker pull for a manual runtime"
        )
    command, timeout = settings
    return render_command(command, agent, item, assignment_path), timeout


def render_preflight_command(
    agent: dict[str, Any], item: dict[str, Any], assignment_path: Path
) -> tuple[list[str], int] | None:
    settings = runner_preflight_settings(agent)
    if settings is None:
        return None
    command, timeout = settings
    return render_command(command, agent, item, assignment_path), timeout


def runner_executable_available(command: list[str]) -> bool:
    """Check only the executable named by argv; never execute a readiness probe here."""

    executable = command[0]
    candidate = Path(executable)
    if candidate.is_absolute():
        return candidate.is_file()
    if candidate.parent != Path("."):
        return (ROOT / candidate).is_file()
    return shutil.which(executable) is not None


def static_runner_readiness(agent: dict[str, Any]) -> dict[str, Any]:
    """Return dispatch-safe readiness without invoking external runtime code."""

    if agent.get("runner") is None:
        return {
            "state": "unknown",
            "reason": "runner_not_configured_manual_runtime",
            "dispatchable": True,
            "execution": "manual",
        }
    try:
        settings = runner_settings(agent)
    except RelWitError:
        return {
            "state": "misconfigured",
            "failure_class": "misconfigured",
            "disposition": "needs_input",
            "reason": "runner_configuration_invalid",
            "dispatchable": False,
            "execution": "runner",
        }
    if settings is None:
        return {
            "state": "no_target",
            "reason": "runner_target_missing",
            "dispatchable": False,
            "execution": "runner",
        }
    command, _ = settings
    if not runner_executable_available(command):
        return {
            "state": "unavailable",
            "failure_class": "unavailable",
            "disposition": "reassign",
            "reason": "runner_executable_unavailable",
            "dispatchable": False,
            "execution": "runner",
        }
    preflight = runner_preflight_settings(agent)
    if preflight is None:
        return {
            "state": "unknown",
            "reason": "preflight_not_configured_legacy_compatibility",
            "dispatchable": True,
            "execution": "runner",
        }
    preflight_command, _ = preflight
    if not runner_executable_available(preflight_command):
        return {
            "state": "unavailable",
            "failure_class": "unavailable",
            "disposition": "reassign",
            "reason": "preflight_executable_unavailable",
            "dispatchable": False,
            "execution": "runner",
        }
    return {
        "state": "unknown",
        "reason": "preflight_not_run",
        "dispatchable": True,
        "execution": "runner",
    }


def append_markdown(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = content.rstrip("\n")
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if existing.strip():
        content = existing.rstrip("\n") + "\n\n" + content
    atomic_write(path, content + "\n")


def write_if_missing(path: Path, content: str) -> None:
    if not path.exists():
        atomic_write(path, content)


def sync_item_frontmatter(item: dict[str, Any]) -> None:
    """Refresh machine-owned header without overwriting agent notes."""

    path = item_path(item["id"])
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    match = re.match(r"\A---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not match:
        return
    values = {
        "id": item["id"],
        "title": json.dumps(item["title"], ensure_ascii=False),
        "level": item["level"],
        "status": item["status"],
        "owner": item["owner"],
        "assigned_to": item.get("assigned_to") or "null",
        "scope": json.dumps(item.get("scope", []), ensure_ascii=False),
        "depends_on": json.dumps(item.get("depends_on", []), ensure_ascii=False),
    }
    for key in ("supersedes", "superseded_by", "takeover_reason"):
        if key in item:
            values[key] = json.dumps(item.get(key), ensure_ascii=False)
    lines = []
    seen: set[str] = set()
    for line in match.group(1).splitlines():
        if ":" not in line:
            lines.append(line)
            continue
        key, _ = line.split(":", 1)
        key = key.strip()
        if key in values:
            lines.append(f"{key}: {values[key]}")
            seen.add(key)
        else:
            lines.append(line)
    for key, value in values.items():
        if key not in seen:
            lines.append(f"{key}: {value}")
    atomic_write(path, "---\n" + "\n".join(lines) + "\n---\n" + text[match.end() :])


def append_event(task_id: str, message: str) -> None:
    path = item_path(task_id)
    if path.exists():
        append_markdown(path, f"- {now_iso()} - {message}\n")


def render_item(item: dict[str, Any]) -> str:
    lines = [
        "---",
        f"id: {item['id']}",
        f"title: {json.dumps(item['title'], ensure_ascii=False)}",
        f"level: {item['level']}",
        f"status: {item['status']}",
        f"owner: {item['owner']}",
        f"assigned_to: {item.get('assigned_to') or 'null'}",
        f"scope: {json.dumps(item.get('scope', []), ensure_ascii=False)}",
        f"depends_on: {json.dumps(item.get('depends_on', []), ensure_ascii=False)}",
    ]
    for key in ("supersedes", "superseded_by", "takeover_reason"):
        if key in item:
            lines.append(f"{key}: {json.dumps(item.get(key), ensure_ascii=False)}")
    lines.extend([
        "---",
        "",
        f"# {item['title']}",
        "",
        "## Objective",
        "",
        item.get("objective", item["title"]),
        "",
        "## Acceptance criteria",
        "",
    ])
    lines.extend(f"- [ ] {criterion}" for criterion in item.get("acceptance", []))
    lines.extend(["", "## Context to read", "", "- `knowledge/INDEX.md`", "", "## Plan", "", "## Files and evidence", "", "## Blockers", "", "## Handover", "", "## Event log", f"- {item['created_at']} - created by {item['owner']}", ""])
    return "\n".join(lines)


def render_assignment(item: dict[str, Any], agent: dict[str, Any], assignment_path: str) -> str:
    checks = item.get("verification") or ["Run focused tests relevant to the scope"]
    lines = [
        "---",
        "type: relwit-assignment",
        f"task_id: {item['id']}",
        f"agent: {agent['id']}",
        f"created_at: {now_iso()}",
        f"scope: {json.dumps(item.get('scope', []), ensure_ascii=False)}",
        "---",
        "",
        f"# Assignment {item['id']}: {item['title']}",
        "",
        "You are the assigned worker. Use `$relwit-worker` and do not modify files outside the scope below.",
        "",
        "## Objective",
        "",
        item.get("objective", item["title"]),
        "",
        "## Scope",
        "",
    ]
    lines.extend(f"- `{scope}`" for scope in item.get("scope", []))
    lines.extend(["", "## Dependencies", "", ", ".join(item.get("depends_on", [])) or "- none", "", "## Acceptance", ""])
    lines.extend(f"- [ ] {criterion}" for criterion in item.get("acceptance", []))
    lines.extend(["", "## Verification", ""])
    lines.extend(f"- `{check}`" for check in checks)
    lines.extend(
        [
            "",
            "## Read first",
            "",
            "- `AGENTS.md`",
            "- `knowledge/INDEX.md`",
            "- `work/items/" + item["id"] + ".md`",
            "",
            "## Required report",
            "",
            f"Run `relwit task report {item['id']} --agent {agent['id']} --result completed --summary \"...\" --next-action \"Review\"`.",
            "Include changed files, checks/evidence and blockers. The supervisor will review before done.",
            "",
            "## Assignment path",
            "",
            f"`{assignment_path}`",
            "",
        ]
    )
    return "\n".join(lines)


def cmd_init(_: argparse.Namespace) -> int:
    ensure_layout()
    print(f"initialized ReleaseWitness supervisor layout at {ROOT}")
    return 0


def cmd_task_new(args: argparse.Namespace) -> int:
    ensure_layout()
    with state_lock():
        data = load_registry()
        if not args.scope:
            raise RelWitError("task needs at least one --scope")
        if not args.acceptance:
            raise RelWitError("task needs at least one --acceptance")
        for dependency in args.depends_on or []:
            if dependency not in data["items"]:
                raise RelWitError(f"unknown dependency: {dependency}")
        supersedes = getattr(args, "supersedes", None)
        takeover_reason = getattr(args, "takeover_reason", None)
        if takeover_reason is not None and not supersedes:
            raise RelWitError("--takeover-reason requires --supersedes")
        predecessor = None
        if supersedes:
            predecessor = data["items"].get(supersedes)
            if not isinstance(predecessor, dict):
                raise RelWitError(f"unknown superseded task: {supersedes}")
            if predecessor.get("status") not in {"blocked", "cancelled"}:
                raise RelWitError(
                    f"{supersedes} is {predecessor.get('status')}; only blocked or cancelled tasks can be superseded"
                )
            if predecessor.get("superseded_by"):
                raise RelWitError(f"{supersedes} is already superseded by {predecessor['superseded_by']}")
            if not isinstance(takeover_reason, str) or not takeover_reason.strip():
                raise RelWitError("--takeover-reason must be non-empty")
            if "\n" in takeover_reason or "\r" in takeover_reason:
                raise RelWitError("--takeover-reason must be a single line")
        task_id = next_id(data["items"])
        created_at = now_iso()
        item = {
            "id": task_id,
            "title": args.title,
            "objective": args.objective or args.title,
            "level": args.level,
            "status": "planned",
            "owner": args.owner,
            "assigned_to": None,
            "scope": [validate_relative_scope(scope) for scope in args.scope],
            "depends_on": args.depends_on or [],
            "preferred_agents": args.preferred_agent or [],
            "capabilities": args.capability or [],
            "acceptance": args.acceptance,
            "verification": args.verification or [],
            "files": [],
            "evidence": [],
            "reports": [],
            "attempts": 0,
            "created_at": created_at,
            "updated_at": created_at,
            "supersedes": supersedes,
            "superseded_by": None,
            "takeover_reason": takeover_reason,
        }
        if predecessor is not None:
            predecessor["superseded_by"] = task_id
            predecessor["updated_at"] = created_at
        data["items"][task_id] = item
        save_registry(data)
        if predecessor is not None:
            sync_item_frontmatter(predecessor)
            append_event(supersedes, f"superseded by {task_id}: {takeover_reason.strip()}")
        atomic_write(item_path(task_id), render_item(item))
    print(task_id)
    return 0


def cmd_task_claim(args: argparse.Namespace) -> int:
    config = load_config()
    with state_lock():
        data = load_registry()
        agent = claim_agent(config, args.agent)
        item = get_item(data, args.task_id)
        if item.get("superseded_by"):
            raise RelWitError(f"{args.task_id} was superseded by {item['superseded_by']}")
        if item["status"] not in {"planned", "assigned", "blocked"}:
            raise RelWitError(f"{args.task_id} is {item['status']}, not claimable")
        if item.get("assigned_to") and item["assigned_to"] != args.agent:
            raise RelWitError(f"{args.task_id} is assigned to {item['assigned_to']}, not {args.agent}")
        blocker = agent_claim_blocker(config, data, item, agent, ignore_id=args.task_id)
        if blocker:
            raise RelWitError(f"{args.task_id} cannot be claimed by {args.agent}: {blocker}")
        if not dependencies_done(data, item):
            raise RelWitError(f"{args.task_id} has unfinished dependencies: {item.get('depends_on', [])}")
        conflict = active_scope_conflict(data, item, ignore_id=args.task_id)
        if conflict:
            raise RelWitError(f"scope conflicts with active task {conflict['id']}: {conflict.get('scope', [])}")
        item["status"] = "in_progress"
        item["assigned_to"] = args.agent
        item["attempts"] = int(item.get("attempts", 0)) + 1
        item["started_at"] = now_iso()
        item["updated_at"] = now_iso()
        record_task_telemetry_locked(config, item, agent, "in_progress")
        save_registry(data)
        sync_item_frontmatter(item)
        append_event(args.task_id, f"claimed by {args.agent}")
    print(f"{args.task_id} claimed by {args.agent}")
    return 0


def cmd_task_update(args: argparse.Namespace) -> int:
    config = load_config()
    with state_lock():
        data = load_registry()
        item = get_item(data, args.task_id)
        assigned = item.get("assigned_to")
        current = item["status"]
        if current in TERMINAL_STATUSES:
            raise RelWitError(f"{current} tasks are terminal; lifecycle updates are not allowed")
        if item.get("superseded_by"):
            raise RelWitError(f"{args.task_id} was superseded by {item['superseded_by']}")
        review_action = args.status in {"needs_review", "done"}
        administrative_action = args.status in {"planned", "blocked", "cancelled"}
        if review_action or administrative_action:
            review_agent(config, args.agent)
        if args.agent and assigned and args.agent != assigned and not review_action:
            raise RelWitError(f"{args.task_id} is assigned to {assigned}, not {args.agent}")
        if args.status == "assigned":
            raise RelWitError("use supervisor cycle/dispatch to assign a task")
        if args.status == "in_progress":
            if current == "assigned":
                raise RelWitError("use task claim before moving a task into in_progress")
            if current != "in_progress":
                raise RelWitError("use task claim before moving a task into in_progress")
            claim_agent(config, args.agent)
        if args.status == "reported":
            raise RelWitError("use task report for a worker completion")
        if args.status == "needs_review" and current != "reported":
            raise RelWitError("a task must be reported before review")
        if args.status == "done" and current != "needs_review":
            raise RelWitError("a task must pass the explicit review gate before done")
        if args.status == "done" and not has_review_evidence(item):
            raise RelWitError("a task needs non-empty review evidence before done")
        if args.status in ACTIVE_WRITER_STATUSES and not assigned:
            raise RelWitError("active task needs an existing assignment; use task claim")
        if args.scopes:
            proposed = dict(item)
            proposed["scope"] = sorted(set(item.get("scope", []) + [validate_relative_scope(path) for path in args.scopes]))
            conflict = active_scope_conflict(data, proposed, ignore_id=args.task_id)
            if conflict:
                raise RelWitError(f"scope conflicts with active task {conflict['id']}: {conflict.get('scope', [])}")
            item["scope"] = proposed["scope"]
        if args.status == "planned":
            item["assigned_to"] = None
        item["status"] = args.status
        if args.files:
            files = [validate_relative_scope(path, "recorded file") for path in args.files]
            out_of_scope = [
                path
                for path in files
                if not any(scope_within(path, task_scope) for task_scope in item.get("scope", []))
            ]
            if out_of_scope:
                raise RelWitError(
                    f"recorded file outside task scope: {', '.join(out_of_scope)}"
                )
            item["files"] = sorted(set(item.get("files", []) + files))
        item["updated_at"] = now_iso()
        save_registry(data)
        sync_item_frontmatter(item)
        append_event(args.task_id, f"status -> {args.status}" + (f": {args.note}" if args.note else ""))
    print(f"{args.task_id}: {args.status}")
    return 0


def normalize_evidence_provenance(
    value: Any,
    default: str = "legacy",
    *,
    allow_legacy: bool = True,
) -> str:
    if value is None:
        value = default
    if not isinstance(value, str) or not value.strip():
        raise RelWitError("evidence provenance cannot be empty")
    provenance = value.strip().lower()
    if provenance not in VALID_EVIDENCE_PROVENANCES or (provenance == "legacy" and not allow_legacy):
        allowed = ", ".join(sorted(VALID_EVIDENCE_PROVENANCES - {"legacy"}))
        raise RelWitError(f"invalid evidence provenance {value!r}; choose one of: {allowed}")
    return provenance


def normalize_evidence_source(value: Any, default: str) -> str:
    if value is None:
        value = default
    if not isinstance(value, str) or not value.strip():
        raise RelWitError("evidence source cannot be empty")
    source = value.strip()
    if "\r" in source or "\n" in source:
        raise RelWitError("evidence source must be a single line")
    return source


def parse_evidence(
    value: str,
    kind: str | None = None,
    *,
    provenance: str | None = None,
    source: str | None = None,
) -> dict[str, str]:
    if not isinstance(value, str):
        raise RelWitError("evidence value must be a string")
    entry: dict[str, str]
    if kind:
        if not isinstance(kind, str) or not kind.strip() or not value.strip():
            raise RelWitError("evidence kind and value cannot be empty")
        entry = {"kind": kind.strip(), "value": value.strip()}
    else:
        if "=" not in value:
            raise RelWitError("evidence must use --kind <kind> --value <value>")
        parsed_kind, parsed_value = value.split("=", 1)
        parsed_kind = parsed_kind.strip()
        parsed_value = parsed_value.strip()
        if not parsed_kind or not parsed_value:
            raise RelWitError("evidence kind and value cannot be empty")
        entry = {"kind": parsed_kind, "value": parsed_value}
    if provenance is not None:
        entry["provenance"] = normalize_evidence_provenance(provenance)
    if source is not None:
        entry["source"] = normalize_evidence_source(source, "cli")
    return entry


def has_review_evidence(item: dict[str, Any]) -> bool:
    evidence = item.get("evidence")
    return isinstance(evidence, list) and any(
        isinstance(entry, dict)
        and entry.get("kind") == "review"
        and isinstance(entry.get("value"), str)
        and bool(entry["value"].strip())
        for entry in evidence
    )


def cmd_task_evidence(args: argparse.Namespace) -> int:
    config = load_config() if args.kind == "review" else None
    with state_lock():
        data = load_registry()
        item = get_item(data, args.task_id)
        if args.kind == "review":
            review_agent(config or {}, args.agent)
            if item.get("status") not in {"reported", "needs_review"}:
                raise RelWitError("review evidence requires a reported or needs_review task")
        evidence = parse_evidence(
            args.value,
            args.kind,
            provenance=normalize_evidence_provenance(args.provenance, "local", allow_legacy=False),
            source=normalize_evidence_source(args.source, "cli"),
        )
        evidence["recorded_at"] = now_iso()
        item.setdefault("evidence", []).append(evidence)
        item["updated_at"] = now_iso()
        if args.kind == "review":
            reviewer = agent_config(config or {}, args.agent)
            upsert_telemetry_event_locked(
                config or {},
                f"review:{args.task_id}:{args.agent}:{evidence['recorded_at']}",
                {
                    "kind": "task",
                    "entity_id": args.task_id,
                    "task_id": args.task_id,
                    "outcome": "completed",
                    "source": "relwit.review",
                    "usage": unavailable_usage("review runtime did not expose usage", "review-runtime"),
                    **_agent_telemetry_metadata(reviewer),
                },
            )
        save_registry(data)
        append_event(args.task_id, f"evidence {evidence['kind']}: {evidence['value']}")
    print(f"evidence added to {args.task_id}")
    return 0


def cmd_task_list(args: argparse.Namespace) -> int:
    data = load_registry()
    items = list(data["items"].values())
    if args.status:
        items = [item for item in items if item.get("status") == args.status]
    for item in sorted(items, key=lambda value: value["id"]):
        deps = ",".join(item.get("depends_on", [])) or "-"
        assigned = item.get("assigned_to") or "-"
        print(f"{item['id']}\t{item['status']}\t{item['level']}\t{assigned}\t{item['title']}\tdeps:{deps}")
    return 0


def cmd_task_show(args: argparse.Namespace) -> int:
    data = load_registry()
    print(json.dumps(get_item(data, args.task_id), indent=2, ensure_ascii=False))
    return 0


def cmd_task_report(args: argparse.Namespace) -> int:
    config = load_config()
    with state_lock():
        data = load_registry()
        item = get_item(data, args.task_id)
        if item.get("assigned_to") != args.agent:
            raise RelWitError(f"{args.task_id} is assigned to {item.get('assigned_to')}, not {args.agent}")
        if item.get("status") != "in_progress":
            raise RelWitError(
                f"{args.task_id} is {item.get('status')}; worker must claim or pull before reporting"
            )
        agent = claim_agent(config, args.agent)
        paths = agent_paths(config, agent)
        report_id = f"{args.task_id}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"
        report_path = path_for(config, "reports_inbox") / f"{report_id}.md"
        default_provenance = "blocked" if args.result == "blocked" else "local"
        provenance = normalize_evidence_provenance(
            getattr(args, "provenance", None),
            default_provenance,
            allow_legacy=False,
        )
        source = normalize_evidence_source(getattr(args, "source", None), rel(report_path))
        files = [validate_relative_scope(path, "reported file") for path in args.files or []]
        out_of_scope = [
            path
            for path in files
            if not any(scope_within(path, task_scope) for task_scope in item.get("scope", []))
        ]
        if out_of_scope:
            raise RelWitError(
                f"reported file outside task scope: {', '.join(out_of_scope)}"
            )
        checks = args.checks or []
        report_lines = [
            "---",
            "type: relwit-worker-report",
            f"task_id: {item['id']}",
            f"agent: {args.agent}",
            f"result: {args.result}",
            f"created_at: {now_iso()}",
            f"provenance: {provenance}",
            f"source: {source}",
            f"files: {json.dumps(files, ensure_ascii=False)}",
            f"checks: {json.dumps(checks, ensure_ascii=False)}",
            "---",
            "",
            f"# Worker report {item['id']}",
            "",
            "## Summary",
            "",
            args.summary,
            "",
            "## Next action",
            "",
            args.next_action,
            "",
            "## Blockers",
            "",
            args.blocker or "- none",
            "",
            "## Evidence",
            "",
        ]
        report_lines.extend(f"- {check}" for check in checks)
        if not checks:
            report_lines.append("- Add focused evidence before review")
        report_lines.append("")
        atomic_write(report_path, "\n".join(report_lines))

        item.setdefault("reports", []).append(rel(report_path))
        item["files"] = sorted(set(item.get("files", []) + files))
        item.setdefault("evidence", []).append(
            {
                "kind": "worker-report",
                "value": rel(report_path),
                "provenance": provenance,
                "source": source,
                "recorded_at": now_iso(),
            }
        )
        for check in checks:
            item.setdefault("evidence", []).append(
                {
                    "kind": "check",
                    "value": check,
                    "provenance": provenance,
                    "source": source,
                    "recorded_at": now_iso(),
                }
            )
        item["status"] = "blocked" if args.result == "blocked" else "reported"
        item["last_result"] = args.result
        item["updated_at"] = now_iso()
        record_task_telemetry_locked(
            config,
            item,
            agent,
            "blocked" if args.result == "blocked" else "completed",
            completed_at=now_iso(),
        )
        save_registry(data)
        sync_item_frontmatter(item)
        append_event(item["id"], f"worker report {rel(report_path)} result={args.result}")

        write_if_missing(paths["report"], f"# Reports for {args.agent}\n\n")
        append_markdown(paths["report"], f"## {now_iso()} - {item['id']} ({args.result})\n\n{args.summary}\n\n- Report: `{rel(report_path)}`\n- Next: {args.next_action}\n\n")
        if args.result == "completed":
            write_if_missing(paths["completed"], f"# Completed reports for {args.agent}\n\n")
            append_markdown(paths["completed"], f"- {now_iso()} - `{item['id']}` - {item['title']} - pending review - `{rel(report_path)}`\n")
            completed = path_for(config, "completed_tasks")
            write_if_missing(completed, "# Completed task reports\n\n")
            append_markdown(completed, f"- {now_iso()} - `{item['id']}` - {item['title']} - worker completed, pending supervisor/reviewer - `{rel(report_path)}`\n")
        reports_index = path_for(config, "reports_index")
        write_if_missing(reports_index, "# Worker reports\n\n")
        append_markdown(reports_index, f"- {now_iso()} - `{item['id']}` - `{args.agent}` - {args.result} - `{rel(report_path)}`\n")
    print(rel(report_path))
    return 0


def cmd_agent_register(args: argparse.Namespace) -> int:
    ensure_layout()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,63}", args.agent_id):
        raise RelWitError("agent id must be lowercase and use letters, digits, _ or -")
    if args.role not in VALID_AGENT_ROLES:
        raise RelWitError(
            f"invalid agent role {args.role}; choose one of: {', '.join(sorted(VALID_AGENT_ROLES))}"
        )
    with state_lock():
        config = load_config()
        if any(agent.get("id") == args.agent_id for agent in config["agents"]):
            raise RelWitError(f"agent already registered: {args.agent_id}")
        directory = normalize_scope(args.directory or f"{config['paths']['agent_root']}/{args.agent_id}")
        agent = {
            "id": args.agent_id,
            "role": args.role,
            "status": "available",
            "directory": directory,
            "scope": [validate_relative_scope(scope) for scope in args.scope or []],
            "capabilities": args.capability or [],
            "max_active": args.max_active,
        }
        if args.inbox_file:
            agent["inbox"] = args.inbox_file
        if args.report_file:
            agent["report"] = args.report_file
        if args.completed_file:
            agent["completed"] = args.completed_file
        if args.runner_command:
            agent["runner"] = {
                "command": args.runner_command,
                "timeout_seconds": args.runner_timeout,
            }
            runner_settings(agent)
        paths = agent_paths(config, agent)
        paths["directory"].mkdir(parents=True, exist_ok=True)
        paths["inbox_dir"].mkdir(parents=True, exist_ok=True)
        write_if_missing(paths["inbox"], f"# INBOX - {args.agent_id}\n\n")
        write_if_missing(paths["report"], f"# REPORTS - {args.agent_id}\n\n")
        write_if_missing(paths["completed"], f"# COMPLETED - {args.agent_id}\n\n")
        config["agents"].append(agent)
        save_config(config)
    print(f"registered agent {args.agent_id} at {directory}")
    return 0


def cmd_agent_status(args: argparse.Namespace) -> int:
    with state_lock():
        config = load_config()
        agent = agent_config(config, args.agent_id)
        agent["status"] = args.status
        save_config(config)
    print(f"{args.agent_id}: {args.status}")
    return 0


def cmd_agent_list(_: argparse.Namespace) -> int:
    config = load_config()
    data = load_registry()
    for agent in config.get("agents", []):
        if not isinstance(agent, dict) or not agent.get("id"):
            continue
        active = sum(
            1
            for item in data["items"].values()
            if isinstance(item, dict)
            and item.get("assigned_to") == agent.get("id")
            and item.get("status") in ACTIVE_WRITER_STATUSES
        )
        execution = "runner" if agent.get("runner") is not None else "manual"
        print(
            f"{agent['id']}\t{agent.get('status', 'available')}\tactive:{active}/{agent.get('max_active', 1)}"
            f"\trole:{agent.get('role', 'worker')}\texecution:{execution}"
        )
    return 0


def agent_active_count(data: dict[str, Any], agent_id: str, ignore_id: str | None = None) -> int:
    return sum(
        1
        for item in data["items"].values()
        if isinstance(item, dict)
        and item.get("id") != ignore_id
        and item.get("assigned_to") == agent_id
        and item.get("status") in ACTIVE_WRITER_STATUSES
    )


def agent_claim_blocker(
    config: dict[str, Any],
    data: dict[str, Any],
    item: dict[str, Any],
    agent: dict[str, Any],
    ignore_id: str | None = None,
) -> str | None:
    agent_id = agent.get("id")
    if not isinstance(agent_id, str) or not agent_id:
        return "agent id is missing"
    if agent.get("status") != "available":
        return f"agent status is {agent.get('status', 'missing')}, expected available"
    try:
        max_active = int(agent.get("max_active", 1))
    except (TypeError, ValueError):
        return "agent max_active is invalid"
    if max_active < 1:
        return "agent max_active must be at least 1"
    active = agent_active_count(data, agent_id, ignore_id=ignore_id)
    if active >= max_active:
        return f"agent is at max_active capacity ({active}/{max_active})"
    if agent.get("role") not in CLAIM_ROLES:
        return "agent role cannot claim implementation work"
    allowed_scopes = agent.get("scope", [])
    task_scopes = item.get("scope", [])
    if not isinstance(allowed_scopes, list) or not isinstance(task_scopes, list):
        return "agent/task scope must be arrays"
    if allowed_scopes and not all(
        isinstance(task_scope, str)
        and any(isinstance(allowed, str) and scope_within(task_scope, allowed) for allowed in allowed_scopes)
        for task_scope in task_scopes
    ):
        return "task scope is outside the agent scope"
    agent_capabilities = agent.get("capabilities", [])
    required_capabilities = item.get("capabilities", [])
    if not isinstance(agent_capabilities, list) or not isinstance(required_capabilities, list):
        return "agent/task capabilities must be arrays"
    if any(not isinstance(capability, str) or not capability.strip() for capability in agent_capabilities):
        return "agent capabilities must be non-empty strings"
    if any(not isinstance(capability, str) or not capability.strip() for capability in required_capabilities):
        return "task capabilities must be non-empty strings"
    capabilities = set(agent_capabilities)
    required = set(required_capabilities)
    if required and not required.issubset(capabilities):
        return f"agent lacks required capabilities: {sorted(required - capabilities)}"
    return None


def agent_can_take(config: dict[str, Any], data: dict[str, Any], item: dict[str, Any], agent: dict[str, Any]) -> bool:
    return agent_claim_blocker(config, data, item, agent) is None


def choose_agent(config: dict[str, Any], data: dict[str, Any], item: dict[str, Any]) -> dict[str, Any] | None:
    raw_agents = config.get("agents", [])
    agents = [agent for agent in raw_agents if isinstance(agent, dict)] if isinstance(raw_agents, list) else []
    preferred = item.get("preferred_agents", [])
    if not isinstance(preferred, list):
        preferred = []
    ordered = [agent for agent_id in preferred for agent in agents if agent.get("id") == agent_id]
    ordered += [agent for agent in agents if agent.get("id") not in preferred]
    for agent in ordered:
        if agent_can_take(config, data, item, agent) and static_runner_readiness(agent)["dispatchable"]:
            return agent
    return None


def assign_task_locked(config: dict[str, Any], data: dict[str, Any], item: dict[str, Any], agent: dict[str, Any]) -> str:
    paths = agent_paths(config, agent)
    assignment_path = paths["inbox_dir"] / f"{item['id']}.md"
    assignment_rel = rel(assignment_path)
    item["status"] = "assigned"
    item["assigned_to"] = agent["id"]
    item["assignment_path"] = assignment_rel
    item["dispatched_at"] = now_iso()
    item["updated_at"] = now_iso()
    content = render_assignment(item, agent, assignment_rel)
    atomic_write(assignment_path, content)
    outbox_path = path_for(config, "outbox") / f"{item['id']}-to-{agent['id']}.md"
    atomic_write(outbox_path, content)
    write_if_missing(paths["inbox"], f"# INBOX - {agent['id']}\n\n")
    append_markdown(paths["inbox"], f"- {now_iso()} - `{item['id']}` assigned - `{assignment_rel}`\n")
    record_assignment_telemetry_locked(config, item, agent)
    return assignment_rel


def dispatch_ready_locked(config: dict[str, Any], data: dict[str, Any], max_assignments: int, retry_blocked: bool = False) -> list[dict[str, str]]:
    candidates = [
        item
        for item in data["items"].values()
        if isinstance(item, dict)
        and (item.get("status") == "planned" or (retry_blocked and item.get("status") == "blocked"))
    ]
    candidates.sort(key=lambda item: (item.get("level", "L4"), item.get("id", "")))
    assignments: list[dict[str, str]] = []
    for item in candidates:
        if len(assignments) >= max_assignments or not dependencies_done(data, item):
            continue
        conflict = active_scope_conflict(data, item, ignore_id=item["id"])
        if conflict:
            continue
        agent = choose_agent(config, data, item)
        if not agent:
            continue
        assignment_rel = assign_task_locked(config, data, item, agent)
        assignments.append({"task_id": item["id"], "agent": agent["id"], "path": assignment_rel})
    if assignments:
        save_registry(data)
        for assignment in assignments:
            sync_item_frontmatter(get_item(data, assignment["task_id"]))
            append_event(assignment["task_id"], f"dispatched to {assignment['agent']} at {assignment['path']}")
    return assignments


def cmd_supervisor_dispatch(args: argparse.Namespace) -> int:
    ensure_layout()
    config = load_config()
    max_assignments = args.max_assignments or int(config["supervisor"].get("max_assignments_per_cycle", 4))
    with state_lock():
        data = load_registry()
        assignments = dispatch_ready_locked(config, data, max_assignments, retry_blocked=args.retry_blocked) if config["supervisor"].get("auto_dispatch", True) else []
    if assignments:
        for assignment in assignments:
            print(f"{assignment['task_id']} -> {assignment['agent']} ({assignment['path']})")
    else:
        print("no task dispatched")
    return 0


def assigned_item_for_agent(data: dict[str, Any], agent_id: str) -> dict[str, Any] | None:
    assigned = [
        item
        for item in data["items"].values()
        if isinstance(item, dict)
        and item.get("assigned_to") == agent_id
        and item.get("status") == "assigned"
    ]
    assigned.sort(key=lambda item: item.get("dispatched_at", item.get("id", "")))
    return assigned[0] if assigned else None


def peek_next_assignment(agent_id: str) -> tuple[dict[str, Any], Path] | None:
    """Read the next assignment without taking ownership of it."""

    config = load_config()
    with state_lock():
        data = load_registry()
        agent = claim_agent(config, agent_id)
        item = assigned_item_for_agent(data, agent_id)
        if item is None:
            return None
        blocker = agent_claim_blocker(config, data, item, agent, ignore_id=item["id"])
        if blocker:
            raise RelWitError(f"{item['id']} cannot be pulled by {agent_id}: {blocker}")
        try:
            path = safe_repo_path(item.get("assignment_path", ""))
        except (TypeError, ValueError, RelWitError) as exc:
            raise RelWitError(
                f"invalid assignment path for {item.get('id', agent_id)}: {exc}"
            ) from exc
        return copy.deepcopy(item), path


def pull_next_assignment(
    agent_id: str, expected_task_id: str | None = None
) -> tuple[dict[str, Any], Path, str] | None:
    config = load_config()
    with state_lock():
        data = load_registry()
        agent = claim_agent(config, agent_id)
        item = assigned_item_for_agent(data, agent_id)
        if item is None or (expected_task_id is not None and item.get("id") != expected_task_id):
            return None
        blocker = agent_claim_blocker(config, data, item, agent, ignore_id=item["id"])
        if blocker:
            raise RelWitError(f"{item['id']} cannot be pulled by {agent_id}: {blocker}")
        try:
            path = safe_repo_path(item.get("assignment_path", ""))
        except (TypeError, ValueError, RelWitError) as exc:
            raise RelWitError(
                f"invalid assignment path for {item.get('id', agent_id)}: {exc}"
            ) from exc
        if not path.is_file():
            raise RelWitError(f"assignment file does not exist: {rel(path)}")
        try:
            assignment_text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise RelWitError(
                f"assignment file cannot be read for {item.get('id', agent_id)}: {exc}"
            ) from exc
        item["status"] = "in_progress"
        item["started_at"] = now_iso()
        item["updated_at"] = now_iso()
        record_task_telemetry_locked(config, item, agent, "in_progress")
        save_registry(data)
        sync_item_frontmatter(item)
        append_event(item["id"], f"pulled by {agent_id}")
    return item, path, assignment_text


def cmd_worker_pull(args: argparse.Namespace) -> int:
    assignment = pull_next_assignment(args.agent)
    if assignment is None:
        print("NO_TASK")
        return 0
    _, _, assignment_text = assignment
    print(assignment_text)
    return 0


def runner_task_status(task_id: str) -> tuple[str, str | None]:
    data = load_registry()
    item = get_item(data, task_id)
    return str(item.get("status")), item.get("last_result") if isinstance(item.get("last_result"), str) else None


def runtime_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def redact_runtime_text(value: Any) -> tuple[str, int]:
    text = runtime_text(value)
    redactions = 0
    for pattern, replacement in RUNTIME_REDACTION_PATTERNS:
        text, count = pattern.subn(replacement, text)
        redactions += count
    return text, redactions


def bound_runtime_output(value: Any, budget: int = MAX_DURABLE_OUTPUT_CHARS) -> dict[str, Any]:
    original = runtime_text(value)
    sanitized, redactions = redact_runtime_text(original)
    truncated = len(sanitized) > budget
    preview = sanitized
    if truncated:
        marker = f"\n...[output clipped; budget={budget} chars]"
        preview = sanitized[: max(0, budget - len(marker))] + marker
    return {
        "captured_chars": len(original),
        "sanitized_chars": len(sanitized),
        "preview_chars": len(preview),
        "budget_chars": budget,
        "redactions": redactions,
        "truncated": truncated,
        "preview": preview,
    }


def parse_machine_runtime_result(value: Any, marker: str) -> dict[str, Any] | None:
    """Accept only a complete, explicit JSON envelope from a trusted adapter."""

    text = runtime_text(value).strip()
    if not text or len(text) > MAX_LOCAL_OUTPUT_CHARS:
        return None
    try:
        payload = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get(marker) != 1:
        return None
    return payload


def safe_runtime_reason(value: Any, default: str) -> str:
    summary = bound_runtime_output(value, 240)["preview"]
    summary = re.sub(r"\s+", " ", summary).strip()
    return summary or default


def default_runtime_disposition(failure_class: str) -> str:
    if failure_class in {"unavailable", "no_target"}:
        return "reassign"
    if failure_class in {"misconfigured", "auth_error"}:
        return "needs_input"
    if failure_class in {"timeout", "runtime_error"}:
        return "retry"
    if failure_class == "quota_limited":
        return "retry"
    return "needs_input"


def normalize_runtime_failure_result(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize an adapter result; text alone never proves quota or auth failure."""

    failure_class = payload.get("failure_class")
    if not isinstance(failure_class, str) or failure_class not in RUNTIME_FAILURE_CLASSES:
        return None
    authoritative = payload.get("authoritative") is True
    if failure_class in {"quota_limited", "auth_error"} and not authoritative:
        return None
    disposition = payload.get("disposition")
    if not isinstance(disposition, str) or disposition not in RUNTIME_DISPOSITIONS:
        disposition = default_runtime_disposition(failure_class)
    return {
        "failure_class": failure_class,
        "disposition": disposition,
        "authoritative": authoritative,
        "classification_source": "adapter_result",
        "reason": safe_runtime_reason(payload.get("reason"), "adapter reported a runtime failure"),
    }


def classify_runner_failure(
    returncode: int,
    stdout: Any = "",
    stderr: Any = "",
    *,
    timed_out: bool = False,
    start_error: str | None = None,
) -> dict[str, Any]:
    """Classify a runner failure without interpreting provider-specific prose."""

    if timed_out:
        failure_class = "timeout"
        return {
            "failure_class": failure_class,
            "disposition": default_runtime_disposition(failure_class),
            "authoritative": True,
            "classification_source": "process_timeout",
            "reason": "runner exceeded its configured timeout",
        }
    if start_error == "not_found":
        failure_class = "unavailable"
        return {
            "failure_class": failure_class,
            "disposition": default_runtime_disposition(failure_class),
            "authoritative": True,
            "classification_source": "process_start",
            "reason": "runner executable was not found",
        }
    if start_error:
        failure_class = "unavailable"
        return {
            "failure_class": failure_class,
            "disposition": default_runtime_disposition(failure_class),
            "authoritative": True,
            "classification_source": "process_start",
            "reason": "runner process could not start",
        }
    for stream in (stdout, stderr):
        payload = parse_machine_runtime_result(stream, RUNTIME_RESULT_MARKER)
        if payload is None:
            continue
        normalized = normalize_runtime_failure_result(payload)
        if normalized is not None:
            return normalized
        return {
            "failure_class": "unknown",
            "disposition": default_runtime_disposition("unknown"),
            "authoritative": False,
            "classification_source": "invalid_adapter_result",
            "reason": "adapter result was missing required authoritative fields",
        }
    failure_class = "runtime_error" if returncode != 0 else "unknown"
    return {
        "failure_class": failure_class,
        "disposition": default_runtime_disposition(failure_class),
        "authoritative": False,
        "classification_source": "exit_code" if returncode != 0 else "missing_report",
        "reason": "runner exited without an authoritative failure envelope"
        if returncode != 0
        else "runner did not submit a worker report",
    }


def safe_markdown_code(value: Any) -> str:
    return runtime_text(value).replace("```", "` ` `")


def safe_runtime_command(command: Any) -> tuple[str, int]:
    if isinstance(command, (list, tuple)):
        safe_parts: list[str] = []
        redactions = 0
        for part in command:
            safe_part, count = redact_runtime_text(part)
            safe_parts.append(safe_part)
            redactions += count
        return json.dumps(safe_parts, ensure_ascii=False), redactions
    return redact_runtime_text(command)


def normalize_qa_command(value: Any, index: int) -> dict[str, Any]:
    """Validate one explicit QA command without interpreting legacy strings."""

    label = f"config.supervisor.qa_commands[{index}]"
    if isinstance(value, str):
        raise RelWitError(
            f"{label} must be a structured object; legacy command strings are rejected; "
            "use {mode: 'argv', argv: [...]} or explicit {mode: 'shell', command: '...'}"
        )
    if not isinstance(value, dict):
        raise RelWitError(f"{label} must be an object with mode and argv/command")
    unknown = [key for key in value if key not in {"mode", "argv", "command"}]
    if unknown:
        raise RelWitError(f"{label} has unsupported fields: {', '.join(map(str, unknown))}")
    mode = value.get("mode")
    if not isinstance(mode, str) or mode not in QA_EXECUTION_MODES:
        raise RelWitError(f"{label}.mode must be one of: argv, shell")
    if mode == "argv":
        if "command" in value:
            raise RelWitError(f"{label} cannot contain command when mode=argv")
        argv = value.get("argv")
        if not isinstance(argv, list) or not argv:
            raise RelWitError(f"{label}.argv must be a non-empty array of non-empty strings")
        if any(not isinstance(part, str) or not part or "\x00" in part for part in argv):
            raise RelWitError(f"{label}.argv must be a non-empty array of non-empty strings without NUL")
        return {"mode": "argv", "argv": list(argv)}
    if "argv" in value:
        raise RelWitError(f"{label} cannot contain argv when mode=shell")
    command = value.get("command")
    if not isinstance(command, str) or not command.strip() or "\x00" in command:
        raise RelWitError(f"{label}.command must be a non-empty string without NUL")
    return {"mode": "shell", "command": command}


def normalize_qa_commands(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise RelWitError("config.supervisor.qa_commands must be an array of structured command objects")
    return [normalize_qa_command(entry, index) for index, entry in enumerate(value)]


def qa_command_display(spec: dict[str, Any]) -> str:
    """Render command identity for evidence; this never participates in execution."""

    if spec["mode"] == "argv":
        return f"argv {json.dumps(spec['argv'], ensure_ascii=False)}"
    return f"shell {spec['command']}"


def write_runtime_spool(
    config: dict[str, Any],
    category: str,
    identifier: str,
    records: list[dict[str, Any]],
) -> Path:
    spool_path = path_for(config, "runtime_spool") / (
        f"{category}-{identifier}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}.md"
    )
    lines = [
        f"# Local runtime output {category} {identifier}",
        "",
        "- provenance: `local`",
        "- storage: `ignored local diagnostic spool`",
        "- note: output is redacted and bounded before local persistence",
        "",
    ]
    for index, record in enumerate(records, start=1):
        command, _ = safe_runtime_command(record.get("command", ""))
        stdout = bound_runtime_output(record.get("stdout", ""), MAX_LOCAL_OUTPUT_CHARS)
        stderr = bound_runtime_output(record.get("stderr", ""), MAX_LOCAL_OUTPUT_CHARS)
        lines.extend(
            [
                f"## command {index}",
                "",
                f"- execution_mode: `{safe_markdown_code(record.get('execution_mode', 'legacy'))}`",
                f"- command: `{safe_markdown_code(command)}`",
                f"- returncode: `{record.get('returncode', 127)}`",
                f"- stdout_chars: `{stdout['captured_chars']}`",
                f"- stderr_chars: `{stderr['captured_chars']}`",
                f"- stdout_truncated: `{str(stdout['truncated']).lower()}`",
                f"- stderr_truncated: `{str(stderr['truncated']).lower()}`",
                "",
                "### stdout",
                "",
                "```text",
                safe_markdown_code(stdout["preview"]),
                "```",
                "",
                "### stderr",
                "",
                "```text",
                safe_markdown_code(stderr["preview"]),
                "```",
                "",
            ]
        )
        if record.get("failure_class"):
            lines[lines.index(f"## command {index}") + 1:lines.index(f"## command {index}") + 1] = [
                f"- failure_class: `{safe_markdown_code(record['failure_class'])}`",
                f"- disposition: `{safe_markdown_code(record.get('disposition', 'needs_input'))}`",
                "",
            ]
    ensure_local_only_dir(config, spool_path.parent)
    atomic_write(spool_path, "\n".join(lines))
    return spool_path


def probe_runtime_readiness(
    config: dict[str, Any],
    agent: dict[str, Any],
    item: dict[str, Any],
    assignment_path: Path,
) -> dict[str, Any]:
    """Run the optional adapter preflight and return a sanitized readiness result."""

    static = static_runner_readiness(agent)
    if static["state"] in {"unavailable", "misconfigured", "no_target"}:
        return static
    rendered = render_preflight_command(agent, item, assignment_path)
    if rendered is None:
        return static
    command, timeout = rendered
    stdout = ""
    stderr = ""
    returncode = 127
    timed_out = False
    start_error: str | None = None
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        returncode = result.returncode
        stdout = result.stdout
        stderr = result.stderr
    except FileNotFoundError:
        start_error = "not_found"
        stderr = "preflight executable was not found"
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        returncode = 124
        stdout = runtime_text(exc.stdout or "")
        stderr = f"preflight timed out after {timeout}s"
    except OSError:
        start_error = "os_error"
        stderr = "preflight process could not start"

    local_spool = write_runtime_spool(
        config,
        "preflight",
        f"{agent['id']}-{item['id']}",
        [
            {
                "command": command,
                "returncode": returncode,
                "stdout": stdout,
                "stderr": stderr,
                "execution_mode": "argv",
            }
        ],
    )
    base = {
        "execution": "runner",
        "preflight": True,
        "local_spool": rel(local_spool),
        "returncode": returncode,
    }
    if start_error or timed_out:
        failure = classify_runner_failure(
            returncode,
            stdout,
            stderr,
            timed_out=timed_out,
            start_error=start_error,
        )
        return {
            **base,
            "state": "unknown" if timed_out else "unavailable",
            "reason": failure["reason"],
            "failure_class": failure["failure_class"],
            "disposition": failure["disposition"],
            "dispatchable": False,
        }
    payload = parse_machine_runtime_result(stdout, PREFLIGHT_RESULT_MARKER)
    if payload is None:
        payload = parse_machine_runtime_result(stderr, PREFLIGHT_RESULT_MARKER)
    state = payload.get("state") if payload is not None else None
    if not isinstance(state, str) or state not in RUNTIME_READINESS_STATES:
        return {
            **base,
            "state": "unknown",
            "reason": "preflight returned no valid machine-readable readiness state",
            "failure_class": "unknown",
            "disposition": "needs_input",
            "dispatchable": False,
        }
    if returncode != 0 and state == "ready":
        return {
            **base,
            "state": "unknown",
            "reason": "preflight exit status contradicted ready state",
            "failure_class": "unknown",
            "disposition": "needs_input",
            "dispatchable": False,
        }
    disposition = payload.get("disposition") if payload is not None else None
    if not isinstance(disposition, str) or disposition not in RUNTIME_DISPOSITIONS:
        disposition = default_runtime_disposition(
            state if state in RUNTIME_FAILURE_CLASSES else "unknown"
        )
    return {
        **base,
        "state": state,
        "reason": safe_runtime_reason(payload.get("reason"), f"preflight state={state}"),
        "failure_class": state if state in RUNTIME_FAILURE_CLASSES else None,
        "disposition": disposition,
        "dispatchable": state == "ready",
    }


def record_runtime_event(task_id: str, result: dict[str, Any], *, kind: str) -> None:
    """Record bounded runtime metadata while leaving task ownership unchanged."""

    fields = [f"state={result.get('state', 'unknown')}"]
    failure_class = result.get("failure_class")
    if isinstance(failure_class, str) and failure_class:
        fields.append(f"failure_class={failure_class}")
    disposition = result.get("disposition")
    if isinstance(disposition, str) and disposition:
        fields.append(f"disposition={disposition}")
    reason = safe_runtime_reason(result.get("reason"), "no runtime reason supplied")
    fields.append(f"reason={reason}")
    local_spool = result.get("local_spool")
    if isinstance(local_spool, str) and local_spool:
        fields.append(f"local_spool={local_spool}")
    record_task_evidence(
        task_id,
        kind,
        "; ".join(fields),
        provenance="local",
        source="worker runtime readiness",
    )


def output_summary_lines(lines: list[str], name: str, summary: dict[str, Any]) -> None:
    lines.extend(
        [
            f"### {name}",
            "",
            f"- captured_chars: `{summary['captured_chars']}`",
            f"- sanitized_chars: `{summary['sanitized_chars']}`",
            f"- preview_chars: `{summary['preview_chars']}`",
            f"- budget_chars: `{summary['budget_chars']}`",
            f"- redactions: `{summary['redactions']}`",
            f"- truncated: `{str(summary['truncated']).lower()}`",
            "",
            "```text",
            safe_markdown_code(summary["preview"]),
            "```",
            "",
        ]
    )


def write_runner_evidence(
    config: dict[str, Any],
    item: dict[str, Any],
    agent: dict[str, Any],
    command: list[str],
    returncode: int,
    duration_seconds: float,
    stdout: str,
    stderr: str,
    local_spool: Path,
    failure: dict[str, Any] | None = None,
) -> Path:
    evidence_path = path_for(config, "evidence") / (
        f"runner-{item['id']}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}.md"
    )
    stdout_summary = bound_runtime_output(stdout)
    stderr_summary = bound_runtime_output(stderr)
    safe_command, command_redactions = safe_runtime_command(command)
    lines = [
        f"# Runner evidence {item['id']}",
        "",
        "- provenance: `local`",
        "- source: `configured agent runner`",
        f"- agent: `{agent['id']}`",
        f"- command: `{safe_markdown_code(safe_command)}`",
        f"- command_redactions: `{command_redactions}`",
        f"- returncode: `{returncode}`",
        f"- duration_seconds: `{duration_seconds:.2f}`",
        f"- local_spool: `{rel(local_spool)}`",
        "",
    ]
    if failure is not None:
        lines.extend(
            [
                f"- failure_class: `{failure['failure_class']}`",
                f"- disposition: `{failure['disposition']}`",
                f"- authoritative: `{str(failure['authoritative']).lower()}`",
                f"- classification_source: `{failure['classification_source']}`",
                f"- failure_reason: `{safe_markdown_code(failure['reason'])}`",
                "",
            ]
        )
    output_summary_lines(lines, "stdout summary", stdout_summary)
    output_summary_lines(lines, "stderr summary", stderr_summary)
    atomic_write(evidence_path, "\n".join(lines))
    return evidence_path


def record_task_evidence(
    task_id: str,
    kind: str,
    value: str,
    *,
    provenance: str = "local",
    source: str = "runner",
) -> None:
    with state_lock():
        data = load_registry()
        item = get_item(data, task_id)
        evidence = parse_evidence(
            value,
            kind,
            provenance=normalize_evidence_provenance(provenance, "local", allow_legacy=False),
            source=normalize_evidence_source(source, "runner"),
        )
        evidence["recorded_at"] = now_iso()
        item.setdefault("evidence", []).append(evidence)
        item["updated_at"] = now_iso()
        save_registry(data)
        append_event(task_id, f"evidence {evidence['kind']}: {evidence['value']}")


def auto_report_runner_failure(
    task_id: str,
    agent_id: str,
    summary: str,
    next_action: str,
    check: str,
) -> None:
    cmd_task_report(
        argparse.Namespace(
            task_id=task_id,
            agent=agent_id,
            result="failed",
            summary=summary,
            next_action=next_action,
            files=[],
            checks=[check],
            blocker="Runner did not complete a valid worker report.",
        )
    )


def run_configured_runner(
    config: dict[str, Any],
    agent: dict[str, Any],
    item: dict[str, Any],
    assignment_path: Path,
) -> tuple[int, Path]:
    command, timeout = render_runner_command(agent, item, assignment_path)
    started = time.monotonic()
    stdout = ""
    stderr = ""
    returncode = 127
    start_error: str | None = None
    timed_out = False
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        returncode = result.returncode
        stdout = result.stdout
        stderr = result.stderr
    except FileNotFoundError as exc:
        start_error = "not_found"
        stderr = f"runner executable was not found: {exc}"
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        returncode = 124
        stdout = str(exc.stdout or "")
        stderr = f"runner timed out after {timeout}s\n{exc.stderr or ''}"
    except OSError as exc:
        start_error = "os_error"
        stderr = f"runner could not start: {exc}"

    failure = classify_runner_failure(
        returncode,
        stdout,
        stderr,
        timed_out=timed_out,
        start_error=start_error,
    )
    execution_duration_ms = max(0, round((time.monotonic() - started) * 1000))
    usage = usage_from_runtime_streams(stdout, stderr)
    status_before_evidence, _ = runner_task_status(item["id"])
    failure_metadata = failure if returncode != 0 or status_before_evidence == "in_progress" else None
    local_spool = write_runtime_spool(
        config,
        "runner",
        item["id"],
        [
            {
                "command": command,
                "returncode": returncode,
                "stdout": stdout,
                "stderr": stderr,
                "execution_mode": "argv",
                "failure_class": failure["failure_class"] if failure_metadata is not None else None,
                "disposition": failure["disposition"] if failure_metadata is not None else None,
            }
        ],
    )
    evidence_path = write_runner_evidence(
        config,
        item,
        agent,
        command,
        returncode,
        execution_duration_ms / 1000,
        stdout,
        stderr,
        local_spool,
        failure_metadata,
    )
    evidence_suffix = (
        f" failure_class={failure['failure_class']} disposition={failure['disposition']}"
        if failure_metadata is not None
        else ""
    )
    record_task_evidence(
        item["id"],
        "runner",
        f"{rel(evidence_path)} (returncode={returncode}{evidence_suffix})",
        provenance="local",
        source=rel(evidence_path),
    )
    status, last_result = runner_task_status(item["id"])
    if status == "in_progress":
        if returncode == 0:
            reason = (
                "runner exited successfully without submitting task report; "
                f"failure_class={failure['failure_class']}; disposition={failure['disposition']}"
            )
        else:
            reason = (
                f"runner exited with returncode {returncode}; "
                f"failure_class={failure['failure_class']}; disposition={failure['disposition']}"
            )
        auto_report_runner_failure(
            item["id"],
            agent["id"],
            f"Configured runner failed validation: {reason}.",
            f"Apply the bounded disposition `{failure['disposition']}` after inspecting the sanitized evidence.",
            f"runner evidence: {rel(evidence_path)}",
        )
        status, last_result = runner_task_status(item["id"])
    elif returncode != 0:
        stderr = stderr or f"runner exited with returncode {returncode} after reporting"
    outcome = "completed" if status == "reported" and last_result == "completed" else "failed"
    with state_lock():
        telemetry_data = load_registry()
        telemetry_item = get_item(telemetry_data, item["id"])
        record_task_telemetry_locked(
            config,
            telemetry_item,
            agent,
            outcome,
            execution_duration_ms=execution_duration_ms,
            usage=usage,
        )
    if status == "blocked":
        return 2, evidence_path
    if returncode != 0 or last_result == "failed":
        return 1, evidence_path
    if status != "reported":
        return 1, evidence_path
    return 0, evidence_path


def cmd_worker_run(args: argparse.Namespace) -> int:
    config = load_config()
    agent = claim_agent(config, args.agent)
    if agent.get("runner") is None:
        raise RelWitError(
            f"agent {args.agent} has no configured runner; use worker pull for a manual runtime"
        )
    if args.max_tasks < 1:
        raise RelWitError("--max-tasks must be at least 1")
    if not 0 <= args.wait_seconds <= MAX_RUNNER_WAIT_SECONDS:
        raise RelWitError(f"--wait-seconds must be between 0 and {MAX_RUNNER_WAIT_SECONDS}")
    if not 0.1 <= args.poll_seconds <= 60:
        raise RelWitError("--poll-seconds must be between 0.1 and 60")

    completed = 0
    deadline = time.monotonic() + args.wait_seconds
    while completed < args.max_tasks:
        candidate = peek_next_assignment(args.agent)
        if candidate is None:
            remaining = deadline - time.monotonic()
            if args.wait_seconds <= 0 or remaining <= 0:
                if completed == 0:
                    print("NO_TASK")
                break
            time.sleep(min(args.poll_seconds, remaining))
            continue
        candidate_item, candidate_path = candidate
        readiness = probe_runtime_readiness(config, agent, candidate_item, candidate_path)
        if not readiness["dispatchable"]:
            record_runtime_event(candidate_item["id"], readiness, kind="runtime-readiness")
            print(
                f"{candidate_item['id']} runtime_state={readiness['state']} "
                f"failure_class={readiness.get('failure_class') or 'none'} "
                f"disposition={readiness.get('disposition') or 'needs_input'} "
                f"spool={readiness.get('local_spool') or 'none'}"
            )
            return 2
        if readiness.get("preflight"):
            record_runtime_event(candidate_item["id"], readiness, kind="runtime-readiness")
        assignment = pull_next_assignment(args.agent, expected_task_id=candidate_item["id"])
        if assignment is None:
            continue
        item, assignment_path, _ = assignment
        result, evidence_path = run_configured_runner(config, agent, item, assignment_path)
        status, last_result = runner_task_status(item["id"])
        print(
            f"{item['id']} runner_status={status} result={last_result or 'none'} "
            f"evidence={rel(evidence_path)}"
        )
        if result != 0:
            return result
        completed += 1
    return 0


def parse_frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"\A---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not match:
        return {}
    result: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            result[key.strip()] = value.strip()
    return result


def parse_frontmatter_json(frontmatter: dict[str, str], key: str, default: Any) -> Any:
    value = frontmatter.get(key)
    if value is None:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value.strip('"')


def load_supervisor_state(config: dict[str, Any]) -> dict[str, Any]:
    path = path_for(config, "supervisor_state")
    if not path.exists():
        return {"version": 1, "cycle": 0, "ingested_reports": [], "last_qa": None}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RelWitError(f"invalid supervisor state: {exc}") from exc
    return value if isinstance(value, dict) else {"version": 1, "cycle": 0, "ingested_reports": [], "last_qa": None}


def save_supervisor_state(config: dict[str, Any], state: dict[str, Any]) -> None:
    atomic_write(path_for(config, "supervisor_state"), json.dumps(state, indent=2, ensure_ascii=False) + "\n")


def ingest_reports_locked(config: dict[str, Any], data: dict[str, Any], state: dict[str, Any]) -> list[str]:
    inbox = path_for(config, "reports_inbox")
    raw_processed = state.get("ingested_reports", [])
    processed = set(value for value in raw_processed if isinstance(value, str)) if isinstance(raw_processed, list) else set()
    ingested: list[str] = []
    for candidate_path in sorted(inbox.glob("*.md")):
        try:
            report_path = safe_repo_path(candidate_path)
            if not report_path.is_file():
                continue
            report_rel = rel(report_path)
            if report_path.name.lower() == "readme.md" or report_rel in processed:
                continue
            frontmatter = parse_frontmatter(report_path)
        except (OSError, UnicodeError, TypeError, ValueError, RelWitError):
            continue
        if frontmatter.get("type") != "relwit-worker-report":
            continue
        task_id = frontmatter.get("task_id")
        if not task_id or task_id not in data["items"]:
            continue
        agent_id = frontmatter.get("agent")
        result = frontmatter.get("result")
        if not agent_id or result not in {"completed", "blocked", "failed"}:
            continue
        try:
            report_agent = agent_config(config, agent_id)
        except RelWitError:
            continue
        item = data["items"][task_id]
        if not isinstance(item, dict):
            continue
        if item.get("assigned_to") != agent_id or item.get("status") != "in_progress":
            continue
        try:
            report_provenance = normalize_evidence_provenance(frontmatter.get("provenance"), "legacy")
            report_source = normalize_evidence_source(frontmatter.get("source"), report_rel)
        except RelWitError:
            continue
        reports = item.get("reports")
        files_on_item = item.get("files")
        evidence = item.get("evidence")
        scopes = item.get("scope")
        if (
            not isinstance(reports, list)
            or not isinstance(files_on_item, list)
            or not isinstance(evidence, list)
            or not isinstance(scopes, list)
            or any(not isinstance(scope, str) for scope in scopes)
        ):
            continue
        if report_rel not in reports:
            reports.append(report_rel)
        item.setdefault("evidence", []).append(
            {
                "kind": "worker-report",
                "value": report_rel,
                "provenance": report_provenance,
                "source": report_source,
                "recorded_at": now_iso(),
            }
        )
        files = parse_frontmatter_json(frontmatter, "files", [])
        safe_files: list[str] = []
        unsafe_files: list[str] = []
        if isinstance(files, list):
            for raw_path in files:
                if not isinstance(raw_path, str):
                    unsafe_files.append(repr(raw_path))
                    continue
                try:
                    candidate = validate_relative_scope(raw_path, "reported file")
                except RelWitError:
                    unsafe_files.append(raw_path)
                    continue
                if any(scope_within(candidate, task_scope) for task_scope in scopes):
                    safe_files.append(candidate)
                else:
                    unsafe_files.append(candidate)
            item["files"] = sorted(set(files_on_item + safe_files))
        if unsafe_files:
            item.setdefault("evidence", []).append(
                {
                    "kind": "warning",
                    "value": f"ignored unsafe or out-of-scope report files: {', '.join(unsafe_files)}",
                    "recorded_at": now_iso(),
                }
            )
        if result == "blocked":
            item["status"] = "blocked"
        elif result in {"completed", "failed"} and item.get("status") in {"assigned", "in_progress"}:
            item["status"] = "reported"
        item["updated_at"] = now_iso()
        record_task_telemetry_locked(
            config,
            item,
            report_agent,
            "blocked" if result == "blocked" else "completed" if result == "completed" else "failed",
            completed_at=now_iso(),
        )
        processed.add(report_rel)
        ingested.append(report_rel)
        append_event(task_id, f"ingested report {report_rel}")
    if ingested:
        save_registry(data)
        state["ingested_reports"] = sorted(processed)
        state["last_ingest_at"] = now_iso()
    return ingested


def cmd_supervisor_ingest(_: argparse.Namespace) -> int:
    config = load_config()
    with state_lock():
        data = load_registry()
        state = load_supervisor_state(config)
        ingested = ingest_reports_locked(config, data, state)
        save_supervisor_state(config, state)
    print("\n".join(ingested) if ingested else "no new reports")
    return 0


def run_qa(config: dict[str, Any], cycle_id: str) -> dict[str, Any]:
    commands = normalize_qa_commands(config["supervisor"].get("qa_commands", []))
    if not commands:
        return {"status": "not_configured", "commands": [], "evidence": None}
    source_state = release_source_fingerprint(config)
    timeout = int(config["supervisor"].get("qa_timeout_seconds", 900))
    raw_results = []
    for spec in commands:
        started = time.monotonic()
        command = spec["argv"] if spec["mode"] == "argv" else spec["command"]
        try:
            result = subprocess.run(
                command,
                cwd=ROOT,
                shell=spec["mode"] == "shell",
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
            raw_results.append(
                {
                    "command": command,
                    "execution_mode": spec["mode"],
                    "returncode": result.returncode,
                    "duration_sec": round(time.monotonic() - started, 2),
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                }
            )
        except subprocess.TimeoutExpired as exc:
            raw_results.append(
                {
                    "command": command,
                    "execution_mode": spec["mode"],
                    "returncode": 124,
                    "duration_sec": round(time.monotonic() - started, 2),
                    "stdout": runtime_text(exc.stdout),
                    "stderr": f"timeout after {timeout}s",
                }
            )
        except FileNotFoundError as exc:
            raw_results.append(
                {
                    "command": command,
                    "execution_mode": spec["mode"],
                    "returncode": 127,
                    "duration_sec": round(time.monotonic() - started, 2),
                    "stdout": "",
                    "stderr": f"QA executable was not found: {exc}",
                }
            )
        except OSError as exc:
            raw_results.append(
                {
                    "command": command,
                    "execution_mode": spec["mode"],
                    "returncode": 126,
                    "duration_sec": round(time.monotonic() - started, 2),
                    "stdout": "",
                    "stderr": f"QA command could not start: {exc}",
                }
            )
    local_spool = write_runtime_spool(config, "qa", cycle_id, raw_results)
    results = []
    for result in raw_results:
        display_command = qa_command_display(
            {"mode": result["execution_mode"], "argv": result["command"]}
            if result["execution_mode"] == "argv"
            else {"mode": "shell", "command": result["command"]}
        )
        safe_command, command_redactions = safe_runtime_command(display_command)
        results.append(
            {
                "command": safe_command,
                "execution_mode": result["execution_mode"],
                "command_redactions": command_redactions,
                "returncode": result["returncode"],
                "duration_sec": result["duration_sec"],
                "stdout": bound_runtime_output(result["stdout"]),
                "stderr": bound_runtime_output(result["stderr"]),
                "local_spool": rel(local_spool),
            }
        )
    status = "pass" if all(result["returncode"] == 0 for result in results) else "fail"
    evidence_path = path_for(config, "evidence") / f"{cycle_id}-qa.md"
    lines = [
        f"# QA evidence {cycle_id}",
        "",
        "- provenance: `local`",
        "- source: `configured supervisor.qa_commands`",
        f"- local_spool: `{rel(local_spool)}`",
        f"- output_budget_chars: `{MAX_DURABLE_OUTPUT_CHARS}` per stream",
        f"- source_fingerprint: `{source_state['fingerprint']}`",
        f"- source_version: `{source_state['version']}`",
        f"- source_vcs: `{source_state['vcs']}`",
        f"- source_head_sha: `{source_state['head_sha']}`",
        f"- source_dirty_state: `{source_state['dirty_state']}`",
        f"- source_dirty_path_count: `{source_state['dirty_path_count']}`",
        f"- source_file_count: `{source_state['source_file_count']}`",
        f"- tracked_path_count: `{source_state['tracked_path_count']}`",
        f"- untracked_path_count: `{source_state['untracked_path_count']}`",
        f"- qa_config_version: `{source_state['qa_config_version']}`",
        f"- qa_config_fingerprint: `{source_state['qa_config_fingerprint']}`",
        f"- executed_checks: `{len(results)}`",
        "",
    ]
    for result in results:
        lines.extend(
            [
                f"## `{safe_markdown_code(result['command'])}`",
                "",
                f"- execution_mode: `{result['execution_mode']}`",
                f"- command_redactions: `{result['command_redactions']}`",
                f"- returncode: `{result['returncode']}`",
                f"- duration_sec: `{result['duration_sec']}`",
                f"- local_spool: `{result['local_spool']}`",
                "",
            ]
        )
        output_summary_lines(lines, "stdout summary", result["stdout"])
        output_summary_lines(lines, "stderr summary", result["stderr"])
    atomic_write(evidence_path, "\n".join(lines))
    return {
        "status": status,
        "commands": results,
        "evidence": rel(evidence_path),
        "local_spool": rel(local_spool),
        "provenance": "local",
        "source": "configured supervisor.qa_commands",
        "recorded_at": now_iso(),
        "source_fingerprint": source_state["fingerprint"],
        "source_fingerprint_algorithm": source_state["algorithm"],
        "source_version": source_state["version"],
        "source_vcs": source_state["vcs"],
        "source_head_sha": source_state["head_sha"],
        "source_dirty": source_state["dirty"],
        "source_dirty_state": source_state["dirty_state"],
        "source_dirty_path_count": source_state["dirty_path_count"],
        "source_file_count": source_state["source_file_count"],
        "tracked_path_count": source_state["tracked_path_count"],
        "untracked_path_count": source_state["untracked_path_count"],
        "qa_config_version": source_state["qa_config_version"],
        "qa_config_fingerprint": source_state["qa_config_fingerprint"],
        "executed_checks": [result["command"] for result in results],
        "execution_modes": [result["execution_mode"] for result in results],
    }


def validate_qa_source(
    config: dict[str, Any], state: dict[str, Any], source_state: dict[str, Any] | None = None
) -> dict[str, str]:
    last_qa = state.get("last_qa")
    if not isinstance(last_qa, dict) or last_qa.get("status") != "pass":
        return {"status": "not_checked"}
    recorded = last_qa.get("source_fingerprint")
    if not isinstance(recorded, str) or not recorded:
        return {"status": "QA_STALE", "reason": "QA result has no source fingerprint"}
    try:
        current = source_state if source_state is not None else release_source_fingerprint(config)
    except RelWitError as exc:
        return {"status": "invalid", "reason": str(exc)}
    if current["fingerprint"] != recorded:
        if last_qa.get("source_fingerprint_algorithm") != current.get("algorithm", SOURCE_FINGERPRINT_ALGORITHM):
            return {"status": "QA_STALE", "reason": "QA was recorded with an earlier source-fingerprint algorithm; rerun QA"}
        return {"status": "QA_STALE", "reason": "current release source state differs from QA source state"}
    return {"status": "valid"}


def production_snapshot_details(
    config: dict[str, Any], data: dict[str, Any], state: dict[str, Any]
) -> tuple[list[tuple[str, str]], bool, dict[str, Any]]:
    items = list(data["items"].values())
    if not items:
        task_gate = "manual"
    else:
        task_gate = "pass" if all(item.get("status") in {"done", "cancelled"} for item in items) else "fail"
    qa_status = (state.get("last_qa") or {}).get("status", "not_configured")
    durability = release_durability_snapshot(config, state)
    qa_source = {"status": durability["qa_source_state"]}
    if durability.get("qa_source_reason"):
        qa_source["reason"] = durability["qa_source_reason"]
    if qa_status == "pass":
        qa_gate = "pass" if qa_source["status"] == "valid" else "fail"
    else:
        qa_gate = "manual" if qa_status == "not_configured" else "fail"
    blocked_gate = "pass" if not any(item.get("status") == "blocked" for item in items) else "fail"
    readiness_files = config["supervisor"].get("operational_readiness_files", [])
    readiness_gate = "manual"
    try:
        if isinstance(readiness_files, list) and readiness_files:
            readiness_gate = "pass"
            for value in readiness_files:
                candidate = safe_repo_path(value)
                if not candidate.is_file() or not candidate.read_text(encoding="utf-8").strip():
                    readiness_gate = "manual"
                    break
    except (OSError, TypeError, RelWitError):
        readiness_gate = "manual"
    gates = [
        ("all_tasks_done", task_gate),
        ("qa", qa_gate),
        ("qa_source_state", "pass" if qa_source["status"] == "valid" else qa_source["status"]),
        ("release_source_durability", durability["status"]),
        ("no_blocked_tasks", blocked_gate),
        ("operational_rollback_notes", readiness_gate),
    ]
    return gates, all(value == "pass" for _, value in gates), durability


def production_snapshot(config: dict[str, Any], data: dict[str, Any], state: dict[str, Any]) -> tuple[list[tuple[str, str]], bool]:
    gates, ready, _ = production_snapshot_details(config, data, state)
    return gates, ready


def choose_next_action(data: dict[str, Any], assignments: list[dict[str, str]], config: dict[str, Any], qa_result: dict[str, Any] | None = None) -> str:
    blocked = [item for item in data["items"].values() if item.get("status") == "blocked"]
    failed = [item for item in data["items"].values() if item.get("last_result") == "failed"]
    reported = [item for item in data["items"].values() if item.get("status") == "reported"]
    needs_review = [item for item in data["items"].values() if item.get("status") == "needs_review"]
    planned = [item for item in data["items"].values() if item.get("status") == "planned" and dependencies_done(data, item)]
    active = [item for item in data["items"].values() if item.get("status") in ACTIVE_WRITER_STATUSES]
    if blocked:
        return f"Resolve blocker for {blocked[0]['id']} and attach the missing decision/evidence."
    if qa_result and qa_result.get("status") == "fail":
        return f"Read QA evidence at {qa_result.get('evidence')}; create a scoped debug task before dispatching more work."
    if failed:
        return f"Read the failed worker report for {failed[0]['id']} and create a scoped debug task with a new hypothesis."
    if reported:
        return f"Review worker report for {reported[0]['id']}; run QA and create a debug task if evidence fails."
    if needs_review:
        return f"Complete the review gate for {needs_review[0]['id']}; accept evidence or create a scoped debug task."
    if assignments:
        return f"Workers pull assigned tasks from their INBOX.md; wait for reports from {', '.join(a['task_id'] for a in assignments)}."
    if planned:
        return "Register an eligible worker or widen its configured scope/capabilities, then dispatch again."
    if active:
        return "Wait for active workers to report; do not assign overlapping writers."
    if data["items"] and all(item.get("status") in {"done", "cancelled"} for item in data["items"].values()):
        return "Run the production release gate and obtain explicit deploy approval."
    return "Create the next scoped work item from the project goal."


SUPERVISOR_REPORT_MARKER = re.compile(
    r"^<!-- relwit-report: registry_sha256=([0-9a-f]{64}) -->$", re.MULTILINE
)


def supervisor_report_freshness(config: dict[str, Any], data: dict[str, Any]) -> dict[str, str]:
    """Check whether the convenience report represents the current registry."""

    path = path_for(config, "supervisor_report")
    result = {"path": rel(path)}
    if not path.is_file():
        return {**result, "status": "missing", "reason": "report file does not exist"}
    expected = registry_revision(data)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {**result, "status": "unknown", "reason": f"report cannot be read: {exc}"}
    match = SUPERVISOR_REPORT_MARKER.match(text)
    if not match:
        return {**result, "status": "unknown", "reason": "report marker is missing or malformed"}
    if match.group(1) != expected:
        return {**result, "status": "stale", "reason": "registry revision differs from report marker"}
    return {**result, "status": "fresh", "revision": expected}


def build_supervisor_report(config: dict[str, Any], data: dict[str, Any], state: dict[str, Any], cycle_id: str, ingested: list[str], assignments: list[dict[str, str]], qa_result: dict[str, Any]) -> tuple[str, str]:
    counts: dict[str, int] = {}
    for item in data["items"].values():
        counts[item.get("status", "unknown")] = counts.get(item.get("status", "unknown"), 0) + 1
    next_action = choose_next_action(data, assignments, config, qa_result)
    gates, production_ready, durability = production_snapshot_details(config, data, state)
    qa_source = {"status": durability["qa_source_state"]}
    if durability.get("qa_source_reason"):
        qa_source["reason"] = durability["qa_source_reason"]
    if qa_result.get("status") == "pass" and qa_source["status"] in {"QA_STALE", "invalid"}:
        if next_action == "Run the production release gate and obtain explicit deploy approval.":
            next_action = "QA_STALE: run `relwit supervisor qa` before the production release gate."
    elif durability["status"] != "pass" and next_action == "Run the production release gate and obtain explicit deploy approval.":
        next_action = f"Release durability is {durability['status']}: {durability['reason']}."
    revision = registry_revision(data)
    lines = [
        f"<!-- relwit-report: registry_sha256={revision} -->",
        "# ReleaseWitness supervisor report",
        "",
        f"- **Cycle:** `{cycle_id}`",
        f"- **Generated:** {now_iso()}",
        f"- **Registry revision:** sha256:{revision}",
        f"- **Next action:** {next_action}",
        f"- **Production snapshot:** `{'ready' if production_ready else 'not_ready'}`",
        "",
        "## Status counts",
        "",
    ]
    lines.extend(f"- `{status}`: {count}" for status, count in sorted(counts.items()))
    lines.extend(["", "## Reports ingested this cycle", ""])
    lines.extend(f"- `{path}`" for path in ingested) or lines.append("- none")
    lines.extend(["", "## Assignments issued this cycle", ""])
    lines.extend(f"- `{entry['task_id']}` -> `{entry['agent']}` — `{entry['path']}`" for entry in assignments) or lines.append("- none")
    lines.extend(["", "## Worker reports awaiting review", ""])
    awaiting = [item for item in data["items"].values() if item.get("status") in {"reported", "needs_review"}]
    lines.extend(f"- `{item['id']}` — {item['title']} — reports: {', '.join(item.get('reports', [])) or 'none'}" for item in awaiting) or lines.append("- none")
    lines.extend(["", "## Completed tasks", ""])
    completed = [item for item in data["items"].values() if item.get("status") == "done"]
    lines.extend(f"- `{item['id']}` — {item['title']} — evidence: {len(item.get('evidence', []))}" for item in completed) or lines.append("- none")
    lines.extend(["", "## Blocked work", ""])
    blocked = [item for item in data["items"].values() if item.get("status") == "blocked"]
    lines.extend(f"- `{item['id']}` — {item['title']}" for item in blocked) or lines.append("- none")
    lines.extend([""])
    lines.extend(render_usage_section(load_telemetry_summary(config)))
    lines.extend(
        [
            "",
            "## QA",
            "",
            f"- status: `{qa_result.get('status')}`",
            f"- source_state: `{qa_source['status']}`",
            f"- source_reason: `{qa_source.get('reason', 'none')}`",
            f"- evidence: `{qa_result.get('evidence') or 'none'}`",
            "",
        ]
    )
    lines.extend(
        [
            "## Release source",
            "",
            f"- vcs: `{durability['vcs']}`",
            f"- head_sha: `{durability['head_sha']}`",
            f"- branch: `{durability['branch'] or 'none'}`",
            f"- source_fingerprint: `{durability['fingerprint']}`",
            f"- source_dirty_state: `{durability['dirty_state']}`",
            f"- source_dirty_path_count: `{durability['dirty_path_count']}`",
            f"- source_untracked_path_count: `{durability['untracked_path_count']}`",
            f"- qa_source_state: `{durability['qa_source_state']}`",
            f"- qa_source_fingerprint: `{durability['qa_source_fingerprint'] or 'none'}`",
            f"- qa_recorded_head_sha: `{durability['qa_recorded_head_sha'] or 'none'}`",
            f"- qa_config_fingerprint: `{durability['qa_config_fingerprint']}`",
            f"- local_durability: `{durability['status']}`",
            f"- durability_reason: `{durability['reason']}`",
            f"- upstream: `{durability['upstream'] or 'none'}`",
            f"- upstream_state: `{durability['upstream_state']}`",
            f"- upstream_relation: `{durability['upstream_relation']}`",
            f"- ahead: `{durability['ahead'] if durability['ahead'] is not None else 'unknown'}`",
            f"- behind: `{durability['behind'] if durability['behind'] is not None else 'unknown'}`",
            "",
        ]
    )
    lines.extend(["## Production gates", ""])
    for name, value in gates:
        lines.append(f"- [{'x' if value == 'pass' else ' '}] `{name}`: `{value}`")
    lines.extend(["", "## Resume instruction", "", f"{next_action}", ""])
    return "\n".join(lines), next_action


def write_checkpoint(config: dict[str, Any], name: str, status: str, summary: str, next_action: str, tasks: list[str], blockers: list[str], agent: str) -> Path:
    checkpoint_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-') or 'cycle'}"
    path = path_for(config, "checkpoints") / f"{checkpoint_id}.md"
    lines = [
        f"# Checkpoint: {name}",
        "",
        f"- **Created:** {now_iso()}",
        f"- **Status:** {status}",
        f"- **Agent:** {agent}",
        f"- **Next action:** {next_action}",
        "",
        "## Summary",
        "",
        summary,
        "",
        "## Tasks",
        "",
    ]
    lines.extend(f"- `{task}`" for task in tasks) or lines.append("- none")
    lines.extend(["", "## Blockers and risks", ""])
    lines.extend(f"- {blocker}" for blocker in blockers) or lines.append("- none")
    lines.extend(["", "## Resume instructions", "", next_action, ""])
    atomic_write(path, "\n".join(lines))
    return path


def cmd_checkpoint_create(args: argparse.Namespace) -> int:
    config = load_config()
    with state_lock():
        ensure_layout()
        path = write_checkpoint(config, args.name, args.status, args.summary, args.next_action, args.tasks or [], args.blockers or [], args.agent)
        state = load_supervisor_state(config)
        state["last_checkpoint"] = rel(path)
        save_supervisor_state(config, state)
    print(rel(path))
    return 0


def cmd_supervisor_report(args: argparse.Namespace) -> int:
    config = load_config()
    if args.check:
        data = load_registry()
        freshness = supervisor_report_freshness(config, data)
        print(freshness["path"])
        print(f"freshness={freshness['status']}")
        if freshness.get("reason"):
            print(f"reason={freshness['reason']}")
        return 0 if freshness["status"] == "fresh" else 1
    with state_lock():
        data = load_registry()
        state = load_supervisor_state(config)
        cycle_id = f"manual-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        report, _ = build_supervisor_report(config, data, state, cycle_id, [], [], state.get("last_qa") or {"status": "not_configured"})
        atomic_write(path_for(config, "supervisor_report"), report)
        atomic_write(path_for(config, "supervisor_cycle"), report)
    print(rel(path_for(config, "supervisor_report")))
    return 0


def cmd_supervisor_qa(_: argparse.Namespace) -> int:
    config = load_config()
    cycle_id = f"manual-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    result = run_qa(config, cycle_id)
    with state_lock():
        state = load_supervisor_state(config)
        state["last_qa"] = result
        state["last_qa_at"] = now_iso()
        save_supervisor_state(config, state)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["status"] in {"pass", "not_configured"} else 1


def evaluate_gate(config: dict[str, Any], state: dict[str, Any], require_clean: bool) -> dict[str, Any]:
    """Assurance-only release gate: is there a passing QA record for the current source?

    Independent of work items, roster and supervision state, so it can run as a
    CI step or a coding-agent hook (audit/redundancy-2026-09-30, sections 6.4-6.5).
    """

    last_qa = state.get("last_qa") if isinstance(state, dict) else None
    last_qa = last_qa if isinstance(last_qa, dict) else {}
    source = release_source_fingerprint(config)
    qa_source = validate_qa_source(config, {"last_qa": last_qa}, source)
    reasons: list[str] = []
    qa_status = gate_text(last_qa.get("status") or "missing")
    if qa_status != "pass":
        reasons.append(f"no passing QA record (last QA status: {qa_status}); run `relwit qa`")
    elif qa_source["status"] != "valid":
        reasons.append(f"{qa_source['status']}: {qa_source.get('reason', 'QA does not apply to the current source')}")
    result: dict[str, Any] = {
        "qa_status": qa_status,
        "qa_source_state": qa_source["status"],
        "qa_evidence": last_qa.get("evidence"),
        "qa_recorded_head_sha": last_qa.get("source_head_sha"),
        "source_fingerprint": source["fingerprint"],
        "source_head_sha": source["head_sha"],
        "source_dirty_state": source["dirty_state"],
    }
    if require_clean:
        durability = release_durability_snapshot(config, {"last_qa": last_qa}, source)
        result["durability"] = durability["status"]
        # A QA problem is already reported above; only add distinct durability failures.
        if durability["status"] != "pass" and durability["reason"] != f"QA source state is {qa_source['status']}":
            reasons.append(f"durability {durability['status']}: {durability['reason']}")
    result["reasons"] = reasons
    result["status"] = "pass" if not reasons else "fail"
    return result


def gate_text(value: Any) -> str:
    """Render untrusted state values on one line so they cannot forge gate output."""

    return re.sub(r"[\x00-\x1f\x7f]", "?", str(value))[:200]


def cmd_gate(args: argparse.Namespace) -> int:
    config = load_config()
    result = evaluate_gate(config, load_supervisor_state(config), args.require_clean)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        for key in ("qa_status", "qa_source_state", "source_head_sha", "source_dirty_state", "durability"):
            if key in result:
                print(f"{key}={gate_text(result[key])}")
        if result["status"] == "pass":
            print("GATE PASS")
        else:
            for reason in result["reasons"]:
                print(f"GATE FAIL: {gate_text(reason)}", file=sys.stderr)
    return 0 if result["status"] == "pass" else 1


def cmd_supervisor_cycle(args: argparse.Namespace) -> int:
    ensure_layout()
    config = load_config()
    cycle_id = f"cycle-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"
    cycle_started_at = now_iso()
    cycle_started_monotonic = time.monotonic()
    with state_lock():
        data = load_registry()
        state = load_supervisor_state(config)
        ingested = ingest_reports_locked(config, data, state)
        max_assignments = args.max_assignments or int(config["supervisor"].get("max_assignments_per_cycle", 4))
        assignments = dispatch_ready_locked(config, data, max_assignments, retry_blocked=args.retry_blocked)
        state["cycle"] = int(state.get("cycle", 0)) + 1
        state["last_cycle_id"] = cycle_id
        save_supervisor_state(config, state)
    should_run_qa = args.run_qa or bool(config["supervisor"].get("run_qa_each_cycle"))
    qa_result = run_qa(config, cycle_id) if should_run_qa else (load_supervisor_state(config).get("last_qa") or {"status": "not_run", "evidence": None})
    with state_lock():
        data = load_registry()
        state = load_supervisor_state(config)
        cycle_completed_at = now_iso()
        record_cycle_telemetry_locked(
            config,
            cycle_id,
            cycle_started_at,
            cycle_completed_at,
            max(0, round((time.monotonic() - cycle_started_monotonic) * 1000)),
        )
        state["last_qa"] = qa_result
        state["last_qa_at"] = now_iso()
        report, next_action = build_supervisor_report(config, data, state, cycle_id, ingested, assignments, qa_result)
        atomic_write(path_for(config, "supervisor_report"), report)
        atomic_write(path_for(config, "supervisor_cycle"), report)
        save_supervisor_state(config, state)
        checkpoint_status = "needs_input" if any(item.get("status") == "blocked" for item in data["items"].values()) else "active"
        checkpoint = write_checkpoint(config, cycle_id, checkpoint_status, f"Ingested {len(ingested)} reports, issued {len(assignments)} assignments, QA={qa_result.get('status')}.", next_action, [item["id"] for item in data["items"].values() if item.get("status") not in {"done", "cancelled"}], [item["id"] for item in data["items"].values() if item.get("status") == "blocked"], "supervisor")
        state["last_checkpoint"] = rel(checkpoint)
        save_supervisor_state(config, state)
    print(f"cycle={cycle_id}")
    print(f"report={rel(path_for(config, 'supervisor_report'))}")
    print(f"checkpoint={rel(checkpoint)}")
    print(f"next={next_action}")
    return 0


def cmd_telemetry_record(args: argparse.Namespace) -> int:
    config = load_config()
    for field in ("duration_ms", "execution_duration_ms"):
        value = getattr(args, field)
        if value is not None and value < 0:
            raise RelWitError(f"telemetry {field} must be non-negative")
    entity_id = safe_telemetry_metadata(args.entity_id)
    if entity_id is None:
        raise RelWitError("telemetry entity id must be a safe identifier")
    event_id = safe_telemetry_metadata(args.event_id) if args.event_id else telemetry_event_id(args.kind, entity_id)
    if event_id is None:
        raise RelWitError("telemetry event id must be a safe identifier")
    usage: dict[str, Any] | None = None
    if args.usage_json is not None:
        try:
            raw_usage = json.loads(args.usage_json)
        except json.JSONDecodeError:
            raw_usage = None
        usage = normalize_usage_envelope(raw_usage, source=args.usage_source)
    fields: dict[str, Any] = {
        "kind": args.kind,
        "entity_id": entity_id,
        "task_id": entity_id if args.kind == "task" else None,
        "outcome": args.outcome,
        "started_at": args.started_at,
        "completed_at": args.completed_at,
        "duration_ms": args.duration_ms,
        "duration_provenance": "measured" if args.duration_ms is not None else "unavailable",
        "execution_duration_ms": args.execution_duration_ms,
        "execution_duration_provenance": "measured" if args.execution_duration_ms is not None else "unavailable",
        "source": safe_telemetry_metadata(args.source) or "telemetry.cli",
        "retry": bool(args.retry),
        "takeover": bool(args.takeover),
    }
    if args.task_id:
        fields["task_ids"] = sorted(set(args.task_id))
    if args.agent:
        fields.update(_agent_telemetry_metadata({"id": args.agent, "role": args.role or "worker", "provider": args.provider, "runtime": args.runtime, "model": args.model}))
    else:
        for key, value in (("role", args.role), ("provider", args.provider), ("runtime", args.runtime), ("model", args.model)):
            safe_value = safe_telemetry_metadata(value)
            if safe_value is not None:
                fields[key] = safe_value
    if usage is not None:
        fields["usage"] = usage
    with state_lock():
        event = upsert_telemetry_event_locked(config, event_id, fields)
    print(f"telemetry event recorded: {event['event_id']} usage={event.get('usage', {}).get('provenance', 'unavailable')}")
    return 0


def cmd_telemetry_summary(_: argparse.Namespace) -> int:
    config = load_config()
    print("\n".join(render_usage_section(load_telemetry_summary(config))).rstrip())
    return 0


def parse_frontmatter_for_skill(path: Path) -> dict[str, str]:
    return parse_frontmatter(path)


def validate_registry(data: dict[str, Any], errors: list[str]) -> None:
    items = data.get("items")
    if not isinstance(items, dict):
        errors.append("registry.items must be an object")
        return
    for task_id, item in items.items():
        valid_task_id = bool(re.fullmatch(r"RW-\d{4,}", task_id))
        if not valid_task_id:
            errors.append(f"invalid task id: {task_id}")
        if not isinstance(item, dict):
            errors.append(f"{task_id} must be an object")
            continue
        for field in ("title", "level", "status", "owner", "scope", "depends_on", "acceptance", "files", "evidence", "reports"):
            if field not in item:
                errors.append(f"{task_id} missing {field}")
        if item.get("level") not in VALID_LEVELS:
            errors.append(f"{task_id} has invalid level {item.get('level')}")
        if item.get("status") not in VALID_STATUSES:
            errors.append(f"{task_id} has invalid status {item.get('status')}")
        scopes = item.get("scope")
        if not isinstance(scopes, list) or not scopes:
            errors.append(f"{task_id} needs a non-empty scope")
        else:
            for scope in scopes:
                if not isinstance(scope, str):
                    errors.append(f"{task_id} has a non-string scope")
                    continue
                try:
                    validate_relative_scope(scope)
                except RelWitError as exc:
                    errors.append(f"{task_id}: {exc}")
        if not isinstance(item.get("acceptance"), list) or not item.get("acceptance"):
            errors.append(f"{task_id} needs acceptance criteria")
        dependencies = item.get("depends_on")
        if not isinstance(dependencies, list):
            errors.append(f"{task_id} depends_on must be an array")
            dependencies = []
        for dependency in dependencies:
            if not isinstance(dependency, str):
                errors.append(f"{task_id} has a non-string dependency")
            elif dependency not in items:
                errors.append(f"{task_id} references missing dependency {dependency}")
        for field in ("files", "evidence", "reports"):
            if not isinstance(item.get(field), list):
                errors.append(f"{task_id} {field} must be an array")
        lineage_fields = ("supersedes", "superseded_by", "takeover_reason")
        for field in lineage_fields:
            if field not in item or item.get(field) is None:
                continue
            value = item.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{task_id} {field} must be a non-empty string or null")
            elif "\n" in value or "\r" in value:
                errors.append(f"{task_id} {field} must be a single line")
        if item.get("takeover_reason") is not None and not item.get("supersedes"):
            errors.append(f"{task_id} takeover_reason requires supersedes")
        if item.get("supersedes") and item.get("takeover_reason") is None:
            errors.append(f"{task_id} supersedes requires takeover_reason")
        evidence = item.get("evidence")
        if isinstance(evidence, list):
            for index, entry in enumerate(evidence):
                if not isinstance(entry, dict):
                    errors.append(f"{task_id} evidence[{index}] must be an object")
                    continue
                if "provenance" in entry:
                    if entry.get("provenance") is None:
                        errors.append(f"{task_id} evidence[{index}]: evidence provenance cannot be empty")
                    else:
                        try:
                            normalize_evidence_provenance(entry.get("provenance"))
                        except RelWitError as exc:
                            errors.append(f"{task_id} evidence[{index}]: {exc}")
                    if "source" not in entry:
                        errors.append(f"{task_id} evidence[{index}] with provenance needs source")
                elif "source" in entry:
                    errors.append(f"{task_id} evidence[{index}] with source needs provenance")
                if "source" in entry:
                    try:
                        normalize_evidence_source(entry.get("source"), "")
                    except RelWitError as exc:
                        errors.append(f"{task_id} evidence[{index}]: {exc}")
        if item.get("status") == "done" and not isinstance(evidence, list):
            errors.append(f"{task_id} evidence must be an array")
        elif item.get("status") == "done" and not evidence:
            errors.append(f"{task_id} is done without evidence")
        elif item.get("status") == "done" and not has_review_evidence(item):
            errors.append(f"{task_id} is done without non-empty review evidence")
        if item.get("status") == "assigned" and not item.get("assigned_to"):
            errors.append(f"{task_id} is assigned without assigned_to")
        if item.get("status") == "reported" and not item.get("reports"):
            errors.append(f"{task_id} is reported without a report path")
        if valid_task_id and not item_path(task_id).exists():
            errors.append(f"missing work item file: {rel(item_path(task_id))}")

    for task_id, item in items.items():
        if not isinstance(item, dict):
            continue
        supersedes = item.get("supersedes")
        if isinstance(supersedes, str) and supersedes.strip():
            if supersedes == task_id:
                errors.append(f"{task_id} cannot supersede itself")
            elif supersedes not in items:
                errors.append(f"{task_id} references missing superseded task {supersedes}")
            elif isinstance(items.get(supersedes), dict):
                predecessor = items[supersedes]
                if predecessor.get("status") not in {"blocked", "cancelled"}:
                    errors.append(f"{task_id} supersedes non-recoverable task {supersedes}")
                if predecessor.get("superseded_by") != task_id:
                    errors.append(f"{task_id} supersedes {supersedes} without reciprocal superseded_by")
        superseded_by = item.get("superseded_by")
        if isinstance(superseded_by, str) and superseded_by.strip():
            if superseded_by == task_id:
                errors.append(f"{task_id} cannot supersede itself via superseded_by")
            elif superseded_by not in items:
                errors.append(f"{task_id} references missing successor {superseded_by}")
            elif isinstance(items.get(superseded_by), dict):
                successor = items[superseded_by]
                if successor.get("supersedes") != task_id:
                    errors.append(f"{task_id} superseded_by {superseded_by} without reciprocal supersedes")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str) -> None:
        if task_id in visiting:
            errors.append(f"dependency cycle includes {task_id}")
            return
        if task_id in visited or task_id not in items:
            return
        visiting.add(task_id)
        dependencies = items[task_id].get("depends_on", [])
        if isinstance(dependencies, list):
            for dependency in dependencies:
                if isinstance(dependency, str):
                    visit(dependency)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in items:
        visit(task_id)

    active = [(task_id, item) for task_id, item in items.items() if item.get("status") in ACTIVE_WRITER_STATUSES]
    for index, (left_id, left) in enumerate(active):
        for right_id, right in active[index + 1 :]:
            left_scopes = left.get("scope", [])
            right_scopes = right.get("scope", [])
            if not isinstance(left_scopes, list) or not isinstance(right_scopes, list):
                continue
            if any(scope_overlaps(a, b) for a in left_scopes for b in right_scopes if isinstance(a, str) and isinstance(b, str)):
                errors.append(f"active writer scope conflict: {left_id} vs {right_id}")


def validate_config(config: dict[str, Any], errors: list[str]) -> None:
    paths_config = config.get("paths")
    if not isinstance(paths_config, dict):
        errors.append("config.paths must be an object")
    else:
        for key in DEFAULT_CONFIG["paths"]:
            value = paths_config.get(key)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"config.paths.{key} must be a non-empty string")
                continue
            try:
                safe_repo_path(value)
            except (TypeError, RelWitError) as exc:
                errors.append(f"config.paths.{key}: {exc}")
        evidence_value = paths_config.get("evidence")
        spool_value = paths_config.get("runtime_spool")
        if isinstance(evidence_value, str) and isinstance(spool_value, str):
            try:
                evidence_path = safe_repo_path(evidence_value)
                spool_path = safe_repo_path(spool_value)
                if (
                    spool_path == evidence_path
                    or spool_path.is_relative_to(evidence_path)
                    or evidence_path.is_relative_to(spool_path)
                ):
                    errors.append("config.paths.runtime_spool must not overlap config.paths.evidence")
            except (TypeError, RelWitError):
                pass

    release_source = config.get("release_source")
    if not isinstance(release_source, dict):
        errors.append("config.release_source must be an object")
        release_source = {}
    version = release_source.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        errors.append("config.release_source.version must be a positive integer")
    volatile_paths = release_source.get("volatile_paths")
    if not isinstance(volatile_paths, list) or any(
        not isinstance(value, str) or not value.strip() for value in volatile_paths
    ):
        errors.append("config.release_source.volatile_paths must be an array of non-empty strings")
    else:
        for value in volatile_paths:
            try:
                validate_relative_scope(value, "config.release_source.volatile_paths")
            except RelWitError as exc:
                errors.append(str(exc))
        if any(normalize_scope(value) == "." for value in volatile_paths):
            errors.append("config.release_source.volatile_paths must not contain the project root")
        paths_config = config.get("paths") if isinstance(config.get("paths"), dict) else {}
        spool_value = paths_config.get("runtime_spool", DEFAULT_CONFIG["paths"]["runtime_spool"])
        if isinstance(spool_value, str):
            spool_relative = normalize_scope(spool_value)
            declared = [normalize_scope(value) for value in volatile_paths]
            # Raw diagnostics outside declared volatile paths would either stale every QA
            # (they change the source manifest) or, if ignored, hide real source.
            if spool_relative == "." or not release_path_is_volatile(spool_relative, declared):
                errors.append("config.paths.runtime_spool must be inside config.release_source.volatile_paths")

    supervisor = config.get("supervisor")
    if not isinstance(supervisor, dict):
        errors.append("config.supervisor must be an object")
        supervisor = {}
    for key in ("max_assignments_per_cycle", "qa_timeout_seconds"):
        value = supervisor.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            errors.append(f"config.supervisor.{key} must be a positive integer")
    for key in ("run_qa_each_cycle", "auto_dispatch"):
        if not isinstance(supervisor.get(key), bool):
            errors.append(f"config.supervisor.{key} must be boolean")
    qa_commands = supervisor.get("qa_commands")
    if not isinstance(qa_commands, list):
        errors.append("config.supervisor.qa_commands must be an array of structured command objects")
    else:
        for index, entry in enumerate(qa_commands):
            try:
                normalize_qa_command(entry, index)
            except RelWitError as exc:
                errors.append(str(exc))
    for key in ("operational_readiness_files", "production_gates"):
        value = supervisor.get(key)
        if not isinstance(value, list) or any(not isinstance(entry, str) or not entry.strip() for entry in value):
            errors.append(f"config.supervisor.{key} must be an array of non-empty strings")
    if isinstance(supervisor.get("operational_readiness_files"), list):
        for value in supervisor["operational_readiness_files"]:
            try:
                safe_repo_path(value)
            except (TypeError, RelWitError) as exc:
                errors.append(f"config.supervisor.operational_readiness_files: {exc}")

    agents = config.get("agents")
    if not isinstance(agents, list):
        errors.append("config.agents must be an array")
        agents = []
    seen: set[str] = set()
    for agent in agents:
        if not isinstance(agent, dict):
            errors.append("config.agents entries must be objects")
            continue
        agent_id = agent.get("id")
        if not isinstance(agent_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,63}", agent_id) or agent_id in seen:
            errors.append(f"invalid or duplicate agent id: {agent_id}")
        if isinstance(agent_id, str):
            seen.add(agent_id)
        if agent.get("role") not in VALID_AGENT_ROLES:
            errors.append(f"invalid role for agent {agent_id}: {agent.get('role')}")
        if agent.get("status", "available") not in VALID_AGENT_STATUSES:
            errors.append(f"invalid status for agent {agent_id}")
        max_active = agent.get("max_active", 1)
        if isinstance(max_active, bool) or not isinstance(max_active, int) or max_active < 1:
            errors.append(f"invalid max_active for agent {agent_id}")
        scopes = agent.get("scope", [])
        if not isinstance(scopes, list):
            errors.append(f"scope must be an array for agent {agent_id}")
        else:
            for scope in scopes:
                if not isinstance(scope, str):
                    errors.append(f"non-string scope for agent {agent_id}")
                    continue
                try:
                    validate_relative_scope(scope, f"scope for agent {agent_id}")
                except RelWitError as exc:
                    errors.append(str(exc))
        capabilities = agent.get("capabilities", [])
        if not isinstance(capabilities, list) or any(
            not isinstance(capability, str) or not capability.strip() for capability in capabilities
        ):
            errors.append(f"capabilities must be an array of non-empty strings for agent {agent_id}")
        if "runner" in agent:
            try:
                runner_settings(agent)
            except RelWitError as exc:
                errors.append(str(exc))
        try:
            paths = agent_paths(config, agent)
        except (TypeError, ValueError, RelWitError) as exc:
            errors.append(str(exc))
            continue
        for key in ("inbox", "report", "completed", "inbox_dir"):
            if not paths[key].exists():
                errors.append(f"missing mailbox file/dir for {agent_id}: {rel(paths[key])}")


def cmd_validate(_: argparse.Namespace) -> int:
    errors: list[str] = []
    required_files = [
        ROOT / "AGENTS.md",
        ROOT / "knowledge" / "INDEX.md",
        ROOT / "knowledge" / "project-map.md",
        ROOT / "work" / "INDEX.md",
        ROOT / "work" / "registry.json",
        ROOT / "relwit.config.json",
        ROOT / "relwit" / "cli.py",
    ]
    for path in required_files:
        if not path.exists():
            errors.append(f"missing required file: {rel(path)}")
    config: dict[str, Any] = {}
    if CONFIG.exists():
        try:
            config = load_config()
            validate_config(config, errors)
        except RelWitError as exc:
            errors.append(str(exc))
    if REGISTRY.exists():
        try:
            validate_registry(load_registry(), errors)
        except RelWitError as exc:
            errors.append(str(exc))

    expected_skills = {"relwit", "relwit-orchestrator", "relwit-context", "relwit-worker", "relwit-review", "relwit-autopilot"}
    skills_root = ROOT / ".agents" / "skills"
    for skill_name in expected_skills:
        skill_dir = skills_root / skill_name
        path = skill_dir / "SKILL.md"
        if not path.exists():
            errors.append(f"missing skill: {rel(path)}")
            continue
        frontmatter = parse_frontmatter_for_skill(path)
        if frontmatter.get("name") != skill_name:
            errors.append(f"skill name mismatch in {rel(path)}")
        if not frontmatter.get("description") or "TODO" in frontmatter.get("description", ""):
            errors.append(f"skill description missing or unfinished in {rel(path)}")
        if "TODO" in path.read_text(encoding="utf-8"):
            errors.append(f"unfinished TODO in {rel(path)}")

    try:
        import tomllib
    except ModuleNotFoundError:
        tomllib = None
    for path in sorted((ROOT / ".codex" / "agents").glob("*.toml")):
        if tomllib is None:
            errors.append("Python 3.11+ is required to validate TOML custom agents")
            break
        try:
            agent = tomllib.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"invalid TOML {rel(path)}: {exc}")
            continue
        for field in ("name", "description", "developer_instructions"):
            if not agent.get(field):
                errors.append(f"custom agent {rel(path)} missing {field}")

    if errors:
        print("INVALID")
        for error in errors:
            print(f"- {error}")
        return 1
    print("VALID")
    return 0


def cmd_context(args: argparse.Namespace) -> int:
    config = load_config()
    data = load_registry()
    sections = []
    report_path = path_for(config, "supervisor_report")
    for path in (ROOT / "knowledge" / "INDEX.md", ROOT / "knowledge" / "project-brief.md", ROOT / "knowledge" / "project-map.md"):
        if path.exists():
            sections.append(f"## {rel(path)}\n{path.read_text(encoding='utf-8')}")
    if report_path.exists():
        freshness = supervisor_report_freshness(config, data)
        report_context = [f"## {rel(report_path)}", "", f"- **Freshness:** {freshness['status']}"]
        if freshness["status"] != "fresh":
            report_context.append("- **Warning:** This convenience view is not current; use the registry and task evidence as authority.")
        report_context.extend(["", report_path.read_text(encoding="utf-8")])
        sections.append("\n".join(report_context))
    active = [item for item in data["items"].values() if item.get("status") not in {"done", "cancelled"}]
    summary = [f"- {item['id']} [{item['status']}] {item['level']}: {item['title']} | scope={','.join(item.get('scope', []))}" for item in sorted(active, key=lambda value: value["id"])]
    sections.append("## active work\n" + ("\n".join(summary) if summary else "(none)"))
    if args.task_id:
        item = get_item(data, args.task_id)
        path = item_path(args.task_id)
        body = path.read_text(encoding="utf-8") if path.exists() else json.dumps(item, indent=2)
        sections.append(f"## task {args.task_id}\n{body}")
    print(clip("\n\n".join(sections), args.max_chars))
    return 0


def clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 80)] + "\n...[context clipped]...\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="relwit", description="ReleaseWitness (RelWit) supervisor control plane")
    parser.add_argument(
        "--root",
        dest="root",
        help="operate on this existing project root (default: source checkout or current directory when installed)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="create missing runtime directories and config")
    init.set_defaults(func=cmd_init)

    context = sub.add_parser("context", help="print bounded supervisor context")
    context.add_argument("--task", dest="task_id")
    context.add_argument("--max-chars", type=int, default=8000)
    context.set_defaults(func=cmd_context)

    qa_alias = sub.add_parser("qa", help="run configured QA and bind the result to the current source")
    qa_alias.set_defaults(func=cmd_supervisor_qa)

    gate = sub.add_parser(
        "gate",
        help="exit 0 only if a passing QA record matches the current source (CI step or agent hook)",
    )
    gate.add_argument("--require-clean", action="store_true", help="also require clean committed Git source")
    gate.add_argument("--json", action="store_true", help="print the gate result as JSON")
    gate.set_defaults(func=cmd_gate)

    validate = sub.add_parser("validate", help="validate project, skills, roster and registry")
    validate.set_defaults(func=cmd_validate)

    task = sub.add_parser("task", help="manage work items")
    task_sub = task.add_subparsers(dest="task_command", required=True)
    task_new = task_sub.add_parser("new", help="create planned task")
    task_new.add_argument("--title", required=True)
    task_new.add_argument("--objective")
    task_new.add_argument("--level", required=True, choices=sorted(VALID_LEVELS))
    task_new.add_argument("--owner", required=True)
    task_new.add_argument("--scope", action="append", required=True)
    task_new.add_argument("--acceptance", action="append", required=True)
    task_new.add_argument("--verification", action="append")
    task_new.add_argument("--preferred-agent", action="append")
    task_new.add_argument("--capability", action="append")
    task_new.add_argument("--depends-on", nargs="*", default=[])
    task_new.add_argument("--supersedes", help="blocked or cancelled predecessor task id")
    task_new.add_argument("--takeover-reason", help="single-line reason for taking over a predecessor")
    task_new.set_defaults(func=cmd_task_new)

    claim = task_sub.add_parser("claim", help="claim a task directly")
    claim.add_argument("task_id")
    claim.add_argument("--agent", required=True)
    claim.set_defaults(func=cmd_task_claim)

    update = task_sub.add_parser("update", help="change task status")
    update.add_argument("task_id")
    update.add_argument("--status", required=True, choices=sorted(VALID_STATUSES))
    update.add_argument("--agent")
    update.add_argument("--scope", dest="scopes", action="append")
    update.add_argument("--file", dest="files", action="append")
    update.add_argument("--note")
    update.set_defaults(func=cmd_task_update)

    evidence = task_sub.add_parser("evidence", help="append evidence")
    evidence.add_argument("task_id")
    evidence.add_argument("--kind", required=True)
    evidence.add_argument("--value", required=True)
    evidence.add_argument("--provenance", help="evidence provenance label")
    evidence.add_argument("--source", help="single-line command, URL or repository path anchor")
    evidence.add_argument("--agent", help="reviewer identity required for review evidence")
    evidence.set_defaults(func=cmd_task_evidence)

    report = task_sub.add_parser("report", help="write worker report and completed logs")
    report.add_argument("task_id")
    report.add_argument("--agent", required=True)
    report.add_argument("--result", choices=["completed", "blocked", "failed"], required=True)
    report.add_argument("--summary", required=True)
    report.add_argument("--next-action", required=True)
    report.add_argument("--file", dest="files", action="append")
    report.add_argument("--check", dest="checks", action="append")
    report.add_argument("--provenance", help="worker report provenance label")
    report.add_argument("--source", help="single-line command, URL or repository path anchor")
    report.add_argument("--blocker")
    report.set_defaults(func=cmd_task_report)

    task_list = task_sub.add_parser("list", help="list tasks")
    task_list.add_argument("--status", choices=sorted(VALID_STATUSES))
    task_list.set_defaults(func=cmd_task_list)

    show = task_sub.add_parser("show", help="show task JSON")
    show.add_argument("task_id")
    show.set_defaults(func=cmd_task_show)

    agent = sub.add_parser("agent", help="manage worker roster and mailboxes")
    agent_sub = agent.add_subparsers(dest="agent_command", required=True)
    register = agent_sub.add_parser("register", help="register worker and create mailbox")
    register.add_argument("--id", dest="agent_id", required=True)
    register.add_argument("--role", default="worker")
    register.add_argument("--directory")
    register.add_argument("--inbox-file")
    register.add_argument("--report-file")
    register.add_argument("--completed-file")
    register.add_argument("--scope", action="append")
    register.add_argument("--capability", action="append")
    register.add_argument("--max-active", type=int, default=1)
    register.add_argument(
        "--runner-arg",
        dest="runner_command",
        action="append",
        help="optional argv element for automatic worker execution; repeat and include {assignment_path}",
    )
    register.add_argument(
        "--runner-timeout",
        type=int,
        default=DEFAULT_RUNNER_TIMEOUT_SECONDS,
        help=f"optional runner timeout in seconds (1-{MAX_RUNNER_TIMEOUT_SECONDS})",
    )
    register.set_defaults(func=cmd_agent_register)
    agent_status_parser = agent_sub.add_parser("status", help="set worker availability")
    agent_status_parser.add_argument("agent_id")
    agent_status_parser.add_argument("--status", required=True, choices=sorted(VALID_AGENT_STATUSES))
    agent_status_parser.set_defaults(func=cmd_agent_status)
    agent_list = agent_sub.add_parser("list", help="list registered workers")
    agent_list.set_defaults(func=cmd_agent_list)

    worker = sub.add_parser("worker", help="worker mailbox operations")
    worker_sub = worker.add_subparsers(dest="worker_command", required=True)
    pull = worker_sub.add_parser("pull", help="pull oldest assigned task")
    pull.add_argument("--agent", required=True)
    pull.set_defaults(func=cmd_worker_pull)
    run = worker_sub.add_parser("run", help="pull and invoke one or more opt-in runner tasks")
    run.add_argument("--agent", required=True)
    run.add_argument("--max-tasks", type=int, default=1)
    run.add_argument("--wait-seconds", type=float, default=0)
    run.add_argument("--poll-seconds", type=float, default=2)
    run.set_defaults(func=cmd_worker_run)

    supervisor = sub.add_parser("supervisor", help="supervisor dispatch/report/QA cycle")
    supervisor_sub = supervisor.add_subparsers(dest="supervisor_command", required=True)
    dispatch = supervisor_sub.add_parser("dispatch", help="assign ready tasks to eligible workers")
    dispatch.add_argument("--max-assignments", type=int)
    dispatch.add_argument("--retry-blocked", action="store_true")
    dispatch.set_defaults(func=cmd_supervisor_dispatch)
    ingest = supervisor_sub.add_parser("ingest", help="ingest incoming worker reports")
    ingest.set_defaults(func=cmd_supervisor_ingest)
    supervisor_report = supervisor_sub.add_parser("report", help="regenerate user-facing report")
    supervisor_report.add_argument(
        "--check",
        action="store_true",
        help="check whether the existing convenience report matches the current registry",
    )
    supervisor_report.set_defaults(func=cmd_supervisor_report)
    qa = supervisor_sub.add_parser("qa", help="run configured QA commands")
    qa.set_defaults(func=cmd_supervisor_qa)
    cycle = supervisor_sub.add_parser("cycle", help="run one bounded supervisor cycle")
    cycle.add_argument("--max-assignments", type=int)
    cycle.add_argument("--retry-blocked", action="store_true")
    cycle.add_argument("--run-qa", action="store_true")
    cycle.set_defaults(func=cmd_supervisor_cycle)

    telemetry = sub.add_parser("telemetry", help="record or summarize privacy-safe execution telemetry")
    telemetry_sub = telemetry.add_subparsers(dest="telemetry_command", required=True)
    telemetry_record = telemetry_sub.add_parser("record", help="record one explicit machine-readable telemetry event")
    telemetry_record.add_argument("--kind", required=True, choices=sorted(TELEMETRY_KINDS))
    telemetry_record.add_argument("--id", dest="entity_id", required=True)
    telemetry_record.add_argument("--event-id")
    telemetry_record.add_argument("--agent")
    telemetry_record.add_argument("--role")
    telemetry_record.add_argument("--provider")
    telemetry_record.add_argument("--runtime")
    telemetry_record.add_argument("--model")
    telemetry_record.add_argument("--task-id", action="append")
    telemetry_record.add_argument("--outcome", choices=sorted(TELEMETRY_OUTCOMES), default="unknown")
    telemetry_record.add_argument("--started-at")
    telemetry_record.add_argument("--completed-at")
    telemetry_record.add_argument("--duration-ms", type=int)
    telemetry_record.add_argument("--execution-duration-ms", type=int)
    telemetry_record.add_argument("--retry", action="store_true")
    telemetry_record.add_argument("--takeover", action="store_true")
    telemetry_record.add_argument("--usage-json", help="complete JSON object with the relwit_usage marker")
    telemetry_record.add_argument("--usage-source", default="adapter")
    telemetry_record.add_argument("--source", default="telemetry.cli")
    telemetry_record.set_defaults(func=cmd_telemetry_record)
    telemetry_summary = telemetry_sub.add_parser("summary", help="print the concise owner-facing Usage summary")
    telemetry_summary.set_defaults(func=cmd_telemetry_summary)

    checkpoint = sub.add_parser("checkpoint", help="create resume checkpoint")
    checkpoint_sub = checkpoint.add_subparsers(dest="checkpoint_command", required=True)
    checkpoint_create = checkpoint_sub.add_parser("create", help="create checkpoint")
    checkpoint_create.add_argument("--name", required=True)
    checkpoint_create.add_argument("--status", required=True, choices=["active", "blocked", "complete", "needs_input"])
    checkpoint_create.add_argument("--summary", required=True)
    checkpoint_create.add_argument("--next-action", required=True)
    checkpoint_create.add_argument("--agent", default="supervisor")
    checkpoint_create.add_argument("--task", dest="tasks", action="append")
    checkpoint_create.add_argument("--blocker", dest="blockers", action="append")
    checkpoint_create.set_defaults(func=cmd_checkpoint_create)
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.root:
            configure_root(args.root)
        return int(args.func(args))
    except RelWitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
