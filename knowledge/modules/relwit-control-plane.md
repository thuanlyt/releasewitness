# Module card: ReleaseWitness control plane

- `freshness`: verified (2026-09-06)
- `owner`: orchestrator
- `source_anchor`: `relwit/cli.py:default_root`, `relwit/cli.py:configure_root`, `relwit/cli.py:validate_relative_scope`, `relwit/cli.py:scope_overlaps`, `relwit/cli.py:scope_within`, `relwit/cli.py:append_markdown`, `relwit/cli.py:agent_claim_blocker`, `relwit/cli.py:cmd_task_new`, `relwit/cli.py:cmd_task_claim`, `relwit/cli.py:cmd_task_update`, `relwit/cli.py:cmd_task_report`, `relwit/cli.py:cmd_worker_pull`, `relwit/cli.py:ingest_reports_locked`, `relwit/cli.py:choose_next_action`, `relwit/cli.py:normalize_evidence_provenance`, `relwit/cli.py:normalize_evidence_source`, `relwit/cli.py:parse_evidence`, `relwit/cli.py:registry_revision`, `relwit/cli.py:release_source_fingerprint`, `relwit/cli.py:git_upstream_snapshot`, `relwit/cli.py:release_durability_snapshot`, `relwit/cli.py:validate_qa_source`, `relwit/cli.py:supervisor_report_freshness`, `relwit/cli.py:build_supervisor_report`, `relwit/cli.py:cmd_supervisor_report`, `relwit/cli.py:cmd_context`, `relwit/cli.py:validate_registry`, `relwit/cli.py:ensure_layout`, `relwit/cli.py:main`, `relwit/cli.py:production_snapshot_details`, `relwit/cli.py:production_snapshot`, `relwit/cli.py:evaluate_gate`, `relwit/cli.py:cmd_gate`, `relwit/cli.py:run_qa`, `relwit/cli.py:runner_settings`, `relwit/cli.py:runner_preflight_settings`, `relwit/cli.py:static_runner_readiness`, `relwit/cli.py:probe_runtime_readiness`, `relwit/cli.py:classify_runner_failure`, `relwit/cli.py:record_runtime_event`

## Responsibility

Create and transition work items, serialize state changes, print bounded context snapshots, create checkpoints and validate the repository coordination layer. It does not modify application code.

## Entry points

- `relwit context`
- `relwit --root <project-root> context`
- `relwit context` after `python -m pip install --no-deps .`
- `relwit task new|claim|update|evidence|report|list|show`
- `relwit agent register|status|list`
- `relwit worker pull --agent <id>`
- `relwit worker run --agent <id> [--max-tasks N]`
- `relwit supervisor dispatch|ingest|report|qa|cycle`
- `relwit telemetry record|summary`
- `relwit checkpoint create`
- `relwit validate`
- `relwit qa` and `relwit gate [--require-clean] [--json]` (assurance-only; no registry required)
- `python examples/multi-runtime-conformance/run_conformance.py`

## Public interfaces / contracts

See `knowledge/contracts/work-registry.md` and `knowledge/contracts/supervisor-protocol.md`. State lives in `work/registry.json`; item Markdown lives in `work/items/`. Repeated `--scope` options are preserved for a task, task scope can be extended through `task update`, takeover lineage is written by `task new --supersedes ... --takeover-reason ...`, configured QA commands use explicit structured `argv` or trusted-local `shell` objects, and production readiness files are repository-safe. A central checkout may pass `--root <project-root>` before the subcommand; all runtime globals and configured paths are rebound to that existing directory, and escape paths are rejected. Direct claims and pulls share dispatcher eligibility checks for availability, capacity, scope and capabilities.

Usage telemetry anchors: `relwit/cli.py:normalize_usage_envelope`,
`relwit/cli.py:upsert_telemetry_event_locked`,
`relwit/cli.py:aggregate_telemetry` and
`relwit/cli.py:render_usage_section`. The event store is
`work/telemetry/events.json`; see `knowledge/contracts/usage-telemetry.md`.

The package entry point is `relwit.cli:main`; an installed CLI uses the
current working directory when the package is outside a prepared source
checkout. Markdown append operations preserve block separation without adding
blank lines at end of file.

Supervisor next-action selection prioritizes `blocked`, failed QA, failed
reports, `reported` work and then `needs_review` work before planned work or a
new task. This keeps a review gate from being hidden by unrelated planning.

Generated supervisor reports carry a deterministic SHA-256 revision of the
registry snapshot. `supervisor report --check` returns `fresh` only when that
marker matches the current registry; missing or malformed markers are
`unknown`, and stale/missing reports are never treated as current. Context
includes the report freshness label and warning. The registry and task evidence
remain authoritative.

Runner and QA output is runtime data first. Future writes keep only bounded
sanitized summaries in `work/evidence/`; bounded redacted diagnostics go to
the Git-ignored `work/.runtime-output/` spool. Historical tracked evidence is
preserved and requires an explicit migration policy before cleanup. Successful
QA binds its pass to a deterministic release-source fingerprint over source
content and QA configuration (not HEAD or dirty paths, which are recorded as
metadata); stale or missing fingerprints fail the production snapshot until QA
is rerun. Volatile
control-plane roots are configured explicitly in `release_source.volatile_paths`
and excluded from the source manifest. The production snapshot separately
 evaluates `release_source_durability`: Git `HEAD`, clean nonvolatile tracked
 state, no non-ignored untracked release source and valid current QA are
 required for `pass`. QA can remain valid on a dirty development tree while
 durability fails. Branch/upstream/ahead-behind metadata is read locally and
never requires `origin/main`, a network push or a merge; non-Git projects are
explicitly `filesystem`/`manual` degraded mode.
The generated root convenience report `work/SUPERVISOR_REPORT.md` is also
configured as volatile so report refreshes cannot self-invalidate QA.

QA defaults to structured argv execution with `shell=False`. A shell command
requires an explicit `mode: "shell"` object and is a trusted-local capability;
legacy command strings are rejected rather than heuristically parsed. QA
evidence records the execution mode, while argv, mode, timeout and related
configuration changes are already covered by the existing QA configuration
fingerprint.

Execution telemetry is provider-neutral and metadata-only. Lifecycle hooks write
idempotent task/cycle events under the ignored `work/telemetry/` path. An
explicit `relwit_usage: 1` JSON envelope is the only token-usage boundary;
missing or prose-only usage is unavailable. Wall duration and runner runtime
remain separate, and supervisor reports render only a concise Usage summary.
See `knowledge/contracts/usage-telemetry.md`.

## Dependency edges

Consumes `AGENTS.md`, `knowledge/`, `work/` and `relwit.config.json`; is used by `$relwit` and all ReleaseWitness skills/custom agents.

## Invariants

Lock only the short state transition. Do not hold the lock while doing exploration, implementation or tests. Reject claim bypasses from `assigned` to `in_progress`, unavailable or over-capacity direct claims/pulls, scope/capability-ineligible claims, claims or lifecycle reopening of superseded predecessors, unactivated worker reports, unauthorized administrative transitions, report-less `reported` transitions, review of active unreported work, lifecycle updates to terminal states, invalid roster roles, review-only agents claiming/reporting implementation work, overlapping active writer scopes, unsafe/out-of-scope recorded files, review actions from non-review roles and `done` without non-empty review evidence. Keep the selected project root explicit, validate malformed config/registry shapes without traceback, ignore unreadable/escaping reports safely and reject configured paths outside it.

Automatic dispatch must not assign a malformed or statically unavailable
configured runner. A pre-start readiness failure preserves `assigned` state,
records only bounded sanitized runtime metadata and a local spool reference,
and supplies a finite retry/reassign/takeover/needs-input disposition. No
provider prose is trusted as quota or auth proof, and no automatic successor
or infinite retry is created.

## Verification

`python -m unittest discover -s tests -v`, `relwit validate`, `python examples/multi-runtime-conformance/run_conformance.py`, explicit-root CLI tests, package metadata/wheel smoke test, configured supervisor QA and a temp-roster supervisor cycle.

## Operator onboarding

`init` creates the empty registry and required mailbox/report scaffolding
without copying maintainer history. `docs/getting-started.md` is the public hands-on guide. It defines the portable
runtime boundary: Codex, Claude Code and Google Antigravity are execution
surfaces, while `supervisor`, `worker`, `reviewer` and the other names are
workflow roles. It also documents the shared-folder default, the worktree
ledger caveat, unique runtime ids, generated outbox prompts and the exact
pull/report cycle.

## Optional execution bridge

`agent register --runner-arg ...` can persist an argv-only adapter for a real
worker runtime. Automatic dispatch performs static runner and executable
checks; malformed or unavailable configured runners are skipped, while an
agent without a runner remains the manual path. A runner may add an argv-only
`preflight` command with a bounded timeout. `worker run` probes it before
`assigned` becomes `in_progress` and accepts only the machine-readable states
`ready`, `unavailable`, `misconfigured`, `no_target` and `unknown`. No
preflight keeps the legacy compatibility path as `unknown`.

The runner can return a complete `relwit_runtime_result` JSON envelope for
normalized failure classes. Quota and auth are accepted only when the adapter
marks them authoritative; prose is not parsed as proof. Readiness failure
history stays attached to the assigned task with a bounded disposition and
local spool reference. Started-runner failures still use the no-report
auto-failure safeguard. The adapter remains provider-specific and trusted;
ReleaseWitness does not promise to sandbox an external model process.

## Known gaps

Scopes use explicit repository-relative path/subtree semantics; arbitrary glob
patterns are intentionally not interpreted. Git worktree orchestration remains
a Codex/product operation rather than a hidden action of this CLI. Vendor
launch flags and hosted-runtime authentication remain outside the portable core
and belong in a project-owned adapter.
