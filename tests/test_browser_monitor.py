"""Real Chromium consumer fixtures: UI deliberately displays no traffic results.

No interception, network mocking, or fixture-side logs are used as capture proof.
"""
import asyncio
import base64
import hashlib
import json
import struct
from contextlib import asynccontextmanager

import pytest

from browser_automation.browser import BrowserSession
from browser_automation.browser_monitor import TrafficMonitor, sanitize_url


@asynccontextmanager
async def traffic_site():
    tasks = set()
    writers = set()

    async def respond(reader, writer):
        task = asyncio.current_task()
        tasks.add(task)
        writers.add(writer)
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            lines = head.decode().split("\r\n")
            path = lines[0].split()[1].split("?", 1)[0]
            headers = dict(line.split(": ", 1) for line in lines[1:] if ": " in line)
            if path == "/ws":
                key = headers["Sec-WebSocket-Key"]
                accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
                writer.write(f"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n".encode())
                await writer.drain()

                async def send(opcode, payload):
                    writer.write(bytes([0x80 | opcode, len(payload)]) + payload)
                    await writer.drain()

                await send(9, b"ping-proof")
                await send(1, b"server secret=hidden token=123 email=a@b.example")
                await send(2, b"\x00\xff\x01")
                while True:
                    first, second = await reader.readexactly(2)
                    size = second & 127
                    if size == 126:
                        size = struct.unpack("!H", await reader.readexactly(2))[0]
                    elif size == 127:
                        size = struct.unpack("!Q", await reader.readexactly(8))[0]
                    if size > 8192:
                        return
                    mask = await reader.readexactly(4) if second & 128 else None
                    payload = await reader.readexactly(size)
                    if mask:
                        payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
                    opcode = first & 15
                    if opcode == 8 or payload == b"close":
                        await send(8, struct.pack("!H", 1000))
                        return
                    if opcode == 1:
                        await send(1, payload)
                return
            status = "503 Service Unavailable" if path == "/503" else "302 Found" if path == "/redirect" else "400 Bad Request" if path == "/badws" else "200 OK"
            body = b"backend-unavailable" if path == "/503" else b'''<!doctype html><title>Traffic fixture</title><h1>Quiet UI</h1>
<button id="http" onclick="fetch('/503?token=never-retain').catch(()=>{});fetch('/redirect').catch(()=>{});fetch('http://127.0.0.1:1/failure?secret=hidden').catch(()=>{})">Request</button>
<button id="socket" onclick="window.socket=new WebSocket('ws://'+location.host+'/ws?token=never-retain');socket.onopen=()=>socket.send('client-proof');socket.onmessage=()=>{}">Socket</button>
<button id="end" onclick="socket.send('close')">End</button>
<button id="error" onclick="window.bad=new WebSocket('ws://'+location.host+'/badws?password=hidden');bad.onerror=()=>{}">Error</button>'''
            location = "Location: /ok?secret=never-retain\r\n" if path == "/redirect" else ""
            writer.write(f"HTTP/1.1 {status}\r\n{location}Content-Type: text/html\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writers.discard(writer)
            writer.close()
            await writer.wait_closed()
            tasks.discard(task)

    server = await asyncio.start_server(respond, "127.0.0.1", 0)
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


async def until(monitor, tab, kind, predicate):
    for _ in range(100):
        result = monitor.list(tab, kind, limit=1000)
        if predicate(result):
            return result
        await asyncio.sleep(0.025)
    raise AssertionError(f"Missing browser traffic event: {result}")


@pytest.mark.asyncio
async def test_real_http_status_failure_redirect_scope_and_cursor(tmp_path):
    async with traffic_site() as url, await BrowserSession.launch(headless=True) as browser:
        tab = (await browser.new_tab(url))["id"]
        page = browser._page(tab)
        other = (await browser.new_tab(url))["id"]
        monitor = TrafficMonitor()
        try:
            await monitor.start(page, tab, "network", max_events=64)
            await browser._page(other).click("#http")
            await asyncio.sleep(0.1)
            assert monitor.list(tab, "network")["events"] == []
            await page.click("#http")
            result = await until(monitor, tab, "network", lambda r: any(e["event"] == "requestfinished" and "/503" in e["url"] for e in r["events"]) and any(e["event"] == "requestfailed" for e in r["events"]))
            events = result["events"]
            assert any(e["event"] == "response" and e["status"] == 503 for e in events)
            assert not any(e["event"] == "requestfailed" and "/503" in e["url"] for e in events)
            assert any(e["redirected_from"] and "/ok" in e["url"] for e in events)
            encoded = json.dumps(result)
            assert "never-retain" not in encoded and "?secret=" not in encoded
            assert await page.locator("h1").inner_text() == "Quiet UI"
            await page.screenshot(path=str(tmp_path / "network-quiet-ui.png"))
            assert monitor.list(tab, "network", cursor=result["next_cursor"])["events"] == []
            with pytest.raises(ValueError, match="cursor"):
                monitor.list(tab, "network", cursor=result["latest_cursor"] + 1)
            capture = monitor._captures[(tab, "network")]
            await monitor.stop(tab, "network")
            assert not capture.listeners and not capture.requests and not capture.events
            await monitor.start(page, tab, "network", max_events=2, url_filter="/503")
            await page.click("#http")
            gap = await until(monitor, tab, "network", lambda r: r["dropped"] > 0)
            assert gap["cursor_gap"] and len(gap["events"]) == 2
            assert all("/503" in e["url"] for e in gap["events"])
            await page.close()
            await asyncio.sleep(0.05)
            assert not monitor._captures
        finally:
            await monitor.close()


@pytest.mark.asyncio
async def test_real_websocket_opcodes_inventory_late_attach_and_teardown(tmp_path):
    async with traffic_site() as url, await BrowserSession.launch(headless=True) as browser:
        tab = (await browser.new_tab(url))["id"]
        page = browser._page(tab)
        other = (await browser.new_tab(url))["id"]
        monitor = TrafficMonitor()
        try:
            await monitor.start(page, tab, "websocket", max_events=128)
            await browser._page(other).click("#socket")
            await asyncio.sleep(0.1)
            assert monitor.list(tab, "websocket")["events"] == []
            await page.click("#socket")
            result = await until(monitor, tab, "websocket", lambda r: {1, 2, 9, 10} <= {e.get("opcode") for e in r["events"]})
            assert any(e["event"] == "open" and e["status"] == 101 for e in result["events"])
            assert any(e["opcode"] == 2 and e["byte_count"] == 3 for e in result["events"] if "opcode" in e)
            assert all("text" not in e for e in result["events"])
            assert result["sockets"][0]["state"] == "open"
            assert await page.locator("h1").inner_text() == "Quiet UI"
            await page.screenshot(path=str(tmp_path / "websocket-quiet-ui.png"))
            await page.click("#end")
            await until(monitor, tab, "websocket", lambda r: any(e["event"] == "close" for e in r["events"]))
            await page.click("#error")
            await until(monitor, tab, "websocket", lambda r: any(e["event"] == "error" for e in r["events"]))
            capture = monitor._captures[(tab, "websocket")]
            await monitor.stop(tab, "websocket")
            assert not capture.listeners and not capture.sockets and not capture.events
            await page.click("#socket")
            await page.wait_for_function("socket.readyState === WebSocket.OPEN")
            late = await monitor.start(page, tab, "websocket")
            assert late["incomplete_history"]
            assert not monitor.list(tab, "websocket")["events"]
            await page.evaluate("socket.send('late-proof')")
            late_events = await until(monitor, tab, "websocket", lambda r: any(e["event"] == "frame_sent" for e in r["events"]))
            assert late_events["incomplete_history"]
        finally:
            await monitor.close()
        assert not monitor._cleanup_tasks


@pytest.mark.asyncio
async def test_payload_host_consent_redaction_and_binary_metadata(monkeypatch):
    async with traffic_site() as url, await BrowserSession.launch(headless=True) as browser:
        tab = (await browser.new_tab(url))["id"]
        page = browser._page(tab)
        monitor = TrafficMonitor()
        try:
            monkeypatch.delenv("BROWSER_MONITOR_PAYLOADS", raising=False)
            with pytest.raises(ValueError, match="BROWSER_MONITOR_PAYLOADS"):
                await monitor.start(page, tab, "websocket", payloads=True)
            assert not monitor._captures
            monkeypatch.setenv("BROWSER_MONITOR_PAYLOADS", "1")
            await monitor.start(page, tab, "websocket", payloads=True, max_payload_bytes=64)
            await page.click("#socket")
            result = await until(monitor, tab, "websocket", lambda r: any(e.get("opcode") == 2 for e in r["events"]))
            texts = [e["text"] for e in result["events"] if "text" in e]
            assert any("[redacted]" in t for t in texts)
            assert not any("hidden" in t or "a@b.example" in t for t in texts)
            assert all(len(t.encode()) <= 64 for t in texts)
            assert all("text" not in e for e in result["events"] if e.get("opcode") != 1)
        finally:
            await monitor.close()


def test_url_secret_stripping_and_monitor_validation():
    assert sanitize_url("https://user:pass@example.test/path?token=secret#private") == "https://example.test/path"
    assert sanitize_url("data:text/plain,secret") == "[unsupported-scheme]"
    monitor = TrafficMonitor()
    with pytest.raises(ValueError, match="start"):
        monitor.list("other-tab", "network")
