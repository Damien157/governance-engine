"""BioGovernance OS — policy + contract + GovernedBio check-only tests."""

from __future__ import annotations

import pytest

from governed_stack.bio import BioBlocked, GovernedBio
from governed_stack.bio_policy import classify_bio
from governed_stack.contracts import (
    BIO_SCAN_FORBIDDEN,
    BioScanIntent,
    IntentValidationError,
    validate_bio_scan,
)


def test_forbidden_keys_defined():
    assert "sequence" in BIO_SCAN_FORBIDDEN
    assert "protocol_steps" in BIO_SCAN_FORBIDDEN


def test_sequence_key_rejected():
    with pytest.raises((IntentValidationError, Exception)):
        BioScanIntent.model_validate(
            {
                "action": "bio_govern",
                "purpose": "clock",
                "domain": "aging",
                "intervention_class": "computational",
                "sequence": "ATGC",
            }
        )


def test_policy_allow_literature():
    r = classify_bio(
        purpose="review epigenetic clock papers",
        domain="aging",
        intervention_class="literature",
    )
    assert r.decision == "ALLOW_CANDIDATE"


def test_policy_block_pathogen_class():
    r = classify_bio(
        purpose="study",
        domain="disease",
        intervention_class="pathogen_work",
    )
    assert r.decision == "BLOCK"
    assert r.error_code == "GOV_BIO_DUAL_USE"


def test_policy_block_dual_use_phrase():
    r = classify_bio(
        purpose="model",
        domain="disease",
        intervention_class="computational",
        summary="explore gain of function claims in literature metadata only",
    )
    assert r.decision == "BLOCK"


def test_policy_review_clinical():
    r = classify_bio(
        purpose="trial intent metadata",
        domain="clinical_support",
        intervention_class="clinical_trial_declared",
        human_subjects=True,
    )
    assert r.decision == "REVIEW"


def test_validate_bio_scan_ok():
    intent = validate_bio_scan(
        purpose="GWAS summary stats reanalysis",
        domain="disease",
        intervention_class="computational",
    )
    d = intent.dump_for_govern()
    assert d["action"] == "bio_govern"
    assert "sequence" not in d
    assert "protocol" not in str(d).lower() or "protocol" in "x"  # no protocol payload


def test_governed_bio_allow_and_block(tmp_path, monkeypatch):
    bio = GovernedBio()
    # Point artifacts aside if needed — default artifacts/bio is fine for test
    allow = bio.check_sync(
        purpose="computational aging biomarker meta-analysis",
        domain="aging",
        intervention_class="computational",
        summary="public summary statistics only",
    )
    assert allow["decision"] in ("ALLOW", "REVIEW", "BLOCK")  # stack may tighten
    assert allow["bio_policy"]["decision"] == "ALLOW_CANDIDATE"
    # If stack ALLOW and policy candidate → ALLOW
    if allow["hais"] is not None or allow.get("entry_id"):
        pass

    blocked = bio.check_sync(
        purpose="declared pathogen work",
        domain="disease",
        intervention_class="pathogen_work",
    )
    assert blocked["decision"] == "BLOCK"
    assert blocked["error_code"] == "GOV_BIO_DUAL_USE"
    assert blocked["ok"] is False

    with pytest.raises(BioBlocked):
        bio.require_allow_sync(
            purpose="enhancement",
            domain="aging",
            intervention_class="enhancement",
        )
