# Work registry contract

`work/registry.json` is the machine-readable source of truth for coordination state. A human-readable `work/items/<id>.md` must exist for every registry entry.

## Required item fields

`id`, `title`, `level`, `status`, `owner`, `assigned_to`, `scope`, `depends_on`, `acceptance`, `files`, `evidence`, `reports`, `attempts`, `created_at`, `updated_at`.

New task items may also carry `supersedes`, `superseded_by` and
`takeover_reason`. These optional fields are omitted by legacy items, but a
CLI-created item always initializes them so a takeover is explicit.

## Evidence provenance

New evidence entries written by the CLI include `kind`, `value`, a controlled
`provenance`, a single-line `source` anchor and `recorded_at`. The supported
provenance values are `local` (verified against the selected checkout or a
local command), `live` (observed on a named deployed endpoint),
`simulation` (replay/mock/generated demonstration), `blocked` (an attempted
check could not be completed) and `operator-confirmed` (a human-confirmed
external fact). `legacy` is reserved for older Markdown reports that predate
the field and is never a claim that the evidence is verified.

Provenance is a typed label, not authentication or review approval. A `live`
label still needs a repeatable source and review evidence. Runner and QA
evidence must contain bounded sanitized summaries plus a repository-relative
local spool reference; raw process output belongs in the ignored runtime spool,
not in committable evidence. Existing evidence without the optional fields
remains readable and valid for backward compatibility; malformed new fields
are validation errors. Existing tracked evidence is historical data and is not
rewritten or deleted by this contract.

Runtime readiness events may be recorded against an `assigned` task before a
worker takes ownership. They contain only normalized state/classification,
bounded reason metadata and a `work/.runtime-output/` reference; they do not
change the task to `in_progress` and do not count as a worker report. A
configured runner's static readiness states are `ready`, `unavailable`,
`misconfigured`, `no_target` and `unknown`. A preflight adapter must use the
explicit JSON envelope defined by the supervisor protocol. Failure classes are
provider-neutral; `quota_limited` and `auth_error` require authoritative
machine-readable adapter evidence, while ambiguous text is not trusted.

A successful QA record must also carry the release-source fingerprint, source
VCS/HEAD and dirty-state provenance, QA/release configuration fingerprint and
executed checks; a missing or mismatched source fingerprint is `QA_STALE` and
cannot satisfy a release gate. The fingerprint (`algorithm: content-v2`) hashes
the release-source content manifest (bytes, executable bit, symlink target;
deleted files are absent) and the QA/release configuration only;
HEAD and dirty state are recorded metadata, not identity. Fingerprints recorded
by the earlier HEAD-bound algorithm never match and therefore read as
`QA_STALE` until QA is rerun.

`relwit qa` (alias of `supervisor qa`) records QA for the current source and
`relwit gate` evaluates it without any work item, roster or supervision state:
exit 0 only when the last QA passed and its fingerprint matches the current
source, exit 1 with a `GATE FAIL:` reason otherwise; configuration or state
errors (for example a Git repository without commits or a malformed state file)
exit 2 with `error:`. `--require-clean` also requires Git release durability.
The gate trusts the local QA record in `work/supervisor/state.json`, consistent
with the trusted-local model: anyone who can write that file can forge a pass,
so CI must run `relwit qa` in the same job before `relwit gate`. The gate is intended as a CI step or a
coding-agent hook (for Claude Code, a `PreToolUse` hook that maps a non-zero
exit to exit 2 blocks the guarded command).

Task `done` does not imply Git durability. A strong local release gate requires
a concrete Git `HEAD`, clean release-relevant tracked state, no non-ignored
untracked release-source files and a QA fingerprint valid for the current
source identity. QA may be valid on a dirty development tree while release
durability fails. A commit that changes release-source content after QA
requires a new QA run; a commit that only adds volatile evidence, or a
message-only amend, keeps QA valid because the verified bytes are unchanged
(audit/redundancy-2026-09-30, report section 6.2). Volatile control-plane paths configured in
`release_source.volatile_paths` remain exempt and historical evidence is not
deleted or rewritten. Upstream branch relation is observational metadata; no
specific remote, push, pull or `origin/main` equality is required. Non-Git
workspaces use an explicit degraded/manual result rather than claiming Git
durability. The generated `work/SUPERVISOR_REPORT.md` is volatile control-plane
output and is not part of the durable release-source identity.

## Invariants

- `id` matches `RW-####` and is unique.
- `level` is one of `L0` through `L4`.
- `status` is a known lifecycle state.
- `scope` and `acceptance` are non-empty arrays.
- Dependencies reference existing items and are acyclic.
- Active writer scopes do not overlap exact paths or parent/child subtrees.
- Recorded task files must be repository-relative and inside the task scope;
  malformed array fields are validation errors, not validator crashes.
- `done` requires non-empty review evidence with a repeatable command/result.
- `assigned` requires an agent mailbox and an assignment Markdown file.
- `reported` requires a worker report path from an already `in_progress` task; it
  is not equivalent to reviewed `done`.
- `reported` is written by `task report`; `task update --status reported` is
  rejected so a report-less completion cannot enter the registry.
- `needs_review` requires the task to already be `reported`; active workers
  cannot be reviewed or closed before their report is recorded.
- `done` and `cancelled` are terminal states; lifecycle updates cannot reopen or
  move them to another status.
- A takeover may reference only a `blocked` or `cancelled` predecessor. The
  successor's `supersedes` and predecessor's `superseded_by` must point to each
  other, and `takeover_reason` must be non-empty and single-line.
- A task with `superseded_by` is preserved as failure history and cannot be
  claimed or moved back into the active lifecycle. Takeover creation never
  reopens or silently mutates the predecessor's status, reports or evidence.
- Review evidence and `needs_review`/`done` transitions require a registered
  `supervisor`, `reviewer` or `release_gate` identity; the assigned worker may
  not self-approve or self-close the task.
- Registered roles are limited to `supervisor`, `explorer`, `planner`, `worker`,
  `reviewer` and `release_gate`. Claim/report writers must use a non-review
  role; reviewers and release gates cannot claim implementation work.
- `assigned -> in_progress` is claim-only. `planned`, `blocked` and `cancelled`
  administrative updates require a registered review-capable identity; workers
  use `task report --result blocked` for a blocked handover.
- Direct `task claim` and `worker pull` must use an available claim-capable agent
  below `max_active` whose scope and capabilities satisfy the task; rejection
  occurs before registry mutation.
- Automatic dispatch skips a configured runner that is malformed or whose
  executable is unavailable. An agent without a runner remains a valid manual
  runtime, and an existing runner without preflight retains an explicit
  `unknown` compatibility path.
- A readiness failure before pull preserves `assigned` ownership and records a
  bounded runtime event with a retry/reassign/takeover/needs-input disposition;
  it must not create an unowned `in_progress` task, an automatic successor or
  an implicit infinite retry.
- Updates are serialized by `relwit/cli.py`; consumers must tolerate `updated_at` changing after every transition.
