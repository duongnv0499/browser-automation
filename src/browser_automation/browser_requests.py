"""Private, single-use HTTP plans using the selected browser context cookie jar.

API outcomes are not rendered UI outcomes. No page events are synthesized.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from .browser_network_data import PRIVACY_NOTE, body_chunk, safe_body, safe_headers, safe_url, sensitive_allowed

_TOKEN = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_RISK = re.compile(r"buy|pay|purchase|checkout|order|delete|remove|send|submit|publish|post|transfer|confirm|accept|authorize|sign.?in|log.?in|log.?out|upload|download|subscribe|unsubscribe", re.I)
_CREDENTIAL = re.compile(r"authorization|cookie|proxy-authorization|.*(?:token|secret|csrf|api[-_]?key|session|credential).*", re.I)
_TRANSPORT = {'host', 'content-length', 'connection', 'transfer-encoding'}


def origin(url: str) -> tuple[str, str, int]:
    parsed = urlsplit(url)
    return parsed.scheme.lower(), (parsed.hostname or '').lower(), parsed.port or (443 if parsed.scheme == 'https' else 80)


def checked_url(url: str) -> str:
    if not isinstance(url, str):
        raise ValueError('url must be an HTTP(S) string')
    parsed = urlsplit(url)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise ValueError('Only absolute HTTP(S) URLs without embedded credentials are permitted')
    origin(url)  # Validate port before preparing or following a redirect.
    if any(c in url for c in '\r\n\x00'):
        raise ValueError('Invalid URL control character')
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or '/', parsed.query, ''))


def bounded_env(name: str, default: int) -> int:
    value = int(os.environ.get(name, str(default)))
    if value < 1:
        raise ValueError(f'{name} must be positive')
    return value


@dataclass(frozen=True)
class Plan:
    plan_id: str
    tab_id: str
    context: Any
    page_url: str
    url: str
    method: str
    headers: tuple[tuple[str, str], ...]
    body: bytes | None
    timeout_ms: int
    max_redirects: int
    expires_at: float
    source: tuple[str, str, str] | None
    preview_json: str


class RequestExecutor:
    def __init__(self) -> None:
        self._plans: dict[str, Plan] = {}
        self._records: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._body_limit = bounded_env('BROWSER_NETWORK_BODY_LIMIT', 16 * 1024 * 1024)
        self._cache_limit = bounded_env('BROWSER_NETWORK_CACHE_LIMIT', 64 * 1024 * 1024)
        self._record_limit = bounded_env('BROWSER_NETWORK_CALL_LIMIT', 256)
        self._plan_limit = bounded_env('BROWSER_NETWORK_PLAN_LIMIT', 256)
        self._bytes = 0

    def prepare(self, tab_id: str, page: Any, spec: dict[str, Any], *, source: Any = None, source_context: Any = None) -> dict[str, Any]:
        allowed = {'url', 'method', 'headers', 'body', 'json_body', 'form', 'body_base64', 'params', 'timeout_ms', 'max_redirects'}
        unknown = set(spec) - allowed
        if unknown:
            raise ValueError('Unknown request options: ' + ', '.join(sorted(unknown)))
        original_url = source['url'] if source else None
        url = checked_url(spec.get('url', original_url))
        if spec.get('params') is not None:
            if not isinstance(spec['params'], dict):
                raise ValueError('params must be an object')
            p = urlsplit(url)
            keys = {str(key) for key in spec['params']}
            query = [(key, value) for key, value in parse_qsl(p.query, keep_blank_values=True) if key not in keys]
            query.extend((str(key), value) for key, value in spec['params'].items())
            url = urlunsplit((p.scheme, p.netloc, p.path, urlencode(query, doseq=True), ''))
        method = spec.get('method', source['method'] if source else 'GET')
        if not isinstance(method, str) or not _TOKEN.fullmatch(method):
            raise ValueError('method must be a valid HTTP token')
        method = method.upper()
        headers: dict[str, tuple[str, str]] = {}
        duplicates = False
        if source:
            for header in source['headers']:
                name, value = header['name'], header['value']
                lower = name.lower()
                if lower in _TRANSPORT or lower == 'cookie':
                    continue
                if _CREDENTIAL.fullmatch(lower) and (origin(original_url) != origin(url) or source_context is not page.context):
                    continue
                duplicates |= lower in headers
                headers[lower] = (name, value)
        overrides = spec.get('headers')
        if overrides is not None:
            if not isinstance(overrides, dict):
                raise ValueError('headers must be a name/value object; duplicate send headers are not supported by Playwright')
            for name, value in overrides.items():
                if not isinstance(name, str) or not _TOKEN.fullmatch(name) or not isinstance(value, str) or any(c in value for c in '\r\n\x00'):
                    raise ValueError('Invalid HTTP header')
                if name.lower() in _TRANSPORT:
                    raise ValueError('Transport-managed headers cannot be overridden')
                headers[name.lower()] = (name, value)
        bodies = [key for key in ('body', 'json_body', 'form', 'body_base64') if key in spec]
        if len(bodies) > 1:
            raise ValueError('body, json_body, form and body_base64 are mutually exclusive')
        body = source['body'] if source else None
        if bodies:
            key = bodies[0]
            value = spec[key]
            if key == 'json_body':
                body = json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
                headers.setdefault('content-type', ('Content-Type', 'application/json'))
            elif key == 'form':
                if not isinstance(value, dict):
                    raise ValueError('form must be an object')
                body = urlencode(value, doseq=True).encode()
                headers.setdefault('content-type', ('Content-Type', 'application/x-www-form-urlencoded'))
            elif key == 'body_base64':
                body = base64.b64decode(value, validate=True)
            elif value is None:
                body = None
            elif isinstance(value, str):
                body = value.encode()
            elif isinstance(value, bytes):
                body = value
            else:
                raise ValueError('body must be text, bytes, or null')
        if body is not None and len(body) > bounded_env('BROWSER_NETWORK_REQUEST_LIMIT', 16 * 1024 * 1024):
            raise ValueError('Request body exceeds host BROWSER_NETWORK_REQUEST_LIMIT')
        if body is not None:
            headers.setdefault('content-type', ('Content-Type', 'application/octet-stream'))
        timeout = spec.get('timeout_ms', 30000)
        redirects = spec.get('max_redirects', 5)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1:
            raise ValueError('timeout_ms must be a positive integer')
        if isinstance(redirects, bool) or not isinstance(redirects, int) or not 0 <= redirects <= bounded_env('BROWSER_NETWORK_REDIRECT_LIMIT', 20):
            raise ValueError('max_redirects exceeds host redirect limit')
        reasons = []
        if os.environ.get('BROWSER_NETWORK_REQUIRE_APPROVAL') == '1':
            reasons.append('Host requires approval for every request')
        if method not in {'GET', 'HEAD', 'OPTIONS'}:
            reasons.append('Method is not an HTTP safe method')
        if not page.url.startswith(('http://', 'https://')) or origin(url) != origin(page.url):
            reasons.append('Destination differs from target tab origin')
        if body is not None:
            reasons.append('Request includes a body')
        if overrides and any(_CREDENTIAL.fullmatch(name) for name in overrides):
            reasons.append('Explicit credential header modification')
        if _RISK.search(urlsplit(url).path + '?' + urlsplit(url).query):
            reasons.append('URL names suggest a consequential action')
        now = time.time()
        for key, old in list(self._plans.items()):
            if old.expires_at <= now:
                self._plans.pop(key)
        if len(self._plans) >= self._plan_limit:
            raise ValueError('Prepared plan capacity reached; execute or allow plans to expire')
        plan_id = 'plan' + uuid.uuid4().hex
        source_binding = (source['tab_id'], source['request_id'], source['capture_id']) if source else None
        binding = {'plan_id': plan_id, 'target_tab_id': tab_id, 'target_page_url': safe_url(page.url), 'context_id': str(id(page.context)), 'url': safe_url(url), 'method': method, 'body_sha256': hashlib.sha256(body).hexdigest() if body is not None else None, 'headers_sha256': hashlib.sha256(json.dumps(list(headers.values())).encode()).hexdigest(), 'timeout_ms': timeout, 'max_redirects': redirects, 'source': list(source_binding) if source_binding else None}
        # Raw URL participates in the hash, never appears in a default model preview.
        digest = hashlib.sha256(json.dumps({**binding, 'raw_url': url, 'raw_page_url': page.url}, sort_keys=True).encode()).hexdigest()
        binding['plan_hash'] = digest
        preview = {'status': 'approval_required' if reasons else 'prepared', 'plan_id': plan_id, 'plan_hash': digest, 'binding': binding, 'expires_at': now + 300, 'approval_required': bool(reasons), 'approval_reason': '; '.join(reasons) or 'Same-origin HTTP safe method; servers can violate safe-method semantics', 'request': {'url': safe_url(url), 'method': method, 'headers': safe_headers([{'name': n, 'value': v} for n, v in headers.values()]), 'body_bytes': len(body) if body is not None else 0}, 'limitations': ['API requests do not render the DOM', 'Duplicate original request headers collapsed to their last value'] if duplicates else ['API requests do not render the DOM']}
        self._plans[plan_id] = Plan(plan_id, tab_id, page.context, page.url, url, method, tuple(headers.values()), body, timeout, redirects, now + 300, source_binding, json.dumps(preview))
        return preview

    def plan(self, plan_id: str) -> Plan:
        plan = self._plans.get(plan_id)
        if plan is None:
            raise ValueError('Unknown or already consumed network plan')
        if plan.expires_at <= time.time():
            self._plans.pop(plan_id)
            raise ValueError('Network plan expired')
        return plan

    def preview(self, plan_id: str) -> dict[str, Any]:
        return json.loads(self.plan(plan_id).preview_json)

    async def execute(self, plan_id: str, *, approved: bool) -> dict[str, Any]:
        plan = self.plan(plan_id)
        if not approved:
            return self.preview(plan_id)
        self._plans.pop(plan_id)  # Consume before first send; never retry an unknown outcome.
        request_id = 'call' + uuid.uuid4().hex
        record = {'request_id': request_id, 'tab_id': plan.tab_id, 'url': plan.url, 'method': plan.method, 'headers': [{'name': n, 'value': v} for n, v in plan.headers], 'body': plan.body, 'response': None, 'response_headers': [], 'response_body': None, 'unavailable_reason': None, 'provenance': 'browser_context_api_request', 'redirects': [], 'status': None}
        self._records[request_id] = record
        while len(self._records) > self._record_limit:
            _, old = self._records.popitem(last=False)
            await self._dispose(old)
        url, method, body = plan.url, plan.method, plan.body
        headers = dict(plan.headers)
        deadline = time.monotonic() + plan.timeout_ms / 1000
        try:
            for hop in range(plan.max_redirects + 1):
                timeout = max(1, int((deadline - time.monotonic()) * 1000))
                response = await plan.context.request.fetch(url, method=method, headers=headers, data=body, timeout=timeout, max_redirects=0, max_retries=0, fail_on_status_code=False)
                record.update(response=response, status=response.status, response_headers=response.headers_array, response_url=response.url)
                location = response.headers.get('location')
                if response.status not in {301, 302, 303, 307, 308} or not location:
                    break
                if hop == plan.max_redirects:
                    record['diagnostic'] = {'code': 'redirect_limit', 'message': 'Redirect bound reached; final response retained'}
                    break
                next_url = checked_url(urljoin(url, location))
                cross_origin = origin(next_url) != origin(url)
                risky_destination = bool(_RISK.search(urlsplit(next_url).path + '?' + urlsplit(next_url).query))
                if cross_origin or risky_destination and next_url != plan.url:
                    record['diagnostic'] = {'code': 'redirect_reapproval_required', 'url': safe_url(next_url), 'message': 'Foreign-origin or consequential-looking redirect was not sent; prepare a separately approved call to this exact destination', 'next_request': {'url': safe_url(next_url), 'method': 'GET' if response.status == 303 and method != 'HEAD' or response.status in {301, 302} and method == 'POST' else method, 'body_sha256': hashlib.sha256(body).hexdigest() if body is not None else None}}
                    break
                record['redirects'].append({'status': response.status, 'url': safe_url(url), 'destination': safe_url(next_url)})
                await response.dispose()
                record['response'] = None
                if response.status == 303 and method != 'HEAD' or response.status in {301, 302} and method == 'POST':
                    method, body = 'GET', None
                    headers = {n: v for n, v in headers.items() if n.lower() not in {'content-type', 'content-encoding'}}
                url = next_url
            if record['response'] is not None:
                await self._retain_response(record)
        except asyncio.CancelledError:
            record['unavailable_reason'] = 'cancelled_unknown_outcome'
            record['diagnostic'] = {'code': 'cancelled_unknown_outcome', 'message': 'Approval consumed; request may have reached server. No retry performed.'}
            if record['response'] is not None:
                await record['response'].dispose()
                record['response'] = None
            raise
        except Exception as exc:
            record['unavailable_reason'] = 'transport_error'
            record['diagnostic'] = {'code': 'transport_error', 'error_type': type(exc).__name__, 'message': 'Request failed; remote outcome may be unknown. No retry performed.'}
            if record['response'] is not None:
                await record['response'].dispose()
                record['response'] = None
        return await self.detail(plan.tab_id, request_id)

    def _record(self, tab_id: str, request_id: str) -> dict[str, Any]:
        record = self._records.get(request_id)
        if record is None or record['tab_id'] != tab_id:
            raise ValueError('Unknown, evicted, or wrong-tab API request')
        return record

    async def detail(self, tab_id: str, request_id: str, fields: list[str] | None = None, include_sensitive: bool = False) -> dict[str, Any]:
        sensitive_allowed(include_sensitive)
        record = self._record(tab_id, request_id)
        result = {'request_id': request_id, 'tab_id': tab_id, 'provenance': record['provenance'], 'url': safe_url(record['url'], include_sensitive), 'method': record['method'], 'request_headers': safe_headers(record['headers'], include_sensitive), 'status': record['status'], 'response_headers': safe_headers(record['response_headers'], include_sensitive), 'response_url': safe_url(record.get('response_url', record['url']), include_sensitive), 'redirects': record['redirects'], 'request_body_bytes': len(record['body']) if record['body'] is not None else 0, 'unavailable_reason': record['unavailable_reason'], 'redaction_best_effort': not include_sensitive}
        result['query'] = [{'name': name, 'value': value} for name, value in parse_qsl(urlsplit(result['url']).query, keep_blank_values=True)]
        result['privacy_note'] = PRIVACY_NOTE
        if record.get('diagnostic'):
            result['diagnostic'] = record['diagnostic']
        if fields is not None:
            if not isinstance(fields, list) or any(not isinstance(f, str) or f not in result for f in fields):
                raise ValueError('Unknown API request detail field')
            result = {key: result[key] for key in {'request_id', 'tab_id', 'provenance', *fields}}
        return result

    async def body(self, tab_id: str, request_id: str, part: str = 'response', offset: int = 0, limit: int = 65536, include_sensitive: bool = False) -> dict[str, Any]:
        sensitive_allowed(include_sensitive)
        body_chunk(None, offset=offset, limit=limit)
        if part not in {'request', 'response'}:
            raise ValueError('part must be request or response')
        record = self._record(tab_id, request_id)
        if part == 'request':
            raw = record['body']
            total = len(raw) if raw is not None else 0
            reason = 'no_request_body' if raw is None else None
            headers = record['headers']
            truncated = False
        else:
            raw = record['response_body']
            total = record.get('total_bytes')
            reason = record['unavailable_reason']
            headers = record['response_headers']
            truncated = record.get('truncated', False)
        content_type = next((h['value'] for h in headers if h['name'].lower() == 'content-type'), '')
        filtered = safe_body(raw, content_type, include_sensitive) if raw is not None else None
        return {'request_id': request_id, 'tab_id': tab_id, 'part': part, 'privacy_note': PRIVACY_NOTE, 'redaction_best_effort': not include_sensitive, 'export_total_bytes': len(filtered) if filtered is not None else None, **body_chunk(filtered, offset=offset, limit=limit, total_bytes=total, truncated=truncated, source_complete=reason is None, unavailable_reason=reason)}

    async def _retain_response(self, record: dict[str, Any]) -> None:
        # Playwright fetch already buffers the HTTP response. Copy the bounded
        # retained portion then release its SDK allocation immediately, rather
        # than keeping full bodies alive until a later model detail request.
        try:
            raw = await asyncio.wait_for(record['response'].body(), bounded_env('BROWSER_NETWORK_BODY_TIMEOUT_MS', 10000) / 1000)
            record['total_bytes'] = len(raw)
            retained = raw[:self._body_limit]
            while self._bytes + len(retained) > self._cache_limit:
                victim = next((r for r in self._records.values() if r is not record and r.get('response_body') is not None), None)
                if victim is None:
                    retained = retained[:max(0, self._cache_limit - self._bytes)]
                    break
                self._bytes -= len(victim['response_body'])
                victim['response_body'] = None
                victim['unavailable_reason'] = 'body_evicted'
            record['response_body'] = retained
            self._bytes += len(retained)
            record['truncated'] = len(retained) < len(raw)
            if record['truncated']:
                record['unavailable_reason'] = 'body_limit' if len(raw) > self._body_limit else 'cache_limit'
        except asyncio.TimeoutError:
            record['unavailable_reason'] = 'body_timeout'
        except Exception:
            record['unavailable_reason'] = 'body_unavailable'
        finally:
            await record['response'].dispose()
            record['response'] = None

    async def _dispose(self, record: dict[str, Any]) -> None:
        if record.get('response_body') is not None:
            self._bytes -= len(record['response_body'])
        if record.get('response') is not None:
            await record['response'].dispose()

    async def close(self) -> None:
        self._plans.clear()
        for record in self._records.values():
            await self._dispose(record)
        self._records.clear()
