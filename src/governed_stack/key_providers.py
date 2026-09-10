"""Signing key providers for the governed stack (P1).

LocalPEMKeyProvider — file-backed RSA-3072 (demo/default).
EnvKMSKeyProvider — local KMS-shaped stand-in: key from env, private_pem is None.
RotatingKeyProvider — active PEM + JSON sidecar of historical public keys.

Honest framing: EnvKMS is a *local* KMS-shaped API surface for tests and
air-gapped deploys. It is not a cloud HSM / AWS KMS / GCP KMS SDK.
"""

from __future__ import annotations

import json
import os
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey

# ---------------------------------------------------------------------------
# ABC
# ---------------------------------------------------------------------------


class SigningKeyProvider(ABC):
    """Minimal interface the governance engine needs for audit + JWT signing."""

    @abstractmethod
    def sign(self, data: bytes) -> bytes:
        """RSA-PSS (SHA-256, MAX_LENGTH salt) signature over data (audit chain)."""

    @abstractmethod
    def verify(self, data: bytes, signature: bytes) -> bool:
        """Return True iff signature is valid for data under this key (PSS)."""

    @property
    @abstractmethod
    def public_pem(self) -> bytes:
        """SubjectPublicKeyInfo PEM of the active verification key."""

    @property
    def private_pem(self) -> Optional[bytes]:
        """
        Private key PEM if the provider keeps one in-process (local only).
        KMS-shaped providers must return None — consumers should use
        sign() / sign_pkcs1() / CryptoEngine.encode_jwt() instead.
        """
        return None

    def sign_pkcs1(self, data: bytes) -> bytes:
        """
        RSA PKCS#1 v1.5 SHA-256 signature (JWT RS256).

        Default raises; providers that hold a private key should override.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement sign_pkcs1(); "
            "needed for JWT RS256 when private_pem is unavailable"
        )

    @property
    def key_id(self) -> Optional[str]:
        """Optional stable key identifier (rotation)."""
        return None


def _pss() -> padding.PSS:
    return padding.PSS(
        mgf=padding.MGF1(hashes.SHA256()),
        salt_length=padding.PSS.MAX_LENGTH,
    )


def _sign_pss(key: RSAPrivateKey, data: bytes) -> bytes:
    return key.sign(data, _pss(), hashes.SHA256())


def _verify_pss(key: RSAPublicKey, data: bytes, signature: bytes) -> bool:
    try:
        key.verify(signature, data, _pss(), hashes.SHA256())
        return True
    except Exception:
        return False


def _sign_pkcs1(key: RSAPrivateKey, data: bytes) -> bytes:
    return key.sign(data, padding.PKCS1v15(), hashes.SHA256())


def _public_pem_bytes(key: Union[RSAPrivateKey, RSAPublicKey]) -> bytes:
    pub = key.public_key() if isinstance(key, RSAPrivateKey) else key
    return pub.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _private_pem_bytes(key: RSAPrivateKey) -> bytes:
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _require_persisted_flag(
    require_persisted_key: Optional[bool],
) -> bool:
    if require_persisted_key is not None:
        return bool(require_persisted_key)
    return os.environ.get("GOVERNANCE_REQUIRE_PERSISTED_KEY", "").strip() == "1"


def _persist_pem(path: str, pem: bytes) -> None:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    tmp_path = f"{path}.tmp-{uuid.uuid4().hex}"
    fd = None
    try:
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            fd = None
            f.write(pem)
        os.replace(tmp_path, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    finally:
        if fd is not None:
            os.close(fd)
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _load_private(pem: bytes) -> RSAPrivateKey:
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, RSAPrivateKey):
        raise TypeError("expected RSA private key")
    return key


def _load_public(pem: bytes) -> RSAPublicKey:
    key = serialization.load_pem_public_key(pem)
    if not isinstance(key, RSAPublicKey):
        raise TypeError("expected RSA public key")
    return key


def _new_rsa() -> RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=3072)


def _fingerprint(public_pem: bytes) -> str:
    digest = hashes.Hash(hashes.SHA256())
    digest.update(public_pem)
    return digest.finalize()[:8].hex()


# ---------------------------------------------------------------------------
# LocalPEMKeyProvider
# ---------------------------------------------------------------------------


class LocalPEMKeyProvider(SigningKeyProvider):
    """
    File-backed RSA-3072 key.

    Load from path if present; otherwise generate and persist with 0600 perms
    when a path is given and require_persisted_key is off. Ephemeral
    (path=None) and missing-file auto-generate are refused when
    require_persisted_key=True or GOVERNANCE_REQUIRE_PERSISTED_KEY=1.
    """

    def __init__(
        self,
        private_key_path: Optional[str] = None,
        *,
        require_persisted_key: Optional[bool] = None,
    ):
        self._path = private_key_path
        require = _require_persisted_flag(require_persisted_key)
        if require and not private_key_path:
            raise ValueError(
                "require_persisted_key is set (config or "
                "GOVERNANCE_REQUIRE_PERSISTED_KEY=1) but private_key_path is "
                "None — refusing to create an ephemeral unpersisted signing key. "
                "Pass a PEM path, EnvKMSKeyProvider, or unset the requirement."
            )
        if require and private_key_path and not os.path.exists(private_key_path):
            raise FileNotFoundError(
                "require_persisted_key is set (config or "
                "GOVERNANCE_REQUIRE_PERSISTED_KEY=1) but private_key_path "
                f"{private_key_path!r} does not exist — refusing to auto-generate "
                "a new signing key (would invalidate prior audit signatures). "
                "Place the PEM at that path, unset the requirement for demos, "
                "or use an intentional rotate()."
            )

        if private_key_path and os.path.exists(private_key_path):
            with open(private_key_path, "rb") as f:
                self._private_key = _load_private(f.read())
        else:
            self._private_key = _new_rsa()
            if private_key_path:
                self._persist(private_key_path)

        self._public_key = self._private_key.public_key()
        self._key_id = _fingerprint(_public_pem_bytes(self._private_key))

    def _persist(self, path: str) -> None:
        _persist_pem(path, _private_pem_bytes(self._private_key))

    def sign(self, data: bytes) -> bytes:
        return _sign_pss(self._private_key, data)

    def verify(self, data: bytes, signature: bytes) -> bool:
        return _verify_pss(self._public_key, data, signature)

    def sign_pkcs1(self, data: bytes) -> bytes:
        return _sign_pkcs1(self._private_key, data)

    @property
    def public_pem(self) -> bytes:
        return _public_pem_bytes(self._private_key)

    @property
    def private_pem(self) -> bytes:
        return _private_pem_bytes(self._private_key)

    @property
    def key_id(self) -> str:
        return self._key_id


# ---------------------------------------------------------------------------
# EnvKMSKeyProvider — local KMS-shaped stand-in
# ---------------------------------------------------------------------------


class EnvKMSKeyProvider(SigningKeyProvider):
    """
    Local KMS-shaped provider.

    Loads a private key from GOVERNANCE_SIGNING_KEY_PEM (PEM text) or
    GOVERNANCE_SIGNING_KEY_PATH (file). Exposes sign()/verify()/public_pem and
    sign_pkcs1() for JWT, but private_pem always returns None so callers cannot
    exfiltrate key bytes through the KMS-shaped API surface.

    This is NOT a cloud HSM SDK — it simulates the non-exportable-key posture
    for local/dev and offline tests.
    """

    def __init__(
        self,
        *,
        pem: Optional[str] = None,
        path: Optional[str] = None,
        env_pem: str = "GOVERNANCE_SIGNING_KEY_PEM",
        env_path: str = "GOVERNANCE_SIGNING_KEY_PATH",
    ):
        raw: Optional[bytes] = None
        if pem is not None:
            raw = pem.encode() if isinstance(pem, str) else pem
        elif path is not None:
            with open(path, "rb") as f:
                raw = f.read()
        else:
            env_val = os.environ.get(env_pem)
            if env_val:
                raw = env_val.encode() if not env_val.lstrip().startswith(
                    "-----"
                ) and "\\n" in env_val else env_val.replace("\\n", "\n").encode()
                # Prefer real newlines; also accept literal \n from env files.
                if b"-----BEGIN" not in raw and env_val:
                    raw = env_val.replace("\\n", "\n").encode()
            else:
                env_p = os.environ.get(env_path)
                if env_p:
                    with open(env_p, "rb") as f:
                        raw = f.read()

        if not raw:
            raise ValueError(
                "EnvKMSKeyProvider requires GOVERNANCE_SIGNING_KEY_PEM, "
                "GOVERNANCE_SIGNING_KEY_PATH, or explicit pem=/path="
            )

        self._private_key = _load_private(raw)
        self._public_key = self._private_key.public_key()
        self._key_id = _fingerprint(_public_pem_bytes(self._private_key))

    def sign(self, data: bytes) -> bytes:
        return _sign_pss(self._private_key, data)

    def verify(self, data: bytes, signature: bytes) -> bool:
        return _verify_pss(self._public_key, data, signature)

    def sign_pkcs1(self, data: bytes) -> bytes:
        return _sign_pkcs1(self._private_key, data)

    @property
    def public_pem(self) -> bytes:
        return _public_pem_bytes(self._private_key)

    @property
    def private_pem(self) -> None:
        return None

    @property
    def key_id(self) -> str:
        return self._key_id


# ---------------------------------------------------------------------------
# RotatingKeyProvider
# ---------------------------------------------------------------------------


class RotatingKeyProvider(SigningKeyProvider):
    """
    Wraps a primary LocalPEM (or EnvKMS) plus optional previous public keys.

    Sidecar ``signing_keys.json`` next to the active PEM lists
    ``{key_id, public_pem, created_at, active}`` for verify_chain of
    historical signatures after rotate().
    """

    SIDECAR_NAME = "signing_keys.json"

    def __init__(
        self,
        primary: Optional[SigningKeyProvider] = None,
        *,
        private_key_path: Optional[str] = None,
        sidecar_path: Optional[str] = None,
        require_persisted_key: Optional[bool] = None,
    ):
        if primary is None:
            if not private_key_path:
                raise ValueError(
                    "RotatingKeyProvider needs primary= or private_key_path="
                )
            primary = LocalPEMKeyProvider(
                private_key_path,
                require_persisted_key=require_persisted_key,
            )
        self._primary = primary
        self._path = private_key_path or getattr(primary, "_path", None)
        self._sidecar: Optional[Path]
        if sidecar_path:
            self._sidecar = Path(sidecar_path)
        elif self._path:
            self._sidecar = Path(self._path).with_name(self.SIDECAR_NAME)
        else:
            self._sidecar = None

        self._previous: List[Dict[str, Any]] = []
        self._load_sidecar()
        self._ensure_active_in_sidecar()

    def _load_sidecar(self) -> None:
        if not self._sidecar or not self._sidecar.is_file():
            return
        try:
            data = json.loads(self._sidecar.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        keys = data.get("keys") if isinstance(data, dict) else data
        if not isinstance(keys, list):
            return
        for entry in keys:
            if not entry.get("active"):
                self._previous.append(entry)

    def _ensure_active_in_sidecar(self) -> None:
        if not self._sidecar:
            return
        entries = list(self._previous)
        active = {
            "key_id": self.key_id,
            "public_pem": self.public_pem.decode(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "active": True,
        }
        # Drop any stale active=True from previous loads; keep inactive only.
        entries = [e for e in entries if not e.get("active")]
        self._write_sidecar(entries + [active])

    def _write_sidecar(self, entries: List[Dict[str, Any]]) -> None:
        if not self._sidecar:
            return
        self._sidecar.parent.mkdir(parents=True, exist_ok=True)
        payload = {"keys": entries}
        tmp = self._sidecar.with_suffix(self._sidecar.suffix + f".tmp-{uuid.uuid4().hex}")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, self._sidecar)

    def sign(self, data: bytes) -> bytes:
        return self._primary.sign(data)

    def sign_pkcs1(self, data: bytes) -> bytes:
        return self._primary.sign_pkcs1(data)

    def verify(self, data: bytes, signature: bytes) -> bool:
        if self._primary.verify(data, signature):
            return True
        for entry in self._previous:
            try:
                pub = _load_public(entry["public_pem"].encode())
            except Exception:
                continue
            if _verify_pss(pub, data, signature):
                return True
        return False

    @property
    def public_pem(self) -> bytes:
        return self._primary.public_pem

    @property
    def private_pem(self) -> Optional[bytes]:
        return self._primary.private_pem

    @property
    def key_id(self) -> Optional[str]:
        kid = self._primary.key_id
        return kid

    def rotate(self, new_path: Optional[str] = None) -> str:
        """
        Generate a new active key, demote current public key to previous,
        persist sidecar. Returns new key_id.
        """
        old_public = self.public_pem.decode()
        old_id = self.key_id or _fingerprint(self.public_pem)
        old_entry = {
            "key_id": old_id,
            "public_pem": old_public,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "active": False,
        }
        self._previous.append(old_entry)

        target = new_path or self._path
        if not target:
            raise ValueError("rotate() needs new_path when no active PEM path is set")

        # Remove existing file so LocalPEM generates fresh key at path.
        if os.path.exists(target):
            # Keep a backup of the old private key next to path.
            bak = f"{target}.bak-{old_id}"
            try:
                os.replace(target, bak)
            except OSError:
                os.remove(target)

        # Intentional create after backup — do not use require's "file must exist".
        self._primary = LocalPEMKeyProvider(target, require_persisted_key=False)
        self._path = target
        if self._sidecar is None:
            self._sidecar = Path(target).with_name(self.SIDECAR_NAME)

        new_id = self.key_id or _fingerprint(self.public_pem)
        active = {
            "key_id": new_id,
            "public_pem": self.public_pem.decode(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "active": True,
        }
        inactive = [e for e in self._previous if not e.get("active")]
        # Ensure old_entry is present
        if not any(e.get("key_id") == old_id for e in inactive):
            inactive.append(old_entry)
        self._previous = inactive
        self._write_sidecar(inactive + [active])
        return new_id


# ---------------------------------------------------------------------------
# Resolver used by CryptoEngine / CGE
# ---------------------------------------------------------------------------


def resolve_signing_key_provider(
    *,
    provider: Optional[SigningKeyProvider] = None,
    private_key_path: Optional[str] = None,
    require_persisted_key: Optional[bool] = None,
    prefer_env_kms: bool = True,
) -> SigningKeyProvider:
    """
    Build a provider from an explicit instance, path, or env.

    Order: explicit provider → LocalPEM(path) when path given →
    EnvKMS (if env set and prefer_env_kms) → LocalPEM(ephemeral/path).
    """
    if provider is not None:
        return provider
    if private_key_path:
        return LocalPEMKeyProvider(
            private_key_path,
            require_persisted_key=require_persisted_key,
        )
    if prefer_env_kms and (
        os.environ.get("GOVERNANCE_SIGNING_KEY_PEM")
        or os.environ.get("GOVERNANCE_SIGNING_KEY_PATH")
    ):
        return EnvKMSKeyProvider()
    return LocalPEMKeyProvider(
        private_key_path,
        require_persisted_key=require_persisted_key,
    )


__all__ = [
    "SigningKeyProvider",
    "LocalPEMKeyProvider",
    "EnvKMSKeyProvider",
    "RotatingKeyProvider",
    "resolve_signing_key_provider",
]
