"""
GovernedBio — biology / aging / disease intent gate in front of GovernedStack.

Scans purpose, domain, intervention_class, and risk metadata only.
Never executes wet-lab work, never returns protocols or sequences.
Secrets and sequence/protocol payloads are rejected at the contract layer.

Axes: Purpose → Cost → Risk → Authority → Audit.
See docs/BIO_GOVERNANCE_OS.md.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from pathlib import Path
from typing import Any, Dict, Optional

from .bio_semantic_judge import (
    DEFAULT_JUDGE,
    BioRequest,
    BioSemanticJudge,
    govern_bio_request,
)
from .contracts import BIO_VOUCHER_TTL_DEFAULT, validate_bio_scan
from .mail import SendBlocked
from .stack import GovernedStack, ensure_import_paths

ensure_import_paths()

try:
    from certified_governance_unified import CryptoEngine
except Exception:  # pragma: no cover
    try:
        from certified_governance import CryptoEngine
    except Exception:  # pragma: no cover
        CryptoEngine = None  # type: ignore

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_BIO_DIR = _REPO_ROOT / "artifacts" / "bio"
_DEFAULT_DB = _DEFAULT_BIO_DIR / "audit.db"
_DEFAULT_KEY = _DEFAULT_BIO_DIR / "signing_key.pem"

BioBlocked = SendBlocked


def intent_for_scan(
    *,
    purpose: str,
    domain: str,
    intervention_class: str,
    summary: str = "",
    subject_scope: str = "",
    risk_notes: str = "",
    authority_role: str = "",
    irreversible: bool = False,
    human_subjects: bool = False,
    dual_use_flag: bool = False,
) -> Dict[str, Any]:
    """Exact intent passed to govern(); sequences/protocols intentionally absent."""
    return validate_bio_scan(
        purpose=purpose,
        domain=domain,
        intervention_class=intervention_class,
        summary=summary,
        subject_scope=subject_scope,
        risk_notes=risk_notes,
        authority_role=authority_role,
        irreversible=irreversible,
        human_subjects=human_subjects,
        dual_use_flag=dual_use_flag,
    ).dump_for_govern()


class GovernedBio:
    """Adapter: bio policy overlay + Authority/Audit via GovernedStack.

    ``semantic_judge`` injects the bio semantic judge (classifier + scorer id +
    kernel config). Default: offline stub classifier. See
    ``governed_stack.bio_semantic_judge``.
    """

    def __init__(
        self,
        stack: Optional[GovernedStack] = None,
        *,
        semantic_judge: Optional[BioSemanticJudge] = None,
    ) -> None:
        if stack is not None:
            self.stack = stack
        else:
            self.stack = self._build_default_stack()
        if semantic_judge is None:
            semantic_judge = DEFAULT_JUDGE
        if not isinstance(semantic_judge, BioSemanticJudge):
            raise TypeError("semantic_judge must be a BioSemanticJudge")
        self.semantic_judge = semantic_judge
        self._token_cache: Dict[tuple, str] = {}

    @staticmethod
    def _build_default_stack() -> GovernedStack:
        bio_dir = _DEFAULT_BIO_DIR
        bio_dir.mkdir(parents=True, exist_ok=True)
        crypto = None
        if CryptoEngine is not None:
            crypto = CryptoEngine(private_key_path=str(_DEFAULT_KEY))
        return GovernedStack(
            config={
                "db_path": str(_DEFAULT_DB),
                "signing_key_path": str(_DEFAULT_KEY),
                "log_level": 40,
            },
            crypto=crypto,
        )

    def issue_token(self, user: str, role: str = "user") -> str:
        key = (user, role)
        tok = self._token_cache.get(key)
        if tok is None:
            tok = self.stack.issue_token(user, role)
            self._token_cache[key] = tok
        return tok

    async def check(
        self,
        *,
        purpose: str,
        domain: str,
        intervention_class: str,
        summary: str = "",
        subject_scope: str = "",
        risk_notes: str = "",
        authority_role: str = "",
        irreversible: bool = False,
        human_subjects: bool = False,
        dual_use_flag: bool = False,
        user: str = "damien",
        role: str = "user",
        approval_voucher: Optional[str] = None,
    ) -> dict:
        intent = intent_for_scan(
            purpose=purpose,
            domain=domain,
            intervention_class=intervention_class,
            summary=summary,
            subject_scope=subject_scope,
            risk_notes=risk_notes,
            authority_role=authority_role,
            irreversible=irreversible,
            human_subjects=human_subjects,
            dual_use_flag=dual_use_flag,
        )
        request = BioRequest(
            purpose=purpose,
            domain=domain,
            intervention_class=intervention_class,
            summary=summary,
            subject_scope=subject_scope,
            risk_notes=risk_notes,
            authority_role=authority_role,
            irreversible=irreversible,
            human_subjects=human_subjects,
            dual_use_flag=dual_use_flag,
        )
        token = self.issue_token(user, role)
        # Single bio pipeline: semantic judge -> govern -> voucher honor ->
        # judgement audit row -> REVIEW enqueue (see bio_semantic_judge).
        out = await govern_bio_request(
            self.stack,
            intent,
            token,
            request,
            approval_voucher=approval_voucher,
            judge=self.semantic_judge,
        )
        env = out.env
        decision = out.decision
        bio_reasons = out.bio_reasons
        bio_code = out.error_code
        entry_id = env.get("entry_id")

        ok = decision == "ALLOW"
        merged_reasons = list(env.get("reasons") or [])
        for r in bio_reasons:
            if r not in merged_reasons:
                merged_reasons.append(r)
        result = {
            "ok": ok,
            "decision": decision,
            "reasons": merged_reasons,
            "entry_id": entry_id,
            "hais": env.get("hais"),
            "haven2": env.get("haven2"),
            "purpose": purpose,
            "domain": domain,
            "intervention_class": intervention_class,
            "summary": summary or "",
            "subject_scope": subject_scope or "",
            "authority_role": authority_role or "",
            "irreversible": bool(irreversible),
            "human_subjects": bool(human_subjects),
            "dual_use_flag": bool(dual_use_flag),
            "bio_policy": out.policy.as_dict(),
            "bio_semantic": out.semantic,
            "blocked_run": not ok,
            "error_code": bio_code or env.get("error_code"),
            "review_enqueued": out.review_enqueued,
            "voucher_honored": out.voucher_honored,
        }
        return result

    async def require_allow(self, **kwargs: Any) -> dict:
        """Raises BioBlocked unless ALLOW — no silent proceed to bio side effects."""
        result = await self.check(**kwargs)
        if not result.get("ok"):
            raise SendBlocked(result)
        return result

    def check_sync(self, **kwargs: Any) -> dict:
        async def _run() -> dict:
            return await self.check(**kwargs)

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(_run())
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, _run()).result()

    def require_allow_sync(self, **kwargs: Any) -> dict:
        async def _run() -> dict:
            return await self.require_allow(**kwargs)

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(_run())
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, _run()).result()


    def list_pending_reviews(self, limit: int = 50) -> list:
        """Pending human REVIEW items (including bio-overlay enqueued)."""
        eng = getattr(self.stack, "engine", None)
        if eng is None or not hasattr(eng, "list_pending_reviews"):
            return []
        return list(eng.list_pending_reviews(limit=limit))

    def resolve_review(
        self,
        entry_id: str,
        *,
        resolved_by: str,
        approve: bool,
        notes: str = "",
        voucher_ttl_seconds: int = BIO_VOUCHER_TTL_DEFAULT,
    ) -> dict:
        """Human resolve of a pending REVIEW. Approve → voucher; deny → BLOCK trail.

        Does not execute wet-lab work. Caller must re-check with the voucher
        (same intent) for ALLOW. HARD BLOCK bio classes remain blocked even
        with a voucher.
        """
        eng = getattr(self.stack, "engine", None)
        if eng is None or not hasattr(eng, "resolve_review"):
            raise RuntimeError("review resolve unavailable on this stack engine")
        return eng.resolve_review(
            entry_id,
            resolved_by=resolved_by,
            approve=approve,
            notes=notes,
            voucher_ttl_seconds=voucher_ttl_seconds,
        )



__all__ = ["GovernedBio", "BioBlocked", "SendBlocked", "intent_for_scan"]
