"""Opt-in actual uvicorn/official SDK/Chromium HTTP acceptance; no live inference."""
import asyncio
import base64
from contextlib import asynccontextmanager
import json
import os
import socket
import signal
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
    logs = (await process.stderr.read()).decode()
    # Uvicorn re-raises handled SIGTERM only after completing ASGI lifespan.
    assert process.returncode in (0, -signal.SIGTERM), logs
    assert "Application shutdown complete" in logs and "ERROR:" not in logs, logs


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
            assert first.protocol_version == ("2025-11-25" if mode == "legacy" else "2026-07-28")
            if mode == "legacy":
                assert "run" in first.instructions and first.server_info.name == "browser-automation"
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
            if mode == "legacy":
                async with client_for(url, mode="legacy") as same_identity_peer:
                    _, peer_doctor = await call(same_identity_peer, "doctor")
                    assert sid not in peer_doctor["sessions"]
                    assert (await same_identity_peer.call_tool("tabs", {"session_id": sid})).is_error
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
            (tmp_path / f"http-{mode}-evidence.json").write_text(json.dumps({
                "mode": mode, "negotiated_protocol": first.protocol_version, "sdk": "2.3.0", "tools": [tool.name for tool in (await first.list_tools()).tools],
                "before_text": obs["text"], "filled_text": filled["text"], "after_text": rendered["text"],
                "other_identity_sessions": doctor["sessions"], "other_identity_error": forbidden.structured_content,
                "image_mime": image.mime_type, "before_png_bytes": len(png), "after_png_bytes": len(after_png)
            }, indent=2))
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


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_idle_ttl_never_expires_active_browser_call(mode, page):
    async with http_server("--session-idle-timeout", "0.3") as (url, _):
        async with client_for(url, mode=mode) as client:
            _, opened = await call(client, "launch", {"headless": True})
            sid = opened["session_id"]
            _, created = await call(client, "new_tab", {"session_id": sid, "url": page})
            args = {"session_id": sid, "tab_id": created["tab"]["id"]}
            _, obs = await call(client, "observe", args)
            await call(client, "act", {**args, "action": {"observation_id": obs["id"], "operation": "wait", "seconds": 1}})
            assert (await call(client, "tabs", {"session_id": sid}))[1]["tabs"]
            await call(client, "close", {"session_id": sid})


async def test_legacy_explicit_cancel_notification_releases_browser_lock(page):
    import httpx
    from test_mcp_http import AUTH, initialize, response_json
    async with http_server() as (url, _):
        async with httpx.AsyncClient(timeout=15) as http:
            init = await http.post(url, json=initialize(), headers=AUTH)
            headers = {**AUTH, "Mcp-Session-Id": init.headers["mcp-session-id"], "Mcp-Protocol-Version": "2025-11-25"}
            await http.post(url, json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=headers)
            async def raw_call(identifier, name, args):
                result = response_json(await http.post(url, headers=headers, json={"jsonrpc": "2.0", "id": identifier, "method": "tools/call", "params": {"name": name, "arguments": args}}))
                assert "error" not in result and not result["result"]["isError"], result
                return result["result"]["structuredContent"]
            opened = await raw_call(2, "launch", {"headless": True})
            sid = opened["session_id"]
            created = await raw_call(3, "new_tab", {"session_id": sid, "url": page})
            args = {"session_id": sid, "tab_id": created["tab"]["id"]}
            obs = await raw_call(4, "observe", args)
            pending = asyncio.create_task(http.post(url, headers=headers, json={"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "act", "arguments": {**args, "action": {"observation_id": obs["id"], "operation": "wait", "seconds": 10}}}}))
            await asyncio.sleep(0.2)
            cancelled = await http.post(url, headers=headers, json={"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 5, "reason": "fixture explicit cancellation"}})
            assert cancelled.status_code == 202
            assert (await asyncio.wait_for(raw_call(6, "tabs", {"session_id": sid}), 3))["tabs"]
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
            await http.delete(url, headers=headers)


async def test_actual_localhost_reverse_proxy_hop(page, tmp_path):
    """Real socket HTTP relay to real uvicorn; no TLS deployment claim."""
    from urllib.parse import urlsplit
    async with http_server() as (url, _):
        upstream = urlsplit(url)
        authorities = []
        async def relay(reader, writer):
            remote_writer = None
            try:
                header = await reader.readuntil(b"\r\n\r\n")
                lines = header.split(b"\r\n")
                authorities.extend(line.decode() for line in lines if line.lower().startswith(b"host:"))
                # One streamed HTTP exchange per upstream connection: tell both
                # peers to reconnect, so every request's Host is rewritten.
                lines = [line for line in lines if not line.lower().startswith((b"host:", b"connection:"))]
                lines.insert(1, b"Connection: close")
                lines.insert(1, f"Host: {upstream.netloc}".encode())
                remote_reader, remote_writer = await asyncio.open_connection(upstream.hostname, upstream.port)
                remote_writer.write(b"\r\n".join(lines))
                await remote_writer.drain()
                async def pump(source, destination):
                    while data := await source.read(65536):
                        destination.write(data)
                        await destination.drain()
                tasks = [asyncio.create_task(pump(reader, remote_writer)), asyncio.create_task(pump(remote_reader, writer))]
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            except (ConnectionError, asyncio.IncompleteReadError):
                pass
            finally:
                writer.close()
                if remote_writer:
                    remote_writer.close()
                    await remote_writer.wait_closed()
                await writer.wait_closed()
        proxy = await asyncio.start_server(relay, "127.0.0.1", 0)
        async with proxy:
            proxy_url = f"http://127.0.0.1:{proxy.sockets[0].getsockname()[1]}/mcp"
            async with client_for(proxy_url) as client:
                assert client.protocol_version == "2026-07-28"
                _, doctor = await call(client, "doctor")
                assert doctor["transport"] == "streamable-http"
                _, opened = await call(client, "launch", {"headless": True})
                sid = opened["session_id"]
                _, tab = await call(client, "new_tab", {"session_id": sid, "url": page})
                result, obs = await call(client, "observe", {"session_id": sid, "tab_id": tab["tab"]["id"], "screenshot": True})
                assert "Waiting" in obs["text"]
                png = base64.b64decode(next(block.data for block in result.content if block.type == "image"))
                (tmp_path / "reverse-proxy-browser.png").write_bytes(png)
                await call(client, "close", {"session_id": sid})
        assert authorities and all(upstream.netloc not in authority for authority in authorities)
        (tmp_path / "reverse-proxy-evidence.json").write_text(json.dumps({"actual_http_hops": 2, "sdk_mode": "auto", "doctor": doctor, "observed_client_host_headers": authorities, "upstream_authority": upstream.netloc, "tls_exercised": False}, indent=2))
