#!/usr/bin/env python3
"""Operator smoke for the hosted check-only sidecar (POST /v1/check).

Starts an ephemeral stdlib sidecar (temp PEM + audit DB), runs a small matrix
aligned with tests/test_sidecar.py edge cases, exits 0 on all pass.

  .venv/bin/python scripts/hosted_check_smoke.py

Not a full eval harness / benchmark suite — buyer-facing contract smoke only.
No /v1/execute path; no NP claims.
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "src", ROOT, ROOT / "hais", ROOT / "imprint" / "src", ROOT / "haven2" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from certified_governance_unified import CryptoEngine  # noqa: E402
from governed_stack.sidecar import SidecarService, create_server  # noqa: E402
from governed_stack.stack import GovernedStack  # noqa: E402


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
        parsed = raw
    return code, parsed


def main() -> int:
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
        if not ok:
            failures.append(name)

    print("HOSTED CHECK SMOKE — check-only sidecar matrix")
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

    try:
        code, body = _http_json(f"{base}/health")
        check("GET /health", code == 200 and isinstance(body, dict) and body.get("status") == "ok")

        code, body = _http_json(f"{base}/ready")
        check("GET /ready", code == 200 and isinstance(body, dict) and body.get("status") == "ready")

        code, body = _http_json(
            f"{base}/v1/check",
            method="POST",
            body={"channel": "mail", "subject": "hi", "body": "there"},
        )
        check(
            "missing JWT → GOV_AUTH_FAILED",
            code == 200
            and isinstance(body, dict)
            and body.get("decision") == "BLOCK"
            and body.get("error_code") == "GOV_AUTH_FAILED",
            str(body.get("error_code") if isinstance(body, dict) else body),
        )

        code, body = _http_json(
            f"{base}/v1/execute",
            method="POST",
            body={"channel": "mail", "token": "x"},
        )
        check(
            "POST /v1/execute → 405",
            code == 405
            and isinstance(body, dict)
            and body.get("error") == "execute_not_supported",
        )

        token = service.issue_token("smoke", "operator")
        code, body = _http_json(
            f"{base}/v1/check",
            method="POST",
            body={
                "channel": "mail",
                "token": token,
                "subject": "Lunch",
                "body": "Are you free tomorrow?",
            },
        )
        check(
            "mail happy path → ALLOW + slim",
            code == 200
            and isinstance(body, dict)
            and body.get("decision") == "ALLOW"
            and all(k not in body for k in ("quantum", "quantum_line", "spectrum", "hais", "haven2")),
            f"decision={body.get('decision') if isinstance(body, dict) else body}",
        )

        code, body = _http_json(
            f"{base}/v1/check",
            method="POST",
            body={"channel": "algorithm", "token": token, "summary": "no purpose"},
        )
        check(
            "algorithm missing purpose → GOV_INTENT_INVALID",
            code == 200
            and isinstance(body, dict)
            and body.get("decision") == "BLOCK"
            and body.get("error_code") == "GOV_INTENT_INVALID",
        )

        code, body = _http_json(
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
        ok_algo = (
            code == 200
            and isinstance(body, dict)
            and body.get("decision") in ("ALLOW", "BLOCK", "REVIEW")
            and "spectrum" in body
            and "quantum_line" in body
        )
        check(
            "algorithm with purpose → decision + spectrum keys",
            ok_algo,
            f"decision={body.get('decision') if isinstance(body, dict) else body}",
        )

        # malformed JSON
        req = urllib.request.Request(
            f"{base}/v1/check",
            data=b"{not-json",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=10)
            check("malformed JSON → 400", False, "expected HTTPError")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8")
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = {}
            check(
                "malformed JSON → 400",
                exc.code == 400 and payload.get("error") == "bad_request",
            )

    finally:
        httpd.shutdown()
        httpd.server_close()
        eng = getattr(stack, "engine", None)
        storage = getattr(eng, "storage", None) if eng else None
        if storage is not None and hasattr(storage, "close"):
            try:
                storage.close()
            except Exception:
                pass
        tmp.cleanup()

    if failures:
        print(f"\nFAILED ({len(failures)}): {', '.join(failures)}")
        return 1
    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
