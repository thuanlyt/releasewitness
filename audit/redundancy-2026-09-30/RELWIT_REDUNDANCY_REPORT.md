# Does ReleaseWitness still deserve to exist?

**Study:** independent redundancy validation of ReleaseWitness (RelWit) against current native
Claude Code. **Dates:** 2026-09-29/30. **Branch:** `claude/relwit-redundancy-validation-2026-09-30`.
**Starting source:** `main@e747b8811c235841e9be1b8a62770e430d0b141d` (v0.2.0).
**Runtime compared:** Claude Code CLI 2.1.284 (current in this environment), Opus 5.5 lead,
Sonnet 5.5 workers, both verified from the runtime's own `modelUsage` records.

Companion files: [`EXPERIMENT_MATRIX.md`](EXPERIMENT_MATRIX.md) (every run and result),
[`GUARANTEE_INVENTORY.md`](GUARANTEE_INVENTORY.md) (what RelWit enforces vs. what it only asks
for), [`REPRODUCE.md`](REPRODUCE.md) (commands and SHAs), `evidence/` (raw transcripts and
metrics).

---

## 1. Executive conclusion

**For most native Claude Code workflows, ReleaseWitness is redundant.** In 56 real sessions
across six test categories, full RelWit supervision (N2) never produced a more correct result
than native Claude Code (N0). It cost **1.9–4.8× more** per task (median ≈3×), took **1.8–5.6× longer**, created
coordination churn (46 work items, 12 cancelled, 2 blocked), and in one case recorded
**committed, working code as a failed attempt to retry**. The old UseAgent-style orchestration
layer should not remain part of the product.

**The assurance-only mode (N1) was not measurably better than N0 either.** Every release decision
in every mode was correct. Native Opus, without being told to, wrote QA records bound to a commit
SHA and tree hash and used them correctly. The one property RelWit adds that native Claude does
not have, *QA verdicts computed by a tool rather than by the model's diligence*, is real. But
it is:

- reproducible with a ~30-line Git hook (which scored 9/11 on the drift battery vs. RelWit's 8/11);
- **not enforceable** in v0.2.0: no command exits non-zero on `QA_STALE`;
- coupled to the work ledger;
- defective in a way that made QA self-invalidate when its own evidence was committed.

**What survives:** at most a *thin, provider-neutral, source-bound evidence gate*. That is a
small tool that records "this check passed for these exact bytes" and fails a CI step or agent
hook when the bytes change. Its justification is portability across runtimes and
machines and determinism independent of model diligence. It is **not** better outcomes on
this study's tasks. If the owner does not value that portability, the evidence supports
archiving ReleaseWitness and publishing the ~30-line hook as a recipe.

**Recommended architecture: OPTION A.** Claude Code (or Codex, etc.) plans and executes;
ReleaseWitness, if kept, only verifies source-bound evidence. Option B (RelWit orchestrates) was
tested as N2 and measured worse on every cost axis with no correctness benefit.

## 2. UseAgent vs. ReleaseWitness scope

| | UseAgent (v0.1.0) | ReleaseWitness (v0.2.0 claim) |
| --- | --- | --- |
| Identity | "file-first multi-agent control plane for coordinating coding agents" (`CHANGELOG.md` 0.1.0) | "source-bound evidence and release assurance for AI-written code" (`README.md`) |
| Primary loop | goal → DAG → dispatch → mailbox pull → report → review → QA → checkpoint | implement → report evidence → review → source-bound QA → Git durability gate |
| Orchestration | the product | "optional lightweight supervision", "not the product's primary identity" |
| Code reality | — | one 4,433-line `relwit/cli.py`; the supervision/mailbox/runner/telemetry machinery is still most of it, and `validate` still requires all six supervision skills and `AGENTS.md` in any assured project |

v0.2.0 was a rename (`CHANGELOG.md`: "no orchestration feature milestone"). The UseAgent
capabilities are all still present: DAG, dispatch, mailboxes, outbox prompts, runner bridge,
supervisor cycles, checkpoints, telemetry and knowledge-ledger skills. They are now labeled
optional. The only capabilities built specifically for assurance are the source-fingerprint
QA binding, the Git durability snapshot, bounded and redacted evidence, and typed provenance
labels. These were added in UA-0048–0055 after OSBlog, per `docs/case-study-osblog.md`.

**Which guarantees are deterministic:** see
[`GUARANTEE_INVENTORY.md`](GUARANTEE_INVENTORY.md).

- **Tool-enforced:** the registry state machine, overlapping-claim rejection, declared-file
  scope, path escape, the QA fingerprint computation, output redaction and report freshness.
- **Computed but unenforced:** QA staleness and production readiness. Both exist only as
  Markdown.
- **Convention only:** actual write scope, reviewer identity and independence, review
  substance, provenance truthfulness, and whether agents use the CLI at all.

## 3. Current Claude Code overlap (primary sources)

Verified against `code.claude.com/docs` pages fetched 2026-09-29 (sub-agents, hooks, agent-teams)
and exercised in the runs where possible.

| RelWit capability | Native Claude Code 2.1.284 | Exercised here | Class |
| --- | --- | --- | --- |
| Planning / DAG creation | Lead model plans; plan mode; native task list | yes (leads planned; task list unused) | **A. Native duplicate** |
| Task dispatch / agent selection | `Agent` tool, `.claude/agents/*.md` | yes | **A** |
| Model routing | subagent `model:` field (tool-enforced; verified via `modelUsage`) | yes, all runs | **A** |
| Subagent orchestration / parallel execution | subagents, background subagents, `SendMessage` | yes | **A** |
| Worktree isolation | `isolation: worktree` (auto-cleaned if unchanged) | yes (forced variant: 3 parallel worktrees, clean merge, cheapest T5 run) | **A** (RelWit explicitly does not own it; its scope rule is coarser than Git merge) |
| Agent teams / shared task list | experimental; file-locked task claims | **BLOCKED** (not spawned in `-p` mode) | A (per docs) |
| Handoff / mailbox / outbox prompts | spawn prompts, `SendMessage`, cross-session messaging | yes | **D. Legacy overhead** |
| Persistent task state (work ledger) | `~/.claude/tasks/` (machine-local) + Git | yes | **D** (see T6 clone findings) |
| Interruption recovery (same machine) | Git working tree; `--resume` transcripts (30-day retention) | yes (7 resumes) | **A** |
| Cross-session recovery | fresh session reading Git; `--resume` | yes | **A** |
| Cross-machine attempt history | Git commit trailers only | yes (6 clone audits) | **B. Complementary** *only if* the ledger is committed; convention-dependent and inconsistent |
| Cross-provider recovery | none native | **BLOCKED** (no second provider) | B (unproven) |
| Independent review | reviewer subagent (`model: opus`, read-only tools) | yes | **A** (value came from the native reviewer in every mode) |
| Review evidence as gate input | none native | yes | **B**: RelWit requires a non-empty string from a self-declared role, which proves nothing (probe P4) |
| Evidence provenance labels | none native | yes | **B**, weak: labels are asserted, not verified |
| Evidence freshness / source-bound QA | none native (docs confirm no SHA binding) | yes | **C. Unique but trivially reproducible**: a 30-line hook matches it |
| Stale-QA detection | none native as a primitive; Opus did it by convention | yes | **C**, same caveat, and unenforced in v0.2.0 |
| Git/source identity, dirty-tree handling, release durability | `git` itself; hooks can enforce | yes | **B/C**: a useful packaging of Git checks |
| Release gate | hooks: `PreToolUse`, `Stop`, `TaskCompleted` exit 2 = block (docs) | yes | **C in intent, not delivered**: RelWit's gate cannot block anything |
| Bounded/sanitized evidence + local spool | none native | code/tests only | **B** (but the spool is not ignored in external projects) |
| Fallback attribution | transcripts record exact model per session and subagent | yes | **A** on the same machine; lost cross-machine in all modes |
| Knowledge ledger (`knowledge/` cards) | `CLAUDE.md`/`AGENTS.md`, memory | yes (N2 wrote cards/ADRs) | **D**: never changed a decision or recovery outcome |
| Checkpoints | Git commits; transcripts | yes | **D**: no recovery used them over Git state |
| Supervisor judgment contract | model behavior | yes | **D** |
| Usage telemetry | `--output-format json` `modelUsage`, `subagent_stats` | yes | **A**: native is authoritative; RelWit's is "unavailable" unless an adapter supplies it |
| Provider neutrality | Claude-only | n/a | **B**: the one structural argument left for RelWit |
| Auditability | transcripts (local, complete) + Git | yes | **A** same machine; **B** cross-machine only when the ledger is committed |

## 4. Methodology

**Fixture.** `fixture/ledgerlite/` is a new, neutral, dependency-free Python invoice library with
a written `SPEC.md` and 11 visible tests. It was written for this study so that no mode had
prior exposure. `harness/seed_defects.py` injects 7 spec violations (D1–D7) that the visible
tests do not catch.

**Hidden acceptance.** Held-out suites (`fixture-hidden/t1`, `t2`, `t5`) were kept outside every
workspace during all runs and validated against reference solutions before any run: clean passes
T2; seeded fails exactly D1–D7; references pass T1/T5. A transcript scan found no access to the
hidden tests, the reference solutions or the RelWit repository from any run.

**Modes.** All modes got an identical `.claude/agents` runtime profile (Sonnet `implementer`,
Opus `reviewer`), an identical task body and identical constraints (commit after
implementation and after review fixes; end with READY/NOT READY). Only a 5–12 line
mode-activation block differed (`prompts/mode-n0.md`, `mode-n1.md`, `mode-n2.md`).
- **N0:** native only; strongest reasonable use (Opus lead, Sonnet workers, Opus reviewer,
  parallelism and worktrees offered).
- **N1:** N0 plus RelWit for evidence only: one work item as the evidence container, review
  evidence, `supervisor qa`, and a gate read.
- **N2:** the `$relwit` supervisor workflow with the full control plane copied into the project
  as `docs/getting-started.md` prescribes.

**Execution.** Every run is a real headless Claude Code session (`claude -p --model opus
--output-format stream-json`) in its own fresh Git workspace built from the identical baseline.
Model identity, tokens and cost are taken only from the runtime's `result` records. Failures
were induced only with real mechanisms: `SIGKILL` of every process in the workspace (T3) and
real `--max-turns` terminations (T6). No provider outage was simulated.

**Tests.** T1 normal multi-file feature; T2 seeded audit→fix→independent re-review; T3 hard
kill at two points, then a fresh session (no conversation) or native `--resume`; T4 QA at
source A → teammate regression commit → release decision at B (plus an 11-scenario
deterministic matrix); T5 four concurrent tasks with a real file overlap (plus a deterministic
scope probe and a forced native-worktree variant); T6 worker failure → takeover by a
different model → independent audit, same machine and fresh clone.

## 5. Results

Full per-run numbers: [`EXPERIMENT_MATRIX.md`](EXPERIMENT_MATRIX.md). Totals: 56 sessions, ≈$48.8
estimated.

| Mode | Workspaces | Cost (est.) | Wall | Top-level tool calls | Final hidden-test failures | False "READY" |
| --- | --- | --- | --- | --- | --- | --- |
| N0 | 9 | $9.38 | 2,488 s | 231 | 0 | 0 |
| N1 | 8 | $10.75 | 3,078 s | 268 | 0 | 0 |
| N2 | 8 | $25.74 | 6,428 s | 686 | 0 | 0 |

**T1 normal implementation.** All modes 8/8. N1 was the cheapest ($0.95), N0 $1.10, and N2
$3.82 with 105 tool calls, 11 subagents, 7 work items and 71 coordination files. RelWit
improved nothing; N2 added ceremony.

**T2 audit → fix → independent re-review.** In all three modes the Sonnet worker's own first
commit already fixed all 7 seeded defects (11/11 hidden). In all three modes the Opus reviewer
then found *additional* real defects (CLI crashes on huge amounts or deeply nested JSON,
half-even rounding in CSV cells), which were fixed before READY. "Completed ≠ verified" held in
every mode because of the **native reviewer subagent**. RelWit's review-evidence state changed
no decision. N2 cost $5.44 vs. $1.14/$1.15.

**T3 interruption / resume.** 7 resumes: late kill (implementation committed, review
pending) × N0-fresh, N0-`--resume`, N1, N2; early kill (uncommitted mid-edit) × N0, N1, N2.
**All 7 recovered accurately.** Each correctly separated committed, uncommitted, planned and
lost work. Nothing was lost, no incorrect assumption was acted on, and no human intervention was
needed beyond the resume prompt. N2's persisted DAG ("RW-0002/0003 planned, never dispatched") was
used correctly, but the Git working tree carried the same information for N0/N1. Resume cost:
N0 $0.83–0.85, N0 native `--resume` $0.65, N1 $0.97–1.24, N2 $2.46–3.06. The N1 ledger refused
a second implementer report for a post-review fix, so that commit sits outside the ledger.

**T4 stale QA / source drift.**
- *Agent level:* **6/6 decisions correct (NOT READY)** across modes.
  - N0: the native Opus QA phase, unprompted, wrote a record bound to commit SHA and tree
    hash, including an exact `git diff <sha> HEAD -- . ':!qa-records'` validity recipe. Both
    N0 deciders used it and answered in 23 s / 92 s for $0.15–0.19, without re-running the
    8-minute soak.
  - N1: both deciders read RelWit's `QA_STALE` and answered in 30–61 s for $0.24–0.35.
  - N2: $0.98 each and 32–35 commands of ledger wrestling; one of the two never consulted the
    gate.
- *QA recording cost:* N0 ran full QA once ($0.24, 510 s). N1 invoked it 4 times ($0.65,
  1,059 s) and N2 6 times ($1.00). Part of this is RelWit's self-invalidation defect
  (§6.2).
- *Deterministic matrix (11 drift scenarios):* the RelWit fingerprint was right in 8, the
  30-line native hook in 9.
  - Both miss a Git-ignored file that is real source (S9).
  - Both are conservatively wrong on "QA'd dirty, then committed unchanged" (S11).
  - RelWit is additionally wrong on a message-only amend (S7) and on committing only its own
    evidence (probe).

**T5 concurrent work.**
- All modes 5/5. The N0/N1 leads partitioned by file ownership on their own: the overlapping
  X+W (`export.py`) and dependent Z went to one worker, with "ONLY touch …" boundaries in the
  spawn prompts. That is the one-writer-per-scope rule, applied by judgment.
- No lead chose worktree isolation unprompted. In the forced variant (`evidence/t5w-summary.txt`),
  three Sonnet workers ran in native `isolation: worktree` in parallel. X and W both edited
  `export.py`, and Git's three-way merge combined them with no conflict. The result was 5/5 for
  $1.09 in 167 s, the cheapest and fastest T5 run. RelWit's file-level one-writer-per-scope rule
  would have **serialized** X and W.
- N2 built a dependency DAG, then cancelled and recreated 3 items without dependencies to get
  parallelism (4 of 10 items cancelled), at $2.86 vs. $1.24/$1.56.
- Deterministic probe: RelWit rejects an overlapping *claim*, but it **accepts an undeclared
  out-of-scope write** and lets a self-declared `reviewer` close the task with "looks fine".
  RelWit's scope control prevents nothing that Git plus native partitioning does not; it is a
  ledger check on honest declarations.

**T6 failure / fallback / handoff.**
- *T6 (`--max-turns 6`):* N0 and N1 workers finished within 6 turns, so **no failure was
  induced**, and I record that rather than claiming one. N2's worker committed complete,
  correct code (8/8) and then hit the turn limit before `task report`. RelWit auto-filed
  `result: failed`, `files: []`, `failure_class: runtime_error`, `disposition: retry`,
  `attempts: 0`, with next action "create a scoped debug task". This is a **false failure**:
  obeying it would redo finished work. The takeover then tried RelWit's own lineage feature
  (`task new --supersedes RW-0001`), which was **rejected** because the auto-failed task is
  `reported`, not `blocked`/`cancelled`.
- *T6b (`--max-turns 3`, the same budget for every mode):* all three failed for real.
  - The N0 worker had the **entire implementation written but uncommitted**; a blind
    `git checkout`/retry would have destroyed it.
  - The N1 and N2 workers had **nothing**: the turns went to claim/skill/assignment protocol.
  - All takeovers finished at 8/8.
- *Audits, same machine:* all 6 were accurate on who, which model, what was completed and what
  was safe to retry. Their primary source was **native transcripts**
  (`~/.claude/projects/<slug>/…/subagents/*.meta.json`); the auditors even caught a false
  premise in my own takeover prompt and RelWit's misclassification.
- *Audits, fresh clone (no transcripts):*
  - N0: most answers UNKNOWN (honestly marked), and one **attribution loss**: Sonnet's
    uncommitted work was later committed under an Opus trailer.
  - N1: the committed ledger was **self-contradictory** ("attempts 1, takeover false").
  - N2: t6-n2's ledger was never committed, so the registry was empty. In **t6b-n2 the ledger
    was committed and the auditor reconstructed the failure and takeover history** that N0
    could not. That is the only case in the study where RelWit uniquely helped, and it
    depended on the supervisor choosing to commit `work/` (1 of 2 N2 runs).

## 6. Falsification attempts and failure cases

1. **"Stale QA needs RelWit."** Falsified as a capability claim.
   - A ~30-line native hook (`harness/native_qa_stamp.sh`) matched or beat the fingerprint (9/11
     vs. 8/11).
   - Native Opus spontaneously produced SHA-bound QA records.
   - 6/6 decisions were correct in every mode.
   - What remains true: RelWit computes the verdict without relying on a model remembering to
     do so.
2. **Fingerprint self-invalidation (defect).** `release_source_fingerprint` hashes `head_sha` and
   `dirty_paths` even though volatile paths are excluded from the manifest. Committing only
   `work/` evidence (0 source files) flips QA to `QA_STALE` (`evidence/t4-ledger-commit-probe.txt`),
   and so does a message-only amend (S7). This drove repeated 8-minute QA runs in N1 (4×).
3. **"Scope control prevents concurrent-writer failures."** Falsified. It rejects overlapping
   claims only; undeclared writes pass (probe P2). Native leads avoided conflicts by
   partitioning.
4. **Gate enforceability.** With `QA_STALE`:
   - `supervisor report`, `supervisor report --check` and `context` all exit 0.
   - `validate` exits 1 only because assurance-only projects lack the supervision control plane.
   - Nothing can block a release.
5. **Assurance coupled to orchestration.**
   - `all_tasks_done` is `manual` for an empty registry.
   - The readiness files default to RelWit's own docs.
   - Review evidence needs a work item.
   - `validate` requires six supervision skills, `AGENTS.md` and a vendored `relwit/cli.py`.
6. **Spool privacy claim.** In an external project, `init` writes no ignore rule, so raw
   diagnostics in `work/.runtime-output/` are one `git add -A` away from being committed.
7. **Runner false failure and unusable lineage** (T6-n2, above).
8. **Identity and review substance.** `--agent reviewer` is self-asserted; "looks fine" satisfies the
   review gate (probe P4).
9. **Protocol overhead under a budget.** Under an identical 3-turn budget, N0 produced a complete
   implementation while N1/N2 produced nothing (T6b).
10. **Case against native (fairly stated).**
    - Native attempt history is machine-local: transcripts are deleted after 30 days and are not
      in the repo.
    - A fresh clone loses who-did-what, and Git trailers can misattribute uncommitted work.
    - Native source-bound QA exists only when the model chooses to write it (1/1 QA phase here).
    - Native offers no provider-neutral record.
    These are the real gaps, but in this study none of them led to a wrong decision.

## 7. Answers

**A. Is RelWit redundant for a normal single-session Claude Code workflow?** Yes. No test showed a
correctness, recovery or decision benefit; every RelWit mode cost more.

**B. Is it redundant when Claude Code natively uses Opus as architect/reviewer and Sonnet as
workers?** Yes, for everything orchestration-related. Native routing via `.claude/agents` `model:`
was verified from `modelUsage` in every run. N2 re-implements planning, dispatch, handoff and
review routing at 1.9–4.8× the cost, with churn and one misleading failure record.

**C. Does N1 provide reproducible value over N0?** Not in outcomes: 0 prevented wrong decisions,
0 correctness gains, and equal recovery fidelity. It does provide a deterministic, model-independent
QA↔source binding, but in v0.2.0 that binding is unenforced, coupled to the work ledger, and
self-invalidating on evidence commits, and a 30-line hook reproduces it. Value is conditional on
the owner prizing provider-neutral, tool-computed evidence over model convention.

**D. Does N2 provide reproducible value over N1?** No. The single positive (cross-machine attempt
history in t6b-n2) did not reproduce in t6-n2, and every other N2 difference was cost, time,
churn or a new failure mode.

**E. Component disposition** (evidence per row in §3 and §6):

| Component | Disposition |
| --- | --- |
| Source fingerprint + QA binding (`release_source_fingerprint`, `validate_qa_source`) | **Core**, but fix it to be content-based |
| Git durability snapshot | **Core** (small) |
| A non-zero-exit gate usable from CI and agent hooks | **Core**; missing in v0.2.0 |
| Bounded/redacted evidence summaries + spool | **Core**, and ignore the spool in external projects |
| Typed provenance labels | **Optional** (cheap; honest labeling, not verification) |
| Review-evidence gate as implemented (any string, self-declared role) | **Deprecate**: gives false confidence; a gate should bind a review *artifact* to a source fingerprint |
| Work registry / lifecycle state machine | **Deprecate** from the assurance path; optional at most |
| DAG, dispatch, mailboxes, outbox prompts, `worker pull/run`, runner bridge, supervisor cycle, autopilot | **Deprecate → delete** (Class D; native duplicates; measured overhead and a false-failure mode) |
| Knowledge ledger skills, checkpoints, supervisor judgment contract | **Delete from product** (use `CLAUDE.md`/`AGENTS.md` and Git) |
| Usage telemetry | **Delete** (native `modelUsage` is authoritative; RelWit's is empty unless an adapter supplies it) |
| `.codex/agents` role profiles, model routing | **Move outside the product boundary** (runtime adapters/profiles) |
| `validate`'s requirement for skills/`AGENTS.md` | **Remove** from any assurance-only path |

**F. Would expanding model routing / orchestration make RelWit UseAgent again?** Yes. It would
also make it worse than the runtime it wraps. N2 *is* that system today, and it lost on every
cost axis. Model routing belongs to the runtime; the study verified it works natively and
deterministically. **Do not hard-code `supervisor = opus` / `worker = sonnet` in RelWit.**

## 8. Recommended product boundary

**OPTION A.** Claude Code / Codex / etc. plan, route and execute. ReleaseWitness, if kept, is only:

```text
relwit qa     → run configured checks; record PASS/FAIL bound to a content fingerprint of the source
relwit gate   → exit 0 only if a passing record matches the current source (optionally: clean Git)
               usable as a CI step, a Claude Code PreToolUse/Stop/TaskCompleted hook, or a Codex hook
```

It has no work ledger, roster, mailbox, DAG, runner, telemetry or knowledge skills in that
path.

Why Option A:

- **N2 (Option B) was measured worse.** It was 1.9–4.8× the cost, ran slower, created churn and a
  false-failure mode, and gave no correctness gain.
- **The only unique property is verification.** It is independent of who planned or executed.
- **Native hooks make enforcement possible.** Hooks can deterministically block on exit code 2,
  so a thin gate becomes enforceable instead of advisory.

**Whether even Option A deserves a product is the owner's call.** The honest ceiling of its value
is portability (same gate for Claude, Codex, CI and humans) and determinism (independent of model
diligence). If those are not worth maintaining a package for, archive ReleaseWitness and publish
`harness/native_qa_stamp.sh` as a recipe.

## 9. WHAT WOULD MAKE RELWIT UNNECESSARY?

Any one of these removes the remaining reason to exist:

1. The coding runtime records test/review results bound to a content or tree hash and exposes a
   blocking check. Today a ~30-line `PreToolUse` hook already does this for Claude Code.
2. CI already runs the full QA on the exact release commit and branch protection requires it
   (the common case for teams with CI). "Stale QA" then cannot reach a release.
3. The team uses a single runtime (Claude Code only) and does not need provider-neutral evidence.
4. Durable attempt history is not needed off-machine, or the runtime's transcripts are shipped to
   shared storage.
5. The owner does not want to maintain a package whose value, per this study, is a
   determinism/portability property rather than a measured outcome improvement.

For the workflows exercised here, conditions 1, 3 and 5 already hold.

## 10. WHAT DID RELWIT PROVE THAT NATIVE CLAUDE COULD NOT?

**No strong evidence.** Specifically:

- It prevented **zero** wrong release decisions (6/6 correct in every mode).
- It caused **zero** correctness gains (all modes reached identical hidden scores).
- It changed **zero** recovery outcomes (7/7 accurate).

The weak, conditional positives were:

- **Deterministic verdicts.** QA staleness was computed by a tool, not by model diligence (N1
  deciders read `QA_STALE` directly). Native reached the same verdict via a model-written SHA
  record, and a 30-line hook reproduces it.
- **One cross-machine reconstruction.** In one of two N2 runs (t6b-n2) a committed ledger let a
  fresh-clone auditor reconstruct failure/takeover history that N0 lost. It did not reproduce
  in the other N2 run, and the N1 committed ledgers were self-contradictory.

On the other side, RelWit also produced evidence that was *worse* than native: a false failure
record, a contradictory attempt count, self-invalidated QA and a gate that cannot block.

## 11. Is an update justified?

Only narrowly, and every change below points to a finding.

| Change (implemented on this branch) | Finding |
| --- | --- |
| Content-based fingerprint: `head_sha` and `dirty_paths` stay as recorded metadata but no longer invalidate QA | §6.2 (evidence-commit probe, S7, S11; N1 ran QA 4×) |
| `relwit gate` command: exit 0/1 on source-bound QA (+ optional `--require-clean`), independent of the work ledger | §6.4, §6.5 (no enforceable gate; ledger coupling) |
| `init` writes `work/.gitignore` for the runtime spool | §6.6 |
| README/architecture positioning: native orchestration first; supervision deprecated, pending owner decision | §5, §7 |

**Not implemented (owner decision required):** deleting the supervision/mailbox/runner/telemetry
machinery and the `validate` control-plane requirement. That is a breaking change, and the
deprecation recommendation above is the evidence for it. Also not implemented: fixes to the
runner false-failure classification and `--supersedes` from `reported` (§6.7). Those sit in
machinery recommended for deletion, so they are recorded as known defects instead of being
invested in.

## 12. Threats to validity

- **Sample size.** One run per cell for most tests (two decisions per mode in T4). The
  direction of cost/overhead effects was consistent across all 25 comparable workspaces;
  correctness was at ceiling everywhere, so the study cannot resolve small correctness effects.
- **Task scale.** The fixture is small (≈200 lines). Multi-day, many-contributor programs, where
  a durable ledger could matter more, were not tested. OSBlog (93 items) is prior evidence of
  both the value and the overhead (F-01 vs. F-07).
- **Headless mode.** Agent teams could not be exercised (`-p`). Interactive usage may differ.
- **Harness artifacts.**
  - A hidden CLI test initially rejected `SystemExit(2)` (a valid "exit 2"). It was corrected
    and every run was re-scored with the final scorer.
  - The git-ignored `.relwit-mode` marker (content `n0`/`n1`/`n2`) was readable, and one N0
    decider printed it.
  - My T6 takeover prompt asserted "did not finish" even when the worker had finished (the
    audits caught this).
- **Cost** figures are Claude Code client-side estimates. Killed sessions report none.
- **Same model family in every role.** Cross-provider claims are **BLOCKED**, not refuted.
