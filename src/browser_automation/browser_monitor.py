"""Opt-in page-scoped network details and WebSocket metadata.

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
from types import MappingProxyType
from uuid import uuid4

from .browser_network_data import PRIVACY_NOTE, body_chunk, safe_body, safe_headers, safe_url, sensitive_allowed


def _host_positive(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        raise ValueError(f"{name} must be a positive integer") from None
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


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
    cleanup_diagnostics: list[str] = field(default_factory=list)
    capture_id: str = field(default_factory=lambda: uuid4().hex)
    records: OrderedDict = field(default_factory=OrderedDict)
    tasks: set = field(default_factory=set)

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
        return {"tab_id": self.tab_id, "kind": self.kind, "active": self.active, "capture_id": self.capture_id,
                "capture_started_at": self.capture_started_at, "incomplete_history": self.incomplete_history,
                "history_note": "Only events observed after capture start are available; preexisting requests/sockets may be incomplete.",
                "max_events": self.max_events, "url_filter": self.url_filter, "payloads": self.payloads,
                "max_payload_bytes": self.max_payload_bytes, "dropped": self.dropped,
                "identity_evictions": self.identity_evictions,
                "cleanup_diagnostics": list(self.cleanup_diagnostics),
                "capture_scope": "page_events" if self.kind == "network" else "main_page_cdp_target",
                "control_frame_visibility": "not_guaranteed_by_cdp" if self.kind == "websocket" else None,
                "scope_limitations": ["WebSockets in workers or out-of-process child frame targets are not captured by the main-page CDP session.", "CDP may omit protocol control frames (ping/pong/close); opcode values are only those actually emitted by CDP."] if self.kind == "websocket" else [],
                "payload_warning": "Text payload redaction is best effort; sensitive data may remain." if self.payloads else None}


class TrafficMonitor:
    """One instance per BrowserSession. All callbacks run on its asyncio loop."""

    def __init__(self) -> None:
        self._captures: dict[tuple[str, str], _Capture] = {}
        self._cleanup_tasks: set[asyncio.Task] = set()
        self._lock = asyncio.Lock()
        self._closed = False
        self.cleanup_diagnostics: deque[str] = deque(maxlen=32)
        self.body_limit = _host_positive("BROWSER_NETWORK_BODY_LIMIT", 16 * 1024 * 1024)
        self.body_cache_limit = _host_positive("BROWSER_NETWORK_CACHE_LIMIT", 64 * 1024 * 1024)
        self.body_timeout_ms = _host_positive("BROWSER_NETWORK_BODY_TIMEOUT_MS", 10000)
        self._body_cache: OrderedDict = OrderedDict()
        self._body_bytes = 0

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
                existing = self._captures[key]
                if (existing.max_events, existing.url_filter, existing.payloads, existing.max_payload_bytes) != (max_events, url_filter, payloads, max_payload_bytes):
                    raise ValueError("Monitor already active with different configuration; stop this tab/kind monitor, then start with the requested options")
                return {**existing.metadata(), "already_active": True}
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
        def finished(done: asyncio.Task) -> None:
            self._cleanup_tasks.discard(done)
            if not done.cancelled() and done.exception() is not None:
                self.cleanup_diagnostics.append("background_monitor_cleanup_failed")
        task.add_done_callback(finished)

    def _network(self, c: _Capture) -> None:
        def identity(request: Any) -> dict:
            key = weakref.ref(request)
            if key not in c.requests:
                c.identity += 1
                prior = request.redirected_from
                previous = c.requests.get(weakref.ref(prior)) if prior is not None else None
                request_id = f"r{c.capture_id}:{c.identity}"
                c.bounded_identity(c.requests, key, {"request_id": request_id, "url": sanitize_url(request.url),
                                                    "method": str(request.method)[:32],
                                                    "redirected_from": previous["request_id"] if previous else None})
                c.records[request_id] = {"request": request, "response": None, "state": "pending", "headers": None,
                                         "source": None, "body_status": {}, "info": dict(c.requests[key])}
                if len(c.records) > c.max_events:
                    old_id, old = c.records.popitem(last=False)
                    self._drop_record(c, old_id, old)
            return c.requests[key]

        def event(name: str, request: Any, **extra: Any) -> None:
            info = identity(request)
            if c.accepts(info["url"]):
                timing = {k: v for k, v in request.timing.items() if k in _TIMING_KEYS and isinstance(v, (int, float)) and math.isfinite(v)}
                c.emit(name, **info, timing=timing, **extra)

        def response(response: Any) -> None:
            info = identity(response.request)
            c.records[info["request_id"]]["response"] = response
            content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
            if content_type not in _CONTENT_TYPES:
                content_type = None
            event("response", response.request, status=response.status, content_type=content_type)

        def finished(request: Any, failed: bool = False) -> None:
            info = identity(request)
            c.records[info["request_id"]]["state"] = "failed" if failed else "finished"
            event("requestfailed" if failed else "requestfinished", request,
                  **({"failure": _failure(request.failure)} if failed else {}))

        self._listen(c, c.page, "request", lambda r: event("request", r))
        self._listen(c, c.page, "response", response)
        self._listen(c, c.page, "requestfinished", finished)
        self._listen(c, c.page, "requestfailed", lambda r: finished(r, True))

    def _record(self, tab_id: str, request_id: str) -> tuple[_Capture, dict]:
        c = self._captures.get((tab_id, "network"))
        if c is None or not isinstance(request_id, str) or request_id not in c.records:
            raise ValueError("Request unavailable: capture stopped, generation changed, unknown ID or record evicted")
        record = c.records[request_id]
        if not c.accepts(record["info"]["url"]):
            raise ValueError("Request is outside this capture's URL filter")
        return c, record

    def _drop_record(self, c: _Capture, request_id: str, record: dict) -> None:
        for part in ("request", "response"):
            cached = self._body_cache.pop((c.capture_id, request_id, part), None)
            if cached is not None:
                self._body_bytes -= len(cached)
        for task in record.get("tasks", {}).values():
            task.cancel()

    async def _headers(self, record: dict, part: str) -> list:
        key = f"{part}_headers"
        if key not in record:
            if record[part] is None:
                return []
            record[key] = await asyncio.wait_for(record[part].headers_array(), self.body_timeout_ms / 1000)
        return record[key]

    async def request_detail(self, tab_id: str, request_id: str, fields: list[str] | None = None,
                             include_sensitive: bool = False) -> dict:
        sensitive_allowed(include_sensitive)
        allowed = {"url", "method", "query", "request_headers", "response_headers", "status", "timing", "failure", "resource_type", "sizes", "frame", "server_addr", "security_details", "http_version"}
        selected = ["url", "method", "query", "request_headers", "response_headers", "status", "timing", "failure", "resource_type"] if fields is None else fields
        if not isinstance(selected, list) or any(not isinstance(f, str) or f not in allowed for f in selected):
            raise ValueError("fields must be a list of supported network detail field names")
        c, record = self._record(tab_id, request_id)
        request, response = record["request"], record["response"]
        result = {"tab_id": tab_id, "request_id": request_id, "capture_id": c.capture_id,
                  "state": record["state"], "provenance": "page_network_event", "privacy_note": PRIVACY_NOTE,
                  "include_sensitive": include_sensitive, "redaction_best_effort": not include_sensitive,
                  "unavailable_fields": {}, "redirected_from": record["info"]["redirected_from"]}
        from urllib.parse import parse_qsl
        for name in selected:
            try:
                if name == "url":
                    value = safe_url(request.url, include_sensitive)
                elif name == "query":
                    value = [{"name": k, "value": v} for k, v in parse_qsl(urlsplit(safe_url(request.url, include_sensitive)).query, keep_blank_values=True)]
                elif name == "method":
                    value = request.method
                elif name.endswith("_headers"):
                    part = name.split("_", 1)[0]
                    if record[part] is None:
                        result["unavailable_fields"][name] = "response_pending" if record["state"] == "pending" else "no_response"
                        continue
                    value = safe_headers(await self._headers(record, part), include_sensitive)
                elif name == "status":
                    value = response.status if response is not None else None
                elif name == "timing":
                    value = {k: v for k, v in request.timing.items() if k in _TIMING_KEYS and isinstance(v, (int, float)) and math.isfinite(v)}
                elif name == "failure":
                    value = _failure(request.failure) if request.failure else None
                elif name == "resource_type":
                    value = request.resource_type
                elif name == "frame":
                    value = {"url": safe_url(request.frame.url, include_sensitive)}
                elif name == "sizes":
                    value = await asyncio.wait_for(request.sizes(), self.body_timeout_ms / 1000)
                else:
                    if response is None:
                        result["unavailable_fields"][name] = "response_pending" if record["state"] == "pending" else "no_response"
                        continue
                    value = await asyncio.wait_for(getattr(response, name)(), self.body_timeout_ms / 1000)
                result[name] = value
            except asyncio.TimeoutError:
                result["unavailable_fields"][name] = "timeout"
            except Exception:
                result["unavailable_fields"][name] = "browser_unavailable"
        self._record(tab_id, request_id)
        return result

    async def replay_source(self, tab_id: str, request_id: str):
        c, record = self._record(tab_id, request_id)
        headers = await self._headers(record, "request")
        body = record["request"].post_data_buffer
        if body is not None and len(body) > min(self.body_limit, self.body_cache_limit):
            raise ValueError("Exact replay body exceeds configured network body retention limit")
        self._record(tab_id, request_id)
        return MappingProxyType({"tab_id": tab_id, "request_id": request_id, "capture_id": c.capture_id,
                                 "url": record["request"].url, "method": record["request"].method,
                                 "headers": tuple(MappingProxyType(dict(h)) for h in headers), "body": body})

    async def _load_body(self, c: _Capture, request_id: str, record: dict, part: str) -> bytes | None:
        key = (c.capture_id, request_id, part)
        if key in self._body_cache:
            self._body_cache.move_to_end(key)
            return self._body_cache[key]
        previous = record["body_status"].get(part)
        if previous is not None:
            reason = previous.get("unavailable_reason")
            recoverable = reason == "body_evicted" or (reason in {"timeout", "response_pending", "browser_body_unavailable"} and record["state"] == "finished")
            if not recoverable:
                return None
            history = record.setdefault("body_history", {}).setdefault(part, deque(maxlen=8))
            history.append({"attempt": record.get("body_attempts", {}).get(part, 0), **previous})
            record["body_status"].pop(part)
        handle = record[part]
        if handle is None:
            if record["state"] != "pending":
                record["body_status"][part] = {"unavailable_reason": "no_response", "source_complete": False}
            return None
        attempts = record.setdefault("body_attempts", {})
        attempts[part] = attempts.get(part, 0) + 1
        try:
            if part == "request":
                data = handle.post_data_buffer or b""
            else:
                if 300 <= handle.status < 400 and handle.status != 304:
                    record["body_status"][part] = {"unavailable_reason": "redirect_body_unavailable", "source_complete": False}
                    return None
                data = await asyncio.wait_for(handle.body(), self.body_timeout_ms / 1000)
            total = len(data)
            retained = data[:min(self.body_limit, self.body_cache_limit)]
            while self._body_cache and self._body_bytes + len(retained) > self.body_cache_limit:
                old_key, old_body = self._body_cache.popitem(last=False)
                self._body_bytes -= len(old_body)
                for capture in self._captures.values():
                    if capture.capture_id == old_key[0] and old_key[1] in capture.records:
                        capture.records[old_key[1]]["body_status"][old_key[2]].update(unavailable_reason="body_evicted", source_complete=False)
                        break
            record["body_status"][part] = {"total_bytes": total, "truncated": total > len(retained),
                                            "source_complete": total == len(retained),
                                            "unavailable_reason": "body_limit" if total > len(retained) else None}
            if not c.active or request_id not in c.records:
                return None
            self._body_cache[key] = retained
            self._body_bytes += len(retained)
            return retained
        except asyncio.TimeoutError:
            record["body_status"][part] = {"unavailable_reason": "timeout", "source_complete": False}
        except Exception:
            record["body_status"][part] = {"unavailable_reason": "transport_failed" if record["state"] == "failed" else "browser_body_unavailable", "source_complete": False}
        return None

    async def request_body(self, tab_id: str, request_id: str, part: str = "response", offset: int = 0,
                           limit: int = 65536, include_sensitive: bool = False) -> dict:
        sensitive_allowed(include_sensitive)
        if part not in {"request", "response"}:
            raise ValueError("part must be request or response")
        body_chunk(None, offset=offset, limit=limit)
        c, record = self._record(tab_id, request_id)
        tasks = record.setdefault("tasks", {})
        if part not in tasks:
            task = asyncio.create_task(self._load_body(c, request_id, record, part))
            tasks[part] = task
            c.tasks.add(task)
            def completed(done: asyncio.Task) -> None:
                c.tasks.discard(done)
                if tasks.get(part) is done:
                    tasks.pop(part, None)
            task.add_done_callback(completed)
        task = tasks[part]
        try:
            data = await asyncio.shield(task)
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise
            reason = "capture_stopped" if not c.active else "request_evicted" if request_id not in c.records else "body_read_cancelled"
            raise ValueError(f"{reason}: network body read invalidated") from None
        self._record(tab_id, request_id)
        status = dict(record["body_status"].get(part, {"unavailable_reason": "response_pending", "source_complete": False}))
        if data is not None:
            try:
                content_type = next((h["value"] for h in await self._headers(record, part) if h["name"].lower() == "content-type"), "")
            except Exception:
                content_type = ""
                status["unavailable_reason"] = status.get("unavailable_reason") or "content_type_unavailable"
            data = safe_body(data, content_type, include_sensitive)
        self._record(tab_id, request_id)
        if data is not None and (c.capture_id, request_id, part) not in self._body_cache:
            data = None
            status = dict(record["body_status"][part])
        return {"tab_id": tab_id, "request_id": request_id, "capture_id": c.capture_id, "part": part,
                "privacy_note": PRIVACY_NOTE, "redaction_best_effort": not include_sensitive,
                "export_total_bytes": len(data) if data is not None else None,
                "body_read_attempts": record.get("body_attempts", {}).get(part, 0),
                "body_read_history": (list(record.get("body_history", {}).get(part, ())) +
                                      [{"attempt": record.get("body_attempts", {}).get(part, 0), **status}])[-8:],
                **body_chunk(data, offset=offset, limit=limit, **status)}

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
            try:
                emitter.remove_listener(name, callback)
            except Exception:
                if len(c.cleanup_diagnostics) < 32:
                    c.cleanup_diagnostics.append("listener_detach_failed")
        c.listeners.clear()
        if c.cdp is not None:
            try:
                await c.cdp.detach()
            except Exception:
                if not c.page.is_closed() and len(c.cleanup_diagnostics) < 32:
                    c.cleanup_diagnostics.append("cdp_detach_failed")
        for request_id, record in c.records.items():
            self._drop_record(c, request_id, record)
        if c.tasks:
            await asyncio.gather(*tuple(c.tasks), return_exceptions=True)
        c.tasks.clear()
        c.records.clear()
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
            return {**metadata, "active": False, "stopped": True, "cleanup_diagnostics": list(c.cleanup_diagnostics)}

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            captures = list(self._captures.values())
            self._captures.clear()
            for capture in captures:
                await self._detach(capture)
        if self._cleanup_tasks:
            await asyncio.gather(*tuple(self._cleanup_tasks), return_exceptions=True)
