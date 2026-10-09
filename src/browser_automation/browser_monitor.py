"""Opt-in, page-scoped traffic metadata; never reads HTTP bodies or credentials.

Contracts researched 2026-10-09:
https://playwright.dev/python/docs/api/class-request
https://chromedevtools.github.io/devtools-protocol/tot/Network/
CDP WebSocketFrame represents a message (possibly fragmented on the wire).
Payload redaction is best effort, NOT a guarantee of removing all secrets.
"""
from __future__ import annotations

import asyncio
import math
import os
import re
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any
import weakref
from itertools import islice
from urllib.parse import urlsplit, urlunsplit


KINDS = {"network", "websocket"}
_TIMING_KEYS = {"startTime", "domainLookupStart", "domainLookupEnd", "connectStart", "secureConnectionStart", "connectEnd", "requestStart", "responseStart", "responseEnd"}
_CONTENT_TYPES = {"text/html", "text/plain", "text/css", "text/javascript", "application/javascript", "application/json", "application/xml", "text/xml", "application/octet-stream", "application/pdf", "image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml", "font/woff", "font/woff2", "text/event-stream"}


def _integer(value: Any, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{name} must be an integer in {low}..{high}")
    return value


def sanitize_url(value: str) -> str:
    """Discard userinfo, query and fragment; never expose protected schemes."""
    try:
        parts = urlsplit(value)
        if parts.scheme.lower() not in {"http", "https", "ws", "wss"}:
            return "[unsupported-scheme]"
        host = parts.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        if parts.port is not None:
            host += f":{parts.port}"
        return urlunsplit((parts.scheme.lower(), host, parts.path, "", ""))[:2048]
    except (ValueError, TypeError):
        return "[invalid-url]"


def _failure(value: Any) -> str:
    # Raw browser error strings can include URLs. Return only known error codes.
    match = re.search(r"\bnet::(ERR_[A-Z0-9_]{1,80})\b", str(value)[:256])
    return f"net::{match.group(1)}" if match else "transport_error"


def _text_payload(value: str, limit: int) -> tuple[str, bool]:
    # Bound before regex work and never retain the complete message.
    fragment = value[:limit + 1].encode("utf-8")[:limit]
    text = fragment.decode("utf-8", errors="ignore")
    text = re.sub(r"(?i)\b(bearer\s+)[^\s,;\"']+", r"\1[redacted]", text)
    text = re.sub(r"(?i)([\"']?(?:password|passwd|token|secret|api[_-]?key|authorization|cookie)[\"']?\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;}]+)", r"\1[redacted]", text)
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[redacted-email]", text)
    truncated = len(value) > limit or len(value[:limit + 1].encode("utf-8")) > limit
    return text.encode("utf-8")[:limit].decode("utf-8", errors="ignore"), truncated


@dataclass
class _Capture:
    page: Any
    tab_id: str
    kind: str
    max_events: int
    url_filter: str | None
    payloads: bool
    max_payload_bytes: int
    capture_started_at: float = field(default_factory=time.time)
    events: deque = field(default_factory=deque)
    listeners: list = field(default_factory=list)
    requests: OrderedDict = field(default_factory=OrderedDict)
    sockets: OrderedDict = field(default_factory=OrderedDict)
    cdp: Any = None
    sequence: int = 0
    identity: int = 0
    dropped: int = 0
    identity_evictions: int = 0
    active: bool = True
    incomplete_history: bool = True

    def accepts(self, url: str | None) -> bool:
        return self.url_filter is None or (url is not None and self.url_filter in url)

    def emit(self, event: str, **data: Any) -> None:
        if not self.active:
            return
        self.sequence += 1
        if len(self.events) == self.max_events:
            self.events.popleft()
            self.dropped += 1
        self.events.append({"cursor": self.sequence, "event": event, "timestamp": time.time(), **data})

    def bounded_identity(self, mapping: OrderedDict, key: Any, value: Any) -> None:
        mapping[key] = value
        mapping.move_to_end(key)
        if len(mapping) > self.max_events:
            mapping.popitem(last=False)
            self.identity_evictions += 1
            self.incomplete_history = True

    def metadata(self) -> dict:
        return {"tab_id": self.tab_id, "kind": self.kind, "active": self.active,
                "capture_started_at": self.capture_started_at, "incomplete_history": self.incomplete_history,
                "history_note": "Only events observed after capture start are available; preexisting requests/sockets may be incomplete.",
                "max_events": self.max_events, "url_filter": self.url_filter, "payloads": self.payloads,
                "max_payload_bytes": self.max_payload_bytes, "dropped": self.dropped,
                "identity_evictions": self.identity_evictions,
                "payload_warning": "Text payload redaction is best effort; sensitive data may remain." if self.payloads else None}


class TrafficMonitor:
    """One instance per BrowserSession. All callbacks run on its asyncio loop."""

    def __init__(self) -> None:
        self._captures: dict[tuple[str, str], _Capture] = {}
        self._cleanup_tasks: set[asyncio.Task] = set()
        self._lock = asyncio.Lock()
        self._closed = False

    async def start(self, page: Any, tab_id: str, kind: str = "network", max_events: int = 256,
                    url_filter: str | None = None, payloads: bool = False, max_payload_bytes: int = 512) -> dict:
        if kind not in KINDS:
            raise ValueError("kind must be network or websocket")
        _integer(max_events, "max_events", 1, 4096)
        _integer(max_payload_bytes, "max_payload_bytes", 1, 4096)
        if url_filter is not None and (not isinstance(url_filter, str) or not 1 <= len(url_filter) <= 256):
            raise ValueError("url_filter must be a nonempty literal substring of at most 256 characters")
        if not isinstance(payloads, bool):
            raise ValueError("payloads must be boolean")
        if payloads and (kind != "websocket" or os.environ.get("BROWSER_MONITOR_PAYLOADS") != "1"):
            raise ValueError("Text payload capture requires websocket kind and host BROWSER_MONITOR_PAYLOADS=1")
        async with self._lock:
            if self._closed:
                raise RuntimeError("TrafficMonitor is closed")
            key = (tab_id, kind)
            if key in self._captures:
                return self._captures[key].metadata()
            if page.is_closed():
                raise ValueError("Cannot monitor a closed page")
            capture = _Capture(page, tab_id, kind, max_events, url_filter, payloads, max_payload_bytes)
            self._captures[key] = capture
            try:
                self._listen(capture, page, "close", lambda *_: self._schedule_stop(tab_id, kind))
                if kind == "network":
                    self._network(capture)
                else:
                    capture.cdp = await page.context.new_cdp_session(page)
                    self._websocket(capture)
                    # Do not request extraInfo events or retain headers/bodies.
                    await capture.cdp.send("Network.enable", {"maxTotalBufferSize": 0, "maxResourceBufferSize": 0})
                return capture.metadata()
            except BaseException:
                self._captures.pop(key, None)
                await self._detach(capture)
                raise

    def _listen(self, capture: _Capture, emitter: Any, name: str, callback: Any) -> None:
        emitter.on(name, callback)
        capture.listeners.append((emitter, name, callback))

    def _schedule_stop(self, tab_id: str, kind: str) -> None:
        task = asyncio.create_task(self.stop(tab_id, kind))
        self._cleanup_tasks.add(task)
        task.add_done_callback(self._cleanup_tasks.discard)

    def _network(self, c: _Capture) -> None:
        def identity(request: Any) -> dict:
            key = weakref.ref(request)
            if key not in c.requests:
                c.identity += 1
                prior = request.redirected_from
                previous = c.requests.get(weakref.ref(prior)) if prior is not None else None
                c.bounded_identity(c.requests, key, {"request_id": f"r{c.identity}", "url": sanitize_url(request.url),
                                                    "method": str(request.method)[:32],
                                                    "redirected_from": previous["request_id"] if previous else None})
            return c.requests[key]

        def event(name: str, request: Any, **extra: Any) -> None:
            info = identity(request)
            if c.accepts(info["url"]):
                timing = {k: v for k, v in request.timing.items() if k in _TIMING_KEYS and isinstance(v, (int, float)) and math.isfinite(v)}
                c.emit(name, **info, timing=timing, **extra)

        def response(response: Any) -> None:
            content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
            if content_type not in _CONTENT_TYPES:
                content_type = None
            event("response", response.request, status=response.status, content_type=content_type)

        self._listen(c, c.page, "request", lambda r: event("request", r))
        self._listen(c, c.page, "response", response)
        self._listen(c, c.page, "requestfinished", lambda r: event("requestfinished", r))
        self._listen(c, c.page, "requestfailed", lambda r: event("requestfailed", r, failure=_failure(r.failure)))

    def _websocket(self, c: _Capture) -> None:
        def socket(params: dict) -> dict:
            key = str(params["requestId"])[:128]
            if key not in c.sockets:
                c.identity += 1
                c.bounded_identity(c.sockets, key, {"socket_id": f"w{c.identity}", "url": None,
                                                    "state": "unknown", "history_complete": False})
            return c.sockets[key]

        def emit(name: str, params: dict, **extra: Any) -> None:
            info = socket(params)
            if c.accepts(info["url"]):
                stamp = params.get("timestamp")
                if isinstance(stamp, (int, float)) and math.isfinite(stamp):
                    extra["monotonic_timestamp"] = stamp
                c.emit(name, **info, **extra)

        def created(params: dict) -> None:
            info = socket(params)
            info.update(url=sanitize_url(params["url"]), state="connecting", history_complete=True)
            emit("created", params)

        def handshake(params: dict) -> None:
            status = params.get("response", {}).get("status")
            info = socket(params)
            info["state"] = "open" if status == 101 else "handshake_failed"
            emit("open" if status == 101 else "handshake_failed", params, status=status)

        def frame(direction: str, params: dict) -> None:
            response = params["response"]
            opcode = int(response["opcode"])
            payload = response.get("payloadData", "")
            # CDP non-text payloads are base64. Count without decoding/copying binary data.
            byte_count = sum(1 if ord(ch) < 128 else 2 if ord(ch) < 2048 else 3 if ord(ch) < 65536 else 4 for ch in payload) if opcode == 1 else max(0, len(payload) * 3 // 4 - (2 if payload.endswith("==") else 1 if payload.endswith("=") else 0))
            extra = {"opcode": opcode, "byte_count": byte_count}
            if opcode == 1 and c.payloads:
                extra["text"], extra["payload_truncated"] = _text_payload(payload, c.max_payload_bytes)
            emit(f"frame_{direction}", params, **extra)

        def closed(params: dict) -> None:
            socket(params)["state"] = "closed"
            emit("close", params)

        self._listen(c, c.cdp, "Network.webSocketCreated", created)
        self._listen(c, c.cdp, "Network.webSocketHandshakeResponseReceived", handshake)
        self._listen(c, c.cdp, "Network.webSocketFrameReceived", lambda p: frame("received", p))
        self._listen(c, c.cdp, "Network.webSocketFrameSent", lambda p: frame("sent", p))
        self._listen(c, c.cdp, "Network.webSocketFrameError", lambda p: emit("error", p, error="websocket_transport_error"))
        self._listen(c, c.cdp, "Network.webSocketClosed", closed)

    def list(self, tab_id: str, kind: str, cursor: int | None = None, limit: int = 100) -> dict:
        _integer(limit, "limit", 1, 1000)
        c = self._captures.get((tab_id, kind))
        if c is None:
            raise ValueError("No active monitor for this tab and kind; start monitoring first")
        if cursor is not None:
            _integer(cursor, "cursor", 0, c.sequence)
        start = 0 if cursor is None else cursor
        first = c.events[0]["cursor"] if c.events else c.sequence + 1
        gap = start < first - 1
        events = [dict(e) for e in islice((e for e in c.events if e["cursor"] > start), limit)]
        return {**c.metadata(), "events": events, "next_cursor": events[-1]["cursor"] if events else start,
                "oldest_cursor": first, "latest_cursor": c.sequence, "cursor_gap": gap,
                "cursor_diagnostic": "Requested history was evicted; resume at oldest_cursor - 1." if gap else None,
                "sockets": [dict(s) for s in c.sockets.values() if c.accepts(s["url"])] if kind == "websocket" else []}

    async def _detach(self, c: _Capture) -> None:
        c.active = False
        for emitter, name, callback in c.listeners:
            emitter.remove_listener(name, callback)
        c.listeners.clear()
        if c.cdp is not None:
            try:
                await c.cdp.detach()
            except Exception:
                # Already-closed targets have no remaining session to detach.
                pass
        c.requests.clear()
        c.sockets.clear()
        c.events.clear()

    async def stop(self, tab_id: str, kind: str) -> dict:
        if kind not in KINDS:
            raise ValueError("kind must be network or websocket")
        async with self._lock:
            c = self._captures.pop((tab_id, kind), None)
            if c is None:
                return {"tab_id": tab_id, "kind": kind, "active": False, "stopped": False}
            metadata = c.metadata()
            await self._detach(c)
            return {**metadata, "active": False, "stopped": True}

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            captures = list(self._captures.values())
            self._captures.clear()
            for capture in captures:
                await self._detach(capture)
        if self._cleanup_tasks:
            await asyncio.gather(*tuple(self._cleanup_tasks), return_exceptions=True)
