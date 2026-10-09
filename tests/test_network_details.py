"""Real browser passive network data consumers; no route interception."""
import asyncio
import base64
import hashlib
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
    release_streams = asyncio.Event()
    calls = {}

    async def serve(reader, writer):
        tasks.add(asyncio.current_task())
        writers.add(writer)
        try:
            raw = await reader.readuntil(b"\r\n\r\n")
            lines = raw.decode().split("\r\n")
            path = lines[0].split()[1].split("?", 1)[0]
            calls[path] = calls.get(path, 0) + 1
            headers = dict((k.lower(), v.strip()) for line in lines[1:] if ":" in line for k, v in [line.split(":", 1)])
            payload = await reader.readexactly(int(headers.get("content-length", "0")))
            status, extra, content_type = "200 OK", "", "application/json"
            if path == "/":
                content_type = "text/html"
                body = b'''<h1>Quiet fixture</h1><button id="go" onclick="fetch('/json?q=ordinary&token=query-private',{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer fixture-private'},body:JSON.stringify({query:'query Item { item }',variables:{id:42,token:'body-private'}})});fetch('/form',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:'item=ordinary&password=form-private'});fetch('/binary');fetch('/503');fetch('/redirect');fetch('http://127.0.0.1:1/fail').catch(()=>{});fetch('/stream').catch(()=>{})">Run</button>'''
                body += b'''<button id="stream" onclick="fetch('/stream').catch(()=>{})">Stream</button><button id="binary" onclick="fetch('/binary')">Binary</button><button id="absolute" onclick="fetch('/absolute-redirect')">Absolute redirect</button>'''
                extra = "Set-Cookie: fixture_session=cookie-private; HttpOnly; Path=/\r\n"
            elif path == "/binary":
                body, content_type = BINARY, "application/octet-stream"
            elif path == "/redirect":
                body, status, extra = b"", "302 Found", "Location: /503?ordinary=visible&token=redirect-private\r\n"
            elif path == "/absolute-redirect":
                destination = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/503?ordinary=visible&token=absolute-private"
                body, status, extra = b"", "302 Found", f"Location: {destination}\r\n"
            elif path == "/stream":
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\ndata: waiting\n\n")
                await writer.drain()
                await asyncio.wait_for(release_streams.wait(), 30)
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
        yield f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}", release_streams, calls
    finally:
        release_streams.set()
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
async def test_real_details_two_tabs_raw_replay_and_generation(monkeypatch, tmp_path):
    monkeypatch.setenv("BROWSER_NETWORK_BODY_TIMEOUT_MS", "10000")
    monkeypatch.delenv("BROWSER_NETWORK_SENSITIVE", raising=False)
    async with detail_site() as site, await BrowserSession.launch(headless=True) as browser:
        url, release_streams, calls = site
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
                assert chunk["data"] is not None, {k: chunk[k] for k in ("unavailable_reason", "total_bytes", "retained_bytes", "offset")}
                result.extend(base64.b64decode(chunk["data"]) if chunk["encoding"] == "base64" else chunk["data"].encode())
                if chunk["next_offset"] is None:
                    break
                offset = chunk["next_offset"]
            assert bytes(result) == BINARY
            assert chunk["total_bytes"] == len(BINARY) and chunk["source_complete"]
            assert (await monitor.request_detail(tab, ids["503"], fields=["status"]))["status"] == 503
            assert (await monitor.request_body(tab, ids["redirect"]))["unavailable_reason"] == "redirect_body_unavailable"
            relative = await monitor.request_detail(tab, ids["redirect"], fields=["response_headers"])
            relative_location = next(h["value"] for h in relative["response_headers"] if h["name"].lower() == "location")
            assert relative_location.startswith("/503?") and "ordinary=visible" in relative_location
            assert "redirect-private" not in relative_location and "%5Bredacted%5D" in relative_location
            async with browser._page(tab).expect_response(lambda response: response.url.endswith("/absolute-redirect")):
                await browser._page(tab).click("#absolute")
            absolute_id = next(e["request_id"] for e in monitor.list(tab, "network", limit=1000)["events"] if e["url"].endswith("/absolute-redirect"))
            absolute = await monitor.request_detail(tab, absolute_id, fields=["response_headers"])
            absolute_location = next(h["value"] for h in absolute["response_headers"] if h["name"].lower() == "location")
            assert absolute_location.startswith(url + "/503?") and "ordinary=visible" in absolute_location
            assert "absolute-private" not in absolute_location and "%5Bredacted%5D" in absolute_location
            assert (await monitor.request_body(tab, ids["fail"]))["unavailable_reason"] == "no_response"
            monitor.body_timeout_ms = 150
            assert (await monitor.request_body(tab, ids["stream"]))["unavailable_reason"] == "timeout"
            proof = {"detail": detail, "request_body": body, "form_body": form,
                     "relative_redirect": relative, "absolute_redirect": absolute,
                     "binary_sha256": hashlib.sha256(result).hexdigest(), "binary_total_bytes": len(result),
                     "tab_capture_ids": [monitor.list(t, "network")["capture_id"] for t in tabs],
                     "events": [monitor.list(t, "network", limit=1000) for t in tabs]}
            (tmp_path / "network-detail-consumer.json").write_text(json.dumps(proof, indent=2))
            for index, captured_tab in enumerate(tabs):
                assert await browser._page(captured_tab).locator("h1").inner_text() == "Quiet fixture"
                await browser._page(captured_tab).screenshot(path=str(tmp_path / f"network-details-tab-{index}.png"))
            first = monitor.list(tab, "network")["capture_id"]
            await monitor.stop(tab, "network")
            await monitor.start(browser._page(tab), tab)
            assert monitor.list(tab, "network")["capture_id"] != first
            with pytest.raises(ValueError, match="generation"):
                await monitor.replay_source(tab, ids["json"])
        finally:
            release_streams.set()
            await monitor.close()
        assert not monitor._body_cache and monitor._body_bytes == 0


@pytest.mark.asyncio
async def test_real_body_limit_and_eviction(monkeypatch):
    monkeypatch.setenv("BROWSER_NETWORK_BODY_LIMIT", "64")
    monkeypatch.setenv("BROWSER_NETWORK_CACHE_LIMIT", "64")
    async with detail_site() as site, await BrowserSession.launch(headless=True) as browser:
        url, release_streams, calls = site
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
            record = monitor._captures[(tab, "network")].records[ids["binary"]]
            assert record["body_status"]["response"]["unavailable_reason"] == "body_evicted"
            assert not any(key[1] == ids["binary"] for key in monitor._body_cache)
            recovered = await monitor.request_body(tab, ids["binary"])
            assert recovered["data"] is not None and recovered["unavailable_reason"] == "body_limit"
            assert recovered["body_read_attempts"] == 2 and calls["/binary"] == 1
            assert any(h["unavailable_reason"] == "body_evicted" for h in recovered["body_read_history"])
            assert monitor._body_bytes <= 64
        finally:
            release_streams.set()
            await monitor.close()


def test_structured_redaction_and_byte_chunks():
    assert "visible" in safe_url("https://example.test/a?q=visible&token=private")
    assert "private" not in safe_url("https://example.test/a?q=visible&token=private")
    assert safe_headers([{"name": "Authorization", "value": "private"}])[0]["value"] == "[redacted]"
    assert safe_headers([{"name": "Location", "value": "../login?q=ordinary&token=private"}])[0]["value"].startswith("../login?q=ordinary&token=%5Bredacted%5D")
    for location in ("javascript:alert(1)", "file:///private", "data:text/plain,private"):
        assert safe_headers([{"name": "Location", "value": location}])[0]["value"] == "[unsupported-scheme]"
    for name in ("X-Authorization", "X-Cookie", "X-CSRF-Token"):
        assert safe_headers([{"name": name, "value": "private"}])[0]["value"] == "[redacted]"
    assert json.loads(safe_body(b'{"ordinary":42,"password":"private"}', "application/json"))["ordinary"] == 42
    chunks = [body_chunk("é".encode(), offset=n, limit=1) for n in range(2)]
    assert b"".join(base64.b64decode(c["data"]) for c in chunks) == "é".encode()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalidate", ["stop", "evict", "cancel"])
async def test_real_concurrent_body_invalidation(monkeypatch, invalidate):
    monkeypatch.setenv("BROWSER_NETWORK_BODY_TIMEOUT_MS", "10000")
    async with detail_site() as site, await BrowserSession.launch(headless=True) as browser:
        url, release_streams, calls = site
        tab = (await browser.new_tab(url))["id"]
        page = browser._page(tab)
        monitor = TrafficMonitor()
        pending = None
        try:
            await monitor.start(page, tab, max_events=1)
            await page.click("#stream")
            for _ in range(100):
                events = monitor.list(tab, "network")["events"]
                if any(e["event"] == "response" and e["url"].endswith("/stream") for e in events):
                    break
                await asyncio.sleep(.01)
            else:
                raise AssertionError("Streaming response did not arrive")
            request_id = events[-1]["request_id"]
            capture = monitor._captures[(tab, "network")]
            pending = asyncio.create_task(monitor.request_body(tab, request_id))
            for _ in range(100):
                if capture.tasks:
                    break
                await asyncio.sleep(.01)
            else:
                raise AssertionError("Body read did not begin")
            if invalidate == "stop":
                await monitor.stop(tab, "network")
                with pytest.raises(ValueError, match="capture_stopped"):
                    await pending
            elif invalidate == "evict":
                await page.click("#binary")
                with pytest.raises(ValueError, match="request_evicted"):
                    await pending
            else:
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
                assert capture.active
        finally:
            release_streams.set()
            if pending is not None and not pending.done():
                pending.cancel()
            await monitor.close()
            if pending is not None:
                await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_real_timed_out_body_recovers_without_reissuing_request(monkeypatch, tmp_path):
    monkeypatch.setenv("BROWSER_NETWORK_BODY_TIMEOUT_MS", "150")
    async with detail_site() as site, await BrowserSession.launch(headless=True) as browser:
        url, release_streams, calls = site
        tab = (await browser.new_tab(url))["id"]
        page = browser._page(tab)
        monitor = TrafficMonitor()
        try:
            await monitor.start(page, tab)
            async with page.expect_response(lambda response: response.url.endswith("/stream")):
                await page.click("#stream")
            request_id = next(e["request_id"] for e in monitor.list(tab, "network")["events"] if e["event"] == "response")
            unavailable = await monitor.request_body(tab, request_id)
            assert unavailable["data"] is None and unavailable["unavailable_reason"] == "timeout"
            assert unavailable["body_read_attempts"] == 1 and calls["/stream"] == 1
            async with page.expect_event("requestfinished", predicate=lambda request: request.url.endswith("/stream")):
                release_streams.set()
            recovered = await monitor.request_body(tab, request_id)
            assert recovered["data"].encode() == b"data: waiting\n\n"
            assert recovered["source_complete"] and recovered["unavailable_reason"] is None
            assert recovered["body_read_attempts"] == 2 and calls["/stream"] == 1
            assert recovered["body_read_history"][0]["unavailable_reason"] == "timeout"
            (tmp_path / "network-body-recovery.json").write_text(json.dumps({"first": unavailable, "recovered": recovered, "http_request_count": calls["/stream"]}, indent=2))
        finally:
            release_streams.set()
            await monitor.close()
