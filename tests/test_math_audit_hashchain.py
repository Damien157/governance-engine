"""Maths of the audit trail, intent hashing, score projection and the QUANTUM line.

Hash chain (certified_governance_unified.AuditStorage)
    body_i      = JSON(sort_keys){id, ts, decision, result, envelope, prev_hash, engine_version, environment_id}
    entry_hash_i = SHA256(body_i)                     (recomputed here with hashlib, independent of the class)
    prev_hash_1 = "0"*64 (genesis);   prev_hash_{i+1} = entry_hash_i
    signature_i = RSA-PSS(body_i)  -> verify_chain flags row i iff
                  SHA256(body_i') != entry_hash_i  or  prev_hash_i != entry_hash_{i-1}  or  signature invalid.
    integrity_pressure = |broken| / |entries|
    Expected broken-set of each tamper is derived by hand below (e.g. rewriting a row's entry_hash breaks that
    row AND its successor, rewriting its payload breaks only that row, deleting a row breaks its successor).
    SHA-256 avalanche: flipping one input bit flips each of the 256 output bits w.p. 1/2 => Hamming distance
    ~ Binomial(256, 1/2): mean 128, sd 8 (checked with a 6-sigma envelope on the mean of 64 trials, sd 1).
Intent hash (cache key / approval voucher binding)
    h(intent) = SHA256(JSON(intent, sort_keys=True)) -- invariant under key order, sensitive to any value change.
Audit-only score projection (governed_stack.audit_projection)
    risk = clip(r + 2 max(0, .25-cap) + .3 (1-e^{-I}), 0, 1) with BLOCK/REVIEW/error-code floors,
    stability = clip(e^{-2I} * latch/realm factors * e^{-|p_hat|}, 0, 1),
    governance = clip(cap (1 - risk/2) * decision factor +- code adjustments, 0, 1);  all hand-evaluated.
QUANTUM line (governed_stack.quantum_line)
    10 fields x width 15 ("{:+015.8f}": sign + 5 integer digits + '.' + 8 decimals; the module docstring says
    "four integer digits" which is off by one -- the width, not the prose, is what the tests pin) = 150 chars; decode(encode(s)) = s within 5e-9 (rounding to 8 dp);
    reconstruction identities S = sigma(4(x-.5)), tau = 1 + .5 e^S, r' = S tau, cap = e^{-2.2 r'}, I = 4S(1-S)(1+x).
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import sqlite3
import tempfile
from pathlib import Path

import certified_governance_unified as cg
import numpy as np
import pytest
from hais_unified_kernel import SovereignKernel

from governed_stack import audit_projection as ap
from governed_stack import quantum_line as ql

GENESIS = "0" * 64


# ====================================================================================== hash chain


@pytest.fixture(scope="module")
def crypto():
    return cg.CryptoEngine(private_key_path=None)


def _fresh(crypto, n=8):
    d = tempfile.TemporaryDirectory()
    path = str(Path(d.name) / "audit.db")
    store = cg.AuditStorage(path, crypto)
    ids = []
    for i in range(n):
        ids.append(
            store.log_decision(
                intent={"action": "act", "i": i, "payload": {"z": 1, "a": [i, i + 1]}},
                decision=("ALLOW", "BLOCK", "REVIEW")[i % 3],
                result=f"r{i}",
                verification_score=0.9,
                risk_signal=0.1 * i,
                anomaly_signal=0.0,
                policy_reasons=["p"],
                enqueue_review=False,
            )
        )
    return d, path, store, ids


@pytest.fixture
def chain(crypto):
    d, path, store, ids = _fresh(crypto)
    yield path, store, ids
    store.close()
    d.cleanup()


def _rows(path):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    rows = c.execute("SELECT * FROM audit_log ORDER BY timestamp ASC, rowid ASC").fetchall()
    c.close()
    return rows


def _body(row, **override):
    d = {
        "id": row["id"], "ts": row["timestamp"], "decision": row["decision"], "result": row["result"],
        "envelope": json.loads(row["intent_envelope"]), "prev_hash": row["prev_hash"],
        "engine_version": row["engine_version"], "environment_id": row["environment_id"],
    }
    d.update(override)
    return json.dumps(d, sort_keys=True).encode()


def _sql(path, stmt, args=()):
    c = sqlite3.connect(path)
    c.execute(stmt, args)
    c.commit()
    c.close()


def test_chain_hashes_recompute_independently_and_link(chain):
    path, store, ids = chain
    rows = _rows(path)
    assert [r["id"] for r in rows] == ids
    prev = GENESIS
    for r in rows:
        assert r["prev_hash"] == prev
        assert hashlib.sha256(_body(r)).hexdigest() == r["entry_hash"]
        assert len(r["entry_hash"]) == 64 and int(r["entry_hash"], 16) >= 0
        assert store.crypto.verify(_body(r), base64.b64decode(r["signature"]))
        prev = r["entry_hash"]
    ts = [r["timestamp"] for r in rows]
    assert all(b > a for a, b in zip(ts, ts[1:]))  # strictly monotone timestamps
    out = store.verify_chain()
    assert out == {"valid": True, "broken_entries": [], "entries_checked": 8, "integrity_pressure": 0.0}


def test_empty_chain_is_valid_with_zero_pressure(crypto):
    with tempfile.TemporaryDirectory() as d:
        s = cg.AuditStorage(str(Path(d) / "e.db"), crypto)
        assert s.verify_chain() == {"valid": True, "broken_entries": [], "entries_checked": 0,
                                    "integrity_pressure": 0.0}
        s.close()


def test_entry_hash_is_deterministic_and_field_sensitive():
    base = {"id": "x", "ts": 1.5, "decision": "ALLOW", "result": "ok", "envelope": {"data": "aa"},
            "prev_hash": GENESIS, "engine_version": "v", "environment_id": "e"}

    def h(d):
        return hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()

    assert h(base) == h(dict(reversed(list(base.items()))))  # key order irrelevant (sort_keys)
    assert h(base) == h(json.loads(json.dumps(base)))
    seen = {h(base)}
    for k, v in [("id", "y"), ("ts", 1.6), ("decision", "BLOCK"), ("result", "no"), ("envelope", {"data": "ab"}),
                 ("prev_hash", "1" * 64), ("engine_version", "w"), ("environment_id", "f")]:
        mutated = dict(base, **{k: v})
        assert h(mutated) not in seen  # each field participates in the hash, no collisions
        seen.add(h(mutated))


def test_sha256_avalanche_on_single_bit_flip():
    rng = np.random.default_rng(2718)
    dists = []
    for _ in range(64):
        msg = bytearray(rng.bytes(64))
        h0 = int.from_bytes(hashlib.sha256(bytes(msg)).digest(), "big")
        bit = int(rng.integers(0, 512))
        msg[bit // 8] ^= 1 << (bit % 8)
        h1 = int.from_bytes(hashlib.sha256(bytes(msg)).digest(), "big")
        dists.append(bin(h0 ^ h1).count("1"))
    assert abs(np.mean(dists) - 128.0) < 6.0  # 6 sigma of the mean (sigma = 8/sqrt(64) = 1)


@pytest.mark.parametrize(
    "column,new,expected",
    [
        ("result", "tampered", 1),  # payload edit: hash mismatch on that row only (successor links stored hash)
        ("decision", "BLOCK", 1),
        ("timestamp", None, 1),
        ("environment_id", "prod", 1),
        ("intent_envelope", json.dumps({"data": base64.b64encode(b'{"evil": 1}').decode()}), 1),
        ("signature", base64.b64encode(b"\x00" * 256).decode(), 1),  # hash fine, RSA-PSS fails
    ],
)
def test_payload_tamper_breaks_exactly_that_row(chain, column, new, expected):
    path, store, ids = chain
    victim = ids[3]
    if column == "timestamp":
        row = [r for r in _rows(path) if r["id"] == victim][0]
        new = math.nextafter(row["timestamp"], math.inf)  # 1 ulp: keeps ordering, changes the hashed text
    _sql(path, f"UPDATE audit_log SET {column} = ? WHERE id = ?", (new, victim))
    out = store.verify_chain()
    assert out["valid"] is False
    assert out["broken_entries"] == [victim]
    assert len(out["broken_entries"]) == expected
    assert out["integrity_pressure"] == pytest.approx(1 / 8)


def test_rewriting_entry_hash_breaks_row_and_successor(chain):
    path, store, ids = chain
    _sql(path, "UPDATE audit_log SET entry_hash = ? WHERE id = ?", ("f" * 64, ids[2]))
    out = store.verify_chain()
    assert out["broken_entries"] == [ids[2], ids[3]]  # own recompute mismatch + successor's prev_hash link
    assert out["integrity_pressure"] == pytest.approx(2 / 8)


def test_rehashing_a_tampered_row_without_resigning_is_still_caught(chain):
    """An attacker who edits the payload AND recomputes entry_hash (fixing the hash) still fails:
    the successor's prev_hash no longer matches, and the RSA-PSS signature no longer verifies."""
    path, store, ids = chain
    row = [r for r in _rows(path) if r["id"] == ids[4]][0]
    forged_body = _body(row, result="forged")
    _sql(path, "UPDATE audit_log SET result = ?, entry_hash = ? WHERE id = ?",
         ("forged", hashlib.sha256(forged_body).hexdigest(), ids[4]))
    out = store.verify_chain()
    assert out["broken_entries"] == [ids[4], ids[5]]  # row 4: bad signature; row 5: prev_hash link broken


def test_deleting_a_middle_row_breaks_its_successor(chain):
    path, store, ids = chain
    _sql(path, "DELETE FROM audit_log WHERE id = ?", (ids[3],))
    out = store.verify_chain()
    assert out["broken_entries"] == [ids[4]]
    assert out["entries_checked"] == 7 and out["integrity_pressure"] == pytest.approx(1 / 7)


def test_deleting_the_first_row_is_detected_via_genesis_link(chain):
    path, store, ids = chain
    _sql(path, "DELETE FROM audit_log WHERE id = ?", (ids[0],))
    assert store.verify_chain()["broken_entries"] == [ids[1]]


def test_swapping_two_rows_timestamps_is_detected(chain):
    path, store, ids = chain
    rows = {r["id"]: r for r in _rows(path)}
    t2, t3 = rows[ids[2]]["timestamp"], rows[ids[3]]["timestamp"]
    _sql(path, "UPDATE audit_log SET timestamp = ? WHERE id = ?", (t3, ids[2]))
    _sql(path, "UPDATE audit_log SET timestamp = ? WHERE id = ?", (t2, ids[3]))
    assert not store.verify_chain()["valid"]


def test_tail_truncation_is_NOT_detectable_known_limitation(chain):
    """Pins current behaviour (documented limitation, not a regression gate): a pure hash chain with no external
    anchor cannot prove nothing was dropped from the *end*. If an anchor/length commitment is ever added this
    test should be updated to expect detection."""
    path, store, ids = chain
    _sql(path, "DELETE FROM audit_log WHERE id = ?", (ids[-1],))
    out = store.verify_chain()
    assert out["valid"] is True and out["entries_checked"] == 7


def test_tip_is_restored_from_db_so_chain_continues_after_reopen(crypto):
    d, path, store, ids = _fresh(crypto, n=3)
    try:
        store.close()
        s2 = cg.AuditStorage(path, crypto)
        new = s2.log_decision({"k": 1}, "ALLOW", "r", 1.0, 0.0, 0.0, [])
        rows = _rows(path)
        assert rows[-1]["id"] == new and rows[-1]["prev_hash"] == rows[-2]["entry_hash"]
        assert s2.verify_chain()["valid"]
        s2.close()
    finally:
        d.cleanup()


# ====================================================================================== intent hash / voucher


def test_intent_hash_and_cache_key_formula():
    intent = {"b": [1, 2, {"y": 1, "x": 2}], "a": "text", "c": None}
    expected = hashlib.sha256(json.dumps(intent, sort_keys=True).encode()).hexdigest()
    key = cg.CertifiedGovernanceEngine._cache_key(None, "alice", intent)
    assert key == f"intent:alice:{expected}"
    reordered = {"c": None, "a": "text", "b": [1, 2, {"x": 2, "y": 1}]}
    assert cg.CertifiedGovernanceEngine._cache_key(None, "alice", reordered) == key  # key-order invariant
    assert cg.CertifiedGovernanceEngine._cache_key(None, "bob", intent) != key  # user-scoped
    changed = {**intent, "a": "texu"}
    assert cg.CertifiedGovernanceEngine._cache_key(None, "alice", changed) != key  # value-sensitive
    # list order is semantically significant and must change the hash
    assert (cg.CertifiedGovernanceEngine._cache_key(None, "alice", {"l": [1, 2]})
            != cg.CertifiedGovernanceEngine._cache_key(None, "alice", {"l": [2, 1]}))


def test_voucher_is_bound_to_the_exact_intent_hash(crypto):
    conn = sqlite3.connect(":memory:")
    sec = cg.SecurityLayer({}, crypto, lambda: conn)
    intent = {"action": "pay", "amount": 10}
    ih = hashlib.sha256(json.dumps(intent, sort_keys=True).encode()).hexdigest()
    voucher = sec.issue_approval_voucher("entry-1", ih, "reviewer")
    assert sec.verify_approval_voucher(voucher, intent)["intent_hash"] == ih
    assert sec.verify_approval_voucher(voucher, {"amount": 10, "action": "pay"})["entry_id"] == "entry-1"
    with pytest.raises(ValueError):
        sec.verify_approval_voucher(voucher, {"action": "pay", "amount": 11})
    sec.revoke_token(sec.verify_approval_voucher(voucher, intent)["jti"])
    with pytest.raises(ValueError):  # revoked jti
        sec.verify_approval_voucher(voucher, intent)
    conn.close()


# ====================================================================================== audit_projection


def test_norm_p_hat_is_euclidean_norm():
    assert ap._norm_p_hat(None) == 0.0
    assert ap._norm_p_hat(-2.5) == 2.5
    assert ap._norm_p_hat([3, 4]) == 5.0
    assert ap._norm_p_hat((1, 2, 2)) == 3.0
    assert ap._norm_p_hat({"a": 6, "b": 8}) == 10.0
    assert ap._norm_p_hat("junk") == 0.0
    rng = np.random.default_rng(3)
    v = rng.normal(size=7)
    assert ap._norm_p_hat(list(v)) == pytest.approx(np.linalg.norm(v))


def test_projection_hand_values():
    assert ap.project_governance_score({}) == {"risk": 0.0, "stability": 1.0, "governance": 1.0}
    # cap .1 (<.25 => +2*(.15) = .3), risk .2, instability 1 => .3 (1-1/e)
    r = 0.2 + 0.3 + 0.3 * (1 - math.exp(-1))
    out = ap.project_governance_score({"hais": {"cap": 0.1, "risk": 0.2, "instability": 1.0}})
    assert out["risk"] == round(r, 3) == 0.69
    assert out["stability"] == round(math.exp(-2), 3) == 0.135
    assert out["governance"] == round(0.1 * (1 - 0.5 * r), 3) == 0.066
    # BLOCK + GOV_POLICY_BLOCK: risk floor .9; gov = 1*(1-.45)*.5 + .15
    out = ap.project_governance_score({"decision": "BLOCK", "error_code": "GOV_POLICY_BLOCK"})
    assert out == {"risk": 0.9, "stability": 1.0, "governance": round(0.55 * 0.5 + 0.15, 3)}
    # defensive realm + closed latch + |p_hat| = 5: stability = .4 * .6 * e^-5
    out = ap.project_governance_score({"haven2": {"realm": "defensive", "open": False, "p_hat": [3, 4]}})
    assert out["stability"] == round(0.4 * 0.6 * math.exp(-5), 3) == 0.002
    # calm realm: 1.1 factor, clipped to 1
    assert ap.project_governance_score({"haven2": {"realm": "calm"}})["stability"] == 1.0
    assert ap.project_governance_score({"decision": "REVIEW"})["risk"] == 0.65
    assert ap.project_governance_score({"decision": "REVIEW"})["governance"] == round(1 * (1 - 0.325) * 0.7, 3)
    assert ap.project_governance_score({"error_code": "GOV_AUTH_FAILED"})["risk"] == 0.7
    assert ap.project_governance_score({"error_code": "GOV_INTERNAL"})["risk"] == 0.8
    assert ap.project_governance_score({"error_code": "GOV_INTERNAL"})["governance"] == round(1 * (1 - 0.4) - 0.2, 3)


def test_projection_bounds_and_monotonicity_random():
    rng = np.random.default_rng(55)
    decisions = [None, "ALLOW", "REVIEW", "BLOCK"]
    codes = [None, "GOV_POLICY_BLOCK", "GOV_POLICY_REVIEW", "GOV_HAIS_CAP", "GOV_LATCH_CLOSED",
             "GOV_AUTH_FAILED", "GOV_INTERNAL"]
    for _ in range(500):
        env = {
            "decision": decisions[rng.integers(0, 4)], "error_code": codes[rng.integers(0, 7)],
            "hais": {"cap": float(rng.uniform(0, 1)), "risk": float(rng.uniform(0, 1)),
                     "instability": float(rng.uniform(0, 3))},
            "haven2": {"realm": ["normal", "calm", "defensive"][rng.integers(0, 3)], "open": bool(rng.integers(0, 2)),
                       "p_hat": [float(v) for v in rng.normal(size=3)]},
        }
        out = ap.project_governance_score(env)
        assert all(0.0 <= out[k] <= 1.0 for k in out)
        # raising instability can only raise risk and lower stability (ceteris paribus)
        env2 = json.loads(json.dumps(env))
        env2["hais"]["instability"] += 0.5
        o2 = ap.project_governance_score(env2)
        assert o2["risk"] >= out["risk"] and o2["stability"] <= out["stability"]
        # lowering cap can only raise risk
        env3 = json.loads(json.dumps(env))
        env3["hais"]["cap"] *= 0.5
        assert ap.project_governance_score(env3)["risk"] >= out["risk"]


# ====================================================================================== QUANTUM line


def test_encoding_constants_and_hand_example():
    assert ql.FIELD_WIDTH == 15 and ql.N_FIELDS == 10 and ql.LINE_LENGTH == 150
    assert "{:+015.8f}".format(0.5) == "+00000.50000000" and len("{:+015.8f}".format(0.5)) == 15
    st = {k: 0.0 for k in ql.FIELD_KEYS}
    st["x"], st["cap"] = 0.5, 0.25
    line = ql.encode_quantum_line(st)
    assert line[:15] == "+00000.50000000"
    assert line[60:75] == "+00000.25000000"
    assert line.count("+") == 10 and len(line) == 150
    assert ql.encode_quantum_line({**st, "x": -1.5})[:15] == "-00001.50000000"


def test_encode_decode_roundtrip_error_bound():
    rng = np.random.default_rng(12)
    for _ in range(500):
        st = {k: float(rng.uniform(-9999, 9999)) for k in ql.FIELD_KEYS}
        line = ql.encode_quantum_line(st)
        assert len(line) == 150
        dec = ql.decode_quantum_line(line)
        for k in ql.FIELD_KEYS:
            assert abs(dec[k] - st[k]) <= 5e-9 + 1e-12  # rounding to 8 decimals
        # idempotent after one quantisation
        assert ql.encode_quantum_line(dec) == line
    with pytest.raises(ValueError):
        ql.decode_quantum_line("x" * 150)
    with pytest.raises(ValueError):
        ql.decode_quantum_line("+00000.50000000" * 9)


def test_reconstruction_identities_match_independent_math():
    for x in np.linspace(0.0, 1.0, 11):
        S = 1.0 / (1.0 + math.exp(-4.0 * (x - 0.5)))
        tau = 1.0 + 0.5 * math.exp(S)
        rp = S * tau
        st = ql.build_quantum_state(x=float(x), S=S)
        assert st["tau"] == pytest.approx(tau, abs=1e-14)
        assert st["r_prime"] == pytest.approx(rp, abs=1e-14)
        assert st["cap"] == pytest.approx(math.exp(-2.2 * rp), abs=1e-14)
        assert st["I"] == pytest.approx(4 * S * (1 - S) * (1 + x), abs=1e-14)
        assert st["delta_r_prime"] == 0.0 and st["t_thresholds_available"] is False
    st = ql.build_quantum_state(x=0.5, S=0.5, prev_r_prime=0.1)
    assert st["delta_r_prime"] == pytest.approx(st["r_prime"] - 0.1)
    assert ql.build_quantum_state(T2=0.3)["t_thresholds_available"] is True
    assert ql.build_quantum_state(beta=0.0, S=0.5)["tau"] == 1.0  # beta -> tau = 1 + 0*e^S
    default = ql.build_quantum_state()  # S default .5, x 0
    assert default["I"] == pytest.approx(4 * 0.25 * 1.0)


def test_live_kernel_envelope_reproduces_quantum_fields():
    k = SovereignKernel()
    prev = 0.0
    for stress in np.linspace(0, 1, 9):
        out = k.evaluate_state(0.3, {"stress": float(stress), "anomaly": 0.1, "drift": 0.0})
        hais = {"risk": out["risk_metric"], "S": out["S"], "tau": out["tau"], "r_prime": out["r_prime"],
                "cap": out["capability_cap"], "instability": out["instability"]}
        st = ql.quantum_from_hais_envelope(hais, prev_r_prime=prev)
        assert st["delta_r_prime"] == pytest.approx(out["delta_r_prime"], abs=1e-14)
        # reconstruction from S alone agrees with what the kernel emitted
        assert ql.build_quantum_state(S=out["S"], x=out["risk_metric"])["cap"] == pytest.approx(
            out["capability_cap"], abs=1e-14)
        prev = out["r_prime"]
        line = ql.encode_quantum_line({k_: st[k_] for k_ in ql.FIELD_KEYS})
        assert ql.decode_quantum_line(line)["cap"] == pytest.approx(out["capability_cap"], abs=5e-9)


def test_attach_quantum_adds_structured_state_and_150_char_line():
    res = {"hais": {"risk": 0.2, "S": 0.3, "cap": 0.5}}
    ql.attach_quantum(res)
    assert len(res["quantum_line"]) == 150
    assert res["quantum"]["x"] == 0.2 and res["quantum"]["cap"] == 0.5
    assert ql.decode_quantum_line(res["quantum_line"])["S"] == pytest.approx(0.3)
