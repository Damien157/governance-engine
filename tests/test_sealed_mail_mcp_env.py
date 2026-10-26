"""sealed_mail_mcp: env-configured adapter + single drafts-only tool (no network)."""

from __future__ import annotations

import asyncio
import pytest

from governed_stack import sealed_mail_mcp as smm
from governed_stack.sealed_mail import SealedMailAdapter


def test_adapter_from_env_spool_and_allowlist(tmp_path, monkeypatch):
    monkeypatch.setenv("SEALED_MAIL_OUTBOX", str(tmp_path / "out"))
    monkeypatch.setenv("SEALED_MAIL_ALLOWLIST", " me@example.com , ,you@example.com")
    ad = smm.adapter_from_env()
    assert isinstance(ad, SealedMailAdapter)


def test_tool_name_is_draft_only():
    assert smm.TOOL_NAME == "propose_draft"
    assert not any("send" in n for n in smm.__all__)


def test_server_exposes_only_propose_draft_and_gates(tmp_path, monkeypatch):
    pytest.importorskip("mcp.server.mcpserver")
    out = tmp_path / "out"
    monkeypatch.setenv("SEALED_MAIL_OUTBOX", str(out))
    monkeypatch.setenv("SEALED_MAIL_ALLOWLIST", "me@example.com")
    server = smm.build_server(smm.adapter_from_env())

    async def go():
        tools = await server.list_tools()
        assert [t.name for t in tools] == ["propose_draft"]
        blocked = await server.call_tool(
            "propose_draft",
            {"to": ["me@example.com"], "subject": "Acct", "body": "Here is your password for the portal"},
        )
        not_allowed = await server.call_tool(
            "propose_draft", {"to": ["stranger@evil.example"], "subject": "Hi", "body": "Lunch?"}
        )
        ok = await server.call_tool(
            "propose_draft", {"to": ["me@example.com"], "subject": "Lunch", "body": "Are you free?"}
        )
        return blocked, not_allowed, ok

    blocked, not_allowed, ok = asyncio.run(go())
    assert blocked.structured_content["result"]["decision"] == "BLOCK"
    assert not blocked.structured_content["result"]["written"]
    assert not not_allowed.structured_content["result"]["written"]
    assert ok.structured_content["result"]["decision"] == "ALLOW"
    files = list(out.rglob("*.json"))
    assert len(files) == 1
    assert "Are you free?" in files[0].read_text()
    assert "password" not in files[0].read_text()
