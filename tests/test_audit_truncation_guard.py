"""Audit-log truncation guard: a signed head anchor {seq, head_hash} kept next to
the per-key audit DB. Tail truncation, DB deletion, rollback to an older copy and a
tampered anchor must all fail closed to BLOCK (never REVIEW), and an approval voucher
must not reopen that BLOCK."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

import pytest

import certified_governance_unified as cg


@pytest.fixture(scope="module")
def crypto():
    return cg.CryptoEngine(private_key_path=None)


@pytest.fixture()
def tmp():
    d = tempfile.TemporaryDirectory()
    yield Path(d.name)
    d.cleanup()


def _engine(tmp: Path, crypto) -> cg.CertifiedGovernanceEngine:
    cfg = {
        "db_path": str(tmp / "key_a" / "audit.db"),
        "risk_block_threshold": 0.99,
        "risk_review_threshold": 0.50,
        "cache_ttl_seconds": 300,
        "log_level": 50,
    }
    (tmp / "key_a").mkdir(exist_ok=True)
    return cg.CertifiedGovernanceEngine(cfg, crypto=crypto)


def _run(eng, intent, token, **kw):
    return asyncio.run(eng.execute_governed_action(intent, token, **kw))["result"]


def _decide(eng, n_prior: int = 4):
    token = eng.security.generate_token("tester", "operator")
    for i in range(n_prior):
        assert _run(eng, {"action": "ping", "n": i}, token)["decision"] == "ALLOW"
    return token


def _reopen(eng, tmp, crypto):
    eng.storage.close()
    return _engine(tmp, crypto)


def _sql(path, q, args=()):
    c = sqlite3.connect(path)
    c.execute(q, args)
    c.commit()
    c.close()


def _assert_truncation_block(res):
    assert res["decision"] == "BLOCK"
    assert any(r.startswith("audit_truncation:") for r in res["policy_reasons"]), res


def test_healthy_log_allows_and_anchor_tracks_head(tmp, crypto):
    eng = _engine(tmp, crypto)
    token = _decide(eng)
    st = eng.storage.verify_chain()
    assert st["valid"] and st["anchor_ok"] and st["anchor_reason"] == "anchor:ok"
    doc = json.loads(Path(eng.storage.anchor_path).read_text())
    assert doc["seq"] == st["entries_checked"] == 4
    # Reopen (restart) keeps working.
    eng = _reopen(eng, tmp, crypto)
    assert _run(eng, {"action": "ping", "n": 99}, token)["decision"] == "ALLOW"


def test_legacy_db_without_anchor_is_bootstrapped(tmp, crypto):
    eng = _engine(tmp, crypto)
    _decide(eng, 3)
    os.remove(eng.storage.anchor_path)
    eng = _reopen(eng, tmp, crypto)
    assert os.path.exists(eng.storage.anchor_path)
    assert eng.storage.verify_chain()["anchor_ok"]


def test_truncated_tail_blocks(tmp, crypto):
    eng = _engine(tmp, crypto)
    token = _decide(eng)
    _sql(eng.storage.db_path,
         "DELETE FROM audit_log WHERE rowid IN (SELECT rowid FROM audit_log ORDER BY rowid DESC LIMIT 2)")
    st = eng.storage.verify_chain()
    assert st["broken_entries"] == [] and not st["anchor_ok"] and not st["valid"]
    assert st["anchor_reason"].startswith("truncated:")
    _assert_truncation_block(_run(eng, {"action": "ping", "n": 1}, token))
    # Survives a restart too.
    eng = _reopen(eng, tmp, crypto)
    _assert_truncation_block(_run(eng, {"action": "ping", "n": 2}, token))


def test_cached_allow_is_not_served_after_truncation(tmp, crypto):
    eng = _engine(tmp, crypto)
    token = _decide(eng, 2)
    intent = {"action": "ping", "n": 0}  # cached ALLOW from _decide
    _sql(eng.storage.db_path,
         "DELETE FROM audit_log WHERE rowid = (SELECT MAX(rowid) FROM audit_log)")
    _assert_truncation_block(_run(eng, intent, token))


def test_deleted_db_with_existing_anchor_blocks(tmp, crypto):
    eng = _engine(tmp, crypto)
    token = _decide(eng)
    db = eng.storage.db_path
    eng.storage.close()
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(db + suffix):
            os.remove(db + suffix)
    eng = _engine(tmp, crypto)
    _assert_truncation_block(_run(eng, {"action": "ping", "n": 1}, token))
    # Appending new (BLOCK) rows must not "heal" it by lowering the anchor.
    for i in range(6):
        _assert_truncation_block(_run(eng, {"action": "ping", "n": 10 + i}, token))


def test_rollback_to_older_copy_blocks(tmp, crypto):
    eng = _engine(tmp, crypto)
    token = _decide(eng, 2)
    db = eng.storage.db_path
    eng.storage.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    backup = str(tmp / "old.db")
    shutil.copy(db, backup)
    for i in range(3):
        _run(eng, {"action": "ping", "n": 50 + i}, token)
    eng.storage.close()
    for suffix in ("-wal", "-shm"):
        if os.path.exists(db + suffix):
            os.remove(db + suffix)
    shutil.copy(backup, db)
    eng = _engine(tmp, crypto)
    _assert_truncation_block(_run(eng, {"action": "ping", "n": 1}, token))
    # Even once enough rows are appended to exceed the anchor's seq, the head
    # hash at that position no longer matches -> still BLOCK.
    for i in range(4):
        _run(eng, {"action": "ping", "n": 70 + i}, token)
    st = eng.storage.verify_chain()
    assert st["anchor_reason"] == "head_mismatch"
    _assert_truncation_block(_run(eng, {"action": "ping", "n": 2}, token))


@pytest.mark.parametrize("how", ["seq_lowered", "garbage", "foreign_key"])
def test_tampered_anchor_blocks(tmp, crypto, how):
    eng = _engine(tmp, crypto)
    token = _decide(eng)
    p = Path(eng.storage.anchor_path)
    doc = json.loads(p.read_text())
    if how == "seq_lowered":
        doc["seq"] = 1  # attacker truncates log and lowers anchor without the key
        p.write_text(json.dumps(doc))
    elif how == "garbage":
        p.write_text("{not json")
    else:
        other = cg.CryptoEngine(private_key_path=None)
        payload = eng.storage._anchor_payload(doc["seq"], doc["head_hash"])
        import base64
        doc["signature"] = base64.b64encode(other.sign(payload)).decode()
        p.write_text(json.dumps(doc))
    st = eng.storage.verify_chain()
    assert not st["anchor_ok"] and st["anchor_reason"].startswith("anchor_")
    _assert_truncation_block(_run(eng, {"action": "ping", "n": 1}, token))
    # The tampered anchor is kept as evidence, not silently rewritten.
    assert eng.storage.verify_chain()["anchor_ok"] is False


def test_anchor_deleted_while_running_blocks(tmp, crypto):
    eng = _engine(tmp, crypto)
    token = _decide(eng)
    os.remove(eng.storage.anchor_path)
    _sql(eng.storage.db_path,
         "DELETE FROM audit_log WHERE rowid = (SELECT MAX(rowid) FROM audit_log)")
    st = eng.storage.verify_chain()
    assert st["anchor_reason"] == "anchor_missing"
    _assert_truncation_block(_run(eng, {"action": "ping", "n": 1}, token))


def test_voucher_cannot_reopen_truncation_block(tmp, crypto):
    eng = _engine(tmp, crypto)
    token = _decide(eng, 2)
    eng.config["risk_review_threshold"] = 0.01
    intent = {"action": "export", "scope": "partial"}
    r = _run(eng, intent, token)
    assert r["decision"] == "REVIEW"
    voucher = eng.resolve_review(r["entry_id"], "auditor", True)["approval_voucher"]
    _sql(eng.storage.db_path,
         "DELETE FROM audit_log WHERE rowid = (SELECT MAX(rowid) FROM audit_log)")
    res = _run(eng, intent, token, approval_voucher=voucher)
    _assert_truncation_block(res)
    assert res["decision"] != "REVIEW"
