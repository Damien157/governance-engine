"""
Thin re-export: GovernedDecisionEngine from the live governed_stack bridge.

Sketch teaching kernel remains hais_governance_unified_runtime_v02.py.
"""

from __future__ import annotations

from governed_stack.stack import ensure_import_paths

ensure_import_paths()

from governed_stack.runtime_bridge import GovernedDecisionEngine  # noqa: E402

__all__ = ["GovernedDecisionEngine"]
