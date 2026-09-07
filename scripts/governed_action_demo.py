#!/usr/bin/env python3
"""
Dry-run demo of GovernedActionBus — no real Gmail/Calendar/social sends.

Shows ALLOW (mock side_effect runs) and BLOCK (side_effect never called).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (
    ROOT / "src",
    ROOT,
    ROOT / "hais",
    ROOT / "imprint" / "src",
    ROOT / "haven2" / "src",
):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from governed_stack import ActionDenied, GovernedActionBus, GovernedStack  # noqa: E402
from certified_governance_unified import CryptoEngine  # noqa: E402


def main() -> int:
    # Ephemeral stack so demo does not touch artifacts/mail.
    import os
    import tempfile

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    key_path = db_path + ".pem"
    crypto = CryptoEngine(private_key_path=None)
    stack = GovernedStack(
        config={"db_path": db_path, "signing_key_path": key_path, "log_level": 40},
        crypto=crypto,
    )
    bus = GovernedActionBus(stack=stack)

    calls: list[str] = []

    def mock_effect(result: dict) -> str:
        calls.append(result.get("decision", "?"))
        return "dry-run-ok"

    print("=== ALLOW path (clean mail) ===")
    try:
        out = bus.execute_sync(
            "mail",
            to="alice@example.com",
            subject="Lunch",
            body="Are you free tomorrow?",
            side_effect=mock_effect,
        )
        print(json.dumps({k: out.get(k) for k in ("ok", "decision", "executed", "side_effect_result")}, indent=2))
    except ActionDenied as exc:
        print("unexpected deny:", exc.result)

    print("=== BLOCK path (extra email in body) ===")
    try:
        bus.execute_sync(
            "mail",
            to="alice@example.com",
            subject="Lunch",
            body="Also CC bob@example.com please",
            side_effect=mock_effect,
        )
        print("unexpected ALLOW")
    except ActionDenied as exc:
        print(json.dumps({"denied": True, "decision": exc.result.get("decision"), "side_effect_calls": list(calls)}, indent=2))

    print("=== side_effect call log (ALLOW only) ===")
    print(calls)

    # cleanup
    try:
        eng = getattr(stack, "engine", None)
        storage = getattr(eng, "storage", None) if eng else None
        if storage is not None and hasattr(storage, "close"):
            storage.close()
    except Exception:
        pass
    for p in (db_path, key_path):
        try:
            os.remove(p)
        except OSError:
            pass

    print("HAIS m=0.5; sketches off govern(); sidecar stays check-only (no /v1/execute).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
