#!/usr/bin/env python3
"""
One-shot REVIEW queue demo on a throwaway temp DB (not artifacts/mail/audit.db).

Injects a REVIEW policy rule for token GOVERN_REVIEW_PROBE, runs govern(),
lists pending reviews, resolves approve, prints the cycle.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_PATHS = (
    ROOT / "src",
    ROOT,  # certified_governance_unified.py ahead of hais/
    ROOT / "hais",
    ROOT / "imprint" / "src",
    ROOT / "haven2" / "src",
)
for p in reversed(_PATHS):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from certified_governance_unified import (  # noqa: E402
    CryptoEngine,
    PolicyEngine,
    PolicyRule,
    PolicySpec,
)
from governed_stack import GovernedStack  # noqa: E402


async def main() -> int:
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    key_path = db_path + ".pem"
    stack = None
    try:
        crypto = CryptoEngine(private_key_path=None)
        stack = GovernedStack(
            config={
                "db_path": db_path,
                "signing_key_path": key_path,
                "log_level": 40,
            },
            crypto=crypto,
        )
        base = PolicyEngine._default_spec()
        probe = PolicyRule(
            id="review_probe",
            type="banned",
            pattern=r"\bGOVERN_REVIEW_PROBE\b",
            action="REVIEW",
            reason="probe:review",
        )
        stack.engine.policy_engine = PolicyEngine(
            spec=PolicySpec(
                pii_rules=list(base.pii_rules),
                banned_terms=list(base.banned_terms) + [probe],
                action_rules=list(base.action_rules),
            )
        )

        token = stack.issue_token("damien", "user")
        env = await stack.govern(
            {
                "action": "query",
                "payload": {"text": "please look at GOVERN_REVIEW_PROBE once"},
            },
            token,
        )
        print("govern decision:", env.get("decision"))
        print("entry_id:", env.get("entry_id"))
        print("reasons:", env.get("reasons"))

        pending = stack.engine.list_pending_reviews()
        print("pending count:", len(pending))
        if not pending:
            print("ERROR: expected a pending REVIEW", file=sys.stderr)
            return 1
        entry_id = pending[0]["entry_id"]
        resolution = stack.engine.resolve_review(
            entry_id, resolved_by="damien", approve=True, notes="demo approve"
        )
        print("resolve:", json.dumps(resolution, indent=2, default=str))
        print("pending after:", len(stack.engine.list_pending_reviews()))
        return 0
    finally:
        if stack is not None:
            try:
                stack.engine.storage.close()
            except Exception:
                pass
        for p in (db_path, key_path):
            if os.path.exists(p):
                os.remove(p)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
