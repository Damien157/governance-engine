#!/usr/bin/env python3
"""CLI gate for calendar writes via GovernedCalendar (does not create events)."""

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

from governed_stack import GovernedCalendar  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Govern calendar write content (gate only; does not create events)."
    )
    parser.add_argument("--summary", required=True, help="Event summary/title.")
    parser.add_argument("--description", default="", help="Event description.")
    parser.add_argument("--location", default="", help="Event location.")
    parser.add_argument(
        "--attendee",
        action="append",
        default=[],
        dest="attendees",
        help="Attendee email (repeatable; kept out of scanned intent).",
    )
    parser.add_argument("--start", default=None, help="Start time (metadata only).")
    parser.add_argument("--end", default=None, help="End time (metadata only).")
    parser.add_argument("--user", default="damien")
    parser.add_argument("--role", default="user")
    args = parser.parse_args(argv)

    try:
        cal = GovernedCalendar()
        result = cal.check_sync(
            summary=args.summary,
            description=args.description,
            location=args.location,
            start=args.start,
            end=args.end,
            attendees=args.attendees or None,
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
