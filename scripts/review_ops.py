#!/usr/bin/env python3
"""
Human operator CLI for pending REVIEW items in a durable audit DB.

Subcommands: list | approve | deny | voucher
Never sends mail/calendar — mutations are audit/review-queue only.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "src", ROOT, ROOT / "hais"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from certified_governance_unified import (  # noqa: E402
    CryptoEngine,
    ZKEnhancedGovernanceEngine,
)


def _default_db() -> Path:
    return ROOT / "artifacts" / "mail" / "audit.db"


def _sibling_key(db_path: Path) -> Path:
    return db_path.with_name("signing_key.pem")


def bootstrap_engine(
    db_path: Path,
    key_path: Optional[Path] = None,
) -> ZKEnhancedGovernanceEngine:
    """
    Load CryptoEngine from sibling signing_key.pem (or --key) and construct
    ZKEnhancedGovernanceEngine against db_path (same pattern as audit_report.py).
    """
    key = key_path or _sibling_key(db_path)
    crypto = CryptoEngine(private_key_path=str(key) if key.is_file() else None)
    return ZKEnhancedGovernanceEngine(
        config={
            "db_path": str(db_path),
            "signing_key_path": str(key) if key.is_file() else None,
            "log_level": 40,
        },
        crypto=crypto,
    )


def recover_intent_from_entry(engine: Any, entry_id: str) -> Optional[Dict[str, Any]]:
    """Decode the (already-redacted) intent from audit_log.intent_envelope."""
    cur = engine.storage.conn.cursor()
    row = cur.execute(
        "SELECT intent_envelope FROM audit_log WHERE id = ?",
        (entry_id,),
    ).fetchone()
    if row is None or not row["intent_envelope"]:
        return None
    try:
        envelope = json.loads(row["intent_envelope"])
        return json.loads(base64.b64decode(envelope["data"]).decode())
    except Exception:
        return None


def format_pending_preview(entry: Dict[str, Any]) -> str:
    entry_id = entry.get("entry_id", "?")
    created = entry.get("queued_at") or entry.get("timestamp") or entry.get("created_at")
    reasons = entry.get("policy_reasons") or entry.get("reasons") or []
    if isinstance(reasons, str):
        reasons_preview = reasons[:80]
    else:
        reasons_preview = "; ".join(str(r) for r in reasons)[:80]
    decision = entry.get("decision", "REVIEW")
    return (
        f"{entry_id}\tcreated_at={created}\tdecision={decision}\t"
        f"reasons={reasons_preview!r}"
    )


def cmd_list(engine: Any, as_json: bool = False, limit: int = 50) -> int:
    pending: List[Dict[str, Any]] = engine.list_pending_reviews(limit=limit)
    # Normalize created_at for display / JSON consumers
    for e in pending:
        if "created_at" not in e:
            e["created_at"] = e.get("queued_at") or e.get("timestamp")
        e.setdefault("decision", "REVIEW")
        if "reasons" not in e and "policy_reasons" in e:
            e["reasons"] = e["policy_reasons"]
    if as_json:
        print(json.dumps({"ok": True, "pending": pending, "count": len(pending)}, indent=2, default=str))
    else:
        if not pending:
            print("(no pending reviews)")
        else:
            for e in pending:
                print(format_pending_preview(e))
            print(f"total: {len(pending)}")
    return 0


def cmd_approve(
    engine: Any,
    entry_id: str,
    resolved_by: str,
    notes: str = "",
) -> int:
    result = engine.resolve_review(
        entry_id, resolved_by=resolved_by, approve=True, notes=notes
    )
    print(json.dumps(result, indent=2, default=str))
    return 0


def cmd_deny(
    engine: Any,
    entry_id: str,
    resolved_by: str,
    notes: str = "",
) -> int:
    result = engine.resolve_review(
        entry_id, resolved_by=resolved_by, approve=False, notes=notes
    )
    print(json.dumps(result, indent=2, default=str))
    return 0


def cmd_voucher(
    engine: Any,
    entry_id: str,
    resolved_by: str,
    ttl_seconds: int = 3600,
) -> int:
    intent = recover_intent_from_entry(engine, entry_id)
    if intent is None:
        print(
            f"intent not recoverable from audit entry {entry_id}; cannot issue voucher",
            file=sys.stderr,
        )
        return 1
    intent_hash = hashlib.sha256(
        json.dumps(intent, sort_keys=True).encode()
    ).hexdigest()
    token = engine.security.issue_approval_voucher(
        entry_id=entry_id,
        intent_hash=intent_hash,
        reviewer=resolved_by,
        ttl_seconds=ttl_seconds,
    )
    print("WARNING: approval voucher is single-use; store securely.", file=sys.stderr)
    print(token)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Operator tooling for pending REVIEW items (durable audit DB)."
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Audit DB path (default: artifacts/mail/audit.db).",
    )
    parser.add_argument(
        "--key",
        type=Path,
        default=None,
        help="Signing key PEM (default: sibling signing_key.pem next to --db).",
    )
    parser.add_argument(
        "--resolved-by",
        default="damien",
        help="Reviewer identity for approve/deny/voucher (default: damien).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="List pending REVIEW entries.")
    p_list.add_argument("--json", action="store_true", help="Emit JSON.")
    p_list.add_argument("-n", "--limit", type=int, default=50, help="Max rows.")

    p_approve = sub.add_parser("approve", help="Approve a pending REVIEW.")
    p_approve.add_argument("entry_id")
    p_approve.add_argument("--notes", default="", help="Optional resolution notes.")

    p_deny = sub.add_parser("deny", help="Deny a pending REVIEW (→ BLOCK).")
    p_deny.add_argument("entry_id")
    p_deny.add_argument("--notes", default="", help="Optional resolution notes.")

    p_voucher = sub.add_parser(
        "voucher",
        help="Issue a single-use approval voucher if intent is recoverable.",
    )
    p_voucher.add_argument("entry_id")
    p_voucher.add_argument(
        "--ttl",
        type=int,
        default=3600,
        help="Voucher TTL seconds (default 3600).",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    db_path = args.db or _default_db()
    if not db_path.is_file() and args.command == "list":
        # list on missing DB: clear error
        print(
            json.dumps({"ok": False, "error": f"audit db not found: {db_path}"}),
            file=sys.stderr,
        )
        return 2

    engine = bootstrap_engine(db_path, key_path=args.key)
    try:
        if args.command == "list":
            return cmd_list(engine, as_json=args.json, limit=args.limit)
        if args.command == "approve":
            return cmd_approve(
                engine, args.entry_id, resolved_by=args.resolved_by, notes=args.notes
            )
        if args.command == "deny":
            return cmd_deny(
                engine, args.entry_id, resolved_by=args.resolved_by, notes=args.notes
            )
        if args.command == "voucher":
            return cmd_voucher(
                engine,
                args.entry_id,
                resolved_by=args.resolved_by,
                ttl_seconds=args.ttl,
            )
        parser.error(f"unknown command: {args.command}")
        return 2
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        try:
            engine.storage.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
