# Experiment matrix — RelWit redundancy validation (2026-09-29/30)

Every row is a real run. Model identity, token and cost figures come only from Claude Code's own
`--output-format stream-json` `result` records (`modelUsage`); cost is the runtime's client-side
estimate. Killed sessions emit no result record, so their cost is excluded (marked "partial").
Hidden scores come from held-out acceptance tests that no run could see, re-scored with one
final scorer (`harness/build_matrix.py` → `evidence/run_metrics.json`, `evidence/run_metrics.md`).

Environment: Claude Code CLI 2.1.284, headless `claude -p`; lead `--model opus` → served
`claude-opus-5-5`; `.claude/agents/implementer.md` `model: sonnet` → served `claude-sonnet-5-5`;
`.claude/agents/reviewer.md` `model: opus`. Identical runtime profile in every mode. Starting
RelWit source: `main@e747b8811c235841e9be1b8a62770e430d0b141d`.

Modes: **N0** native Claude Code only · **N1** native + RelWit assurance commands only ·
**N2** full RelWit supervision (`$relwit` skill, DAG, dispatch, pull/report, review, QA, gate).

## Agent runs

| Test | Mode | Result (hidden) | Worker commit → final | Wall s | Cost $ | Tool calls | Subagents | RelWit items (cancelled) | Coord. files | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| T1 normal | N0 | PASS | 8/8 → 8/8 | 407 | 1.10 | 23 | 2 | – | 0 | reviewer found CSV rounding nit |
| T1 normal | N1 | PASS | 8/8 → 8/8 | 196 | 0.95 | 21 | 2 | 1 (0) | 18 | |
| T1 normal | N2 | PASS | 4/8 → 8/8 | 1013 | 3.82 | 105 | 11 | 7 (1) | 71 | first commit is DAG step 1 of 2 |
| T2 audit→fix→review | N0 | PASS | 11/11 → 11/11 | 269 | 1.14 | 29 | 2 | – | 0 | review found 2 extra real defects |
| T2 audit→fix→review | N1 | PASS | 11/11 → 11/11 | 235 | 1.15 | 29 | 2 | 1 (0) | 18 | review found 2 extra real defects |
| T2 audit→fix→review | N2 | PASS | 11/11 → 11/11 | 1477 | 5.44 | 130 | 10 | 11 (4) | 81 | review found extra real defects |
| T3 late kill → fresh | N0 | PASS, accurate recovery | 8/8 → 8/8 | 149* | 0.83* | 27 | 1 | – | 0 | redid only review |
| T3 late kill → `--resume` | N0 | PASS | 8/8 → 8/8 | 156* | 0.65* | 25 | 2 | – | 0 | native resume |
| T3 late kill → fresh | N1 | PASS, accurate | 8/8 → 8/8 | 268* | 0.97* | 33 | 2 | 1 (0) | 18 | ledger refused 2nd report |
| T3 late kill → fresh | N2 | PASS, accurate | 4/8 → 8/8 | 841* | 2.46* | 87 | 4 | 5 (1, +1 blocked) | 59 | used persisted DAG |
| T3 early kill → fresh | N0 | PASS, accurate | kill: uncommitted → 8/8 | 173* | 0.85* | 27 | 2 | – | 0 | kept uncommitted work |
| T3 early kill → fresh | N1 | PASS, accurate | kill: uncommitted → 8/8 | 267* | 1.24* | 40 | 3 | 1 (0) | 18 | |
| T3 early kill → fresh | N2 | PASS, accurate | kill: 4/8 uncommitted → 8/8 | 710* | 3.06* | 95 | 6 | 6 (2, +1 blocked) | 68 | used persisted DAG |
| T4 record QA at A | N0 | SHA+tree-bound record (unprompted) | – | 510 | 0.24 | 7 | 0 | – | 0 | 1 full QA run |
| T4 record QA at A | N1 | RelWit QA evidence | – | 1059 | 0.65 | 21 | 1 | 1 | 20 | 4 full-QA invocations |
| T4 record QA at A | N2 | RelWit QA evidence | – | 665 | 1.00 | 30 | 2 | 1 | 39 | 6 full-QA invocations |
| T4 decide at B (×2) | N0 | 2/2 correct NOT READY | – | 23 / 92 | 0.15 / 0.19 | 3 / 4 | 0 | – | – | manual SHA diff, no soak |
| T4 decide at B (×2) | N1 | 2/2 correct NOT READY | – | 30 / 61 | 0.24 / 0.35 | 5 / 11 | 0 / 1 | – | – | read RelWit gate (`QA_STALE`) |
| T4 decide at B (×2) | N2 | 2/2 correct NOT READY | – | 173 / 176 | 0.98 / 0.98 | 35 / 34 | 2 / 2 | +3 items | – | 1 of 2 read the gate |
| T5 concurrent | N0 | PASS | 5/5 → 5/5 | 235 | 1.24 | 31 | 3 | – | 0 | file-disjoint split, no worktrees |
| T5 concurrent | N1 | PASS | 5/5 → 5/5 | 252 | 1.56 | 41 | 3 | 1 | 18 | same split |
| T5 concurrent | N2 | PASS | 5/5 → 5/5 | 469 | 2.86 | 83 | 8 | 10 (4) | 77 | DAG recreated without deps |
| T5 forced worktrees | N0 | PASS | 3 parallel worktrees → 3 merges → 5/5 | 167 | 1.09 | 29 | 4 | – | 0 | X and W both edited `export.py`; Git merged cleanly; RelWit scope would have serialized them |
| T6 fail (max-turns 6) | N0 | no failure induced; takeover PASS | 8/8 → 8/8 | 323 | 1.78 | 35 | 2 | – | 0 | worker finished in 6 turns |
| T6 fail (max-turns 6) | N1 | no failure induced; PASS | 8/8 → 8/8 | 400 | 2.19 | 43 | 2 | 1 | 18 | |
| T6 fail (max-turns 6) | N2 | **false failure**; PASS | 8/8 → 8/8 | 593 | 3.36 | 75 | 4 | 3 | 63 | ledger: failed/retry for committed work |
| T6b fail (max-turns 3) | N0 | real failure; PASS | A: complete but uncommitted → 8/8 | 266 | 1.54 | 27 | 1 | – | 0 | worker did the work |
| T6b fail (max-turns 3) | N1 | real failure; PASS | A: nothing → 8/8 | 401 | 2.05 | 40 | 2 | 1 | 18 | turns spent on claim |
| T6b fail (max-turns 3) | N2 | real failure; PASS | A: nothing → 8/8 | 660 | 3.73 | 81 | 4 | 3 | 57 | turns spent on protocol |

`*` T3 wall/cost cover the resume session only (killed sessions report none). Per-session detail
for T3/T6 (phases A, B, C, C-clone) is in `evidence/run_metrics.json`.

## Audits (TEST 6 phase C) — "what happened, who did it, what is safe to retry"

| Run | Same machine (repo + native transcripts) | Fresh `git clone` only (cross-machine) |
| --- | --- | --- |
| t6-n0 | accurate: models per session/subagent, order, commits | most answers UNKNOWN (honest); model only from commit trailers |
| t6-n1 | accurate; detected false takeover premise | ledger self-contradictory ("attempts 1, takeover false") |
| t6-n2 | accurate; detected RelWit false-failure classification and rejected `--supersedes` | ledger never committed → empty registry; only commit messages/trailers |
| t6b-n0 | accurate | attribution **lost**: Sonnet's uncommitted work committed under an Opus trailer |
| t6b-n1 | accurate | ledger conflates failed and successful attempt under one id |
| t6b-n2 | accurate | **ledger committed → failure + takeover history reconstructed**; models still UNKNOWN |

## Deterministic tests (no model involved)

| Test | Artifact | Result |
| --- | --- | --- |
| T4 drift matrix, 11 scenarios | `evidence/t4-deterministic-matrix.json` | RelWit fingerprint 8/11 correct; ~30-line native hook 9/11 |
| T4 gate enforceability | this report §6.4 | no RelWit command exits non-zero on `QA_STALE` |
| T4 evidence-commit probe | `evidence/t4-ledger-commit-probe.txt` | committing only `work/` evidence flips RelWit QA to `QA_STALE`; native SHA-diff convention says valid |
| T5 scope/identity probe | `evidence/t5-deterministic-scope-probe.txt` | overlapping claim rejected; undeclared out-of-scope write accepted; self-declared reviewer closes task |
| N1 onboarding dry-run | `dry-n1` (§6.6) | external `init` leaves `work/.runtime-output/` un-ignored |

## Blocked / not exercised

| Capability | Status | Reason |
| --- | --- | --- |
| Agent teams | BLOCKED | Official docs: teammates are not spawned in non-interactive `-p` mode; the harness is headless |
| Cross-provider fallback (Codex, Antigravity, …) | BLOCKED | No other provider runtime or credentials in this container; only Anthropic models were launchable |
| Real provider outage / quota exhaustion | NOT INDUCED | Not fabricated; failures were induced with real `--max-turns` limits and `SIGKILL` instead |
| Native task list (`TaskCreate`) | NOT USED | Available to leads, never chosen in any run |
| Repetition count | LIMITED | 1 run per cell except T4 decisions (2 per mode) and T3/T6 variants; see threats to validity |
