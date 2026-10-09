"""Bounded network exports. Redaction is best effort, never a secrecy guarantee."""
from __future__ import annotations

import base64
import json
import os
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_SENSITIVE = re.compile(r"password|passwd|secret|token|api[-_]?key|authorization|cookie|csrf|session|credential", re.I)
PRIVACY_NOTE = "Selected network data may be sent to model providers. Default redaction is best effort; arbitrary text/binary data may contain secrets."


def sensitive_allowed(include_sensitive: bool) -> bool:
    if not isinstance(include_sensitive, bool):
        raise ValueError("include_sensitive must be boolean")
    if include_sensitive and os.environ.get("BROWSER_NETWORK_SENSITIVE") != "1":
        raise ValueError("Sensitive exports require host BROWSER_NETWORK_SENSITIVE=1")
    return include_sensitive


def safe_url(url: str, include_sensitive: bool = False) -> str:
    sensitive_allowed(include_sensitive)
    try:
        parts = urlsplit(url)
        if parts.scheme.lower() not in {"http", "https", "ws", "wss"}:
            return "[unsupported-scheme]"
        host = parts.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        if parts.port is not None:
            host += f":{parts.port}"
        if include_sensitive:
            query = parts.query
        else:
            query = urlencode([(k, "[redacted]" if _SENSITIVE.search(k) else v) for k, v in parse_qsl(parts.query, keep_blank_values=True)])
        return urlunsplit((parts.scheme, host, parts.path, query, ""))
    except ValueError:
        return "[invalid-url]"


def safe_headers(headers, include_sensitive: bool = False) -> list[dict]:
    sensitive_allowed(include_sensitive)
    result = []
    for header in headers:
        name, value = str(header["name"]), str(header["value"])
        if not include_sensitive:
            if _SENSITIVE.search(name):
                value = "[redacted]"
            elif name.lower() in {"location", "referer", "referrer"}:
                value = safe_url(value)
        result.append({"name": name, "value": value})
    return result


def _scrub(value):
    if isinstance(value, dict):
        return {k: "[redacted]" if _SENSITIVE.search(k) else _scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    if isinstance(value, str):
        return _scrub_text(value)
    return value


def _scrub_text(text: str) -> str:
    text = re.sub(r"(?i)\b(bearer\s+)[^\s,;\"']+", r"\1[redacted]", text)
    return re.sub(r"(?i)([\"']?(?:password|passwd|token|secret|api[_-]?key|authorization|cookie|csrf|session)[\"']?\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;}]+)", r"\1[redacted]", text)


def safe_body(body: bytes, content_type: str = "", include_sensitive: bool = False) -> bytes:
    if sensitive_allowed(include_sensitive):
        return body
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return body
    media = content_type.split(";", 1)[0].lower()
    if media == "application/json" or media.endswith("+json"):
        try:
            return json.dumps(_scrub(json.loads(text)), ensure_ascii=False, separators=(",", ":")).encode()
        except (ValueError, RecursionError):
            pass
    if media == "application/x-www-form-urlencoded":
        return urlencode([(k, "[redacted]" if _SENSITIVE.search(k) else _scrub_text(v)) for k, v in parse_qsl(text, keep_blank_values=True)]).encode()
    return _scrub_text(text).encode()


def body_chunk(body: bytes | None, *, offset: int = 0, limit: int = 65536,
               total_bytes: int | None = None, truncated: bool = False,
               source_complete: bool = True, unavailable_reason: str | None = None) -> dict:
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be a nonnegative integer")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1048576:
        raise ValueError("limit must be an integer in 1..1048576")
    retained = len(body) if body is not None else 0
    fragment = body[offset:offset + limit] if body is not None else b""
    try:
        data, encoding = fragment.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        data, encoding = base64.b64encode(fragment).decode("ascii"), "base64"
    end = min(offset + len(fragment), retained)
    return {"data": data if body is not None else None, "encoding": encoding if body is not None else None,
            "offset": offset, "next_offset": end if end < retained else None,
            "total_bytes": total_bytes if total_bytes is not None else (retained if body is not None else None),
            "retained_bytes": retained, "truncated": truncated, "source_complete": source_complete,
            "unavailable_reason": unavailable_reason}
