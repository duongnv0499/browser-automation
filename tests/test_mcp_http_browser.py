"""Opt-in actual uvicorn/official SDK/Chromium HTTP acceptance; no live inference."""
import asyncio
import base64
from contextlib import asynccontextmanager
import json
import os
import socket
import sys

import pytest
import pytest_asyncio

pytest.importorskip("mcp")
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.environ.get("BROWSER_INTEGRATION_TESTS") != "1", reason="Coordinated real-browser verification opt-in")]
TOKEN_A = "fixture-principal-a-0123456789"
TOKEN_B = "fixture-principal-b-0123456789"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@asynccontextmanager
async def http_server(*flags):
    port = free_port()
    env = {**os.environ, "BROWSER_MCP_TOKENS": json.dumps({"a": TOKEN_A, "b": TOKEN_B})}
    env.pop("BROWSER_MCP_TOKEN", None)
    process = await asyncio.create_subprocess_exec(sys.executable, "-m", "browser_automation", "mcp", "--transport", "streamable-http", "--port", str(port), *flags,
                                                  env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    url = f"http://127.0.0.1:{port}/mcp"
    try:
        for _ in range(100):
            if process.returncode is not None:
                raise AssertionError((await process.stderr.read()).decode())
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.close()
                await writer.wait_closed()
                break
            except OSError:
                await asyncio.sleep(0.05)
        else:
            raise AssertionError("HTTP server did not listen")
        yield url, process
    finally:
        if process.returncode is None:
            process.terminate()
        await asyncio.wait_for(process.wait(), 20)
    assert process.returncode == 0, (await process.stderr.read()).decode()


@asynccontextmanager
async def client_for(url, token=TOKEN_A, mode="auto", terminate_on_close=True):
    async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + token}) as http:
        async with Client(streamable_http_client(url, http_client=http, terminate_on_close=terminate_on_close), mode=mode) as client:
            yield client


async def call(client, name, args=None):
    result = await client.call_tool(name, args or {})
    assert not result.is_error, result
    return result, result.structured_content


@pytest_asyncio.fixture
async def page():
    body = b'''<!doctype html><title>HTTP browser proof</title><h1>Waiting</h1>
<label>Name <input aria-label="Name" oninput="document.querySelector('h1').textContent='Name '+this.value"></label>
<a href="#result" onclick="document.querySelector('h1').textContent='Rendered HTTP SUCCESS'">Show result</a>'''
    async def handle(reader, writer):
        try:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: " + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    async with server:
        yield f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/"


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_official_client_browser_images_guarded_input_and_identity(mode, page, tmp_path):
    async with http_server() as (url, process):
        async with client_for(url, mode=mode) as first, client_for(url, TOKEN_B, mode=mode) as second:
            assert {"run", "observe", "act", "approved_act"} <= {tool.name for tool in (await first.list_tools()).tools}
            _, opened = await call(first, "launch", {"headless": True})
            sid = opened["session_id"]
            _, created = await call(first, "new_tab", {"session_id": sid, "url": page})
            args = {"session_id": sid, "tab_id": created["tab"]["id"]}
            before, obs = await call(first, "observe", {**args, "screenshot": True})
            image = next(block for block in before.content if block.type == "image")
            png = base64.b64decode(image.data)
            assert png.startswith(b"\x89PNG\r\n\x1a\n") and "screenshot" not in obs
            (tmp_path / f"http-{mode}-before.png").write_bytes(png)
            _, doctor = await call(second, "doctor")
            assert sid not in doctor["sessions"]
            forbidden = await second.call_tool("tabs", {"session_id": sid})
            assert forbidden.is_error and forbidden.structured_content["error"]["code"] == "unknown_session"
            target = next(e for e in obs["elements"] if e["name"] == "Name")
            await call(first, "act", {**args, "action": {"observation_id": obs["id"], "operation": "fill", "target": target["id"], "text": "Ada"}})
            _, filled = await call(first, "observe", args)
            assert "Name Ada" in filled["text"]
            target = next(e for e in filled["elements"] if e["name"] == "Show result")
            await call(first, "act", {**args, "action": {"observation_id": filled["id"], "operation": "click", "target": target["id"]}})
            after, rendered = await call(first, "observe", {**args, "screenshot": True})
            assert "Rendered HTTP SUCCESS" in rendered["text"]
            after_png = base64.b64decode(next(block.data for block in after.content if block.type == "image"))
            assert png != after_png
            (tmp_path / f"http-{mode}-after.png").write_bytes(after_png)
            stale = await first.call_tool("act", {**args, "action": {"observation_id": filled["id"], "operation": "click", "target": target["id"]}})
            assert stale.is_error
            await call(first, "close", {"session_id": sid})


async def test_modern_same_identity_persistence_and_disconnect_cancellation(page):
    async with http_server() as (url, _):
        async with client_for(url, mode="2026-07-28") as first:
            _, opened = await call(first, "launch", {"headless": True})
            sid = opened["session_id"]
            _, tab = await call(first, "new_tab", {"session_id": sid, "url": page})
            args = {"session_id": sid, "tab_id": tab["tab"]["id"]}
            _, obs = await call(first, "observe", args)
            task = asyncio.create_task(first.call_tool("act", {**args, "action": {"observation_id": obs["id"], "operation": "wait", "seconds": 10}}))
            await asyncio.sleep(0.2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await asyncio.wait_for(call(first, "tabs", {"session_id": sid}), 3)
        # Ordinary modern socket/client close does not delete owned browser state.
        async with client_for(url, mode="2026-07-28") as reconnected:
            _, tabs = await call(reconnected, "tabs", {"session_id": sid})
            assert tabs["tabs"]
            await call(reconnected, "close", {"session_id": sid})


@pytest.mark.parametrize("lifecycle", ["delete", "expire", "shutdown"])
async def test_attached_original_survives_http_lifecycle(lifecycle, page):
    from playwright.async_api import async_playwright
    cdp_port = free_port()
    env = dict(os.environ)
    library = env.get("BROWSER_AGENT_LIBRARY_PATH")
    if library:
        env["LD_LIBRARY_PATH"] = os.pathsep.join(filter(None, [library, env.get("LD_LIBRARY_PATH")]))
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, args=[f"--remote-debugging-port={cdp_port}"], env=env)
        try:
            context = await browser.new_context()
            original = await context.new_page()
            await original.goto(page)
            async with http_server("--session-idle-timeout", "0.5") as (url, process):
                async with client_for(url, mode="legacy", terminate_on_close=lifecycle == "delete") as client:
                    _, opened = await call(client, "connect", {"endpoint": f"http://127.0.0.1:{cdp_port}"})
                    assert opened["mode"] == "attached"
                    await call(client, "new_tab", {"session_id": opened["session_id"], "url": page})
                if lifecycle == "expire":
                    await asyncio.sleep(1.2)
                elif lifecycle == "shutdown":
                    process.terminate()
                    await asyncio.wait_for(process.wait(), 20)
                assert browser.is_connected() and not original.is_closed()
                assert await original.title() == "HTTP browser proof"
                assert len(context.pages) == 1, "HTTP cleanup must close owned tabs, not original user tabs"
        finally:
            await browser.close()
