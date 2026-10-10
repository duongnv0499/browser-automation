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

# Screenshot observations are single JSON lines larger than asyncio's 64 KiB default.
LINE_LIMIT = 64 * 1024 * 1024
pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.environ.get("BROWSER_INTEGRATION_TESTS") != "1", reason="Final integration verification opt-in")]
HTML = '''<!doctype html><title>Transport proof</title><h1>Visible fixture</h1>
<a href="#result" onclick="document.querySelector('h1').textContent='Rendered SUCCESS'">Show result</a>
<h2>Drag pending</h2><a href="#source" draggable="true" ondragend="document.querySelector('h2').textContent='Dragged SUCCESS'">Move item</a>
<a href="#drop" style="display:inline-block;margin-left:100px;padding:40px" ondragover="event.preventDefault()">Drop zone</a>
<h3>Selection pending</h3><label>Multi choices <select multiple onchange="document.querySelector('h3').textContent=this.selectedOptions.length===0?'Selection cleared':'Selection changed'"><option value="a" selected>A</option><option value="b" selected>B</option></select></label>
<h4>Record retained</h4><button onclick="document.querySelector('h4').textContent='Record DELETED'">Delete record</button>
<h5>Upload pending</h5><label>File selection <input type="file" onchange="document.querySelector('h5').textContent='Uploaded '+this.files[0].name"></label>
<a href="/download" download="proof.txt">Download fixture</a>'''.encode()

@pytest_asyncio.fixture
async def local_page():
    async def respond(reader, writer):
        try:
            headers = await reader.readuntil(b"\r\n\r\n")
            download = headers.split(b"\r\n", 1)[0].split(b" ")[1] == b"/download"
            body = b"DOWNLOAD_FIXTURE_CONTENT" if download else HTML
            content_type = b"text/plain" if download else b"text/html; charset=utf-8"
            disposition = b"Content-Disposition: attachment; filename=proof.txt\r\n" if download else b""
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: " + content_type + b"\r\n" + disposition + b"Content-Length: " + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
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
    process = await asyncio.create_subprocess_exec(sys.executable, "-m", "browser_automation", "serve", stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=LINE_LIMIT)
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
        wait_request = {"id": "slow", "command": "act", "arguments": {**args, "action": {"observation_id": after["id"], "operation": "wait", "seconds": 10}}}
        process.stdin.write((json.dumps(wait_request) + "\n").encode())
        await process.stdin.drain()
        await asyncio.sleep(0.1)
        process.stdin.write((json.dumps({"command": "cancel", "arguments": {"request_id": "slow"}}) + "\n").encode())
        await process.stdin.drain()
        cancelled = json.loads(await asyncio.wait_for(process.stdout.readline(), 3))
        assert cancelled["id"] == "slow" and cancelled["error"]["code"] == "cancelled"
        assert (await cli_call(process, "tabs", {"session_id": sid}, "retained"))["tabs"]
        await cli_call(process, "close", {"session_id": sid}, 7)
    finally:
        process.stdin.close()
        await asyncio.wait_for(process.wait(), 15)
    assert process.returncode == 0, (await process.stderr.read()).decode()


async def test_cli_navigate_owned_tab_and_settle(tmp_path, local_page):
    process = await asyncio.create_subprocess_exec(sys.executable, "-m", "browser_automation", "serve", stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=LINE_LIMIT)
    async def failed(command, arguments, identifier):
        process.stdin.write((json.dumps({"id": identifier, "command": command, "arguments": arguments}) + "\n").encode())
        await process.stdin.drain()
        response = json.loads(await asyncio.wait_for(process.stdout.readline(), 30))
        assert response.get("id") == identifier and "error" in response, response
        return response["error"]
    try:
        sid = (await cli_call(process, "launch", {"headless": True}, 1))["session_id"]
        tab = (await cli_call(process, "new_tab", {"session_id": sid}, 2))["tab"]
        args = {"session_id": sid, "tab_id": tab["id"]}
        blank = await cli_call(process, "observe", {**args, "screenshot": True}, 3)
        moved = await cli_call(process, "navigate", {**args, "url": local_page}, 4)
        assert moved == {"tab": {"id": tab["id"], "url": local_page, "title": "Transport proof"}, "navigation_status": "complete", "wait_until": "domcontentloaded"}
        stale = await failed("act", {**args, "action": {"observation_id": blank["id"], "operation": "wait", "seconds": 0}}, 5)
        assert stale["code"] == "unknown_observation" and stale["recommended_next_action"] == "reobserve"
        secret = tmp_path / "secret.txt"
        secret.write_text("HOST_SECRET_NOT_BROWSER_CONTENT")
        denied = await failed("navigate", {**args, "url": secret.as_uri()}, 6)
        assert denied["code"] == "prohibited_url" and "HOST_SECRET" not in json.dumps(denied)
        obs = await cli_call(process, "observe", {**args, "screenshot": True}, 7)
        assert obs["url"] == local_page and "Visible fixture" in obs["text"]
        png = base64.b64decode(obs["screenshot"])
        assert png.startswith(b"\x89PNG\r\n\x1a\n") and obs["screenshot"] != blank["screenshot"]
        (tmp_path / "cli-navigated.png").write_bytes(png)
        target = next(e for e in obs["elements"] if e["name"] == "Show result")
        clicked = await cli_call(process, "act", {**args, "action": {"observation_id": obs["id"], "operation": "click", "target": target["id"]}}, 8)
        assert clicked["navigation"] == {"started": True, "status": "complete", "url": local_page + "#result"}
        after = await cli_call(process, "observe", {**args, "screenshot": True}, 9)
        assert "Rendered SUCCESS" in after["text"]
        (tmp_path / "cli-navigated-after.png").write_bytes(base64.b64decode(after["screenshot"]))
        await cli_call(process, "close", {"session_id": sid}, 10)
    finally:
        process.stdin.close()
        await asyncio.wait_for(process.wait(), 15)
    assert process.returncode == 0, (await process.stderr.read()).decode()


async def test_mcp_official_client_real_browser(tmp_path, local_page):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    approval_path = tmp_path / "approvals.json"
    approval_path.write_text("[]")
    allowed_directory = tmp_path / "allowed"
    allowed_directory.mkdir()
    params = StdioServerParameters(command=sys.executable, args=["-m", "browser_automation.mcp"], env={**os.environ, "BROWSER_APPROVALS_FILE": str(approval_path), "BROWSER_FILES_DIRECTORY": str(allowed_directory)})
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as client:
            initialized = await client.initialize()
            assert initialized.model_dump(by_alias=True)["serverInfo"]["name"] == "browser-automation"
            tools = await client.list_tools()
            assert {"observe", "act", "connect_default", "approved_act", "navigate"} <= {tool.name for tool in tools.tools}
            action_properties = next(t.model_dump(by_alias=True)["inputSchema"] for t in tools.tools if t.name == "act")["properties"]["action"]["properties"]
            assert {"to_target", "to_x", "to_y", "delta_x", "delta_y", "seconds", "settle_ms"} <= set(action_properties)
            navigate_schema = next(t.model_dump(by_alias=True)["inputSchema"] for t in tools.tools if t.name == "navigate")
            assert set(navigate_schema["required"]) == {"session_id", "tab_id", "url"}
            async def call(name, arguments):
                result = (await client.call_tool(name, arguments)).model_dump(by_alias=True)
                assert not result["isError"], result
                return result, json.loads(result["content"][0]["text"])
            _, opened = await call("launch", {"headless": True})
            sid = opened["session_id"]
            secret = tmp_path / "secret.txt"
            secret.write_text("HOST_SECRET_NOT_BROWSER_CONTENT")
            denied = (await client.call_tool("new_tab", {"session_id": sid, "url": secret.as_uri()})).model_dump(by_alias=True)
            assert denied["isError"] and "prohibited_url" in denied["content"][0]["text"] and "HOST_SECRET" not in denied["content"][0]["text"]
            _, tab_result = await call("new_tab", {"session_id": sid, "url": local_page})
            args = {"session_id": sid, "tab_id": tab_result["tab"]["id"]}
            result, obs = await call("observe", {**args, "screenshot": True})
            images = [block for block in result["content"] if block["type"] == "image"]
            assert len(images) == 1 and images[0]["mimeType"] == "image/png"
            assert "screenshot" not in obs
            (tmp_path / "mcp-before.png").write_bytes(base64.b64decode(images[0]["data"]))
            target = next(e for e in obs["elements"] if e["name"] == "Show result")
            await call("act", {**args, "action": {"observation_id": obs["id"], "operation": "click", "target": target["id"]}})
            result, after = await call("observe", {**args, "screenshot": True})
            assert "Rendered SUCCESS" in after["text"]
            (tmp_path / "mcp-after.png").write_bytes(base64.b64decode(next(b["data"] for b in result["content"] if b["type"] == "image")))
            source = next(e for e in after["elements"] if e["name"] == "Move item")
            destination = next(e for e in after["elements"] if e["name"] == "Drop zone")
            action = {"observation_id": after["id"], "operation": "drag", "target": source["id"], "to_target": destination["id"]}
            _, paused = await call("act", {**args, "action": action})
            assert paused["status"] == "approval_required"
            approval_path.write_text(json.dumps([{"token": "host-drag-fixture", "binding": paused["binding"], "expires_at": time.time() + 60}]))
            await call("approved_act", {**args, "observation_id": after["id"], "approval_token": "host-drag-fixture"})
            result, dragged = await call("observe", {**args, "screenshot": True})
            assert "Dragged SUCCESS" in dragged["text"]
            (tmp_path / "mcp-dragged.png").write_bytes(base64.b64decode(next(b["data"] for b in result["content"] if b["type"] == "image")))
            select = next(e for e in dragged["elements"] if "select" in e["operations"] and e.get("multiple"))
            await call("act", {**args, "action": {"observation_id": dragged["id"], "operation": "select", "target": select["id"], "value": []}})
            result, cleared = await call("observe", {**args, "screenshot": True})
            assert "Selection cleared" in cleared["text"]
            (tmp_path / "mcp-cleared.png").write_bytes(base64.b64decode(next(b["data"] for b in result["content"] if b["type"] == "image")))
            delete = next(e for e in cleared["elements"] if e["name"] == "Delete record")
            _, paused_delete = await call("act", {**args, "action": {"observation_id": cleared["id"], "operation": "click", "target": delete["id"]}})
            assert paused_delete["status"] == "approval_required"
            _, paused_enter = await call("act", {**args, "action": {"observation_id": cleared["id"], "operation": "press", "key": "Enter", "target": delete["id"]}})
            assert paused_enter["status"] == "approval_required"
            await call("act", {**args, "action": {"observation_id": cleared["id"], "operation": "press", "key": "Escape", "target": delete["id"]}})
            result, guarded = await call("observe", {**args, "screenshot": True})
            assert "Record retained" in guarded["text"] and "Record DELETED" not in guarded["text"]
            (tmp_path / "mcp-policy-guarded.png").write_bytes(base64.b64decode(next(b["data"] for b in result["content"] if b["type"] == "image")))
            upload_file = allowed_directory / "fixture.txt"
            upload_file.write_text("EXPLICITLY_APPROVED_UPLOAD_CONTENT")
            file_input = next(e for e in guarded["elements"] if e["name"] == "File selection")
            upload_args = {**args, "observation_id": guarded["id"], "target": file_input["id"], "paths": [str(upload_file)]}
            _, pending_upload = await call("upload", upload_args)
            assert pending_upload["status"] == "approval_required"
            approval_path.write_text(json.dumps([{"token": "host-upload-fixture", "binding": pending_upload["binding"], "expires_at": time.time() + 60}]))
            await call("upload", {**upload_args, "approval_token": "host-upload-fixture"})
            result, uploaded = await call("observe", {**args, "screenshot": True})
            assert "Uploaded fixture.txt" in uploaded["text"]
            (tmp_path / "mcp-uploaded.png").write_bytes(base64.b64decode(next(b["data"] for b in result["content"] if b["type"] == "image")))
            download_link = next(e for e in uploaded["elements"] if e["name"] == "Download fixture")
            destination = allowed_directory / "download.txt"
            download_args = {**args, "action": {"observation_id": uploaded["id"], "operation": "click", "target": download_link["id"]}, "destination": str(destination)}
            _, pending_download = await call("download", download_args)
            assert pending_download["status"] == "approval_required" and not destination.exists()
            approval_path.write_text(json.dumps([{"token": "host-download-fixture", "binding": pending_download["binding"], "expires_at": time.time() + 60}]))
            await call("download", {**download_args, "approval_token": "host-download-fixture"})
            assert destination.read_bytes() == b"DOWNLOAD_FIXTURE_CONTENT"
            failed = (await client.call_tool("tabs", {"session_id": "missing"})).model_dump(by_alias=True)
            assert failed["isError"] and "unknown_session" in failed["content"][0]["text"]
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
        process = await asyncio.create_subprocess_exec(sys.executable, "-m", "browser_automation", "run", "--isolated", "--headless", "--url", local_page, *flags, "Read the Visible fixture page", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env, limit=LINE_LIMIT)
        output, errors = await asyncio.wait_for(process.communicate(), 45)
    assert process.returncode == 0, errors.decode()
    result = json.loads(output)
    assert result["status"] == "success" and result["verification"]["satisfied"]
    assert "screenshot" not in result["observation"]
    assert {next(iter(request["questions"])) for request in requests} == {"next_action", "goal_verification"}

async def test_mcp_attached_browser_preserves_preexisting_tab(tmp_path, local_page):
    # Disposable real CDP browser stands in for a consented user's running Chrome.
    # It is not evidence of a logged-in third-party account or a live Chrome consent dialog.
    from playwright.async_api import async_playwright
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    import socket
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    async with async_playwright() as playwright:
        launch_env = dict(os.environ)
        library = os.environ.get("BROWSER_AGENT_LIBRARY_PATH") or os.path.expanduser("~/.local/lib/chromium/usr/lib/x86_64-linux-gnu")
        if os.path.isdir(library):
            launch_env["LD_LIBRARY_PATH"] = os.pathsep.join(filter(None, [library, launch_env.get("LD_LIBRARY_PATH")]))
        browser = await playwright.chromium.launch(headless=True, args=[f"--remote-debugging-port={port}"], env=launch_env)
        try:
            context = await browser.new_context()
            await context.add_cookies([{"name": "session", "value": "fixture-user", "url": local_page}])
            original = await context.new_page()
            await original.goto(local_page)
            await original.evaluate("document.querySelector('h1').textContent='CDP preexisting session'")
            params = StdioServerParameters(command=sys.executable, args=["-m", "browser_automation.mcp"])
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as client:
                    await client.initialize()
                    async def call(name, args):
                        result = (await client.call_tool(name, args)).model_dump(by_alias=True)
                        assert not result["isError"], result
                        return result, json.loads(result["content"][0]["text"])
                    _, opened = await call("connect", {"endpoint": f"http://127.0.0.1:{port}"})
                    assert opened["mode"] == "attached"
                    tab = next(tab for tab in opened["tabs"] if tab["url"] == local_page)
                    args = {"session_id": opened["session_id"], "tab_id": tab["id"]}
                    result, obs = await call("observe", {**args, "screenshot": True})
                    assert "CDP preexisting session" in obs["text"]
                    (tmp_path / "mcp-attached.png").write_bytes(base64.b64decode(next(block["data"] for block in result["content"] if block["type"] == "image")))
                    forbidden = (await client.call_tool("close_tab", args)).model_dump(by_alias=True)
                    assert forbidden["isError"], "Service must not close a preexisting user tab"
                    refused = (await client.call_tool("navigate", {**args, "url": local_page + "?moved"})).model_dump(by_alias=True)
                    assert refused["isError"] and refused["structuredContent"]["error"]["code"] == "not_owned_tab"
                    assert refused["structuredContent"]["error"]["recommended_next_action"] == "new_tab"
                    await call("close", {"session_id": opened["session_id"]})
            assert browser.is_connected() and not original.is_closed() and original.url == local_page
            assert await original.locator("h1").inner_text() == "CDP preexisting session"
            assert (await context.cookies())[0]["value"] == "fixture-user"
        finally:
            await browser.close()
