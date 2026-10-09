"""Authenticated host-local browser tools over the official MCP HTTP transport.

Modern requests share browser state only within a configured bearer principal.
Legacy sessions additionally isolate state by the SDK-issued MCP session ID.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import hmac
import ipaddress
import json
import math
import os
import time

from .mcp import INSTRUCTIONS, TOOLS, tool_result
from .service import BrowserService, ServiceError, error_payload


def credentials_from_env():
    single = os.environ.get("BROWSER_MCP_TOKEN")
    multiple = os.environ.get("BROWSER_MCP_TOKENS")
    if bool(single) == bool(multiple):
        raise ValueError("Configure exactly one of BROWSER_MCP_TOKEN or BROWSER_MCP_TOKENS")
    if multiple and len(multiple) > 65536:
        raise ValueError("BROWSER_MCP_TOKENS exceeds 65536 characters")
    credentials = json.loads(multiple) if multiple else {"default": single}
    if not isinstance(credentials, dict) or not credentials or len(credentials) > 64:
        raise ValueError("BROWSER_MCP_TOKENS must map 1..64 identities to bearer tokens")
    if any(not isinstance(k, str) or not k or not isinstance(v, str) or len(v) < 16
           or not v.isascii() or any(c.isspace() for c in v) for k, v in credentials.items()):
        raise ValueError("Bearer identities must be nonempty; tokens must be ASCII, at least 16 characters, without whitespace")
    if len(set(credentials.values())) != len(credentials):
        raise ValueError("Each bearer identity must have a distinct token")
    return credentials


@dataclass
class OwnedService:
    service: BrowserService = field(default_factory=BrowserService)
    touched: float = field(default_factory=time.monotonic)
    active: int = 0
    admission: asyncio.Lock = field(default_factory=asyncio.Lock)


class ServiceRegistry:
    def __init__(self, maximum, idle_timeout):
        self.maximum = maximum
        self.idle_timeout = idle_timeout
        self.entries = {}
        self.legacy_owners = {}
        self.lock = asyncio.Lock()

    async def acquire(self, principal, session_id=None):
        key = (principal, session_id)
        async with self.lock:
            entry = self.entries.get(key)
            if entry is None:
                if len(self.entries) >= self.maximum:
                    raise ServiceError("capacity", "HTTP ownership capacity reached; close or wait for idle expiry")
                entry = self.entries[key] = OwnedService()
            entry.active += 1
            entry.touched = time.monotonic()
            return entry

    def release(self, entry):
        entry.active -= 1
        entry.touched = time.monotonic()

    async def discard(self, key):
        async with self.lock:
            entry = self.entries.pop(key, None)
            if key[1]:
                self.legacy_owners.pop(key[1], None)
        if entry:
            await entry.service.close()

    async def reap(self):
        while True:
            await asyncio.sleep(min(self.idle_timeout / 2, 30))
            async with self.lock:
                keys = [key for key, entry in self.entries.items()
                        if not entry.active and time.monotonic() - entry.touched >= self.idle_timeout]
                expired = [(key, self.entries.pop(key)) for key in keys]
                for key in keys:
                    if key[1]:
                        self.legacy_owners.pop(key[1], None)
            for _, entry in expired:
                try:
                    await entry.service.close()
                except Exception:
                    # Cleanup attempts every scope; never log browser/page data.
                    import logging
                    logging.getLogger(__name__).error("Idle browser cleanup failed")

    async def close(self):
        failures = []
        for key in list(self.entries):
            try:
                await self.discard(key)
            except Exception as exc:
                failures.append(exc)
        if failures:
            raise ExceptionGroup("HTTP browser cleanup failures", failures)


class SecurityBoundary:
    def __init__(self, app, credentials, hosts, origins, registry):
        self.app, self.credentials = app, credentials
        self.hosts, self.origins, self.registry = set(hosts), set(origins), registry

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        from starlette.responses import JSONResponse
        from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
        from mcp.server.auth.provider import AccessToken
        headers = {}
        for name, value in scope["headers"]:
            headers.setdefault(name.lower(), []).append(value.decode("latin-1"))
        async def reject(status, message):
            await JSONResponse({"error": message}, status_code=status,
                               headers={"WWW-Authenticate": "Bearer"} if status == 401 else None)(scope, receive, send)
        if len(headers.get(b"host", [])) != 1 or headers[b"host"][0].lower() not in self.hosts:
            await reject(421, "Host not allowed")
            return
        origins = headers.get(b"origin", [])
        if len(origins) > 1 or (origins and origins[0] not in self.origins):
            await reject(403, "Origin not allowed")
            return
        auth = headers.get(b"authorization", [])
        principal = None
        if len(auth) == 1 and auth[0][:7].lower() == "bearer ":
            token = auth[0][7:].encode("utf-8")
            for identity, expected in self.credentials.items():
                if hmac.compare_digest(token, expected.encode("ascii")):
                    principal = identity
        if principal is None:
            await reject(401, "Authentication required")
            return
        session_headers = headers.get(b"mcp-session-id", [])
        if len(session_headers) > 1:
            await reject(400, "Duplicate MCP session header")
            return
        session_id = session_headers[0] if session_headers else None
        if session_id and self.registry.legacy_owners.get(session_id) != principal:
            await reject(404, "Session not found")
            return
        scope = dict(scope)
        scope["browser_principal"] = principal
        # Give SDK its public authenticated identity so its legacy lifecycle also
        # checks credential ownership; never use forwarded headers as identity.
        scope["user"] = AuthenticatedUser(AccessToken(token="", client_id=principal, scopes=[]))
        entry = None
        if session_id:
            entry = self.registry.entries.get((principal, session_id))
            if entry:
                entry.active += 1
        status = None
        async def guarded_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                for name, value in message.get("headers", []):
                    if name.lower() == b"mcp-session-id" and status == 200:
                        sid = value.decode("ascii")
                        allocated = await self.registry.acquire(principal, sid)
                        self.registry.legacy_owners[sid] = principal
                        self.registry.release(allocated)
            await send(message)
        try:
            await self.app(scope, receive, guarded_send)
        finally:
            if entry:
                self.registry.release(entry)
            if session_id and ((scope["method"] == "DELETE" and status == 200) or status == 404):
                await self.registry.discard((principal, session_id))


def create_app(*, credentials=None, host="127.0.0.1", port=8767, allow_hosts=(), allow_origins=(),
               max_sessions=64, session_idle_timeout=1800, max_request_body_size=1048576,
               max_browser_sessions=8):
    """Build one single-process ASGI app; HTTP dependencies are optional."""
    from jsonschema import Draft202012Validator
    from mcp.server import Server
    from mcp.server.transport_security import TransportSecuritySettings
    from mcp.types import CallToolResult, ListToolsResult, Tool
    from starlette.applications import Starlette
    from starlette.routing import Mount
    if any(not isinstance(v, int) or isinstance(v, bool) or v <= 0
           for v in (max_sessions, max_request_body_size, max_browser_sessions)):
        raise ValueError("HTTP limits must be positive integers")
    if not math.isfinite(session_idle_timeout) or session_idle_timeout <= 0:
        raise ValueError("session-idle-timeout must be positive and finite")
    credentials = credentials if credentials is not None else credentials_from_env()
    try:
        loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if not loopback and not allow_hosts:
        raise ValueError("Network binding requires explicit --allow-host authorities")
    hosts = [h.lower() for h in allow_hosts] or [f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"]
    if any(not h or any(c in h for c in "/*\\ \t\r\n") for h in hosts):
        raise ValueError("allow-host must be an exact host[:port] authority, without wildcards")
    origins = list(allow_origins)
    # SDK bounds legacy protocol sessions; configured identities bound modern
    # persistent scopes. Reserve room for both without coupling SDK internals.
    registry = ServiceRegistry(max_sessions + len(credentials), session_idle_timeout)
    schemas = {name: Draft202012Validator(spec) for name, _, spec in TOOLS}

    async def list_tools(ctx, params):
        return ListToolsResult(tools=[Tool(name=name, description=desc, input_schema=spec) for name, desc, spec in TOOLS])

    async def call_tool(ctx, params):
        entry = None
        progress = 0
        async def report(event):
            nonlocal progress
            if not asyncio.current_task().cancelling():
                progress += 1
                await ctx.session.report_progress(progress, message=json.dumps(event, ensure_ascii=False, separators=(",", ":")))
        try:
            if params.name not in schemas:
                raise ServiceError("unknown_tool", "Unknown tool")
            args = params.arguments or {}
            schemas[params.name].validate(args)
            principal = ctx.request.scope["browser_principal"]
            sid = ctx.request.headers.get("mcp-session-id")
            entry = await registry.acquire(principal, sid)
            # Serialize lifecycle admission with creation; BrowserService itself
            # serializes browser operations and retains its native safety rules.
            if params.name in {"launch", "connect", "connect_default"}:
                async with entry.admission:
                    if len(entry.service.sessions) >= max_browser_sessions:
                        raise ServiceError("capacity", "Browser session limit reached; close an owned session first")
                    result = await entry.service.dispatch(params.name, args, on_progress=report)
            else:
                result = await entry.service.dispatch(params.name, args, on_progress=report)
            if params.name == "doctor":
                result["transport"] = "streamable-http"
            return CallToolResult.model_validate(tool_result(result))
        except Exception as exc:
            data = {"error": error_payload(exc)}
            return CallToolResult.model_validate({**tool_result(data), "isError": True})
        finally:
            if entry:
                registry.release(entry)

    server = Server("browser-automation", version="0.1.0", instructions=INSTRUCTIONS,
                    on_list_tools=list_tools, on_call_tool=call_tool)
    sdk_app = server.streamable_http_app(
        max_request_body_size=max_request_body_size, session_idle_timeout=session_idle_timeout,
        max_sessions=max_sessions, transport_security=TransportSecuritySettings(
            allowed_hosts=hosts, allowed_origins=origins))

    @asynccontextmanager
    async def lifespan(app):
        reaper = asyncio.create_task(registry.reap())
        try:
            async with sdk_app.router.lifespan_context(sdk_app):
                yield
        finally:
            reaper.cancel()
            await asyncio.gather(reaper, return_exceptions=True)
            await registry.close()

    app = Starlette(routes=[Mount("/", sdk_app)], lifespan=lifespan)
    app.state.browser_registry = registry
    app.state.mcp_server = server
    return SecurityBoundary(app, credentials, hosts, origins, registry)


def run(args):
    try:
        import uvicorn
        app = create_app(host=args.host, port=args.port, allow_hosts=args.allow_host,
                         allow_origins=args.allow_origin, max_sessions=args.max_sessions,
                         session_idle_timeout=args.session_idle_timeout,
                         max_request_body_size=args.max_request_body_size,
                         max_browser_sessions=args.max_browser_sessions)
    except ImportError as exc:
        raise ValueError("HTTP transport requires optional dependencies: uv sync --extra http") from exc
    uvicorn.run(app, host=args.host, port=args.port, proxy_headers=False, access_log=False)
