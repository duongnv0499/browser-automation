"""Local, transport-independent browser tool service. Never logs page contents."""
from __future__ import annotations

import asyncio
import importlib.util
import ipaddress
import json
from pathlib import Path
import os
import time
import uuid
from collections import OrderedDict
from urllib.parse import urlsplit


class ServiceError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def local_endpoint(endpoint: str) -> str:
    """CDP is unauthenticated control: accept literal loopback hosts only."""
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https", "ws", "wss"} or parsed.username or parsed.password:
        raise ServiceError("invalid_endpoint", "Use an explicit loopback HTTP or WebSocket CDP endpoint without credentials")
    host = parsed.hostname
    if host != "localhost":
        try:
            if not ipaddress.ip_address(host or "").is_loopback:
                raise ValueError()
        except ValueError:
            raise ServiceError("invalid_endpoint", "Only loopback CDP endpoints are accepted") from None
    return endpoint

def web_url(url: str) -> str:
    parsed = urlsplit(url)
    if url == "about:blank" or (parsed.scheme in {"http", "https"} and parsed.hostname):
        return url
    raise ServiceError("prohibited_url", "Browser tools allow only HTTP(S) pages and about:blank; local files and browser-internal pages are not readable")


class BrowserService:
    def __init__(self):
        self.sessions: dict[str, object] = {}
        self.locks: dict[str, asyncio.Lock] = {}
        self.snapshots: OrderedDict[tuple[str, str], dict] = OrderedDict()
        self._lifecycle = asyncio.Lock()
        self._closed = False
        self._used_approvals: set[str] = set()
        self.approvals_file = os.environ.get("BROWSER_APPROVALS_FILE")
        self.files_directory = os.environ.get("BROWSER_FILES_DIRECTORY")
        self.pending_actions: dict[tuple[str, str, str], dict] = {}

    def _approve(self, binding: dict, token: str | None = None) -> bool:
        if not self.approvals_file or not Path(self.approvals_file).exists():
            return False
        records = json.loads(Path(self.approvals_file).read_text())
        for record in records:
            identifier = record["token"]
            if identifier in self._used_approvals or (token is not None and identifier != token):
                continue
            if record.get("expires_at", 0) > time.time() and record.get("binding") == binding:
                self._used_approvals.add(identifier)
                return True
        return False

    async def dispatch(self, command: str, arguments: dict | None = None) -> dict:
        args = dict(arguments or {})
        if command == "doctor":
            return {"python_playwright": importlib.util.find_spec("playwright") is not None,
                    "openrouter_key": bool(os.environ.get("OPENROUTER_API_KEY")),
                    "openai_key": bool(os.environ.get("OPENAI_API_KEY")),
                    "transport": "local stdio", "sessions": list(self.sessions),
                    "native_consent": os.environ.get("BROWSER_NATIVE_CONSENT") == "1",
                    "native_guidance": "Enable Chrome remote debugging consent; set BROWSER_NATIVE_CONSENT=1 for connect_default. Explicit loopback CDP connect never falls back to isolated launch."}
        if command in {"launch", "connect", "connect_default"}:
            from .browser import BrowserSession
            async with self._lifecycle:
                if self._closed:
                    raise ServiceError("closed", "Service is closed")
                if command == "connect":
                    browser = await BrowserSession.connect(local_endpoint(args["endpoint"]))
                elif command == "connect_default":
                    browser = await BrowserSession.connect_default(profile_dir=os.environ.get("BROWSER_NATIVE_PROFILE_DIRECTORY"), consent=os.environ.get("BROWSER_NATIVE_CONSENT") == "1")
                else:
                    executable = os.environ.get("BROWSER_EXECUTABLE_PATH")
                    if args.get("executable_path") and args["executable_path"] != executable:
                        raise ServiceError("host_policy_required", "Browser executable path must be configured by host BROWSER_EXECUTABLE_PATH")
                    browser = await BrowserSession.launch(headless=args.get("headless", False), executable_path=executable)
                sid = uuid.uuid4().hex[:16]
                self.sessions[sid] = browser
                self.locks[sid] = asyncio.Lock()
                return {"session_id": sid, "mode": "isolated" if command == "launch" else "attached", "tabs": await browser.tabs()}
        sid = args.pop("session_id", None)
        if sid not in self.sessions:
            raise ServiceError("unknown_session", "Use launch or connect first and retain session_id")
        async with self.locks[sid]:
            browser = self.sessions.get(sid)
            if browser is None:
                raise ServiceError("unknown_session", "Session has closed")
            if command in {"observe", "act", "approved_act", "run", "upload", "download"}:
                web_url(await browser.tab_url(args["tab_id"]))
            if command == "tabs":
                return {"tabs": await browser.tabs()}
            if command == "new_tab":
                return {"tab": await browser.new_tab(web_url(args.get("url", "about:blank")))}
            if command == "observe":
                limit = args.get("max_text", 12000)
                if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100000:
                    raise ServiceError("invalid_argument", "max_text must be 1..100000")
                observation = await browser.observe(args["tab_id"], screenshot=args.get("screenshot", False), max_text=limit)
                full = dict(observation)
                full.pop("screenshot", None)
                key = (sid, observation["id"])
                self.snapshots[key] = full
                self.snapshots.move_to_end(key)
                while len(self.snapshots) > 8:
                    self.snapshots.popitem(last=False)
                return observation
            if command == "text":
                snapshot = self.snapshots.get((sid, args["observation_id"]))
                if snapshot is None:
                    raise ServiceError("unknown_observation", "Snapshot expired; observe again")
                offset, limit = args.get("offset", 0), args.get("limit", 12000)
                if any(not isinstance(x, int) or isinstance(x, bool) for x in (offset, limit)) or offset < 0 or not 1 <= limit <= 100000:
                    raise ServiceError("invalid_argument", "offset must be nonnegative and limit 1..100000")
                return await browser.text_continuation(snapshot["tab_id"], args["observation_id"], offset=offset, max_text=limit)
            if command == "act":
                from .agent import BrowserAgent
                action = args["action"]
                snapshot = self.snapshots.get((sid, action["observation_id"]))
                if snapshot is None or snapshot["tab_id"] != args["tab_id"]:
                    raise ServiceError("unknown_observation", "Observe this tab before acting")
                reason = BrowserAgent._approval_reason(action, snapshot)
                if reason:
                    import copy
                    binding = {"session_id": sid, "tab_id": args["tab_id"], "observation_id": action["observation_id"],
                               "action": copy.deepcopy(action), "reason": reason}
                    key = (sid, args["tab_id"], action["observation_id"])
                    expiry = time.time() + 300
                    self.pending_actions[key] = {"binding": binding, "expires_at": expiry}
                    while len(self.pending_actions) > 8:
                        self.pending_actions.pop(next(iter(self.pending_actions)))
                    return {"status": "approval_required", "binding": binding, "expires_at": expiry,
                            "host_command": "browser-agent approve --binding-file binding.json --approval-file /path/to/approvals.json", "resume_tool": "approved_act"}
                return await browser.act(args["tab_id"], action)
            if command == "approved_act":
                key = (sid, args["tab_id"], args["observation_id"])
                pending = self.pending_actions.get(key)
                if pending is None or pending["expires_at"] <= time.time():
                    raise ServiceError("approval_expired", "No matching pending action; run again")
                if not self._approve(pending["binding"], args["approval_token"]):
                    raise ServiceError("approval_required", "Exact host approval token required")
                del self.pending_actions[key]
                return await browser.act(args["tab_id"], pending["binding"]["action"])
            if command == "close_tab":
                await browser.close_tab(args["tab_id"])
                return {"closed_tab": args["tab_id"]}
            if command in {"upload", "download"}:
                if not self.files_directory:
                    raise ServiceError("file_policy_required", "Host must set BROWSER_FILES_DIRECTORY")
                binding = {"session_id": sid, "operation": command, "tab_id": args["tab_id"]}
                if command == "upload":
                    binding.update({"observation_id": args["observation_id"], "target": args["target"], "paths": args["paths"]})
                else:
                    binding.update({"action": args["action"], "destination": args["destination"]})
                if not self._approve(binding, args.get("approval_token")):
                    return {"status": "approval_required", "binding": binding, "expires_at": time.time() + 300,
                            "host_command": "browser-agent approve --binding-file binding.json --approval-file /path/to/approvals.json"}
                if command == "upload":
                    return await browser.upload(args["tab_id"], args["observation_id"], args["target"], args["paths"], allowed_directory=self.files_directory, approved=True)
                return await browser.download(args["tab_id"], args["action"], args["destination"], allowed_directory=self.files_directory, approved=True)
            if command == "run":
                from .agent import BrowserAgent
                from .providers import DecisionProvider
                async def approve(request):
                    return self._approve({"session_id": sid, **request})
                provider = DecisionProvider.from_env(provider=args.get("provider", "openrouter"), model=args.get("model"))
                try:
                    screenshot = args.get("screenshot")
                    if screenshot is None:
                        screenshot = provider.capabilities["vision"]
                    agent = BrowserAgent(browser, provider, max_steps=args.get("max_steps", 50), approval=approve, screenshot=screenshot)
                    result = await agent.run(args["tab_id"], args["goal"])
                    if result.get("status") == "approval_required" and result.get("approval"):
                        binding = {"session_id": sid, **result["approval"]}
                        cached = dict(result["observation"])
                        cached.pop("screenshot", None)
                        self.snapshots[(sid, cached["id"])] = cached
                        while len(self.snapshots) > 8:
                            self.snapshots.popitem(last=False)
                        expiry = time.time() + 300
                        key = (sid, args["tab_id"], binding["observation_id"])
                        self.pending_actions[key] = {"binding": binding, "expires_at": expiry}
                        while len(self.pending_actions) > 8:
                            self.pending_actions.pop(next(iter(self.pending_actions)))
                        result["host_approval"] = {"binding": binding, "expires_at": expiry,
                            "host_command": "browser-agent approve --binding-file binding.json --approval-file /path/to/approvals.json",
                            "resume_tool": "approved_act"}
                    return result
                finally:
                    await provider.close()
            if command == "close":
                await browser.close()
                self.sessions.pop(sid, None)
                self.locks.pop(sid, None)
                for key in list(self.snapshots):
                    if key[0] == sid:
                        del self.snapshots[key]
                for key in list(self.pending_actions):
                    if key[0] == sid:
                        del self.pending_actions[key]
                return {"closed": sid}
            raise ServiceError("unknown_command", f"Unknown command: {command}")

    async def close(self):
        async with self._lifecycle:
            self._closed = True
            failures = []
            for sid, browser in list(self.sessions.items()):
                async with self.locks[sid]:
                    try:
                        await browser.close()
                    except Exception as exc:
                        failures.append(exc)
                    finally:
                        self.sessions.pop(sid, None)
            self.snapshots.clear()
            self.pending_actions.clear()
            self.locks.clear()
            if failures:
                raise ExceptionGroup("Browser cleanup failures", failures)


def error_payload(exc: Exception) -> dict:
    # Errors are returned to the caller, never emitted to routine diagnostic logs.
    return {"code": getattr(exc, "code", type(exc).__name__), "message": str(exc)}
