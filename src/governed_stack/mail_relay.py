"""
MailRelay: move sealed, spooled drafts into a partner's Gmail or Outlook
Drafts folder. Drafts only; nothing is ever transmitted.

Input is the spool written by ``sealed_mail.spool_backend`` (one
``draft_<ms>_<sha12>.json`` per ALLOW, holding ``DraftPayload.as_dict()``:
``to, subject, body, cc, entry_id, content_sha256, decision, kind``).

Per relay run (``MailRelay.run``), under an exclusive ``flock`` on the spool:

  1. The signed audit chain must verify (``storage.verify_chain()['valid']``).
     If not, nothing is uploaded.
  2. Each spool file is re-verified (``verify_spool_record``):
       * exact field set, ``kind == "draft"``, ``decision == "ALLOW"``;
       * ``content_sha256`` recomputed with ``sealed_mail.content_digest``;
       * ``connectors.assert_bound_content(..., "mail")`` on the record;
       * the govern() row named by ``entry_id`` exists in the chain, says
         ALLOW, and its scanned intent is exactly this subject and body;
       * no sealed-mail denial row refers to that entry.
     Any failure: refused, nothing uploaded, reason codes recorded.
  3. Partner recipient allowlist: every to/cc/bcc address must be on it.
     An empty allowlist refuses everything.
  4. Idempotency by ``content_sha256`` in ``.mail_relay_state.json`` beside
     the spool. The state is marked ``in_flight`` (fsynced) before the
     provider call, so a crash mid-upload is never retried blindly.
  5. One provider call (``DraftProvider.upload_draft``). No retries inside a
     run; the run stops at the first provider error (circuit breaker).
     Ambiguous outcomes (timeout, 5xx, bad 2xx) are terminal
     ``outcome_unknown``: never retried automatically, so no duplicate.
  6. One signed audit row per attempt (``storage.log_decision``,
     ``enqueue_review=False``): SHA-256 of subject and body only, recipient
     count, status, reason codes, error digest. No recipient addresses or
     recipient hashes, no body, no token, no raw error text.

``run()`` never raises; every outcome is a status in its return value. The
relay is not called from ``govern()`` and cannot loosen any decision.

Single-writer limit: ``AuditStorage`` keeps the chain tip in memory, so the
relay must share the process (and storage object) of whatever else writes the
same audit DB, or run while that writer is stopped. See docs/MAIL_RELAY.md.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import stat
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Tuple, Union

from .connectors import ContentBindingError, assert_bound_content
from .mail_relay_providers import (
    KIND_BAD_RESPONSE,
    KIND_CREDENTIAL_MISSING,
    KIND_CREDENTIAL_SCOPE,
    KIND_RATE_LIMITED,
    KIND_REJECTED,
    KIND_SERVER_ERROR,
    KIND_TIMEOUT,
    KIND_UNREACHABLE,
    DraftProvider,
    ProviderError,
    RelayDraft,
    error_digest,
)
from .sealed_mail import content_digest

log = logging.getLogger("governed_stack.mail_relay")

RELAY_AUDIT_ACTION = "mail_relay_upload"
RELAY_STAGE_MARKER = "MAIL_RELAY"
STATE_FILE = ".mail_relay_state.json"
LOCK_FILE = ".mail_relay.lock"
SPOOL_NAME_RE = re.compile(r"^draft_\d+_[0-9a-f]{12}\.json$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_SPOOL_BYTES = 1024 * 1024
SPOOL_KEYS: FrozenSet[str] = frozenset(
    {"to", "subject", "body", "cc", "entry_id", "content_sha256", "decision", "kind"}
)
_ADDR_RE = re.compile(r"^[^\s@<>,;:\"()\[\]\\]+@[^\s@<>,;:\"()\[\]\\]+$")

# Statuses
UPLOADED = "uploaded"
ALREADY_UPLOADED = "already_uploaded"
REFUSED = "refused"
SKIPPED = "skipped"
NOT_ATTEMPTED = "not_attempted"
RETRY_LATER = "retry_later"  # nothing reached the provider, or provider did not process
REJECTED = "rejected"  # provider refused (4xx): terminal
OUTCOME_UNKNOWN = "outcome_unknown"  # may exist in Drafts: terminal, operator checks
FAILED_PERMANENT = "failed_permanent"  # retry budget used up
AUDIT_FAILED = "audit_failed"

TERMINAL_DRAFT_STATES = frozenset(
    {UPLOADED, REJECTED, OUTCOME_UNKNOWN, FAILED_PERMANENT, "in_flight"}
)

# ProviderError.kind -> draft status. Only kinds where the provider certainly
# did not create a draft are retryable on a later run.
RETRY_POLICY: Dict[str, str] = {
    KIND_CREDENTIAL_MISSING: RETRY_LATER,
    KIND_CREDENTIAL_SCOPE: RETRY_LATER,
    KIND_UNREACHABLE: RETRY_LATER,
    KIND_RATE_LIMITED: RETRY_LATER,
    KIND_REJECTED: REJECTED,
    KIND_SERVER_ERROR: OUTCOME_UNKNOWN,
    KIND_TIMEOUT: OUTCOME_UNKNOWN,
    KIND_BAD_RESPONSE: OUTCOME_UNKNOWN,
}

R_PREFIX = "mail_relay:"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def _norm_addr(a: str) -> str:
    return a.strip().lower()


@dataclass(frozen=True)
class PartnerConfig:
    """Per-partner relay settings. ``recipient_allowlist`` empty = refuse all."""

    partner_id: str
    spool_dir: Path
    recipient_allowlist: FrozenSet[str]
    max_attempts: int = 3

    @classmethod
    def build(
        cls,
        *,
        partner_id: str,
        spool_dir: Union[str, Path],
        recipient_allowlist: Iterable[str],
        max_attempts: int = 3,
    ) -> "PartnerConfig":
        allow = set()
        for a in recipient_allowlist:
            if not isinstance(a, str) or not _ADDR_RE.match(a.strip()):
                raise ValueError("recipient_allowlist entries must be plain addresses")
            allow.add(_norm_addr(a))
        if not partner_id or not re.match(r"^[A-Za-z0-9._-]{1,64}$", partner_id):
            raise ValueError("partner_id must be 1-64 chars of [A-Za-z0-9._-]")
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        return cls(partner_id, Path(spool_dir), frozenset(allow), int(max_attempts))


# --------------------------------------------------------------------------
# Re-verification
# --------------------------------------------------------------------------


def _audit_row(storage: Any, entry_id: str) -> Optional[Dict[str, Any]]:
    r = storage.conn.execute(
        "SELECT id, decision, result, intent_envelope FROM audit_log WHERE id = ?",
        (entry_id,),
    ).fetchone()
    if r is None:
        return None
    return {k: r[k] for k in r.keys()}


def _row_intent(row: Dict[str, Any]) -> Any:
    env = json.loads(row["intent_envelope"])
    return json.loads(base64.b64decode(env["data"]).decode("utf-8"))


def _entry_denied(storage: Any, entry_id: str) -> bool:
    """True if any sealed-mail denial row (e.g. a content-binding refusal
    after a govern() ALLOW) names this govern() entry."""
    rows = storage.conn.execute(
        "SELECT metadata FROM audit_log WHERE result LIKE 'sealed_mail:%' AND metadata LIKE ?",
        (f"%{entry_id}%",),
    ).fetchall()
    for r in rows:
        try:
            md = json.loads(r["metadata"])
        except (ValueError, TypeError):
            return True  # unreadable row naming this entry: fail closed
        if isinstance(md, dict) and md.get("governed_entry_id") == entry_id:
            return True
    return False


def verify_spool_record(record: Any, storage: Any) -> Tuple[Optional[RelayDraft], List[str]]:
    """Re-verify one spooled draft against its seal and the signed audit chain.

    Returns ``(draft, [])`` when every check passes, else ``(None, reasons)``.
    Never raises. Does not check the recipient allowlist (``check_allowlist``).
    """
    if not isinstance(record, dict):
        return None, [R_PREFIX + "spool_not_object"]
    keys = set(record)
    if keys != SPOOL_KEYS:
        reasons = []
        if keys - SPOOL_KEYS:
            reasons.append(R_PREFIX + "spool_unexpected_fields")
        if SPOOL_KEYS - keys:
            reasons.append(R_PREFIX + "spool_missing_fields")
        return None, reasons
    if record["kind"] != "draft" or record["decision"] != "ALLOW":
        return None, [R_PREFIX + "spool_decision_not_allow"]
    to, cc = record["to"], record["cc"]
    subject, body = record["subject"], record["body"]
    entry_id, claimed = record["entry_id"], record["content_sha256"]
    if not (
        isinstance(to, list)
        and to
        and isinstance(cc, list)
        and all(isinstance(a, str) and a.strip() == a and a for a in to + cc)
        and isinstance(subject, str)
        and isinstance(body, str)
        and isinstance(entry_id, str)
        and entry_id
        and isinstance(claimed, str)
        and SHA256_RE.match(claimed)
    ):
        return None, [R_PREFIX + "spool_malformed"]

    # Seal: content hash over exactly what was approved and spooled.
    if not hmac.compare_digest(content_digest(to, subject, body, cc), claimed):
        return None, [R_PREFIX + "content_hash_mismatch"]
    try:
        assert_bound_content({"to": to, "subject": subject, "body": body, "cc": cc}, "mail")
    except ContentBindingError:
        return None, [R_PREFIX + "content_binding_refused"]

    # Binding to the signed chain: the govern() row is ALLOW for this text.
    try:
        row = _audit_row(storage, entry_id)
        if row is None:
            return None, [R_PREFIX + "governed_row_missing"]
        if row["decision"] != "ALLOW":
            return None, [R_PREFIX + "governed_decision_not_allow"]
        intent = _row_intent(row)
        payload = intent.get("payload") if isinstance(intent, dict) else None
        if not (
            isinstance(intent, dict)
            and intent.get("action") == "send_email"
            and isinstance(payload, dict)
            and payload.get("subject") == subject
            and payload.get("text") == body
        ):
            return None, [R_PREFIX + "governed_content_mismatch"]
        if _entry_denied(storage, entry_id):
            return None, [R_PREFIX + "governed_entry_denied"]
    except Exception:
        return None, [R_PREFIX + "governed_row_unreadable"]

    return (
        RelayDraft(
            to=tuple(to), cc=tuple(cc), subject=subject, body=body, content_sha256=claimed
        ),
        [],
    )


def check_allowlist(draft: RelayDraft, allowlist: FrozenSet[str], bcc: Iterable[str] = ()) -> List[str]:
    """Reason codes if any to/cc/bcc is outside the partner allowlist (empty
    allowlist refuses all), or a header value is unsafe. ``[]`` = pass."""
    if not allowlist:
        return [R_PREFIX + "allowlist_empty"]
    recips = list(draft.to) + list(draft.cc) + list(bcc)
    if any(not _ADDR_RE.match(a) for a in recips):
        return [R_PREFIX + "recipient_malformed"]
    outside = [a for a in recips if _norm_addr(a) not in allowlist]
    if outside:
        return [f"{R_PREFIX}recipient_not_allowlisted:{len(outside)}"]
    if any(c in draft.subject for c in "\r\n"):
        return [R_PREFIX + "subject_header_unsafe"]
    return []


# --------------------------------------------------------------------------
# State (beside the spool) and lock
# --------------------------------------------------------------------------


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


class _State:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: Dict[str, Any] = {"version": 1, "drafts": {}, "files": {}}

    def load(self) -> None:
        if not self.path.exists():
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if (
            not isinstance(data, dict)
            or data.get("version") != 1
            or not isinstance(data.get("drafts"), dict)
            or not isinstance(data.get("files"), dict)
        ):
            raise ValueError("relay state unreadable")
        self.data = data

    def save(self) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        blob = json.dumps(self.data, sort_keys=True, indent=1, allow_nan=False).encode("utf-8")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, blob)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, self.path)
        _fsync_dir(self.path.parent)

    def draft(self, content_sha: str) -> Dict[str, Any]:
        d = self.data["drafts"].get(content_sha)
        return dict(d) if isinstance(d, dict) else {}

    def set_draft(self, content_sha: str, **fields: Any) -> None:
        d = self.draft(content_sha)
        d.update(fields, updated_at=time.time())
        self.data["drafts"][content_sha] = d
        self.save()

    def file(self, file_sha: str) -> Dict[str, Any]:
        f = self.data["files"].get(file_sha)
        return dict(f) if isinstance(f, dict) else {}

    def set_file(self, file_sha: str, **fields: Any) -> None:
        self.data["files"][file_sha] = dict(fields, updated_at=time.time())
        self.save()


class _SpoolLock:
    """Exclusive, non-blocking ``flock`` on ``<spool>/.mail_relay.lock``."""

    def __init__(self, spool_dir: Path) -> None:
        self.path = spool_dir / LOCK_FILE
        self.fd: Optional[int] = None

    def acquire(self) -> bool:
        import fcntl  # POSIX only; ImportError -> run() reports relay_error

        fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return False
        self.fd = fd
        return True

    def release(self) -> None:
        if self.fd is not None:
            import fcntl

            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            finally:
                os.close(self.fd)
                self.fd = None


# --------------------------------------------------------------------------
# Relay
# --------------------------------------------------------------------------


class _StopRun(Exception):
    pass


class MailRelay:
    """Draft-only relay from a sealed spool to one partner mailbox."""

    def __init__(self, *, config: PartnerConfig, provider: DraftProvider, storage: Any) -> None:
        for attr in ("log_decision", "verify_chain", "conn"):
            if not hasattr(storage, attr):
                raise TypeError(f"storage must provide {attr} (AuditStorage)")
        if not callable(getattr(provider, "upload_draft", None)):
            raise TypeError("provider must implement upload_draft(RelayDraft)")
        self.config = config
        self.provider = provider
        self.storage = storage
        self._thread_lock = threading.Lock()

    # ---- audit ----
    def _audit(
        self,
        *,
        status: str,
        reasons: List[str],
        subject: Optional[str],
        body: Optional[str],
        entry_id: Optional[str],
        recipient_count: Optional[int],
        attempt: Optional[int] = None,
        error: Optional[Dict[str, Any]] = None,
        provider_ref: Optional[str] = None,
    ) -> str:
        decision = "ALLOW" if status == UPLOADED else "BLOCK"
        digests = {
            "subject_sha256": None if subject is None else _sha256(subject),
            "body_sha256": None if body is None else _sha256(body),
        }
        metadata = {
            "channel": "mail",
            "stage": "mail_relay",
            "status": status,
            "partner_id": self.config.partner_id,
            "provider": getattr(self.provider, "name", "unknown"),
            "governed_entry_id": entry_id,
            "recipient_count": recipient_count,
            "attempt": attempt,
            "error": error,
            "provider_ref_sha256": None if provider_ref is None else _sha256(provider_ref),
            **digests,
        }
        new_id = self.storage.log_decision(
            intent={
                "action": RELAY_AUDIT_ACTION,
                "channel": "mail",
                "partner_id": self.config.partner_id,
                "provider": metadata["provider"],
                **digests,
            },
            decision=decision,
            result=f"mail_relay:{status}",
            verification_score=1.0,
            risk_signal=0.0 if decision == "ALLOW" else 1.0,
            anomaly_signal=0.0,
            policy_reasons=[RELAY_STAGE_MARKER] + list(reasons),
            metadata=metadata,
            enqueue_review=False,
        )
        return str(new_id)

    def _audit_or_stop(self, out: Dict[str, Any], **kw: Any) -> None:
        try:
            out["audit_entry_id"] = self._audit(**kw)
        except Exception as exc:
            out["audit_entry_id"] = None
            out["audit_error"] = error_digest(exc)
            out["reasons"] = list(out.get("reasons") or []) + [R_PREFIX + "audit_write_failed"]
            raise _StopRun(AUDIT_FAILED) from None

    # ---- run ----
    def run(self) -> Dict[str, Any]:
        """Relay every pending spooled draft once. Never raises."""
        summary: Dict[str, Any] = {
            "status": "ok",
            "partner_id": self.config.partner_id,
            "provider": getattr(self.provider, "name", "unknown"),
            "results": [],
        }
        try:
            with self._thread_lock:
                self._run(summary)
        except Exception as exc:  # last line: nothing escapes
            summary["status"] = "relay_error"
            summary["error"] = error_digest(exc)
        counts: Dict[str, int] = {}
        for r in summary["results"]:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        summary["counts"] = counts
        log.info(
            "mail_relay run status=%s partner=%s counts=%s",
            summary["status"],
            self.config.partner_id,
            json.dumps(counts, sort_keys=True),
        )
        return summary

    def _run(self, summary: Dict[str, Any]) -> None:
        spool = self.config.spool_dir
        if not spool.is_dir():
            summary["status"] = "spool_missing"
            return
        lock = _SpoolLock(spool)
        if not lock.acquire():
            summary["status"] = "busy"
            return
        try:
            state = _State(spool / STATE_FILE)
            try:
                state.load()
            except Exception as exc:
                summary["status"] = "state_unreadable"
                summary["error"] = error_digest(exc)
                return
            try:
                chain = self.storage.verify_chain()
                chain_ok = bool(chain.get("valid"))
            except Exception as exc:
                summary["error"] = error_digest(exc)
                chain_ok = False
            if not chain_ok:
                summary["status"] = "audit_chain_invalid"
                return
            files = sorted(p for p in spool.iterdir() if SPOOL_NAME_RE.match(p.name))
            stopped: Optional[str] = None
            for path in files:
                if stopped is not None:
                    summary["results"].append({"file": path.name, "status": NOT_ATTEMPTED})
                    continue
                out: Dict[str, Any] = {"file": path.name}
                try:
                    stop = self._one(path, state, out)
                except _StopRun as s:
                    stop = str(s)
                    out.setdefault("status", AUDIT_FAILED)
                summary["results"].append(out)
                if stop:
                    stopped = stop
            if stopped is not None:
                summary["status"] = "stopped"
                summary["stopped_on"] = stopped
        finally:
            lock.release()

    def _one(self, path: Path, state: _State, out: Dict[str, Any]) -> Optional[str]:
        """Handle one spool file. Returns a stop reason to halt the run, else None."""
        st = os.lstat(path)
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_SPOOL_BYTES:
            out.update(status=SKIPPED, reasons=[R_PREFIX + "spool_file_not_regular_or_too_big"])
            return None
        raw = path.read_bytes()
        file_sha = hashlib.sha256(raw).hexdigest()
        prior_file = state.file(file_sha)
        if prior_file.get("status") == REFUSED:
            out.update(status=SKIPPED, reasons=list(prior_file.get("reasons") or []), prior=REFUSED)
            return None

        def _reject_constant(name: str) -> Any:
            raise ValueError("non-finite number in spool")

        subject: Optional[str] = None
        body: Optional[str] = None
        entry_id: Optional[str] = None
        try:
            record = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
        except Exception as exc:
            draft, reasons = None, [R_PREFIX + "spool_unparseable"]
            out["error"] = error_digest(exc)
        else:
            draft, reasons = verify_spool_record(record, self.storage)
            if isinstance(record, dict):
                s, b, e = record.get("subject"), record.get("body"), record.get("entry_id")
                subject = s if isinstance(s, str) else None
                body = b if isinstance(b, str) else None
                entry_id = e if isinstance(e, str) else None
        if draft is not None:
            reasons = check_allowlist(draft, self.config.recipient_allowlist)
        if draft is None or reasons:
            out.update(status=REFUSED, reasons=reasons)
            state.set_file(file_sha, status=REFUSED, reasons=reasons)
            self._audit_or_stop(
                out,
                status=REFUSED,
                reasons=reasons,
                subject=subject,
                body=body,
                entry_id=entry_id,
                recipient_count=None if draft is None else len(draft.to) + len(draft.cc),
                error=out.get("error"),
            )
            log.warning("mail_relay refused file_sha=%s reasons=%s", file_sha[:12], reasons)
            return None

        ch = draft.content_sha256
        out["content_sha256"] = ch
        prior = state.draft(ch)
        pstatus = prior.get("status")
        if pstatus == UPLOADED:
            out.update(status=ALREADY_UPLOADED)
            return None
        if pstatus in TERMINAL_DRAFT_STATES:
            # in_flight left by a crash is treated as outcome unknown: never re-sent.
            out.update(status=SKIPPED, prior=pstatus if pstatus != "in_flight" else OUTCOME_UNKNOWN)
            return None
        attempts = int(prior.get("attempts") or 0)
        if attempts >= self.config.max_attempts:
            state.set_draft(ch, status=FAILED_PERMANENT, attempts=attempts)
            out.update(status=SKIPPED, prior=FAILED_PERMANENT)
            return None

        attempt = attempts + 1
        state.set_draft(ch, status="in_flight", attempts=attempt, file=path.name)
        common = dict(
            subject=draft.subject,
            body=draft.body,
            entry_id=entry_id,
            recipient_count=len(draft.to) + len(draft.cc),
            attempt=attempt,
        )
        try:
            ref = self.provider.upload_draft(draft)
            if not isinstance(ref, str) or not ref:
                raise ProviderError(KIND_BAD_RESPONSE)
        except Exception as exc:
            if isinstance(exc, ProviderError):
                kind, err = exc.kind, exc.digest
            else:  # unknown client failure: assume the request may have gone out
                kind, err = KIND_TIMEOUT, error_digest(exc)
            new_status = RETRY_POLICY.get(kind, OUTCOME_UNKNOWN)
            if new_status == RETRY_LATER and attempt >= self.config.max_attempts:
                new_status = FAILED_PERMANENT
            reasons = [f"{R_PREFIX}provider_{kind}"]
            state.set_draft(ch, status=new_status, attempts=attempt, error=err)
            out.update(status=new_status, reasons=reasons, error=err)
            self._audit_or_stop(out, status=new_status, reasons=reasons, error=err, **common)
            log.warning("mail_relay provider failure content=%s kind=%s", ch[:12], kind)
            return f"provider_{kind}"  # circuit breaker: stop this run

        state.set_draft(
            ch, status=UPLOADED, attempts=attempt, provider_ref_sha256=_sha256(ref)
        )
        out.update(status=UPLOADED, provider_ref=ref)
        self._audit_or_stop(out, status=UPLOADED, reasons=[], provider_ref=ref, **common)
        log.info("mail_relay uploaded content=%s", ch[:12])
        return None


def provider_from_env(provider: str) -> DraftProvider:
    """Partner-side construction: token from ``HAVEN_RELAY_GMAIL_TOKEN`` or
    ``HAVEN_RELAY_GRAPH_TOKEN`` (read per upload, never stored)."""
    from .mail_relay_providers import GmailDraftsClient, GraphDraftsClient, env_token

    if provider == "gmail":
        return GmailDraftsClient(env_token("HAVEN_RELAY_GMAIL_TOKEN"))
    if provider == "outlook":
        return GraphDraftsClient(env_token("HAVEN_RELAY_GRAPH_TOKEN"))
    raise ValueError("provider must be 'gmail' or 'outlook'")


def main(argv: Optional[List[str]] = None) -> int:  # pragma: no cover - entrypoint
    import argparse

    p = argparse.ArgumentParser(description="Draft-only relay: sealed spool -> partner Drafts")
    p.add_argument("--spool", required=True)
    p.add_argument("--partner", required=True)
    p.add_argument("--provider", choices=("gmail", "outlook"), required=True)
    p.add_argument("--allowlist", default=os.environ.get("HAVEN_RELAY_ALLOWLIST", ""))
    args = p.parse_args(argv)
    from .mail import GovernedMail

    stack = GovernedMail._build_default_stack()  # same audit DB as sealed_mail_mcp
    cfg = PartnerConfig.build(
        partner_id=args.partner,
        spool_dir=args.spool,
        recipient_allowlist=[a for a in (s.strip() for s in args.allowlist.split(",")) if a],
    )
    relay = MailRelay(config=cfg, provider=provider_from_env(args.provider), storage=stack.engine.storage)
    out = relay.run()
    for r in out["results"]:
        r.pop("provider_ref", None)
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0 if out["status"] == "ok" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "MailRelay",
    "PartnerConfig",
    "RELAY_AUDIT_ACTION",
    "RETRY_POLICY",
    "check_allowlist",
    "provider_from_env",
    "verify_spool_record",
]
