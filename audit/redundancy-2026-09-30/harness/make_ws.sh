#!/usr/bin/env bash
# make_ws.sh <name> <clean|seeded> <n0|n1|n2>
# Creates an isolated Git workspace under $RW_SCRATCH/runs/<name> from the
# identical fixture state. All modes receive the same Claude Code runtime
# profile (.claude/agents: Sonnet implementer, Opus reviewer). N1 adds only the
# RelWit assurance config; N2 adds the full RelWit control plane.
source "$(dirname "$0")/lib.sh"
name="$1" variant="$2" mode="$3"
ws="$RUNS/$name"
rm -rf "$ws" && mkdir -p "$RUNS"
cp -r "$FIXTURE" "$ws"
find "$ws" -name __pycache__ -prune -exec rm -rf {} +
if [[ "$variant" == "seeded" ]]; then
  python "$AUDIT/harness/seed_defects.py" "$ws" >/dev/null
fi

# Identical native runtime profile for every mode (routing lives in Claude Code).
mkdir -p "$ws/.claude/agents"
cat > "$ws/.claude/agents/implementer.md" <<'EOF'
---
name: implementer
description: Implementation, debugging and test worker. Use for writing code and tests for one scoped task.
model: sonnet
---
You are an implementation worker. Implement exactly the scoped task you are
given, keep changes inside the files it needs, run the full test suite with
`python -m unittest discover -s tests`, and return a concise summary: files
changed, commands run and their results, and anything left undone. Never claim
success without running the tests.
EOF
cat > "$ws/.claude/agents/reviewer.md" <<'EOF'
---
name: reviewer
description: Independent reviewer. Use after implementation to verify a change against SPEC.md and the acceptance criteria.
model: opus
tools: Read, Grep, Glob, Bash
---
You are an independent reviewer; you did not write this change. Verify it
against SPEC.md and the stated acceptance criteria: read the diff, run the
tests, and probe edge cases with throwaway commands (do not modify tracked
files). Report each finding with severity, file:line and a reproducing command,
then a clear verdict: APPROVE or CHANGES_REQUESTED.
EOF

echo "$mode" > "$ws/.relwit-mode"
printf '__pycache__/\n*.pyc\n.relwit-mode\n' > "$ws/.gitignore"

if [[ "$mode" == "n1" || "$mode" == "n2" ]]; then
  ensure_relwit_venv
fi

write_config() {
  python - "$ws" "$1" <<'EOF'
import json, sys
ws, with_supervisor = sys.argv[1], sys.argv[2] == "yes"
config = {
    "version": 2,
    "supervisor": {
        "max_assignments_per_cycle": 4,
        "run_qa_each_cycle": False,
        "auto_dispatch": True,
        "qa_timeout_seconds": 300,
        "qa_commands": [{"mode": "argv", "argv": ["python", "-m", "unittest", "discover", "-s", "tests"]}],
        "operational_readiness_files": ["README.md"],
        "production_gates": [
            "All acceptance criteria are evidenced",
            "Focused and integration tests pass",
            "No open P0/P1 review finding",
            "Operational and rollback notes exist",
        ],
    },
    "agents": [],
}
if with_supervisor:
    config["agents"].append({"id": "supervisor", "role": "supervisor", "status": "available",
                             "directory": "work/agents/supervisor", "scope": ["."],
                             "capabilities": ["orchestration"], "max_active": 1})
json.dump(config, open(f"{ws}/relwit.config.json", "w"), indent=2)
EOF
}

if [[ "$mode" == "n1" ]]; then
  write_config no
  ( cd "$ws" && "$RELWIT_VENV/bin/relwit" init >/dev/null )
  ( cd "$ws" && "$RELWIT_VENV/bin/relwit" agent register --id implementer --role worker --scope . >/dev/null )
  ( cd "$ws" && "$RELWIT_VENV/bin/relwit" agent register --id reviewer --role reviewer --scope . >/dev/null )
fi

if [[ "$mode" == "n2" ]]; then
  cp "$RW_REPO/AGENTS.md" "$ws/AGENTS.md"
  mkdir -p "$ws/.agents" "$ws/knowledge/contracts" "$ws/knowledge/decisions" "$ws/knowledge/modules"
  cp -r "$RW_REPO/.agents/skills" "$ws/.agents/skills"
  cp -r "$RW_REPO/templates" "$ws/templates"
  cp -r "$RW_REPO/relwit" "$ws/relwit"
  find "$ws/relwit" -name __pycache__ -prune -exec rm -rf {} +
  cp "$RW_REPO/knowledge/INDEX.md" "$RW_REPO/knowledge/architecture.md" "$ws/knowledge/"
  cp "$RW_REPO"/knowledge/contracts/*.md "$ws/knowledge/contracts/"
  printf '# Project brief\n\n- `goal`: (not yet written; the supervisor records it)\n' > "$ws/knowledge/project-brief.md"
  printf '# Project map\n\n- `ledgerlite/`: package\n- `tests/`: unit tests\n- `SPEC.md`: behavior contract\n' > "$ws/knowledge/project-map.md"
  write_config yes
  ( cd "$ws" && python relwit/cli.py init >/dev/null )
fi

( cd "$ws" && git init -q -b main && git config user.name "experiment" && git config user.email "experiment@example.invalid" \
  && git add -A && git commit -q -m "baseline ($variant, $mode)" && git tag baseline )
echo "$ws"
