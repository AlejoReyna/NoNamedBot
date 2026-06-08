"""Parsing tests for the optional async CMC MCP client."""

from __future__ import annotations

from src.data.cmc_mcp_client import CmcMcpClient


def test_parse_sse_json() -> None:
    client = CmcMcpClient(enabled=True)
    text = 'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n\n'

    payload = client._parse_sse_json(text)

    assert payload["result"]["ok"] is True
