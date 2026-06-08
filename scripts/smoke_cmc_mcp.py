#!/usr/bin/env python3
"""Smoke test the optional CMC MCP endpoint without configuring payment."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config.data_sources import CMC_MCP_URL  # noqa: E402
from src.data.cmc_mcp_client import CmcMcpClient, CmcMcpError  # noqa: E402


async def run() -> int:
    os.chdir(ROOT)
    load_dotenv(ROOT / ".env")
    client = CmcMcpClient(
        enabled=True,
        shadow_mode=False,
        url=os.getenv("CMC_MCP_URL", CMC_MCP_URL),
        timeout_s=float(os.getenv("CMC_MCP_TIMEOUT_S", "20")),
    )
    try:
        async with httpx.AsyncClient(timeout=client.timeout_s) as http_client:
            initialize_payload = await client.initialize(http_client)
            print(f"initialize_ok={bool(initialize_payload)}")
            tools = await client.list_tools(http_client)
            print(f"tools_count={len(tools)}")
            for tool in tools:
                name = tool.get("name")
                if name:
                    print(name)
        return 0
    except CmcMcpError as exc:
        print(f"cmc_mcp_error={exc}", file=sys.stderr)
        artifact = ROOT / "artifacts" / "x402_402_response.json"
        if artifact.exists():
            print(f"402_artifact={artifact}")
        return 1
    except Exception as exc:
        print(f"unexpected_error={exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
