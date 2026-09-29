#!/usr/bin/env bash
# Shared harness helpers for the RelWit redundancy experiment.
# Usage: source harness/lib.sh   (requires RW_SCRATCH and RW_REPO)
set -euo pipefail

: "${RW_REPO:?set RW_REPO to the ReleaseWitness checkout}"
: "${RW_SCRATCH:?set RW_SCRATCH to a scratch directory outside the repo}"
AUDIT="$RW_REPO/audit/redundancy-2026-09-30"
FIXTURE="$AUDIT/fixture/ledgerlite"
RUNS="$RW_SCRATCH/runs"
EVID="$AUDIT/evidence"
RELWIT_VENV="$RW_SCRATCH/relwit-venv"

# Nested Claude Code sessions must not inherit the orchestrating session's id,
# its additional directories (which would load unrelated instructions) or its
# child-session marker. Everything else (auth/proxy) is inherited unchanged.
claude_clean() {
  env -u CLAUDE_CODE_SESSION_ID -u CLAUDE_CODE_REMOTE_SESSION_ID \
      -u CLAUDE_ADDITIONAL_DIRECTORIES -u CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD \
      -u CLAUDE_CODE_CHILD_SESSION IS_SANDBOX=1 "$@"
}

# run_claude <workspace> <model> <prompt-file> <out-name> <timeout-sec> [extra claude args...]
# Writes the full stream-json transcript to $EVID/raw/<out-name>.jsonl.
run_claude() {
  local ws="$1" model="$2" prompt="$3" out="$4" limit="$5"; shift 5
  mkdir -p "$EVID/raw"
  local path_prefix=""
  if [[ -f "$ws/.relwit-mode" ]] && [[ "$(cat "$ws/.relwit-mode")" != "n0" ]]; then
    path_prefix="$RELWIT_VENV/bin:"
  fi
  local started; started=$(date +%s)
  ( cd "$ws" && PATH="${path_prefix}$PATH" claude_clean timeout --kill-after=10 "$limit" \
      claude -p "$(cat "$prompt")" --model "$model" --dangerously-skip-permissions \
      --output-format stream-json --verbose "$@" ) > "$EVID/raw/$out.jsonl" 2> "$EVID/raw/$out.stderr" || true
  echo "$(( $(date +%s) - started ))" > "$EVID/raw/$out.wall"
}

ensure_relwit_venv() {
  if [[ ! -x "$RELWIT_VENV/bin/relwit" ]]; then
    python -m venv "$RELWIT_VENV"
    "$RELWIT_VENV/bin/python" -m pip install -q --no-deps "$RW_REPO"
  fi
}
