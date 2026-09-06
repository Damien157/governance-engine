#!/usr/bin/env python3
"""CLI gate for outbound social posts via GovernedPost (does not publish)."""

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

from governed_stack import GovernedPost  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Govern outbound social/post content (gate only; does not publish)."
    )
    parser.add_argument(
        "--platform",
        default="",
        help="Optional platform tag (linkedin, x, …); scanned as subject.",
    )
    body_group = parser.add_mutually_exclusive_group(required=True)
    body_group.add_argument("--body", help="Post body text.")
    body_group.add_argument(
        "--body-file",
        type=Path,
        help="Path to a file whose contents are the body.",
    )
    parser.add_argument(
        "--recipient",
        action="append",
        default=[],
        dest="recipients",
        help="Recipient handle (repeatable; kept out of scanned intent).",
    )
    parser.add_argument(
        "--url",
        action="append",
        default=[],
        dest="urls",
        help="Out-of-band URL (repeatable; kept out of scanned intent).",
    )
    parser.add_argument("--user", default="damien")
    parser.add_argument("--role", default="user")
    args = parser.parse_args(argv)

    if args.body_file is not None:
        body = args.body_file.read_text(encoding="utf-8")
    else:
        body = args.body or ""

    try:
        post = GovernedPost()
        result = post.check_sync(
            text=body,
            platform=args.platform,
            recipients=args.recipients or None,
            urls=args.urls or None,
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
