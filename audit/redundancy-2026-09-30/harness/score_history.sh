#!/usr/bin/env bash
# score_history.sh <workspace> <hidden-suite> <label>
# Scores every commit since `baseline` (in a detached temporary worktree) plus
# the live working tree, so "worker done" and "after review" states are visible.
source "$(dirname "$0")/lib.sh"
ws="$1" hidden="$2" label="$3"
cd "$ws"
for sha in $(git rev-list --reverse baseline^..HEAD 2>/dev/null || git rev-list --reverse HEAD); do
  tmp=$(mktemp -d); git worktree add -q --detach "$tmp/w" "$sha" >/dev/null 2>&1
  line=$("$AUDIT/harness/score.sh" "$tmp/w" "$hidden" "$label@$sha")
  msg=$(git log -1 --format=%s "$sha" | head -c 90)
  python -c 'import json,sys; d=json.loads(sys.argv[1]); d["subject"]=sys.argv[2]; print(json.dumps(d))' "$line" "$msg"
  git worktree remove --force "$tmp/w" >/dev/null 2>&1; rm -rf "$tmp"
done
"$AUDIT/harness/score.sh" "$ws" "$hidden" "$label@worktree"
