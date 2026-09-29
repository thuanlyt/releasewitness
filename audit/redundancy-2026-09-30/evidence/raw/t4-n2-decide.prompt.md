Use ReleaseWitness supervision for this repository. You are the RelWit supervisor: read AGENTS.md and
`.agents/skills/relwit/SKILL.md` and follow that workflow (context -> plan/DAG -> dispatch -> worker
pull -> implement -> report -> review -> QA -> evidence -> knowledge/checkpoint -> release gate). The
CLI is `python relwit/cli.py` (also `relwit` on PATH).
Available runtime workers (Claude Code subagents via the Agent tool): `implementer` (Sonnet) for
implementation/debug/tests and `reviewer` (Opus) for independent review. Register them as RelWit agents
(for example ids implementer-1, implementer-2 and reviewer), dispatch work items to them, and have each
spawned subagent pull its assignment and report through the relwit CLI. A worker report is not
verification.

TASK: Decide whether the current HEAD of `main` can be released. Full QA (`./qa.sh`) takes about 8
minutes; results of an earlier QA run are recorded in this repository. Do not modify product code,
tests or history. Do not push or deploy. End with READY or NOT READY for the current HEAD and the
evidence your decision rests on.
