#!/usr/bin/env python3
"""
AST / text scan: forbid direct outbound connector calls outside allowlisted paths.

Fails (exit 1) if forbidden patterns appear in scanned sources. Real Gmail /
Calendar / social SDK writes must only run inside bus side_effects
(``connectors.py`` factories / mocks) or tests/docs.

Allowlist (relative to repo root):
  - tests/
  - src/governed_stack/connectors.py
  - src/governed_stack/action_bus.py
  - docs/
  - scripts/lint_no_bypass.py (this file)
  - scripts/send_governed_mail.py (documents forbidden symbols in strings)
  - src/governed_stack/AGENT_*.md
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]

# Substrings / call shapes that must not appear outside allowlisted paths.
FORBIDDEN_SUBSTRINGS: Tuple[str, ...] = (
    "send_message(",
    "gmail.users().messages().send",
    "messages().send(",
    "users().messages().send",
    "create_draft(",
    "tweets.create",
    "tweet.create",
    "statuses/update",
    "publish_tweet(",
    "create_tweet(",
    "call_tool(",
    "invoke_tool(",
)

# AST Call attribute names treated as forbidden when they match these.
FORBIDDEN_ATTR_CALLS: frozenset[str] = frozenset(
    {
        "send_message",
        "create_draft",
        "publish_tweet",
        "create_tweet",
        "call_tool",
        "invoke_tool",
    }
)

ALLOWLIST_PREFIXES: Tuple[str, ...] = (
    "tests/",
    "docs/",
    "src/governed_stack/connectors.py",
    "src/governed_stack/tool.py",
    "src/governed_stack/action_bus.py",
    "scripts/lint_no_bypass.py",
    "scripts/send_governed_mail.py",
    "src/governed_stack/AGENT_MAIL.md",
    "src/governed_stack/AGENT_CALENDAR.md",
    "src/governed_stack/AGENT_SOCIAL.md",
    "src/governed_stack/AGENT_ALGORITHM.md",
    "docs/AGENT_MANDATES.md",
)

SCAN_GLOBS: Tuple[str, ...] = (
    "src/governed_stack/**/*.py",
    "scripts/**/*.py",
    "haven2/src/**/*.py",
    "hais/**/*.py",
)

SKIP_DIR_PARTS: frozenset[str] = frozenset(
    {".venv", "__pycache__", ".mypy_cache", ".ruff_cache", ".git", "node_modules"}
)


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def is_allowlisted(rel: str) -> bool:
    for prefix in ALLOWLIST_PREFIXES:
        if rel == prefix or rel.startswith(prefix):
            return True
    return False


def iter_scan_files(root: Path = ROOT) -> List[Path]:
    found: List[Path] = []
    for pattern in SCAN_GLOBS:
        for path in root.glob(pattern):
            if not path.is_file():
                continue
            if any(part in SKIP_DIR_PARTS for part in path.parts):
                continue
            found.append(path)
    # Deduplicate while preserving order
    seen = set()
    out: List[Path] = []
    for p in found:
        key = p.resolve()
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _scan_text(rel: str, text: str) -> List[str]:
    hits: List[str] = []
    for i, line in enumerate(text.splitlines(), start=1):
        # Skip pure comments that document the ban (still catch code).
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        for pat in FORBIDDEN_SUBSTRINGS:
            if pat in line:
                hits.append(f"{rel}:{i}: forbidden substring {pat!r}")
    return hits


class _CallVisitor(ast.NodeVisitor):
    def __init__(self, rel: str) -> None:
        self.rel = rel
        self.hits: List[str] = []

    def visit_Call(self, node: ast.Call) -> None:
        name: str | None = None
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
        if name and name in FORBIDDEN_ATTR_CALLS:
            self.hits.append(
                f"{self.rel}:{getattr(node, 'lineno', '?')}: "
                f"forbidden call {name}("
            )
        self.generic_visit(node)


def _scan_ast(rel: str, text: str) -> List[str]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    visitor = _CallVisitor(rel)
    visitor.visit(tree)
    return visitor.hits


def lint_paths(paths: Iterable[Path]) -> List[str]:
    violations: List[str] = []
    for path in paths:
        rel = _rel(path)
        if is_allowlisted(rel):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            violations.append(f"{rel}: cannot read: {exc}")
            continue
        violations.extend(_scan_text(rel, text))
        if path.suffix == ".py":
            violations.extend(_scan_ast(rel, text))
    return violations


def self_check() -> int:
    """Unit self-check: allowlisted path ignored; synthetic hit detected."""
    # 1) This script itself is allowlisted.
    assert is_allowlisted("scripts/lint_no_bypass.py")
    assert is_allowlisted("tests/test_foo.py")
    assert is_allowlisted("src/governed_stack/connectors.py")
    assert not is_allowlisted("src/governed_stack/mail.py")

    # 2) Synthetic snippet with forbidden pattern is detected.
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        bad = Path(td) / "evil.py"
        bad.write_text(
            "def go():\n    send_message(to='x')\n",
            encoding="utf-8",
        )
        # Temporarily treat as if under src/governed_stack (not allowlisted name)
        # by scanning via lint_paths with a monkeypatched relative path:
        hits = _scan_text("src/governed_stack/evil.py", bad.read_text(encoding="utf-8"))
        hits += _scan_ast("src/governed_stack/evil.py", bad.read_text(encoding="utf-8"))
        assert any("send_message" in h for h in hits), hits

    # 3) Clean snippet has no hits.
    clean = "def go():\n    return 1\n"
    assert _scan_text("src/governed_stack/clean.py", clean) == []
    assert _scan_ast("src/governed_stack/clean.py", clean) == []

    print("lint_no_bypass self-check: OK")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="Run built-in unit self-check and exit.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Optional paths to scan (default: package scan globs).",
    )
    args = parser.parse_args(argv)

    if args.self_check:
        return self_check()

    if args.paths:
        paths = [Path(p) for p in args.paths]
    else:
        paths = iter_scan_files(ROOT)

    violations = lint_paths(paths)
    if violations:
        print("lint_no_bypass: FAILED — direct connector bypass patterns found:", file=sys.stderr)
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        return 1
    print(f"lint_no_bypass: OK ({len(list(paths))} files scanned)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
