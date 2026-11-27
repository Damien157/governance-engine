"""
Draft-only provider clients for ``governed_stack.mail_relay``.

Two clients, one interface (``DraftProvider.upload_draft``):

* ``GmailDraftsClient``: one HTTPS POST to the Gmail API ``users.drafts.create``
  endpoint (``/gmail/v1/users/me/drafts``). Scope the partner grants:
  ``gmail.compose`` at most.
* ``GraphDraftsClient``: one HTTPS POST to Microsoft Graph ``/me/messages``,
  which creates the message in the Drafts folder. Permission the partner
  grants: ``Mail.ReadWrite``.

Draft-only by construction: the only HTTP method is POST, the only URLs are
the two draft-creation endpoints above (``ALLOWED_ENDPOINTS``), and redirects
are refused so the bearer token can never be forwarded to another host. There
is no transmit, reply, forward, list, read or delete call in this module, and
``scripts/lint_no_bypass.py`` fails CI if a transmit endpoint, transmit scope
or read method string appears here.

Honest limit: Google has no draft-only scope. ``gmail.compose`` also lets the
token holder transmit mail, and ``Mail.ReadWrite`` lets the holder read and
delete the mailbox. The code never does either, but the token is more
powerful than this code; see docs/MAIL_RELAY.md.

Credentials: a ``TokenProvider`` callable supplied by the partner side (e.g.
``env_token("HAVEN_RELAY_GMAIL_TOKEN")``). The repo ships none, never mints,
refreshes, stores or logs tokens. Errors carry only
``{error_class, error_sha256, error_len}`` (``ProviderError.digest``); the
response body or exception text is never kept in clear.

Stdlib only (urllib, email, json): no new required dependency.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from email.message import EmailMessage
from email.policy import SMTP
from typing import Any, Callable, Dict, FrozenSet, Mapping, Optional, Protocol, Tuple

GMAIL_COMPOSE_SCOPE = "https://www.googleapis.com/auth/gmail.compose"
GMAIL_DRAFTS_CREATE_URL = "https://gmail.googleapis.com/gmail/v1/users/me/drafts"

GRAPH_MAIL_PERMISSION = "Mail.ReadWrite"
GRAPH_CREATE_MESSAGE_URL = "https://graph.microsoft.com/v1.0/me/messages"

# The only (method, url) pairs any transport in this module will issue.
ALLOWED_METHOD = "POST"
ALLOWED_ENDPOINTS: FrozenSet[str] = frozenset({GMAIL_DRAFTS_CREATE_URL, GRAPH_CREATE_MESSAGE_URL})

# Graph delegated permissions a relay token may carry (when the token is a
# readable JWT). Anything else, including any transmit permission, is refused
# before a request is made. Allowlist, not denylist.
GRAPH_ALLOWED_SCOPES: FrozenSet[str] = frozenset(
    {GRAPH_MAIL_PERMISSION, "User.Read", "openid", "profile", "email", "offline_access"}
)

DEFAULT_TIMEOUT_S = 15.0
MAX_RESPONSE_BYTES = 256 * 1024

# ProviderError kinds -> relay handling (see mail_relay.RETRY_POLICY).
KIND_CREDENTIAL_MISSING = "credential_missing"  # nothing sent
KIND_CREDENTIAL_SCOPE = "credential_scope_refused"  # nothing sent
KIND_UNREACHABLE = "unreachable"  # connection never made: nothing sent
KIND_RATE_LIMITED = "rate_limited"  # 429 / 408: provider did not process
KIND_REJECTED = "rejected"  # other 4xx: provider refused, will refuse again
KIND_SERVER_ERROR = "server_error"  # 5xx: draft may or may not exist
KIND_TIMEOUT = "timeout"  # request sent, no answer: draft may exist
KIND_BAD_RESPONSE = "bad_response"  # 2xx without a usable id: draft may exist
KIND_REFUSED_ENDPOINT = "refused_endpoint"  # programming error: nothing sent


def error_digest(exc: BaseException, detail: Optional[str] = None) -> Dict[str, Any]:
    """``{error_class, error_sha256, error_len}`` for an exception (never raw text)."""
    text = str(exc) if detail is None else detail
    raw = text.encode("utf-8", "surrogatepass")
    return {
        "error_class": type(exc).__name__,
        "error_sha256": hashlib.sha256(raw).hexdigest(),
        "error_len": len(text),
    }


class ProviderError(Exception):
    """Provider failure. ``str()`` is ``kind`` + status only; the detail text is
    digested at construction and not kept."""

    def __init__(self, kind: str, *, status: Optional[int] = None, detail: str = "") -> None:
        self.kind = kind
        self.status = status
        raw = (detail or "").encode("utf-8", "surrogatepass")
        self.digest: Dict[str, Any] = {
            "error_class": f"ProviderError:{kind}",
            "error_sha256": hashlib.sha256(raw).hexdigest(),
            "error_len": len(detail or ""),
        }
        super().__init__(f"{kind}" + (f":{status}" if status is not None else ""))


@dataclass(frozen=True)
class RelayDraft:
    """What a provider receives: verified, allowlisted draft content only."""

    to: Tuple[str, ...]
    cc: Tuple[str, ...]
    subject: str
    body: str
    content_sha256: str


class DraftProvider(Protocol):
    name: str

    def upload_draft(self, draft: RelayDraft) -> str:
        """Create ONE draft; return the provider's draft/message id. Raise
        ``ProviderError`` on failure. Must never transmit mail."""
        ...


TokenProvider = Callable[[], str]
# transport(method, url, headers, body, timeout) -> (status, response_bytes)
Transport = Callable[[str, str, Mapping[str, str], bytes, float], Tuple[int, bytes]]


def env_token(var_name: str) -> TokenProvider:
    """Read a bearer token from the partner's environment at call time."""

    def _get() -> str:
        tok = os.environ.get(var_name, "").strip()
        if not tok:
            raise ProviderError(KIND_CREDENTIAL_MISSING, detail=var_name)
        return tok

    return _get


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None  # never follow: a redirect would carry the bearer token


_OPENER = urllib.request.build_opener(_RefuseRedirects())


def urllib_transport(
    method: str, url: str, headers: Mapping[str, str], body: bytes, timeout: float
) -> Tuple[int, bytes]:
    """Stdlib HTTPS POST to an allowed endpoint. No redirects, bounded read."""
    if method != ALLOWED_METHOD or url not in ALLOWED_ENDPOINTS:
        raise ProviderError(KIND_REFUSED_ENDPOINT)
    req = urllib.request.Request(url, data=body, method=method, headers=dict(headers))
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            return int(resp.status), resp.read(MAX_RESPONSE_BYTES)
    except urllib.error.HTTPError as exc:
        try:
            data = exc.read(MAX_RESPONSE_BYTES)
        except Exception:
            data = b""
        return int(exc.code), data
    except (TimeoutError, socket.timeout) as exc:
        raise ProviderError(KIND_TIMEOUT, detail=str(exc)) from None
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, (TimeoutError, socket.timeout)):
            raise ProviderError(KIND_TIMEOUT, detail=str(reason)) from None
        if isinstance(reason, (ConnectionRefusedError, socket.gaierror)):
            raise ProviderError(KIND_UNREACHABLE, detail=str(reason)) from None
        raise ProviderError(KIND_TIMEOUT, detail=str(reason)) from None
    except OSError as exc:  # reset mid-exchange etc.: outcome unknown
        raise ProviderError(KIND_TIMEOUT, detail=str(exc)) from None


def _classify(status: int, data: bytes) -> str:
    """Return the created id on 2xx, else raise ``ProviderError``."""
    detail = data.decode("utf-8", "replace")
    if 200 <= status < 300:
        try:
            parsed = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ProviderError(KIND_BAD_RESPONSE, status=status, detail=detail) from None
        ident = parsed.get("id") if isinstance(parsed, dict) else None
        if not isinstance(ident, str) or not ident:
            raise ProviderError(KIND_BAD_RESPONSE, status=status, detail=detail)
        return ident
    if status in (408, 429):
        raise ProviderError(KIND_RATE_LIMITED, status=status, detail=detail)
    if 400 <= status < 500:
        raise ProviderError(KIND_REJECTED, status=status, detail=detail)
    raise ProviderError(KIND_SERVER_ERROR, status=status, detail=detail)


def _get_token(token_provider: TokenProvider) -> str:
    try:
        tok = token_provider()
    except ProviderError:
        raise
    except Exception as exc:
        raise ProviderError(KIND_CREDENTIAL_MISSING, detail=str(exc)) from None
    if not isinstance(tok, str) or not tok.strip():
        raise ProviderError(KIND_CREDENTIAL_MISSING)
    if any(c in tok for c in "\r\n"):
        raise ProviderError(KIND_CREDENTIAL_MISSING)
    return tok.strip()


def build_mime(draft: RelayDraft) -> bytes:
    """RFC 5322 message for Gmail's ``raw`` field. Raises ValueError on unsafe headers."""
    msg = EmailMessage(policy=SMTP)
    msg["To"] = ", ".join(draft.to)
    if draft.cc:
        msg["Cc"] = ", ".join(draft.cc)
    msg["Subject"] = draft.subject
    msg.set_content(draft.body)
    return msg.as_bytes()


class GmailDraftsClient:
    """Gmail ``users.drafts.create`` only (scope: ``gmail.compose`` at most)."""

    name = "gmail"
    scope = GMAIL_COMPOSE_SCOPE

    def __init__(
        self,
        token_provider: TokenProvider,
        *,
        transport: Transport = urllib_transport,
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self._token_provider = token_provider
        self._transport = transport
        self._timeout = timeout

    def upload_draft(self, draft: RelayDraft) -> str:
        raw = base64.urlsafe_b64encode(build_mime(draft)).decode("ascii")
        payload = json.dumps({"message": {"raw": raw}}).encode("utf-8")
        token = _get_token(self._token_provider)
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
        }
        status, data = self._transport(
            ALLOWED_METHOD, GMAIL_DRAFTS_CREATE_URL, headers, payload, self._timeout
        )
        return _classify(status, data)


def graph_token_scopes(token: str) -> Optional[FrozenSet[str]]:
    """Scopes in a Graph access token's ``scp`` claim, or None when the token is
    not a readable JWT (e.g. opaque consumer-account tokens). Not a signature
    check: used only to refuse over-broad tokens (tighten-only)."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    seg = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(seg.encode("ascii")).decode("utf-8"))
    except (ValueError, UnicodeError, binascii.Error):
        return None
    if not isinstance(claims, dict):
        return None
    scp = claims.get("scp")
    roles = claims.get("roles")
    scopes = set(scp.split()) if isinstance(scp, str) else set()
    if isinstance(roles, list):  # app-only tokens: always refused (not /me)
        scopes.update(f"role:{r}" for r in roles if isinstance(r, str))
    return frozenset(scopes)


class GraphDraftsClient:
    """Microsoft Graph ``POST /me/messages`` (creates in Drafts). Permission:
    ``Mail.ReadWrite``. Readable JWT tokens carrying any permission outside
    ``GRAPH_ALLOWED_SCOPES`` are refused before any request."""

    name = "outlook"
    scope = GRAPH_MAIL_PERMISSION

    def __init__(
        self,
        token_provider: TokenProvider,
        *,
        transport: Transport = urllib_transport,
        timeout: float = DEFAULT_TIMEOUT_S,
        require_readable_scopes: bool = False,
    ) -> None:
        self._token_provider = token_provider
        self._transport = transport
        self._timeout = timeout
        self._require_readable_scopes = require_readable_scopes

    def _check_scopes(self, token: str) -> None:
        scopes = graph_token_scopes(token)
        if scopes is None:
            if self._require_readable_scopes:
                raise ProviderError(KIND_CREDENTIAL_SCOPE, detail="opaque")
            return
        extra = sorted(scopes - GRAPH_ALLOWED_SCOPES)
        if extra or GRAPH_MAIL_PERMISSION not in scopes:
            raise ProviderError(KIND_CREDENTIAL_SCOPE, detail=" ".join(extra))

    def upload_draft(self, draft: RelayDraft) -> str:
        body = {
            "subject": draft.subject,
            "body": {"contentType": "Text", "content": draft.body},
            "toRecipients": [{"emailAddress": {"address": a}} for a in draft.to],
            "ccRecipients": [{"emailAddress": {"address": a}} for a in draft.cc],
        }
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        token = _get_token(self._token_provider)
        self._check_scopes(token)
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
        }
        status, data = self._transport(
            ALLOWED_METHOD, GRAPH_CREATE_MESSAGE_URL, headers, payload, self._timeout
        )
        return _classify(status, data)


__all__ = [
    "ALLOWED_ENDPOINTS",
    "DraftProvider",
    "GMAIL_COMPOSE_SCOPE",
    "GMAIL_DRAFTS_CREATE_URL",
    "GRAPH_ALLOWED_SCOPES",
    "GRAPH_CREATE_MESSAGE_URL",
    "GRAPH_MAIL_PERMISSION",
    "GmailDraftsClient",
    "GraphDraftsClient",
    "ProviderError",
    "RelayDraft",
    "TokenProvider",
    "Transport",
    "build_mime",
    "env_token",
    "error_digest",
    "graph_token_scopes",
    "urllib_transport",
]
