"""
Tiny stdio MCP server exposing exactly one tool: ``propose_draft``.

The tool routes through ``SealedMailAdapter`` (bio seal → GovernedMail gate →
draft from the ALLOW envelope only). There is no send / reply / forward tool.

Register THIS server with an agent *instead of* a raw Gmail connector; that is
the only configuration in which the gate is actually enforced. If the agent
also holds the raw Gmail tool, it can bypass this server.

Default backend is ``spool_backend`` (local JSON files, no credentials). A
host that owns Gmail credentials may consume the spool; this module never
touches credentials or the network.

Run::

    SEALED_MAIL_OUTBOX=artifacts/sealed_mail/outbox \
    SEALED_MAIL_ALLOWLIST=me@example.com \
    python -m governed_stack.sealed_mail_mcp

Requires the optional ``mcp`` package (``pip install -e '.[mcp]'``).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from .connectors import ContentBindingError
from .sealed_mail import SealedMailAdapter, spool_backend

TOOL_NAME = "propose_draft"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_OUTBOX = _REPO_ROOT / "artifacts" / "sealed_mail" / "outbox"


def build_server(adapter: SealedMailAdapter) -> Any:
    """Build an MCPServer with the single gated tool bound to ``adapter``."""
    from mcp.server.mcpserver import MCPServer  # optional dependency (mcp>=2)

    server = MCPServer(
        name="sealed-mail",
        instructions=(
            "Drafts only. Every draft passes the governance gate; BLOCK/REVIEW "
            "write nothing. There is no send tool."
        ),
    )

    @server.tool(
        name=TOOL_NAME,
        description=(
            "Propose an email DRAFT (never sent). Gated by governance: returns "
            "decision ALLOW (draft written from the approved envelope), BLOCK, "
            "or REVIEW (nothing written)."
        ),
    )
    async def propose_draft(
        to: List[str], subject: str, body: str, cc: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        try:
            return await adapter.propose_draft(to=to, subject=subject, body=body, cc=cc)
        except ContentBindingError as exc:
            return {
                "ok": False,
                "decision": "REFUSED",
                "reasons": [f"sealed_mail:content_binding:{exc}"],
                "written": False,
            }
        except TypeError as exc:
            return {
                "ok": False,
                "decision": "REFUSED",
                "reasons": [f"sealed_mail:bad_request:{exc}"],
                "written": False,
            }

    return server


def adapter_from_env() -> SealedMailAdapter:
    outbox = os.environ.get("SEALED_MAIL_OUTBOX") or str(_DEFAULT_OUTBOX)
    allow_raw = os.environ.get("SEALED_MAIL_ALLOWLIST", "").strip()
    allowlist = [a for a in (s.strip() for s in allow_raw.split(",")) if a] or None
    return SealedMailAdapter(spool_backend(outbox), recipient_allowlist=allowlist)


def main() -> None:  # pragma: no cover - process entrypoint
    build_server(adapter_from_env()).run("stdio")


if __name__ == "__main__":  # pragma: no cover
    main()


__all__ = ["TOOL_NAME", "adapter_from_env", "build_server", "main"]
