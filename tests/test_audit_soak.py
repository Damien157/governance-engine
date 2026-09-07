"""Concurrent audit soak — prove the live gate under parallel govern()/check load.

SQLite WAL expectations (AuditStorage):
  - PRAGMA journal_mode=WAL + busy_timeout; per-thread connections via threading.local
  - Hash-chain integrity needs a **single logical writer**: ``_chain_lock`` is held
    across tip read → monotonic timestamp → hash/sign → INSERT → tip update.
    (0.4.3: timestamp must be assigned under that lock; assigning it before the
    lock let link order diverge from ``verify_chain`` timestamp order.)
  - Multi-process concurrent writers to one audit DB are not supported for a
    valid hash chain — use one process (or an external single-writer queue).
  - Identical intents may hit GovernanceCache (TTL) and skip a new audit row —
    this soak uses unique nonces so each call writes a chain entry
  - Call AuditStorage.close() after the soak to release the thread-local
    connection and avoid ResourceWarnings on temp DB teardown
  - WAL leaves ``*-wal`` / ``*-shm`` beside the DB until checkpoint/close;
    TemporaryDirectory cleanup removes them with the parent dir

CI target: keep wall time under ~15–20s (default 8 threads × 25 calls).
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
_PATHS = (
    ROOT / "src",
    ROOT,
    ROOT / "hais",
    ROOT / "imprint" / "src",
    ROOT / "haven2" / "src",
)
for p in reversed(_PATHS):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from certified_governance_unified import CryptoEngine  # noqa: E402
from governed_stack import GovernedMail, GovernedStack  # noqa: E402

# Tunable soak size — keep CI under ~20s on this box.
N_THREADS = 8
N_CALLS = 25
TOTAL_CALLS = N_THREADS * N_CALLS


def _close_stack_storage(stack: GovernedStack) -> None:
    eng = getattr(stack, "engine", None)
    if eng is None:
        return
    storage = getattr(eng, "storage", None)
    if storage is not None and hasattr(storage, "close"):
        try:
            storage.close()
        except Exception:
            pass


def _allow_intent(tid: int, i: int) -> Dict[str, Any]:
    return {
        "action": "query",
        "payload": {"text": f"hello clean lunch plan thread={tid} call={i}"},
        "telemetry": {},
        "nonce": f"allow-{tid}-{i}",
    }


def _block_intent(tid: int, i: int) -> Dict[str, Any]:
    return {
        "action": "query",
        "payload": {"text": f"reach me at soak{tid}_{i}@example.com please"},
        "telemetry": {},
        "nonce": f"block-{tid}-{i}",
    }


class TestAuditSoak(unittest.TestCase):
    """Parallel govern() + mail check() against one temp stack/audit DB."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._t0 = time.time()
        # Shared RSA key — keygen once (same pattern as other live tests).
        cls.shared_crypto = CryptoEngine(private_key_path=None)

    @classmethod
    def tearDownClass(cls) -> None:
        elapsed = time.time() - cls._t0
        print(f"\n  [TestAuditSoak] class total: {elapsed:.2f}s")

    def test_concurrent_govern_and_mail_check_preserves_audit_chain(self) -> None:
        with tempfile.TemporaryDirectory(prefix="audit_soak_") as td:
            db = str(Path(td) / "soak.db")
            key = str(Path(td) / "signing_key.pem")
            stack = GovernedStack(
                config={
                    "db_path": db,
                    "signing_key_path": key,
                    "log_level": 50,
                    # Prefer unique nonces; TTL 0 still helps if a key collides.
                    "cache_ttl_seconds": 0,
                },
                crypto=self.shared_crypto,
            )
            mail = GovernedMail(stack=stack)
            token = stack.issue_token("soak_user", "operator")

            errors: List[str] = []
            results: List[Tuple[str, Optional[str]]] = []
            lock = threading.Lock()

            def worker(tid: int) -> None:
                try:
                    for i in range(N_CALLS):
                        # Mix: most via govern(); every 5th via mail check_sync.
                        if i % 5 == 4:
                            subject = f"Soak {tid}/{i}"
                            if i % 4 == 0:
                                body = f"contact soak{tid}_{i}@example.com"
                            else:
                                body = f"Are you free for lunch {tid}-{i}?"
                            env = mail.check_sync(
                                to="ops@example.com",
                                subject=subject,
                                body=body,
                                user="soak_user",
                                role="operator",
                            )
                        else:
                            intent = (
                                _block_intent(tid, i)
                                if i % 4 == 0
                                else _allow_intent(tid, i)
                            )
                            env = asyncio.run(stack.govern(intent, token))

                        decision = env.get("decision")
                        if decision not in ("ALLOW", "BLOCK", "REVIEW"):
                            raise AssertionError(f"bad decision: {decision!r}")
                        with lock:
                            results.append((str(decision), env.get("entry_id")))
                except Exception as exc:  # noqa: BLE001 — collect for main thread
                    with lock:
                        errors.append(f"thread-{tid}: {type(exc).__name__}: {exc}")
                finally:
                    # Release this thread's WAL connection.
                    _close_stack_storage(stack)

            t0 = time.time()
            threads = [
                threading.Thread(target=worker, args=(t,), name=f"soak-{t}")
                for t in range(N_THREADS)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            elapsed = time.time() - t0

            self.assertEqual(errors, [], msg="; ".join(errors))
            self.assertEqual(len(results), TOTAL_CALLS)

            snap = stack.metrics.snapshot()
            self.assertEqual(
                snap["total"],
                TOTAL_CALLS,
                msg=f"metrics total {snap['total']} != {TOTAL_CALLS}",
            )
            self.assertEqual(
                snap["allow"] + snap["block"] + snap["review"] + snap["error"],
                TOTAL_CALLS,
            )
            self.assertEqual(snap["error"], 0)

            # verify_chain on main-thread connection after workers closed.
            verify = stack.engine.storage.verify_chain()
            self.assertTrue(verify.get("valid"), msg=repr(verify))
            self.assertEqual(verify.get("broken_entries"), [])
            # Unique intents → one audit row per call (cache_ttl=0 + nonces).
            self.assertEqual(
                verify.get("entries_checked"),
                TOTAL_CALLS,
                msg=f"audit entries {verify.get('entries_checked')} != {TOTAL_CALLS}",
            )

            # Checkpoint WAL then close main-thread connection before tempdir rm.
            try:
                stack.engine.storage.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                stack.engine.storage.conn.commit()
            except Exception:
                pass
            _close_stack_storage(stack)

            # Soft budget for CI (hard assert only if wildly over — box load varies).
            self.assertLess(
                elapsed,
                45.0,
                msg=f"soak took {elapsed:.1f}s (expected <~20s; hard cap 45s)",
            )
            print(
                f"\n  [soak] threads={N_THREADS} calls/thread={N_CALLS} "
                f"total={TOTAL_CALLS} elapsed={elapsed:.2f}s "
                f"metrics={snap} verify_entries={verify.get('entries_checked')}"
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
