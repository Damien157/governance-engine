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
    SidecarService,
    create_server,
    load_sidecar_config,
)
from governed_stack.stack import GovernedStack  # noqa: E402


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
        eng = getattr(cls.service.stack, "engine", None)
        storage = getattr(eng, "storage", None) if eng else None
        if storage is not None and hasattr(storage, "close"):
            try:
                storage.close()
            except Exception:
                pass
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
        eng = getattr(cls.service.stack, "engine", None)
        storage = getattr(eng, "storage", None) if eng else None
        if storage is not None and hasattr(storage, "close"):
            try:
                storage.close()
            except Exception:
                pass
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
                storage = getattr(stack.engine, "storage", None)
                if storage is not None and hasattr(storage, "close"):
                    try:
                        storage.close()
                    except Exception:
                        pass


class TestLoadConfig(unittest.TestCase):
    def test_defaults(self):
        # Clear customer-ish env for isolation.
        old = {k: os.environ.pop(k, None) for k in (
            "GOVERNANCE_DB_PATH",
            "GOVERNANCE_SIGNING_KEY_PATH",
            "GOVERNANCE_HOST",
            "GOVERNANCE_PORT",
            "GOVERNANCE_API_KEY",
        )}
        try:
            cfg = load_sidecar_config()
            self.assertEqual(cfg["host"], "127.0.0.1")
            self.assertEqual(cfg["port"], 8080)
            self.assertIn("audit.db", cfg["db_path"])
        finally:
            for k, v in old.items():
                if v is not None:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
