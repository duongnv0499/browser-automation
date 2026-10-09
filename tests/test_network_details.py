"""Real browser passive network data consumers; no route interception."""
import asyncio
import base64
import json
from contextlib import asynccontextmanager

import pytest

from browser_automation.browser import BrowserSession
from browser_automation.browser_monitor import TrafficMonitor
from browser_automation.browser_network_data import body_chunk, safe_body, safe_headers, safe_url

BINARY = bytes(range(256)) * 1024


@asynccontextmanager
async def detail_site():
    tasks, writers = set(), set()

    async def serve(reader, writer):
        tasks.add(asyncio.current_task())
        writers.add(writer)
        try:
            raw = await reader.readuntil(b"\r\n\r\n")
            lines = raw.decode().split("\r\n")
            path = lines[0].split()[1].split("?", 1)[0]
            headers = dict((k.lower(), v.strip()) for line in lines[1:] if ":" in line for k, v in [line.split(":", 1)])
            payload = await reader.readexactly(int(headers.get("content-length", "0")))
            status, extra, content_type = "200 OK", "", "application/json"
            if path == "/":
                content_type = "text/html"
                body = b'''<h1>Quiet fixture</h1><button id="go" onclick="fetch('/json?q=ordinary&token=query-private',{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer fixture-private'},body:JSON.stringify({query:'query Item { item }',variables:{id:42,token:'body-private'}})});fetch('/form',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:'item=ordinary&password=form-private'});fetch('/binary');fetch('/503');fetch('/redirect');fetch('http://127.0.0.1:1/fail').catch(()=>{});fetch('/stream').catch(()=>{})">Run</button>'''
                extra = "Set-Cookie: fixture_session=cookie-private; HttpOnly; Path=/\r\n"
            elif path == "/binary":
                body, content_type = BINARY, "application/octet-stream"
            elif path == "/redirect":
                body, status, extra = b"", "302 Found", "Location: /503\r\n"
            elif path == "/stream":
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\ndata: waiting\n\n")
                await writer.drain()
                await asyncio.sleep(60)
                return
            else:
                if path == "/503":
                    status = "503 Service Unavailable"
                body = json.dumps({"payload": payload.decode(), "ordinary": "visible", "token": "response-private"}).encode()
                extra = "Set-Cookie: one=first-private; Path=/\r\nSet-Cookie: two=second-private; Path=/\r\n"
            writer.write(f"HTTP/1.1 {status}\r\nContent-Type: {content_type}\r\n{extra}Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writers.discard(writer)
            writer.close()
            await writer.wait_closed()
            tasks.discard(asyncio.current_task())

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    try:
        yield f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    finally:
        server.close()
        await server.wait_closed()
        for writer in tuple(writers):
            writer.close()
        for task in tuple(tasks):
            task.cancel()
        await asyncio.gather(*tuple(tasks), return_exceptions=True)


async def completed(monitor, tab):
    for _ in range(150):
        events = monitor.list(tab, "network", limit=1000)["events"]
        if any(e["event"] == "requestfinished" and e["url"].endswith("/binary") for e in events) and any(e["event"] == "requestfailed" for e in events) and any(e["event"] == "response" and e["url"].endswith("/stream") for e in events):
            return {e["url"].rsplit("/", 1)[-1]: e["request_id"] for e in events}
        await asyncio.sleep(.02)
    raise AssertionError("Expected fixture traffic did not arrive")


@pytest.mark.asyncio
async def test_real_details_two_tabs_raw_replay_and_generation(monkeypatch):
    monkeypatch.setenv("BROWSER_NETWORK_BODY_TIMEOUT_MS", "150")
    monkeypatch.delenv("BROWSER_NETWORK_SENSITIVE", raising=False)
    async with detail_site() as url, await BrowserSession.launch(headless=True) as browser:
        tabs = [(await browser.new_tab(url))["id"] for _ in range(2)]
        monitor = TrafficMonitor()
        try:
            for tab in tabs:
                await monitor.start(browser._page(tab), tab, max_events=100)
                await browser._page(tab).click("#go")
            records = [await completed(monitor, tab) for tab in tabs]
            assert records[0]["json"] != records[1]["json"]
            tab, ids = tabs[0], records[0]
            detail = await monitor.request_detail(tab, ids["json"])
            assert "ordinary" in detail["url"] and "query-private" not in json.dumps(detail)
            assert detail["method"] == "POST"
            assert len([h for h in detail["response_headers"] if h["name"].lower() == "set-cookie"]) == 2
            assert "fixture-private" not in json.dumps(detail) and "cookie-private" not in json.dumps(detail)
            body = await monitor.request_body(tab, ids["json"], part="request")
            parsed = json.loads(body["data"])
            assert parsed["variables"] == {"id": 42, "token": "[redacted]"}
            form = await monitor.request_body(tab, ids["form"], part="request")
            assert "ordinary" in form["data"] and "form-private" not in form["data"]
            source = await monitor.replay_source(tab, ids["json"])
            assert source["url"].endswith("q=ordinary&token=query-private")
            assert json.loads(source["body"])["variables"]["token"] == "body-private"
            assert any(h["name"].lower() == "authorization" and "fixture-private" in h["value"] for h in source["headers"])
            with pytest.raises(TypeError):
                source["method"] = "DELETE"
            with pytest.raises(ValueError, match="BROWSER_NETWORK_SENSITIVE"):
                await monitor.request_detail(tab, ids["json"], include_sensitive=True)
            monkeypatch.setenv("BROWSER_NETWORK_SENSITIVE", "1")
            raw = await monitor.request_body(tab, ids["json"], part="request", include_sensitive=True)
            assert "body-private" in raw["data"]
            offset, result = 0, bytearray()
            while True:
                chunk = await monitor.request_body(tab, ids["binary"], offset=offset, limit=7777)
                result.extend(base64.b64decode(chunk["data"]) if chunk["encoding"] == "base64" else chunk["data"].encode())
                if chunk["next_offset"] is None:
                    break
                offset = chunk["next_offset"]
            assert bytes(result) == BINARY
            assert chunk["total_bytes"] == len(BINARY) and chunk["source_complete"]
            assert (await monitor.request_detail(tab, ids["503"], fields=["status"]))["status"] == 503
            assert (await monitor.request_body(tab, ids["redirect"]))["unavailable_reason"] == "redirect_body_unavailable"
            assert (await monitor.request_body(tab, ids["fail"]))["unavailable_reason"] == "no_response"
            assert (await monitor.request_body(tab, ids["stream"]))["unavailable_reason"] == "timeout"
            first = monitor.list(tab, "network")["capture_id"]
            await monitor.stop(tab, "network")
            await monitor.start(browser._page(tab), tab)
            assert monitor.list(tab, "network")["capture_id"] != first
            with pytest.raises(ValueError, match="generation"):
                await monitor.replay_source(tab, ids["json"])
        finally:
            await monitor.close()
        assert not monitor._body_cache and monitor._body_bytes == 0


@pytest.mark.asyncio
async def test_real_body_limit_and_eviction(monkeypatch):
    monkeypatch.setenv("BROWSER_NETWORK_BODY_LIMIT", "64")
    monkeypatch.setenv("BROWSER_NETWORK_CACHE_LIMIT", "64")
    async with detail_site() as url, await BrowserSession.launch(headless=True) as browser:
        tab = (await browser.new_tab(url))["id"]
        monitor = TrafficMonitor()
        try:
            await monitor.start(browser._page(tab), tab)
            await browser._page(tab).click("#go")
            ids = await completed(monitor, tab)
            binary = await monitor.request_body(tab, ids["binary"])
            assert binary["retained_bytes"] == 64 and binary["total_bytes"] == len(BINARY)
            assert binary["truncated"] and binary["unavailable_reason"] == "body_limit"
            await monitor.request_body(tab, ids["503"])
            evicted = await monitor.request_body(tab, ids["binary"])
            assert evicted["data"] is None and evicted["unavailable_reason"] == "body_evicted"
            assert monitor._body_bytes <= 64
        finally:
            await monitor.close()


def test_structured_redaction_and_byte_chunks():
    assert "visible" in safe_url("https://example.test/a?q=visible&token=private")
    assert "private" not in safe_url("https://example.test/a?q=visible&token=private")
    assert safe_headers([{"name": "Authorization", "value": "private"}])[0]["value"] == "[redacted]"
    assert json.loads(safe_body(b'{"ordinary":42,"password":"private"}', "application/json"))["ordinary"] == 42
    chunks = [body_chunk("é".encode(), offset=n, limit=1) for n in range(2)]
    assert b"".join(base64.b64decode(c["data"]) for c in chunks) == "é".encode()
