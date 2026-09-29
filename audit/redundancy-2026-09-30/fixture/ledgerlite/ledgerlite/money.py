"""Money parsing, rounding and formatting helpers."""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")
SYMBOLS = {"USD": "$"}


def parse_amount(value):
    """Parse a user-supplied amount into a Decimal.

    Accepts int, Decimal or strings such as "12.50" or "1,234.50".
    """
    if isinstance(value, bool):
        raise ValueError(f"invalid amount: {value!r}")
    if isinstance(value, (int, Decimal)):
        return Decimal(value)
    text = str(value).strip().replace(",", "")
    if not text:
        raise ValueError("empty amount")
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"invalid amount: {value!r}") from exc


def round_money(value):
    """Round to cents using half-up rounding."""
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def format_money(value, currency="USD"):
    """Format an amount such as -1234.5 as "-$1,234.50"."""
    amount = round_money(value)
    sign = "-" if amount < 0 else ""
    symbol = SYMBOLS.get(currency, f"{currency} ")
    return f"{sign}{symbol}{abs(amount):,.2f}"
