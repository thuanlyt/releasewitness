#!/usr/bin/env bash
# t6_clone_audit.sh <run-name>: cross-machine variant of TEST 6 phase C.
# `git clone` carries only committed state; no ~/.claude transcripts exist for the clone path.
source "$(dirname "$0")/lib.sh"; set +e
src="$RUNS/$1" dst="$RUNS/$1-clone"
rm -rf "$dst"; git clone -q "$src" "$dst"
echo "n0" > "$dst/.relwit-mode"   # no relwit on PATH: the auditor reads files only
run_claude "$dst" opus "$AUDIT/prompts/task-t6-audit-clone.md" "$1-Cclone" 1800
python "$AUDIT/harness/summarize_run.py" "$EVID/raw/$1-Cclone.jsonl" > "$EVID/raw/$1-Cclone.summary.json"
echo "done $1-Cclone"
