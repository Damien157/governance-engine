"""
zk_enhanced_governance.py

Adds cryptographic *proof* generation on top of CertifiedGovernanceEngine.

Two genuinely different kinds of "proof" live here, and they are NOT
interchangeable — conflating them is exactly the kind of thing that makes
a "ZK-enhanced" system dishonest, so this module keeps them explicit:

1. RISK_THRESHOLD is a real zero-knowledge proof. A verifier learns that
   the entry's risk_signal was below a stated threshold at decision time,
   and learns NOTHING else about the actual risk_signal value. This is
   built from standard, well-understood primitives:
     - Pedersen commitments over the RFC 3526 2048-bit MODP group (a
       published, vetted prime — not something invented for this module).
     - A Chaum-Damgard-Schoenmakers (CDS94) OR-proof to show a commitment
       opens to 0 or 1, made non-interactive via Fiat-Shamir.
     - A bit-decomposition range proof built from k of those OR-proofs,
       algebraically linked back to the original risk commitment so the
       verifier never needs the prover to reveal it.
   This is real, sound cryptography — soundness and zero-knowledge here
   rest on the discrete-log assumption in the chosen group, not on trust
   in this implementation being "basically fine."

2. POLICY_REDACTION and REVIEW_APPROVAL are signed attestations, not
   zero-knowledge proofs. There is no secret being hidden in either case
   (the PII is already gone — replaced by a one-way hash marker — and a
   reviewer's approval is not confidential). What they add is a
   cryptographic, independently-verifiable signature (RSA-PSS, the same
   scheme the audit chain already uses) binding a specific claim to a
   specific audit entry, so the claim can be verified later without
   trusting whoever is asserting it. Calling these "ZK" would be false
   advertising; they are simply signed statements.

Building a true ZK proof about the SHA-256 redaction marker itself (e.g.
"this hash is the redaction of some PII, and I'm not telling you which
rule matched") would require a general-purpose arithmetic-circuit SNARK
(circom/snarkjs-class tooling) to express SHA-256 as a circuit. That's a
different, much heavier engineering project and is deliberately out of
scope here — implementing it badly would be worse than not implementing
it at all.
"""

from __future__ import annotations

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

from certified_governance import CertifiedGovernanceEngine, CryptoEngine

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
        """
        response = await super().execute_governed_action(
            intent, token, trace_id=trace_id, environment_id=environment_id
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
    ) -> Dict[str, Any]:
        result = super().resolve_review(entry_id, resolved_by, approve, notes)

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
