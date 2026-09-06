"""Customer-operable HTTP sidecar over the live govern gate (check-only).

Stdlib ``http.server`` only — no FastAPI / heavy web deps.
Never sends mail/calendar/social; ``POST /v1/check`` runs govern/adapters
in check mode only. Sketches stay off this path.

0.4.1: multi-tenant lite (API-key → tenant + isolated audit DB) and
in-memory rate limits per tenant on ``/v1/*``.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from .calendar import intent_for_scan as calendar_intent_for_scan
from .contracts import GOV_RATE_LIMIT
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
_DEFAULT_TENANTS_ROOT = _REPO_ROOT / "artifacts" / "tenants"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080
DEFAULT_RATE_LIMIT_PER_MIN = 60


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
    rate_limit_per_min: Optional[int] = None,
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
    if rate_limit_per_min is not None:
        rl = int(rate_limit_per_min)
    else:
        rl = int(_env("GOVERNANCE_RATE_LIMIT_PER_MIN", str(DEFAULT_RATE_LIMIT_PER_MIN)) or str(DEFAULT_RATE_LIMIT_PER_MIN))
    return {
        "db_path": db,
        "signing_key_path": key,
        "host": h,
        "port": p,
        "api_key": key_hdr,
        "require_persisted_key": require_persisted,
        "rate_limit_per_min": rl,
        "master_api_key": _env("GOVERNANCE_MASTER_API_KEY"),
    }


def _run_coro(coro: Any) -> Any:
    """Run an async coroutine from a sync HTTP handler thread."""

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


# ---------------------------------------------------------------------------
# Rate limiting (sliding window, in-memory, per tenant / bucket key)
# ---------------------------------------------------------------------------


class RateLimiter:
    """Thread-safe sliding-window limiter: ``limit`` requests per ``window_s``.

    ``limit <= 0`` disables limiting (always allow).
    """

    def __init__(self, limit: int = DEFAULT_RATE_LIMIT_PER_MIN, window_s: float = 60.0) -> None:
        self.limit = int(limit)
        self.window_s = float(window_s)
        self._lock = threading.Lock()
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        if self.limit <= 0:
            return True
        now = time.monotonic()
        cutoff = now - self.window_s
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= cutoff:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True

    def reset(self, key: Optional[str] = None) -> None:
        with self._lock:
            if key is None:
                self._hits.clear()
            else:
                self._hits.pop(key, None)


# ---------------------------------------------------------------------------
# Multi-tenant lite
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TenantSpec:
    """One tenant's bind: API key + isolated audit DB (+ optional signing key)."""

    tenant_id: str
    api_key: str
    db_path: str
    signing_key_path: str


def _parse_api_keys_csv(raw: str) -> Dict[str, str]:
    """Parse ``tenant1:key1,tenant2:key2`` → {tenant_id: api_key}."""
    out: Dict[str, str] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            raise ValueError(f"GOVERNANCE_API_KEYS entry missing ':': {part!r}")
        tid, key = part.split(":", 1)
        tid = tid.strip()
        key = key.strip()
        if not tid or not key:
            raise ValueError(f"GOVERNANCE_API_KEYS empty tenant or key in {part!r}")
        out[tid] = key
    return out


def _tenant_specs_from_env(
    *,
    default_signing_key: str,
    tenants_root: Optional[Path] = None,
) -> Optional[Dict[str, TenantSpec]]:
    """Build tenant specs from GOVERNANCE_TENANTS_JSON or GOVERNANCE_API_KEYS.

    Returns None when neither multi-tenant env var is set (single-tenant mode).
    """
    root = tenants_root or _DEFAULT_TENANTS_ROOT
    json_raw = _env("GOVERNANCE_TENANTS_JSON")
    if json_raw:
        data = json.loads(json_raw)
        if not isinstance(data, dict):
            raise ValueError("GOVERNANCE_TENANTS_JSON must be a JSON object")
        specs: Dict[str, TenantSpec] = {}
        for tid, cfg in data.items():
            tid_s = str(tid).strip()
            if not tid_s:
                continue
            if not isinstance(cfg, dict):
                raise ValueError(f"tenant {tid_s!r} config must be an object")
            api_key = str(cfg.get("api_key") or "").strip()
            if not api_key:
                raise ValueError(f"tenant {tid_s!r} missing api_key")
            db_path = str(cfg.get("db_path") or (root / tid_s / "audit.db"))
            sk = str(cfg.get("signing_key_path") or "").strip()
            if not sk:
                per = root / tid_s / "signing_key.pem"
                sk = str(per) if per.is_file() else default_signing_key
            specs[tid_s] = TenantSpec(
                tenant_id=tid_s,
                api_key=api_key,
                db_path=db_path,
                signing_key_path=sk,
            )
        return specs or None

    csv_raw = _env("GOVERNANCE_API_KEYS")
    if csv_raw:
        mapping = _parse_api_keys_csv(csv_raw)
        specs = {}
        for tid, api_key in mapping.items():
            per_key = root / tid / "signing_key.pem"
            sk = str(per_key) if per_key.is_file() else default_signing_key
            specs[tid] = TenantSpec(
                tenant_id=tid,
                api_key=api_key,
                db_path=str(root / tid / "audit.db"),
                signing_key_path=sk,
            )
        return specs or None

    return None


class TenantRegistry:
    """Resolve ``X-API-Key`` → tenant id + ``SidecarService`` (isolated DB).

    Optional master key (``GOVERNANCE_MASTER_API_KEY``) + ``X-Tenant-Id`` selects
    any configured tenant. Primary auth remains a tenant-mapped API key.
    """

    def __init__(
        self,
        specs: Dict[str, TenantSpec],
        *,
        master_api_key: Optional[str] = None,
        rate_limit_per_min: int = DEFAULT_RATE_LIMIT_PER_MIN,
        base_config: Optional[Dict[str, Any]] = None,
        crypto_factory: Optional[Callable[[str], Any]] = None,
        service_factory: Optional[Callable[[TenantSpec], "SidecarService"]] = None,
    ) -> None:
        if not specs:
            raise ValueError("TenantRegistry requires at least one tenant")
        self.specs = dict(specs)
        self.master_api_key = master_api_key or None
        self.base_config = dict(base_config or {})
        self.rate_limiter = RateLimiter(rate_limit_per_min)
        self._crypto_factory = crypto_factory
        self._service_factory = service_factory
        self._services: Dict[str, SidecarService] = {}
        self._lock = threading.Lock()
        self._api_key_index: Dict[str, str] = {}
        for spec in self.specs.values():
            if spec.api_key in self._api_key_index:
                raise ValueError(f"duplicate API key for tenants {self._api_key_index[spec.api_key]!r} and {spec.tenant_id!r}")
            self._api_key_index[spec.api_key] = spec.tenant_id

    @classmethod
    def from_env(
        cls,
        base_config: Optional[Dict[str, Any]] = None,
        *,
        crypto_factory: Optional[Callable[[str], Any]] = None,
        service_factory: Optional[Callable[[TenantSpec], "SidecarService"]] = None,
    ) -> Optional["TenantRegistry"]:
        cfg = dict(base_config or load_sidecar_config())
        default_key = str(cfg.get("signing_key_path") or _DEFAULT_KEY)
        specs = _tenant_specs_from_env(default_signing_key=default_key)
        if specs is None:
            return None
        return cls(
            specs,
            master_api_key=cfg.get("master_api_key") or _env("GOVERNANCE_MASTER_API_KEY"),
            rate_limit_per_min=int(cfg.get("rate_limit_per_min") or DEFAULT_RATE_LIMIT_PER_MIN),
            base_config=cfg,
            crypto_factory=crypto_factory,
            service_factory=service_factory,
        )

    def tenant_ids(self) -> List[str]:
        return sorted(self.specs.keys())

    def get_service(self, tenant_id: str) -> "SidecarService":
        with self._lock:
            svc = self._services.get(tenant_id)
            if svc is not None:
                return svc
            spec = self.specs[tenant_id]
            Path(spec.db_path).parent.mkdir(parents=True, exist_ok=True)
            Path(spec.signing_key_path).parent.mkdir(parents=True, exist_ok=True)
            if self._service_factory is not None:
                svc = self._service_factory(spec)
            else:
                svc = self._build_service(spec)
            self._services[tenant_id] = svc
            return svc

    def _build_service(self, spec: TenantSpec) -> "SidecarService":
        crypto = None
        if self._crypto_factory is not None:
            crypto = self._crypto_factory(spec.signing_key_path)
        cfg = {
            **self.base_config,
            "db_path": spec.db_path,
            "signing_key_path": spec.signing_key_path,
            "api_key": spec.api_key,
            # Per-tenant services do not nest another registry.
            "_skip_registry": True,
        }
        return SidecarService(config=cfg, crypto=crypto)

    def resolve(
        self,
        api_key: Optional[str],
        tenant_header: Optional[str] = None,
    ) -> Tuple[Optional[str], Optional["SidecarService"], Optional[str]]:
        """Return ``(tenant_id, service, error_reason)``."""
        if not api_key:
            return None, None, "missing or invalid X-API-Key"
        if self.master_api_key and api_key == self.master_api_key:
            tid = (tenant_header or "").strip()
            if not tid:
                return None, None, "X-Tenant-Id required with master key"
            if tid not in self.specs:
                return None, None, f"unknown tenant {tid!r}"
            return tid, self.get_service(tid), None
        mapped = self._api_key_index.get(api_key)
        if mapped is None:
            return None, None, "missing or invalid X-API-Key"
        tid = mapped
        if tenant_header and tenant_header.strip() and tenant_header.strip() != tid:
            return None, None, "X-Tenant-Id mismatch for API key"
        return tid, self.get_service(tid), None

    def readiness(self) -> Tuple[bool, List[str]]:
        reasons: List[str] = []
        for tid in self.tenant_ids():
            spec = self.specs[tid]
            key_path = Path(spec.signing_key_path)
            if not key_path.is_file() and not _env("GOVERNANCE_SIGNING_KEY_PEM"):
                reasons.append(f"tenant {tid}: signing key path missing: {key_path}")
            db_parent = Path(spec.db_path).parent
            try:
                db_parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                reasons.append(f"tenant {tid}: audit db parent not creatable: {exc}")
                continue
            if not os.access(db_parent, os.W_OK):
                reasons.append(f"tenant {tid}: audit db parent not writable: {db_parent}")
        return (len(reasons) == 0), reasons

    def metrics_prometheus(self) -> str:
        parts: List[str] = []
        for tid in self.tenant_ids():
            parts.append(f"# tenant={tid}")
            try:
                parts.append(self.get_service(tid).metrics_prometheus())
            except Exception as exc:  # pragma: no cover
                parts.append(f"# metrics_error tenant={tid}: {exc}\n")
        return "\n".join(parts) if parts else "# no metrics\n"


class SidecarService:
    """Holds one GovernedStack + readiness / check helpers for the HTTP layer.

    When multi-tenant env is set (or ``registry=`` passed), HTTP resolves a
    per-tenant service via ``TenantRegistry``; otherwise single-tenant behavior
    is unchanged.
    """

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        stack: Optional[GovernedStack] = None,
        *,
        crypto: Any = None,
        registry: Optional[TenantRegistry] = None,
    ) -> None:
        self.config = dict(config or load_sidecar_config())
        self.api_key: Optional[str] = self.config.get("api_key")
        skip_registry = bool(self.config.pop("_skip_registry", False))
        if registry is not None:
            self.registry: Optional[TenantRegistry] = registry
        elif skip_registry:
            self.registry = None
        else:
            self.registry = TenantRegistry.from_env(self.config)
        rl = int(self.config.get("rate_limit_per_min") or DEFAULT_RATE_LIMIT_PER_MIN)
        if self.registry is not None:
            self.rate_limiter = self.registry.rate_limiter
        else:
            self.rate_limiter = RateLimiter(rl)
        if stack is not None:
            self.stack = stack
        elif self.registry is not None:
            # Default stack = first tenant (issue_token / metrics fallback).
            first = self.registry.tenant_ids()[0]
            self.stack = self.registry.get_service(first).stack
        else:
            self.stack = self._build_stack(crypto=crypto)

    def _build_stack(self, crypto: Any = None) -> GovernedStack:
        db_path = str(self.config["db_path"])
        key_path = str(self.config["signing_key_path"])
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        Path(key_path).parent.mkdir(parents=True, exist_ok=True)
        eng_crypto = crypto
        if eng_crypto is None and CryptoEngine is not None:
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
        if self.registry is not None:
            return self.registry.readiness()
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
        if self.registry is not None:
            return self.registry.metrics_prometheus()
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

        def _api_key_header(self) -> Optional[str]:
            return self.headers.get("X-API-Key") or self.headers.get("x-api-key")

        def _tenant_header(self) -> Optional[str]:
            return self.headers.get("X-Tenant-Id") or self.headers.get("x-tenant-id")

        def _authorize_v1(self) -> Tuple[Optional[SidecarService], Optional[str]]:
            """Auth + rate-limit for ``/v1/*``. Returns (tenant_service, tenant_id) or sends error."""
            registry = service.registry
            if registry is not None:
                tid, tenant_svc, err = registry.resolve(
                    self._api_key_header(),
                    self._tenant_header(),
                )
                if err or tenant_svc is None or tid is None:
                    self._send(
                        401,
                        {
                            "error": "unauthorized",
                            "reason": err or "missing or invalid X-API-Key",
                        },
                    )
                    return None, None
                if not service.rate_limiter.allow(tid):
                    self._send(
                        429,
                        {"error": "rate_limited", "error_code": GOV_RATE_LIMIT},
                    )
                    return None, None
                return tenant_svc, tid

            # Single-tenant: optional API key gate.
            expected = service.api_key
            if expected:
                got = self._api_key_header()
                if got != expected:
                    self._send(
                        401,
                        {
                            "error": "unauthorized",
                            "reason": "missing or invalid X-API-Key",
                        },
                    )
                    return None, None
            bucket = "default"
            if not service.rate_limiter.allow(bucket):
                self._send(
                    429,
                    {"error": "rate_limited", "error_code": GOV_RATE_LIMIT},
                )
                return None, None
            return service, None

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
                tenant_svc, tenant_id = self._authorize_v1()
                if tenant_svc is None:
                    return
            else:
                tenant_svc, tenant_id = service, None
            if path == "/v1/check":
                body, err = self._read_json()
                if err:
                    self._send(400, {"error": "bad_request", "reason": err})
                    return
                assert body is not None
                try:
                    result = tenant_svc.check(body)
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
                if tenant_id is not None:
                    result = dict(result)
                    result["tenant_id"] = tenant_id
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
                pending = tenant_svc.list_pending_reviews(limit=limit)
                payload: Dict[str, Any] = {
                    "ok": True,
                    "pending": pending,
                    "count": len(pending),
                }
                if tenant_id is not None:
                    payload["tenant_id"] = tenant_id
                self._send(200, payload)
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
    tenants = ""
    if svc.registry is not None:
        tenants = f" tenants={svc.registry.tenant_ids()}"
    print(
        f"governed sidecar listening on http://{addr[0]!s}:{addr[1]} "
        f"(version={_PKG_VERSION} db={svc.config.get('db_path')}{tenants})"
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down sidecar")
    finally:
        httpd.server_close()


__all__ = [
    "SidecarService",
    "TenantRegistry",
    "TenantSpec",
    "RateLimiter",
    "load_sidecar_config",
    "make_handler",
    "create_server",
    "serve_forever",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "DEFAULT_RATE_LIMIT_PER_MIN",
]
