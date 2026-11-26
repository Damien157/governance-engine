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
