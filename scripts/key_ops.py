#!/usr/bin/env python3
"""Signing key operations: generate / public / rotate / check.

Uses LocalPEMKeyProvider + RotatingKeyProvider (local; not a cloud KMS SDK).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "src", ROOT, ROOT / "hais"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from governed_stack.key_providers import (  # noqa: E402
    LocalPEMKeyProvider,
    RotatingKeyProvider,
)


def cmd_generate(path: Path) -> int:
    if path.is_file():
        print(f"exists: {path}")
        return 0
    prov = LocalPEMKeyProvider(str(path), require_persisted_key=True)
    print(f"generated: {path} key_id={prov.key_id}")
    return 0


def cmd_public(path: Path) -> int:
    if not path.is_file():
        print(f"missing: {path}", file=sys.stderr)
        return 1
    prov = LocalPEMKeyProvider(str(path))
    sys.stdout.buffer.write(prov.public_pem)
    if not prov.public_pem.endswith(b"\n"):
        sys.stdout.buffer.write(b"\n")
    return 0


def cmd_rotate(path: Path) -> int:
    # Ensure an active key exists first.
    if not path.is_file():
        LocalPEMKeyProvider(str(path), require_persisted_key=True)
    rotating = RotatingKeyProvider(private_key_path=str(path))
    new_id = rotating.rotate(str(path))
    print(f"rotated: path={path} new_key_id={new_id}")
    sidecar = path.with_name("signing_keys.json")
    print(f"sidecar: {sidecar}")
    return 0


def cmd_check(path: Path) -> int:
    if not path.is_file():
        print(f"FAIL: missing {path}", file=sys.stderr)
        return 1
    prov = LocalPEMKeyProvider(str(path))
    msg = b"governance-key-ops-roundtrip"
    sig = prov.sign(msg)
    ok = prov.verify(msg, sig)
    # JWT/PKCS1 path
    sig2 = prov.sign_pkcs1(msg)
    ok2 = len(sig2) > 0
    if ok and ok2:
        print(f"OK: {path} key_id={prov.key_id} sign/verify roundtrip")
        return 0
    print("FAIL: sign/verify roundtrip", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Signing key ops for governed stack")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_gen = sub.add_parser("generate", help="Create PEM 0600 if missing")
    p_gen.add_argument("--path", type=Path, required=True)

    p_pub = sub.add_parser("public", help="Print public PEM")
    p_pub.add_argument("--path", type=Path, required=True)

    p_rot = sub.add_parser("rotate", help="Rotate via RotatingKeyProvider")
    p_rot.add_argument("--path", type=Path, required=True)

    p_chk = sub.add_parser("check", help="Verify file exists + sign/verify")
    p_chk.add_argument("--path", type=Path, required=True)

    args = parser.parse_args(argv)
    if args.cmd == "generate":
        return cmd_generate(args.path)
    if args.cmd == "public":
        return cmd_public(args.path)
    if args.cmd == "rotate":
        return cmd_rotate(args.path)
    if args.cmd == "check":
        return cmd_check(args.path)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
