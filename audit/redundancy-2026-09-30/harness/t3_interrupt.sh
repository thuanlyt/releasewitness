#!/usr/bin/env bash
# t3_interrupt.sh <run-name> <n0|n1|n2> <resume: fresh|native>
# TEST 3: start the TEST-1 task, hard-kill (SIGKILL to the process group) after
# meaningful partial progress, snapshot ground truth, then resume with either a
# fresh session that receives no prior conversation (fresh) or Claude Code's
# native `--resume <session-id>` (native).
source "$(dirname "$0")/lib.sh"; set +e
name="$1" mode="$2" resume="$3"
ws=$("$AUDIT/harness/make_ws.sh" "$name" clean "$mode" | tail -1)
prompt="$EVID/raw/$name.prompt.md"
cat "$AUDIT/prompts/mode-$mode.md" "$AUDIT/prompts/common-constraints.md" "$AUDIT/prompts/task-t1-currency.md" > "$prompt"
path_prefix=""; [[ "$mode" != "n0" ]] && path_prefix="$RELWIT_VENV/bin:"

progress() {  # number of changed product/test files across the workspace and its worktrees
  local total=0 wt
  while read -r wt; do
    n=$(git -C "$wt" status --porcelain -- ledgerlite tests SPEC.md 2>/dev/null | wc -l)
    c=$(git -C "$wt" diff --name-only baseline HEAD -- ledgerlite tests SPEC.md 2>/dev/null | wc -l)
    total=$(( total + n + c ))
  done < <(git -C "$ws" worktree list --porcelain | sed -n 's/^worktree //p')
  echo "$total"
}

started=$(date +%s)
( cd "$ws" && PATH="${path_prefix}$PATH" claude_clean claude -p "$(cat "$prompt")" --model opus \
    --dangerously-skip-permissions --output-format stream-json --verbose ) > "$EVID/raw/$name.jsonl" 2> "$EVID/raw/$name.stderr" &
leader=$!
kill_ws() {  # SIGKILL every process whose cwd is inside the workspace (session, tools, worktrees)
  local pid cwd
  for pid in $(ls /proc | grep -E '^[0-9]+$'); do
    cwd=$(readlink "/proc/$pid/cwd" 2>/dev/null) || continue
    [[ "$cwd" == "$ws" || "$cwd" == "$ws/"* ]] && kill -9 "$pid" 2>/dev/null
  done
}
triggered=""
while kill -0 $leader 2>/dev/null; do
  p=$(progress)
  if [[ -z "$triggered" && "$p" -ge 3 ]]; then triggered=$(date +%s); fi
  if [[ -n "$triggered" && $(( $(date +%s) - triggered )) -ge 45 ]]; then break; fi
  if [[ $(( $(date +%s) - started )) -ge 1500 ]]; then break; fi
  sleep 5
done
if kill -0 $leader 2>/dev/null; then
  kill_ws; kill -9 $leader 2>/dev/null
  echo "KILLED after $(( $(date +%s) - started ))s (progress=$(progress))" > "$EVID/raw/$name.kill"
else
  echo "NOT KILLED: session finished on its own after $(( $(date +%s) - started ))s" > "$EVID/raw/$name.kill"
fi
sleep 2
{
  echo "## kill-time ground truth"; cat "$EVID/raw/$name.kill"
  echo "## git log"; git -C "$ws" log --oneline baseline^..HEAD
  echo "## worktrees"; git -C "$ws" worktree list
  while read -r wt; do
    echo "### $wt status"; git -C "$wt" status --porcelain | grep -v '^?? work/'
    echo "### $wt diffstat vs baseline"; git -C "$wt" diff --stat baseline
  done < <(git -C "$ws" worktree list --porcelain | sed -n 's/^worktree //p')
  if [[ "$mode" != "n0" ]]; then echo "## relwit task list"; ( cd "$ws" && "$RELWIT_VENV/bin/relwit" task list ); fi
  echo "## hidden score of main working tree at kill"; "$AUDIT/harness/score.sh" "$ws" "$RW_SCRATCH/hidden/t1" "$name@kill"
} > "$EVID/raw/$name.killstate.txt" 2>&1
python "$AUDIT/harness/summarize_run.py" "$EVID/raw/$name.jsonl" > "$EVID/raw/$name.summary.json"

# Resume.
if [[ "$resume" == "native" ]]; then
  sid=$(python -c 'import json,sys
for l in open(sys.argv[1]):
    try: e=json.loads(l)
    except Exception: continue
    if e.get("session_id"): print(e["session_id"]); break' "$EVID/raw/$name.jsonl")
  printf 'Your previous run of this session was killed abruptly partway through the task. Continue the same task to completion, following all of the original instructions and constraints.\n' > "$EVID/raw/$name-resume.prompt.md"
  run_claude "$ws" opus "$EVID/raw/$name-resume.prompt.md" "$name-resume" 3000 --resume "$sid"
else
  { printf 'A previous session working on the task below was killed abruptly partway through. You do not have its conversation. First determine the true current state of the work from this repository, then complete the task. In your final report include a "Recovery assessment" listing what you found completed, partially done, lost, and what you redid.\n\n'
    cat "$prompt"; } > "$EVID/raw/$name-resume.prompt.md"
  run_claude "$ws" opus "$EVID/raw/$name-resume.prompt.md" "$name-resume" 3000
fi
python "$AUDIT/harness/summarize_run.py" "$EVID/raw/$name-resume.jsonl" > "$EVID/raw/$name-resume.summary.json"
"$AUDIT/harness/score_history.sh" "$ws" "$RW_SCRATCH/hidden/t1" "$name" > "$EVID/raw/$name.scores.jsonl"
echo "done $name"
