#!/usr/bin/env python3
"""Repeatable exercises for spectral honesty FIX 1–4.

Validates silent-fallback / honesty fixes — not the happy-path product curl.
See docs/SPECTRAL_AUDIT_FIXES.md.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hais"))
sys.path.insert(0, str(ROOT / "imprint" / "src"))
sys.path.insert(0, str(ROOT / "haven2" / "src"))

from certified_governance_unified import CryptoEngine  # noqa: E402
from governed_stack import GovernedStack  # noqa: E402
from governed_stack.spectral_audit import build_spectrum  # noqa: E402


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def _ok(label: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    extra = f" — {detail}" if detail else ""
    print(f"  [{status}] {label}{extra}")
    if not cond:
        raise AssertionError(f"{label}{extra}")


def exercise_a_fix1() -> None:
    _section("A FIX1 — sigma from nested haven2.zeta")
    env = {
        "haven2": {
            "zeta": {
                "Z_E": 0.1,
                "Z_R": 0.0,
                "Z_C": 0.2,
                "Z_H": 0.3,
                "sigma": 5.0,
            },
        }
    }
    out = build_spectrum(env, sigma=2.0)
    print(f"  sigma={out.get('sigma')!r} available={out.get('available')!r}")
    _ok("out['sigma']==5.0", out.get("sigma") == 5.0, f"got {out.get('sigma')!r}")
    _ok("available True", out.get("available") is True)


def exercise_b_fix2() -> None:
    _section("B FIX2 — incomplete zeta (missing Z_H), no p_hat, engine=None")
    env = {
        "haven2": {
            "zeta": {"Z_E": 0.1, "Z_R": 0.0, "Z_C": 0.2},  # no Z_H
        }
    }
    out = build_spectrum(env, engine=None)
    print(
        f"  available={out.get('available')!r} partial={out.get('partial')!r} "
        f"Z_H={out.get('Z_H')!r} reason={out.get('reason')!r}"
    )
    _ok("available False", out.get("available") is False)
    _ok("partial True", out.get("partial") is True)
    _ok("Z_H None", out.get("Z_H") is None)
    reason = (out.get("reason") or "").lower()
    _ok("reason mentions incomplete", "incomplete" in reason, reason)


def exercise_c_fix3() -> None:
    _section("C FIX3 — malformed c (no silent zero)")
    env = {"haven2": {"p_hat": 0.5, "c": "not-a-float"}}
    out = build_spectrum(env, engine=None)
    print(f"  available={out.get('available')!r} reason={out.get('reason')!r}")
    _ok("available False (no silent zero)", out.get("available") is False)


def exercise_d_fix4() -> None:
    _section("D FIX4 — zeta_summaries failure surfaces zeta_error")
    tmp = Path(tempfile.mkdtemp(prefix="spectral_fix_", dir="/tmp"))
    db_path = str(tmp / "fix4.db")
    key_path = str(tmp / "signing_key.pem")
    crypto = CryptoEngine(private_key_path=key_path)
    stack = GovernedStack(
        config={"db_path": db_path, "signing_key_path": key_path, "log_level": 40},
        crypto=crypto,
    )

    def _boom(*_a, **_k):
        raise RuntimeError("forced_zeta_fail")

    stack.haven2.zeta_summaries = _boom  # type: ignore[method-assign]
    token = stack.issue_token("alice", "user")
    env = asyncio.run(
        stack.govern({"action": "query", "payload": {"text": "ping"}}, token)
    )
    haven2 = env.get("haven2") or {}
    notes = env.get("notes") or []
    decision = env.get("decision")
    print(
        f"  decision={decision!r} zeta_error={haven2.get('zeta_error')!r} "
        f"has_zeta={'zeta' in haven2} notes={notes!r}"
    )
    _ok("haven2 has zeta_error", "zeta_error" in haven2, str(haven2.get("zeta_error")))
    _ok("no zeta key", "zeta" not in haven2)
    _ok(
        "notes contain haven2_zeta_summaries_error",
        any("haven2_zeta_summaries_error" in str(n) for n in notes),
        str(notes),
    )
    _ok(
        "decision is normal (ALLOW/BLOCK/REVIEW)",
        decision in ("ALLOW", "BLOCK", "REVIEW"),
        f"got {decision!r}",
    )


def main() -> int:
    print(
        "SPECTRAL FIX 1–4 EXERCISES — validate honesty fixes, "
        "not the happy-path product curl"
    )
    try:
        exercise_a_fix1()
        exercise_b_fix2()
        exercise_c_fix3()
        exercise_d_fix4()
    except AssertionError as exc:
        print(f"\nFAILED: {exc}")
        return 1
    except Exception as exc:
        print(f"\nERROR: {type(exc).__name__}: {exc}")
        return 1
    print("\n=== ALL PASS ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
