"""
Unified certified governance reference (hais package surface).

Re-exports the full single-file reference from the repo root so imports
work when hais/ is on sys.path. Loads by file path to avoid circular
import when hais/ precedes the repo root on sys.path.

Does not replace certified_governance.py / zk_enhanced_governance.py.
GovernedStack prefers the repo-root unified module when root is ahead
of hais/ on sys.path (see ensure_import_paths).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT_FILE = Path(__file__).resolve().parent.parent / "certified_governance_unified.py"
_ALIAS = "_certified_governance_unified_root"

def _load_root():
    cached = sys.modules.get(_ALIAS)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(_ALIAS, _ROOT_FILE)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load unified module from {_ROOT_FILE}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_ALIAS] = mod
    spec.loader.exec_module(mod)
    return mod

_mod = _load_root()

CertifiedGovernanceEngine = _mod.CertifiedGovernanceEngine
ZKEnhancedGovernanceEngine = _mod.ZKEnhancedGovernanceEngine
ClaimType = _mod.ClaimType
CryptoEngine = _mod.CryptoEngine
AuditStorage = _mod.AuditStorage
PolicyEngine = _mod.PolicyEngine
PolicyRule = _mod.PolicyRule
PolicySpec = _mod.PolicySpec

__all__ = [
    "CertifiedGovernanceEngine",
    "ZKEnhancedGovernanceEngine",
    "ClaimType",
    "CryptoEngine",
    "AuditStorage",
    "PolicyEngine",
    "PolicyRule",
    "PolicySpec",
]
