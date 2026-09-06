"""
Certified Governance Engine v1.0 (HAIS-style reference implementation)

This module implements a regulator-aligned governance kernel:

- Auth (JWT + revocation)
- Flow control (token bucket)
- Declarative policy (PII, banned terms, action rules)
- Mathematical signals (risk, anomaly, trend) as decision-support
- Explicit rule-based decision logic (ALLOW / REVIEW / BLOCK)
- Hash-chained, RSA-PSS signed audit log
- Integrity verification and exportable audit reports

All mathematical models are documented as heuristics / signals, not
certified statistical models. Decision logic is rule-based and fully
auditable.
"""

from __future__ import annotations

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
    """RSA-PSS signing/verification for the audit chain and JWT."""

    def __init__(self, private_key_path: Optional[str] = None):
        if private_key_path and os.path.exists(private_key_path):
            with open(private_key_path, "rb") as f:
                self._private_key = serialization.load_pem_private_key(
                    f.read(), password=None
                )
            logger.info(f"Loaded existing signing key from {private_key_path}")
        else:
            self._private_key = rsa.generate_private_key(
                public_exponent=65537,
                key_size=3072,
            )
            if private_key_path:
                self._persist_key(private_key_path)
                logger.warning(
                    f"Generated new signing key and saved to {private_key_path}. "
                    "Every prior audit-log signature was produced under a different "
                    "key and will now fail verify_chain()."
                )
            else:
                logger.warning(
                    "CryptoEngine started with no private_key_path — a fresh, "
                    "unpersisted key was generated. This key (and every signature "
                    "made with it, and every JWT issued with it) will be lost and "
                    "unverifiable on the next restart. Pass private_key_path in "
                    "production."
                )
        self._public_key = self._private_key.public_key()

    def _persist_key(self, path: str) -> None:
        """
        Write the private key to disk with owner-only permissions.
        Uses a temp-file + atomic rename so a crash mid-write can't leave
        a corrupt or partially-written key file behind.
        """
        directory = os.path.dirname(os.path.abspath(path)) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = None, f"{path}.tmp-{uuid.uuid4().hex}"
        try:
            fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as f:
                fd = None  # ownership transferred to the file object
                f.write(self.private_pem)
            os.replace(tmp_path, path)
        finally:
            if fd is not None:
                os.close(fd)
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    @property
    def private_pem(self) -> bytes:
        return self._private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )

    @property
    def public_pem(self) -> bytes:
        return self._public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

    def sign(self, data: bytes) -> bytes:
        return self._private_key.sign(
            data,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )

    def verify(self, data: bytes, signature: bytes) -> bool:
        try:
            self._public_key.verify(
                signature,
                data,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH,
                ),
                hashes.SHA256(),
            )
            return True
        except Exception:
            return False

# ============================================================================
# STORAGE (HASH-CHAINED, SIGNED AUDIT LOG)
# ============================================================================

class AuditStorage:
    """
    Hash-chained, RSA-signed audit log with integrity verification
    and exportable reports.
    """

    def __init__(self, db_path: str, crypto: CryptoEngine):
        self.db_path = db_path
        self.crypto = crypto
        self._local = threading.local()
        self._chain_lock = threading.Lock()
        self._last_hash = GENESIS_HASH
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

        row = cur.execute(
            "SELECT entry_hash FROM audit_log ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        if row:
            self._last_hash = row["entry_hash"]

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
        ts = time.time()
        envelope = {"data": base64.b64encode(json.dumps(intent).encode()).decode()}

        with self._chain_lock:
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
            FROM audit_log ORDER BY timestamp ASC
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
        return jwt.encode(payload, self.crypto.private_pem, algorithm="RS256")

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
        self.config = config or self._default_config()
        logging.basicConfig(level=self.config.get("log_level", logging.INFO))

        # `crypto` is an injection point for tests/tooling: RSA-3072
        # keygen is genuinely expensive, so a test suite creating many
        # engines can pass one shared CryptoEngine instead of paying that
        # cost per instance. Production code should leave this as None —
        # each deployed engine should have its own key.
        self.crypto = crypto or CryptoEngine(private_key_path=self.config.get("signing_key_path"))
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
    ) -> Dict[str, Any]:
        """
        Main governance call.

        Invariants:
            - Auth must succeed or action is rejected.
            - Policy must be evaluated.
            - Signals must be computed.
            - Decision must be rule-based and logged.
        """
        start_time = time.time()
        try:
            decoded = self.security.verify_token(token)
            user_id = decoded.get("user", "anonymous")
            role = decoded.get("role", "unknown")

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

            # Policy evaluation (declarative)
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
                # Rule-based decision
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
    ) -> Dict[str, Any]:
        """
        Human-in-the-loop resolution of a pending REVIEW decision. This
        records the reviewer's judgment as a new signed audit entry and
        updates coarse business metrics to reflect the final outcome — it
        does not itself perform or unblock the original intent, which is
        a separate concern from this engine's ALLOW/REVIEW/BLOCK decision.
        """
        result = self.storage.resolve_review(entry_id, resolved_by, approve, notes)
        if approve:
            self.business_metrics["allowed_actions"] += 1
        else:
            self.business_metrics["blocked_actions"] += 1
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
