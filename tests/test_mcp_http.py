"""Consumer-visible HTTP boundaries, using the SDK app (no browser mocks)."""
import asyncio
import json

import httpx
import pytest

pytest.importorskip("mcp")
from browser_automation.mcp_http import create_app, credentials_from_env

TOKEN_A = "fixture-principal-a-0123456789"
TOKEN_B = "fixture-principal-b-0123456789"
AUTH = {"Authorization": "Bearer " + TOKEN_A, "Accept": "application/json, text/event-stream"}


def initialize(identifier=1):
    return {"jsonrpc": "2.0", "id": identifier, "method": "initialize", "params": {
        "protocolVersion": "2025-11-25", "capabilities": {},
        "clientInfo": {"name": "boundary-fixture", "version": "1"}}}


def response_json(response):
    if response.headers.get("content-type", "").startswith("text/event-stream"):
        return json.loads(next(line[6:] for line in response.text.splitlines() if line.startswith("data: ")))
    return response.json()


@pytest.mark.asyncio
async def test_every_route_security_and_body_bounds():
    app = create_app(credentials={"a": TOKEN_A, "b": TOKEN_B}, max_request_body_size=512)
    async with app.app.router.lifespan_context(app.app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:8767") as http:
            for route in ("/mcp", "/unknown"):
                assert (await http.post(route, json=initialize())).status_code == 401
                assert (await http.post(route, json=initialize(), headers={"Authorization": "Bearer wrong"})).status_code == 401
                assert (await http.post(route, json=initialize(), headers={**AUTH, "Origin": "https://evil.example"})).status_code == 403
                assert (await http.post(route, json=initialize(), headers={**AUTH, "Host": "evil.example"})).status_code == 421
            oversized = await http.post("/mcp", content=b"x" * 513, headers={**AUTH, "Content-Type": "application/json"})
            assert oversized.status_code == 413
            invalid = await http.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                                      headers={**AUTH, "Mcp-Protocol-Version": "2099-01-01", "Mcp-Method": "tools/list"})
            assert invalid.status_code == 400
            spoof = await http.post("/mcp", json=initialize(), headers={**AUTH, "Host": "evil.example", "X-Forwarded-Host": "127.0.0.1:8767"})
            assert spoof.status_code == 421


@pytest.mark.asyncio
async def test_legacy_owner_delete_expiry_and_capacity():
    app = create_app(credentials={"a": TOKEN_A, "b": TOKEN_B}, max_sessions=2, session_idle_timeout=0.2)
    async with app.app.router.lifespan_context(app.app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:8767") as http:
            first = await http.post("/mcp", json=initialize(), headers=AUTH)
            assert first.status_code == 200, first.text
            assert "run" in response_json(first)["result"]["instructions"]
            sid = first.headers["mcp-session-id"]
            second = await http.post("/mcp", json=initialize(2), headers=AUTH)
            other = second.headers["mcp-session-id"]
            assert (await http.post("/mcp", json=initialize(3), headers=AUTH)).status_code == 503
            hijack = await http.delete("/mcp", headers={**AUTH, "Authorization": "Bearer " + TOKEN_B, "Mcp-Session-Id": sid})
            assert hijack.status_code == 404
            assert sid in app.registry.legacy_owners
            assert (await http.delete("/mcp", headers={**AUTH, "Mcp-Session-Id": sid})).status_code == 200
            assert sid not in app.registry.legacy_owners and other in app.registry.legacy_owners
            await asyncio.sleep(0.5)
            assert (await http.get("/mcp", headers={**AUTH, "Mcp-Session-Id": other})).status_code == 404
            assert not app.registry.entries


def test_fail_closed_configuration(monkeypatch):
    monkeypatch.delenv("BROWSER_MCP_TOKEN", raising=False)
    monkeypatch.delenv("BROWSER_MCP_TOKENS", raising=False)
    with pytest.raises(ValueError, match="exactly one"):
        credentials_from_env()
    monkeypatch.setenv("BROWSER_MCP_TOKEN", "short")
    with pytest.raises(ValueError, match="16"):
        credentials_from_env()
    monkeypatch.delenv("BROWSER_MCP_TOKEN")
    monkeypatch.setenv("BROWSER_MCP_TOKENS", json.dumps({"a": TOKEN_A, "b": TOKEN_A}))
    with pytest.raises(ValueError, match="distinct"):
        credentials_from_env()
    with pytest.raises(ValueError, match="allow-host"):
        create_app(credentials={"a": TOKEN_A}, host="0.0.0.0")
    with pytest.raises(ValueError, match="positive"):
        create_app(credentials={"a": TOKEN_A}, max_sessions=0)


@pytest.mark.asyncio
async def test_explicit_reverse_proxy_authority_without_trusting_forwarded_headers():
    app = create_app(credentials={"a": TOKEN_A}, allow_hosts=["browser.example"], allow_origins=["https://agent.example"])
    async with app.app.router.lifespan_context(app.app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://browser.example") as http:
            good = await http.post("/mcp", json=initialize(), headers={**AUTH, "Origin": "https://agent.example"})
            assert good.status_code == 200
            assert (await http.post("/mcp", json=initialize(), headers={**AUTH, "Host": "attacker.example", "X-Forwarded-Host": "browser.example"})).status_code == 421
