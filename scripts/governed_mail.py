#!/usr/bin/env python3
"""CLI gate for outbound mail via GovernedMail (does not send)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (
    ROOT / "src",
    ROOT,
    ROOT / "hais",
    ROOT / "imprint" / "src",
    ROOT / "haven2" / "src",
):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from governed_stack import GovernedMail  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Govern outbound email content (gate only; does not send)."
    )
    parser.add_argument(
        "--to",
        action="append",
        required=True,
        help="Recipient address (repeatable).",
    )
    parser.add_argument("--subject", required=True, help="Email subject.")
    body_group = parser.add_mutually_exclusive_group(required=True)
    body_group.add_argument("--body", help="Email body text.")
    body_group.add_argument(
        "--body-file",
        type=Path,
        help="Path to a file whose contents are the body.",
    )
    parser.add_argument("--user", default="damien")
    parser.add_argument("--role", default="user")
    args = parser.parse_args(argv)

    if args.body_file is not None:
        body = args.body_file.read_text(encoding="utf-8")
    else:
        body = args.body or ""

    try:
        mail = GovernedMail()
        result = mail.check_sync(
            to=args.to,
            subject=args.subject,
            body=body,
            user=args.user,
            role=args.role,
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
        return 2

    print(json.dumps(result, indent=2, default=str))
    if result.get("ok"):
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
