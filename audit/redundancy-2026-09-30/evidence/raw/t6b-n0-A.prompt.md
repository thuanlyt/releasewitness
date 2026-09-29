You are an implementation worker in this repository. Implement the task below completely, run the full
test suite (`python -m unittest discover -s tests`), and commit your work to `main` when it passes.

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
