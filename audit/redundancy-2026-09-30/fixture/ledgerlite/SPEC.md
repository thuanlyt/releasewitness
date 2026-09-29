# ledgerlite specification

`ledgerlite` is a small, dependency-free invoice calculator. This document is
the authoritative behavior contract; tests must follow it.

## Amounts

- S1. `parse_amount(value)` accepts `int`, `Decimal` or a string. Strings may
  contain thousands separators (`"1,234.50"`). Empty or non-numeric input
  raises `ValueError`.
- S2. `round_money(value)` rounds to cents using **half-up** rounding:
  `2.675 -> 2.68`, `0.125 -> 0.13`, `-0.125 -> -0.13`. Rounding must be exact
  decimal arithmetic, never binary floating point.
- S3. `format_money(value, currency="USD")` renders the sign first, then the
  symbol, then the amount with thousands separators and two decimals:
  `format_money(Decimal("-1234.5")) == "-$1,234.50"`.

## Line items

- S4. `LineItem(description, quantity, unit_price, discount_pct=0)`.
  `quantity` must be a positive `int`; `unit_price` must be `>= 0`;
  `discount_pct` must be between `0` and `100` inclusive. Violations raise
  `ValueError`.
- S5. `gross = round_money(unit_price * quantity)`;
  `discount = round_money(gross * discount_pct / 100)`;
  `net = gross - discount`.

## Invoice

- S6. `Invoice(lines, tax_rate="0")`; `tax_rate` is a fraction between `0` and
  `1` inclusive, otherwise `ValueError`.
- S7. `subtotal` is the sum of line gross amounts; `discount_total` is the sum
  of line discounts; `taxable = subtotal - discount_total`.
- S8. Tax is charged on the **discounted** amount:
  `tax = round_money(taxable * tax_rate)`; `total = taxable + tax`.

## CSV export

- S9. `to_csv(invoice)` returns RFC 4180 CSV with header
  `description,quantity,unit_price,discount_pct,net`, one row per line and a
  final `TOTAL` row whose last cell is the invoice total. Fields containing
  commas, quotes or newlines are quoted, and embedded quotes are doubled.

## CLI

- S10. `python -m ledgerlite invoice.json` prints `Subtotal`, `Discount`,
  `Tax` and `Total` lines using `format_money`. `--csv` prints the CSV export.
  Invalid input prints `error: ...` to stderr and exits with status `1`.
