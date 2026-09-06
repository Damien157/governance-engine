#!/usr/bin/env python3
"""Verify hash-chained audit log signatures (cron-friendly).

Exit 0 on OK, 1 on FAIL, 2 on usage/missing DB.
Bootstrap matches audit_report.py / review_ops.py.
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

from certified_governance_unified import AuditStorage, CryptoEngine  # noqa: E402


def _default_db() -> Path:
    for rel in (
        "artifacts/mail/audit.db",
        "artifacts/calendar/audit.db",
        "artifacts/social/audit.db",
    ):
        cand = ROOT / rel
        if cand.is_file():
            return cand
    return ROOT / "artifacts" / "mail" / "audit.db"


def _sibling_key(db_path: Path) -> Path:
    return db_path.with_name("signing_key.pem")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify audit chain integrity (hash + RSA-PSS signatures)."
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Path to audit.db (default: artifacts/mail/audit.db if present).",
    )
    parser.add_argument(
        "--key",
        type=Path,
        default=None,
        help="Signing key PEM (default: sibling signing_key.pem).",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON result.")
    args = parser.parse_args(argv)

    db_path = args.db or _default_db()
    if not db_path.is_file():
        msg = {"ok": False, "error": f"audit db not found: {db_path}"}
        print(json.dumps(msg, indent=2) if args.json else f"FAIL: {msg['error']}", file=sys.stderr)
        return 2

    key_path = args.key or _sibling_key(db_path)
    if not key_path.is_file():
        msg = {"ok": False, "error": f"signing key not found: {key_path}"}
        print(json.dumps(msg, indent=2) if args.json else f"FAIL: {msg['error']}", file=sys.stderr)
        return 2

    crypto = CryptoEngine(private_key_path=str(key_path))
    storage = AuditStorage(str(db_path), crypto)
    result = storage.verify_chain()
    ok = bool(result.get("valid"))
    payload = {
        "ok": ok,
        "db_path": str(db_path),
        "key_path": str(key_path),
        **result,
    }
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        status = "OK" if ok else "FAIL"
        print(
            f"{status}: entries={result.get('entries_checked')} "
            f"broken={len(result.get('broken_entries') or [])} "
            f"db={db_path}"
        )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
