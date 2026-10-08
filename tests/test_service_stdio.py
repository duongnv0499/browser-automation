"""Real subprocess/browser exercises; opt in only during final verification.
Run: BROWSER_INTEGRATION_TESTS=1 uv run --with mcp pytest tests/test_service_stdio.py
"""
import asyncio
import base64
import json
import os
import sys
import time
import pytest_asyncio
import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.environ.get("BROWSER_INTEGRATION_TESTS") != "1", reason="Final integration verification opt-in")]
HTML = '''<!doctype html><title>Transport proof</title><h1>Visible fixture</h1>
<a href="#result" onclick="document.querySelector('h1').textContent='Rendered SUCCESS'">Show result</a>
<h2>Drag pending</h2><a href="#source" draggable="true" ondragend="document.querySelector('h2').textContent='Dragged SUCCESS'">Move item</a>
<a href="#drop" style="display:inline-block;margin-left:100px;padding:40px" ondragover="event.preventDefault()">Drop zone</a>'''.encode()

@pytest_asyncio.fixture
async def local_page():
    async def respond(reader, writer):
        try:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: " + str(len(HTML)).encode() + b"\r\nConnection: close\r\n\r\n" + HTML)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
    server = await asyncio.start_server(respond, "127.0.0.1", 0)
    async with server:
        yield f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/"


async def cli_call(process, command, arguments, identifier):
    process.stdin.write((json.dumps({"id": identifier, "command": command, "arguments": arguments}) + "\n").encode())
    await process.stdin.drain()
    response = json.loads(await asyncio.wait_for(process.stdout.readline(), 30))
    assert response.get("id") == identifier and "error" not in response, response
    return response["result"]


async def test_cli_persistent_real_browser(tmp_path, local_page):
    process = await asyncio.create_subprocess_exec(sys.executable, "-m", "browser_automation", "serve", stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        opened = await cli_call(process, "launch", {"headless": True}, 1)
        sid = opened["session_id"]
        secret = tmp_path / "secret.txt"
        secret.write_text("HOST_SECRET_NOT_BROWSER_CONTENT")
        process.stdin.write((json.dumps({"id": "denial", "command": "new_tab", "arguments": {"session_id": sid, "url": secret.as_uri()}}) + "\n").encode())
        await process.stdin.drain()
        denied = json.loads(await asyncio.wait_for(process.stdout.readline(), 30))
        assert denied["error"]["code"] == "prohibited_url" and "HOST_SECRET" not in json.dumps(denied)
        tab = (await cli_call(process, "new_tab", {"session_id": sid, "url": local_page}, 2))["tab"]
        args = {"session_id": sid, "tab_id": tab["id"]}
        obs = await cli_call(process, "observe", {**args, "screenshot": True}, 3)
        png = base64.b64decode(obs["screenshot"])
        assert png.startswith(b"\x89PNG\r\n\x1a\n")
        (tmp_path / "cli-before.png").write_bytes(png)
        target = next(e for e in obs["elements"] if e["name"] == "Show result")
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


async def test_mcp_official_client_real_browser(tmp_path, local_page):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    approval_path = tmp_path / "approvals.json"
    approval_path.write_text("[]")
    params = StdioServerParameters(command=sys.executable, args=["-m", "browser_automation.mcp"], env={**os.environ, "BROWSER_APPROVALS_FILE": str(approval_path)})
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as client:
            initialized = await client.initialize()
            assert initialized.serverInfo.name == "browser-automation"
            tools = await client.list_tools()
            assert {"observe", "act", "connect_default", "approved_act"} <= {tool.name for tool in tools.tools}
            action_properties = next(t.inputSchema for t in tools.tools if t.name == "act")["properties"]["action"]["properties"]
            assert {"to_target", "to_x", "to_y", "delta_x", "delta_y", "seconds"} <= set(action_properties)
            async def call(name, arguments):
                result = await client.call_tool(name, arguments)
                assert not result.isError, result
                return result, json.loads(result.content[0].text)
            _, opened = await call("launch", {"headless": True})
            sid = opened["session_id"]
            secret = tmp_path / "secret.txt"
            secret.write_text("HOST_SECRET_NOT_BROWSER_CONTENT")
            denied = await client.call_tool("new_tab", {"session_id": sid, "url": secret.as_uri()})
            assert denied.isError and "prohibited_url" in denied.content[0].text and "HOST_SECRET" not in denied.content[0].text
            _, tab_result = await call("new_tab", {"session_id": sid, "url": local_page})
            args = {"session_id": sid, "tab_id": tab_result["tab"]["id"]}
            result, obs = await call("observe", {**args, "screenshot": True})
            images = [block for block in result.content if block.type == "image"]
            assert len(images) == 1 and images[0].mimeType == "image/png"
            assert "screenshot" not in obs
            (tmp_path / "mcp-before.png").write_bytes(base64.b64decode(images[0].data))
            target = next(e for e in obs["elements"] if e["name"] == "Show result")
            await call("act", {**args, "action": {"observation_id": obs["id"], "operation": "click", "target": target["id"]}})
            result, after = await call("observe", {**args, "screenshot": True})
            assert "Rendered SUCCESS" in after["text"]
            (tmp_path / "mcp-after.png").write_bytes(base64.b64decode(next(b.data for b in result.content if b.type == "image")))
            source = next(e for e in after["elements"] if e["name"] == "Move item")
            destination = next(e for e in after["elements"] if e["name"] == "Drop zone")
            action = {"observation_id": after["id"], "operation": "drag", "target": source["id"], "to_target": destination["id"]}
            _, paused = await call("act", {**args, "action": action})
            assert paused["status"] == "approval_required"
            approval_path.write_text(json.dumps([{"token": "host-drag-fixture", "binding": paused["binding"], "expires_at": time.time() + 60}]))
            await call("approved_act", {**args, "observation_id": after["id"], "approval_token": "host-drag-fixture"})
            result, dragged = await call("observe", {**args, "screenshot": True})
            assert "Dragged SUCCESS" in dragged["text"]
            (tmp_path / "mcp-dragged.png").write_bytes(base64.b64decode(next(b.data for b in result.content if b.type == "image")))
            failed = await client.call_tool("tabs", {"session_id": "missing"})
            assert failed.isError and "unknown_session" in failed.content[0].text
            await call("close", {"session_id": sid})

@pytest.mark.parametrize("flags", [[], ["--no-screenshot"]])
async def test_cli_text_only_local_decisions_loop(local_page, flags):
    requests = []
    async def decide(reader, writer):
        try:
            headers = (await reader.readuntil(b"\r\n\r\n")).decode()
            length = next(int(line.split(":", 1)[1]) for line in headers.split("\r\n") if line.lower().startswith("content-length:"))
            payload = json.loads(await reader.readexactly(length))
            requests.append(payload)
            assert isinstance(payload["state"], str), "Text-only mode must not transmit visual input"
            name, question = next(iter(payload["questions"].items()))
            choices = question["criteria"]
            choice = "satisfied" if name == "goal_verification" else next(value for value, description in choices.items() if json.loads(description)["action"]["operation"] == "done")
            body = json.dumps({"answers": {name: {"type": "choice", "choice": choice, "confidence": 1.0, "probabilities": {value: float(value == choice) for value in choices}}}}).encode()
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: " + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
    server = await asyncio.start_server(decide, "127.0.0.1", 0)
    async with server:
        endpoint = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/decisions"
        env = {**os.environ, "OPENROUTER_API_KEY": "local-test-fixture-not-live", "BROWSER_AGENT_VISION": "false", "BROWSER_AGENT_DECISIONS_ENDPOINT": endpoint}
        process = await asyncio.create_subprocess_exec(sys.executable, "-m", "browser_automation", "run", "--isolated", "--headless", "--url", local_page, *flags, "Read the Visible fixture page", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env)
        output, errors = await asyncio.wait_for(process.communicate(), 45)
    assert process.returncode == 0, errors.decode()
    result = json.loads(output)
    assert result["status"] == "success" and result["verification"]["satisfied"]
    assert "screenshot" not in result["observation"]
    assert {next(iter(request["questions"])) for request in requests} == {"next_action", "goal_verification"}
