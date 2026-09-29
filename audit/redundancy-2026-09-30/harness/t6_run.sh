#!/usr/bin/env bash
# t6_run.sh <n0|n1|n2>: TEST 6 failure / fallback / handoff.
#  A. A Sonnet worker attempts the TEST-1 task with a hard `--max-turns 6` limit
#     (a real runtime termination, not a simulated outage).
#     n0: plain worker session; n1: worker also claims/reports a RelWit item;
#     n2: dispatched through RelWit's own runner bridge (`relwit worker run`).
#  B. A fresh Opus session (different model) takes over with no conversation.
#  C. A fresh Opus auditor answers what happened, from durable artifacts only.
source "$(dirname "$0")/lib.sh"; set +e
mode="$1" name="t6-$1"
ws=$("$AUDIT/harness/make_ws.sh" "$name" clean "$mode" | tail -1)
R="$RELWIT_VENV/bin/relwit"
task="$AUDIT/prompts/task-t1-currency.md"
mkdir -p "$EVID/raw"

if [[ "$mode" == "n0" ]]; then
  cat "$AUDIT/prompts/task-t6-worker.md" "$task" > "$EVID/raw/$name-A.prompt.md"
  run_claude "$ws" sonnet "$EVID/raw/$name-A.prompt.md" "$name-A" 1800 --max-turns 6
elif [[ "$mode" == "n1" ]]; then
  ( cd "$ws" && "$R" task new --title "Add multi-currency support" --level L2 --owner supervisor --scope . \
      --acceptance "Currencies section in SPEC.md implemented per task-t1 rules 1-6 with tests" >/dev/null )
  { cat "$AUDIT/prompts/task-t6-worker.md"
    printf '\nRecord your work in ReleaseWitness: before editing run `relwit task claim RW-0001 --agent implementer`; when finished run `relwit task report RW-0001 --agent implementer --result completed --summary "..." --next-action review --file <path> ... --check "<command>: <result>"`.\n'
    cat "$task"; } > "$EVID/raw/$name-A.prompt.md"
  run_claude "$ws" sonnet "$EVID/raw/$name-A.prompt.md" "$name-A" 1800 --max-turns 6
else
  # RelWit's documented opt-in runner bridge: argv adapter with {assignment_path}.
  adapter="$ws/.relwit-adapter.sh"
  cat > "$adapter" <<EOF
#!/usr/bin/env bash
# Claude Code worker adapter for RelWit (argv runner). Arg 1: assignment path.
cd "$ws"
prompt="\$(cat "$AUDIT/prompts/task-t6-worker.md")
You are the RelWit worker session sonnet-worker. Read .agents/skills/relwit-worker/SKILL.md and follow it.
Your assignment (already pulled; the task is in_progress) is at: \$1
Implement it, run the tests, commit, and submit with \\\`relwit task report\\\`."
PATH="$RELWIT_VENV/bin:\$PATH" env -u CLAUDE_CODE_SESSION_ID -u CLAUDE_CODE_REMOTE_SESSION_ID \\
  -u CLAUDE_ADDITIONAL_DIRECTORIES -u CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD -u CLAUDE_CODE_CHILD_SESSION IS_SANDBOX=1 \\
  claude -p "\$prompt" --model sonnet --max-turns 6 --dangerously-skip-permissions \\
  --output-format stream-json --verbose > "$EVID/raw/$name-A.jsonl" 2> "$EVID/raw/$name-A.stderr"
rc=\$?
echo "adapter: claude exited \$rc"
exit \$rc
EOF
  chmod +x "$adapter"; echo ".relwit-adapter.sh" >> "$ws/.git/info/exclude"
  {
    cd "$ws"
    python relwit/cli.py agent register --id sonnet-worker --role worker --scope . \
      --runner-arg=bash --runner-arg="$adapter" --runner-arg={assignment_path} --runner-timeout 1800
    python relwit/cli.py agent register --id reviewer --role reviewer --scope .
    acceptance=$(python -c 'import re,sys; t=open(sys.argv[1]).read(); print(" ".join(t.split()))' "$task")
    python relwit/cli.py task new --title "Add multi-currency support" --level L2 --owner supervisor --scope . \
      --preferred-agent sonnet-worker --acceptance "$acceptance" \
      --verification "python -m unittest discover -s tests"
    python relwit/cli.py supervisor dispatch
    started=$(date +%s)
    PATH="$RELWIT_VENV/bin:$PATH" python relwit/cli.py worker run --agent sonnet-worker --max-tasks 1
    echo "$(( $(date +%s) - started ))" > "$EVID/raw/$name-A.wall"
    python relwit/cli.py supervisor cycle
  } > "$EVID/raw/$name-A.relwit.txt" 2>&1
fi
python "$AUDIT/harness/summarize_run.py" "$EVID/raw/$name-A.jsonl" > "$EVID/raw/$name-A.summary.json"
{ echo "## after attempt A"; git -C "$ws" log --oneline baseline^..HEAD; git -C "$ws" status --porcelain | grep -v '^?? work/'
  [[ "$mode" != "n0" ]] && ( cd "$ws" && "$R" task list )
  "$AUDIT/harness/score.sh" "$ws" "$RW_SCRATCH/hidden/t1" "$name@A"; } > "$EVID/raw/$name-A.state.txt" 2>&1

cat "$AUDIT/prompts/mode-$mode.md" "$AUDIT/prompts/common-constraints.md" "$AUDIT/prompts/task-t6-takeover.md" "$task" > "$EVID/raw/$name-B.prompt.md"
run_claude "$ws" opus "$EVID/raw/$name-B.prompt.md" "$name-B" 3000
python "$AUDIT/harness/summarize_run.py" "$EVID/raw/$name-B.jsonl" > "$EVID/raw/$name-B.summary.json"
"$AUDIT/harness/score_history.sh" "$ws" "$RW_SCRATCH/hidden/t1" "$name" > "$EVID/raw/$name.scores.jsonl"

cp "$AUDIT/prompts/task-t6-audit.md" "$EVID/raw/$name-C.prompt.md"
run_claude "$ws" opus "$EVID/raw/$name-C.prompt.md" "$name-C" 1800
python "$AUDIT/harness/summarize_run.py" "$EVID/raw/$name-C.jsonl" > "$EVID/raw/$name-C.summary.json"
echo "done $name"
