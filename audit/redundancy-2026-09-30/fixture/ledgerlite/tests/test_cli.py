import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from ledgerlite.cli import main


class CliTests(unittest.TestCase):
    def run_cli(self, payload, *extra):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "invoice.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = main([path, *extra])
            return code, out.getvalue(), err.getvalue()

    def test_summary(self):
        code, out, _ = self.run_cli({"tax_rate": "0.1", "lines": [{"description": "A", "quantity": 1, "unit_price": "100"}]})
        self.assertEqual(code, 0)
        self.assertIn("Total: $110.00", out)

    def test_invalid_input(self):
        code, _, err = self.run_cli({"lines": [{"description": "A", "quantity": 0, "unit_price": "1"}]})
        self.assertEqual(code, 1)
        self.assertIn("error:", err)


if __name__ == "__main__":
    unittest.main()
