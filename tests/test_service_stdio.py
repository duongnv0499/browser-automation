"""Real subprocess/browser exercises; opt in only during final verification.
Run: BROWSER_INTEGRATION_TESTS=1 uv run --with mcp pytest tests/test_service_stdio.py
"""
import asyncio
import base64
import json
import os
import sys
from urllib.parse import quote
import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.environ.get("BROWSER_INTEGRATION_TESTS") != "1", reason="Final integration verification opt-in")]
PAGE = "data:text/html," + quote('<!doctype html><title>Transport proof</title><h1>Visible fixture</h1><button onclick="document.querySelector(\'h1\').textContent=\'Rendered SUCCESS\'">Update page</button>')


async def cli_call(process, command, arguments, identifier):
    process.stdin.write((json.dumps({"id": identifier, "command": command, "arguments": arguments}) + "\n").encode())
    await process.stdin.drain()
    response = json.loads(await asyncio.wait_for(process.stdout.readline(), 30))
    assert response.get("id") == identifier and "error" not in response, response
    return response["result"]


async def test_cli_persistent_real_browser(tmp_path):
    process = await asyncio.create_subprocess_exec(sys.executable, "-m", "browser_automation", "serve", stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        opened = await cli_call(process, "launch", {"headless": True}, 1)
        sid = opened["session_id"]
        tab = (await cli_call(process, "new_tab", {"session_id": sid, "url": PAGE}, 2))["tab"]
        args = {"session_id": sid, "tab_id": tab["id"]}
        obs = await cli_call(process, "observe", {**args, "screenshot": True}, 3)
        png = base64.b64decode(obs["screenshot"])
        assert png.startswith(b"\x89PNG\r\n\x1a\n")
        (tmp_path / "cli-before.png").write_bytes(png)
        target = next(e for e in obs["elements"] if e["name"] == "Update page")
        await cli_call(process, "act", {**args, "action": {"observation_id": obs["id"], "operation": "click", "target": target["id"]}}, 4)
        after = await cli_call(process, "observe", {**args, "screenshot": True}, 5)
        assert "Rendered SUCCESS" in after["text"]
        (tmp_path / "cli-after.png").write_bytes(base64.b64decode(after["screenshot"]))
        assert after["screenshot"] != obs["screenshot"]
        text = await cli_call(process, "text", {"session_id": sid, "observation_id": after["id"], "offset": 0}, 6)
        assert "Rendered SUCCESS" in text["text"]
        await cli_call(process, "close", {"session_id": sid}, 7)
    finally:
        process.stdin.close()
        await asyncio.wait_for(process.wait(), 15)
    assert process.returncode == 0, (await process.stderr.read()).decode()


async def test_mcp_official_client_real_browser(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    params = StdioServerParameters(command=sys.executable, args=["-m", "browser_automation.mcp"])
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as client:
            initialized = await client.initialize()
            assert initialized.serverInfo.name == "browser-automation"
            tools = await client.list_tools()
            assert {"observe", "act", "connect_default", "approved_act"} <= {tool.name for tool in tools.tools}
            async def call(name, arguments):
                result = await client.call_tool(name, arguments)
                assert not result.isError, result
                return result, json.loads(result.content[0].text)
            _, opened = await call("launch", {"headless": True})
            sid = opened["session_id"]
            _, tab_result = await call("new_tab", {"session_id": sid, "url": PAGE})
            args = {"session_id": sid, "tab_id": tab_result["tab"]["id"]}
            result, obs = await call("observe", {**args, "screenshot": True})
            images = [block for block in result.content if block.type == "image"]
            assert len(images) == 1 and images[0].mimeType == "image/png"
            assert "screenshot" not in obs
            (tmp_path / "mcp-before.png").write_bytes(base64.b64decode(images[0].data))
            target = next(e for e in obs["elements"] if e["name"] == "Update page")
            await call("act", {**args, "action": {"observation_id": obs["id"], "operation": "click", "target": target["id"]}})
            result, after = await call("observe", {**args, "screenshot": True})
            assert "Rendered SUCCESS" in after["text"]
            (tmp_path / "mcp-after.png").write_bytes(base64.b64decode(next(b.data for b in result.content if b.type == "image")))
            failed = await client.call_tool("tabs", {"session_id": "missing"})
            assert failed.isError and "unknown_session" in failed.content[0].text
            await call("close", {"session_id": sid})
