"""Customer-ops sidecar HTTP tests (stdlib server + urllib)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_PATHS = (
    ROOT / "src",
    ROOT,
    ROOT / "hais",
    ROOT / "imprint" / "src",
    ROOT / "haven2" / "src",
)
for p in reversed(_PATHS):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from certified_governance_unified import CryptoEngine  # noqa: E402
from governed_stack import __version__  # noqa: E402
from governed_stack.sidecar import (  # noqa: E402
    RateLimiter,
    SidecarService,
    TenantRegistry,
    TenantSpec,
    create_server,
    load_sidecar_config,
)
from governed_stack.stack import GovernedStack  # noqa: E402

_MT_ENV = (
    "GOVERNANCE_TENANTS_JSON",
    "GOVERNANCE_API_KEYS",
    "GOVERNANCE_MASTER_API_KEY",
    "GOVERNANCE_RATE_LIMIT_PER_MIN",
    "GOVERNANCE_API_KEY",
    "GOVERNANCE_DB_PATH",
    "GOVERNANCE_SIGNING_KEY_PATH",
    "GOVERNANCE_HOST",
    "GOVERNANCE_PORT",
)


def _http_json(
    url: str,
    *,
    method: str = "GET",
    body: dict | None = None,
    headers: dict | None = None,
    timeout: float = 30.0,
) -> tuple[int, dict | str]:
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            code = resp.status
            ctype = resp.headers.get("Content-Type", "")
            if "json" in ctype or raw[:1] in ("{", "["):
                return code, json.loads(raw)
            return code, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            payload: dict | str = json.loads(raw)
        except json.JSONDecodeError:
            payload = raw
        return exc.code, payload


def _close_stack(stack: GovernedStack) -> None:
    eng = getattr(stack, "engine", None)
    storage = getattr(eng, "storage", None) if eng else None
    if storage is not None and hasattr(storage, "close"):
        try:
            storage.close()
        except Exception:
            pass


class TestSidecarHTTP(unittest.TestCase):
    """Spin a local ThreadingHTTPServer; hit with urllib."""

    @classmethod
    def setUpClass(cls):
        cls._t0 = time.time()
        cls._tmpdir = tempfile.TemporaryDirectory()
        td = Path(cls._tmpdir.name)
        cls.db_path = str(td / "audit.db")
        cls.key_path = str(td / "signing_key.pem")
        # Persist a real key so /ready is 200.
        cls.crypto = CryptoEngine(private_key_path=cls.key_path)
        assert Path(cls.key_path).is_file()
        stack = GovernedStack(
            config={
                "db_path": cls.db_path,
                "signing_key_path": cls.key_path,
                "log_level": 50,
            },
            crypto=cls.crypto,
        )
        cls.service = SidecarService(
            config={
                "db_path": cls.db_path,
                "signing_key_path": cls.key_path,
                "host": "127.0.0.1",
                "port": 0,
                "api_key": None,
                "log_level": 50,
                "rate_limit_per_min": 0,  # unlimited for suite volume
            },
            stack=stack,
        )
        cls.httpd, _ = create_server(cls.service, host="127.0.0.1", port=0)
        cls.port = cls.httpd.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls._thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls._thread.start()
        # Brief settle
        time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        _close_stack(cls.service.stack)
        cls._tmpdir.cleanup()
        print(f"\n  [TestSidecarHTTP] class total: {time.time() - cls._t0:.2f}s")

    def test_health(self):
        code, body = _http_json(f"{self.base}/health")
        self.assertEqual(code, 200)
        assert isinstance(body, dict)
        self.assertEqual(body.get("status"), "ok")
        self.assertEqual(body.get("version"), __version__)

    def test_ready(self):
        code, body = _http_json(f"{self.base}/ready")
        self.assertEqual(code, 200)
        assert isinstance(body, dict)
        self.assertEqual(body.get("status"), "ready")

    def test_metrics(self):
        code, body = _http_json(f"{self.base}/metrics")
        self.assertEqual(code, 200)
        text = body if isinstance(body, str) else str(body)
        self.assertIn("governed_stack_decisions", text)

    def test_check_allow_mail(self):
        token = self.service.issue_token("tester", "operator")
        code, body = _http_json(
            f"{self.base}/v1/check",
            method="POST",
            body={
                "channel": "mail",
                "token": token,
                "subject": "Lunch",
                "body": "Are you free tomorrow?",
            },
        )
        self.assertEqual(code, 200)
        assert isinstance(body, dict)
        self.assertEqual(body.get("decision"), "ALLOW")
        self.assertIn("latency_ms", body)
        self.assertIn("entry_id", body)

    def test_check_bad_intent_invalid(self):
        token = self.service.issue_token("tester", "operator")
        code, body = _http_json(
            f"{self.base}/v1/check",
            method="POST",
            body={
                "channel": "raw",
                "token": token,
                "intent": {"action": ""},  # empty action → GOV_INTENT_INVALID
            },
        )
        self.assertEqual(code, 200)
        assert isinstance(body, dict)
        self.assertEqual(body.get("decision"), "BLOCK")
        self.assertEqual(body.get("error_code"), "GOV_INTENT_INVALID")

    def test_check_allow_algorithm_with_quantum_spectrum(self):
        token = self.service.issue_token("tester", "operator")
        code, body = _http_json(
            f"{self.base}/v1/check",
            method="POST",
            body={
                "channel": "algorithm",
                "token": token,
                "purpose": "batch_dedupe",
                "summary": "Nightly anonymized id dedupe",
                "time_cost": "O(n log n)",
                "space_cost": "O(n)",
                "energy_cost": "low",
                "speedup": "~2x",
                "risk_notes": "read-only replica",
                "security_margin": "standard",
            },
        )
        self.assertEqual(code, 200)
        assert isinstance(body, dict)
        self.assertEqual(body.get("decision"), "ALLOW")
        self.assertTrue(body.get("ok"))
        self.assertIn("latency_ms", body)
        self.assertIn("entry_id", body)
        self.assertIn("hais", body)
        self.assertIn("haven2", body)
        haven2 = body.get("haven2") or {}
        self.assertIn("realm", haven2)
        self.assertIn("open", haven2)
        self.assertIn("p_hat", haven2)
        self.assertIn("quantum", body)
        self.assertIn("quantum_line", body)
        self.assertIsInstance(body.get("quantum_line"), str)
        self.assertGreater(len(body["quantum_line"]), 0)
        self.assertIn("spectrum", body)
        spec = body["spectrum"]
        self.assertIsInstance(spec, dict)
        self.assertIn("available", spec)

    def test_check_unknown_channel_blocks(self):
        token = self.service.issue_token("tester", "operator")
        code, body = _http_json(
            f"{self.base}/v1/check",
            method="POST",
            body={
                "channel": "fax",
                "token": token,
                "purpose": "nope",
            },
        )
        self.assertEqual(code, 200)
        assert isinstance(body, dict)
        self.assertEqual(body.get("decision"), "BLOCK")
        self.assertEqual(body.get("error_code"), "GOV_INTENT_INVALID")
        reasons = body.get("reasons") or []
        self.assertTrue(any("unknown channel" in str(r) for r in reasons))

    def test_check_algorithm_rejects_secret_keys(self):
        token = self.service.issue_token("tester", "operator")
        code, body = _http_json(
            f"{self.base}/v1/check",
            method="POST",
            body={
                "channel": "algorithm",
                "token": token,
                "purpose": "batch_dedupe",
                "password": "should-not-be-here",
            },
        )
        self.assertEqual(code, 200)
        assert isinstance(body, dict)
        self.assertEqual(body.get("decision"), "BLOCK")
        self.assertEqual(body.get("error_code"), "GOV_INTENT_INVALID")
        reasons = body.get("reasons") or []
        self.assertTrue(any("scan_intent_forbids:password" in str(r) for r in reasons))


class TestSidecarApiKey(unittest.TestCase):
    """Separate server with GOVERNANCE_API_KEY required on /v1/*."""

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.TemporaryDirectory()
        td = Path(cls._tmpdir.name)
        cls.db_path = str(td / "audit.db")
        cls.key_path = str(td / "signing_key.pem")
        crypto = CryptoEngine(private_key_path=cls.key_path)
        stack = GovernedStack(
            config={
                "db_path": cls.db_path,
                "signing_key_path": cls.key_path,
                "log_level": 50,
            },
            crypto=crypto,
        )
        cls.api_key = "test-customer-api-key-9f3a"
        cls.service = SidecarService(
            config={
                "db_path": cls.db_path,
                "signing_key_path": cls.key_path,
                "api_key": cls.api_key,
                "log_level": 50,
                "rate_limit_per_min": 0,
            },
            stack=stack,
        )
        cls.httpd, _ = create_server(cls.service, host="127.0.0.1", port=0)
        cls.port = cls.httpd.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls._thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls._thread.start()
        time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        _close_stack(cls.service.stack)
        cls._tmpdir.cleanup()

    def test_health_open_without_key(self):
        code, body = _http_json(f"{self.base}/health")
        self.assertEqual(code, 200)
        assert isinstance(body, dict)
        self.assertEqual(body["status"], "ok")

    def test_v1_rejects_missing_api_key(self):
        token = self.service.issue_token("tester", "operator")
        code, body = _http_json(
            f"{self.base}/v1/check",
            method="POST",
            body={
                "channel": "mail",
                "token": token,
                "subject": "x",
                "body": "y",
            },
        )
        self.assertEqual(code, 401)
        assert isinstance(body, dict)
        self.assertEqual(body.get("error"), "unauthorized")

    def test_v1_accepts_valid_api_key(self):
        token = self.service.issue_token("tester", "operator")
        code, body = _http_json(
            f"{self.base}/v1/check",
            method="POST",
            body={
                "channel": "mail",
                "token": token,
                "subject": "Lunch",
                "body": "Are you free tomorrow?",
            },
            headers={"X-API-Key": self.api_key},
        )
        self.assertEqual(code, 200)
        assert isinstance(body, dict)
        self.assertEqual(body.get("decision"), "ALLOW")


class TestSidecarReadyFail(unittest.TestCase):
    def test_ready_503_without_key_file(self):
        with tempfile.TemporaryDirectory() as td:
            parent = Path(td) / "sub"
            parent.mkdir(parents=True, exist_ok=True)
            db = str(parent / "audit.db")
            missing_key = str(Path(td) / "no_such_key.pem")
            # Build stack with ephemeral crypto but point config at missing path.
            crypto = CryptoEngine(private_key_path=None)
            stack = GovernedStack(
                config={
                    "db_path": db,
                    "signing_key_path": missing_key,
                    "log_level": 50,
                },
                crypto=crypto,
            )
            svc = SidecarService(
                config={
                    "db_path": db,
                    "signing_key_path": missing_key,
                    "api_key": None,
                    "rate_limit_per_min": 0,
                },
                stack=stack,
            )
            ok, reasons = svc.readiness()
            self.assertFalse(ok)
            self.assertTrue(any("signing" in r.lower() for r in reasons))
            httpd, _ = create_server(svc, host="127.0.0.1", port=0)
            port = httpd.server_address[1]
            t = threading.Thread(target=httpd.serve_forever, daemon=True)
            t.start()
            try:
                time.sleep(0.05)
                code, body = _http_json(f"http://127.0.0.1:{port}/ready")
                self.assertEqual(code, 503)
                assert isinstance(body, dict)
                self.assertEqual(body.get("status"), "not_ready")
            finally:
                httpd.shutdown()
                httpd.server_close()
                _close_stack(stack)


class TestLoadConfig(unittest.TestCase):
    def test_defaults(self):
        old = {k: os.environ.pop(k, None) for k in _MT_ENV}
        try:
            cfg = load_sidecar_config()
            self.assertEqual(cfg["host"], "127.0.0.1")
            self.assertEqual(cfg["port"], 8080)
            self.assertIn("audit.db", cfg["db_path"])
            self.assertEqual(cfg["rate_limit_per_min"], 60)
        finally:
            for k, v in old.items():
                if v is not None:
                    os.environ[k] = v


class TestRateLimiterUnit(unittest.TestCase):
    def test_sliding_window_trips(self):
        lim = RateLimiter(limit=3, window_s=60.0)
        self.assertTrue(lim.allow("t1"))
        self.assertTrue(lim.allow("t1"))
        self.assertTrue(lim.allow("t1"))
        self.assertFalse(lim.allow("t1"))
        self.assertTrue(lim.allow("t2"))  # other bucket

    def test_disabled_when_zero(self):
        lim = RateLimiter(limit=0)
        for _ in range(20):
            self.assertTrue(lim.allow("x"))


class TestSidecarMultiTenant(unittest.TestCase):
    """Two API keys → isolated decisions/dbs; shared signing key."""

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.TemporaryDirectory()
        td = Path(cls._tmpdir.name)
        cls.key_path = str(td / "signing_key.pem")
        crypto = CryptoEngine(private_key_path=cls.key_path)
        assert Path(cls.key_path).is_file()

        specs = {
            "alpha": TenantSpec(
                tenant_id="alpha",
                api_key="key-alpha-aaa",
                db_path=str(td / "tenants" / "alpha" / "audit.db"),
                signing_key_path=cls.key_path,
            ),
            "beta": TenantSpec(
                tenant_id="beta",
                api_key="key-beta-bbb",
                db_path=str(td / "tenants" / "beta" / "audit.db"),
                signing_key_path=cls.key_path,
            ),
        }

        def _factory(spec: TenantSpec) -> SidecarService:
            # Share crypto object so both tenants verify the same JWTs if needed;
            # each still gets its own GovernedStack / audit DB.
            stack = GovernedStack(
                config={
                    "db_path": spec.db_path,
                    "signing_key_path": spec.signing_key_path,
                    "log_level": 50,
                },
                crypto=crypto,
            )
            return SidecarService(
                config={
                    "db_path": spec.db_path,
                    "signing_key_path": spec.signing_key_path,
                    "api_key": spec.api_key,
                    "log_level": 50,
                    "rate_limit_per_min": 0,
                    "_skip_registry": True,
                },
                stack=stack,
            )

        registry = TenantRegistry(
            specs,
            rate_limit_per_min=0,
            base_config={"log_level": 50, "signing_key_path": cls.key_path},
            service_factory=_factory,
        )
        cls.registry = registry
        cls.service = SidecarService(
            config={
                "db_path": specs["alpha"].db_path,
                "signing_key_path": cls.key_path,
                "log_level": 50,
                "rate_limit_per_min": 0,
            },
            registry=registry,
        )
        cls.httpd, _ = create_server(cls.service, host="127.0.0.1", port=0)
        cls.port = cls.httpd.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls._thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls._thread.start()
        time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        for tid in cls.registry.tenant_ids():
            _close_stack(cls.registry.get_service(tid).stack)
        cls._tmpdir.cleanup()

    def test_ready_checks_tenants(self):
        code, body = _http_json(f"{self.base}/ready")
        self.assertEqual(code, 200)
        assert isinstance(body, dict)
        self.assertEqual(body.get("status"), "ready")

    def test_isolated_dbs_and_tenant_id(self):
        alpha = self.registry.get_service("alpha")
        beta = self.registry.get_service("beta")
        token_a = alpha.issue_token("alice", "operator")
        token_b = beta.issue_token("bob", "operator")

        code_a, body_a = _http_json(
            f"{self.base}/v1/check",
            method="POST",
            body={
                "channel": "mail",
                "token": token_a,
                "subject": "Alpha lunch",
                "body": "Alpha body unique",
            },
            headers={"X-API-Key": "key-alpha-aaa"},
        )
        code_b, body_b = _http_json(
            f"{self.base}/v1/check",
            method="POST",
            body={
                "channel": "mail",
                "token": token_b,
                "subject": "Beta lunch",
                "body": "Beta body unique",
            },
            headers={"X-API-Key": "key-beta-bbb"},
        )
        self.assertEqual(code_a, 200)
        self.assertEqual(code_b, 200)
        assert isinstance(body_a, dict) and isinstance(body_b, dict)
        self.assertEqual(body_a.get("tenant_id"), "alpha")
        self.assertEqual(body_b.get("tenant_id"), "beta")
        self.assertEqual(body_a.get("decision"), "ALLOW")
        self.assertEqual(body_b.get("decision"), "ALLOW")
        self.assertNotEqual(body_a.get("entry_id"), body_b.get("entry_id"))

        # Distinct SQLite files on disk.
        self.assertTrue(Path(self.registry.specs["alpha"].db_path).is_file())
        self.assertTrue(Path(self.registry.specs["beta"].db_path).is_file())
        self.assertNotEqual(
            self.registry.specs["alpha"].db_path,
            self.registry.specs["beta"].db_path,
        )

    def test_wrong_key_401(self):
        code, body = _http_json(
            f"{self.base}/v1/check",
            method="POST",
            body={"channel": "mail", "token": "x", "subject": "s", "body": "b"},
            headers={"X-API-Key": "nope"},
        )
        self.assertEqual(code, 401)
        assert isinstance(body, dict)
        self.assertEqual(body.get("error"), "unauthorized")


class TestSidecarRateLimitHTTP(unittest.TestCase):
    """Rate limit trips 429 on /v1/*."""

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.TemporaryDirectory()
        td = Path(cls._tmpdir.name)
        cls.db_path = str(td / "audit.db")
        cls.key_path = str(td / "signing_key.pem")
        crypto = CryptoEngine(private_key_path=cls.key_path)
        stack = GovernedStack(
            config={
                "db_path": cls.db_path,
                "signing_key_path": cls.key_path,
                "log_level": 50,
            },
            crypto=crypto,
        )
        cls.api_key = "rate-limit-key"
        cls.service = SidecarService(
            config={
                "db_path": cls.db_path,
                "signing_key_path": cls.key_path,
                "api_key": cls.api_key,
                "log_level": 50,
                "rate_limit_per_min": 3,
            },
            stack=stack,
        )
        cls.httpd, _ = create_server(cls.service, host="127.0.0.1", port=0)
        cls.port = cls.httpd.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls._thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls._thread.start()
        time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        _close_stack(cls.service.stack)
        cls._tmpdir.cleanup()

    def test_rate_limit_429(self):
        token = self.service.issue_token("tester", "operator")
        payload = {
            "channel": "mail",
            "token": token,
            "subject": "Lunch",
            "body": "Are you free tomorrow?",
        }
        hdrs = {"X-API-Key": self.api_key}
        codes = []
        for _ in range(4):
            code, body = _http_json(
                f"{self.base}/v1/check",
                method="POST",
                body=payload,
                headers=hdrs,
            )
            codes.append(code)
            if code == 429:
                assert isinstance(body, dict)
                self.assertEqual(body.get("error"), "rate_limited")
                self.assertEqual(body.get("error_code"), "GOV_RATE_LIMIT")
        self.assertIn(429, codes)
        self.assertEqual(codes.count(200), 3)
        # Health stays open under rate limit pressure.
        hcode, _ = _http_json(f"{self.base}/health")
        self.assertEqual(hcode, 200)


class TestTenantRegistryFromEnv(unittest.TestCase):
    def test_api_keys_csv(self):
        old = {k: os.environ.pop(k, None) for k in _MT_ENV}
        try:
            with tempfile.TemporaryDirectory() as td:
                key = str(Path(td) / "signing_key.pem")
                CryptoEngine(private_key_path=key)
                os.environ["GOVERNANCE_API_KEYS"] = "t1:secret1,t2:secret2"
                os.environ["GOVERNANCE_SIGNING_KEY_PATH"] = key
                os.environ["GOVERNANCE_RATE_LIMIT_PER_MIN"] = "0"
                # Point tenants root via JSON instead for predictable paths under td —
                # CSV uses repo artifacts/tenants; use TENANTS_JSON for isolation.
                os.environ.pop("GOVERNANCE_API_KEYS", None)
                os.environ["GOVERNANCE_TENANTS_JSON"] = json.dumps(
                    {
                        "t1": {
                            "api_key": "secret1",
                            "db_path": str(Path(td) / "t1" / "audit.db"),
                            "signing_key_path": key,
                        },
                        "t2": {
                            "api_key": "secret2",
                            "db_path": str(Path(td) / "t2" / "audit.db"),
                            "signing_key_path": key,
                        },
                    }
                )
                reg = TenantRegistry.from_env(
                    {"signing_key_path": key, "rate_limit_per_min": 0, "log_level": 50}
                )
                self.assertIsNotNone(reg)
                assert reg is not None
                tid, svc, err = reg.resolve("secret1")
                self.assertIsNone(err)
                self.assertEqual(tid, "t1")
                self.assertIsNotNone(svc)
                for t in reg.tenant_ids():
                    _close_stack(reg.get_service(t).stack)
        finally:
            for k, v in old.items():
                if v is not None:
                    os.environ[k] = v
                else:
                    os.environ.pop(k, None)


if __name__ == "__main__":
    unittest.main()
