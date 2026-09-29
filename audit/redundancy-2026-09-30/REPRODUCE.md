# Reproducing the RelWit redundancy experiments

## Source identities

| What | Value |
| --- | --- |
| RelWit under test (starting `main`) | `e747b8811c235841e9be1b8a62770e430d0b141d` (v0.2.0) |
| Study branch | `claude/relwit-redundancy-validation-2026-09-30` |
| Claude Code CLI | 2.1.284 (`claude --version`) |
| Models (as served, from `modelUsage`) | `claude-opus-5-5` (lead, reviewer), `claude-sonnet-5-5` (implementer) |
| Python | 3.11.15 |

The fixture baseline is identical for every run; each workspace records it as its root commit,
tagged `baseline`.

## Layout

```text
fixture/ledgerlite/        clean fixture (TEST 1, 3, 4, 5, 6 start here)
harness/seed_defects.py    TEST 2 seeded variant (7 spec violations)
fixture-hidden/t1,t2,t5    held-out acceptance suites (never copied into a workspace)
fixture-reference/*.patch  reference solutions used to validate the hidden suites
prompts/                   mode activation blocks + task bodies (assembled per run)
harness/                   workspace generator, run drivers, scorers, probes
evidence/                  metrics, deterministic results, redacted raw transcripts
```

## Setup

```bash
export RW_REPO=$PWD                       # this checkout
export RW_SCRATCH=/some/scratch/dir       # outside the repo; workspaces + hidden suites live here
mkdir -p "$RW_SCRATCH/hidden" && cp -r audit/redundancy-2026-09-30/fixture-hidden/* "$RW_SCRATCH/hidden/"
H=audit/redundancy-2026-09-30/harness
```

Nested sessions run as `claude -p --model <m> --dangerously-skip-permissions --output-format
stream-json --verbose` with `IS_SANDBOX=1`. That is acceptable only in a throwaway container:
every run is confined to its own scratch workspace. `harness/lib.sh:claude_clean` strips the
parent session id and additional-directory variables so that nested runs load no outside
instructions.

## Commands

```bash
# Validate hidden suites against the fixture (clean passes T2; seeded fails exactly D1-D7)
(cd audit/redundancy-2026-09-30/fixture/ledgerlite && PYTHONPATH=. python -m unittest discover -s "$RW_SCRATCH/hidden/t2")

# TEST 1 / 2 / 5 (one fresh workspace + one Opus-led session each)
for m in n0 n1 n2; do $H/run_task.sh t1-$m $m clean  task-t1-currency.md   "$RW_SCRATCH/hidden/t1" 3000; done
for m in n0 n1 n2; do $H/run_task.sh t2-$m $m seeded task-t2-audit.md      "$RW_SCRATCH/hidden/t2" 3000; done
for m in n0 n1 n2; do $H/run_task.sh t5-$m $m clean  task-t5-concurrent.md "$RW_SCRATCH/hidden/t5" 3000; done
$H/t5w_run.sh                                           # forced native worktree isolation

# TEST 3 (SIGKILL after progress; then fresh session or native --resume)
$H/t3_interrupt.sh t3-n0 n0 fresh; $H/t3_interrupt.sh t3-n0r n0 native
$H/t3_interrupt.sh t3-n1 n1 fresh; $H/t3_interrupt.sh t3-n2 n2 fresh
for m in n0 n1 n2; do T3_MIN_FILES=2 T3_DELAY=5 $H/t3_interrupt.sh t3e-$m $m fresh; done

# TEST 4 (agent level: QA at A -> drift commit -> 2 fresh decisions) and deterministic parts
for m in n0 n1 n2; do $H/t4_run.sh $m 2; done
python $H/t4_stale_qa_matrix.py > audit/redundancy-2026-09-30/evidence/t4-deterministic-matrix.json
$H/t4_ledger_commit_probe.sh
(cd audit/redundancy-2026-09-30/evidence/raw && python ../../harness/t4_classify_decisions.py t4-n{0,1,2}-d{1,2})

# TEST 5 deterministic scope/identity probe
$H/t5_scope_probe.sh

# TEST 6 (max-turns 6 and 3; takeover; audits same-machine and fresh clone)
for m in n0 n1 n2; do $H/t6_run.sh $m; done
for m in n0 n1 n2; do T6_SUFFIX=b T6_MAX_TURNS=3 $H/t6_run.sh $m; done
for r in t6-n0 t6-n1 t6-n2 t6b-n0 t6b-n1 t6b-n2; do $H/t6_clone_audit.sh $r; done

# Aggregate + re-score everything with the final scorer
python $H/build_matrix.py
```

Model output is non-deterministic: re-runs reproduce the protocol, not identical transcripts.
The deterministic probes (T4 matrix, evidence-commit probe, T5 scope probe) are exactly
reproducible.

## Raw evidence

`evidence/raw-transcripts-redacted.tgz` holds every session's full stream-json transcript and
stderr. The owner's e-mail address and the account/organization identifiers inherited from the
host session were replaced with placeholders; nothing else was altered. `evidence/raw/*.summary.json`,
`*.scores*.jsonl`, `*.wall`, `*.killstate.txt`, `*.state.txt` and `*.prompt.md` are committed
unmodified.
