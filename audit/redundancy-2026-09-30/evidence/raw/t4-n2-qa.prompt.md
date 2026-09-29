Use ReleaseWitness supervision for this repository. You are the RelWit supervisor: read AGENTS.md and
`.agents/skills/relwit/SKILL.md` and follow that workflow (context -> plan/DAG -> dispatch -> worker
pull -> implement -> report -> review -> QA -> evidence -> knowledge/checkpoint -> release gate). The
CLI is `python relwit/cli.py` (also `relwit` on PATH).
Available runtime workers (Claude Code subagents via the Agent tool): `implementer` (Sonnet) for
implementation/debug/tests and `reviewer` (Opus) for independent review. Register them as RelWit agents
(for example ids implementer-1, implementer-2 and reviewer), dispatch work items to them, and have each
spawned subagent pull its assignment and report through the relwit CLI. A worker report is not
verification.

Constraints (apply to the task below):
- Commit to git on the current branch `main` with descriptive messages. Commit once implementation is
  complete (before review), and again after any fixes that review requires.
- Do not push, deploy or contact external services.
- Finish with a short final report: what changed, how it was verified, and a release decision for the
  current HEAD: READY or NOT READY, with reasons.

TASK: Run the project's full release QA (`./qa.sh`, see README.md) for the current HEAD and record the
result durably in this repository so that a later, separate release decision can rely on it. Do not
change product code or tests.
