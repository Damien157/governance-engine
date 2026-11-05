"""Fail-closed tool gate: whole-intent scan, non-dict payloads, widened rm rule."""
from __future__ import annotations

import tempfile

import pytest

from governed_stack.stack import GovernedStack
from governed_stack.tool import GovernedTool


def _fresh_tool():
    d = tempfile.mkdtemp()
    st = GovernedStack(config={"db_path": d + "/a.db", "signing_key_path": d + "/k.pem", "log_level": 40})
    return GovernedTool(stack=st)


@pytest.fixture
def tool():
    # Fresh stack per test: HAIS kernel state carries across calls, so a run of
    # BLOCKs could otherwise cap a later benign call (hais_capability_cap).
    return _fresh_tool()


RM_BLOCK = [
    {"action": "shell", "cmd": "rm -rf /"},
    {"action": "shell", "cmd": "rm\t-rf /"},
    {"action": "shell", "cmd": "echo hi\nrm -rf /"},
    {"action": "shell", "argv": ["rm", "-rf", "/"]},
    {"action": "shell", "cmd": "rm -r -f /"},
    {"action": "shell", "cmd": "rm -f -r /"},
    {"action": "shell", "cmd": "rm -fr /"},
    {"action": "shell", "cmd": "rm -Rf /"},
    {"action": "shell", "cmd": "rm -R -f ~"},
    {"action": "shell", "cmd": "rm --recursive --force /"},
    {"action": "shell", "cmd": "rm --force -r *"},
    {"action": "shell", "cmd": "rm  -rf    ~/*"},
    {"action": "shell", "payload": {"cmd": "rm -rf /"}},
    {"action": "shell", "payload": {"cmd": "rm\t-rf /"}},
    {"action": "shell", "payload": {"argv": ["rm", "-rf", "/"]}},
    {"action": "shell", "payload": {"cmd": "rm -r -f /"}},
    {"action": "shell", "payload": {"steps": [{"run": "ls"}, {"run": "rm -r --force /"}]}},
    {"action": "shell", "opts": {"nested": {"cmd": "cd /tmp\nrm  -Rf  /"}}},
]


@pytest.mark.parametrize("intent", RM_BLOCK, ids=lambda i: repr(i)[:60])
def test_rm_variants_block(tool, intent):
    r = tool.check_sync(intent=intent)
    assert r["decision"] == "BLOCK", r
    assert "banned_term:destructive:rm_rf_root" in (r["reasons"] or [])


NONDICT_BLOCK = [
    {"action": "shell", "payload": "rm -rf /"},
    {"action": "shell", "payload": ["rm", "-rf", "/"]},
    {"action": "send_email", "payload": "synthesize sarin", "body": "hello"},
    {"action": "send_email", "payload": ["synthesize sarin"], "body": "hello"},
    {"action": "send_email", "payload": "synthesize sarin", "summary": "hello"},
    {"action": "calendar_write", "payload": "synthesize sarin", "summary": "meeting"},
    {"action": "calendar_write", "payload": ["synthesize sarin"], "text": "meeting"},
    {"action": "publish_post", "payload": "synthesize sarin", "text": "hi"},
    {"action": "publish_post", "payload": ["synthesize sarin"], "body": "hi"},
    {"action": "create_event", "payload": "synthesize sarin", "summary": "meeting"},
    {"action": "post", "payload": "synthesize sarin", "text": "hi"},
    {"action": "send_email", "payload": "synthesize sarin"},
]


@pytest.mark.parametrize("intent", NONDICT_BLOCK, ids=lambda i: repr(i)[:60])
def test_nondict_payload_blocks_invalid(tool, intent):
    r = tool.check_sync(intent=intent)  # must not raise
    assert r["decision"] == "BLOCK", r
    assert r["error_code"] == "GOV_INTENT_INVALID", r


def test_stack_govern_nondict_payload_no_crash(tool):
    import asyncio
    tok = tool.issue_token("damien")
    env = asyncio.run(tool.stack.govern({"action": "shell", "payload": "x"}, tok))
    assert env["decision"] == "BLOCK" and env["error_code"] == "GOV_INTENT_INVALID"


ALLOW = [
    {"action": "shell", "cmd": "rm file.txt"},
    {"action": "shell", "cmd": "rm -f build.log"},
    {"action": "shell", "argv": ["rm", "-f", "build.log"]},
    {"action": "shell", "cmd": "ls -la /"},
    {"action": "send_email", "subject": "Anthrax concert tickets",
     "body": "Got 2 tickets for Anthrax at the 3Arena Friday"},
    {"action": "send_email", "subject": "lunch", "body": "lunch at 1?"},
]


@pytest.mark.parametrize("intent", ALLOW, ids=lambda i: repr(i)[:60])
def test_negatives_allow(tool, intent):
    r = tool.check_sync(intent=intent)
    assert r["decision"] == "ALLOW", r


def test_rm_regex_unit():
    from certified_governance_unified import PolicyEngine  # type: ignore
    eng = PolicyEngine()
    hits = lambda t: eng.evaluate({"action": "shell", "cmd": t})["reasons"]
    for t in ["rm file.txt", "rm -f build.log", "rm -r build/", "rm -i /"]:
        assert "banned_term:destructive:rm_rf_root" not in hits(t), t
    assert "banned_term:destructive_review:rm_rf" in hits("rm -r -f build/")


# ---- review gaps (PR #33 follow-up) ----------------------------------------

ROOT_GAP_BLOCK = [
    # 1. recursive without force on a root target
    "rm -r /", "rm --recursive /", "rm -r -I /",
    # 2. end-of-options / quoted / dot root
    "rm -rf -- /", "rm -rf '/'", 'rm -rf "/"', "rm -rf /.",
    # 3. unicode + quoting tricks
    "r\u200bm -rf /", "rm \uff0drf /", "\uff52\uff4d -rf /", "r''m -rf /", "r\\m -rf /",
    # 5. top-level system dirs and one level below still BLOCK
    "rm -rf /home", "rm -rf /home/", "rm -rf /home/user", "rm -rf /var", "rm -rf /etc",
    "rm -rf /usr/lib", "rm -rf /home/me/../..",
]


@pytest.mark.parametrize("cmd", ROOT_GAP_BLOCK, ids=repr)
def test_rm_root_gaps_block(tool, cmd):
    r = tool.check_sync(intent={"action": "shell", "cmd": cmd})
    assert r["decision"] == "BLOCK", r
    assert "banned_term:destructive:rm_rf_root" in (r["reasons"] or [])


@pytest.mark.parametrize("cmd", ["rm -rf /home/me/proj/dist", "rm -rf /var/tmp/x"], ids=repr)
def test_rm_deep_paths_review_not_root(tool, cmd):
    r = tool.check_sync(intent={"action": "shell", "cmd": cmd})
    assert r["decision"] == "REVIEW", r
    assert "banned_term:destructive:rm_rf_root" not in (r["reasons"] or [])


@pytest.mark.parametrize("cmd,want", [
    ("rm -f build.log", "ALLOW"), ("git rm -r --cached foo", "ALLOW"),
    ("rm -rf ./build", "REVIEW"), ("rm -rf /tmp/cache", "REVIEW"),
], ids=repr)
def test_rm_benign_unchanged(tool, cmd, want):
    assert tool.check_sync(intent={"action": "shell", "cmd": cmd})["decision"] == want


@pytest.mark.parametrize("intent", [
    {"action": "shell", ("k",): "rm -rf /"},
    {"action": "shell", "payload": {"cmd": "ls", ("a", 1): "x"}},
], ids=repr)
def test_non_str_key_clean_block(tool, intent):
    r = tool.check_sync(intent=intent)
    assert r["decision"] == "BLOCK", r
    assert r["error_code"] == "GOV_INTENT_INVALID"


@pytest.mark.parametrize("cmd,reason", [
    ("find / -delete", "banned_term:destructive:find_delete_root"),
    ("find / -name x -exec rm -rf {} +", "banned_term:destructive:find_delete_root"),
    ("echo cm0gLXJmIC8= | base64 -d | sh", "banned_term:destructive:decode_pipe_shell"),
    ("base64 --decode p.txt | sudo bash", "banned_term:destructive:decode_pipe_shell"),
], ids=repr)
def test_find_delete_and_decode_pipe_block(tool, cmd, reason):
    r = tool.check_sync(intent={"action": "shell", "cmd": cmd})
    assert r["decision"] == "BLOCK", r
    assert reason in (r["reasons"] or [])


@pytest.mark.parametrize("cmd", ["find . -name '*.pyc' -delete", "base64 -d blob > out.bin"], ids=repr)
def test_find_and_decode_benign_not_blocked(tool, cmd):
    assert tool.check_sync(intent={"action": "shell", "cmd": cmd})["decision"] != "BLOCK"
