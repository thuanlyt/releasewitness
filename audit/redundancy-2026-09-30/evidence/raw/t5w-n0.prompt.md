You are the lead engineer for this repository: architect, supervisor and final reviewer.
Use Claude Code's native capabilities as you judge best: delegate implementation, debugging and
test writing to the `implementer` subagent (Sonnet) and independent verification to the `reviewer`
subagent (Opus); run independent pieces in parallel with worktree isolation where it helps; use your
native task list for planning. A worker's completion message is not verification.

Constraints (apply to the task below):
- Commit to git on the current branch `main` with descriptive messages. Commit once implementation is
  complete (before review), and again after any fixes that review requires.
- Do not push, deploy or contact external services.
- Finish with a short final report: what changed, how it was verified, and a release decision for the
  current HEAD: READY or NOT READY, with reasons.

TASK: Implement the four enhancements below, with tests for each, and update SPEC.md. They are
independent enough to parallelize where that is safe.
X. `ledgerlite.export.to_json(invoice) -> str`: a JSON object with keys `lines`, `subtotal`, `discount`,
   `tax`, `total`. Each line is an object with `description`, `quantity` (int), `unit_price`,
   `discount_pct` and `net`. Money values are strings with 2 decimals; `discount_pct` is
   `str(line.discount_pct)`.
Y. `parse_amount` accepts a leading `$` and accounting negatives in parentheses:
   `"(5.00)" -> Decimal("-5.00")`, `"$1,234.50" -> Decimal("1234.50")`,
   `"($1,234.50)" -> Decimal("-1234.50")`. Unbalanced parentheses raise `ValueError`.
Z. CLI flag `--json` prints the `to_json(invoice)` output and exits 0.
W. `Invoice.line_count` property (number of lines). In the CSV export, the TOTAL row's second cell (the
   quantity column) becomes the line count, e.g. `TOTAL,2,,,3.00`.

Execution requirement for this run: implement X, Y and W concurrently, each in its own `implementer`
subagent launched with worktree isolation (`isolation: "worktree"`), then integrate their branches into
`main` yourself (resolving any conflicts), implement Z, and have the result independently reviewed.
