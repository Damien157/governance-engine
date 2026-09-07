#!/usr/bin/env python3
"""Manual concurrent audit soak (same parameters as tests/test_audit_soak.py).

Usage:
  .venv/bin/python scripts/audit_soak.py
  .venv/bin/python scripts/audit_soak.py --threads 8 --calls 25

Does not send mail/calendar/social. Temp PEM + temp audit DB only.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "src"),
    str(ROOT),
    str(ROOT / "hais"),
    str(ROOT / "imprint" / "src"),
    str(ROOT / "haven2" / "src"),
]

from certified_governance_unified import CryptoEngine  # noqa: E402
from governed_stack import GovernedMail, GovernedStack  # noqa: E402


def _close(stack: GovernedStack) -> None:
    storage = getattr(getattr(stack, "engine", None), "storage", None)
    if storage is not None and hasattr(storage, "close"):
        try:
            storage.close()
        except Exception:
            pass


def run_soak(n_threads: int, n_calls: int) -> int:
    total = n_threads * n_calls
    with tempfile.TemporaryDirectory(prefix="audit_soak_manual_") as td:
        db = str(Path(td) / "soak.db")
        key = str(Path(td) / "signing_key.pem")
        crypto = CryptoEngine(private_key_path=key)
        stack = GovernedStack(
            config={
                "db_path": db,
                "signing_key_path": key,
                "log_level": 50,
                "cache_ttl_seconds": 0,
            },
            crypto=crypto,
        )
        mail = GovernedMail(stack=stack)
        token = stack.issue_token("soak_user", "operator")

        errors: List[str] = []
        results: List[Tuple[str, Optional[str]]] = []
        lock = threading.Lock()

        def worker(tid: int) -> None:
            try:
                for i in range(n_calls):
                    if i % 5 == 4:
                        body = (
                            f"contact soak{tid}_{i}@example.com"
                            if i % 4 == 0
                            else f"Are you free for lunch {tid}-{i}?"
                        )
                        env = mail.check_sync(
                            to="ops@example.com",
                            subject=f"Soak {tid}/{i}",
                            body=body,
                            user="soak_user",
                            role="operator",
                        )
                    else:
                        if i % 4 == 0:
                            intent: Dict[str, Any] = {
                                "action": "query",
                                "payload": {
                                    "text": f"reach me at soak{tid}_{i}@example.com please"
                                },
                                "telemetry": {},
                                "nonce": f"block-{tid}-{i}",
                            }
                        else:
                            intent = {
                                "action": "query",
                                "payload": {
                                    "text": f"hello clean lunch plan thread={tid} call={i}"
                                },
                                "telemetry": {},
                                "nonce": f"allow-{tid}-{i}",
                            }
                        env = asyncio.run(stack.govern(intent, token))
                    decision = env.get("decision")
                    if decision not in ("ALLOW", "BLOCK", "REVIEW"):
                        raise AssertionError(f"bad decision: {decision!r}")
                    with lock:
                        results.append((str(decision), env.get("entry_id")))
            except Exception as exc:  # noqa: BLE001
                with lock:
                    errors.append(f"thread-{tid}: {type(exc).__name__}: {exc}")
            finally:
                _close(stack)

        t0 = time.time()
        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed = time.time() - t0

        snap = stack.metrics.snapshot()
        verify = stack.engine.storage.verify_chain()
        _close(stack)

        print(f"threads={n_threads} calls/thread={n_calls} total={total}")
        print(f"elapsed_s={elapsed:.2f}")
        print(f"errors={errors or 'none'}")
        print(f"results={len(results)} metrics_total={snap.get('total')}")
        print(f"metrics={snap}")
        print(f"verify={verify}")
        ok = (
            not errors
            and len(results) == total
            and snap.get("total") == total
            and verify.get("valid") is True
            and verify.get("entries_checked") == total
        )
        print(f"OK={ok}")
        return 0 if ok else 1


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--threads", type=int, default=8)
    p.add_argument("--calls", type=int, default=25)
    args = p.parse_args()
    return run_soak(args.threads, args.calls)


if __name__ == "__main__":
    raise SystemExit(main())
