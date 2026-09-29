"""
SealedMail — drafts-only mail adapter that can reach a mail backend ONLY
through the governance gate.

Public surface (by design, nothing else):

    SealedMailAdapter.propose_draft(to=..., subject=..., body=..., cc=...)
    SealedMailAdapter.propose_draft_sync(...)

Flow for every call:

  1. Recipient allowlist (optional, operator-configured) — BLOCK outside it.
  2. Bio seal (pre-probe, *before* the mail gate):
       * JSON-object subject/body → ``SidecarService._bio_shaped_probe``
         (same structural probe that seals ``channel=raw``).
       * Free text → ``classify_bio_with_semantic`` (structural phrases +
         0.6.2 semantic stub), tighten-only. BLOCK/REVIEW short-circuits.
  3. ``GovernedMail.check`` → ``GovernedStack.govern`` (subject+body only;
     recipients stay out of the scanned intent).
  4. BLOCK / REVIEW → return the decision; the backend is never called.
  5. ALLOW → build the draft payload **only** from the ALLOW envelope's
     bound content (``to``/``subject``/``body``/``cc``), after
     ``assert_bound_content``. If the envelope content differs from what the
     caller asked for, raise ``ContentBindingError`` and write nothing.

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

from .bio_semantic import classify_bio_with_semantic
from .connectors import ContentBindingError, assert_bound_content
from .mail import GovernedMail

Recipients = Union[str, Iterable[str]]

STAGE_ALLOWLIST = "recipient_allowlist"
STAGE_BIO_SEAL = "bio_seal"
STAGE_MAIL_GATE = "mail_gate"
STAGE_WRITTEN = "draft_written"


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


def bio_seal(subject: str, body: str) -> Optional[Dict[str, Any]]:
    """Tighten-only bio pre-probe for mail content.

    Returns ``None`` when mail may proceed to the mail gate, else a dict with
    ``decision`` (BLOCK|REVIEW), ``reasons`` and ``error_code``.
    """
    # Lazy import: sidecar pulls the HTTP server module; keep import cheap.
    from .sidecar import SidecarService

    for where, text in (("subject", subject), ("body", body)):
        probe = _maybe_json_dict(text)
        if probe is not None and SidecarService._bio_shaped_probe(probe):
            return {
                "decision": "BLOCK",
                "reasons": [
                    f"sealed_mail:bio_backdoor_blocked:{where}:use_channel_bio",
                ],
                "error_code": "GOV_INTENT_INVALID",
            }

    policy, semantic = classify_bio_with_semantic(
        purpose=subject or "",
        domain="other",
        intervention_class="literature",
        summary=body or "",
    )
    if policy.decision in ("BLOCK", "REVIEW"):
        return {
            "decision": policy.decision,
            "reasons": ["sealed_mail:bio_seal"] + list(policy.reasons),
            "error_code": policy.error_code,
            "bio_semantic": semantic.as_dict() if hasattr(semantic, "as_dict") else None,
        }
    return None


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


class SealedMailAdapter:
    """Drafts-only, gate-first mail adapter. See module docstring."""

    __slots__ = ("__backend", "__mail", "__allowlist", "__user", "__role")

    def __init__(
        self,
        backend: DraftBackend,
        *,
        mail: Optional[GovernedMail] = None,
        recipient_allowlist: Optional[Iterable[str]] = None,
        user: str = "damien",
        role: str = "user",
    ) -> None:
        if backend is None or not callable(backend):
            raise TypeError("backend must be a callable taking a DraftPayload")
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
                return self._denied(
                    STAGE_ALLOWLIST,
                    "BLOCK",
                    [f"sealed_mail:recipient_not_allowlisted:{len(outside)}"],
                    error_code="GOV_POLICY_BLOCK",
                )

        # 2. Bio seal before the mail gate.
        sealed = bio_seal(subject, body)
        if sealed is not None:
            return self._denied(
                STAGE_BIO_SEAL,
                sealed["decision"],
                sealed["reasons"],
                error_code=sealed.get("error_code"),
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
        if decision != "ALLOW" or not result.get("ok"):
            # 4. BLOCK / REVIEW → nothing written.
            return self._denied(
                STAGE_MAIL_GATE,
                decision if decision in ("BLOCK", "REVIEW") else "BLOCK",
                result.get("reasons"),
                entry_id=result.get("entry_id"),
            )

        # 5. ALLOW → payload from the envelope's bound content only.
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
    "spool_backend",
]
