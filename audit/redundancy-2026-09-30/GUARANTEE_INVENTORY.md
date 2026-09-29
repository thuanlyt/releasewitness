# ReleaseWitness guarantee inventory (v0.2.0, `e747b88`)

Which RelWit guarantees are enforced by code, and which depend on agents following
the workflow. Anchors are `relwit/cli.py` functions at `e747b88`. "Probe" rows were
re-checked empirically in this study (see `evidence/`).

## A. Tool-enforced (deterministic, inside the CLI)

| Guarantee | Anchor | Verified here |
| --- | --- | --- |
| Registry lifecycle: `assigned -> in_progress` only by claim/pull; `reported` only by `task report`; `done`/`cancelled` terminal | `cmd_task_claim`, `cmd_task_update`, `cmd_task_report` | existing unit tests (133 pass) |
| `needs_review`/`done` require a registered review-capable role and a non-empty review string | `review_agent`, `has_review_evidence` | probe P4 (`evidence/t5-deterministic-scope-probe.txt`) |
| Overlapping active *claims* are rejected (exact path or parent/child subtree) | `active_scope_conflict`, `agent_claim_blocker` | probe P1 |
| *Declared* report files must lie inside the task scope | `cmd_task_report` | probe P3 |
| Config/state paths cannot escape `--root` | `safe_repo_path`, `configure_root` | unit tests |
| QA pass is bound to a source fingerprint (HEAD, tracked+untracked content, dirty paths, QA config) and later compared | `release_source_fingerprint`, `validate_qa_source` | `evidence/t4-deterministic-matrix.json` |
| Release durability (Git HEAD, clean nonvolatile tree, no untracked source, QA valid) is computed separately from task completion | `release_durability_snapshot` | T4 matrix |
| Runner/QA output is bounded and pattern-redacted before it is written to `work/evidence/` | `bound_runtime_output`, `redact_runtime_text` | unit tests |
| Convenience report carries a registry hash; `supervisor report --check` detects a stale report | `supervisor_report_freshness` | unit tests |
| A started runner that exits without a report produces an automatic failed report | `auto_report_runner_failure` | TEST 6 (N2) |
| Takeover lineage: successor may only supersede a blocked/cancelled predecessor | `cmd_task_new` | unit tests |

## B. Computed but NOT enforced anywhere

| Property | Why it is not enforced | Verified here |
| --- | --- | --- |
| "QA is stale" / "release durability fails" | Exists only as Markdown lines in `work/SUPERVISOR_REPORT.md`; `supervisor report`, `supervisor report --check` and `context` all exit 0 with `QA_STALE` | T4 probe (this file, section D) |
| Production readiness | `production_snapshot` is rendered, never returned as an exit status; no `gate`/`release check` command exists | code search: no caller maps it to an exit code |

## C. Convention / prompting only

| Claimed property | What actually holds | Verified here |
| --- | --- | --- |
| One writer per scope | Only *claims* and *declared* files are checked. An undeclared out-of-scope write is accepted and the task can reach `done` | probe P2 |
| Reviewer independence | `--agent reviewer` is self-declared; the worker can record its own review and close the task | probe P4 |
| Review substance | Any non-empty string satisfies the gate ("looks fine" passed) | probe P4 |
| Evidence provenance truthfulness | `local`/`live`/`simulation` labels are validated as an enum, not verified | code: `normalize_evidence_provenance` |
| Agents use the CLI at all | Nothing prevents editing without a claim or releasing without reading the gate | all agent runs |
| Knowledge ledger freshness, checkpoint content, DAG quality, supervisor judgment | Skill/contract text | n/a |
| "Raw diagnostics stay local" | True in the RelWit repo (its `.gitignore` has `/work/`); in an external project `init` writes no ignore rule, so `work/.runtime-output/` is untracked and `git add -A` commits it | dry-run `dry-n1` (`git status` shows `?? work/.runtime-output/`) |

## D. Coupling that affects assurance-only use

- `production_snapshot_details` returns `all_tasks_done = manual` for an empty registry, so a
  project that uses only QA/durability can never reach `ready` without adopting the work ledger.
- `operational_readiness_files` defaults to RelWit's own `docs/operations.md` and
  `docs/autopilot.md`; in another project the gate is `manual` until reconfigured.
- Review evidence can only be attached to a work item (`task evidence <id>`), so recording a
  review requires creating, claiming and reporting a work item.
- `validate` requires `AGENTS.md`, `knowledge/INDEX.md`, `knowledge/project-map.md`, six
  supervision skills and a vendored `relwit/cli.py` in the target project. An assurance-only
  project is always `INVALID`.
