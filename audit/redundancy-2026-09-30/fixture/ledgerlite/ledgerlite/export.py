"""CSV export for invoices."""

import csv
import io

HEADER = ["description", "quantity", "unit_price", "discount_pct", "net"]


def to_csv(invoice):
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(HEADER)
    for line in invoice.lines:
        writer.writerow(
            [
                line.description,
                line.quantity,
                f"{line.unit_price:.2f}",
                f"{line.discount_pct}",
                f"{line.net():.2f}",
            ]
        )
    writer.writerow(["TOTAL", "", "", "", f"{invoice.total():.2f}"])
    return buffer.getvalue()
