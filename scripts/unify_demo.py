#!/usr/bin/env python3
"""Demo the unified front door: manifest, import_check, live ALLOW, sketch demos."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hais"))
sys.path.insert(0, str(ROOT / "imprint" / "src"))
sys.path.insert(0, str(ROOT / "haven2" / "src"))

from governed_stack import HavenUnified, import_check, live_ok  # noqa: E402
from governed_stack.catalog import TIERS  # noqa: E402


def _pprint(label: str, payload) -> None:
    print(f"\n=== {label} ===")
    if isinstance(payload, str):
        print(payload)
    else:
        print(json.dumps(payload, indent=2, default=str))


async def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="unify_demo_", dir="/tmp"))
    db_path = str(tmp / "unify.db")
    key_path = str(tmp / "signing_key.pem")

    hu = HavenUnified(
        config={"db_path": db_path, "signing_key_path": key_path, "log_level": 40}
    )
    token = hu.issue_token("demo_user", "user")

    man = hu.manifest()
    print("\n=== manifest (tiers + paths) ===")
    print("version:", man["version"])
    print("front_door:", man["front_door"])
    print("live_kernel:", man["live_kernel"])
    for tier, mods in man["tiers"].items():
        print(f"  [{tier}] ({len(mods)})")
        for m in mods:
            print(f"    - {m}")
    print("paths:", json.dumps(man["paths"], indent=2))

    check = import_check()
    _pprint("import_check", check)
    print("live_ok:", live_ok(check))

    benign = await hu.govern(
        {"action": "query", "payload": {"text": "hello there, status please"}},
        token,
    )
    _pprint("ALLOW govern query", {"decision": benign.get("decision"), "reasons": benign.get("reasons"), "hais_cap": (benign.get("hais") or {}).get("cap")})

    mail = hu.mail.check_sync(
        to="alice@example.com",
        subject="Lunch",
        body="Are you free tomorrow?",
    )
    _pprint("mail check ALLOW", {"decision": mail["decision"], "ok": mail["ok"], "to": mail["to"]})

    cal = hu.calendar.check_sync(
        summary="Team sync",
        description="Weekly status",
        attendees="alice@example.com",
    )
    _pprint(
        "calendar check ALLOW",
        {"decision": cal["decision"], "ok": cal["ok"], "summary": cal.get("summary")},
    )

    sat = hu.run_3sat_demo()
    _pprint("3sat sketch", {"sat": sat["sat"], "solution": sat["solution"], "on_decision_path": sat["on_decision_path"]})

    topo = hu.run_topology_diagnostics()
    print(f"\n=== topology diagnostics ===\n{topo['summary']} (on_decision_path={topo['on_decision_path']})")

    pipe = hu.run_production_pipeline_sketch()
    _pprint(
        "production pipeline sketch",
        {
            "on_decision_path": pipe["on_decision_path"],
            "note": pipe["note"],
            "status": (pipe.get("result") or {}).get("status"),
        },
    )

    fluids = hu.fluids_smoke()
    _pprint("fluids smoke", fluids)

    sketch_fails = {
        e["module"]: check.get(e["module"], False)
        for e in TIERS["sketch"]
        if not check.get(e["module"], False)
    }
    if sketch_fails:
        print("\nsketch import failures (non-fatal):", sketch_fails)

    ok = live_ok(check)
    print("\nexit:", 0 if ok else 1, "| live imports ok:", ok)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
