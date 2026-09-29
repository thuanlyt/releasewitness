"""Command line entry point: python -m ledgerlite invoice.json [--csv]."""

import argparse
import json
import sys

from .export import to_csv
from .invoice import Invoice
from .money import format_money


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ledgerlite")
    parser.add_argument("path", help="invoice JSON file")
    parser.add_argument("--csv", action="store_true", help="print CSV instead of a summary")
    args = parser.parse_args(argv)
    try:
        with open(args.path, encoding="utf-8") as handle:
            invoice = Invoice.from_dict(json.load(handle))
    except (OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.csv:
        sys.stdout.write(to_csv(invoice))
        return 0
    print(f"Subtotal: {format_money(invoice.subtotal())}")
    print(f"Discount: {format_money(invoice.discount_total())}")
    print(f"Tax: {format_money(invoice.tax())}")
    print(f"Total: {format_money(invoice.total())}")
    return 0
