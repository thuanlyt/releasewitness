You are the lead engineer for this repository: architect, supervisor and final reviewer.
Use Claude Code's native capabilities as you judge best: delegate implementation, debugging and
test writing to the `implementer` subagent (Sonnet) and independent verification to the `reviewer`
subagent (Opus); run independent pieces in parallel with worktree isolation where it helps; use your
native task list for planning. A worker's completion message is not verification.

Assurance layer: ReleaseWitness (`relwit` CLI, on PATH) is configured here for ASSURANCE ONLY.
Claude Code owns planning, delegation, model choice and worktrees; do not use RelWit for planning or
dispatch. Use it only to record evidence and check release state:
- Create one work item for the whole task: `relwit task new --title "..." --level L2 --owner supervisor --scope . --acceptance "..."` (repeat --acceptance per criterion), then `relwit task claim <id> --agent implementer`.
- After implementation: `relwit task report <id> --agent implementer --result completed --summary "..." --next-action "review" --file <path> ... --check "<command>: <result>"`.
- After independent review passes: `relwit task evidence <id> --kind review --agent reviewer --value "<repeatable command and result>"`, then `relwit task update <id> --status needs_review --agent reviewer` and `relwit task update <id> --status done --agent reviewer`.
- Before declaring a release decision: commit, run `relwit supervisor qa` (source-bound QA), then `relwit supervisor report`, and base the decision on the Production gates and QA source state in `work/SUPERVISOR_REPORT.md`.

Constraints (apply to the task below):
- Commit to git on the current branch `main` with descriptive messages. Commit once implementation is
  complete (before review), and again after any fixes that review requires.
- Do not push, deploy or contact external services.
- Finish with a short final report: what changed, how it was verified, and a release decision for the
  current HEAD: READY or NOT READY, with reasons.

TASK: A previous worker attempted the task below and did not finish (its process ended early). You do
not have its conversation. Take over: first determine what state the work is in, then complete the task.

TASK: Add multi-currency support to ledgerlite. Append a "Currencies" section to SPEC.md with these rules
and implement them, with tests for each rule:
1. Supported currencies: USD (symbol `$`, 2 decimals), EUR (symbol `€`, 2 decimals), JPY (symbol `¥`,
   0 decimals). Any other code raises `ValueError("unsupported currency: <CODE>")`.
2. `round_money(value, currency="USD")` rounds half-up to the currency's minor unit
   (`round_money(Decimal("1234.5"), "JPY") == Decimal("1235")`).
3. `format_money(value, currency="USD")`: sign first, then symbol, thousands separators, currency
   decimals: `format_money(Decimal("-1234.5"), "EUR") == "-€1,234.50"`,
   `format_money(Decimal("1234.5"), "JPY") == "¥1,235"`. Unsupported codes raise `ValueError`.
4. `Invoice(lines, tax_rate="0", currency="USD")`: every line gross/discount, tax and total uses the
   invoice currency's rounding. An unsupported currency raises `ValueError` at construction.
   `Invoice.from_dict` reads an optional `"currency"` key (default USD).
5. CSV export: add a `currency` column as the LAST column of the header, every line row and the TOTAL
   row. Numeric cells are plain decimals with the currency's decimals and no symbol (JPY `10`, EUR `10.00`).
6. CLI: `--currency CODE` overrides the JSON `currency` field (default USD when neither is given). The
   summary uses the invoice currency. An unsupported `--currency` prints a message to stderr and exits
   with status 2.
Existing USD behavior must not change except for the added CSV column.
