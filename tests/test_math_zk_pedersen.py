"""Maths of the Pedersen / CDS94 bit-proof / bit-decomposition range proof
(certified_governance_unified.py "ZK EXTENSION"; identical constants in hais/zk_enhanced_governance.py).

Group.  P = RFC 3526 group 14 (2048-bit MODP safe prime):
            P = 2^2048 - 2^1984 - 1 + 2^64 ( floor(2^1918 pi) + 124476 )
        verified here by computing pi with integer Machin arithmetic.   Q = (P-1)/2 is prime
        (Miller-Rabin, fixed bases + seeded random bases);  G = (H(seed) mod P)^2 and H likewise
        lie in the order-Q quadratic-residue subgroup:  G^Q = H^Q = 1 (mod P), G, H != 1, G != H.
Commitment.  commit(v, r) = G^v H^r  mod P  -- additively homomorphic:
            commit(a, r1) commit(b, r2) = commit(a+b, r1+r2);  exponents live mod Q.
Bit proof (OR-composition of two Schnorr proofs, Fiat-Shamir):
            e0 + e1 = e = H(C|a0|a1)  (mod Q),   H^{z0} = a0 C^{e0},   H^{z1} = a1 (C/G)^{e1}
            complete for bit in {0,1}; rejects commitments to any other value and any tampered field.
Range proof (value in [0, 2^K)), K = 18:
            bit commitments C_i = commit(b_i, r_i) with sum r_i 2^i = r  =>  prod C_i^{2^i} = commit(v, r) = C
Risk-threshold statement (GovernedStack ZK claim "risk < threshold"):
            d = (T - 1) - r_scaled >= 0  proven in range; the verifier recomputes
            C_d = G^{T-1} C_r^{-1} = commit(d, -s_r).   RISK_SCALE = 1e5 < 2^17 < 2^18.

Fixed seeds for all blindings supplied by the test (the library's internal Fiat-Shamir
nonces use `secrets`, but every property below holds for every nonce).
Range-proof round trips cost ~3.5 s each (4 of them run: one random value, the risk-threshold
statement, and the boundary values 0 and 2^18 - 1); nothing is skipped.
"""
from __future__ import annotations

import hashlib
import random

import certified_governance_unified as cg
import pytest
import zk_enhanced_governance as zk_copy

P, Q, G, H = cg.P, cg.Q, cg.G, cg.H
SEED = 1_618_033
RNG = random.Random(SEED)



# ------------------------------------------------------------------ helpers (independent of the module under test)


def miller_rabin(n: int, rounds: int = 24, seed: int = SEED) -> bool:
    if n < 2:
        return False
    small = (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37)
    for p in small:
        if n % p == 0:
            return n == p
    d, s = n - 1, 0
    while d % 2 == 0:
        d //= 2
        s += 1
    rng = random.Random(seed)
    bases = list(small) + [rng.randrange(2, n - 1) for _ in range(rounds)]
    for a in bases:
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(s - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def pi_fixed(bits: int) -> int:
    """floor(2^bits * pi) by Machin: pi = 16 atan(1/5) - 4 atan(1/239), with 64 guard bits."""
    guard = 64
    one = 1 << (bits + guard)

    def atan_inv(x: int) -> int:
        total = term = one // x
        x2, n, sign = x * x, 3, -1
        while term:
            term //= x2
            total += sign * (term // n)
            n += 2
            sign = -sign
        return total

    return (16 * atan_inv(5) - 4 * atan_inv(239)) >> guard


# ------------------------------------------------------------------ group parameters


def test_P_is_the_rfc3526_group14_constant():
    expected = (1 << 2048) - (1 << 1984) - 1 + (1 << 64) * (pi_fixed(1918) + 124476)
    assert P == expected
    assert P.bit_length() == 2048


def test_P_and_Q_are_prime_safe_prime_structure():
    assert miller_rabin(P)
    assert Q == (P - 1) // 2
    assert miller_rabin(Q)
    assert (P - 1) % 2 == 0 and (P - 1) // Q == 2


def test_generators_are_distinct_elements_of_the_order_Q_subgroup():
    assert 1 < G < P and 1 < H < P and G != H
    assert pow(G, Q, P) == 1 and pow(H, Q, P) == 1  # quadratic residues: order divides prime Q
    assert pow(G, 2, P) != 1 and pow(H, 2, P) != 1  # hence order is exactly Q
    # reproduce the nothing-up-my-sleeve derivation independently
    for seed, val in ((b"zk-governance/generator/G/v1", G), (b"zk-governance/generator/H/v1", H)):
        x = int.from_bytes(hashlib.sha256(seed).digest(), "big") % P
        assert pow(x, 2, P) == val


def test_hais_copy_has_identical_group():
    assert (zk_copy.P, zk_copy.Q, zk_copy.G, zk_copy.H, zk_copy.K_BITS) == (P, Q, G, H, cg.K_BITS)


def test_risk_scale_fits_in_range_proof():
    assert cg.RISK_SCALE == 100_000
    assert cg.RISK_SCALE < (1 << 17) < (1 << cg.K_BITS) < Q


# ------------------------------------------------------------------ Pedersen commitment


def test_commitment_is_additively_homomorphic():
    for _ in range(10):
        a, b = RNG.randrange(0, 10**6), RNG.randrange(0, 10**6)
        r1, r2 = RNG.randrange(1, Q), RNG.randrange(1, Q)
        assert (cg.commit(a, r1) * cg.commit(b, r2)) % P == cg.commit(a + b, (r1 + r2) % Q)
        # scalar multiple: commit(v, r)^k = commit(k v, k r)
        k = RNG.randrange(2, 50)
        assert pow(cg.commit(a, r1), k, P) == cg.commit(k * a, k * r1)


def test_commitment_exponents_are_reduced_mod_Q():
    assert cg.commit(7 + Q, 11) == cg.commit(7, 11)
    assert cg.commit(7, 11 + Q) == cg.commit(7, 11)
    assert cg.commit(-1, 5) == cg.commit(Q - 1, 5)
    assert cg.commit(0, 0) == 1


def test_commit_matches_direct_definition_and_lies_in_subgroup():
    v, r = 31337, RNG.randrange(1, Q)
    assert cg.commit(v, r) == (pow(G, v, P) * pow(H, r, P)) % P
    assert pow(cg.commit(v, r), Q, P) == 1


# ------------------------------------------------------------------ bit proof (CDS94)


@pytest.mark.parametrize("bit", [0, 1])
def test_bit_proof_complete_and_satisfies_the_sigma_equations(bit):
    for _ in range(6):
        r = RNG.randrange(1, Q)
        C = cg.commit(bit, r)
        pf = cg.prove_bit(bit, r)
        assert cg.verify_bit(C, pf)
        e = cg._bit_challenge(C, pf.a0, pf.a1)
        assert (pf.e0 + pf.e1) % Q == e % Q  # challenge split
        t0, t1 = C, (C * pow(G, -1, P)) % P
        assert pow(H, pf.z0, P) == (pf.a0 * pow(t0, pf.e0, P)) % P  # branch 0 equation
        assert pow(H, pf.z1, P) == (pf.a1 * pow(t1, pf.e1, P)) % P  # branch 1 equation


def test_bit_proof_rejects_non_bits_and_wrong_commitments():
    with pytest.raises(ValueError):
        cg.prove_bit(2, 5)
    r = RNG.randrange(1, Q)
    pf = cg.prove_bit(1, r)
    assert not cg.verify_bit(cg.commit(2, r), pf)  # commitment to 2
    assert not cg.verify_bit(cg.commit(0, r), pf)  # proof for 1 does not open a 0 commitment
    assert not cg.verify_bit(cg.commit(1, r + 1), pf)  # wrong blinding
    # honest prover cannot "prove" commit(3, r) is a bit: forge by reusing prove_bit(1) -> fails
    assert not cg.verify_bit(cg.commit(3, r), cg.prove_bit(1, r))


@pytest.mark.parametrize("field", ["a0", "a1", "e0", "e1", "z0", "z1"])
def test_bit_proof_tamper_detection_every_field(field):
    r = RNG.randrange(1, Q)
    C = cg.commit(0, r)
    pf = cg.prove_bit(0, r)
    assert cg.verify_bit(C, pf)
    tampered = cg.BitProof(**{**pf.__dict__, field: (getattr(pf, field) + 1) % (Q if field[0] in "ez" else P)})
    assert not cg.verify_bit(C, tampered)


# ------------------------------------------------------------------ range proof


@pytest.fixture(scope="module")
def range_fixture():
    value = RNG.randrange(1, 1 << cg.K_BITS)
    blinding = RNG.randrange(1, Q)
    C = cg.commit(value, blinding)
    proof = cg.prove_range(value, blinding)
    return value, blinding, C, proof


def test_range_proof_complete_and_bit_commitments_recompose_to_target(range_fixture):
    value, blinding, C, proof = range_fixture
    assert len(proof.bit_commitments) == len(proof.bit_proofs) == cg.K_BITS
    # identity: prod C_i^{2^i} = commit(sum b_i 2^i, sum r_i 2^i) = commit(v, r) = C
    recomposed = 1
    for i, Ci in enumerate(proof.bit_commitments):
        recomposed = (recomposed * pow(Ci, 1 << i, P)) % P
    assert recomposed == C
    assert cg.verify_range(C, proof)


def test_range_proof_rejects_wrong_target_and_tampering(range_fixture):
    value, blinding, C, proof = range_fixture
    # different target commitment (value + 1): all bit proofs still verify, recomposition fails
    assert not cg.verify_range((C * G) % P, proof)
    # tamper the lowest bit commitment: its OR-proof no longer verifies (fails fast)
    bad = cg.RangeProof(
        bit_commitments=[(proof.bit_commitments[0] * G) % P] + proof.bit_commitments[1:],
        bit_proofs=proof.bit_proofs,
    )
    assert not cg.verify_range(C, bad)
    # structural: wrong lengths
    assert not cg.verify_range(C, cg.RangeProof(proof.bit_commitments[:-1], proof.bit_proofs[:-1]))
    # serialisation round-trip is lossless
    again = cg.RangeProof.from_dict(proof.to_dict())
    assert again.bit_commitments == proof.bit_commitments
    assert [vars(p) for p in again.bit_proofs] == [vars(p) for p in proof.bit_proofs]


def test_range_prover_refuses_out_of_range_values():
    for bad in (-1, 1 << cg.K_BITS, (1 << cg.K_BITS) + 12345):
        with pytest.raises(ValueError):
            cg.prove_range(bad, 5)


def test_risk_threshold_statement_algebra_and_end_to_end(range_fixture):
    """Replicate _prove_risk_threshold / verify_attestation maths at primitive level.

    risk 0.31 < threshold 0.50:  d = (T-1) - r = 49999 - 31000 = 18999 in [0, 2^18).
    Verifier-side C_d = G^{T-1} * C_r^{-1} must equal commit(d, -s_r).
    """
    r_scaled = round(0.31 * cg.RISK_SCALE)
    t_scaled = round(0.50 * cg.RISK_SCALE)
    assert (r_scaled, t_scaled) == (31000, 50000)
    d = (t_scaled - 1) - r_scaled
    assert d == 18999 and 0 <= d < (1 << cg.K_BITS)
    s_r = RNG.randrange(1, Q)
    C_r = cg.commit(r_scaled, s_r)
    C_d_expected = (pow(G, t_scaled - 1, P) * pow(C_r, -1, P)) % P
    s_d = (-s_r) % Q
    assert C_d_expected == cg.commit(d, s_d)  # the verifier derivation is exactly commit(d, -s_r)
    proof = cg.prove_range(d, s_d)
    assert cg.verify_range(C_d_expected, proof)
    # a commitment to a DIFFERENT risk (0.32) with the same proof is rejected
    C_r_other = cg.commit(round(0.32 * cg.RISK_SCALE), s_r)
    C_d_other = (pow(G, t_scaled - 1, P) * pow(C_r_other, -1, P)) % P
    assert not cg.verify_range(C_d_other, proof)


def test_false_claim_has_negative_slack_and_cannot_be_proven():
    """risk >= threshold => d < 0 => prove_range refuses (a false statement is never proven)."""
    t_scaled = round(0.50 * cg.RISK_SCALE)
    for risk in (0.50, 0.75, 1.0):
        d = (t_scaled - 1) - round(risk * cg.RISK_SCALE)
        assert d < 0
        with pytest.raises(ValueError):
            cg.prove_range(d, 3)


@pytest.mark.parametrize("value", [0, (1 << cg.K_BITS) - 1])
def test_range_proof_boundary_values(value):
    b = RNG.randrange(1, Q)
    assert cg.verify_range(cg.commit(value, b), cg.prove_range(value, b))
