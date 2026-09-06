"""P1 key provider tests: LocalPEM, require_persisted, EnvKMS, rotate, CryptoEngine."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import jwt
from certified_governance_unified import CryptoEngine

from governed_stack.key_providers import (
    EnvKMSKeyProvider,
    LocalPEMKeyProvider,
    RotatingKeyProvider,
)


class TestLocalPEM(unittest.TestCase):
    def test_persist_and_reload(self):
        with tempfile.TemporaryDirectory() as td:
            path = str(Path(td) / "signing.pem")
            a = LocalPEMKeyProvider(path)
            self.assertTrue(Path(path).is_file())
            mode = Path(path).stat().st_mode & 0o777
            self.assertEqual(mode, 0o600)
            sig = a.sign(b"hello")
            b = LocalPEMKeyProvider(path)
            self.assertTrue(b.verify(b"hello", sig))
            self.assertEqual(a.public_pem, b.public_pem)

    def test_require_persisted_key_raises(self):
        with self.assertRaises(ValueError) as ctx:
            LocalPEMKeyProvider(None, require_persisted_key=True)
        self.assertIn("require_persisted_key", str(ctx.exception))

    def test_require_persisted_via_env(self):
        old = os.environ.get("GOVERNANCE_REQUIRE_PERSISTED_KEY")
        os.environ["GOVERNANCE_REQUIRE_PERSISTED_KEY"] = "1"
        try:
            with self.assertRaises(ValueError):
                LocalPEMKeyProvider(None)
        finally:
            if old is None:
                os.environ.pop("GOVERNANCE_REQUIRE_PERSISTED_KEY", None)
            else:
                os.environ["GOVERNANCE_REQUIRE_PERSISTED_KEY"] = old

    def test_ephemeral_still_allowed_by_default(self):
        p = LocalPEMKeyProvider(None)
        self.assertTrue(p.verify(b"x", p.sign(b"x")))


class TestEnvKMS(unittest.TestCase):
    def test_sign_verify_private_pem_none_and_jwt(self):
        with tempfile.TemporaryDirectory() as td:
            path = str(Path(td) / "k.pem")
            LocalPEMKeyProvider(path)
            pem_text = Path(path).read_text()
            prov = EnvKMSKeyProvider(pem=pem_text)
            self.assertIsNone(prov.private_pem)
            sig = prov.sign(b"audit")
            self.assertTrue(prov.verify(b"audit", sig))
            crypto = CryptoEngine(provider=prov)
            token = crypto.encode_jwt({"user": "damien", "exp": 9999999999})
            decoded = jwt.decode(token, crypto.public_pem, algorithms=["RS256"])
            self.assertEqual(decoded["user"], "damien")
            # private_pem on CryptoEngine should raise
            with self.assertRaises(RuntimeError):
                _ = crypto.private_pem

    def test_from_env_path(self):
        with tempfile.TemporaryDirectory() as td:
            path = str(Path(td) / "k.pem")
            LocalPEMKeyProvider(path)
            old = os.environ.get("GOVERNANCE_SIGNING_KEY_PATH")
            os.environ["GOVERNANCE_SIGNING_KEY_PATH"] = path
            try:
                prov = EnvKMSKeyProvider()
                self.assertIsNone(prov.private_pem)
                self.assertTrue(prov.verify(b"z", prov.sign(b"z")))
            finally:
                if old is None:
                    os.environ.pop("GOVERNANCE_SIGNING_KEY_PATH", None)
                else:
                    os.environ["GOVERNANCE_SIGNING_KEY_PATH"] = old


class TestRotating(unittest.TestCase):
    def test_rotate_verify_old_sign_new(self):
        with tempfile.TemporaryDirectory() as td:
            path = str(Path(td) / "active.pem")
            LocalPEMKeyProvider(path)
            rot = RotatingKeyProvider(private_key_path=path)
            old_sig = rot.sign(b"historical")
            old_pub = rot.public_pem
            old_id = rot.key_id
            new_id = rot.rotate(path)
            self.assertNotEqual(old_id, new_id)
            # Old signature still verifies via previous public keys
            self.assertTrue(rot.verify(b"historical", old_sig))
            # New key signs and verifies
            new_sig = rot.sign(b"fresh")
            self.assertTrue(rot.verify(b"fresh", new_sig))
            self.assertNotEqual(old_pub, rot.public_pem)
            sidecar = Path(td) / "signing_keys.json"
            self.assertTrue(sidecar.is_file())
            text = sidecar.read_text()
            self.assertIn(old_id, text)
            self.assertIn(new_id, text)


class TestCryptoEngineProvider(unittest.TestCase):
    def test_crypto_with_provider(self):
        with tempfile.TemporaryDirectory() as td:
            path = str(Path(td) / "c.pem")
            prov = LocalPEMKeyProvider(path)
            crypto = CryptoEngine(provider=prov)
            self.assertEqual(crypto.public_pem, prov.public_pem)
            self.assertTrue(crypto.verify(b"m", crypto.sign(b"m")))
            tok = crypto.encode_jwt({"role": "user", "exp": 9999999999})
            self.assertTrue(jwt.decode(tok, crypto.public_pem, algorithms=["RS256"]))


if __name__ == "__main__":
    unittest.main()
