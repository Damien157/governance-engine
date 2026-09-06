"""Focused PolicyEngine phone rule: no false positive on bare 2^32."""

from __future__ import annotations

import unittest

from certified_governance import PolicyEngine


class TestPhonePolicy(unittest.TestCase):
    def setUp(self):
        self.pe = PolicyEngine()

    def test_bare_uint32_not_phone(self):
        result = self.pe.evaluate(
            {"action": "query", "payload": {"text": "const n = 4294967296;"}}
        )
        self.assertEqual(result["decision_hint"], "ALLOW")
        self.assertFalse(any("phone" in r for r in result["reasons"]))

    def test_separated_phone_blocks(self):
        for text in ("555-123-4567", "555.123.4567", "(555) 123-4567"):
            result = self.pe.evaluate(
                {"action": "query", "payload": {"text": f"reach me at {text}"}}
            )
            self.assertEqual(result["decision_hint"], "BLOCK", msg=text)
            self.assertTrue(any("phone" in r for r in result["reasons"]), msg=text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
