#!/usr/bin/env python3
"""Report decision metrics from a JSON snapshot and/or audit DB GROUP BY.

Offline view works without process memory — reads durable audit_log.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "src", ROOT, ROOT / "hais"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)


def from_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data


def from_audit_db(db_path: Path) -> dict:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT decision, COUNT(*) AS n FROM audit_log GROUP BY decision ORDER BY decision"
    ).fetchall()
    conn.close()
    counts = {str(r["decision"]).upper(): int(r["n"]) for r in rows}
    total = sum(counts.values())
    return {
        "source": "audit_db",
        "db_path": str(db_path),
        "allow": counts.get("ALLOW", 0),
        "review": counts.get("REVIEW", 0),
        "block": counts.get("BLOCK", 0),
        "error": counts.get("ERROR", 0),
        "by_decision": counts,
        "total": total,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Decision metrics report")
    parser.add_argument(
        "--metrics-json",
        type=Path,
        default=None,
        help="Path to DecisionMetrics.snapshot() JSON file.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Audit DB for offline GROUP BY decision counts.",
    )
    parser.add_argument(
        "--prometheus",
        action="store_true",
        help="If --metrics-json given, also print prometheus_text from counters.",
    )
    args = parser.parse_args(argv)

    if not args.metrics_json and not args.db:
        parser.error("provide --metrics-json and/or --db")

    out: dict = {}
    if args.metrics_json:
        if not args.metrics_json.is_file():
            print(f"metrics json not found: {args.metrics_json}", file=sys.stderr)
            return 2
        snap = from_json(args.metrics_json)
        out["snapshot"] = snap
        if args.prometheus:
            from governed_stack.observability import DecisionMetrics

            m = DecisionMetrics()
            # hydrate
            m.allow = int(snap.get("allow", 0))
            m.review = int(snap.get("review", 0))
            m.block = int(snap.get("block", 0))
            m.error = int(snap.get("error", 0))
            m.total = int(snap.get("total", m.allow + m.review + m.block + m.error))
            m.latency_ms_sum = float(snap.get("latency_ms_sum", 0.0))
            m.latency_ms_count = int(snap.get("latency_ms_count", 0))
            m.latency_ms_max = float(snap.get("latency_ms_max", 0.0))
            out["prometheus_text"] = m.prometheus_text()

    if args.db:
        if not args.db.is_file():
            print(f"audit db not found: {args.db}", file=sys.stderr)
            return 2
        out["audit"] = from_audit_db(args.db)

    print(json.dumps(out, indent=2, default=str))
    if args.prometheus and "prometheus_text" in out:
        print(out["prometheus_text"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
