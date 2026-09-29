#!/usr/bin/env bash
# run_task.sh <run-name> <n0|n1|n2> <clean|seeded> <task-prompt-file> <hidden-suite> <timeout-sec>
# Fresh workspace -> one real Opus-led Claude Code session -> scores.
source "$(dirname "$0")/lib.sh"
name="$1" mode="$2" variant="$3" task="$4" hidden="$5" limit="$6"
ws=$("$AUDIT/harness/make_ws.sh" "$name" "$variant" "$mode" | tail -1)
prompt="$EVID/raw/$name.prompt.md"
mkdir -p "$EVID/raw"
cat "$AUDIT/prompts/mode-$mode.md" "$AUDIT/prompts/common-constraints.md" "$AUDIT/prompts/$task" > "$prompt"
run_claude "$ws" opus "$prompt" "$name" "$limit"
python "$AUDIT/harness/summarize_run.py" "$EVID/raw/$name.jsonl" > "$EVID/raw/$name.summary.json"
"$AUDIT/harness/score_history.sh" "$ws" "$hidden" "$name" > "$EVID/raw/$name.scores.jsonl"
echo "done $name"
