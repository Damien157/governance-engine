#!/usr/bin/env python3
"""
Sealed-mail demo: run inputs through SealedMailAdapter with the local spool
backend (no credentials, no network). ALLOW drafts land as JSON handoff files
under --outbox; BLOCK/REVIEW write nothing.

A host that owns a Gmail connector may then create the draft from a spooled
file *verbatim* (verify content_sha256). This script never sends mail.

    .venv/bin/python scripts/sealed_mail_demo.py --to me@example.com \
        --subject "[governance-engine] sealed-path test draft" \
        --body "Gated test draft." [--body "second input" ...]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "haven2" / "src", ROOT / "imprint" / "src", ROOT / "hais", ROOT, ROOT / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from governed_stack.sealed_mail import SealedMailAdapter, spool_backend  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--to", required=True, action="append")
    ap.add_argument("--subject", required=True)
    ap.add_argument("--body", required=True, action="append", help="repeat for several inputs")
    ap.add_argument("--outbox", default=str(ROOT / "artifacts" / "sealed_mail" / "outbox"))
    args = ap.parse_args(argv)

    adapter = SealedMailAdapter(spool_backend(args.outbox), recipient_allowlist=args.to)
    results = []
    for body in args.body:
        out = adapter.propose_draft_sync(to=args.to, subject=args.subject, body=body)
        results.append(
            {
                "body": body,
                "decision": out["decision"],
                "stage": out["stage"],
                "written": out["written"],
                "reasons": out["reasons"],
                "entry_id": out.get("entry_id"),
                "spooled": (out.get("backend_result") or {}).get("spooled"),
                "content_sha256": (out.get("draft") or {}).get("content_sha256"),
            }
        )
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
