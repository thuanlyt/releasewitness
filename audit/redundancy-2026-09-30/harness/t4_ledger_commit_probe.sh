#!/usr/bin/env bash
# Does committing ONLY RelWit's own volatile evidence (work/) invalidate the QA it records?
source "$(dirname "$0")/lib.sh"; set +e
ws=$("$AUDIT/harness/make_ws.sh" t4det-ledgercommit clean n1 | tail -1); cd "$ws"; R="$RELWIT_VENV/bin/relwit"
"$R" supervisor qa >/dev/null; "$R" supervisor report >/dev/null
qa_sha=$(git rev-parse HEAD)
echo "after QA:            $(grep -o 'qa_source_state`: `[^`]*' work/SUPERVISOR_REPORT.md | head -1)"
git add work && git commit -qm "Record QA evidence (work/ only)"
echo "files in that commit: $(git show --name-only --format= HEAD | grep -vc '^work/') non-work, $(git show --name-only --format= HEAD | grep -c '^work/') work/"
"$R" supervisor report >/dev/null
echo "after ledger commit: $(grep -o 'qa_source_state`: `[^`]*' work/SUPERVISOR_REPORT.md | head -1)"
# The convention the N0 agent wrote unprompted in its QA record (TEST 4, t4-n0-qa):
git diff --quiet "$qa_sha" HEAD -- . ':!work'; echo "N0-convention check (git diff <qa-sha> HEAD excluding evidence dir): exit=$? (0 = QA still applies)"
