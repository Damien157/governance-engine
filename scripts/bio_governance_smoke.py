#!/usr/bin/env python3
"""Local smoke for BioGovernance OS (no network, check-only)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from governed_stack.bio import GovernedBio  # noqa: E402


def main() -> int:
    bio = GovernedBio()
    cases = [
        dict(
            purpose="literature review on senescence markers",
            domain="aging",
            intervention_class="literature",
        ),
        dict(
            purpose="in vivo aging intervention metadata",
            domain="aging",
            intervention_class="in_vivo_declared",
            irreversible=True,
        ),
        dict(
            purpose="pathogen work declared",
            domain="disease",
            intervention_class="pathogen_work",
        ),
    ]
    for c in cases:
        r = bio.check_sync(**c)
        print(json.dumps({
            "purpose": c["purpose"],
            "intervention_class": c["intervention_class"],
            "decision": r["decision"],
            "error_code": r.get("error_code"),
            "bio_policy": r.get("bio_policy"),
        }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
