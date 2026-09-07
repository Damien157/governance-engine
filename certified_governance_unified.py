"""
Certified Governance Engine — unified reference implementation (v1.1.0)

Single module containing the full system built across this work:

  - Auth (JWT RS256 + revocation)
  - Flow control (token bucket) + per-user sliding-window rate limits
  - Declarative policy (PII, banned terms, action rules) + hot reload
  - Heuristic signals (risk, anomaly, integrity, throughput, trend)
  - Explicit ALLOW / REVIEW / BLOCK decision logic
  - Hash-chained, RSA-PSS signed audit log with PII redaction
  - Human review queue + single-use approval vouchers (F-04)
  - SigningKeyProvider interface for KMS-backed keys + LocalPEMKeyProvider
  - ZK extension: Pedersen + CDS94 range proofs (RISK_THRESHOLD)
  - Signed attestations for POLICY_REDACTION and REVIEW_APPROVAL (not ZK)

Mathematical models are heuristics / signals only. Decision logic is
rule-based and auditable. ZK is real only for RISK_THRESHOLD.

Notes (v1.1.0 reference patches):
  - Default pii_phone regex tightened (requires separators / parens / +)
  - REVIEW policy hints honored in PolicyEngine.evaluate and _decide
"""

from __future__ import annotations

# === KEY PROVIDERS (KMS interface) ===
# Source of truth: src/governed_stack/key_providers.py
# Loaded via importlib by file path to avoid circular import with governed_stack.stack.
import importlib.util
from pathlib import Path as _Path

def _load_key_providers_module():
    # File-load only during CGE import to avoid circular import with
    # governed_stack.stack (which imports this module). Register under the
    # canonical submodule name so later imports reuse the same object.
    # Do NOT stub the parent package — that would shadow src/governed_stack.
    import sys
    name = "governed_stack.key_providers"
    if name in sys.modules:
        return sys.modules[name]
    kp_path = _Path(__file__).resolve().parent / "src" / "governed_stack" / "key_providers.py"
    spec = importlib.util.spec_from_file_location(name, kp_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load key_providers from {kp_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

_kp = _load_key_providers_module()
SigningKeyProvider = _kp.SigningKeyProvider
LocalPEMKeyProvider = _kp.LocalPEMKeyProvider
EnvKMSKeyProvider = _kp.EnvKMSKeyProvider
RotatingKeyProvider = _kp.RotatingKeyProvider
resolve_signing_key_provider = _kp.resolve_signing_key_provider

# Re-export for backward compatibility: `from certified_governance_unified import LocalPEMKeyProvider`

# === CORE ENGINE ===
import asyncio
import base64
import hashlib
import json
import logging
import math
import os
import re
import sqlite3
import statistics
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

import jwt
import psutil
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

logger = logging.getLogger("certified_governance")

GENESIS_HASH = "0" * 64

# ============================================================================
# MATHEMATICAL SIGNALS (HEURISTIC, DECISION-SUPPORT ONLY)
# ============================================================================

class MathematicalSignals:
    """
    Heuristic, tunable signals used to inform governance decisions.
    These are NOT validated statistical models. They must be treated
    as decision-support inputs, never as sole determinants.
    """

    @staticmethod
    def risk_signal(
        verification_score: float,
        anomaly_score: float,
        flow_utilization: float,
        weights: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        """
        Heuristic composite risk signal.

        R = a*(1-V) + b*A + c*F + d*V*A*F

        Inputs:
            verification_score: 0-1, higher = more trusted
            anomaly_score: 0-1, higher = more anomalous
            flow_utilization: 0-1, higher = more saturated
            weights: optional dict of a,b,c,d (sum ~ 1 by convention)

        Outputs:
            risk_signal: float in [0,1]
            components: contributions
            qualitative_level: LOW/MEDIUM/HIGH
        """
        w = weights or {"a": 0.4, "b": 0.3, "c": 0.2, "d": 0.1}

        V = max(0.0, min(1.0, verification_score))
        A = max(0.0, min(1.0, anomaly_score))
        F = max(0.0, min(1.0, flow_utilization))

        component_verification = w["a"] * (1 - V)
        component_anomaly = w["b"] * A
        component_flow = w["c"] * F
        interaction_term = w["d"] * V * A * F

        raw_risk = component_verification + component_anomaly + component_flow + interaction_term
        normalized = min(1.0, max(0.0, raw_risk))

        if normalized > 0.7:
            level = "HIGH"
        elif normalized > 0.4:
            level = "MEDIUM"
        else:
            level = "LOW"

        return {
            "risk_signal": normalized,
            "components": {
                "verification_contribution": component_verification,
                "anomaly_contribution": component_anomaly,
                "flow_contribution": component_flow,
                "interaction_term": interaction_term,
            },
            "qualitative_level": level,
        }

    @staticmethod
    def integrity_pressure_signal(
        chain_breaks: int,
        total_entries: int,
        time_window_hours: float,
        lam: float = 0.1,
    ) -> Dict[str, Any]:
        """
        Time-discounted integrity pressure signal.

        P = (breaks/total) * e^(-lambda * t_days)

        Inputs:
            chain_breaks: number of broken entries in window
            total_entries: total entries in window
            time_window_hours: window size
            lam: decay constant

        Outputs:
            integrity_pressure: float in [0,1]
        """
        if total_entries == 0:
            return {"integrity_pressure": 0.0}

        t_days = time_window_hours / 24.0
        raw = chain_breaks / total_entries
        decay = math.exp(-lam * t_days)
        pressure = min(1.0, raw * decay)
        return {"integrity_pressure": pressure}

    @staticmethod
    def throughput_signal(
        current_load: float,
        max_capacity: float,
        cpu_usage: float,
        memory_usage: float,
    ) -> Dict[str, Any]:
        """
        Resource-based throughput signal.

        Uses average CPU/memory utilization and load ratio to produce
        an efficiency score and qualitative status.
        """
        R_cpu = min(1.0, max(0.0, cpu_usage))
        R_mem = min(1.0, max(0.0, memory_usage))
        R_avg = (R_cpu + R_mem) / 2.0

        current_ratio = current_load / max_capacity if max_capacity > 0 else 0.0
        S = current_ratio + (1.0 - R_avg)
        sigma = 0.5
        available_capacity = max_capacity * (1.0 - R_avg) * math.exp(-sigma * S)

        efficiency = (1.0 - R_avg) * (1.0 - abs(current_ratio - 0.5) * 2.0)
        efficiency = max(0.0, min(1.0, efficiency))

        if efficiency > 0.7:
            status = "OPTIMAL"
        elif efficiency > 0.4:
            status = "STRAINED"
        else:
            status = "CRITICAL"

        return {
            "available_capacity": max(0.0, available_capacity),
            "utilization_percentage": R_avg * 100.0,
            "efficiency_score": efficiency,
            "status": status,
        }

    @staticmethod
    def anomaly_signal(
        metrics: List[float],
        window: int = 10,
        std_threshold: float = 3.0,
    ) -> Dict[str, Any]:
        """
        Rolling z-score anomaly signal over last `window` samples.

        Outputs:
            anomaly_signal: float in [0,1] (confidence-like)
            severity: LOW/MEDIUM/HIGH/CRITICAL
        """
        if len(metrics) < window:
            return {
                "anomaly_signal": 0.0,
                "z_score": 0.0,
                "severity": "LOW",
                "reason": "insufficient_data",
            }

        recent = metrics[-window:]
        current = recent[-1]
        baseline = recent[:-1]
        mean = statistics.mean(baseline)
        std = statistics.stdev(baseline) if len(baseline) > 1 else 0.0

        if std == 0.0:
            # A perfectly flat baseline that then moves at all is a real
            # anomaly, not a non-event — treat any deviation as maximal
            # rather than silently reporting z=0.
            z = 0.0 if current == mean else 10.0
        else:
            z = abs(current - mean) / std
        if z > 5.0:
            severity = "CRITICAL"
        elif z > 3.0:
            severity = "HIGH"
        elif z > 2.0:
            severity = "MEDIUM"
        else:
            severity = "LOW"

        anomaly_signal = min(1.0, z / 6.0)

        return {
            "anomaly_signal": anomaly_signal,
            "z_score": z,
            "mean": mean,
            "std": std,
            "current": current,
            "severity": severity,
        }

    @staticmethod
    def risk_trend_signal(
        historical_risks: List[float],
        horizon: int = 5,
    ) -> Dict[str, Any]:
        """
        Ordinary least-squares linear regression over risk history.

        Outputs:
            trend: RISING/FALLING/STABLE/UNKNOWN
            slope, intercept
            predictions
            r_squared
        """
        if len(historical_risks) < 3:
            return {
                "trend": "UNKNOWN",
                "slope": 0.0,
                "intercept": 0.0,
                "predictions": [],
                "r_squared": None,
            }

        x = list(range(len(historical_risks)))
        y = historical_risks
        n = len(x)
        x_mean = sum(x) / n
        y_mean = sum(y) / n

        num = sum((xi - x_mean) * (yi - y_mean) for xi, yi in zip(x, y))
        den = sum((xi - x_mean) ** 2 for xi in x)
        slope = num / den if den != 0 else 0.0
        intercept = y_mean - slope * x_mean

        ss_tot = sum((yi - y_mean) ** 2 for yi in y)
        ss_res = sum((yi - (slope * xi + intercept)) ** 2 for xi, yi in zip(x, y))
        r_squared = 1.0 - (ss_res / ss_tot) if ss_tot != 0 else 1.0

        future_x = list(range(len(historical_risks), len(historical_risks) + horizon))
        predictions = [slope * xi + intercept for xi in future_x]

        if slope > 0.01:
            trend = "RISING"
        elif slope < -0.01:
            trend = "FALLING"
        else:
            trend = "STABLE"

        return {
            "trend": trend,
            "slope": slope,
            "intercept": intercept,
            "predictions": predictions,
            "r_squared": r_squared,
        }

# ============================================================================
# CRYPTO ENGINE (RSA-PSS)
# ============================================================================

class CryptoEngine:
    """
    RSA-PSS signing/verification for the audit chain and JWT.

    Accepts either a local PEM path (legacy) or an explicit SigningKeyProvider.
    For KMS-shaped providers (EnvKMSKeyProvider) private_pem is None; use
    encode_jwt() which signs via provider.sign_pkcs1() without exposing PEM.
    EnvKMS is a local stand-in, not a cloud HSM SDK.
    """

    def __init__(
        self,
        private_key_path: Optional[str] = None,
        provider: Optional[Any] = None,
        *,
        require_persisted_key: Optional[bool] = None,
    ):
        if provider is not None:
            self._provider = provider
        else:
            require = require_persisted_key
            if require is None:
                require = os.environ.get("GOVERNANCE_REQUIRE_PERSISTED_KEY", "").strip() == "1"
            # Prefer env KMS when configured; else LocalPEM (ephemeral OK for demos/tests).
            self._provider = resolve_signing_key_provider(
                private_key_path=private_key_path,
                require_persisted_key=require,
                prefer_env_kms=True,
            )
            if isinstance(self._provider, LocalPEMKeyProvider):
                if private_key_path and os.path.exists(private_key_path):
                    pass
                elif private_key_path:
                    logger.warning(
                        f"Generated new signing key and saved to {private_key_path}. "
                        "Every prior audit-log signature was produced under a different "
                        "key and will now fail verify_chain()."
                    )
                elif not private_key_path and not require:
                    logger.warning(
                        "CryptoEngine started with no private_key_path — a fresh, "
                        "unpersisted key was generated. This key (and every signature "
                        "made with it, and every JWT issued with it) will be lost and "
                        "unverifiable on the next restart. Pass private_key_path or a "
                        "SigningKeyProvider in production."
                    )

    @property
    def provider(self) -> Any:
        return self._provider

    @property
    def private_pem(self) -> bytes:
        pem = self._provider.private_pem
        if pem is None:
            raise RuntimeError(
                "this SigningKeyProvider does not expose a private key "
                "(expected for KMS-shaped providers). Use CryptoEngine.encode_jwt() "
                "or provider.sign_pkcs1() instead."
            )
        return pem

    @property
    def public_pem(self) -> bytes:
        return self._provider.public_pem

    def sign(self, data: bytes) -> bytes:
        return self._provider.sign(data)

    def verify(self, data: bytes, signature: bytes) -> bool:
        return self._provider.verify(data, signature)

    def encode_jwt(self, payload: Dict[str, Any], algorithm: str = "RS256") -> str:
        """Issue a JWT using private_pem when available, else provider.sign_pkcs1()."""
        if algorithm != "RS256":
            raise ValueError(f"unsupported JWT algorithm: {algorithm}")
        pem = self._provider.private_pem
        if pem is not None:
            return jwt.encode(payload, pem, algorithm="RS256")
        # Pure KMS-shaped path: build RS256 JWT without exposing private PEM.
        header = {"alg": "RS256", "typ": "JWT"}

        def _b64url(data: bytes) -> bytes:
            return base64.urlsafe_b64encode(data).rstrip(b"=")

        h = _b64url(json.dumps(header, separators=(",", ":"), sort_keys=True).encode())
        # PyJWT keeps claim order as given; do not sort payload keys.
        p = _b64url(json.dumps(payload, separators=(",", ":")).encode())
        signing_input = h + b"." + p
        sig = self._provider.sign_pkcs1(signing_input)
        return (signing_input + b"." + _b64url(sig)).decode("ascii")

# ============================================================================
# STORAGE (HASH-CHAINED, SIGNED AUDIT LOG)
# ============================================================================

class AuditStorage:
    """
    Hash-chained, RSA-signed audit log with integrity verification
    and exportable reports.

    Concurrent appends: WAL + per-thread connections allow parallel DB I/O,
    but hash-chain linking requires a single logical writer — ``_chain_lock``
    serializes tip read → monotonic timestamp → hash/sign → INSERT → tip update.
    """

    def __init__(self, db_path: str, crypto: CryptoEngine):
        self.db_path = db_path
        self.crypto = crypto
        self._local = threading.local()
        # Single-writer lock for hash-chain integrity: SQLite WAL allows
        # concurrent readers, but prev_hash → entry_hash linking must be
        # serialized across threads (one logical writer for the chain).
        self._chain_lock = threading.Lock()
        self._last_hash = GENESIS_HASH
        self._last_ts = 0.0
        self._init_schema()

    @property
    def conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn"):
            c = sqlite3.connect(self.db_path, check_same_thread=False, timeout=10)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA busy_timeout=10000")
            self._local.conn = c
        return self._local.conn

    def _init_schema(self):
        cur = self.conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                id TEXT PRIMARY KEY,
                timestamp REAL,
                decision TEXT,
                result TEXT,
                verification_score REAL,
                risk_signal REAL,
                anomaly_signal REAL,
                policy_reasons TEXT,
                metadata TEXT,
                trace_id TEXT,
                intent_envelope TEXT,
                prev_hash TEXT,
                entry_hash TEXT,
                signature TEXT,
                engine_version TEXT,
                environment_id TEXT
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp DESC)"
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS review_queue (
                entry_id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'PENDING',
                created_at REAL,
                resolved_at REAL,
                resolved_by TEXT,
                resolution_entry_id TEXT,
                notes TEXT,
                FOREIGN KEY (entry_id) REFERENCES audit_log(id)
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_review_status ON review_queue(status, created_at)"
        )
        self.conn.commit()

        # Prefer insertion order (rowid) over timestamp when seeding tip —
        # timestamps alone can race under concurrent writers (fixed in 0.4.3).
        row = cur.execute(
            "SELECT entry_hash, timestamp FROM audit_log ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        if row:
            self._last_hash = row["entry_hash"]
            self._last_ts = float(row["timestamp"] or 0.0)

    def close(self) -> None:
        """Close the thread-local SQLite connection if present and clear it."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            finally:
                try:
                    del self._local.conn
                except AttributeError:
                    pass

    def __del__(self):  # best-effort
        try:
            self.close()
        except Exception:
            pass

    def log_decision(
        self,
        intent: Dict[str, Any],
        decision: str,
        result: str,
        verification_score: float,
        risk_signal: float,
        anomaly_signal: float,
        policy_reasons: List[str],
        metadata: Optional[Dict[str, Any]] = None,
        trace_id: Optional[str] = None,
        engine_version: str = "CertifiedGovernanceEngine v1.0",
        environment_id: str = "default",
    ) -> str:
        """
        Log a governed decision into the audit chain.
        """
        entry_id = str(uuid.uuid4())
        envelope = {"data": base64.b64encode(json.dumps(intent).encode()).decode()}

        # Hold _chain_lock across: assign ts → read tip → hash/sign → INSERT
        # → commit → update tip. Timestamp must be taken under the lock so
        # verify_chain (ORDER BY timestamp, rowid) matches link order. Taking
        # time.time() *before* the lock let thread A stamp earlier than B but
        # acquire the lock after B — prev_hash linked to B while timestamp
        # order put A first → broken chain under parallel writers.
        with self._chain_lock:
            ts = time.time()
            if ts <= self._last_ts:
                # Strictly monotonic under the writer lock (clock skew / ties).
                ts = self._last_ts + 1e-6
            prev_hash = self._last_hash
            body = json.dumps(
                {
                    "id": entry_id,
                    "ts": ts,
                    "decision": decision,
                    "result": result,
                    "envelope": envelope,
                    "prev_hash": prev_hash,
                    "engine_version": engine_version,
                    "environment_id": environment_id,
                },
                sort_keys=True,
            ).encode()
            entry_hash = hashlib.sha256(body).hexdigest()
            signature = base64.b64encode(self.crypto.sign(body)).decode()

            cur = self.conn.cursor()
            cur.execute(
                """
                INSERT INTO audit_log
                (id, timestamp, decision, result, verification_score, risk_signal,
                 anomaly_signal, policy_reasons, metadata, trace_id,
                 intent_envelope, prev_hash, entry_hash, signature,
                 engine_version, environment_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry_id,
                    ts,
                    decision,
                    result,
                    verification_score,
                    risk_signal,
                    anomaly_signal,
                    json.dumps(policy_reasons or []),
                    json.dumps(metadata or {}),
                    trace_id or "",
                    json.dumps(envelope),
                    prev_hash,
                    entry_hash,
                    signature,
                    engine_version,
                    environment_id,
                ),
            )
            if decision == "REVIEW":
                # Same connection, same transaction as the INSERT above --
                # committed together, so the audit log and the review
                # queue can never disagree about whether this entry is
                # pending human review.
                cur.execute(
                    "INSERT INTO review_queue (entry_id, status, created_at) VALUES (?, 'PENDING', ?)",
                    (entry_id, ts),
                )
            self.conn.commit()
            self._last_hash = entry_hash
            self._last_ts = ts
        return entry_id

    def list_pending_reviews(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        List REVIEW-decisions awaiting human resolution, oldest first.
        Includes the (already-redacted) intent so a reviewer can see what
        they're being asked to judge without exposing raw PII.
        """
        cur = self.conn.cursor()
        rows = cur.execute(
            """
            SELECT r.entry_id, r.created_at AS queued_at, a.timestamp, a.result,
                   a.risk_signal, a.anomaly_signal, a.policy_reasons, a.metadata,
                   a.trace_id, a.environment_id, a.intent_envelope
            FROM review_queue r
            JOIN audit_log a ON a.id = r.entry_id
            WHERE r.status = 'PENDING'
            ORDER BY r.created_at ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        pending = []
        for r in rows:
            envelope = json.loads(r["intent_envelope"])
            intent = json.loads(base64.b64decode(envelope["data"]).decode())
            pending.append({
                "entry_id": r["entry_id"],
                "queued_at": r["queued_at"],
                "timestamp": r["timestamp"],
                "risk_signal": r["risk_signal"],
                "anomaly_signal": r["anomaly_signal"],
                "policy_reasons": json.loads(r["policy_reasons"] or "[]"),
                "metadata": json.loads(r["metadata"] or "{}"),
                "trace_id": r["trace_id"],
                "environment_id": r["environment_id"],
                "intent": intent,
            })
        return pending

    def resolve_review(
        self,
        entry_id: str,
        resolved_by: str,
        approve: bool,
        notes: str = "",
    ) -> Dict[str, Any]:
        """
        Resolve a pending REVIEW as either approved (-> ALLOW) or denied
        (-> BLOCK). The original REVIEW entry is never mutated — audit
        entries are append-only by design. Instead this writes the human
        resolution as its OWN new, signed, chained audit entry that
        references the original by id, giving a full paper trail:
        automated REVIEW -> human decision -> final outcome.
        """
        cur = self.conn.cursor()

        # Atomically claim this review so two reviewers can't resolve the
        # same item concurrently. The UPDATE...WHERE status='PENDING' is a
        # single atomic DB statement, so this doubles as the concurrency
        # guard without needing an extra application-level lock.
        claimed = cur.execute(
            "UPDATE review_queue SET status = 'RESOLVING' WHERE entry_id = ? AND status = 'PENDING'",
            (entry_id,),
        )
        self.conn.commit()
        if claimed.rowcount == 0:
            row = cur.execute(
                "SELECT status FROM review_queue WHERE entry_id = ?", (entry_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"no review found for entry_id={entry_id}")
            raise ValueError(
                f"review for entry_id={entry_id} is not pending "
                f"(current status: {row['status']})"
            )

        # Recover the (already-redacted) original intent so an approval
        # voucher can be bound to its exact hash. This closes the gap
        # where resolve_review only recorded a decision and left the
        # caller with no cryptographically bound way to re-submit the
        # same action after human approval.
        row = cur.execute(
            "SELECT intent_envelope FROM audit_log WHERE id = ?", (entry_id,)
        ).fetchone()
        original_intent: Dict[str, Any] = {}
        if row and row["intent_envelope"]:
            try:
                envelope = json.loads(row["intent_envelope"])
                original_intent = json.loads(
                    base64.b64decode(envelope["data"]).decode()
                )
            except Exception:
                original_intent = {}

        new_decision = "ALLOW" if approve else "BLOCK"
        try:
            resolution_entry_id = self.log_decision(
                intent={"review_resolution_for": entry_id},
                decision=new_decision,
                result="reviewed_approved" if approve else "reviewed_denied",
                verification_score=1.0,  # human-verified
                risk_signal=0.0,
                anomaly_signal=0.0,
                policy_reasons=[f"human_review:{'approved' if approve else 'denied'}"],
                metadata={
                    "reviewed_entry_id": entry_id,
                    "reviewer": resolved_by,
                    "notes": notes,
                },
            )
        except Exception:
            # Release the claim rather than leaving it stuck in RESOLVING
            # forever if the audit write itself failed (e.g. breaker open).
            cur.execute(
                "UPDATE review_queue SET status = 'PENDING' WHERE entry_id = ?",
                (entry_id,),
            )
            self.conn.commit()
            raise

        cur.execute(
            """
            UPDATE review_queue
            SET status = ?, resolved_at = ?, resolved_by = ?,
                resolution_entry_id = ?, notes = ?
            WHERE entry_id = ?
            """,
            (
                "APPROVED" if approve else "DENIED",
                time.time(),
                resolved_by,
                resolution_entry_id,
                notes,
                entry_id,
            ),
        )
        self.conn.commit()

        return {
            "entry_id": entry_id,
            "final_decision": new_decision,
            "resolution_entry_id": resolution_entry_id,
            "resolved_by": resolved_by,
            "original_intent": original_intent,
        }

    def verify_chain(self) -> Dict[str, Any]:
        """
        Verify hash chain and signatures over all entries.
        """
        cur = self.conn.cursor()
        rows = cur.execute(
            """
            SELECT id, timestamp, decision, result, intent_envelope,
                   prev_hash, entry_hash, signature, engine_version, environment_id
            FROM audit_log ORDER BY timestamp ASC, rowid ASC
            """
        ).fetchall()

        broken = []
        last_hash = GENESIS_HASH
        total = len(rows)

        for r in rows:
            envelope = json.loads(r["intent_envelope"])
            body = json.dumps(
                {
                    "id": r["id"],
                    "ts": r["timestamp"],
                    "decision": r["decision"],
                    "result": r["result"],
                    "envelope": envelope,
                    "prev_hash": r["prev_hash"],
                    "engine_version": r["engine_version"],
                    "environment_id": r["environment_id"],
                },
                sort_keys=True,
            ).encode()

            recomputed = hashlib.sha256(body).hexdigest()
            hash_ok = recomputed == r["entry_hash"] and r["prev_hash"] == last_hash
            sig_ok = self.crypto.verify(body, base64.b64decode(r["signature"]))

            if not (hash_ok and sig_ok):
                broken.append(r["id"])

            last_hash = r["entry_hash"]

        integrity_pressure = (len(broken) / total) if total > 0 else 0.0

        return {
            "valid": len(broken) == 0,
            "broken_entries": broken,
            "entries_checked": total,
            "integrity_pressure": integrity_pressure,
        }

    def stats(self, hours: int = 24) -> Dict[str, Any]:
        cutoff = time.time() - hours * 3600
        row = self.conn.cursor().execute(
            """
            SELECT COUNT(*) total,
                   SUM(CASE WHEN decision='ALLOW' THEN 1 ELSE 0 END) allowed,
                   SUM(CASE WHEN decision='BLOCK' THEN 1 ELSE 0 END) blocked,
                   AVG(verification_score) avg_verification
            FROM audit_log WHERE timestamp > ?
            """,
            (cutoff,),
        ).fetchone()
        return {
            "total": row["total"] or 0,
            "allowed": row["allowed"] or 0,
            "blocked": row["blocked"] or 0,
            "avg_verification_score": row["avg_verification"] or 0.0,
        }

    def export_report(self, hours: int = 24) -> List[Dict[str, Any]]:
        """
        Export a time-bounded audit report suitable for regulators.
        """
        cutoff = time.time() - hours * 3600
        cur = self.conn.cursor()
        rows = cur.execute(
            """
            SELECT id, timestamp, decision, result, verification_score,
                   risk_signal, anomaly_signal, policy_reasons, metadata,
                   trace_id, engine_version, environment_id
            FROM audit_log WHERE timestamp > ? ORDER BY timestamp ASC
            """,
            (cutoff,),
        ).fetchall()

        report = []
        for r in rows:
            report.append(
                {
                    "id": r["id"],
                    "timestamp": r["timestamp"],
                    "decision": r["decision"],
                    "result": r["result"],
                    "verification_score": r["verification_score"],
                    "risk_signal": r["risk_signal"],
                    "anomaly_signal": r["anomaly_signal"],
                    "policy_reasons": json.loads(r["policy_reasons"] or "[]"),
                    "metadata": json.loads(r["metadata"] or "{}"),
                    "trace_id": r["trace_id"],
                    "engine_version": r["engine_version"],
                    "environment_id": r["environment_id"],
                }
            )
        return report

# ============================================================================
# SECURITY (JWT AUTH + REVOCATION)
# ============================================================================

class SecurityLayer:
    """
    JWT-based auth with revocation table.
    """

    def __init__(
        self,
        config: Dict[str, Any],
        crypto: CryptoEngine,
        db_connection_provider: Callable[[], sqlite3.Connection],
    ):
        self.config = config
        self.crypto = crypto
        self._get_conn = db_connection_provider
        self._init_revocation_table()

    def _init_revocation_table(self):
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS token_blacklist (
                jti TEXT PRIMARY KEY,
                revoked_at REAL
            )
            """
        )
        conn.commit()

    def generate_token(self, user: str, role: str, ttl: Optional[int] = None) -> str:
        payload = {
            "user": user,
            "role": role,
            "exp": int(time.time()) + (ttl or self.config["jwt_ttl_seconds"]),
            "iat": int(time.time()),
            "jti": str(uuid.uuid4()),
        }
        return self.crypto.encode_jwt(payload)

    def issue_approval_voucher(
        self,
        entry_id: str,
        intent_hash: str,
        reviewer: str,
        ttl_seconds: int = 3600,
    ) -> str:
        """
        Issue a short-lived, signed approval voucher bound to a specific
        REVIEW entry and the hash of the (redacted) intent that was reviewed.

        The caller can later re-submit the *same* intent together with this
        voucher; the engine will honour it as a human-approved ALLOW without
        re-running policy/risk gates. The voucher itself is just a JWT —
        it does not grant broad authority, only permission for that exact
        intent hash after that exact review.

        Single-use (F-04): after a successful ALLOW that is durably logged,
        the voucher's jti is written to the token blacklist so a second
        submission with the same voucher is rejected. If the first attempt
        is blocked by a hard safety gate (circuit open, rate limit, etc.)
        the voucher remains valid for retry.
        """
        payload = {
            "typ": "approval_voucher",
            "entry_id": entry_id,
            "intent_hash": intent_hash,
            "reviewer": reviewer,
            "exp": int(time.time()) + ttl_seconds,
            "iat": int(time.time()),
            "jti": str(uuid.uuid4()),
        }
        return self.crypto.encode_jwt(payload)

    def verify_approval_voucher(
        self, voucher: str, intent: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Verify an approval voucher and check that it matches the supplied
        intent. Raises ValueError on any problem (expired, wrong type,
        intent hash mismatch, revoked, bad signature).
        """
        try:
            decoded = jwt.decode(
                voucher, self.crypto.public_pem, algorithms=["RS256"]
            )
        except Exception as e:
            raise ValueError(f"Invalid approval voucher: {e}")

        if decoded.get("typ") != "approval_voucher":
            raise ValueError("token is not an approval voucher")

        intent_hash = hashlib.sha256(
            json.dumps(intent, sort_keys=True).encode()
        ).hexdigest()
        if decoded.get("intent_hash") != intent_hash:
            raise ValueError(
                "approval voucher intent_hash does not match supplied intent "
                "(voucher is bound to the exact intent that was reviewed)"
            )

        jti = decoded.get("jti")
        if jti:
            conn = self._get_conn()
            row = conn.cursor().execute(
                "SELECT 1 FROM token_blacklist WHERE jti = ?", (jti,)
            ).fetchone()
            if row:
                raise ValueError("approval voucher has been revoked")

        return decoded

    def revoke_token(self, jti: str):
        conn = self._get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO token_blacklist (jti, revoked_at) VALUES (?, ?)",
            (jti, time.time()),
        )
        conn.commit()

    def verify_token(self, token: str) -> Dict[str, Any]:
        try:
            decoded = jwt.decode(token, self.crypto.public_pem, algorithms=["RS256"])
        except Exception as e:
            raise ValueError(f"Invalid token: {e}")

        jti = decoded.get("jti")
        if jti:
            conn = self._get_conn()
            row = conn.cursor().execute(
                "SELECT 1 FROM token_blacklist WHERE jti = ?", (jti,)
            ).fetchone()
            if row:
                raise ValueError("Token revoked")

        return decoded

# ============================================================================
# CACHE (USER-AWARE, TTL + LRU)
# ============================================================================

class GovernanceCache:
    def __init__(self, max_size: int = 1000):
        self.store: Dict[str, Any] = {}
        self.expiry: Dict[str, float] = {}
        self.max_size = max_size
        self._order: deque = deque()
        self._lock = asyncio.Lock()

    async def set(self, key: str, value: Any, ttl: int = 60):
        async with self._lock:
            if len(self.store) >= self.max_size and key not in self.store:
                while self._order:
                    oldest = self._order.popleft()
                    if oldest in self.store:
                        del self.store[oldest]
                        self.expiry.pop(oldest, None)
                        break
            self.store[key] = value
            self.expiry[key] = time.time() + ttl
            if key in self._order:
                self._order.remove(key)
            self._order.append(key)

    async def get(self, key: str) -> Optional[Any]:
        async with self._lock:
            if key not in self.store:
                return None
            if time.time() > self.expiry.get(key, 0):
                del self.store[key]
                self.expiry.pop(key, None)
                if key in self._order:
                    self._order.remove(key)
                return None
            if key in self._order:
                self._order.remove(key)
            self._order.append(key)
            return self.store[key]

    def stats(self) -> Dict[str, Any]:
        now = time.time()
        return {
            "size": len(self.store),
            "max_size": self.max_size,
            "expired": sum(1 for v in self.expiry.values() if now > v),
        }

    def clear(self) -> int:
        """
        Synchronously clear every cached decision. Deliberately not async
        and not lock-guarded, so it can be called from a plain sync method
        like reload_policy_spec() without needing an event loop. This is a
        rare admin operation, not a hot path — the worst case if it races
        with a concurrent get()/set() is one request seeing an extra cache
        miss, not a correctness or security issue.
        """
        n = len(self.store)
        self.store.clear()
        self.expiry.clear()
        self._order.clear()
        return n

# ============================================================================
# DECLARATIVE POLICY SPEC + ENGINE
# ============================================================================

@dataclass
class PolicyRule:
    """
    Declarative policy rule.

    Example:
        - type: "pii"
        - id: "pii_email"
        - pattern: regex
        - action: "BLOCK"
        - reason: "pii:email"
    """
    id: str
    type: str
    pattern: Optional[str] = None
    term: Optional[str] = None
    action: str = "BLOCK"
    reason: str = ""

@dataclass
class PolicySpec:
    """
    Declarative policy specification.

    Contains:
        - PII rules
        - banned terms
        - action-level rules (optional)

    Can be built in code (see CertifiedGovernanceEngine's built-in default)
    or loaded from a YAML/JSON file via from_file(), so policy changes are
    a config edit + reload, not a code change + redeploy.
    """
    pii_rules: List[PolicyRule] = field(default_factory=list)
    banned_terms: List[PolicyRule] = field(default_factory=list)
    action_rules: List[PolicyRule] = field(default_factory=list)

    _SECTION_TYPES = {
        "pii_rules": "pii",
        "banned_terms": "banned",
        "action_rules": "action",
    }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PolicySpec":
        """
        Build and validate a PolicySpec from a parsed dict (already loaded
        from YAML or JSON). Raises ValueError with a specific, actionable
        message on any structural problem — a policy file is config an
        operator edits by hand, so it should fail loudly and precisely at
        load time rather than silently produce a broken or partial spec.
        """
        if not isinstance(data, dict):
            raise ValueError(
                f"policy spec must be a mapping at the top level, got {type(data).__name__}"
            )

        seen_ids: set = set()

        def _build_section(section_name: str) -> List[PolicyRule]:
            entries = data.get(section_name, [])
            if entries in (None, {}):
                entries = []
            if not isinstance(entries, list):
                raise ValueError(
                    f"'{section_name}' must be a list of rules, got {type(entries).__name__}"
                )

            rules: List[PolicyRule] = []
            for i, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    raise ValueError(
                        f"{section_name}[{i}] must be a mapping, got {type(entry).__name__}"
                    )

                rule_id = entry.get("id")
                if not rule_id or not isinstance(rule_id, str):
                    raise ValueError(f"{section_name}[{i}] is missing a valid string 'id'")
                if rule_id in seen_ids:
                    raise ValueError(f"duplicate rule id '{rule_id}' (rule ids must be unique across the whole spec)")
                seen_ids.add(rule_id)

                pattern = entry.get("pattern")
                term = entry.get("term")
                if not pattern and not term:
                    raise ValueError(f"rule '{rule_id}' must define either 'pattern' (regex) or 'term' (exact match)")
                if pattern and term:
                    raise ValueError(f"rule '{rule_id}' must define only one of 'pattern' or 'term', not both")
                if pattern:
                    try:
                        re.compile(pattern)
                    except re.error as e:
                        raise ValueError(f"rule '{rule_id}' has an invalid regex pattern: {e}")

                action = entry.get("action", "BLOCK")
                if action not in ("BLOCK", "ALLOW", "REVIEW"):
                    raise ValueError(f"rule '{rule_id}' has unsupported action '{action}' (must be BLOCK, ALLOW, or REVIEW)")

                reason = entry.get("reason") or rule_id
                rule_type = entry.get("type", cls._SECTION_TYPES.get(section_name, section_name))

                rules.append(PolicyRule(
                    id=rule_id, type=rule_type, pattern=pattern, term=term,
                    action=action, reason=reason,
                ))
            return rules

        return cls(
            pii_rules=_build_section("pii_rules"),
            banned_terms=_build_section("banned_terms"),
            action_rules=_build_section("action_rules"),
        )

    @classmethod
    def from_file(cls, path: str) -> "PolicySpec":
        """
        Load a PolicySpec from a .yaml/.yml or .json file. YAML support
        requires PyYAML; a clear ImportError is raised (not a silent
        fallback) if it's missing and a .yaml file is requested.
        """
        if not os.path.exists(path):
            raise FileNotFoundError(f"policy spec file not found: {path}")

        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()

        ext = os.path.splitext(path)[1].lower()
        if ext in (".yaml", ".yml"):
            try:
                import yaml
            except ImportError as e:
                raise ImportError(
                    "loading a .yaml/.yml policy spec requires PyYAML "
                    "(pip install pyyaml), or use a .json spec file instead"
                ) from e
            data = yaml.safe_load(raw) or {}
        elif ext == ".json":
            data = json.loads(raw) if raw.strip() else {}
        else:
            raise ValueError(
                f"unsupported policy spec file extension '{ext}' — use .yaml, .yml, or .json"
            )

        return cls.from_dict(data)

class PolicyEngine:
    """
    Policy engine that interprets PolicySpec and returns reasons.

    The spec can be supplied directly (`spec=`), loaded from a file
    (`spec_path=`), or left as the built-in default. reload() lets an
    operator push an updated spec file at runtime without restarting the
    engine: the new spec is fully loaded and validated into local
    variables first, and only swapped in — under a lock — if that
    succeeds, so a bad file never leaves the engine either running with
    no policy or serving requests against a half-updated one.
    """

    def __init__(self, spec: Optional[PolicySpec] = None, spec_path: Optional[str] = None):
        self._lock = threading.Lock()
        self.spec_path = spec_path
        if spec_path:
            spec = PolicySpec.from_file(spec_path)
        elif spec is None:
            spec = self._default_spec()
        self.spec = spec
        self._compiled_patterns: Dict[str, re.Pattern] = self._compile_patterns(spec)

    @staticmethod
    def _compile_patterns(spec: PolicySpec) -> Dict[str, re.Pattern]:
        compiled: Dict[str, re.Pattern] = {}
        for rule in spec.pii_rules + spec.banned_terms + spec.action_rules:
            if rule.pattern:
                compiled[rule.id] = re.compile(rule.pattern, re.IGNORECASE)
        return compiled

    def reload(self, spec_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Reload the policy spec from disk. Pass spec_path to switch to a
        different file, or omit it to re-load the currently configured
        path (e.g. after an operator edits it in place).
        """
        path = spec_path or self.spec_path
        if not path:
            raise ValueError("no spec_path configured — pass one explicitly to reload()")

        new_spec = PolicySpec.from_file(path)
        new_compiled = self._compile_patterns(new_spec)

        with self._lock:
            self.spec = new_spec
            self._compiled_patterns = new_compiled
            self.spec_path = path

        counts = {
            "pii_rules": len(new_spec.pii_rules),
            "banned_terms": len(new_spec.banned_terms),
            "action_rules": len(new_spec.action_rules),
        }
        logger.info(f"Policy spec reloaded from {path}: {counts}")
        return counts

    @staticmethod
    def _default_spec() -> PolicySpec:
        return PolicySpec(
            pii_rules=[
                PolicyRule(
                    id="pii_email",
                    type="pii",
                    pattern=r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
                    action="BLOCK",
                    reason="pii:email",
                ),
                PolicyRule(
                    id="pii_phone",
                    type="pii",
                    pattern=r"(?:\b\d{3}[-.]\d{3}[-.]\d{4}\b|\(\d{3}\)\s*\d{3}[-.]?\d{4}|\+\d{1,3}[\s.-]?\d{2,4}[\s.-]?\d{3,4}[\s.-]?\d{3,4})",
                    action="BLOCK",
                    reason="pii:phone",
                ),
                PolicyRule(
                    id="pii_ssn",
                    type="pii",
                    pattern=r"\b\d{3}-\d{2}-\d{4}\b",
                    action="BLOCK",
                    reason="pii:ssn",
                ),
            ],
            banned_terms=[
                PolicyRule(
                    id="banned_password",
                    type="banned",
                    pattern=r"\bpassword\b",
                    action="BLOCK",
                    reason="banned_term:password",
                ),
                PolicyRule(
                    id="banned_credit_card",
                    type="banned",
                    pattern=r"\bcredit card\b",
                    action="BLOCK",
                    reason="banned_term:credit card",
                ),
                PolicyRule(
                    id="banned_ssn_term",
                    type="banned",
                    pattern=r"\bssn\b|\bsocial security\b",
                    action="BLOCK",
                    reason="banned_term:ssn",
                ),
            ],
            action_rules=[],
        )

    def evaluate(self, intent: Dict[str, Any]) -> Dict[str, Any]:
        """
        Evaluate intent against declarative policy spec.

        Returns:
            decision_hint: "BLOCK", "REVIEW", or "ALLOW" (hint only)
            reasons: list of policy reasons
        """
        text = json.dumps(intent)
        reasons: List[str] = []
        actions: List[str] = []

        # Snapshot spec + compiled patterns together under the lock, so a
        # concurrent reload() can't hand this call a mix of old and new
        # rules mid-evaluation.
        with self._lock:
            spec = self.spec
            compiled_patterns = self._compiled_patterns

        for rule in spec.pii_rules + spec.banned_terms:
            pat = compiled_patterns.get(rule.id)
            if pat and pat.search(text):
                reasons.append(rule.reason)
                actions.append(rule.action)

        # Action-level rules (optional)
        action_name = intent.get("action", "")
        for rule in spec.action_rules:
            if rule.term and rule.term.lower() == action_name.lower():
                reasons.append(rule.reason)
                actions.append(rule.action)

        if any(a == "BLOCK" for a in actions):
            decision_hint = "BLOCK"
        elif any(a == "REVIEW" for a in actions):
            decision_hint = "REVIEW"
        else:
            decision_hint = "ALLOW"

        return {
            "decision_hint": decision_hint,
            "reasons": reasons,
        }

    def redact(self, intent: Dict[str, Any]) -> Dict[str, Any]:
        """
        Return a copy of `intent` with any substring matched by a PII rule
        replaced by a non-reversible marker: "[REDACTED:<reason>:<hash8>]".

        The 8-char marker is a truncated SHA-256 of the original matched
        value, not the value itself — it lets an investigator confirm two
        blocked attempts contained the *same* PII (e.g. the same email
        repeated across attempts) without the plaintext ever being written
        to disk. This is one-way; there is no way to recover the original
        value from the marker.

        Only pii_rules are masked here, not banned_terms — a banned term
        like "password" or "credit card" is a flagged *word*, not a
        sensitive *value*, so keeping it visible in the audit record is
        fine and useful for review; masking it would just make the log
        less readable for no privacy benefit.

        Falls back to a coarse, fully-redacted envelope if substitution
        would produce invalid JSON (e.g. a match spans a structural
        character) — better to over-redact than risk a PII fragment
        surviving in the stored envelope.
        """
        text = json.dumps(intent, sort_keys=True)
        redacted_text = text
        any_match = False

        with self._lock:
            spec = self.spec
            compiled_patterns = self._compiled_patterns

        for rule in spec.pii_rules:
            pat = compiled_patterns.get(rule.id)
            if not pat:
                continue

            def _mask(m: "re.Match", rule=rule) -> str:
                value_hash = hashlib.sha256(m.group(0).encode()).hexdigest()[:8]
                return f"[REDACTED:{rule.reason}:{value_hash}]"

            new_text = pat.sub(_mask, redacted_text)
            if new_text != redacted_text:
                any_match = True
            redacted_text = new_text

        if not any_match:
            return intent

        try:
            return json.loads(redacted_text)
        except json.JSONDecodeError:
            logger.warning(
                "PII redaction broke JSON structure; storing a coarse "
                "fully-redacted envelope instead of a partial one."
            )
            return {"_redacted": True, "_original_action": intent.get("action", "unknown")}

# ============================================================================
# CIRCUIT BREAKER
# ============================================================================

class CircuitBreaker:
    class State(Enum):
        CLOSED = "CLOSED"
        OPEN = "OPEN"
        HALF_OPEN = "HALF_OPEN"

    def __init__(self, failure_threshold: int = 3, timeout: float = 5.0):
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.state = self.State.CLOSED
        self.failures = 0
        self.last_failure_time = 0.0
        self.successes = 0
        self._lock = asyncio.Lock()

    async def call(self, func: Callable[[], Any]) -> Any:
        async with self._lock:
            now = time.time()
            if self.state == self.State.OPEN:
                if now - self.last_failure_time > self.timeout:
                    self.state = self.State.HALF_OPEN
                    self.successes = 0
                    logger.info("Circuit entering HALF_OPEN")
                else:
                    raise RuntimeError(f"Circuit open (failures={self.failures})")

        try:
            result = await func()
        except Exception:
            async with self._lock:
                self.failures += 1
                self.last_failure_time = time.time()
                if self.failures >= self.failure_threshold:
                    self.state = self.State.OPEN
                    logger.warning(f"Circuit opened after {self.failures} failures")
            raise
        else:
            async with self._lock:
                if self.state == self.State.HALF_OPEN:
                    self.successes += 1
                    if self.successes >= 2:
                        self.state = self.State.CLOSED
                        self.failures = 0
                        logger.info("Circuit closed")
                else:
                    self.failures = max(0, self.failures - 1)
            return result

    def stats(self) -> Dict[str, Any]:
        return {
            "state": self.state.value,
            "failures": self.failures,
            "successes": self.successes,
            "last_failure": self.last_failure_time,
        }

# ============================================================================
# FLOW CONTROL (TOKEN BUCKET)
# ============================================================================

class FlowControl:
    def __init__(self, capacity: int, refill_per_sec: float):
        self.capacity = capacity
        self.tokens = float(capacity)
        self.refill_per_sec = refill_per_sec
        self.last_check = time.time()
        self._lock = threading.Lock()
        self._total_consumed = 0.0
        self._total_rejected = 0.0

    def consume(self, amount: float = 1.0) -> float:
        """
        Consume tokens and return utilization (0-1).
        """
        with self._lock:
            now = time.time()
            elapsed = now - self.last_check
            self.last_check = now
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_sec)

            if self.tokens >= amount:
                self.tokens -= amount
                self._total_consumed += amount
                return (self.capacity - self.tokens) / self.capacity
            else:
                self._total_rejected += amount
                return 1.0

    def mode(self, utilization: float) -> str:
        if utilization < 0.5:
            return "NORMAL"
        elif utilization < 0.7:
            return "ELEVATED"
        elif utilization < 0.9:
            return "HIGH_PRESSURE"
        else:
            return "EMERGENCY"

    def stats(self) -> Dict[str, Any]:
        return {
            "capacity": self.capacity,
            "current_tokens": self.tokens,
            "utilization": (self.capacity - self.tokens) / self.capacity,
            "total_consumed": self._total_consumed,
            "total_rejected": self._total_rejected,
        }

# ============================================================================
# PER-USER RATE LIMITING (SLIDING WINDOW)
# ============================================================================

class UserRateLimiter:
    """
    Per-user sliding-window rate limiter.

    This is deliberately separate from FlowControl: FlowControl's token
    bucket protects the *engine* from aggregate overload across everyone,
    but it's a single shared pool — one noisy or compromised user can
    consume the whole bucket and starve every other user, and FlowControl
    has no way to tell users apart. This class enforces a per-user ceiling
    (`max_actions_per_user_per_hour`) on top of that, so no single actor
    can exhaust capacity meant to be shared.

    Every call to record_and_check() counts, whether the action that
    follows is ultimately ALLOWed, REVIEWed, or BLOCKed — a rate limit
    that only counted successful actions would let an attacker retry
    forever without ever tripping it.
    """

    def __init__(self, max_per_hour: int, max_tracked_users: int = 10000):
        self.max_per_hour = max_per_hour
        self.max_tracked_users = max_tracked_users
        self._windows: Dict[str, deque] = {}
        self._lock = threading.Lock()

    def record_and_check(self, user_id: str) -> Dict[str, Any]:
        now = time.time()
        cutoff = now - 3600.0

        with self._lock:
            window = self._windows.setdefault(user_id, deque())
            while window and window[0] < cutoff:
                window.popleft()
            window.append(now)
            count = len(window)

            # Opportunistic cleanup so a long-running process with many
            # distinct users doesn't accumulate one deque per user forever.
            # A user's entry is only actually removed once their whole
            # window has aged out, so this never affects correctness —
            # only memory footprint.
            if len(self._windows) > self.max_tracked_users:
                self._prune_stale_locked(cutoff)

        return {
            "count_last_hour": count,
            "limit": self.max_per_hour,
            "exceeded": count > self.max_per_hour,
        }

    def _prune_stale_locked(self, cutoff: float) -> None:
        stale = [
            uid for uid, w in self._windows.items()
            if not w or w[-1] < cutoff
        ]
        for uid in stale:
            del self._windows[uid]

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "tracked_users": len(self._windows),
                "max_per_hour": self.max_per_hour,
            }

# ============================================================================
# CERTIFIED GOVERNANCE ENGINE
# ============================================================================

class CertifiedGovernanceEngine:
    """
    Wires auth -> flow control -> policy -> signals -> rule-based decision
    -> signed audit log into one call: execute_governed_action.

    All mathematical outputs are treated as signals; decision logic is
    explicit, rule-based, and auditable.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None, crypto: Optional[CryptoEngine] = None):
        defaults = self._default_config()
        if config:
            defaults.update(config)
        self.config = defaults
        logging.basicConfig(level=self.config.get("log_level", logging.INFO))

        # `crypto` is an injection point for tests/tooling: RSA-3072
        # keygen is genuinely expensive, so a test suite creating many
        # engines can pass one shared CryptoEngine instead of paying that
        # cost per instance. Production code should leave this as None —
        # each deployed engine should have its own key.
        if crypto is not None:
            self.crypto = crypto
        else:
            provider = self.config.get("signing_key_provider")
            self.crypto = CryptoEngine(
                private_key_path=self.config.get("signing_key_path"),
                provider=provider,
                require_persisted_key=self.config.get("require_persisted_key"),
            )
        self.storage = AuditStorage(self.config["db_path"], self.crypto)
        self.security = SecurityLayer(self.config, self.crypto, lambda: self.storage.conn)
        self.cache = GovernanceCache(max_size=1000)
        self.policy_engine = PolicyEngine(spec_path=self.config.get("policy_spec_path"))
        self.breaker = CircuitBreaker(
            failure_threshold=self.config["circuit_failure_threshold"],
            timeout=self.config["circuit_timeout_seconds"],
        )
        self.flow_control = FlowControl(
            capacity=self.config["rate_limit_capacity"],
            refill_per_sec=self.config["rate_limit_refill_per_sec"],
        )
        self.user_rate_limiter = UserRateLimiter(
            max_per_hour=self.config["max_actions_per_user_per_hour"],
        )
        self.signals = MathematicalSignals()

        self._start_time = time.time()
        self._metrics_buffer: deque = deque(maxlen=100)
        self._risk_history: deque = deque(maxlen=50)
        self._latency_samples: deque = deque(maxlen=100)

        self.business_metrics = {
            "total_actions": 0,
            "allowed_actions": 0,
            "blocked_actions": 0,
            "review_actions": 0,
            "actions_by_user": {},
            "actions_by_action": {},
        }

    @staticmethod
    def _default_config() -> Dict[str, Any]:
        return {
            "db_path": "certified_governance.db",
            "signing_key_path": "certified_governance_signing_key.pem",
            "signing_key_provider": None,
            "require_persisted_key": None,  # or True / honor GOVERNANCE_REQUIRE_PERSISTED_KEY
            "rate_limit_capacity": 20,
            "rate_limit_refill_per_sec": 5,
            "cache_ttl_seconds": 60,
            "jwt_ttl_seconds": 300,
            "circuit_failure_threshold": 3,
            "circuit_timeout_seconds": 5,
            "log_level": logging.INFO,
            "anomaly_std_threshold": 3.0,
            "risk_prediction_horizon": 5,
            "max_actions_per_user_per_hour": 1000,
            "policy_spec_path": None,
            "risk_block_threshold": 0.8,
            "risk_review_threshold": 0.5,
            "integrity_block_threshold": 0.5,
        }

    # ------------------------------------------------------------------
    # Core governed action
    # ------------------------------------------------------------------

    async def execute_governed_action(
        self,
        intent: Dict[str, Any],
        token: str,
        trace_id: Optional[str] = None,
        environment_id: str = "default",
        approval_voucher: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Main governance call.

        Invariants:
            - Auth must succeed or action is rejected.
            - Policy must be evaluated (unless a valid approval_voucher
              for this exact intent is presented).
            - Signals must be computed.
            - Decision must be rule-based and logged.

        approval_voucher:
            Optional short-lived JWT issued by resolve_review(approve=True).
            When present and valid for this exact intent, the engine
            treats the action as already human-approved and returns ALLOW
            without re-applying policy/risk gates. The voucher is
            single-intent-bound (hash of the intent) and does not grant
            broader authority.
        """
        start_time = time.time()
        try:
            decoded = self.security.verify_token(token)
            user_id = decoded.get("user", "anonymous")
            role = decoded.get("role", "unknown")

            # Honour a valid human-approval voucher *before* policy/risk
            # evaluation so a previously reviewed action is not re-blocked
            # by the same rules that sent it to REVIEW in the first place.
            voucher_claims: Optional[Dict[str, Any]] = None
            if approval_voucher:
                voucher_claims = self.security.verify_approval_voucher(
                    approval_voucher, intent
                )

            # Per-user rate check. This is deliberately separate from
            # flow_control below: flow_control protects the engine as a
            # whole from aggregate overload via one shared bucket, but a
            # single user can legally consume that entire shared bucket
            # and starve everyone else. This enforces a ceiling per actor.
            user_rate_status = self.user_rate_limiter.record_and_check(user_id)
            user_rate_exceeded = user_rate_status["exceeded"]

            # Flow control
            flow_utilization = self.flow_control.consume(1.0)
            flow_mode = self.flow_control.mode(flow_utilization)

            # Policy evaluation (declarative). Skipped only when a valid
            # approval voucher is present — the human already judged this
            # exact intent.
            if voucher_claims is not None:
                policy_hint = "ALLOW"
                policy_reasons = [
                    f"human_review:approved_via_voucher:"
                    f"{voucher_claims.get('entry_id', '')}"
                ]
            else:
                policy_result = self.policy_engine.evaluate(intent)
                policy_hint = policy_result["decision_hint"]
                policy_reasons = policy_result["reasons"]

            # Signals
            anomaly_signal = self._calculate_anomaly_signal()
            risk_signal_result = self.signals.risk_signal(
                verification_score=0.9 if policy_hint == "ALLOW" else 0.3,
                anomaly_score=anomaly_signal["anomaly_signal"],
                flow_utilization=flow_utilization,
            )
            risk_signal = risk_signal_result["risk_signal"]

            # Chain verification is real storage/crypto I/O, so it goes
            # through the circuit breaker rather than being called directly.
            # This single read is reused below for the integrity signal AND
            # later for the analytics response, instead of scanning the
            # whole chain twice per request.
            try:
                async def _read_chain_status():
                    return self.storage.verify_chain()

                chain_status = await self.breaker.call(_read_chain_status)
                chain_unavailable = False
            except Exception as e:
                logger.error(f"Chain verification unavailable: {e}")
                chain_status = {
                    "valid": False,
                    "broken_entries": [],
                    "entries_checked": 0,
                    "integrity_pressure": 0.0,
                }
                chain_unavailable = True

            integrity_signal = self.signals.integrity_pressure_signal(
                chain_breaks=len(chain_status["broken_entries"]),
                total_entries=chain_status["entries_checked"],
                time_window_hours=24.0,
            )["integrity_pressure"]
            if chain_unavailable:
                # "we couldn't check the chain" must never look like "we
                # checked, and it's fine" — treat it as maximum pressure,
                # not the default zero a failed/empty check would imply.
                integrity_signal = 1.0

            # Read circuit state AFTER the breaker call above, so a failure
            # that just tripped the breaker is reflected in THIS decision,
            # not only in the next request.
            circuit_state = self.breaker.state.value

            # Cache (user-aware)
            cache_key = self._cache_key(user_id, intent)
            cached = await self.cache.get(cache_key)
            cache_hit = cached is not None and not user_rate_exceeded

            if cache_hit:
                result = cached
            else:
                # Rule-based decision. A valid approval voucher short-circuits
                # to ALLOW: the human already judged this exact intent.
                # Hard safety gates (circuit open, integrity pressure, user
                # rate limit) still apply — a voucher is not a blank cheque
                # to override operational safety.
                if voucher_claims is not None:
                    if circuit_state == "OPEN" or user_rate_exceeded:
                        final_decision = "BLOCK"
                    elif integrity_signal > self.config["integrity_block_threshold"]:
                        final_decision = "BLOCK"
                    else:
                        final_decision = "ALLOW"
                else:
                    final_decision = self._decide(
                        policy_hint=policy_hint,
                        policy_reasons=policy_reasons,
                        risk_signal=risk_signal,
                        anomaly_signal=anomaly_signal["anomaly_signal"],
                        flow_utilization=flow_utilization,
                        integrity_signal=integrity_signal,
                        circuit_state=circuit_state,
                        user_rate_exceeded=user_rate_exceeded,
                        user_id=user_id,
                        role=role,
                        intent=intent,
                    )

                if user_rate_exceeded:
                    policy_reasons = list(policy_reasons) + ["rate_limit:user_exceeded"]

                metadata = {
                    "user": user_id,
                    "role": role,
                    "flow_mode": flow_mode,
                    "flow_utilization": flow_utilization,
                    "risk_signal_level": risk_signal_result["qualitative_level"],
                    "integrity_signal": integrity_signal,
                    "user_rate_count_last_hour": user_rate_status["count_last_hour"],
                    "user_rate_limit": user_rate_status["limit"],
                }
                if voucher_claims is not None:
                    metadata["approval_voucher_entry_id"] = voucher_claims.get("entry_id")
                    metadata["approval_voucher_reviewer"] = voucher_claims.get("reviewer")

                # Redact PII out of the intent BEFORE it ever reaches the
                # audit envelope. This runs regardless of decision — an
                # ALLOWed action can still contain PII the policy engine
                # didn't need to act on, and the audit log should never be
                # the place sensitive values end up permanently retained.
                redacted_intent = self.policy_engine.redact(intent)

                # Log — also breaker-guarded, since this is a real DB write
                # + RSA-PSS sign. If it fails, we must NOT report the
                # decision as if it had been durably, verifiably recorded.
                try:
                    async def _write_log():
                        return self.storage.log_decision(
                            intent=redacted_intent,
                            decision=final_decision,
                            result=self._result_status(final_decision),
                            verification_score=0.9 if final_decision == "ALLOW" else 0.3,
                            risk_signal=risk_signal,
                            anomaly_signal=anomaly_signal["anomaly_signal"],
                            policy_reasons=policy_reasons,
                            metadata=metadata,
                            trace_id=trace_id,
                            engine_version="CertifiedGovernanceEngine v1.0",
                            environment_id=environment_id,
                        )

                    entry_id = await self.breaker.call(_write_log)
                    logged = True
                except Exception as e:
                    # Fail closed: an ALLOW that never made it into the
                    # signed audit chain is unauditable, which for a
                    # governance system is worse than a denied action.
                    # We do not silently drop this into the generic
                    # top-level error handler — we still return a coherent
                    # BLOCK result explaining why.
                    logger.error(f"Audit log write failed, failing closed: {e}")
                    final_decision = "BLOCK"
                    entry_id = None
                    logged = False
                    policy_reasons = list(policy_reasons) + ["audit_log_unavailable"]

                self._update_business_metrics(final_decision, user_id, intent)

                # F-04: single-use approval vouchers. Revoke the voucher's
                # jti only after a successful ALLOW that was durably logged.
                # If hard safety gates forced BLOCK (circuit open, rate
                # limit, integrity pressure) or the audit write failed, the
                # voucher is left intact so the caller can retry.
                if (
                    voucher_claims is not None
                    and final_decision == "ALLOW"
                    and logged
                ):
                    voucher_jti = voucher_claims.get("jti")
                    if voucher_jti:
                        self.security.revoke_token(voucher_jti)

                result = {
                    "status": "success" if logged else "degraded",
                    "decision": final_decision,
                    "risk_signal": risk_signal,
                    "risk_level": risk_signal_result["qualitative_level"],
                    "policy_reasons": policy_reasons,
                    "entry_id": entry_id,
                    "logged": logged,
                    "timestamp": time.time(),
                }

                if final_decision == "ALLOW" and logged:
                    await self.cache.set(
                        cache_key,
                        result,
                        ttl=self.config["cache_ttl_seconds"],
                    )

            latency = time.time() - start_time
            self._latency_samples.append(latency)
            self._risk_history.append(risk_signal)

            response = self._analytics_response(
                result=result,
                latency=latency,
                flow_utilization=flow_utilization,
                flow_mode=flow_mode,
                risk_signal=risk_signal_result,
                anomaly_signal=anomaly_signal,
                integrity_signal=integrity_signal,
                cache_hit=cache_hit,
                chain_status=chain_status,
            )
            return response

        except Exception as e:
            logger.error(f"Action execution failed: {e}")
            return {
                "status": "error",
                "error": str(e),
                "timestamp": time.time(),
            }

    # ------------------------------------------------------------------
    # Signals + decision logic
    # ------------------------------------------------------------------

    def _calculate_anomaly_signal(self) -> Dict[str, Any]:
        """
        Anomaly signal based on CPU usage history.
        """
        self._record_system_metrics()
        if len(self._metrics_buffer) >= 10:
            cpu_series = [m["cpu"] for m in self._metrics_buffer]
            return self.signals.anomaly_signal(
                metrics=cpu_series,
                window=min(10, len(cpu_series)),
                std_threshold=self.config["anomaly_std_threshold"],
            )
        return {
            "anomaly_signal": 0.3,
            "z_score": 0.0,
            "severity": "LOW",
            "reason": "default_until_enough_history",
            "mean": 0.0,
            "std": 0.0,
            "current": 0.0,
        }

    def _decide(
        self,
        policy_hint: str,
        policy_reasons: List[str],
        risk_signal: float,
        anomaly_signal: float,
        flow_utilization: float,
        integrity_signal: float,
        circuit_state: str,
        user_rate_exceeded: bool,
        user_id: str,
        role: str,
        intent: Dict[str, Any],
    ) -> str:
        """
        Explicit, rule-based decision logic.

        Rules (example set, tunable via config/spec):
            - If circuit is OPEN -> BLOCK
            - If integrity_signal > threshold -> BLOCK
            - If user has exceeded their per-hour action cap -> BLOCK
            - If policy_hint == BLOCK -> BLOCK
            - If policy_hint == REVIEW -> REVIEW
            - If risk_signal > risk_block_threshold -> BLOCK
            - If risk_signal > risk_review_threshold -> REVIEW
            - If flow_utilization > 0.9 -> REVIEW
            - Else ALLOW
        """
        cfg = self.config

        if circuit_state == "OPEN":
            return "BLOCK"

        if integrity_signal > cfg["integrity_block_threshold"]:
            return "BLOCK"

        if user_rate_exceeded:
            return "BLOCK"

        if policy_hint == "BLOCK":
            return "BLOCK"

        if policy_hint == "REVIEW":
            return "REVIEW"

        if risk_signal > cfg["risk_block_threshold"]:
            return "BLOCK"

        if risk_signal > cfg["risk_review_threshold"]:
            return "REVIEW"

        if flow_utilization > 0.9:
            return "REVIEW"

        return "ALLOW"

    @staticmethod
    def _result_status(decision: str) -> str:
        if decision == "BLOCK":
            return "blocked"
        elif decision == "REVIEW":
            return "review"
        else:
            return "success"

    def _cache_key(self, user_id: str, intent: Dict[str, Any]) -> str:
        intent_str = json.dumps(intent, sort_keys=True)
        intent_hash = hashlib.sha256(intent_str.encode()).hexdigest()
        return f"intent:{user_id}:{intent_hash}"

    def _update_business_metrics(self, decision: str, user_id: str, intent: Dict[str, Any]):
        self.business_metrics["total_actions"] += 1
        if decision == "ALLOW":
            self.business_metrics["allowed_actions"] += 1
        elif decision == "BLOCK":
            self.business_metrics["blocked_actions"] += 1
        elif decision == "REVIEW":
            self.business_metrics["review_actions"] += 1

        if user_id not in self.business_metrics["actions_by_user"]:
            self.business_metrics["actions_by_user"][user_id] = 0
        self.business_metrics["actions_by_user"][user_id] += 1

        action = intent.get("action", "unknown")
        if action not in self.business_metrics["actions_by_action"]:
            self.business_metrics["actions_by_action"][action] = 0
        self.business_metrics["actions_by_action"][action] += 1

    # ------------------------------------------------------------------
    # System metrics + analytics
    # ------------------------------------------------------------------

    def _record_system_metrics(self):
        vm = psutil.virtual_memory()
        cpu = psutil.cpu_percent(interval=0.1)
        disk = psutil.disk_usage("/")

        self._metrics_buffer.append(
            {
                "cpu": cpu / 100.0,
                "memory": vm.percent / 100.0,
                "disk": disk.percent / 100.0,
                "timestamp": time.time(),
            }
        )

    def _system_health(self) -> Dict[str, Any]:
        vm = psutil.virtual_memory()
        cpu = psutil.cpu_percent(interval=0.1)
        disk = psutil.disk_usage("/")

        uptime = time.time() - self._start_time

        memory_score = 1.0 - (vm.percent / 100.0)
        cpu_score = 1.0 - (cpu / 100.0)
        disk_score = 1.0 - (disk.percent / 100.0)
        health_score = (memory_score + cpu_score + disk_score) / 3.0 * 100.0

        if health_score > 70.0:
            status = "HEALTHY"
        elif health_score > 40.0:
            status = "DEGRADED"
        else:
            status = "CRITICAL"

        return {
            "health_score": health_score,
            "status": status,
            "cpu_usage": cpu,
            "memory_usage": vm.percent,
            "disk_usage": disk.percent,
            "uptime_hours": uptime / 3600.0,
            "uptime_formatted": str(timedelta(seconds=int(uptime))),
            "processes": len(psutil.pids()),
        }

    def _throughput(self) -> Dict[str, Any]:
        vm = psutil.virtual_memory()
        cpu = psutil.cpu_percent(interval=0.1)

        recent_actions = self.business_metrics["total_actions"]
        time_window = max(1.0, (time.time() - self._start_time) / 3600.0)
        current_load = recent_actions / time_window
        max_capacity = 1000.0

        return self.signals.throughput_signal(
            current_load=current_load,
            max_capacity=max_capacity,
            cpu_usage=cpu / 100.0,
            memory_usage=vm.percent / 100.0,
        )

    def _analytics_response(
        self,
        result: Dict[str, Any],
        latency: float,
        flow_utilization: float,
        flow_mode: str,
        risk_signal: Dict[str, Any],
        anomaly_signal: Dict[str, Any],
        integrity_signal: float,
        cache_hit: bool,
        chain_status: Dict[str, Any],
    ) -> Dict[str, Any]:
        system_health = self._system_health()
        throughput = self._throughput()

        if len(self._risk_history) >= 3:
            trend = self.signals.risk_trend_signal(
                historical_risks=list(self._risk_history),
                horizon=self.config["risk_prediction_horizon"],
            )
        else:
            trend = {
                "trend": "UNKNOWN",
                "slope": 0.0,
                "intercept": 0.0,
                "predictions": [],
                "r_squared": None,
            }

        return {
            "result": result,
            "analytics": {
                "latency": latency,
                "flow_utilization": flow_utilization,
                "flow_mode": flow_mode,
                "cache_hit": cache_hit,
                "risk_signal": risk_signal,
                "anomaly_signal": anomaly_signal,
                "integrity_signal": integrity_signal,
                "system_health": system_health,
                "throughput": throughput,
                "risk_trend": trend,
                "chain_status": chain_status,
            },
            "metadata": {
                "timestamp": time.time(),
                "engine_version": "CertifiedGovernanceEngine v1.0",
            },
        }

    # ------------------------------------------------------------------
    # Public stats
    # ------------------------------------------------------------------

    def list_pending_reviews(self, limit: int = 50) -> List[Dict[str, Any]]:
        """List REVIEW-decisions awaiting human resolution, oldest first."""
        return self.storage.list_pending_reviews(limit)

    def resolve_review(
        self,
        entry_id: str,
        resolved_by: str,
        approve: bool,
        notes: str = "",
        voucher_ttl_seconds: int = 3600,
    ) -> Dict[str, Any]:
        """
        Human-in-the-loop resolution of a pending REVIEW decision.

        Records the reviewer's judgment as a new signed audit entry and
        updates business metrics. When approve=True, also issues a
        short-lived approval voucher bound to the exact (redacted) intent
        that was reviewed. The caller can re-submit that same intent
        together with the voucher; the engine will then ALLOW it without
        re-applying policy/risk gates, while still writing a fresh audit
        entry that links back to this review.

        The engine still does not itself perform side-effects of the
        original intent — that remains the caller's responsibility. The
        voucher only closes the previous gap where there was no
        cryptographically bound way to express "a human already approved
        this exact action".
        """
        result = self.storage.resolve_review(entry_id, resolved_by, approve, notes)
        if approve:
            self.business_metrics["allowed_actions"] += 1
            original_intent = result.pop("original_intent", {}) or {}
            intent_hash = hashlib.sha256(
                json.dumps(original_intent, sort_keys=True).encode()
            ).hexdigest()
            voucher = self.security.issue_approval_voucher(
                entry_id=entry_id,
                intent_hash=intent_hash,
                reviewer=resolved_by,
                ttl_seconds=voucher_ttl_seconds,
            )
            result["approval_voucher"] = voucher
            result["voucher_intent_hash"] = intent_hash
            result["voucher_expires_in_seconds"] = voucher_ttl_seconds
        else:
            self.business_metrics["blocked_actions"] += 1
            result.pop("original_intent", None)
        self.business_metrics["review_actions"] = max(
            0, self.business_metrics["review_actions"] - 1
        )
        return result

    def reload_policy_spec(self, spec_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Hot-reload the declarative policy spec from disk without
        restarting the engine or dropping in-flight requests. See
        PolicyEngine.reload() for the load-then-swap safety details.

        Also clears the decision cache: a cached ALLOW/BLOCK was computed
        under the OLD policy, so leaving it in place would mean an
        operator pushes a policy change and identical requests keep
        getting the previous answer until the cache TTL happens to expire.
        """
        counts = self.policy_engine.reload(spec_path)
        cleared = self.cache.clear()
        logger.info(f"Policy reload cleared {cleared} cached decisions")
        counts["cache_entries_cleared"] = cleared
        return counts

    def stats(self, hours: int = 24) -> Dict[str, Any]:
        storage_stats = self.storage.stats(hours)
        avg_latency = statistics.mean(self._latency_samples) if self._latency_samples else 0.0

        return {
            "storage": storage_stats,
            "cache": self.cache.stats(),
            "circuit_breaker": self.breaker.stats(),
            "flow_control": self.flow_control.stats(),
            "user_rate_limiter": self.user_rate_limiter.stats(),
            "system_health": self._system_health(),
            "business_metrics": {
                "total_actions": self.business_metrics["total_actions"],
                "allowed_actions": self.business_metrics["allowed_actions"],
                "blocked_actions": self.business_metrics["blocked_actions"],
                "review_actions": self.business_metrics["review_actions"],
                "avg_latency": avg_latency,
            },
        }

# === ZK EXTENSION ===
import hashlib
import json
import logging
import secrets
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

# CertifiedGovernanceEngine, CryptoEngine defined above in this module

logger = logging.getLogger("certified_governance.zk")

# ============================================================================
# GROUP SETUP
# ============================================================================
# RFC 3526 Group 14 (2048-bit MODP). Published, widely reviewed, used in
# IKE/TLS for years — using a well-known prime instead of generating our
# own avoids the (real) risk of accidentally picking parameters with a
# hidden weakness.

_P_HEX = (
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD"
    "129024E088A67CC74020BBEA63B139B22514A08798E3404"
    "DDEF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C"
    "245E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406"
    "B7EDEE386BFB5A899FA5AE9F24117C4B1FE649286651ECE"
    "45B3DC2007CB8A163BF0598DA48361C55D39A69163FA8FD"
    "24CF5F83655D23DCA3AD961C62F356208552BB9ED529077"
    "096966D670C354E4ABC9804F1746C08CA18217C32905E46"
    "2E36CE3BE39E772C180E86039B2783A2EC07A28FB5C55DF"
    "06F4C52C9DE2BCBF6955817183995497CEA956AE515D226"
    "1898FA051015728E5A8AACAA68FFFFFFFFFFFFFFFF"
)
P = int(_P_HEX, 16)
Q = (P - 1) // 2  # order of the quadratic-residue subgroup (P is a safe prime)

def _hash_to_qr_element(seed: bytes) -> int:
    """
    Derive a generator of the order-Q subgroup with NO known discrete log
    relative to any other generator ("nothing up my sleeve"). Hashing
    straight to a group element (rather than computing g^H(seed), which
    would hand everyone the discrete log H(seed)) is the standard way to
    do this: squaring any element lands it in the quadratic-residue
    subgroup, which has prime order Q since P is a safe prime, so any
    non-trivial result has order exactly Q.
    """
    x = int.from_bytes(hashlib.sha256(seed).digest(), "big") % P
    for i in range(1000):
        cand = pow(x, 2, P)
        if cand not in (0, 1):
            return cand
        x = int.from_bytes(hashlib.sha256(seed + bytes([i])).digest(), "big") % P
    raise RuntimeError("failed to derive a group element from seed")

G = _hash_to_qr_element(b"zk-governance/generator/G/v1")
H = _hash_to_qr_element(b"zk-governance/generator/H/v1")
assert G != H and G != 1 and H != 1

def _rand_scalar() -> int:
    return 1 + secrets.randbelow(Q - 1)

def _inv(x: int, mod: int) -> int:
    return pow(x, -1, mod)

def commit(value: int, blinding: int) -> int:
    """Pedersen commitment: g^value * h^blinding mod P."""
    return (pow(G, value % Q, P) * pow(H, blinding % Q, P)) % P

# ============================================================================
# BIT PROOF: Chaum-Damgard-Schoenmakers (CDS94) OR-proof, Fiat-Shamir NIZK
# ============================================================================
# Proves a commitment C opens to 0 or to 1, without revealing which.
# Branch 0: C == h^r            (statement "C is a commitment to 0")
# Branch 1: C * g^-1 == h^r     (statement "C is a commitment to 1")
# Exactly one branch is proven honestly; the other is simulated. Fiat-
# Shamir binds both branches' first messages into a single challenge that
# the prover must split between the two branches, which is what stops a
# prover from faking both simultaneously.

@dataclass
class BitProof:
    a0: int
    a1: int
    e0: int
    e1: int
    z0: int
    z1: int

def _bit_challenge(C: int, a0: int, a1: int) -> int:
    data = f"{C}|{a0}|{a1}".encode()
    return int.from_bytes(hashlib.sha256(data).digest(), "big") % Q

def prove_bit(bit: int, blinding: int) -> BitProof:
    if bit not in (0, 1):
        raise ValueError("bit must be 0 or 1")
    C = commit(bit, blinding)
    target0 = C
    target1 = (C * _inv(G, P)) % P

    if bit == 0:
        # Real proof on branch 0, simulate branch 1.
        k0 = _rand_scalar()
        a0 = pow(H, k0, P)
        e1 = _rand_scalar()
        z1 = _rand_scalar()
        a1 = (pow(H, z1, P) * _inv(pow(target1, e1, P), P)) % P
        e = _bit_challenge(C, a0, a1)
        e0 = (e - e1) % Q
        z0 = (k0 + e0 * blinding) % Q
    else:
        k1 = _rand_scalar()
        a1 = pow(H, k1, P)
        e0 = _rand_scalar()
        z0 = _rand_scalar()
        a0 = (pow(H, z0, P) * _inv(pow(target0, e0, P), P)) % P
        e = _bit_challenge(C, a0, a1)
        e1 = (e - e0) % Q
        z1 = (k1 + e1 * blinding) % Q

    return BitProof(a0=a0, a1=a1, e0=e0, e1=e1, z0=z0, z1=z1)

def verify_bit(C: int, proof: BitProof) -> bool:
    target0 = C
    target1 = (C * _inv(G, P)) % P
    e = _bit_challenge(C, proof.a0, proof.a1)
    if (proof.e0 + proof.e1) % Q != e % Q:
        return False
    lhs0 = pow(H, proof.z0, P)
    rhs0 = (proof.a0 * pow(target0, proof.e0, P)) % P
    lhs1 = pow(H, proof.z1, P)
    rhs1 = (proof.a1 * pow(target1, proof.e1, P)) % P
    return lhs0 == rhs0 and lhs1 == rhs1

# ============================================================================
# RANGE PROOF (bit decomposition), linked to an external commitment
# ============================================================================
# Proves: the value committed in C_target (public, published up front)
# lies in [0, 2^K_BITS - 1], without revealing the value. Built by
# committing to each bit separately, proving each is 0/1 via prove_bit,
# and choosing the bit blindings so their weighted sum equals the
# blinding of C_target — which makes the product of (bit commitment)^2^i
# equal to C_target automatically, tying the range proof to the specific
# committed value instead of some other value the prover picked.

K_BITS = 18  # risk_signal in [0,1] scaled by RISK_SCALE=1e5 -> max value ~1e5; 2^18 ≈ 262k is the
             # tightest power of two that comfortably covers it. Each bit costs ~5 modexps to prove
             # and verify, so this directly trades proof precision/headroom for latency — don't raise
             # it without a reason, and don't lower it below 17 or values near the top of the range
             # stop being provable.

@dataclass
class RangeProof:
    bit_commitments: List[int]
    bit_proofs: List[BitProof]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bit_commitments": [str(c) for c in self.bit_commitments],
            "bit_proofs": [
                {
                    "a0": str(p.a0), "a1": str(p.a1),
                    "e0": str(p.e0), "e1": str(p.e1),
                    "z0": str(p.z0), "z1": str(p.z1),
                }
                for p in self.bit_proofs
            ],
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RangeProof":
        return cls(
            bit_commitments=[int(c) for c in d["bit_commitments"]],
            bit_proofs=[
                BitProof(
                    a0=int(p["a0"]), a1=int(p["a1"]),
                    e0=int(p["e0"]), e1=int(p["e1"]),
                    z0=int(p["z0"]), z1=int(p["z1"]),
                )
                for p in d["bit_proofs"]
            ],
        )

def prove_range(value: int, blinding: int) -> RangeProof:
    """
    Prove 0 <= value < 2^K_BITS. `blinding` must be the blinding used in
    the external commitment C_target = commit(value, blinding) that the
    verifier already has — the bit blindings are chosen so they
    reconstruct exactly that commitment.
    """
    if value < 0 or value >= (1 << K_BITS):
        raise ValueError(
            f"value {value} out of provable range [0, {(1 << K_BITS) - 1}] "
            f"— refusing to fabricate a proof of a claim that isn't true"
        )

    bits = [(value >> i) & 1 for i in range(K_BITS)]

    # Choose all but the last bit blinding at random; solve the last one
    # so that sum(r_i * 2^i) == blinding (mod Q) exactly.
    bit_blindings = [_rand_scalar() for _ in range(K_BITS - 1)]
    partial = sum(r * (1 << i) for i, r in enumerate(bit_blindings)) % Q
    last_weight = pow(2, K_BITS - 1, Q)
    last_blinding = ((blinding - partial) * _inv(last_weight, Q)) % Q
    bit_blindings.append(last_blinding)

    bit_commitments = [commit(bits[i], bit_blindings[i]) for i in range(K_BITS)]
    bit_proofs = [prove_bit(bits[i], bit_blindings[i]) for i in range(K_BITS)]

    return RangeProof(bit_commitments=bit_commitments, bit_proofs=bit_proofs)

def verify_range(C_target: int, proof: RangeProof) -> bool:
    if len(proof.bit_commitments) != K_BITS or len(proof.bit_proofs) != K_BITS:
        return False

    for C_bit, bit_proof in zip(proof.bit_commitments, proof.bit_proofs):
        if not verify_bit(C_bit, bit_proof):
            return False

    # Recompose: product of C_bit^(2^i) must equal C_target.
    recomposed = 1
    for i, C_bit in enumerate(proof.bit_commitments):
        recomposed = (recomposed * pow(C_bit, 1 << i, P)) % P

    return recomposed == C_target

# ============================================================================
# CLAIM TYPES
# ============================================================================

class ClaimType(Enum):
    RISK_THRESHOLD = "risk_threshold"       # real ZK range proof
    POLICY_REDACTION = "policy_redaction"   # signed attestation, not ZK
    REVIEW_APPROVAL = "review_approval"     # signed attestation, not ZK

RISK_SCALE = 100_000  # risk_signal in [0,1] -> integer with 5 decimals of precision

# ============================================================================
# ATTESTATION STORE
# ============================================================================

class AttestationStore:
    """
    Stores generated proofs/attestations alongside the audit log they
    reference. Kept as a separate table (not mixed into audit_log) since
    these are optional, generated after the fact, and — for the ZK ones —
    contain commitments/proof transcripts rather than governance data.
    """

    def __init__(self, conn_provider):
        self._get_conn = conn_provider
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self):
        conn = self._get_conn()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS zk_attestations (
                attestation_id TEXT PRIMARY KEY,
                entry_id TEXT NOT NULL,
                claim_type TEXT NOT NULL,
                is_zero_knowledge INTEGER NOT NULL,
                public_inputs TEXT NOT NULL,
                proof_data TEXT NOT NULL,
                signature TEXT,
                created_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_attestation_entry ON zk_attestations(entry_id)"
        )
        conn.commit()

    def store(
        self,
        entry_id: str,
        claim_type: ClaimType,
        is_zero_knowledge: bool,
        public_inputs: Dict[str, Any],
        proof_data: Dict[str, Any],
        signature: Optional[str] = None,
    ) -> str:
        attestation_id = str(uuid.uuid4())
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                """
                INSERT INTO zk_attestations
                (attestation_id, entry_id, claim_type, is_zero_knowledge,
                 public_inputs, proof_data, signature, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attestation_id,
                    entry_id,
                    claim_type.value,
                    1 if is_zero_knowledge else 0,
                    json.dumps(public_inputs),
                    json.dumps(proof_data),
                    signature,
                    time.time(),
                ),
            )
            conn.commit()
        return attestation_id

    def list_for_entry(self, entry_id: str) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM zk_attestations WHERE entry_id = ? ORDER BY created_at ASC",
            (entry_id,),
        ).fetchall()
        return [
            {
                "attestation_id": r["attestation_id"],
                "entry_id": r["entry_id"],
                "claim_type": r["claim_type"],
                "is_zero_knowledge": bool(r["is_zero_knowledge"]),
                "public_inputs": json.loads(r["public_inputs"]),
                "proof_data": json.loads(r["proof_data"]),
                "signature": r["signature"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def get(self, attestation_id: str) -> Optional[Dict[str, Any]]:
        conn = self._get_conn()
        r = conn.execute(
            "SELECT * FROM zk_attestations WHERE attestation_id = ?", (attestation_id,)
        ).fetchone()
        if r is None:
            return None
        return {
            "attestation_id": r["attestation_id"],
            "entry_id": r["entry_id"],
            "claim_type": r["claim_type"],
            "is_zero_knowledge": bool(r["is_zero_knowledge"]),
            "public_inputs": json.loads(r["public_inputs"]),
            "proof_data": json.loads(r["proof_data"]),
            "signature": r["signature"],
            "created_at": r["created_at"],
        }

# ============================================================================
# ZK-ENHANCED ENGINE
# ============================================================================

class ZKEnhancedGovernanceEngine(CertifiedGovernanceEngine):
    """
    CertifiedGovernanceEngine plus opt-in proof generation. Proof
    generation is never on the decision path — it happens after
    execute_governed_action's normal decision + audit-log write, and a
    failure to generate a proof never changes or blocks the underlying
    governance decision. Requesting a claim that isn't actually true
    (e.g. RISK_THRESHOLD against a threshold the entry's risk exceeded)
    raises rather than silently returning no proof or a fake one.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None, crypto: Optional[CryptoEngine] = None):
        super().__init__(config, crypto=crypto)
        self.attestations = AttestationStore(lambda: self.storage.conn)

    async def execute_governed_action(
        self,
        intent: Dict[str, Any],
        token: str,
        trace_id: Optional[str] = None,
        environment_id: str = "default",
        generate_proofs: bool = False,
        proof_claims: Optional[List[str]] = None,
        risk_threshold: Optional[float] = None,
        proof_mode: str = "background",
        approval_voucher: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        proof_mode:
            "background" (default) — the governed decision returns
                immediately; proof generation runs as a detached asyncio
                task and lands in list_attestations() shortly after
                (typically low single-digit seconds in this environment —
                see module note on modexp cost). response["attestations"]
                will contain {"pending": [...claims...]}, not finished ids.
            "sync" — block and return with attestation ids already
                populated, as in the earlier synchronous version. Useful
                for tests/scripts, NOT recommended for a request path a
                caller is waiting on: each ZK claim costs multiple
                seconds in this environment (no hardware-accelerated
                bignum backend available here). Signed (non-ZK)
                attestations are cheap either way.

        approval_voucher is forwarded to the base engine (see
        CertifiedGovernanceEngine.execute_governed_action).
        """
        response = await super().execute_governed_action(
            intent, token,
            trace_id=trace_id,
            environment_id=environment_id,
            approval_voucher=approval_voucher,
        )

        entry_id = response.get("result", {}).get("entry_id")
        if not generate_proofs or not entry_id:
            return response

        claims = proof_claims or [ClaimType.RISK_THRESHOLD.value]
        risk_signal = response["result"].get("risk_signal")
        policy_reasons = response["result"].get("policy_reasons", [])
        threshold = risk_threshold if risk_threshold is not None else self.config["risk_review_threshold"]

        if proof_mode == "sync":
            generated, errors = self._generate_claims(entry_id, claims, risk_signal, policy_reasons, threshold)
            response["attestations"] = {"generated": generated, "errors": errors}
        elif proof_mode == "background":
            import asyncio as _asyncio
            _asyncio.create_task(
                self._generate_claims_async(entry_id, claims, risk_signal, policy_reasons, threshold)
            )
            response["attestations"] = {"pending": claims, "entry_id": entry_id}
        else:
            raise ValueError(f"unknown proof_mode: {proof_mode!r} (use 'background' or 'sync')")

        return response

    def _generate_claims(
        self, entry_id: str, claims: List[str], risk_signal: Optional[float],
        policy_reasons: List[str], threshold: float,
    ) -> Tuple[List[str], List[str]]:
        attestation_ids, errors = [], []
        for claim in claims:
            try:
                if claim == ClaimType.RISK_THRESHOLD.value:
                    att_id = self._prove_risk_threshold(entry_id, risk_signal, threshold=threshold)
                elif claim == ClaimType.POLICY_REDACTION.value:
                    att_id = self._attest_policy_redaction(entry_id, policy_reasons)
                else:
                    errors.append(f"unknown claim type: {claim}")
                    continue
                attestation_ids.append(att_id)
            except ValueError as e:
                # A false claim (e.g. risk was NOT below the requested
                # threshold) — report it, don't fabricate a proof.
                errors.append(f"{claim}: {e}")
        return attestation_ids, errors

    async def _generate_claims_async(
        self, entry_id: str, claims: List[str], risk_signal: Optional[float],
        policy_reasons: List[str], threshold: float,
    ) -> None:
        """
        Runs the (CPU-bound, multi-second) proof generation in a worker
        thread so it doesn't block the event loop other requests are
        running on, then logs the outcome. Exceptions here must never
        propagate anywhere that could be mistaken for a governance
        decision — this is purely a background side effect.
        """
        loop = __import__("asyncio").get_event_loop()
        try:
            generated, errors = await loop.run_in_executor(
                None, self._generate_claims, entry_id, claims, risk_signal, policy_reasons, threshold
            )
            if errors:
                logger.warning(f"background proof generation for {entry_id} had errors: {errors}")
        except Exception as e:
            logger.error(f"background proof generation for {entry_id} failed: {e}")

    def resolve_review(
        self,
        entry_id: str,
        resolved_by: str,
        approve: bool,
        notes: str = "",
        generate_proof: bool = False,
        voucher_ttl_seconds: int = 3600,
    ) -> Dict[str, Any]:
        result = super().resolve_review(
            entry_id, resolved_by, approve, notes,
            voucher_ttl_seconds=voucher_ttl_seconds,
        )

        if generate_proof:
            att_id = self._attest_review_approval(entry_id, result, resolved_by, approve, notes)
            result["attestation_id"] = att_id

        return result

    # ------------------------------------------------------------------
    # Claim implementations
    # ------------------------------------------------------------------

    def _prove_risk_threshold(
        self, entry_id: str, risk_signal: Optional[float], threshold: float
    ) -> str:
        """
        Real ZK proof: risk_signal < threshold, without revealing
        risk_signal to whoever verifies this attestation later.
        """
        if risk_signal is None:
            raise ValueError("no risk_signal available on this result to prove a claim about")

        r_scaled = round(risk_signal * RISK_SCALE)
        t_scaled = round(threshold * RISK_SCALE)
        d = (t_scaled - 1) - r_scaled
        if d < 0:
            raise ValueError(
                f"claim is false: risk_signal ({risk_signal}) was not below "
                f"threshold ({threshold}) — refusing to generate a proof of it"
            )

        s_r = _rand_scalar()
        C_r = commit(r_scaled, s_r)

        # C_d = g^(T-1) * C_r^-1 = g^d * h^(-s_r) — verifier can compute this
        # themselves from C_r and the public threshold; the prover just
        # needs blindings for the bit decomposition that sum to (-s_r).
        s_d = (-s_r) % Q
        range_proof = prove_range(d, s_d)

        proof_data = {
            "commitment_r": str(C_r),
            "range_proof": range_proof.to_dict(),
            "k_bits": K_BITS,
        }
        public_inputs = {
            "threshold": threshold,
            "threshold_scaled": t_scaled,
            "scale": RISK_SCALE,
            "claim": "risk_signal < threshold",
        }
        return self.attestations.store(
            entry_id=entry_id,
            claim_type=ClaimType.RISK_THRESHOLD,
            is_zero_knowledge=True,
            public_inputs=public_inputs,
            proof_data=proof_data,
        )

    def _attest_policy_redaction(self, entry_id: str, policy_reasons: List[str]) -> str:
        """
        Signed attestation (NOT zero-knowledge — see module docstring):
        cryptographically certifies which redaction/policy rules fired
        on this entry, signed with the engine's existing RSA-PSS key so
        it can be verified independent of trusting the DB row.
        """
        pii_reasons = [r for r in policy_reasons if r.startswith("pii:")]
        payload = {
            "entry_id": entry_id,
            "claim": "redaction_applied" if pii_reasons else "no_pii_matched",
            "rules_fired": pii_reasons,
            "timestamp": time.time(),
        }
        body = json.dumps(payload, sort_keys=True).encode()
        signature = self.crypto.sign(body).hex()

        return self.attestations.store(
            entry_id=entry_id,
            claim_type=ClaimType.POLICY_REDACTION,
            is_zero_knowledge=False,
            public_inputs=payload,
            proof_data={"signed_payload": json.dumps(payload, sort_keys=True)},
            signature=signature,
        )

    def _attest_review_approval(
        self, entry_id: str, resolve_result: Dict[str, Any],
        resolved_by: str, approve: bool, notes: str,
    ) -> str:
        """Signed attestation of a human reviewer's decision. Not ZK — a
        reviewer's approval isn't a secret."""
        payload = {
            "entry_id": entry_id,
            "resolved_by": resolved_by,
            "approved": approve,
            "final_decision": resolve_result.get("final_decision"),
            "resolution_entry_id": resolve_result.get("resolution_entry_id"),
            "notes": notes,
            "timestamp": time.time(),
        }
        body = json.dumps(payload, sort_keys=True).encode()
        signature = self.crypto.sign(body).hex()

        return self.attestations.store(
            entry_id=entry_id,
            claim_type=ClaimType.REVIEW_APPROVAL,
            is_zero_knowledge=False,
            public_inputs=payload,
            proof_data={"signed_payload": json.dumps(payload, sort_keys=True)},
            signature=signature,
        )

    # ------------------------------------------------------------------
    # Public verification API
    # ------------------------------------------------------------------

    def list_attestations(self, entry_id: str) -> List[Dict[str, Any]]:
        return self.attestations.list_for_entry(entry_id)

    def verify_attestation(self, attestation_id: str) -> Dict[str, Any]:
        """
        Independently re-verify a stored attestation. For ZK claims this
        re-runs the actual cryptographic verification (not just "does a
        row exist"). For signed attestations it re-checks the RSA-PSS
        signature over the exact payload.
        """
        att = self.attestations.get(attestation_id)
        if att is None:
            return {"valid": False, "reason": "attestation not found"}

        claim_type = att["claim_type"]

        if claim_type == ClaimType.RISK_THRESHOLD.value:
            try:
                C_r = int(att["proof_data"]["commitment_r"])
                t_scaled = att["public_inputs"]["threshold_scaled"]
                range_proof = RangeProof.from_dict(att["proof_data"]["range_proof"])
                C_d_expected = (pow(G, t_scaled - 1, P) * _inv(C_r, P)) % P
                ok = verify_range(C_d_expected, range_proof)
                return {
                    "valid": ok,
                    "claim_type": claim_type,
                    "is_zero_knowledge": True,
                    "claim": att["public_inputs"]["claim"],
                    "threshold": att["public_inputs"]["threshold"],
                }
            except Exception as e:
                return {"valid": False, "reason": f"verification error: {e}"}

        elif claim_type in (ClaimType.POLICY_REDACTION.value, ClaimType.REVIEW_APPROVAL.value):
            try:
                signed_payload = att["proof_data"]["signed_payload"]
                signature = bytes.fromhex(att["signature"])
                ok = self.crypto.verify(signed_payload.encode(), signature)
                return {
                    "valid": ok,
                    "claim_type": claim_type,
                    "is_zero_knowledge": False,
                    "payload": json.loads(signed_payload),
                }
            except Exception as e:
                return {"valid": False, "reason": f"verification error: {e}"}

        return {"valid": False, "reason": f"unknown claim type: {claim_type}"}

# ============================================================================
# SELF-TEST — run: python certified_governance_unified.py
# ============================================================================

async def _self_test() -> int:
    """Functional test of the unified module. Returns 0 on success."""
    import tempfile
    from pathlib import Path

    failures = []

    def check(cond: bool, msg: str) -> None:
        if cond:
            print(f"  PASS  {msg}")
        else:
            print(f"  FAIL  {msg}")
            failures.append(msg)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        cfg = {
            "db_path": str(tmp_p / "gov.db"),
            "signing_key_path": str(tmp_p / "signing.pem"),
            "risk_block_threshold": 0.99,
            "risk_review_threshold": 0.50,
            "log_level": 50,
        }

        print("1. Core engine")
        eng = CertifiedGovernanceEngine(cfg)
        token = eng.security.generate_token("tester", "operator")

        r = await eng.execute_governed_action({"action": "ping", "n": 1}, token)
        check(r.get("result", {}).get("decision") == "ALLOW", "clean action ALLOW")

        r = await eng.execute_governed_action(
            {"action": "notify", "email": "hidden@example.com"}, token
        )
        check(r.get("result", {}).get("decision") == "BLOCK", "PII BLOCK")
        check(
            any("pii:email" in x for x in r.get("result", {}).get("policy_reasons", [])),
            "PII reason recorded",
        )

        rows = eng.storage.conn.execute("SELECT intent_envelope FROM audit_log").fetchall()
        leaked = any("hidden@example.com" in (row["intent_envelope"] or "") for row in rows)
        check(not leaked, "PII not stored in audit log")

        print("2. Review + single-use voucher")
        eng.config["risk_review_threshold"] = 0.01
        intent = {"action": "export", "scope": "partial"}
        r = await eng.execute_governed_action(intent, token)
        check(r.get("result", {}).get("decision") == "REVIEW", "forced REVIEW")
        if r.get("result", {}).get("decision") == "REVIEW":
            res = eng.resolve_review(r["result"]["entry_id"], "auditor", True)
            check("approval_voucher" in res, "voucher issued on approve")
            voucher = res.get("approval_voucher")
            r2 = await eng.execute_governed_action(
                intent, token, approval_voucher=voucher
            )
            check(r2.get("result", {}).get("decision") == "ALLOW", "voucher ALLOW")
            r3 = await eng.execute_governed_action(
                intent, token, approval_voucher=voucher
            )
            check(
                r3.get("status") == "error" and "revoked" in r3.get("error", "").lower(),
                "voucher single-use (second use rejected)",
            )

        st = eng.storage.verify_chain()
        check(st.get("valid") is True, f"audit chain valid ({st.get('entries_checked')} entries)")

        print("3. Crypto primitives")
        prov = LocalPEMKeyProvider(str(tmp_p / "k2.pem"))
        sig = prov.sign(b"hello")
        check(prov.verify(b"hello", sig) is True, "LocalPEMKeyProvider sign/verify")
        check(prov.verify(b"hello!", sig) is False, "tampered verify fails")

        b = _rand_scalar()
        C = commit(1, b)
        check(verify_bit(C, prove_bit(1, b)) is True, "bit proof 1")
        check(verify_range(commit(42, b), prove_range(42, b)) is True, "range proof 42")

        print("4. ZK engine")
        zk = ZKEnhancedGovernanceEngine({
            "db_path": str(tmp_p / "zk.db"),
            "signing_key_path": str(tmp_p / "zk.pem"),
            "risk_review_threshold": 0.90,
            "risk_block_threshold": 0.95,
            "log_level": 50,
        })
        tok2 = zk.security.generate_token("bob", "operator")
        resp = await zk.execute_governed_action(
            {"action": "safe_read"},
            tok2,
            generate_proofs=True,
            proof_claims=[ClaimType.RISK_THRESHOLD.value],
            risk_threshold=0.90,
            proof_mode="sync",
        )
        att = resp.get("attestations", {})
        if att.get("generated"):
            v = zk.verify_attestation(att["generated"][0])
            check(v.get("valid") is True and v.get("is_zero_knowledge") is True, "ZK risk proof verifies")
        else:
            # Environment can produce risk >= 0.9; refusal of a false claim is also correct.
            ok_false = any("claim is false" in e for e in att.get("errors", []))
            check(ok_false, f"ZK claim refused or generated (errors={att.get('errors')})")

        resp2 = await zk.execute_governed_action(
            {"action": "mail", "email": "x@y.com"},
            tok2,
            generate_proofs=True,
            proof_claims=[ClaimType.POLICY_REDACTION.value],
            proof_mode="sync",
        )
        att2 = resp2.get("attestations", {})
        if att2.get("generated"):
            v2 = zk.verify_attestation(att2["generated"][0])
            check(v2.get("valid") is True and v2.get("is_zero_knowledge") is False, "redaction attestation verifies")
        else:
            check(False, f"redaction attestation missing: {att2}")

    print()
    if failures:
        print(f"{len(failures)} FAILED")
        return 1
    print("ALL CHECKS PASSED")
    return 0

if __name__ == "__main__":
    raise SystemExit(asyncio.run(_self_test()))
