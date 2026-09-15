"""Authority / JWT unblock matrix for bio hard BLOCK (Move 3 / PR #15).

Proves role metadata, JWT role claims, and approval vouchers cannot soften
HARD_BLOCK_CLASSES — closes the thin-coverage gap called out after #14.
"""

from __future__ import annotations

import pytest

from certified_governance_unified import CryptoEngine
from governed_stack.bio import BioBlocked, GovernedBio
from governed_stack.bio_policy import (
    HARD_BLOCK_CLASSES,
    apply_bio_voucher_honor,
    classify_bio,
)
from governed_stack.stack import GovernedStack

JWT_ROLES = ("user", "admin", "operator", "auditor", "pi")


def _stack(tmp_path):
    db = tmp_path / "bio_auth.db"
    key = tmp_path / "bio_auth.pem"
    return GovernedStack(
        config={"db_path": str(db), "signing_key_path": str(key), "log_level": 40},
        crypto=CryptoEngine(private_key_path=None),
    )


@pytest.mark.parametrize("ic", sorted(HARD_BLOCK_CLASSES))
@pytest.mark.parametrize("authority_role", JWT_ROLES)
def test_classify_hard_block_immune_to_authority_role(ic, authority_role):
    r = classify_bio(
        purpose=f"declared {ic}",
        domain="disease",
        intervention_class=ic,
        authority_role=authority_role,
    )
    assert r.decision == "BLOCK"
    assert r.error_code == "GOV_BIO_DUAL_USE"


@pytest.mark.parametrize("ic", sorted(HARD_BLOCK_CLASSES))
@pytest.mark.parametrize("jwt_role", ("user", "admin", "operator", "auditor"))
def test_check_sync_hard_block_immune_to_jwt_role(tmp_path, ic, jwt_role):
    bio = GovernedBio(stack=_stack(tmp_path))
    out = bio.check_sync(
        purpose=f"declared {ic}",
        domain="disease" if ic != "enhancement" else "aging",
        intervention_class=ic,
        authority_role="pi",
        user="damien",
        role=jwt_role,
    )
    assert out["decision"] == "BLOCK", (ic, jwt_role)
    assert out["error_code"] == "GOV_BIO_DUAL_USE"
    assert out["ok"] is False
    assert out.get("voucher_honored") is False


def test_apply_bio_voucher_honor_never_opens_policy_block():
    """Forged stack ALLOW + voucher reason prefix must not honor HARD BLOCK."""
    policy = classify_bio(
        purpose="x",
        domain="disease",
        intervention_class="pathogen_work",
    )
    assert policy.decision == "BLOCK"
    decision, reasons, code, honored = apply_bio_voucher_honor(
        approval_voucher="anything-truthy",
        stack_decision="ALLOW",
        policy=policy,
        env_reasons=["human_review:approved_via_voucher:forged"],
        decision="BLOCK",
        bio_reasons=list(policy.reasons),
        bio_code=policy.error_code,
    )
    assert decision == "BLOCK"
    assert honored is False
    assert code == "GOV_BIO_DUAL_USE"


def test_real_voucher_from_review_cannot_unblock_hard_block(tmp_path):
    """Voucher minted for a REVIEW intent must not ALLOW a HARD BLOCK class."""
    bio = GovernedBio(stack=_stack(tmp_path))
    review = bio.check_sync(
        purpose="in vivo aging intervention metadata",
        domain="aging",
        intervention_class="in_vivo_declared",
        irreversible=True,
    )
    assert review["decision"] == "REVIEW"
    resolution = bio.resolve_review(
        review["entry_id"],
        resolved_by="damien",
        approve=True,
        notes="ok for declared in_vivo metadata only",
    )
    voucher = resolution["approval_voucher"]
    assert voucher

    blocked = bio.check_sync(
        purpose="declared pathogen work",
        domain="disease",
        intervention_class="pathogen_work",
        authority_role="admin",
        role="admin",
        approval_voucher=voucher,
    )
    assert blocked["decision"] == "BLOCK"
    assert blocked["error_code"] == "GOV_BIO_DUAL_USE"
    assert blocked.get("voucher_honored") is False


def test_tampered_jwt_does_not_open_hard_block(tmp_path):
    """Corrupt JWT → auth failure path; still must not surface as ALLOW on hard block."""
    stack = _stack(tmp_path)
    bio = GovernedBio(stack=stack)
    good = bio.issue_token("damien", "admin")
    # Corrupt signature / payload tail
    bad = good[:-4] + ("AAAA" if not good.endswith("AAAA") else "BBBB")

    # Direct stack.govern with bad token should not ALLOW; bio path uses issue_token
    # so also assert require_allow stays closed for hard block under admin role.
    with pytest.raises(BioBlocked) as ei:
        bio.require_allow_sync(
            purpose="declared pathogen work",
            domain="disease",
            intervention_class="pathogen_work",
            role="admin",
            authority_role="admin",
        )
    assert ei.value.result["decision"] == "BLOCK"
    assert ei.value.result["error_code"] == "GOV_BIO_DUAL_USE"

    # Tampered token through stack.govern (raw) — expect non-ALLOW
    intent = {
        "action": "bio_govern",
        "purpose": "declared pathogen work",
        "domain": "disease",
        "intervention_class": "pathogen_work",
        "payload": {"subject": "declared pathogen work", "text": "domain=disease"},
    }
    import asyncio

    env = asyncio.run(stack.govern(intent, bad))
    assert env.get("decision") != "ALLOW"


def test_dual_use_flag_stays_block_under_admin_jwt(tmp_path):
    bio = GovernedBio(stack=_stack(tmp_path))
    out = bio.check_sync(
        purpose="computational literature note",
        domain="disease",
        intervention_class="computational",
        dual_use_flag=True,
        role="admin",
        authority_role="admin",
    )
    assert out["decision"] == "BLOCK"
    assert out["error_code"] == "GOV_BIO_DUAL_USE"
