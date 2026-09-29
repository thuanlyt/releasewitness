
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
