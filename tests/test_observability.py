"""P1 observability tests: counters, prometheus, structured log, govern hook."""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
import unittest
from pathlib import Path

from certified_governance_unified import CryptoEngine

from governed_stack.observability import (
    DECISIONS_LOGGER,
    DecisionMetrics,
    structured_log,
)
from governed_stack.stack import GovernedStack


class TestDecisionMetrics(unittest.TestCase):
    def test_counters_and_snapshot(self):
        m = DecisionMetrics()
        m.record("ALLOW", 10.5)
        m.record("allow", 2.0)  # case-insensitive
        m.record("REVIEW", 3.0)
        m.record("BLOCK", 4.0)
        m.record("ERROR", 5.0)
        snap = m.snapshot()
        self.assertEqual(snap["allow"], 2)
        self.assertEqual(snap["review"], 1)
        self.assertEqual(snap["block"], 1)
        self.assertEqual(snap["error"], 1)
        self.assertEqual(snap["total"], 5)
        self.assertEqual(snap["latency_ms_count"], 5)
        self.assertAlmostEqual(snap["latency_ms_max"], 10.5)
        self.assertAlmostEqual(snap["latency_ms_sum"], 24.5)

    def test_prometheus_text(self):
        m = DecisionMetrics()
        m.record("ALLOW", 1.0)
        text = m.prometheus_text()
        self.assertIn('governed_stack_decisions_total{decision="allow"} 1', text)
        self.assertIn("# TYPE governed_stack_decisions_total counter", text)
        self.assertIn("governed_stack_decision_latency_ms_sum", text)

    def test_structured_log_json(self):
        records = []

        class Handler(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        h = Handler()
        DECISIONS_LOGGER.addHandler(h)
        DECISIONS_LOGGER.setLevel(logging.INFO)
        try:
            structured_log("test_event", decision="ALLOW", latency_ms=1.2)
        finally:
            DECISIONS_LOGGER.removeHandler(h)
        self.assertTrue(records)
        payload = json.loads(records[-1])
        self.assertEqual(payload["event"], "test_event")
        self.assertEqual(payload["decision"], "ALLOW")


class TestGovernMetricsIntegration(unittest.TestCase):
    def test_govern_allow_bumps_counter(self):
        with tempfile.TemporaryDirectory() as td:
            db = str(Path(td) / "a.db")
            key = str(Path(td) / "k.pem")
            crypto = CryptoEngine(private_key_path=key)
            stack = GovernedStack(
                config={"db_path": db, "signing_key_path": key, "log_level": 50},
                crypto=crypto,
            )
            before = stack.metrics.snapshot()["allow"]
            token = stack.issue_token("tester", "operator")
            intent = {"action": "query", "payload": {"difficulty": 0.2}, "telemetry": {}}
            env = asyncio.run(stack.govern(intent, token))
            self.assertEqual(env.get("decision"), "ALLOW")
            after = stack.metrics.snapshot()
            self.assertEqual(after["allow"], before + 1)
            self.assertGreaterEqual(after["latency_ms_count"], 1)


class TestAuditVerifyCLI(unittest.TestCase):
    def test_audit_verify_on_temp_db(self):
        import subprocess
        import sys

        with tempfile.TemporaryDirectory() as td:
            db = str(Path(td) / "audit.db")
            key = str(Path(td) / "signing_key.pem")
            crypto = CryptoEngine(private_key_path=key)
            stack = GovernedStack(
                config={"db_path": db, "signing_key_path": key, "log_level": 50},
                crypto=crypto,
            )
            token = stack.issue_token("tester", "operator")
            asyncio.run(stack.govern({"action": "query", "payload": {"difficulty": 0.2}}, token))
            root = Path(__file__).resolve().parents[1]
            proc = subprocess.run(
                [
                    sys.executable,
                    str(root / "scripts" / "audit_verify.py"),
                    "--db",
                    db,
                    "--key",
                    key,
                ],
                cwd=str(root),
                capture_output=True,
                text=True,
                env={
                    **dict(**{k: v for k, v in __import__("os").environ.items()}),
                    "PYTHONPATH": f"{root}:{root/'src'}:{root/'hais'}",
                },
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("OK", proc.stdout)


if __name__ == "__main__":
    unittest.main()
