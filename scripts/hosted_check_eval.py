#!/usr/bin/env python3
"""
Unified hosted-check **eval** against the live governance-engine.

Rewrites a draft matrix that simulated a parallel fake gate. Useful pieces kept:
  - EvalCase matrix + pass/fail summary
  - outbound stub gated by require_allow (no silent send)
  - transparent example scoring maths (NOT the live gate — self-check only)

Live surfaces under test:
  - ephemeral POST /v1/check sidecar (real channels + GOV_* codes)
  - GovernedActionBus.execute_sync (mail) — side_effect only on ALLOW

  .venv/bin/python scripts/hosted_check_eval.py

See docs/HOSTED_CHECK_API.md. Complements scripts/hosted_check_smoke.py.
"""

from __future__ import annotations

import json
import math
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "src", ROOT, ROOT / "hais", ROOT / "imprint" / "src", ROOT / "haven2" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from certified_governance_unified import CryptoEngine  # noqa: E402
from governed_stack import GovernedActionBus, GovernedStack, SendBlocked  # noqa: E402
from governed_stack.sidecar import SidecarService, create_server  # noqa: E402


# ---------------------------------------------------------------------------
# Example scoring maths — NOT wired into govern() / sidecar / HAIS
# Kept from the draft as a transparent, non-magical formula demo only.
# ---------------------------------------------------------------------------

def example_governance_score_math(risk_factors: Dict[str, float]) -> Dict[str, float]:
    """Example-only. Live decisions use ops policy + HAIS + Haven2, not this."""
    autonomy = risk_factors.get("autonomy", 0.0)
    safety_mod = risk_factors.get("safety_mod", 0.0)
    external_impact = risk_factors.get("external_impact", 0.0)
    risk = max(0.0, min(1.0, 0.5 * autonomy + 0.3 * safety_mod + 0.4 * external_impact))
    instability = 0.6 * autonomy + 0.6 * safety_mod
    stability = max(0.0, min(1.0, math.exp(-2.0 * instability)))
    governance = max(0.0, min(1.0, (1.0 - safety_mod) * (1.0 - 0.5 * external_impact)))
    return {
        "risk": round(risk, 3),
        "stability": round(stability, 3),
        "governance": round(governance, 3),
    }


# ---------------------------------------------------------------------------
# Matrix
# ---------------------------------------------------------------------------

@dataclass
class EvalCase:
    name: str
    kind: str  # http | bus_mail | example_math
    expect: Dict[str, Any]
    build: Callable[[Any], Dict[str, Any]]


def _http_json(url: str, method: str = "GET", body: dict | None = None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if body is not None else {},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            code = resp.status
    except urllib.error.HTTPError as exc:
        code = exc.code
        raw = exc.read().decode("utf-8")
    try:
        parsed = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        parsed = {"_raw": raw}
    return {"http_status": code, "body": parsed}


def _close_stack(stack: GovernedStack) -> None:
    eng = getattr(stack, "engine", None)
    storage = getattr(eng, "storage", None) if eng else None
    if storage is not None and hasattr(storage, "close"):
        try:
            storage.close()
        except Exception:
            pass


def _approx(a: float, b: float, tol: float = 1e-3) -> bool:
    return abs(float(a) - float(b)) <= tol


def _match(expect: Any, got: Any, path: str = "") -> Tuple[bool, str]:
    if isinstance(expect, dict) and isinstance(got, dict):
        for k, v in expect.items():
            if k not in got:
                return False, f"missing {path}{k}"
            ok, msg = _match(v, got[k], f"{path}{k}.")
            if not ok:
                return False, msg
        return True, "OK"
    if isinstance(expect, float) and isinstance(got, (int, float)):
        if _approx(expect, float(got)):
            return True, "OK"
        return False, f"{path} expect={expect} got={got}"
    if expect == got:
        return True, "OK"
    return False, f"{path} expect={expect!r} got={got!r}"


def build_cases(ctx: Dict[str, Any]) -> List[EvalCase]:
    base = ctx["base"]
    service: SidecarService = ctx["service"]
    bus: GovernedActionBus = ctx["bus"]
    token = service.issue_token("eval", "operator")

    cases: List[EvalCase] = []

    # --- HTTP: auth / contract errors (real GOV_* / HTTP codes) ---
    cases.append(
        EvalCase(
            name="http_missing_jwt_auth_failed",
            kind="http",
            expect={
                "http_status": 200,
                "body": {"decision": "BLOCK", "error_code": "GOV_AUTH_FAILED"},
            },
            build=lambda _c: _http_json(
                f"{base}/v1/check",
                method="POST",
                body={"channel": "mail", "subject": "hi", "body": "there"},
            ),
        )
    )
    cases.append(
        EvalCase(
            name="http_execute_refused_405",
            kind="http",
            expect={"http_status": 405, "body": {"error": "execute_not_supported"}},
            build=lambda _c: _http_json(
                f"{base}/v1/execute",
                method="POST",
                body={"channel": "mail", "token": "x"},
            ),
        )
    )
    cases.append(
        EvalCase(
            name="http_algorithm_missing_purpose",
            kind="http",
            expect={
                "http_status": 200,
                "body": {"decision": "BLOCK", "error_code": "GOV_INTENT_INVALID"},
            },
            build=lambda _c: _http_json(
                f"{base}/v1/check",
                method="POST",
                body={"channel": "algorithm", "token": token, "summary": "no purpose"},
            ),
        )
    )
    cases.append(
        EvalCase(
            name="http_algorithm_secret_key_forbidden",
            kind="http",
            expect={
                "http_status": 200,
                "body": {"decision": "BLOCK", "error_code": "GOV_INTENT_INVALID"},
            },
            build=lambda _c: _http_json(
                f"{base}/v1/check",
                method="POST",
                body={
                    "channel": "algorithm",
                    "token": token,
                    "purpose": "batch_dedupe",
                    "password": "smuggled",
                },
            ),
        )
    )

    # --- HTTP: slim mail ALLOW ---
    cases.append(
        EvalCase(
            name="http_mail_slim_allow",
            kind="http",
            expect={"http_status": 200, "body": {"decision": "ALLOW"}},
            build=lambda _c: _http_json(
                f"{base}/v1/check",
                method="POST",
                body={
                    "channel": "mail",
                    "token": token,
                    "subject": "Lunch",
                    "body": "Are you free tomorrow?",
                },
            ),
        )
    )

    # --- HTTP: algorithm with spectrum keys present ---
    def _algo(_c: Any) -> Dict[str, Any]:
        out = _http_json(
            f"{base}/v1/check",
            method="POST",
            body={
                "channel": "algorithm",
                "token": token,
                "purpose": "batch_dedupe",
                "summary": "Nightly anonymized id dedupe",
                "time_cost": "O(n log n)",
                "space_cost": "O(n)",
                "energy_cost": "low",
                "speedup": "~2x",
                "risk_notes": "read-only replica",
                "security_margin": "standard",
            },
        )
        body = out["body"] if isinstance(out["body"], dict) else {}
        # Assert slim-vs-rich: algorithm carries spectrum/quantum keys
        out["_has_spectrum"] = "spectrum" in body
        out["_has_quantum_line"] = "quantum_line" in body
        out["_decision"] = body.get("decision")
        return out

    cases.append(
        EvalCase(
            name="http_algorithm_has_spectrum",
            kind="http",
            expect={
                "http_status": 200,
                "_has_spectrum": True,
                "_has_quantum_line": True,
            },
            build=_algo,
        )
    )

    # --- HTTP: slim mail must NOT leak spectrum ---
    def _mail_slim_keys(_c: Any) -> Dict[str, Any]:
        out = _http_json(
            f"{base}/v1/check",
            method="POST",
            body={
                "channel": "mail",
                "token": token,
                "subject": "hello",
                "body": "world without secrets",
            },
        )
        body = out["body"] if isinstance(out["body"], dict) else {}
        leaked = [k for k in ("quantum", "quantum_line", "spectrum", "hais", "haven2") if k in body]
        out["_leaked"] = leaked
        out["_decision"] = body.get("decision")
        return out

    cases.append(
        EvalCase(
            name="http_mail_no_spectrum_leak",
            kind="http",
            expect={"http_status": 200, "_leaked": []},
            build=_mail_slim_keys,
        )
    )

    # --- Bus: outbound mail stub — ALLOW runs side_effect once ---
    def _bus_allow(_c: Any) -> Dict[str, Any]:
        calls: List[Dict[str, Any]] = []

        def side_effect(result: dict) -> Dict[str, Any]:
            preview = {
                "simulated": True,
                "to": "test@example.com",
                "subject": "Hello",
                "body": "Simulated outbound — not sent.",
            }
            calls.append({"result_decision": result.get("decision"), "preview": preview})
            return preview

        out = bus.execute_sync(
            "mail",
            to="test@example.com",
            subject="Hello",
            body="Simulated outbound — not sent.",
            side_effect=side_effect,
            user="eval",
            role="operator",
        )
        return {
            "decision": out.get("decision"),
            "side_effect_calls": len(calls),
            "side_effect_result": out.get("side_effect_result"),
            "simulated": (out.get("side_effect_result") or {}).get("simulated"),
        }

    cases.append(
        EvalCase(
            name="bus_mail_allow_side_effect_once",
            kind="bus_mail",
            expect={
                "decision": "ALLOW",
                "side_effect_calls": 1,
                "simulated": True,
            },
            build=_bus_allow,
        )
    )

    # --- Bus: policy BLOCK (email in body) — side_effect never runs ---
    def _bus_block(_c: Any) -> Dict[str, Any]:
        calls: List[Any] = []

        def side_effect(result: dict) -> None:
            calls.append(result)

        try:
            bus.execute_sync(
                "mail",
                to="alice@example.com",
                subject="Lunch",
                body="Also CC bob@example.com please",
                side_effect=side_effect,
                user="eval",
                role="operator",
            )
            return {"raised": False, "side_effect_calls": len(calls)}
        except SendBlocked as exc:
            return {
                "raised": True,
                "decision": exc.result.get("decision"),
                "side_effect_calls": len(calls),
            }

    cases.append(
        EvalCase(
            name="bus_mail_block_no_side_effect",
            kind="bus_mail",
            expect={"raised": True, "decision": "BLOCK", "side_effect_calls": 0},
            build=_bus_block,
        )
    )

    # --- Example maths self-check (not a gate) ---
    cases.append(
        EvalCase(
            name="example_math_high_risk_formula",
            kind="example_math",
            expect={
                # Same formula as example_governance_score_math (includes [0,1] cap).
                "risk": 1.0,
                "stability": round(math.exp(-2.0 * (0.6 * 1.0 + 0.6 * 1.0)), 3),
                "governance": 0.0,
            },
            build=lambda _c: example_governance_score_math(
                {"autonomy": 1.0, "safety_mod": 1.0, "external_impact": 0.8}
            ),
        )
    )

    return cases


def main() -> int:
    print("HOSTED CHECK EVAL — live sidecar + action bus (no fake parallel gate)")
    print("Example scoring maths is self-check only; it does not drive decisions.\n")

    tmp = tempfile.TemporaryDirectory()
    td = Path(tmp.name)
    db_path = str(td / "audit.db")
    key_path = str(td / "signing_key.pem")
    crypto = CryptoEngine(private_key_path=key_path)
    stack = GovernedStack(
        config={"db_path": db_path, "signing_key_path": key_path, "log_level": 50},
        crypto=crypto,
    )
    service = SidecarService(
        config={
            "db_path": db_path,
            "signing_key_path": key_path,
            "host": "127.0.0.1",
            "port": 0,
            "api_key": None,
            "log_level": 50,
            "rate_limit_per_min": 0,
        },
        stack=stack,
    )
    httpd, _ = create_server(service, host="127.0.0.1", port=0)
    port = httpd.server_address[1]
    base = f"http://127.0.0.1:{port}"
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.05)

    bus = GovernedActionBus(stack=stack)
    ctx = {"base": base, "service": service, "bus": bus}
    cases = build_cases(ctx)
    passed = 0
    failures: List[str] = []

    try:
        for case in cases:
            try:
                got = case.build(None)
            except Exception as exc:  # pragma: no cover
                msg = f"{case.name}: EXCEPTION {type(exc).__name__}: {exc}"
                print(f"  [FAIL] {msg}")
                failures.append(msg)
                continue
            ok, detail = _match(case.expect, got)
            if ok:
                # Extra: mail slim allow should not leak (checked in dedicated case)
                print(f"  [PASS] {case.name}")
                passed += 1
            else:
                msg = f"{case.name}: {detail}"
                print(f"  [FAIL] {msg}")
                print(f"         got={got!r}")
                failures.append(msg)
    finally:
        httpd.shutdown()
        httpd.server_close()
        _close_stack(stack)
        tmp.cleanup()

    total = len(cases)
    print(f"\nSummary: {passed}/{total} PASS")
    if failures:
        print("Failures:")
        for f in failures:
            print(" -", f)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
