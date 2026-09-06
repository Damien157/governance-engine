#!/usr/bin/env python3
"""
Read-only audit report for durable mail/calendar/social audit DBs.

Prints storage.stats plus the last N decisions (decision, reasons, entry_id).
Uses CertifiedGovernanceEngine / AuditStorage from certified_governance_unified.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "src", ROOT, ROOT / "hais"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from certified_governance_unified import (  # noqa: E402
    AuditStorage,
    CryptoEngine,
)


def _default_db() -> Path:
    mail = ROOT / "artifacts" / "mail" / "audit.db"
    if mail.is_file():
        return mail
    cal = ROOT / "artifacts" / "calendar" / "audit.db"
    if cal.is_file():
        return cal
    social = ROOT / "artifacts" / "social" / "audit.db"
    if social.is_file():
        return social
    return mail  # default path even if missing (caller gets clear error)


def _sibling_key(db_path: Path) -> Path:
    return db_path.with_name("signing_key.pem")


def last_decisions(storage: AuditStorage, n: int) -> list[dict]:
    cur = storage.conn.cursor()
    rows = cur.execute(
        """
        SELECT id, timestamp, decision, policy_reasons, result
        FROM audit_log
        ORDER BY timestamp DESC
        LIMIT ?
        """,
        (n,),
    ).fetchall()
    out = []
    for r in rows:
        reasons = r["policy_reasons"]
        try:
            reasons = json.loads(reasons) if reasons else []
        except Exception:
            reasons = [reasons] if reasons else []
        out.append(
            {
                "entry_id": r["id"],
                "decision": r["decision"],
                "reasons": reasons,
                "result": r["result"],
                "timestamp": r["timestamp"],
            }
        )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only audit stats + last N decisions from a durable audit DB."
    )
    parser.add_argument(
        "db_path",
        nargs="?",
        type=Path,
        default=None,
        help="Path to audit.db (default: artifacts/mail/audit.db if present).",
    )
    parser.add_argument("-n", "--last", type=int, default=10, help="Last N decisions (default 10).")
    parser.add_argument(
        "--hours",
        type=int,
        default=24,
        help="Hours window for storage.stats (default 24).",
    )
    parser.add_argument(
        "--key",
        type=Path,
        default=None,
        help="Optional signing key PEM (default: sibling signing_key.pem).",
    )
    args = parser.parse_args(argv)

    db_path = args.db_path or _default_db()
    if not db_path.is_file():
        print(
            json.dumps(
                {"ok": False, "error": f"audit db not found: {db_path}"},
                indent=2,
            ),
            file=sys.stderr,
        )
        return 2

    key_path = args.key or _sibling_key(db_path)
    # Read-only report: load existing key when present; otherwise ephemeral
    # (stats / SELECT do not need a matching key).
    crypto = CryptoEngine(
        private_key_path=str(key_path) if key_path.is_file() else None
    )
    storage = AuditStorage(str(db_path), crypto)

    stats = storage.stats(hours=args.hours)
    decisions = last_decisions(storage, args.last)

    report = {
        "ok": True,
        "db_path": str(db_path),
        "stats_hours": args.hours,
        "stats": stats,
        "last_n": args.last,
        "decisions": decisions,
    }
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
