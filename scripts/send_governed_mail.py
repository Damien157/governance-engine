#!/usr/bin/env python3
"""
Stub outbound send path: gate with GovernedMail.require_allow, then print
"would send". Does NOT call Gmail or any network mail API.

Agents must call GovernedMail.check / require_allow first; only on ALLOW
may they invoke Gmail MCP send_message / reply / forward / create_draft.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (
    ROOT / "src",
    ROOT / "hais",
    ROOT / "imprint" / "src",
    ROOT / "haven2" / "src",
):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from governed_stack import GovernedMail, SendBlocked  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Gate outbound email then print 'would send' (no Gmail API). "
            "Agents must check first; only ALLOW may proceed to real send."
        )
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

    mail = GovernedMail()
    try:
        result = mail.require_allow_sync(
            to=args.to,
            subject=args.subject,
            body=body,
            user=args.user,
            role=args.role,
        )
    except SendBlocked as exc:
        print(json.dumps(exc.result, indent=2, default=str))
        print("refused: not ALLOW — do not call Gmail send/draft", file=sys.stderr)
        return 1
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
        return 2

    print(json.dumps(result, indent=2, default=str))
    print("would send")
    print(
        "NOTE: this stub does not call Gmail. Agents must only call "
        "Gmail send_message/reply/forward/create_draft after ALLOW.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
