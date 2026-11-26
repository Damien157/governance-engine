"""Audit hash body v2: metadata and policy_reasons are hashed and signed.

Row format is versioned by ``audit_log.hash_schema``:
  * v1 (legacy, every row written before this change): body without
    metadata / policy_reasons. Existing DBs are migrated with
    ``ALTER TABLE ... ADD COLUMN hash_schema ... DEFAULT 1`` and still verify.
  * v2 (every new row): body adds ``hash_schema: 2`` and the exact stored
    canonical-JSON text of metadata and policy_reasons.

The verifier never infers the version from the row's content. The version is
bound twice: ``hash_schema: 2`` is inside the hashed + signed v2 body, and a
signed cutover record (``audit_meta.hash_schema_cutover`` = first v2 row id)
fixes which rows must be v1 and which must be v2. Stripping or downgrading
either must fail verification (tighten-only).
"""
from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import tempfile
import time
import uuid
from pathlib import Path

import pytest

import certified_governance_unified as cg

GENESIS = "0" * 64

LEGACY_SCHEMA = """
CREATE TABLE audit_log (
    id TEXT PRIMARY KEY, timestamp REAL, decision TEXT, result TEXT,
    verification_score REAL, risk_signal REAL, anomaly_signal REAL,
    policy_reasons TEXT, metadata TEXT, trace_id TEXT, intent_envelope TEXT,
    prev_hash TEXT, entry_hash TEXT, signature TEXT, engine_version TEXT,
    environment_id TEXT
)
"""


@pytest.fixture(scope="module")
def crypto():
    return cg.CryptoEngine(private_key_path=None)


@pytest.fixture()
def tmp():
    d = tempfile.TemporaryDirectory()
    yield Path(d.name)
    d.cleanup()


def _sql(path, stmt, args=()):
    c = sqlite3.connect(path)
    c.execute(stmt, args)
    c.commit()
    c.close()


def _rows(path):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    rows = c.execute("SELECT * FROM audit_log ORDER BY timestamp ASC, rowid ASC").fetchall()
    c.close()
    return rows


def _log(store, i, metadata=None, reasons=None):
    return store.log_decision(
        intent={"action": "act", "i": i},
        decision="ALLOW",
        result=f"r{i}",
        verification_score=0.9,
        risk_signal=0.1,
        anomaly_signal=0.0,
        policy_reasons=reasons if reasons is not None else [f"p{i}", "pii:email"],
        metadata=metadata if metadata is not None else {"z": i, "a": {"k": [1, 2]}, "link": "x"},
        enqueue_review=False,
    )


def _v2_store(tmp, crypto, n=6):
    path = str(tmp / "audit.db")
    store = cg.AuditStorage(path, crypto)
    ids = [_log(store, i) for i in range(n)]
    return path, store, ids


def _legacy_db(path, crypto, n=3):
    """A DB exactly as main@43712f9 wrote it: no hash_schema column, v1 body."""
    c = sqlite3.connect(path)
    c.execute(LEGACY_SCHEMA)
    prev, ts, ids = GENESIS, time.time() - 100, []
    for i in range(n):
        eid = str(uuid.uuid4())
        envelope = {"data": base64.b64encode(json.dumps({"legacy": i}).encode()).decode()}
        body = json.dumps(
            {"id": eid, "ts": ts + i, "decision": "ALLOW", "result": f"old{i}", "envelope": envelope,
             "prev_hash": prev, "engine_version": "CertifiedGovernanceEngine v1.0", "environment_id": "default"},
            sort_keys=True,
        ).encode()
        h = hashlib.sha256(body).hexdigest()
        c.execute(
            "INSERT INTO audit_log VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (eid, ts + i, "ALLOW", f"old{i}", 0.9, 0.1, 0.0, json.dumps(["legacy"]), json.dumps({"m": i}), "",
             json.dumps(envelope), prev, h, base64.b64encode(crypto.sign(body)).decode(),
             "CertifiedGovernanceEngine v1.0", "default"),
        )
        prev = h
        ids.append(eid)
    c.commit()
    c.close()
    return ids


def _v2_body(row, **override):
    d = {
        "id": row["id"], "ts": row["timestamp"], "decision": row["decision"], "result": row["result"],
        "envelope": json.loads(row["intent_envelope"]), "prev_hash": row["prev_hash"],
        "engine_version": row["engine_version"], "environment_id": row["environment_id"],
        "hash_schema": 2, "metadata": row["metadata"], "policy_reasons": row["policy_reasons"],
    }
    d.update(override)
    return json.dumps(d, sort_keys=True, separators=(",", ":")).encode()


# ------------------------------------------------------------------ v2 binding


def test_new_rows_are_v2_and_hash_covers_metadata_and_reasons(tmp, crypto):
    path, store, ids = _v2_store(tmp, crypto)
    rows = _rows(path)
    for r in rows:
        assert r["hash_schema"] == 2
        assert hashlib.sha256(_v2_body(r)).hexdigest() == r["entry_hash"]
        assert crypto.verify(_v2_body(r), base64.b64decode(r["signature"]))
    # stored as canonical JSON (sorted keys, no whitespace)
    assert rows[0]["metadata"] == '{"a":{"k":[1,2]},"link":"x","z":0}'
    assert rows[0]["policy_reasons"] == '["p0","pii:email"]'
    assert store.verify_chain()["valid"] is True


@pytest.mark.parametrize(
    "column,new",
    [
        ("metadata", '{"a":{"k":[1,2]},"link":"EVIL","z":3}'),
        ("metadata", "{}"),
        ("metadata", '{"z": 3, "a": {"k": [1, 2]}, "link": "x"}'),  # same meaning, other bytes
        ("policy_reasons", '["p3"]'),
        ("policy_reasons", "[]"),
    ],
)
def test_editing_metadata_or_reasons_breaks_that_row(tmp, crypto, column, new):
    path, store, ids = _v2_store(tmp, crypto)
    _sql(path, f"UPDATE audit_log SET {column} = ? WHERE id = ?", (new, ids[3]))
    out = store.verify_chain()
    assert out["valid"] is False
    assert out["broken_entries"] == [ids[3]]


def test_rehashing_an_edited_metadata_row_without_key_still_fails(tmp, crypto):
    path, store, ids = _v2_store(tmp, crypto)
    row = [r for r in _rows(path) if r["id"] == ids[2]][0]
    forged = _v2_body(row, metadata='{"forged":true}')
    _sql(path, "UPDATE audit_log SET metadata = ?, entry_hash = ? WHERE id = ?",
         ('{"forged":true}', hashlib.sha256(forged).hexdigest(), ids[2]))
    assert store.verify_chain()["broken_entries"] == [ids[2], ids[3]]


# ------------------------------------------------- stripping / downgrading v2


@pytest.mark.parametrize(
    "stmt",
    [
        "UPDATE audit_log SET hash_schema = 1 WHERE id = ?",
        "UPDATE audit_log SET metadata = NULL WHERE id = ?",
        "UPDATE audit_log SET policy_reasons = NULL WHERE id = ?",
        "UPDATE audit_log SET metadata = NULL, policy_reasons = NULL WHERE id = ?",
        "UPDATE audit_log SET metadata = NULL, policy_reasons = NULL, hash_schema = 1 WHERE id = ?",
        "UPDATE audit_log SET metadata = '{}', policy_reasons = '[]', hash_schema = 1 WHERE id = ?",
    ],
)
def test_stripping_or_downgrading_a_v2_row_fails(tmp, crypto, stmt):
    path, store, ids = _v2_store(tmp, crypto)
    _sql(path, stmt, (ids[4],))
    out = store.verify_chain()
    assert out["valid"] is False
    assert ids[4] in out["broken_entries"]


def test_downgrade_with_v1_rehash_still_fails(tmp, crypto):
    """Attacker sets hash_schema=1 and rewrites entry_hash to the v1 body hash: the RSA-PSS
    signature (over the v2 body) and the cutover boundary (row must be v2) both reject it."""
    path, store, ids = _v2_store(tmp, crypto)
    r = [r for r in _rows(path) if r["id"] == ids[1]][0]
    v1 = json.dumps({"id": r["id"], "ts": r["timestamp"], "decision": r["decision"], "result": r["result"],
                     "envelope": json.loads(r["intent_envelope"]), "prev_hash": r["prev_hash"],
                     "engine_version": r["engine_version"], "environment_id": r["environment_id"]},
                    sort_keys=True).encode()
    _sql(path, "UPDATE audit_log SET hash_schema = 1, metadata = NULL, policy_reasons = NULL, entry_hash = ? "
               "WHERE id = ?", (hashlib.sha256(v1).hexdigest(), ids[1]))
    out = store.verify_chain()
    assert out["valid"] is False and ids[1] in out["broken_entries"]


@pytest.mark.parametrize("bad", [99, 0, 3, -1, None, "two", "x", 2.5])  # ("2" is coerced to 2 by INTEGER affinity: not a tamper)
def test_unknown_hash_schema_fails_closed(tmp, crypto, bad):
    path, store, ids = _v2_store(tmp, crypto)
    c = sqlite3.connect(path)
    # bypass the NOT NULL constraint for the NULL case by rebuilding without it
    if bad is None:
        c.execute("PRAGMA writable_schema=ON")
        c.execute("UPDATE sqlite_master SET sql = replace(sql, 'hash_schema INTEGER NOT NULL DEFAULT 1', "
                  "'hash_schema INTEGER DEFAULT 1') WHERE name = 'audit_log'")
        c.execute("PRAGMA writable_schema=OFF")
        c.commit()
        c.close()
        c = sqlite3.connect(path)
    c.execute("UPDATE audit_log SET hash_schema = ? WHERE id = ?", (bad, ids[2]))
    c.commit()
    c.close()
    store.close()
    s2 = cg.AuditStorage(path, crypto)
    out = s2.verify_chain()
    assert out["valid"] is False and ids[2] in out["broken_entries"]
    s2.close()


def test_hash_body_rejects_unknown_and_bool_schema():
    row = {k: "x" for k in ("id", "ts", "decision", "result", "envelope", "prev_hash", "engine_version",
                            "environment_id", "metadata", "policy_reasons")}
    for bad in (None, 0, 3, True, "1", 1.0):
        with pytest.raises(ValueError):
            cg._audit_hash_body(row, bad)
    with pytest.raises(ValueError):
        cg._audit_hash_body({**row, "metadata": None}, 2)
    assert b'"hash_schema":2' in cg._audit_hash_body(row, 2)
    assert b"hash_schema" not in cg._audit_hash_body(row, 1)


# ------------------------------------------------------- legacy + mixed chain


def test_legacy_v1_db_migrates_and_still_verifies(tmp, crypto):
    path = str(tmp / "audit.db")
    legacy = _legacy_db(path, crypto)
    store = cg.AuditStorage(path, crypto)
    assert [r["hash_schema"] for r in _rows(path)] == [1, 1, 1]
    out = store.verify_chain()
    assert out["valid"] is True and out["entries_checked"] == 3
    assert store._read_cutover() == ("none", None)
    # legacy rows remain unsigned for metadata (documented residual)
    _sql(path, "UPDATE audit_log SET metadata = '{\"m\":99}' WHERE id = ?", (legacy[1],))
    assert store.verify_chain()["valid"] is True
    store.close()


def test_mixed_v1_then_v2_chain_verifies_and_boundary_is_recorded(tmp, crypto):
    path = str(tmp / "audit.db")
    legacy = _legacy_db(path, crypto)
    store = cg.AuditStorage(path, crypto)
    new = [_log(store, i) for i in range(3)]
    rows = _rows(path)
    assert [r["hash_schema"] for r in rows] == [1, 1, 1, 2, 2, 2]
    assert rows[3]["prev_hash"] == rows[2]["entry_hash"]  # v2 links onto the v1 tip
    assert store._read_cutover() == ("ok", new[0])
    out = store.verify_chain()
    assert out["valid"] is True and out["entries_checked"] == 6 and out["anchor_ok"] is True
    # reopen: migration is idempotent, cutover is not rewritten
    store.close()
    s2 = cg.AuditStorage(path, crypto)
    _log(s2, 9)
    assert s2._read_cutover() == ("ok", new[0])
    assert s2.verify_chain()["valid"] is True
    # a legacy row before the boundary claiming v2 fails; a v2 row after it claiming v1 fails
    _sql(path, "UPDATE audit_log SET hash_schema = 2 WHERE id = ?", (legacy[0],))
    assert legacy[0] in s2.verify_chain()["broken_entries"]
    _sql(path, "UPDATE audit_log SET hash_schema = 1 WHERE id = ?", (legacy[0],))
    _sql(path, "UPDATE audit_log SET hash_schema = 1 WHERE id = ?", (new[2],))
    out = s2.verify_chain()
    assert out["valid"] is False and out["broken_entries"] == [new[2]]
    s2.close()


# ----------------------------------------------------------- cutover record


def test_cutover_record_deleted_fails(tmp, crypto):
    path = str(tmp / "audit.db")
    _legacy_db(path, crypto)
    store = cg.AuditStorage(path, crypto)
    new = [_log(store, i) for i in range(3)]
    _sql(path, "DELETE FROM audit_meta WHERE key = 'hash_schema_cutover'")
    out = store.verify_chain()
    assert out["valid"] is False and out["broken_entries"] == [new[0]]


def test_cutover_record_moved_or_forged_fails(tmp, crypto):
    path = str(tmp / "audit.db")
    _legacy_db(path, crypto)
    store = cg.AuditStorage(path, crypto)
    new = [_log(store, i) for i in range(3)]
    # move the boundary later (would let new[0] be called v1): signature no longer matches
    _sql(path, "UPDATE audit_meta SET value = ? WHERE key = 'hash_schema_cutover'",
         (json.dumps({"first_v2_id": new[2]}),))
    assert store.verify_chain()["valid"] is False
    assert store._read_cutover() == ("bad", None)
    _sql(path, "UPDATE audit_meta SET value = ?, signature = ? WHERE key = 'hash_schema_cutover'",
         (json.dumps({"first_v2_id": new[0]}), base64.b64encode(b"\x00" * 256).decode()))
    assert store.verify_chain()["valid"] is False


def test_all_v2_stripped_and_cutover_deleted_still_fails(tmp, crypto):
    path, store, ids = _v2_store(tmp, crypto, n=3)
    _sql(path, "DELETE FROM audit_meta")
    _sql(path, "UPDATE audit_log SET hash_schema = 1, metadata = NULL, policy_reasons = NULL")
    out = store.verify_chain()
    assert out["valid"] is False and out["broken_entries"] == ids  # v1 recompute != signed v2 hash


def test_deleting_the_cutover_row_fails(tmp, crypto):
    path = str(tmp / "audit.db")
    _legacy_db(path, crypto)
    store = cg.AuditStorage(path, crypto)
    new = [_log(store, i) for i in range(3)]
    _sql(path, "DELETE FROM audit_log WHERE id = ?", (new[0],))
    out = store.verify_chain()
    assert out["valid"] is False and new[1] in out["broken_entries"]


# ------------------------------------------------------- truncation anchor


def test_truncation_anchor_still_guards_v2_chain(tmp, crypto):
    path, store, ids = _v2_store(tmp, crypto)
    assert store.verify_chain()["anchor_reason"] == "anchor:ok"
    _sql(path, "DELETE FROM audit_log WHERE id = ?", (ids[-1],))
    out = store.verify_chain()
    assert out["broken_entries"] == [] and out["anchor_ok"] is False and out["valid"] is False


def test_anchor_head_hash_covers_metadata(tmp, crypto):
    """The anchor signs head_hash = entry_hash of the tip, which for v2 already covers
    metadata/policy_reasons, so the anchor format needs no change."""
    path, store, ids = _v2_store(tmp, crypto, n=2)
    anchor = json.loads(Path(path + ".anchor").read_text())
    tip = _rows(path)[-1]
    assert anchor["head_hash"] == tip["entry_hash"] == hashlib.sha256(_v2_body(tip)).hexdigest()


def test_legacy_db_bootstraps_anchor_then_appends_v2(tmp, crypto):
    path = str(tmp / "audit.db")
    _legacy_db(path, crypto)
    store = cg.AuditStorage(path, crypto)
    assert Path(path + ".anchor").exists()
    _log(store, 0)
    assert store.verify_chain()["anchor_reason"] == "anchor:ok"
    store.close()


# =====================================================================
# Review follow-ups (Damien, Nov 26): items 1-6
# =====================================================================


def _v1_body(row, **override):
    d = {
        "id": row["id"], "ts": row["timestamp"], "decision": row["decision"], "result": row["result"],
        "envelope": json.loads(row["intent_envelope"]), "prev_hash": row["prev_hash"],
        "engine_version": row["engine_version"], "environment_id": row["environment_id"],
    }
    d.update(override)
    return json.dumps(d, sort_keys=True).encode()


def _row(path, eid):
    return [r for r in _rows(path) if r["id"] == eid][0]


# ---- item 1: downgrade protection rests on the RSA-PSS signature + chain link


def test_flip_only_hash_schema_column_to_1_fails(tmp, crypto):
    """Only the hash_schema column changes (entry_hash, signature, metadata untouched)."""
    path, store, ids = _v2_store(tmp, crypto)
    before = _row(path, ids[3])
    _sql(path, "UPDATE audit_log SET hash_schema = 1 WHERE id = ?", (ids[3],))
    after = _row(path, ids[3])
    assert after["hash_schema"] == 1
    for k in ("entry_hash", "signature", "metadata", "policy_reasons", "prev_hash"):
        assert after[k] == before[k]
    # Under v1 the recomputed hash differs from the stored (signed) v2 hash,
    # and the stored signature does not verify over the v1 body.
    assert hashlib.sha256(_v1_body(after)).hexdigest() != after["entry_hash"]
    assert crypto.verify(_v1_body(after), base64.b64decode(after["signature"])) is False
    out = store.verify_chain()
    assert out["valid"] is False
    assert out["broken_entries"] == [ids[3]]  # stored entry_hash unchanged, so ids[4] still links


def test_flip_schema_and_rewrite_entry_hash_to_v1_hash_fails_on_signature(tmp, crypto):
    """Attacker without the key: hash_schema=1 and entry_hash = sha256(v1 body), metadata kept.
    The hash now matches, so it is the RSA-PSS signature (made over the v2 body) that rejects
    the row; the next row's prev_hash link (to the old v2 hash) breaks as well."""
    path, store, ids = _v2_store(tmp, crypto)
    r = _row(path, ids[2])
    v1_hash = hashlib.sha256(_v1_body(r)).hexdigest()
    _sql(path, "UPDATE audit_log SET hash_schema = 1, entry_hash = ? WHERE id = ?", (v1_hash, ids[2]))
    r2 = _row(path, ids[2])
    assert hashlib.sha256(_v1_body(r2)).hexdigest() == r2["entry_hash"]  # hash check would pass
    assert crypto.verify(_v1_body(r2), base64.b64decode(r2["signature"])) is False  # signature fails
    out = store.verify_chain()
    assert out["valid"] is False
    assert out["broken_entries"] == [ids[2], ids[3]]  # row + its successor's prev_hash link


def test_prev_hash_link_is_checked(tmp, crypto):
    path, store, ids = _v2_store(tmp, crypto)
    _sql(path, "UPDATE audit_log SET prev_hash = ? WHERE id = ?", ("f" * 64, ids[4]))
    assert store.verify_chain()["broken_entries"] == [ids[4]]


# ---- item 2: positional boundary rejects any v1 row after the cutover


def _append_v1_row(path, crypto, *, sign_with_key: bool, ts_offset: float = 1.0):
    """Append a v1-format row at the tip with a correct v1 hash and a correct prev_hash
    link to the current tip. ``sign_with_key=False`` = attacker without the key
    (reuses the tip's signature); True = an old-format writer that holds the key."""
    tip = _rows(path)[-1]
    eid = str(uuid.uuid4())
    envelope = {"data": base64.b64encode(json.dumps({"forged": True}).encode()).decode()}
    ts = tip["timestamp"] + ts_offset
    body = json.dumps(
        {"id": eid, "ts": ts, "decision": "ALLOW", "result": "executed", "envelope": envelope,
         "prev_hash": tip["entry_hash"], "engine_version": "CertifiedGovernanceEngine v1.0",
         "environment_id": "default"},
        sort_keys=True,
    ).encode()
    sig = crypto.sign(body) if sign_with_key else base64.b64decode(tip["signature"])
    _sql(path,
         "INSERT INTO audit_log (id, timestamp, decision, result, verification_score, risk_signal,"
         " anomaly_signal, policy_reasons, metadata, trace_id, intent_envelope, prev_hash, entry_hash,"
         " signature, engine_version, environment_id, hash_schema)"
         " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
         (eid, ts, "ALLOW", "executed", 0.9, 0.1, 0.0, "[]", "{}", "", json.dumps(envelope),
          tip["entry_hash"], hashlib.sha256(body).hexdigest(), base64.b64encode(sig).decode(),
          "CertifiedGovernanceEngine v1.0", "default"))
    return eid, body, sig


def test_v1_row_appended_after_cutover_without_key_fails(tmp, crypto):
    path, store, ids = _v2_store(tmp, crypto, n=3)
    eid, body, sig = _append_v1_row(path, crypto, sign_with_key=False)
    r = _row(path, eid)
    assert r["prev_hash"] == _row(path, ids[-1])["entry_hash"]           # correct chain link
    assert hashlib.sha256(body).hexdigest() == r["entry_hash"]           # correct v1 hash
    out = store.verify_chain()
    assert out["valid"] is False and out["broken_entries"] == [eid]


def test_validly_signed_v1_row_after_cutover_is_rejected_by_boundary_alone(tmp, crypto):
    """Hash, chain link AND signature are all valid (an old-format writer holding the key):
    the only thing that rejects the row is the signed positional cutover boundary."""
    path, store, ids = _v2_store(tmp, crypto, n=3)
    eid, body, sig = _append_v1_row(path, crypto, sign_with_key=True)
    r = _row(path, eid)
    assert hashlib.sha256(body).hexdigest() == r["entry_hash"]
    assert r["prev_hash"] == _row(path, ids[-1])["entry_hash"]
    assert crypto.verify(body, sig) is True
    out = store.verify_chain()
    assert out["valid"] is False and out["broken_entries"] == [eid]


def test_validly_signed_v1_row_after_v2_with_cutover_record_deleted_still_fails(tmp, crypto):
    """No usable cutover record while v2 rows exist: the boundary falls back to the first
    v2 row AND that row is marked broken, so the chain still cannot verify."""
    path, store, ids = _v2_store(tmp, crypto, n=3)
    eid, _, _ = _append_v1_row(path, crypto, sign_with_key=True)
    _sql(path, "DELETE FROM audit_meta")
    out = store.verify_chain()
    assert out["valid"] is False
    assert eid in out["broken_entries"] and ids[0] in out["broken_entries"]


def test_v1_row_before_cutover_relabelled_after_it_by_timestamp_fails(tmp, crypto):
    """Mixed chain: move a legacy v1 row's timestamp past the cutover (relabel position)."""
    path = str(tmp / "audit.db")
    legacy = _legacy_db(path, crypto)
    store = cg.AuditStorage(path, crypto)
    new = [_log(store, i) for i in range(2)]
    _sql(path, "UPDATE audit_log SET timestamp = ? WHERE id = ?",
         (_rows(path)[-1]["timestamp"] + 5, legacy[2]))
    out = store.verify_chain()
    assert out["valid"] is False and legacy[2] in out["broken_entries"]


# ---- item 3: NaN / Infinity fail loudly, and govern fails closed to BLOCK


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_c14n_rejects_non_finite_floats(bad):
    with pytest.raises(ValueError):
        cg._audit_c14n({"x": bad})
    with pytest.raises(ValueError):
        cg._audit_c14n([bad])


@pytest.mark.parametrize(
    "metadata,reasons",
    [({"score": float("nan")}, ["ok"]), ({"ok": 1}, [float("inf")]), ({"deep": {"x": [float("-inf")]}}, [])],
)
def test_log_decision_with_non_finite_value_raises_and_writes_nothing(tmp, crypto, metadata, reasons):
    path, store, ids = _v2_store(tmp, crypto, n=2)
    head = (store._last_hash, store._seq)
    with pytest.raises(ValueError):
        _log(store, 9, metadata=metadata, reasons=reasons)
    assert len(_rows(path)) == 2 and (store._last_hash, store._seq) == head
    assert all("NaN" not in r["metadata"] and "Infinity" not in r["metadata"] for r in _rows(path))
    # the writer state is intact: the next good write links and verifies
    _log(store, 10)
    out = store.verify_chain()
    assert out["valid"] is True and out["entries_checked"] == 3


def _nan_injecting(storage):
    real = storage.log_decision

    def wrapper(**kw):
        kw["metadata"] = {**(kw.get("metadata") or {}), "flow_utilization": float("nan")}
        return real(**kw)

    return wrapper


def test_engine_fails_closed_to_block_when_metadata_has_nan(tmp, crypto):
    import asyncio

    (tmp / "k").mkdir()
    eng = cg.CertifiedGovernanceEngine(
        {"db_path": str(tmp / "k" / "audit.db"), "log_level": 50}, crypto=crypto)
    token = eng.security.generate_token("tester", "operator")
    ok = asyncio.run(eng.execute_governed_action({"action": "ping", "n": 0}, token))["result"]
    assert ok["decision"] == "ALLOW" and ok["logged"] is True
    eng.storage.log_decision = _nan_injecting(eng.storage)
    res = asyncio.run(eng.execute_governed_action({"action": "ping", "n": 1}, token))["result"]
    assert res["decision"] == "BLOCK"
    assert res["logged"] is False and res["entry_id"] is None and res["status"] == "degraded"
    assert "audit_log_unavailable" in res["policy_reasons"]
    assert eng.storage.verify_chain()["valid"] is True  # nothing half-written
    eng.storage.close()


def test_governed_stack_govern_blocks_not_raises_when_metadata_has_nan(tmp, crypto):
    import asyncio

    from governed_stack.stack import GovernedStack

    stack = GovernedStack(
        config={"db_path": str(tmp / "s.db"), "signing_key_path": str(tmp / "s.pem"), "log_level": 50},
        crypto=crypto,
    )
    token = stack.issue_token("tester", "operator")
    stack.engine.storage.log_decision = _nan_injecting(stack.engine.storage)
    env = asyncio.run(stack.govern({"action": "ping"}, token))
    assert env["decision"] == "BLOCK", env
    assert any("audit_log_unavailable" in str(x) for x in env.get("reasons", [])), env
    stack.engine.storage.close()


# ---- item 4: verifier reports every bad row and keeps going


def test_verifier_reports_two_separately_broken_rows_and_keeps_checking(tmp, crypto):
    path, store, ids = _v2_store(tmp, crypto, n=8)
    _sql(path, "UPDATE audit_log SET hash_schema = 99 WHERE id = ?", (ids[1],))  # ValueError path
    _sql(path, "UPDATE audit_log SET metadata = NULL WHERE id = ?", (ids[5],))  # ValueError path
    out = store.verify_chain()
    assert out["valid"] is False
    assert out["broken_entries"] == [ids[1], ids[5]]  # both reported; 0,2,3,4,6,7 checked and fine
    assert out["entries_checked"] == 8
    assert out["integrity_pressure"] == pytest.approx(2 / 8)


@pytest.mark.parametrize("exc", [ValueError, KeyError, TypeError])
def test_verifier_catches_value_key_and_type_errors_per_row(tmp, crypto, monkeypatch, exc):
    path, store, ids = _v2_store(tmp, crypto, n=5)
    real = cg._audit_hash_body
    bad = {ids[1], ids[3]}

    def flaky(row, schema):
        if row["id"] in bad:
            raise exc("boom")
        return real(row, schema)

    monkeypatch.setattr(cg, "_audit_hash_body", flaky)
    out = store.verify_chain()
    assert out["valid"] is False and out["broken_entries"] == [ids[1], ids[3]]
    assert out["entries_checked"] == 5


# ---- item 5: the v1 default is a one-time backfill; NULL never means v1


def test_migration_backfills_only_existing_rows_and_new_rows_are_v2(tmp, crypto):
    path = str(tmp / "audit.db")
    _legacy_db(path, crypto)
    store = cg.AuditStorage(path, crypto)
    c = sqlite3.connect(path)
    col = [r for r in c.execute("PRAGMA table_info(audit_log)") if r[1] == "hash_schema"][0]
    c.close()
    assert col[3] == 1 and col[4] == "1"  # NOT NULL, DEFAULT 1 (backfill for pre-change rows)
    _log(store, 0)
    assert [r["hash_schema"] for r in _rows(path)] == [1, 1, 1, 2]  # new row written explicitly as 2
    store.close()


def _drop_not_null(path):
    c = sqlite3.connect(path)
    c.execute("PRAGMA writable_schema=ON")
    c.execute("UPDATE sqlite_master SET sql = replace(sql, 'NOT NULL DEFAULT 1', 'DEFAULT 1') "
              "WHERE name = 'audit_log'")
    c.execute("PRAGMA writable_schema=OFF")
    c.commit()
    c.close()


def test_null_hash_schema_on_a_pre_cutover_legacy_row_fails_closed(tmp, crypto):
    """NULL is not read as v1, even where v1 is the expected version."""
    path = str(tmp / "audit.db")
    legacy = _legacy_db(path, crypto)
    store = cg.AuditStorage(path, crypto)
    store.close()
    _drop_not_null(path)
    _sql(path, "UPDATE audit_log SET hash_schema = NULL WHERE id = ?", (legacy[1],))
    s2 = cg.AuditStorage(path, crypto)
    out = s2.verify_chain()
    assert out["valid"] is False and out["broken_entries"] == [legacy[1]]
    s2.close()


def test_row_inserted_without_hash_schema_after_cutover_gets_1_and_fails(tmp, crypto):
    """A writer that omits the column gets DEFAULT 1, which the boundary rejects after cutover."""
    path, store, ids = _v2_store(tmp, crypto, n=2)
    eid, _, _ = _append_v1_row(path, crypto, sign_with_key=True)
    assert _row(path, eid)["hash_schema"] == 1
    assert store.verify_chain()["broken_entries"] == [eid]


# ---- item 6: known-answer vectors and None / {} / [] behaviour

KAT_ROW = {
    "id": "00000000-0000-4000-8000-000000000001",
    "ts": 1700000000.5,
    "decision": "ALLOW",
    "result": "executed",
    "envelope": {"data": "eyJhY3Rpb24iOiAicGluZyJ9"},
    "prev_hash": "0" * 64,
    "engine_version": "CertifiedGovernanceEngine v1.0",
    "environment_id": "default",
    "metadata": '{"role":"operator","user":"u1"}',
    "policy_reasons": '["pii:email"]',
}

KAT_V1_BYTES = (
    b'{"decision": "ALLOW", "engine_version": "CertifiedGovernanceEngine v1.0", '
    b'"envelope": {"data": "eyJhY3Rpb24iOiAicGluZyJ9"}, "environment_id": "default", '
    b'"id": "00000000-0000-4000-8000-000000000001", '
    b'"prev_hash": "0000000000000000000000000000000000000000000000000000000000000000", '
    b'"result": "executed", "ts": 1700000000.5}'
)
KAT_V1_SHA256 = "6e67ab84e8c19cf54ee8ff44ddffe26d901dea6df50f87b8b27463c0df004f7a"

KAT_V2_BYTES = (
    b'{"decision":"ALLOW","engine_version":"CertifiedGovernanceEngine v1.0",'
    b'"envelope":{"data":"eyJhY3Rpb24iOiAicGluZyJ9"},"environment_id":"default","hash_schema":2,'
    b'"id":"00000000-0000-4000-8000-000000000001",'
    b'"metadata":"{\\"role\\":\\"operator\\",\\"user\\":\\"u1\\"}",'
    b'"policy_reasons":"[\\"pii:email\\"]",'
    b'"prev_hash":"0000000000000000000000000000000000000000000000000000000000000000",'
    b'"result":"executed","ts":1700000000.5}'
)
KAT_V2_SHA256 = "5f9bace1a17f91d23234c68db14508c0e1888fb037ac70414859295c42f19d0a"


def test_known_answer_v1_body_bytes_and_sha256():
    body = cg._audit_hash_body(KAT_ROW, 1)
    assert body == KAT_V1_BYTES
    assert hashlib.sha256(body).hexdigest() == KAT_V1_SHA256
    # v1 ignores metadata / policy_reasons entirely (the legacy residual)
    assert cg._audit_hash_body({**KAT_ROW, "metadata": None, "policy_reasons": "x"}, 1) == KAT_V1_BYTES


def test_known_answer_v2_body_bytes_and_sha256():
    body = cg._audit_hash_body(KAT_ROW, 2)
    assert body == KAT_V2_BYTES
    assert hashlib.sha256(body).hexdigest() == KAT_V2_SHA256


@pytest.mark.parametrize(
    "field,value",
    [("metadata", '{"role":"admin","user":"u1"}'), ("metadata", '{"user":"u1","role":"operator"}'),
     ("policy_reasons", "[]"), ("policy_reasons", '["pii:email","x"]')],
)
def test_known_answer_v2_changes_when_metadata_or_reasons_change(field, value):
    assert hashlib.sha256(cg._audit_hash_body({**KAT_ROW, field: value}, 2)).hexdigest() != KAT_V2_SHA256


@pytest.mark.parametrize("field", ["metadata", "policy_reasons"])
def test_known_answer_v2_stripped_field_raises(field):
    with pytest.raises(ValueError):
        cg._audit_hash_body({**KAT_ROW, field: None}, 2)
    stripped = {k: v for k, v in KAT_ROW.items() if k != field}
    with pytest.raises(KeyError):  # caught per row by verify_chain (item 4)
        cg._audit_hash_body(stripped, 2)


@pytest.mark.parametrize("schema", [True, False, "2", 2.0, None])
def test_known_answer_true_and_other_non_int_schemas_raise(schema):
    with pytest.raises(ValueError):
        cg._audit_hash_body(KAT_ROW, schema)


def test_none_and_empty_metadata_reasons_store_canonical_empty_and_verify(tmp, crypto):
    """Intent: None and {} / [] are the same thing on write -- both stored as '{}' / '[]' and
    both signed that way. At verify time a NULL column on a v2 row is NOT treated as empty:
    it fails (covered by test_stripping_or_downgrading_a_v2_row_fails)."""
    path = str(tmp / "audit.db")
    store = cg.AuditStorage(path, crypto)
    a = store.log_decision(intent={"a": 1}, decision="ALLOW", result="executed", verification_score=0.9,
                           risk_signal=0.1, anomaly_signal=0.0, policy_reasons=None, metadata=None,
                           enqueue_review=False)
    b = store.log_decision(intent={"a": 2}, decision="ALLOW", result="executed", verification_score=0.9,
                           risk_signal=0.1, anomaly_signal=0.0, policy_reasons=[], metadata={},
                           enqueue_review=False)
    ra, rb = _row(path, a), _row(path, b)
    assert (ra["metadata"], ra["policy_reasons"]) == ("{}", "[]") == (rb["metadata"], rb["policy_reasons"])
    assert store.verify_chain()["valid"] is True
    _sql(path, "UPDATE audit_log SET metadata = NULL WHERE id = ?", (a,))
    assert store.verify_chain()["broken_entries"] == [a]
    store.close()


def test_old_v1_row_still_verifies_byte_for_byte(tmp, crypto):
    """A row written by main@43712f9 (v1 body, no hash_schema column) verifies unchanged after
    migration, and its stored entry_hash equals the v1 body hash."""
    path = str(tmp / "audit.db")
    legacy = _legacy_db(path, crypto, n=2)
    store = cg.AuditStorage(path, crypto)
    for r in _rows(path):
        assert r["hash_schema"] == 1
        assert hashlib.sha256(cg._audit_hash_body(
            {**dict(r), "ts": r["timestamp"], "envelope": json.loads(r["intent_envelope"])}, 1)
        ).hexdigest() == r["entry_hash"]
    out = store.verify_chain()
    assert out["valid"] is True and out["entries_checked"] == 2 and out["broken_entries"] == []
    store.close()
