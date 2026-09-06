"""Customer-operable HTTP sidecar over the live govern gate (check-only).

Stdlib ``http.server`` only — no FastAPI / heavy web deps.
Never sends mail/calendar/social; ``POST /v1/check`` runs govern/adapters
in check mode only. Sketches stay off this path.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from .calendar import intent_for_scan as calendar_intent_for_scan
from .mail import intent_for_scan as mail_intent_for_scan
from .social import intent_for_scan as social_intent_for_scan
from .stack import GovernedStack, ensure_import_paths

ensure_import_paths()

try:
    from certified_governance_unified import CryptoEngine
except Exception:  # pragma: no cover
    CryptoEngine = None  # type: ignore

# Package version (customer ops milestone bumps __init__.__version__).
try:
    from . import __version__ as _PKG_VERSION
except Exception:  # pragma: no cover
    _PKG_VERSION = "0.0.0"

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_ARTIFACTS = _REPO_ROOT / "artifacts" / "customer"
_DEFAULT_DB = _DEFAULT_ARTIFACTS / "audit.db"
_DEFAULT_KEY = _DEFAULT_ARTIFACTS / "signing_key.pem"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    val = os.environ.get(name)
    if val is None or val == "":
        return default
    return val


def load_sidecar_config(
    *,
    db_path: Optional[str] = None,
    signing_key_path: Optional[str] = None,
    host: Optional[str] = None,
    port: Optional[int] = None,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve sidecar config from explicit args + env (explicit wins)."""
    db = db_path or _env("GOVERNANCE_DB_PATH") or str(_DEFAULT_DB)
    key = signing_key_path or _env("GOVERNANCE_SIGNING_KEY_PATH") or str(_DEFAULT_KEY)
    h = host or _env("GOVERNANCE_HOST") or DEFAULT_HOST
    p_raw = port if port is not None else _env("GOVERNANCE_PORT")
    if p_raw is None:
        p = DEFAULT_PORT
    else:
        p = int(p_raw)
    key_hdr = api_key if api_key is not None else _env("GOVERNANCE_API_KEY")
    require_persisted = _env("GOVERNANCE_REQUIRE_PERSISTED_KEY", "0") == "1"
    return {
        "db_path": db,
        "signing_key_path": key,
        "host": h,
        "port": p,
        "api_key": key_hdr,
        "require_persisted_key": require_persisted,
    }


def _run_coro(coro: Any) -> Any:
    """Run an async coroutine from a sync HTTP handler thread."""

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


class SidecarService:
    """Holds one GovernedStack + readiness / check helpers for the HTTP layer."""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        stack: Optional[GovernedStack] = None,
        *,
        crypto: Any = None,
    ) -> None:
        self.config = dict(config or load_sidecar_config())
        self.api_key: Optional[str] = self.config.get("api_key")
        if stack is not None:
            self.stack = stack
        else:
            self.stack = self._build_stack(crypto=crypto)

    def _build_stack(self, crypto: Any = None) -> GovernedStack:
        db_path = str(self.config["db_path"])
        key_path = str(self.config["signing_key_path"])
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        Path(key_path).parent.mkdir(parents=True, exist_ok=True)
        eng_crypto = crypto
        if eng_crypto is None and CryptoEngine is not None:
            # Persist/load PEM when path is set; ephemeral only if allowed.
            require = bool(self.config.get("require_persisted_key"))
            if require and not Path(key_path).is_file():
                # CryptoEngine generates+persists when path given and missing.
                pass
            eng_crypto = CryptoEngine(private_key_path=key_path)
        return GovernedStack(
            config={
                "db_path": db_path,
                "signing_key_path": key_path,
                "log_level": int(self.config.get("log_level", 40)),
                "require_persisted_key": bool(self.config.get("require_persisted_key")),
            },
            crypto=eng_crypto,
        )

    # ------------------------------------------------------------------
    # Readiness
    # ------------------------------------------------------------------

    def _signing_configured(self) -> Tuple[bool, str]:
        key_path = self.config.get("signing_key_path")
        if key_path and Path(str(key_path)).is_file():
            return True, "signing_key_path exists"
        if _env("GOVERNANCE_SIGNING_KEY_PEM"):
            return True, "GOVERNANCE_SIGNING_KEY_PEM configured"
        env_path = _env("GOVERNANCE_SIGNING_KEY_PATH")
        if env_path and Path(env_path).is_file():
            return True, "GOVERNANCE_SIGNING_KEY_PATH exists"
        return False, "signing key path missing / provider not configured"

    def _db_parent_writable(self) -> Tuple[bool, str]:
        db_path = Path(str(self.config["db_path"]))
        parent = db_path.parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return False, f"audit db parent not creatable: {exc}"
        if not os.access(parent, os.W_OK):
            return False, f"audit db parent not writable: {parent}"
        return True, f"audit db parent writable: {parent}"

    def readiness(self) -> Tuple[bool, List[str]]:
        reasons: List[str] = []
        ok_key, msg_key = self._signing_configured()
        if not ok_key:
            reasons.append(msg_key)
        ok_db, msg_db = self._db_parent_writable()
        if not ok_db:
            reasons.append(msg_db)
        return (len(reasons) == 0), reasons

    # ------------------------------------------------------------------
    # Check (never send)
    # ------------------------------------------------------------------

    async def check_async(self, body: Dict[str, Any]) -> Dict[str, Any]:
        channel = str(body.get("channel") or "").strip().lower()
        token = body.get("token")
        if not isinstance(token, str) or not token.strip():
            return {
                "decision": "BLOCK",
                "reasons": ["sidecar: missing token"],
                "error_code": "GOV_AUTH_FAILED",
                "latency_ms": 0.0,
                "entry_id": None,
            }
        if channel == "mail":
            subject = str(body.get("subject") or "")
            # Prefer body; accept text as alias. Never require to/cc for scan.
            mail_body = body.get("body")
            if mail_body is None:
                mail_body = body.get("text") or ""
            intent = mail_intent_for_scan(subject, str(mail_body))
        elif channel == "calendar":
            intent = calendar_intent_for_scan(
                summary=str(body.get("summary") or ""),
                description=str(body.get("description") or ""),
                location=str(body.get("location") or ""),
            )
        elif channel == "social":
            intent = social_intent_for_scan(
                text=str(body.get("text") or body.get("body") or ""),
                platform=str(body.get("platform") or ""),
            )
        elif channel == "raw":
            raw_intent = body.get("intent")
            if raw_intent is None and isinstance(body.get("action"), str):
                # Allow flattened raw: action + other fields at top level.
                raw_intent = {
                    k: v
                    for k, v in body.items()
                    if k not in ("channel", "token")
                }
            if not isinstance(raw_intent, dict):
                env = await self.stack.govern({"action": ""}, token)
                return self._slim_envelope(env)
            intent = raw_intent
        else:
            return {
                "decision": "BLOCK",
                "reasons": [f"sidecar: unknown channel {channel!r}"],
                "error_code": "GOV_INTENT_INVALID",
                "latency_ms": 0.0,
                "entry_id": None,
            }

        env = await self.stack.govern(intent, token)
        return self._slim_envelope(env)

    def check(self, body: Dict[str, Any]) -> Dict[str, Any]:
        return _run_coro(self.check_async(body))

    @staticmethod
    def _slim_envelope(env: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "decision": env.get("decision", "BLOCK"),
            "reasons": env.get("reasons"),
            "error_code": env.get("error_code"),
            "latency_ms": env.get("latency_ms"),
        }
        if env.get("entry_id") is not None:
            out["entry_id"] = env.get("entry_id")
        return out

    def list_pending_reviews(self, limit: int = 50) -> List[Dict[str, Any]]:
        eng = getattr(self.stack, "engine", None)
        if eng is None or not hasattr(eng, "list_pending_reviews"):
            return []
        return list(eng.list_pending_reviews(limit=limit))

    def metrics_prometheus(self) -> str:
        metrics = getattr(self.stack, "metrics", None)
        if metrics is None:
            return "# no metrics\n"
        return metrics.prometheus_text()

    def issue_token(self, user: str = "customer", role: str = "user") -> str:
        return self.stack.issue_token(user, role)


def make_handler(service: SidecarService) -> type:
    """Build a BaseHTTPRequestHandler subclass bound to ``service``."""

    class SidecarHandler(BaseHTTPRequestHandler):
        server_version = f"GovernedSidecar/{_PKG_VERSION}"

        def log_message(self, fmt: str, *args: Any) -> None:  # quieter tests
            if os.environ.get("GOVERNANCE_SIDECAR_VERBOSE") == "1":
                super().log_message(fmt, *args)

        def _send(
            self,
            code: int,
            body: Any,
            *,
            content_type: str = "application/json",
        ) -> None:
            if isinstance(body, (dict, list)):
                raw = json.dumps(body).encode("utf-8")
            elif isinstance(body, str):
                raw = body.encode("utf-8")
            else:
                raw = bytes(body)
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _read_json(self) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
            length = int(self.headers.get("Content-Length") or "0")
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                data = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError as exc:
                return None, f"invalid JSON: {exc}"
            if not isinstance(data, dict):
                return None, "JSON body must be an object"
            return data, None

        def _check_api_key(self) -> bool:
            expected = service.api_key
            if not expected:
                return True
            got = self.headers.get("X-API-Key") or self.headers.get("x-api-key")
            if got != expected:
                self._send(
                    401,
                    {"error": "unauthorized", "reason": "missing or invalid X-API-Key"},
                )
                return False
            return True

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path.rstrip("/") or "/"
            if path == "/health":
                self._send(
                    200,
                    {"status": "ok", "version": _PKG_VERSION},
                )
                return
            if path == "/ready":
                ok, reasons = service.readiness()
                if ok:
                    self._send(200, {"status": "ready", "reasons": []})
                else:
                    self._send(
                        503,
                        {"status": "not_ready", "reasons": reasons},
                    )
                return
            if path == "/metrics":
                text = service.metrics_prometheus()
                self._send(200, text, content_type="text/plain; version=0.0.4")
                return
            self._send(404, {"error": "not_found", "path": path})

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path.rstrip("/") or "/"
            if path.startswith("/v1"):
                if not self._check_api_key():
                    return
            if path == "/v1/check":
                body, err = self._read_json()
                if err:
                    self._send(400, {"error": "bad_request", "reason": err})
                    return
                assert body is not None
                try:
                    result = service.check(body)
                except Exception as exc:  # pragma: no cover
                    self._send(
                        500,
                        {
                            "decision": "ERROR",
                            "reasons": [f"sidecar_internal:{exc}"],
                            "error_code": "GOV_INTERNAL",
                            "latency_ms": None,
                        },
                    )
                    return
                self._send(200, result)
                return
            if path == "/v1/review/list":
                length = int(self.headers.get("Content-Length") or "0")
                qs = parse_qs(urlparse(self.path).query)
                limit = 50
                if length > 0:
                    body, err = self._read_json()
                    if err:
                        self._send(400, {"error": "bad_request", "reason": err})
                        return
                    assert body is not None
                    limit = int(body.get("limit") or qs.get("limit", ["50"])[0])
                else:
                    limit = int((qs.get("limit") or ["50"])[0])
                pending = service.list_pending_reviews(limit=limit)
                self._send(
                    200,
                    {"ok": True, "pending": pending, "count": len(pending)},
                )
                return
            self._send(404, {"error": "not_found", "path": path})

    return SidecarHandler


def create_server(
    service: Optional[SidecarService] = None,
    *,
    host: Optional[str] = None,
    port: Optional[int] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Tuple[ThreadingHTTPServer, SidecarService]:
    """Construct a ThreadingHTTPServer bound to host:port."""
    svc = service or SidecarService(config=config)
    h = host or svc.config.get("host") or DEFAULT_HOST
    p = int(port if port is not None else svc.config.get("port") or DEFAULT_PORT)
    handler = make_handler(svc)
    httpd = ThreadingHTTPServer((str(h), int(p)), handler)
    return httpd, svc


def serve_forever(
    *,
    host: Optional[str] = None,
    port: Optional[int] = None,
    config: Optional[Dict[str, Any]] = None,
    service: Optional[SidecarService] = None,
) -> None:
    """Block serving the sidecar (used by ``scripts/run_sidecar.py``)."""
    httpd, svc = create_server(service=service, host=host, port=port, config=config)
    addr = httpd.server_address
    print(
        f"governed sidecar listening on http://{addr[0]!s}:{addr[1]} "
        f"(version={_PKG_VERSION} db={svc.config.get('db_path')})"
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down sidecar")
    finally:
        httpd.server_close()


__all__ = [
    "SidecarService",
    "load_sidecar_config",
    "make_handler",
    "create_server",
    "serve_forever",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
]
