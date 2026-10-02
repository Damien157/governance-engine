"""
SealedMail — drafts-only mail adapter that can reach a mail backend ONLY
through the governance gate.

Public surface (by design, nothing else):

    SealedMailAdapter.propose_draft(to=..., subject=..., body=..., cc=...)
    SealedMailAdapter.propose_draft_sync(...)

Flow for every call:

  1. Recipient allowlist (optional, operator-configured) — BLOCK outside it
     (audited: recipient count + SHA-256 of normalized recipients only).
  2. Bio seal (pre-probe, *before* the mail gate):
       * JSON-object subject/body → ``SidecarService._bio_shaped_probe``
         (same structural probe that seals ``channel=raw``).
       * Free text → the bio channel's semantic judge
         (``bio_semantic_judge.judge_bio_request``: structural ``classify_bio``
         + the overlay kernel, with the same default stub classifier,
         injectable ``BioSemanticJudge``, fail-closed REVIEW and charter reason
         vocabulary). Tighten-only. BLOCK/REVIEW short-circuits, and the sealed
         decision is first written to the signed audit chain (SHA-256 of
         subject/body only, never raw text).
  3. ``GovernedMail.check`` → ``GovernedStack.govern`` (subject+body only;
     recipients stay out of the scanned intent).
  4. BLOCK / REVIEW → return the decision; the backend is never called.
     (govern() wrote the signed row; if it did not, the adapter writes one.)
  5. ALLOW → build the draft payload **only** from the ALLOW envelope's
     bound content (``to``/``subject``/``body``/``cc``), after
     ``assert_bound_content``. If the envelope content differs from what the
     caller asked for, raise ``ContentBindingError`` and write nothing
     (the refusal is audited first; the govern() row says ALLOW).

Every denial writes a signed, chained audit row before returning/raising
(see ``record_denial``). Rows carry the real decision (BLOCK/REVIEW), a stage
marker and reason codes, and SHA-256 digests instead of subject, body or
recipient addresses. Adapter-written REVIEW rows are logged with
``enqueue_review=False``: a sealed stop is never a human-approvable item.

There is **no send operation**. The only write is "create a draft", executed
by an injected ``backend`` callable that receives a frozen ``DraftPayload``.
No credentials live in this module; a real Gmail backend is supplied by the
host (e.g. an agent relaying the sealed payload to its Gmail connector).

Enforcement limit (honest): this module only constrains callers that go
through it. An agent that still holds a raw Gmail tool can bypass it. Real
enforcement requires the agent's *only* mail tool to be the gated one (e.g.
``sealed_mail_mcp`` registered instead of the raw Gmail connector).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import inspect
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Tuple, Union

from .bio_semantic_judge import (
    CHARTER_REASONS,
    DEFAULT_JUDGE,
    BioJudgement,
    BioRequest,
    BioSemanticJudge,
    judge_bio_request,
    semantic_audit_dict,
)
from .connectors import ContentBindingError, assert_bound_content
from .mail import GovernedMail

Recipients = Union[str, Iterable[str]]

STAGE_ALLOWLIST = "recipient_allowlist"
STAGE_BIO_SEAL = "bio_seal"
STAGE_MAIL_GATE = "mail_gate"
STAGE_WRITTEN = "draft_written"

# Mail text -> bio request mapping. Mail has no structural bio fields, so it is
# judged as a low-risk literature request: only the free text can tighten it.
# The bio channel with the same fields scores identically.
MAIL_BIO_DOMAIN = "other"
MAIL_BIO_INTERVENTION_CLASS = "literature"

# Audit rows for adapter denials. decision = the real decision (BLOCK/REVIEW);
# the stage marker below is the first policy reason. Adapter-written REVIEW
# rows are logged with enqueue_review=False, so they never enter
# review_queue: a sealed stop must not be approvable into a voucher.
DENIAL_AUDIT_ACTION = "sealed_mail_denial"
STAGE_CONTENT_BINDING = "content_binding"
STAGE_MARKERS: Dict[str, str] = {
    STAGE_ALLOWLIST: "SEALED_MAIL_ALLOWLIST_DENIAL",
    STAGE_BIO_SEAL: "SEALED_MAIL_BIO_SEAL",
    STAGE_MAIL_GATE: "SEALED_MAIL_GATE_DENIAL",
    STAGE_CONTENT_BINDING: "SEALED_MAIL_CONTENT_BINDING_REFUSAL",
}
REASON_SEAL = "sealed_mail:bio_seal"
REASON_SEAL_ERROR = "sealed_mail:bio_seal_error"
REASON_SEAL_AUDIT_FAILED = "sealed_mail:audit_write_failed"
REASON_AUDIT_FAILED = REASON_SEAL_AUDIT_FAILED


@dataclass(frozen=True)
class DraftPayload:
    """Immutable draft built exclusively from an ALLOW envelope."""

    to: Tuple[str, ...]
    subject: str
    body: str
    cc: Tuple[str, ...]
    entry_id: Optional[str]
    content_sha256: str
    decision: str = "ALLOW"
    kind: str = "draft"

    def as_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["to"] = list(self.to)
        d["cc"] = list(self.cc)
        return d


DraftBackend = Callable[[DraftPayload], Union[Any, Awaitable[Any]]]


def content_digest(to: Iterable[str], subject: str, body: str, cc: Iterable[str]) -> str:
    """Stable sha256 over the bound draft content (for audit / handoff checks)."""
    blob = json.dumps(
        {"to": list(to), "subject": subject, "body": body, "cc": list(cc)},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _norm_recipients(value: Optional[Recipients]) -> Tuple[str, ...]:
    if value is None:
        return ()
    items = [value] if isinstance(value, str) else list(value)
    out: List[str] = []
    for r in items:
        if not isinstance(r, str) or not r.strip():
            raise TypeError(f"recipient must be a non-empty string, got {r!r}")
        out.append(r.strip())
    return tuple(out)


def _maybe_json_dict(text: str) -> Optional[Dict[str, Any]]:
    s = (text or "").strip()
    if not (s.startswith("{") and s.endswith("}")):
        return None
    try:
        data = json.loads(s)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def mail_bio_request(subject: str, body: str) -> BioRequest:
    """The bio request mail text is judged as (same as ``channel=bio`` with these fields)."""
    return BioRequest(
        purpose=subject or "",
        domain=MAIL_BIO_DOMAIN,
        intervention_class=MAIL_BIO_INTERVENTION_CLASS,
        summary=body or "",
    )


def _reason_codes(reasons: Iterable[str]) -> List[str]:
    """Charter reasons pass through. ``bio_policy:`` reasons are cut to their
    category (``bio_policy:block_phrase:<phrase>`` -> ``bio_policy:block_phrase``)
    so no request text reaches mail reasons or the audit row. Anything else is
    dropped."""
    out: List[str] = []
    for r in reasons:
        if not isinstance(r, str):
            continue
        if r in CHARTER_REASONS:
            code = r
        elif r.startswith("bio_policy:"):
            code = ":".join(r.split(":")[:2])
        else:
            continue
        if code not in out:
            out.append(code)
    return out


def bio_seal(
    subject: str, body: str, *, judge: Optional[BioSemanticJudge] = None
) -> Optional[Dict[str, Any]]:
    """Tighten-only bio pre-probe for mail content.

    Returns ``None`` when mail may proceed to the mail gate. Otherwise returns
    a dict with ``decision`` (BLOCK|REVIEW), ``reasons`` (codes only),
    ``error_code``, ``seal`` (``raw_json_probe`` | ``semantic_judge``),
    ``scorer`` and, for the judge, ``bio_semantic`` (audit view: hashes and
    scores only). Free text is scored by the same judge path as the bio
    channel. Any unexpected error gives REVIEW.
    """
    # Lazy import: sidecar pulls the HTTP server module; keep import cheap.
    from .sidecar import SidecarService

    judge = judge if judge is not None else DEFAULT_JUDGE
    for where, text in (("subject", subject), ("body", body)):
        probe = _maybe_json_dict(text)
        if probe is not None and SidecarService._bio_shaped_probe(probe):
            return {
                "decision": "BLOCK",
                "reasons": [
                    f"sealed_mail:bio_backdoor_blocked:{where}:use_channel_bio",
                ],
                "error_code": "GOV_INTENT_INVALID",
                "seal": "raw_json_probe",
                "scorer": judge.scorer_id,
                "bio_semantic": None,
            }

    try:
        judgement: BioJudgement = judge_bio_request(mail_bio_request(subject, body), judge)
    except Exception:  # fail closed; message deliberately not propagated
        return {
            "decision": "REVIEW",
            "reasons": [REASON_SEAL, REASON_SEAL_ERROR],
            "error_code": "GOV_BIO_SEMANTIC_REVIEW",
            "seal": "semantic_judge",
            "scorer": judge.scorer_id,
            "bio_semantic": None,
        }
    policy = judgement.policy
    if policy.decision == "ALLOW_CANDIDATE":
        return None
    decision = policy.decision if policy.decision in ("BLOCK", "REVIEW") else "BLOCK"
    return {
        "decision": decision,
        "reasons": [REASON_SEAL] + _reason_codes(policy.reasons),
        "error_code": policy.error_code,
        "seal": "semantic_judge",
        "scorer": judgement.scorer_id,
        "bio_semantic": semantic_audit_dict(judgement),
    }


def recipients_digest(recipients: Iterable[str]) -> str:
    """SHA-256 over sorted, lower-cased recipients (correlation, not secrecy)."""
    norm = sorted({r.strip().lower() for r in recipients})
    return _sha256(json.dumps(norm, ensure_ascii=False))


def record_denial(
    engine: object,
    *,
    stage: str,
    decision: str,
    reasons: Iterable[str],
    subject: str,
    body: str,
    recipients: Iterable[str],
    error_code: Optional[str] = None,
    entry_id: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    """Write an adapter denial to the signed audit chain; return its entry id.

    ``decision`` is the real decision (BLOCK or REVIEW; anything else is
    recorded as BLOCK). ``policy_reasons`` = [stage marker] + reason codes.
    Stores SHA-256 of subject, body and normalized recipients plus the
    recipient count: never raw text or addresses. Always
    ``enqueue_review=False``. Raises if the engine has no audit storage.
    """
    storage = getattr(engine, "storage", None)
    log = getattr(storage, "log_decision", None)
    if not callable(log):
        raise RuntimeError("sealed_mail audit unavailable: engine lacks storage.log_decision")
    if stage not in STAGE_MARKERS:
        raise ValueError(f"unknown sealed_mail stage: {stage!r}")
    decision = decision if decision in ("BLOCK", "REVIEW") else "BLOCK"
    marker = STAGE_MARKERS[stage]
    recips = list(recipients)
    digests = {
        "subject_sha256": _sha256(subject),
        "body_sha256": _sha256(body),
        "recipients_sha256": recipients_digest(recips),
        "recipient_count": len(recips),
    }
    codes = [marker] + [str(r) for r in reasons if str(r) != marker]
    metadata: Dict[str, Any] = {
        "channel": "mail",
        "stage": stage,
        "stage_marker": marker,
        "decision": decision,
        "error_code": error_code,
        "governed_entry_id": entry_id,
        **digests,
    }
    metadata.update(extra or {})
    new_id = log(
        intent={"action": DENIAL_AUDIT_ACTION, "channel": "mail", "stage": stage, **digests},
        decision=decision,
        result=f"sealed_mail:{stage}",
        verification_score=1.0,
        risk_signal=1.0 if decision == "BLOCK" else 0.5,
        anomaly_signal=0.0,
        policy_reasons=codes,
        metadata=metadata,
        enqueue_review=False,
    )
    return str(new_id)


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


class SealedMailAdapter:
    """Drafts-only, gate-first mail adapter. See module docstring."""

    __slots__ = ("__backend", "__mail", "__allowlist", "__user", "__role", "__judge")

    def __init__(
        self,
        backend: DraftBackend,
        *,
        mail: Optional[GovernedMail] = None,
        recipient_allowlist: Optional[Iterable[str]] = None,
        user: str = "damien",
        role: str = "user",
        bio_judge: Optional[BioSemanticJudge] = None,
    ) -> None:
        if backend is None or not callable(backend):
            raise TypeError("backend must be a callable taking a DraftPayload")
        if bio_judge is None:
            bio_judge = DEFAULT_JUDGE  # same default as GovernedBio / sidecar
        if not isinstance(bio_judge, BioSemanticJudge):
            raise TypeError("bio_judge must be a BioSemanticJudge")
        self.__judge = bio_judge
        self.__backend = backend
        self.__mail = mail if mail is not None else GovernedMail()
        self.__allowlist = (
            frozenset(r.strip().lower() for r in recipient_allowlist)
            if recipient_allowlist is not None
            else None
        )
        self.__user = user
        self.__role = role

    @staticmethod
    def _denied(stage: str, decision: str, reasons: Any, **extra: Any) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "ok": False,
            "decision": decision,
            "reasons": list(reasons or []),
            "stage": stage,
            "written": False,
            "draft": None,
            "backend_result": None,
        }
        out.update(extra)
        return out

    def _audit_denial(self, reasons: List[str], **kw: Any) -> Optional[str]:
        """record_denial via this adapter's stack. If the write fails, the denial
        stands: append the audit-failed reason and return None."""
        try:
            return record_denial(getattr(self.__mail.stack, "engine", None), reasons=reasons, **kw)
        except Exception:
            reasons.append(REASON_AUDIT_FAILED)
            return None

    async def propose_draft(
        self,
        *,
        to: Recipients,
        subject: str,
        body: str,
        cc: Optional[Recipients] = None,
    ) -> Dict[str, Any]:
        """Gate, then (on ALLOW only) write a draft built from the envelope."""
        if not isinstance(subject, str) or not isinstance(body, str):
            raise TypeError("subject and body must be strings")
        req_to = _norm_recipients(to)
        req_cc = _norm_recipients(cc)
        if not req_to:
            raise TypeError("propose_draft requires at least one 'to' recipient")

        # 1. Operator recipient allowlist (routing is not scanned by policy).
        if self.__allowlist is not None:
            outside = [r for r in req_to + req_cc if r.lower() not in self.__allowlist]
            if outside:
                reasons = [f"sealed_mail:recipient_not_allowlisted:{len(outside)}"]
                audit_id = self._audit_denial(
                    reasons,
                    stage=STAGE_ALLOWLIST,
                    decision="BLOCK",
                    subject=subject,
                    body=body,
                    recipients=req_to + req_cc,
                    error_code="GOV_POLICY_BLOCK",
                )
                return self._denied(
                    STAGE_ALLOWLIST,
                    "BLOCK",
                    reasons,
                    error_code="GOV_POLICY_BLOCK",
                    audit_entry_id=audit_id,
                )

        # 2. Bio seal before the mail gate (judge off the event loop, like
        #    govern_bio_request). Any exception here is a REVIEW seal, never a
        #    pass-through to the gate.
        try:
            sealed = await asyncio.to_thread(bio_seal, subject, body, judge=self.__judge)
        except Exception:
            sealed = {
                "decision": "REVIEW",
                "reasons": [REASON_SEAL, REASON_SEAL_ERROR],
                "error_code": "GOV_BIO_SEMANTIC_REVIEW",
                "seal": "semantic_judge",
                "scorer": self.__judge.scorer_id,
                "bio_semantic": None,
            }
        if sealed is not None:
            decision = sealed["decision"] if sealed["decision"] in ("BLOCK", "REVIEW") else "BLOCK"
            sealed["decision"] = decision
            reasons = list(sealed["reasons"])
            audit_entry_id = self._audit_denial(
                reasons,
                stage=STAGE_BIO_SEAL,
                decision=decision,
                subject=subject,
                body=body,
                recipients=req_to + req_cc,
                error_code=sealed.get("error_code"),
                extra={
                    "seal": sealed.get("seal"),
                    "scorer": sealed.get("scorer"),
                    "bio_semantic": sealed.get("bio_semantic"),
                },
            )
            return self._denied(
                STAGE_BIO_SEAL,
                decision,
                reasons,
                error_code=sealed.get("error_code"),
                audit_entry_id=audit_entry_id,
                scorer=sealed.get("scorer"),
                bio_semantic=sealed.get("bio_semantic"),
            )

        # 3. Mail gate → GovernedStack.govern (subject + body only).
        result = await self.__mail.check(
            to=list(req_to),
            subject=subject,
            body=body,
            cc=list(req_cc) if req_cc else None,
            user=self.__user,
            role=self.__role,
        )
        decision = result.get("decision", "BLOCK")
        entry_id = result.get("entry_id")
        if decision != "ALLOW" or not result.get("ok"):
            # 4. BLOCK / REVIEW → nothing written.
            out_decision = decision if decision in ("BLOCK", "REVIEW") else "BLOCK"
            reasons = list(result.get("reasons") or [])
            gate_audit_id: Optional[str] = None
            if not entry_id or out_decision != decision:
                # govern() left no row (e.g. invalid intent) or its row does not
                # say what we return: write our own (codes only, no gate reasons).
                gate_reasons = [f"sealed_mail:gate_decision:{str(decision)[:16]}"]
                gate_audit_id = self._audit_denial(
                    gate_reasons,
                    stage=STAGE_MAIL_GATE,
                    decision=out_decision,
                    subject=subject,
                    body=body,
                    recipients=req_to + req_cc,
                    error_code=result.get("error_code"),
                    entry_id=entry_id,
                )
                if gate_audit_id is None:
                    reasons.append(REASON_AUDIT_FAILED)
            return self._denied(
                STAGE_MAIL_GATE,
                out_decision,
                reasons,
                entry_id=entry_id,
                audit_entry_id=gate_audit_id or entry_id,
            )

        # 5. ALLOW → payload from the envelope's bound content only. Any
        #    binding refusal is audited (the govern() row says ALLOW), then raised.
        mismatched: List[str] = []
        try:
            assert_bound_content(result, "mail")
            env_to = _norm_recipients(result["to"])
            env_subject = result["subject"]
            env_body = result["body"]
            env_cc = _norm_recipients(result.get("cc") or None)
            if not isinstance(env_subject, str) or not isinstance(env_body, str):
                raise ContentBindingError("sealed_mail: envelope subject/body not strings")

            mismatched = [
                name
                for name, env_v, req_v in (
                    ("to", env_to, req_to),
                    ("subject", env_subject, subject),
                    ("body", env_body, body),
                    ("cc", env_cc, req_cc),
                )
                if env_v != req_v
            ]
            if mismatched:
                raise ContentBindingError(
                    "sealed_mail: ALLOW envelope content differs from the requested "
                    f"draft on {mismatched}; refusing to write (content-swap guard)"
                )
        except (ContentBindingError, KeyError, TypeError) as exc:
            self._audit_denial(
                ["sealed_mail:content_binding_refused"]
                + [f"sealed_mail:content_mismatch:{f}" for f in mismatched],
                stage=STAGE_CONTENT_BINDING,
                decision="BLOCK",
                subject=subject,
                body=body,
                recipients=req_to + req_cc,
                error_code="GOV_CONTENT_BINDING",
                entry_id=entry_id,
            )
            if isinstance(exc, ContentBindingError):
                raise
            raise ContentBindingError(
                f"sealed_mail: malformed ALLOW envelope ({type(exc).__name__}); refusing to write"
            ) from exc

        payload = DraftPayload(
            to=env_to,
            subject=env_subject,
            body=env_body,
            cc=env_cc,
            entry_id=result.get("entry_id"),
            content_sha256=content_digest(env_to, env_subject, env_body, env_cc),
        )
        backend_out = await _maybe_await(self.__backend(payload))
        return {
            "ok": True,
            "decision": "ALLOW",
            "reasons": list(result.get("reasons") or []),
            "stage": STAGE_WRITTEN,
            "written": True,
            "entry_id": result.get("entry_id"),
            "draft": payload.as_dict(),
            "backend_result": backend_out,
        }

    def propose_draft_sync(
        self,
        *,
        to: Recipients,
        subject: str,
        body: str,
        cc: Optional[Recipients] = None,
    ) -> Dict[str, Any]:
        """Sync wrapper around ``propose_draft`` (running-loop safe)."""

        async def _run() -> Dict[str, Any]:
            return await self.propose_draft(to=to, subject=subject, body=body, cc=cc)

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(_run())
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, _run()).result()


def spool_backend(outbox_dir: Union[str, Path]) -> DraftBackend:
    """Local, credential-free backend: write each sealed draft as a JSON file.

    Useful as a handoff: a host process holding real mail credentials may
    create the draft from the spooled payload verbatim (and should verify
    ``content_sha256``). Nothing here talks to a network.
    """
    out = Path(outbox_dir)

    def _write(payload: DraftPayload) -> Dict[str, Any]:
        out.mkdir(parents=True, exist_ok=True)
        name = f"draft_{int(time.time() * 1000)}_{payload.content_sha256[:12]}.json"
        path = out / name
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload.as_dict(), indent=2, ensure_ascii=False))
        os.replace(tmp, path)
        return {"spooled": str(path), "content_sha256": payload.content_sha256}

    return _write


__all__ = [
    "DraftBackend",
    "DraftPayload",
    "SealedMailAdapter",
    "bio_seal",
    "content_digest",
    "mail_bio_request",
    "recipients_digest",
    "record_denial",
    "spool_backend",
]
