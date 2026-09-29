#!/usr/bin/env bash
# t4_run.sh <n0|n1|n2> <repeats>: TEST 4 agent level.
# Phase QA (real session records QA at source A) -> teammate drift commit (source B)
# -> <repeats> independent fresh release-decision sessions on B.
source "$(dirname "$0")/lib.sh"; set +e
mode="$1" reps="$2" name="t4-$1"
ws=$("$AUDIT/harness/make_ws.sh" "$name" clean "$mode" | tail -1)
"$AUDIT/harness/t4_add_soak.sh" "$ws"
cat "$AUDIT/prompts/mode-$mode.md" "$AUDIT/prompts/common-constraints.md" "$AUDIT/prompts/task-t4-qa.md" > "$EVID/raw/$name-qa.prompt.md"
run_claude "$ws" opus "$EVID/raw/$name-qa.prompt.md" "$name-qa" 2400
python "$AUDIT/harness/summarize_run.py" "$EVID/raw/$name-qa.jsonl" > "$EVID/raw/$name-qa.summary.json"
git -C "$ws" log --oneline > "$EVID/raw/$name-qa.gitlog"
git -C "$ws" status --porcelain >> "$EVID/raw/$name-qa.gitlog"
"$AUDIT/harness/t4_drift.sh" "$ws" > "$EVID/raw/$name-drift.txt"
git -C "$ws" rev-parse HEAD >> "$EVID/raw/$name-drift.txt"
decide="$AUDIT/prompts/mode-$mode.md"; [[ "$mode" == "n1" ]] && decide="$AUDIT/prompts/mode-n1-decide.md"
cat "$decide" "$AUDIT/prompts/task-t4-decide.md" > "$EVID/raw/$name-decide.prompt.md"
for i in $(seq 1 "$reps"); do
  # Each decision starts from the identical drifted state.
  snap="$RUNS/$name-d$i"; rm -rf "$snap"; cp -a "$ws" "$snap"
  run_claude "$snap" opus "$EVID/raw/$name-decide.prompt.md" "$name-d$i" 2400
  python "$AUDIT/harness/summarize_run.py" "$EVID/raw/$name-d$i.jsonl" > "$EVID/raw/$name-d$i.summary.json"
  git -C "$snap" status --porcelain > "$EVID/raw/$name-d$i.after-status"
done
echo "done $name"
