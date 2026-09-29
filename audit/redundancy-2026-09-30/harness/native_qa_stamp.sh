#!/usr/bin/env bash
# Native baseline for source-bound QA: no RelWit involved.
#   native_qa_stamp.sh run    -> run QA; on pass write .git/qa-stamp bound to source state
#   native_qa_stamp.sh check  -> exit 0 if the stamp matches the current source, else exit 2
# Usable as a Claude Code PreToolUse hook (exit 2 blocks e.g. `git tag`/`git push`).
# Source identity = HEAD tree + staged/unstaged diff + non-ignored untracked file
# contents + the QA command itself. Stored inside .git so it never dirties the tree.
set -uo pipefail
QA_CMD=${QA_CMD:-"python -m unittest discover -s tests"}
# Optional pathspec to ignore (only used when RelWit's own work/ ledger shares the tree).
EXCLUDE=${NATIVE_EXCLUDE:-}
spec=(.); [[ -n "$EXCLUDE" ]] && spec=(. ":(exclude)$EXCLUDE")
stamp_file="$(git rev-parse --git-dir)/qa-stamp"
source_id() {
  {
    git rev-parse 'HEAD^{tree}' 2>/dev/null || echo no-head
    git diff HEAD --binary -- "${spec[@]}" 2>/dev/null
    git ls-files --others --exclude-standard -z -- "${spec[@]}" | xargs -0 -r sha256sum
    echo "$QA_CMD"
  } | sha256sum | cut -d' ' -f1
}
case "${1:-check}" in
  run)
    if bash -c "$QA_CMD" >/dev/null 2>&1; then
      echo "$(source_id) $(git rev-parse HEAD) $(date -u +%FT%TZ)" > "$stamp_file"; echo "QA PASS (stamped)"
    else
      rm -f "$stamp_file"; echo "QA FAIL"; exit 1
    fi ;;
  check)
    [[ -f "$stamp_file" ]] || { echo "NO_QA: no passing QA stamp" >&2; exit 2; }
    [[ "$(cut -d' ' -f1 "$stamp_file")" == "$(source_id)" ]] || { echo "QA_STALE: source changed since QA" >&2; exit 2; }
    echo "QA_VALID" ;;
esac
