#!/usr/bin/env python3
"""
Hosted-check eval + audit-only governance projection against the live stack.

- Ephemeral POST /v1/check (real channels, GOV_*)
- GovernedActionBus mail stub (side_effect only on ALLOW)
- project_governance_score: AUDIT-ONLY shadow — does not affect decisions

  .venv/bin/python scripts/hosted_check_eval.py

See docs/HOSTED_CHECK_API.md. Complements scripts/hosted_check_smoke.py.
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "src", ROOT, ROOT / "hais", ROOT / "imprint" / "src", ROOT / "haven2" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from certified_governance_unified import CryptoEngine  # noqa: E402
from governed_stack import GovernedActionBus, GovernedStack, SendBlocked  # noqa: E402
from governed_stack.audit_projection import project_governance_score  # noqa: E402
from governed_stack.sidecar import SidecarService, create_server  # noqa: E402


@dataclass
class EvalCase:
    name: str
    kind: str  # http | bus_mail | projection
    expect: Dict[str, Any]
    build: Callable[[], Dict[str, Any]]
    project: bool = False  # attach audit projection when True


def _http_json(url: str, method: str = "GET", body: dict | None = None) -> Dict[str, Any]:
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


def _match(expect: Any, got: Any, path: str = "") -> Tuple[bool, str]:
    if isinstance(expect, dict) and isinstance(got, dict):
        for k, v in expect.items():
            if k not in got:
                return False, f"missing {path}{k}"
            ok, msg = _match(v, got[k], f"{path}{k}.")
            if not ok:
                return False, msg
        return True, "OK"
    if expect == got:
        return True, "OK"
    return False, f"{path} expect={expect!r} got={got!r}"


def build_cases(ctx: Dict[str, Any]) -> List[EvalCase]:
    base = ctx["base"]
    service: SidecarService = ctx["service"]
    bus: GovernedActionBus = ctx["bus"]
    token = service.issue_token("eval", "operator")
    cases: List[EvalCase] = []

    cases.append(
        EvalCase(
            name="http_missing_jwt_auth_failed",
            kind="http",
            expect={
                "http_status": 200,
                "body": {"decision": "BLOCK", "error_code": "GOV_AUTH_FAILED"},
            },
            build=lambda: _http_json(
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
            build=lambda: _http_json(
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
            build=lambda: _http_json(
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
            build=lambda: _http_json(
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
    cases.append(
        EvalCase(
            name="http_mail_slim_allow",
            kind="http",
            expect={"http_status": 200, "body": {"decision": "ALLOW"}},
            build=lambda: _http_json(
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

    def _algo() -> Dict[str, Any]:
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
        out["_has_spectrum"] = "spectrum" in body
        out["_has_quantum_line"] = "quantum_line" in body
        # Projection uses algorithm envelope fields (hais/haven2), not http wrapper.
        out["_projection_input"] = body
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
            project=True,
        )
    )

    def _mail_slim() -> Dict[str, Any]:
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
        out["_leaked"] = [
            k for k in ("quantum", "quantum_line", "spectrum", "hais", "haven2") if k in body
        ]
        return out

    cases.append(
        EvalCase(
            name="http_mail_no_spectrum_leak",
            kind="http",
            expect={"http_status": 200, "_leaked": []},
            build=_mail_slim,
        )
    )

    def _bus_allow() -> Dict[str, Any]:
        calls: List[Dict[str, Any]] = []

        def side_effect(result: dict) -> Dict[str, Any]:
            preview = {
                "simulated": True,
                "to": "test@example.com",
                "subject": "Hello",
                "body": "Simulated outbound — not sent.",
            }
            calls.append(preview)
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
            "error_code": out.get("error_code"),
            "side_effect_calls": len(calls),
            "simulated": (out.get("side_effect_result") or {}).get("simulated"),
            "_projection_input": out,
        }

    cases.append(
        EvalCase(
            name="bus_mail_allow_side_effect_once",
            kind="bus_mail",
            expect={"decision": "ALLOW", "side_effect_calls": 1, "simulated": True},
            build=_bus_allow,
            project=True,
        )
    )

    def _bus_block() -> Dict[str, Any]:
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
                "error_code": exc.result.get("error_code"),
                "side_effect_calls": len(calls),
                "_projection_input": exc.result,
            }

    cases.append(
        EvalCase(
            name="bus_mail_block_no_side_effect",
            kind="bus_mail",
            expect={"raised": True, "decision": "BLOCK", "side_effect_calls": 0},
            build=_bus_block,
            project=True,
        )
    )

    # Synthetic projection fixtures — audit-only, no HTTP (codes the live matrix
    # cannot cheaply force without policy/rate fixtures).
    cases.append(
        EvalCase(
            name="projection_hais_cap_shapes_risk",
            kind="projection",
            expect={"risk": 0.9},  # floor from GOV_HAIS_CAP shaping
            build=lambda: project_governance_score(
                {
                    "decision": "BLOCK",
                    "error_code": "GOV_HAIS_CAP",
                    "hais": {"cap": 0.1, "risk": 0.2, "instability": 0.1},
                    "haven2": {"realm": "normal", "open": True, "p_hat": 0.0},
                }
            ),
        )
    )
    cases.append(
        EvalCase(
            name="projection_latch_closed_boosts_governance",
            kind="projection",
            expect={"risk": 0.9},
            build=lambda: project_governance_score(
                {
                    "decision": "BLOCK",
                    "error_code": "GOV_LATCH_CLOSED",
                    "hais": {"cap": 0.9, "risk": 0.1, "instability": 0.05},
                    "haven2": {"realm": "defensive", "open": False, "p_hat": 0.2},
                }
            ),
        )
    )
    cases.append(
        EvalCase(
            name="projection_allow_calm_bounded",
            kind="projection",
            expect={},  # only assert keys + bounds below via post-check
            build=lambda: project_governance_score(
                {
                    "decision": "ALLOW",
                    "error_code": None,
                    "hais": {"cap": 0.8, "risk": 0.2, "instability": 0.1},
                    "haven2": {"realm": "calm", "open": True, "p_hat": 0.01},
                }
            ),
        )
    )

    return cases


def main() -> int:
    print("HOSTED CHECK EVAL — live sidecar + ActionBus")
    print("project_governance_score is AUDIT-ONLY (does not drive GOV_*).\n")

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
    cases = build_cases({"base": base, "service": service, "bus": bus})
    passed = 0
    failures: List[str] = []

    try:
        for case in cases:
            try:
                got = case.build()
            except Exception as exc:  # pragma: no cover
                msg = f"{case.name}: EXCEPTION {type(exc).__name__}: {exc}"
                print(f"  [FAIL] {msg}")
                failures.append(msg)
                continue

            if case.name == "projection_allow_calm_bounded":
                ok = (
                    isinstance(got, dict)
                    and set(got) >= {"risk", "stability", "governance"}
                    and all(0.0 <= float(got[k]) <= 1.0 for k in ("risk", "stability", "governance"))
                )
                detail = "OK" if ok else f"bounds/keys bad: {got!r}"
            else:
                ok, detail = _match(case.expect, got)

            proj_note = ""
            if case.project:
                src = got.get("_projection_input") if isinstance(got, dict) else None
                if isinstance(src, dict):
                    proj = project_governance_score(src)
                    got["_projection"] = proj
                    proj_note = f" | projection={proj}"
                    # Sanity: projection must be in [0,1]
                    if not all(0.0 <= float(proj[k]) <= 1.0 for k in proj):
                        ok = False
                        detail = f"projection out of bounds: {proj}"

            if ok:
                print(f"  [PASS] {case.name}{proj_note}")
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
