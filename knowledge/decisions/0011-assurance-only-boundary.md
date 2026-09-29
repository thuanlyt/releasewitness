# ADR-0011: Assurance-only product boundary (proposed)

- `Status`: proposed — the deletion part requires an explicit owner decision
- `Date`: 2026-09-30
- `Owner`: supervisor (recommendation); project owner (decision)

## Context

The 2026-09-29/30 redundancy study
(`audit/redundancy-2026-09-30/RELWIT_REDUNDANCY_REPORT.md`) compared native Claude Code
(N0), native plus RelWit assurance commands (N1) and full RelWit supervision (N2) in 56 real
sessions (Opus 5.5 lead, Sonnet 5.5 workers, models verified from `modelUsage`), plus
deterministic probes.

- Correctness and recovery were identical across modes: every final hidden acceptance suite
  passed, and there were 0 false READY decisions, 7/7 accurate resumes and 6/6 correct
  stale-QA decisions.
- N2 cost 1.9–4.8× more and took 1.8–5.6× longer. It created 46 work items (12 cancelled,
  2 blocked) and recorded committed, working code as a failed attempt to retry.
- The only RelWit property native Claude lacks is a tool-computed, source-bound QA verdict.
  A ~30-line Git hook reproduced it (9/11 drift scenarios vs. RelWit's 8/11). In v0.2.0 the
  verdict was unenforceable (no non-zero exit), coupled to the work ledger, and invalidated by
  committing its own evidence.

## Decision

1. The product boundary is **Option A**: coding runtimes plan, route models and execute;
   ReleaseWitness only records and checks source-bound evidence (`relwit qa`, `relwit gate`).
2. The QA source fingerprint is content-based (`algorithm: content-v2`). HEAD and dirty state
   are recorded provenance, not identity.
3. `relwit gate` is the enforceable, ledger-independent entry point for CI steps and agent
   hooks.
4. The UseAgent-era supervision layer is **deprecated**. This covers the DAG, dispatch,
   mailboxes, outbox prompts, the runner bridge, supervisor cycles, autopilot, checkpoints,
   knowledge-ledger skills, usage telemetry and role profiles. It stays in place, unchanged,
   until the owner decides to delete it or to spin it out of the product.
5. Model routing (`supervisor = opus`, `worker = sonnet`) must not be hard-coded into
   RelWit. It belongs to runtime profiles such as `.claude/agents/*.md`.

## Consequences

- QA fingerprints recorded by v0.2.0 read as `QA_STALE` once and need one QA rerun.
- Assurance-only projects no longer need work items, a roster or supervision skills to gate
  a release.
- `validate` still requires the supervision control plane. Removing that requirement belongs
  with the owner's deletion decision.
- Known defects in deprecated machinery are recorded, not fixed:
  - the runner auto-failure classifies committed work as `retry`;
  - `--supersedes` rejects an auto-failed task in `reported`;
  - the ledger refuses a second implementer report after review fixes.
- If the owner does not value provider-neutral, tool-computed evidence enough to maintain a
  package, the study supports archiving ReleaseWitness and publishing
  `audit/redundancy-2026-09-30/harness/native_qa_stamp.sh` as a recipe.
