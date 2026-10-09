"""Surface contracts and opt-in real Chromium/official MCP progress consumers."""
import asyncio
import base64
from contextlib import asynccontextmanager
import json
import os
import sys

import pytest

from browser_automation.mcp import TOOLS, validate_arguments
from browser_automation.service import BrowserService, ServiceError


def test_bounded_surface_contracts():
    names = {name for name, _, _ in TOOLS}
    assert {f"{kind}_{op}" for kind in ("network", "websocket") for op in ("start", "list", "stop")} <= names
    validate_arguments("new_tab", {"session_id": "s", "wait_until": "commit", "timeout_ms": 150})
    validate_arguments("act", {"session_id": "s", "tab_id": "t", "action": {"observation_id": "r", "operation": "reload", "timeout_ms": 100}})
    for command, args in [("new_tab", {"session_id": "s", "timeout_ms": 0}), ("run", {"session_id": "s", "tab_id": "t", "goal": "x", "max_steps": 201}), ("network_start", {"session_id": "s", "tab_id": "t", "max_events": 4097}), ("observe", {"session_id": "s", "tab_id": "t", "recovery_policy": {}})]:
        with pytest.raises(ServiceError) as failure:
            validate_arguments(command, args)
        assert failure.value.code == "invalid_argument"


@pytest.mark.asyncio
async def test_missing_visual_credentials_preserve_dom(monkeypatch):
    from test_service import service_with_session
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    service, session = service_with_session()
    obs = await service.dispatch("observe", {"session_id": "s", "tab_id": "t", "interpret_visual": True})
    assert obs["text"] and obs["page_state"] and obs["visual_interpretation_error"]["message"]
    assert "visual_summary" not in obs
    await service.close()


@pytest.mark.asyncio
async def test_jsonl_progress_is_interim_and_request_local(monkeypatch, capsys):
    import io
    from browser_automation.cli import json_lines
    class Worker:
        async def dispatch(self, command, args, *, on_progress=None):
            if on_progress:
                await on_progress({"seq": 1, "status": "observe", "page_state": {"state": "ready", "source": "dom"}})
                await asyncio.sleep(0.01)
            return {"complete": command}
    request = {"id": "goal-request", "command": "run", "progress": True}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request) + "\n"))
    # Keep EOF from cancelling the simulated request until its final result.
    original = asyncio.to_thread
    async def read_line(fn):
        result = await original(fn)
        if not result:
            await asyncio.sleep(0.05)
        return result
    monkeypatch.setattr(asyncio, "to_thread", read_line)
    await json_lines(Worker())
    frames = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert frames[0]["id"] == frames[1]["id"] == "goal-request"
    assert "progress" in frames[0] and frames[1]["result"] == {"complete": "run"}


@asynccontextmanager
async def deterministic_fixture():
    """Explicit local HTTP provider, not live model quality evidence."""
    async def handle(reader, writer):
        try:
            headers = (await reader.readuntil(b"\r\n\r\n")).decode()
            path = headers.split(" ")[1]
            if path == "/decisions":
                length = next(int(line.split(":", 1)[1]) for line in headers.split("\r\n") if line.lower().startswith("content-length:"))
                request = json.loads(await reader.readexactly(length))
                name, question = next(iter(request["questions"].items()))
                choices = question["criteria"]
                choice = "satisfied" if name == "goal_verification" else next(key for key, value in choices.items() if json.loads(value)["action"]["operation"] == "done")
                await asyncio.sleep(0.3)
                body = json.dumps({"answers": {name: {"type": "choice", "choice": choice, "confidence": 1.0, "probabilities": {key: float(key == choice) for key in choices}}}}).encode()
                mime = b"application/json"
            elif path == "/unavailable":
                body, mime = b"Unavailable", b"text/plain"
            else:
                body = b'<!doctype html><title>Agent-native proof</title><h1>Visible READY</h1><button onclick="fetch(\'/unavailable\')">Fetch status</button>'
                mime = b"text/html"
            status = b"503 Service Unavailable" if path == "/unavailable" else b"200 OK"
            writer.write(b"HTTP/1.1 " + status + b"\r\nContent-Type: " + mime + b"\r\nContent-Length: " + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    async with server:
        yield f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("BROWSER_INTEGRATION_TESTS") != "1", reason="Coordinated real browser verification")
@pytest.mark.parametrize("transport", ["stdio", "2026-07-28", "legacy"])
async def test_official_sdk_incremental_progress_real_browser(transport, monkeypatch, tmp_path):
    from mcp import Client, StdioServerParameters
    from test_mcp_http_browser import http_server, client_for
    async with deterministic_fixture() as origin:
        monkeypatch.setenv("OPENROUTER_API_KEY", "local-deterministic-fixture-not-live")
        monkeypatch.setenv("BROWSER_AGENT_DECISIONS_ENDPOINT", origin + "/decisions")
        monkeypatch.setenv("BROWSER_AGENT_VISION", "false")
        @asynccontextmanager
        async def connection():
            if transport == "stdio":
                async with Client(StdioServerParameters(command=sys.executable, args=["-m", "browser_automation.mcp"], env=dict(os.environ)), mode="legacy") as client:
                    yield client
            else:
                async with http_server() as (url, _), client_for(url, mode=transport) as client:
                    yield client
        async with connection() as client:
            async def call(name, args):
                result = await client.call_tool(name, args)
                assert not result.is_error, result
                return result.structured_content
            opened = await call("launch", {"headless": True})
            sid = opened["session_id"]
            created = await call("new_tab", {"session_id": sid, "url": origin, "wait_until": "domcontentloaded", "timeout_ms": 3000})
            args = {"session_id": sid, "tab_id": created["tab"]["id"]}
            observed = await client.call_tool("observe", {**args, "screenshot": True})
            assert not observed.is_error
            obs = observed.structured_content
            assert "Visible READY" in obs["text"] and obs["page_state"]["source"] == "dom"
            image = next(block.data for block in observed.content if block.type == "image")
            (tmp_path / f"{transport}-independent.png").write_bytes(base64.b64decode(image))
            await call("network_start", {**args, "max_events": 16})
            target = next(e["id"] for e in obs["elements"] if e["name"] == "Fetch status")
            await call("act", {**args, "action": {"observation_id": obs["id"], "operation": "click", "target": target}})
            await asyncio.sleep(0.2)
            events = await call("network_list", args)
            assert any(event.get("status") == 503 for event in events["events"]), events
            await call("network_stop", args)
            fresh = await call("observe", args)
            paused = await call("act", {**args, "action": {"observation_id": fresh["id"], "operation": "reload"}})
            assert paused["status"] == "approval_required"
            received = asyncio.Event()
            progress = []
            async def update(value, total, message):
                progress.append((value, total, json.loads(message)))
                received.set()
            task = asyncio.create_task(client.call_tool("run", {**args, "goal": "Read the visible READY heading", "max_steps": 3, "screenshot": False}, progress_callback=update))
            await asyncio.wait_for(received.wait(), 10)
            assert not task.done(), "Progress must arrive before final result"
            result = await asyncio.wait_for(task, 20)
            assert not result.is_error and result.structured_content["status"] == "success", result
            assert progress and all(b[0] > a[0] for a, b in zip(progress, progress[1:]))
            assert all(total is None for _, total, _ in progress)
            assert "screenshot" not in json.dumps(progress)
            await call("close", {"session_id": sid})
