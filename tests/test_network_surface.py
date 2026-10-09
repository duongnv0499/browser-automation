"""Real HTTP ground truth through public transports; no live account or model calls."""
import asyncio
import base64
from contextlib import asynccontextmanager
import json
import os
import sys
import time
from urllib.parse import urlsplit

import pytest

from browser_automation.mcp import TOOLS, validate_arguments
from browser_automation.service import ServiceError

NETWORK_TOOLS = {"network_start_many", "network_list_many", "network_stop_many", "network_detail", "network_body", "network_call", "network_replay", "network_execute"}


def test_network_catalog_and_host_only_options():
    names = {name for name, _, _ in TOOLS}
    assert NETWORK_TOOLS <= names
    assert len(names) == len(TOOLS) == 29
    validate_arguments("network_call", {"session_id": "s", "tab_id": "t", "url": "https://example.org/api", "method": "PROPFIND", "prepare_only": True})
    validate_arguments("network_replay", {"session_id": "s", "tab_id": "t", "request_id": "r", "method": "GET", "body": None})
    for option in ("approved", "approval_required", "allow_sensitive", "executable_path"):
        with pytest.raises(ServiceError):
            validate_arguments("network_call", {"session_id": "s", "tab_id": "t", "url": "https://example.org", option: True})
    validate_arguments("network_start_many", {"session_id": "s", "include_new_tabs": True})
    with pytest.raises(ServiceError):
        validate_arguments("network_execute", {"session_id": "s", "plan_id": "p"})


@asynccontextmanager
async def network_site():
    seen = []
    async def handle(reader, writer):
        try:
            raw = await reader.readuntil(b"\r\n\r\n")
            lines = raw.decode("latin1").split("\r\n")
            method, target, _ = lines[0].split(" ")
            headers = dict(line.split(": ", 1) for line in lines[1:] if ": " in line)
            lowered = {key.lower(): value for key, value in headers.items()}
            body = await reader.readexactly(int(lowered.get("content-length", "0")))
            seen.append({"method": method, "target": target, "headers": lowered, "body": body})
            path = urlsplit(target).path
            extra = b""
            status = b"200 OK"
            if path == "/":
                content = b'''<!doctype html><title>Network surface fixture</title><h1>Quiet UI</h1>
<a href="#traffic" onclick="fetch('/api?ordinary=visible&token=fixture-private-query',{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer fixture-private-auth','X-CSRF-Token':'fixture-private-csrf'},body:JSON.stringify({operationName:'Lookup',variables:{ordinary:'payload-visible',password:'fixture-private-body'}})})">Fetch records</a>
<a href="/" target="_blank">Open popup</a>'''
                mime = b"text/html"
                extra = b"Set-Cookie: fixture_session=private-cookie; HttpOnly; SameSite=Lax; Path=/\r\n"
            elif path == "/binary":
                content, mime = bytes(range(256)) * 8, b"application/octet-stream"
            else:
                content = json.dumps({"ordinary": "response-visible", "token": "fixture-private-response", "padding": "x" * 70000}).encode()
                mime = b"application/json"
                extra = b"Set-Cookie: updated=jar-updated; HttpOnly; Path=/\r\nSet-Cookie: second=two; Path=/\r\n"
                if path == "/503":
                    status = b"503 Service Unavailable"
            writer.write(b"HTTP/1.1 " + status + b"\r\nContent-Type: " + mime + b"\r\n" + extra + b"Content-Length: " + str(len(content)).encode() + b"\r\nConnection: close\r\n\r\n" + content)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    async with server:
        yield f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}", seen


@asynccontextmanager
async def public_transport(transport):
    if transport == "cli":
        process = await asyncio.create_subprocess_exec(sys.executable, "-m", "browser_automation", "serve", stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        sequence = 0
        async def call(name, args):
            nonlocal sequence
            sequence += 1
            process.stdin.write((json.dumps({"id": sequence, "command": name, "arguments": args}) + "\n").encode())
            await process.stdin.drain()
            response = json.loads(await asyncio.wait_for(process.stdout.readline(), 30))
            assert response["id"] == sequence
            return response.get("result", {"error": response.get("error")})
        try:
            yield call, None
        finally:
            process.stdin.close()
            output, errors = await asyncio.wait_for(process.communicate(), 20)
            assert process.returncode == 0, errors.decode()
    else:
        from mcp import Client, StdioServerParameters
        from test_mcp_http_browser import http_server, client_for
        @asynccontextmanager
        async def connection():
            if transport == "stdio":
                async with Client(StdioServerParameters(command=sys.executable, args=["-m", "browser_automation.mcp"], env=dict(os.environ)), mode="legacy") as client:
                    yield client, None
            else:
                async with http_server() as (url, _), client_for(url, mode=transport) as client:
                    yield client, url
        async with connection() as (client, url):
            async def call(name, args):
                result = await client.call_tool(name, args)
                data = dict(result.structured_content)
                if name == "observe" and args.get("screenshot"):
                    image = next((block.data for block in result.content if block.type == "image"), None)
                    if image:
                        data["screenshot"] = image
                return data
            assert NETWORK_TOOLS <= {tool.name for tool in (await client.list_tools()).tools}
            yield call, url


async def successful(call, name, args):
    result = await call(name, args)
    assert "error" not in result, result
    return result


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("BROWSER_INTEGRATION_TESTS") != "1", reason="Coordinated real browser verification")
@pytest.mark.parametrize("transport", ["stdio", "2026-07-28", "legacy", "cli"])
async def test_real_network_public_workflow(transport, monkeypatch, tmp_path):
    approvals = tmp_path / "approvals.json"
    approvals.write_text("[]")
    monkeypatch.setenv("BROWSER_APPROVALS_FILE", str(approvals))
    monkeypatch.delenv("BROWSER_NETWORK_SENSITIVE", raising=False)
    monkeypatch.delenv("BROWSER_NETWORK_REQUIRE_APPROVAL", raising=False)
    async with network_site() as (origin, seen), public_transport(transport) as (call, http_url):
        opened = await successful(call, "launch", {"headless": True})
        sid = opened["session_id"]
        first = (await successful(call, "new_tab", {"session_id": sid, "url": origin}))["tab"]["id"]
        second = (await successful(call, "new_tab", {"session_id": sid, "url": origin}))["tab"]["id"]
        args = {"session_id": sid, "tab_id": first}
        await successful(call, "network_start_many", {"session_id": sid, "tab_ids": [first, second], "include_new_tabs": True})
        observed = await successful(call, "observe", args)
        target = next(e for e in observed["elements"] if e["name"] == "Fetch records")
        await successful(call, "act", {**args, "action": {"observation_id": observed["id"], "operation": "click", "target": target["id"]}})
        for _ in range(100):
            events = await successful(call, "network_list", args)
            match = next((e for e in events["events"] if e["event"] == "requestfinished" and "/api" in e["url"]), None)
            if match:
                break
            await asyncio.sleep(0.025)
        assert match, events
        request_id = match["request_id"]
        detail = await successful(call, "network_detail", {**args, "request_id": request_id})
        exported = json.dumps(detail)
        assert "visible" in exported and "fixture-private" not in exported
        denied = await call("network_detail", {**args, "request_id": request_id, "include_sensitive": True})
        assert denied["error"]["code"] == "host_policy_required"
        chunk = await successful(call, "network_body", {**args, "request_id": request_id, "part": "response", "limit": 1024})
        assert chunk["next_offset"] is not None and chunk["total_bytes"] > 1024
        assert "fixture-private-response" not in json.dumps(chunk)
        second_args = {"session_id": sid, "tab_id": second}
        observed_second = await successful(call, "observe", second_args)
        second_target = next(e for e in observed_second["elements"] if e["name"] == "Fetch records")
        await successful(call, "act", {**second_args, "action": {"observation_id": observed_second["id"], "operation": "click", "target": second_target["id"]}})
        for _ in range(100):
            other_events = await successful(call, "network_list", second_args)
            if any(e["event"] == "requestfinished" and "/api" in e["url"] for e in other_events["events"]):
                break
            await asyncio.sleep(0.025)
        assert any(e["event"] == "requestfinished" and "/api" in e["url"] for e in other_events["events"])
        assert request_id not in {e["request_id"] for e in other_events["events"]}
        observed = await successful(call, "observe", args)
        popup_target = next(e for e in observed["elements"] if e["name"] == "Open popup")
        await successful(call, "act", {**args, "action": {"observation_id": observed["id"], "operation": "click", "target": popup_target["id"]}})
        for _ in range(100):
            many = await successful(call, "network_list_many", {"session_id": sid})
            if len(many["captures"]) >= 3:
                break
            await asyncio.sleep(0.025)
        assert len(many["captures"]) >= 3 and not many["failures"]
        before = len(seen)
        paused = await successful(call, "network_replay", {**args, "request_id": request_id, "target_tab_id": second, "json_body": {"ordinary": "edited"}})
        assert paused["status"] == "approval_required" and len(seen) == before
        assert "fixture-private" not in json.dumps(paused)
        preview = paused["binding"]["body_preview"]
        assert json.loads(preview["data"]) == {"ordinary": "edited"}
        assert preview["encoding"] == "utf-8" and preview["total_bytes"] > 0
        assert paused["binding"]["headers_preview"]
        assert "private-cookie" not in json.dumps(paused["binding"])
        execute = {"session_id": sid, "plan_id": paused["plan_id"], "approval_token": "host-exact"}
        rejected = await call("network_execute", execute)
        assert rejected["error"]["code"] == "approval_required" and len(seen) == before
        approvals.write_text(json.dumps([{"token": "host-exact", "binding": paused["binding"], "expires_at": time.time() + 60}]))
        replayed = await successful(call, "network_execute", execute)
        assert replayed["provenance"] == "browser_context_api_request"
        replay_body = await successful(call, "network_body", {"session_id": sid, "tab_id": second, "request_id": replayed["request_id"], "limit": 1024})
        assert "response-visible" in replay_body["data"] and "fixture-private-response" not in replay_body["data"]
        assert len(seen) == before + 1
        sent = seen[-1]
        assert sent["method"] == "POST" and json.loads(sent["body"]) == {"ordinary": "edited"}
        assert sent["headers"]["authorization"] == "Bearer fixture-private-auth"
        assert "fixture_session=private-cookie" in sent["headers"]["cookie"]
        assert "error" in await call("network_execute", execute)
        cleared = await successful(call, "network_replay", {**args, "request_id": request_id, "method": "GET", "body": None, "prepare_only": True})
        before = len(seen)
        approvals.write_text(json.dumps([{"token": "host-clear", "binding": cleared["binding"], "expires_at": time.time() + 60}]))
        await successful(call, "network_execute", {"session_id": sid, "plan_id": cleared["plan_id"], "approval_token": "host-clear"})
        assert len(seen) == before + 1 and seen[-1]["method"] == "GET" and seen[-1]["body"] == b""
        original_body = await successful(call, "network_body", {**args, "request_id": request_id, "part": "request"})
        assert "payload-visible" in original_body["data"], "Replay must not mutate the captured POST body"
        before = len(seen)
        safe = await successful(call, "network_call", {**args, "url": origin + "/503"})
        assert safe["provenance"] == "browser_context_api_request" and len(seen) == before + 1
        assert safe["status"] == 503
        assert seen[-1]["target"] == "/503" and "updated=jar-updated" in seen[-1]["headers"]["cookie"]
        binary = await successful(call, "network_call", {**args, "url": origin + "/binary"})
        data = await successful(call, "network_body", {**args, "request_id": binary["request_id"], "limit": 2048})
        assert data["encoding"] == "base64"
        assert base64.b64decode(data["data"]) == bytes(range(256)) * 8
        before = len(seen)
        planned = await successful(call, "network_call", {**args, "url": origin + "/api", "prepare_only": True})
        assert planned["plan_id"] and len(seen) == before
        await successful(call, "network_stop", args)
        await successful(call, "network_start", args)
        expired = await call("network_replay", {**args, "request_id": request_id})
        assert "error" in expired and len(seen) == before
        if http_url:
            from test_mcp_http_browser import client_for, TOKEN_B
            async with client_for(http_url, TOKEN_B, mode=transport) as other:
                forbidden = await other.call_tool("network_execute", execute)
                assert forbidden.is_error and forbidden.structured_content["error"]["code"] == "unknown_session"
        await successful(call, "network_list_many", {"session_id": sid})
        await successful(call, "network_stop_many", {"session_id": sid})
        visible = await successful(call, "observe", {**args, "screenshot": True})
        assert "Quiet UI" in visible["text"], "API response does not render a fabricated UI outcome"
        (tmp_path / f"network-{transport}-independent.png").write_bytes(base64.b64decode(visible["screenshot"]))
        (tmp_path / f"network-{transport}-groundtruth.json").write_text(json.dumps({
            "transport": transport, "catalog_tools": 29, "capture_id": events["capture_id"],
            "request_id": request_id, "captured_tab_ids": [capture["tab_id"] for capture in many["captures"]],
            "captured_ordinary_query_present": "visible" in exported,
            "default_private_fields_absent": "fixture-private" not in exported,
            "response_total_bytes": chunk["total_bytes"], "next_offset": chunk["next_offset"],
            "edited_replay_method": sent["method"], "edited_replay_body": json.loads(sent["body"]),
            "replay_response_provenance": replayed["provenance"], "api_response_text": replay_body["data"],
            "safe_read_http_status": safe["status"], "binary_bytes": len(base64.b64decode(data["data"])),
            "server_observed_requests": [{"method": item["method"], "path": urlsplit(item["target"]).path, "body_bytes": len(item["body"])} for item in seen],
            "capture_restart_replay_error": expired["error"]["code"], "rendered_heading": "Quiet UI",
        }, indent=2))
        await successful(call, "close", {"session_id": sid})


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("BROWSER_INTEGRATION_TESTS") != "1", reason="Coordinated real browser verification")
@pytest.mark.parametrize("transport", ["stdio", "2026-07-28", "legacy", "cli"])
async def test_sensitive_host_policy_and_foreign_approval(transport, monkeypatch, tmp_path):
    approvals = tmp_path / "approvals.json"
    approvals.write_text("[]")
    monkeypatch.setenv("BROWSER_APPROVALS_FILE", str(approvals))
    monkeypatch.setenv("BROWSER_NETWORK_SENSITIVE", "1")
    async with network_site() as (origin, seen), network_site() as (foreign, foreign_seen), public_transport(transport) as (call, _):
        sid = (await successful(call, "launch", {"headless": True}))["session_id"]
        tab = (await successful(call, "new_tab", {"session_id": sid, "url": origin}))["tab"]["id"]
        args = {"session_id": sid, "tab_id": tab}
        await successful(call, "network_start", args)
        observed = await successful(call, "observe", args)
        target = next(e for e in observed["elements"] if e["name"] == "Fetch records")
        await successful(call, "act", {**args, "action": {"observation_id": observed["id"], "operation": "click", "target": target["id"]}})
        for _ in range(100):
            events = await successful(call, "network_list", args)
            match = next((e for e in events["events"] if e["event"] == "requestfinished" and "/api" in e["url"]), None)
            if match:
                break
            await asyncio.sleep(0.025)
        assert match, events
        request_id = match["request_id"]
        exposed = await successful(call, "network_detail", {**args, "request_id": request_id, "include_sensitive": True})
        assert "fixture-private-query" in json.dumps(exposed) and "fixture-private-auth" in json.dumps(exposed)
        body = await successful(call, "network_body", {**args, "request_id": request_id, "part": "request", "include_sensitive": True})
        assert "fixture-private-body" in body["data"]
        paused = await successful(call, "network_replay", {**args, "request_id": request_id, "url": foreign + "/api", "body": "approved body"})
        assert paused["approval_required"] and not foreign_seen
        assert "fixture-private" not in json.dumps(paused)
        execute = {"session_id": sid, "plan_id": paused["plan_id"], "approval_token": "foreign-host"}
        approvals.write_text(json.dumps([{"token": "foreign-host", "binding": paused["binding"], "expires_at": time.time() - 1}]))
        denied = await call("network_execute", execute)
        assert denied["error"]["code"] == "approval_required" and not foreign_seen
        wrong = {**paused["binding"], "method": "DELETE"}
        approvals.write_text(json.dumps([{"token": "foreign-host", "binding": wrong, "expires_at": time.time() + 60}]))
        assert "error" in await call("network_execute", execute)
        assert not foreign_seen
        approvals.write_text(json.dumps([{"token": "foreign-host", "binding": paused["binding"], "expires_at": time.time() + 60}]))
        await successful(call, "network_execute", execute)
        assert len(foreign_seen) == 1 and foreign_seen[0]["body"] == b"approved body"
        assert "authorization" not in foreign_seen[0]["headers"]
        assert "x-csrf-token" not in foreign_seen[0]["headers"]
        (tmp_path / f"network-{transport}-foreign-groundtruth.json").write_text(json.dumps({
            "transport": transport, "sensitive_query_visible_with_host_policy": "fixture-private-query" in json.dumps(exposed),
            "sensitive_request_body_visible_with_host_policy": "fixture-private-body" in body["data"],
            "expired_host_token_error": denied["error"]["code"], "foreign_requests_after_approval": len(foreign_seen),
            "foreign_method": foreign_seen[0]["method"], "foreign_body": foreign_seen[0]["body"].decode(),
            "captured_authorization_forwarded": "authorization" in foreign_seen[0]["headers"],
            "captured_csrf_forwarded": "x-csrf-token" in foreign_seen[0]["headers"],
        }, indent=2))
        await successful(call, "close", {"session_id": sid})
