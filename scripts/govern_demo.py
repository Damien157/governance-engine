#!/usr/bin/env python3
"""Async demo of the unified GovernedStack entry point."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "hais"))
sys.path.insert(0, str(ROOT / "imprint" / "src"))
sys.path.insert(0, str(ROOT / "haven2" / "src"))

from governed_stack import GovernedStack  # noqa: E402


def _pprint(label: str, envelope: dict) -> None:
    print(f"\n=== {label} ===")
    print(json.dumps(envelope, indent=2, default=str))


async def main() -> int:
    artifacts = ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="governed_stack_", dir="/tmp"))
    db_path = str(tmp / "governed_stack.db")
    key_path = str(tmp / "signing_key.pem")

    stack = GovernedStack(
        config={
            "db_path": db_path,
            "signing_key_path": key_path,
            "log_level": 40,  # ERROR — keep demo output clean
        }
    )
    token = stack.issue_token("demo_user", "user")
    print(f"token issued; db={db_path}")

    benign = await stack.govern(
        {"action": "query", "payload": {"text": "hello there, status please"}},
        token,
    )
    _pprint("benign query", benign)

    pii = await stack.govern(
        {
            "action": "query",
            "payload": {"text": "reach me at bob@example.com"},
        },
        token,
    )
    _pprint("email PII", pii)

    if benign.get("decision") == "ALLOW":
        # Fresh stack with a looser latch so the optional 3DM solver can run
        # (default epsilon_switch=0.05 closes on typical CGE risk_signal).
        from haven2 import Haven2Engine
        stack3 = GovernedStack(
            config={
                "db_path": str(tmp / "governed_stack_3dm.db"),
                "signing_key_path": key_path,
                "log_level": 40,
            },
            crypto=stack.engine.crypto,
        )
        stack3.haven2 = Haven2Engine(epsilon_switch=2.0)
        token3 = stack3.issue_token("demo_user", "user")
        three_dm = await stack3.govern(
            {"action": "3dm", "payload": {"text": "collapse a demo matching"}, "seed": 0},
            token3,
        )
        _pprint("optional 3dm", three_dm)

    print("\nsummary:", benign.get("decision"), "then", pii.get("decision"))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
