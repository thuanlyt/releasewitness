"""Invoice model: line items, discounts, tax and totals."""

from decimal import Decimal

from .money import parse_amount, round_money


class LineItem:
    def __init__(self, description, quantity, unit_price, discount_pct=0):
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise ValueError("quantity must be a positive integer")
        price = parse_amount(unit_price)
        if price < 0:
            raise ValueError("unit_price must not be negative")
        discount = parse_amount(discount_pct)
        if discount < 0 or discount > 100:
            raise ValueError("discount_pct must be between 0 and 100")
        self.description = str(description)
        self.quantity = quantity
        self.unit_price = price
        self.discount_pct = discount

    def gross(self):
        return round_money(self.unit_price * self.quantity)

    def discount(self):
        return round_money(self.gross() * self.discount_pct / Decimal(100))

    def net(self):
        return self.gross() - self.discount()


class Invoice:
    def __init__(self, lines, tax_rate="0"):
        self.lines = list(lines)
        self.tax_rate = parse_amount(tax_rate)
        if self.tax_rate < 0 or self.tax_rate > 1:
            raise ValueError("tax_rate must be a fraction between 0 and 1")

    def subtotal(self):
        return sum((line.gross() for line in self.lines), Decimal("0.00"))

    def discount_total(self):
        return sum((line.discount() for line in self.lines), Decimal("0.00"))

    def taxable(self):
        return self.subtotal() - self.discount_total()

    def tax(self):
        return round_money(self.taxable() * self.tax_rate)

    def total(self):
        return self.taxable() + self.tax()

    @classmethod
    def from_dict(cls, data):
        lines = [
            LineItem(
                entry["description"],
                entry["quantity"],
                entry["unit_price"],
                entry.get("discount_pct", 0),
            )
            for entry in data.get("lines", [])
        ]
        return cls(lines, data.get("tax_rate", "0"))
