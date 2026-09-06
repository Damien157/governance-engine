#!/usr/bin/env python3
"""Run the customer-ops HTTP sidecar (check-only gate).

Env (see config/customer.env.example / docs/CUSTOMER_OPS.md):
  GOVERNANCE_DB_PATH, GOVERNANCE_SIGNING_KEY_PATH,
  GOVERNANCE_HOST (default 127.0.0.1), GOVERNANCE_PORT (default 8080),
  GOVERNANCE_API_KEY (optional; if set, required on /v1/*),
  GOVERNANCE_REQUIRE_PERSISTED_KEY=1 (production posture).

Never sends mail/calendar/social.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "src", ROOT, ROOT / "hais", ROOT / "imprint" / "src", ROOT / "haven2" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from governed_stack.sidecar import (  # noqa: E402
    DEFAULT_HOST,
    DEFAULT_PORT,
    load_sidecar_config,
    serve_forever,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Governed stack customer sidecar")
    parser.add_argument("--host", default=None, help="bind host (env GOVERNANCE_HOST)")
    parser.add_argument("--port", type=int, default=None, help="bind port (env GOVERNANCE_PORT)")
    parser.add_argument("--db", default=None, help="audit db path (env GOVERNANCE_DB_PATH)")
    parser.add_argument(
        "--key",
        default=None,
        help="signing key PEM path (env GOVERNANCE_SIGNING_KEY_PATH)",
    )
    args = parser.parse_args()
    cfg = load_sidecar_config(
        db_path=args.db,
        signing_key_path=args.key,
        host=args.host,
        port=args.port,
    )
    # Fill CLI defaults only when env/args omitted.
    if args.host is None and not cfg.get("host"):
        cfg["host"] = DEFAULT_HOST
    if args.port is None and cfg.get("port") is None:
        cfg["port"] = DEFAULT_PORT
    serve_forever(config=cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
