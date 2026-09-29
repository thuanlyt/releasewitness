#!/usr/bin/env bash
# TEST 5 variant: force native worktree isolation for concurrent writers (N0 only).
source "$(dirname "$0")/lib.sh"; set +e
name=t5w-n0
ws=$("$AUDIT/harness/make_ws.sh" "$name" clean n0 | tail -1)
cat "$AUDIT/prompts/mode-n0.md" "$AUDIT/prompts/common-constraints.md" "$AUDIT/prompts/task-t5-concurrent.md" "$AUDIT/prompts/task-t5w-worktree.md" > "$EVID/raw/$name.prompt.md"
run_claude "$ws" opus "$EVID/raw/$name.prompt.md" "$name" 3000
python "$AUDIT/harness/summarize_run.py" "$EVID/raw/$name.jsonl" > "$EVID/raw/$name.summary.json"
"$AUDIT/harness/score_history.sh" "$ws" "$RW_SCRATCH/hidden/t5" "$name" > "$EVID/raw/$name.scores.jsonl"
{ git -C "$ws" log --graph --oneline --all; git -C "$ws" worktree list; git -C "$ws" branch -a; } > "$EVID/raw/$name.git-graph.txt" 2>&1
echo "done $name"
