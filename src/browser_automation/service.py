"""Local, transport-independent browser tool service. Never logs page contents."""
from __future__ import annotations

import asyncio
import anyio
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
        from .page_state import recovery_policy_from_env
        self.recovery_policy = recovery_policy_from_env()

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

    async def dispatch(self, command: str, arguments: dict | None = None, *, on_progress=None) -> dict:
        args = dict(arguments or {})
        from .mcp import validate_arguments
        validate_arguments(command, args)
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
                try:
                    tabs = await browser.tabs()
                except BaseException:
                    await browser.close()
                    raise
                sid = uuid.uuid4().hex[:16]
                self.sessions[sid] = browser
                self.locks[sid] = asyncio.Lock()
                return {"session_id": sid, "mode": "isolated" if command == "launch" else "attached", "tabs": tabs}
        sid = args.pop("session_id", None)
        if sid not in self.sessions:
            raise ServiceError("unknown_session", "Use launch or connect first and retain session_id")
        async with self.locks[sid]:
            browser = self.sessions.get(sid)
            if browser is None:
                raise ServiceError("unknown_session", "Session has closed")
            if command in {"observe", "act", "approved_act", "run", "upload", "download", "network_start", "network_list", "network_stop", "network_detail", "network_body", "network_calls", "network_call", "network_replay", "websocket_start", "websocket_list", "websocket_stop"}:
                web_url(await browser.tab_url(args["tab_id"]))
            if command == "tabs":
                return {"tabs": await browser.tabs()}
            if command == "new_tab":
                url = web_url(args.get("url", "about:blank"))
                return {"tab": await browser.new_tab(url, wait_until=args.get("wait_until", "domcontentloaded"), timeout_ms=args.get("timeout_ms", 15000))}
            if command == "observe":
                limit = args.get("max_text", 12000)
                if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100000:
                    raise ServiceError("invalid_argument", "max_text must be 1..100000")
                interpret_visual = args.get("interpret_visual", False)
                observation = await browser.observe(args["tab_id"], screenshot=args.get("screenshot", False) or interpret_visual, max_text=limit)
                from .page_state import compose_page_state
                visual = None
                if interpret_visual:
                    from .providers import DecisionProvider
                    provider = None
                    try:
                        provider = DecisionProvider.from_env(provider=args.get("provider", "openrouter"), model=args.get("model"))
                        visual = await provider.interpret_visual(observation)
                        observation["visual_summary"] = visual
                    except Exception as exc:
                        observation["visual_interpretation_error"] = error_payload(exc)
                    finally:
                        if provider is not None:
                            with anyio.move_on_after(5, shield=True):
                                await provider.close()
                observation["page_state"] = compose_page_state(observation, visual=visual, policy=self.recovery_policy)
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
                reason = BrowserAgent._approval_reason(action, snapshot, recovery_policy=self.recovery_policy)
                if reason:
                    import copy
                    binding = {"session_id": sid, "tab_id": args["tab_id"], "observation_id": action["observation_id"],
                               "action": copy.deepcopy(action), "reason": reason}
                    if action["operation"] == "reload":
                        from .page_state import WARNING
                        binding["recovery"] = copy.deepcopy(snapshot.get("page_state", {}).get("recovery_candidates", []))
                        binding["data_loss_warning"] = WARNING
                        binding["expected_rendered_result"] = "A fresh document observation, potentially still showing the error."
                        binding["provenance"] = snapshot.get("page_state", {}).get("source", "dom")
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
                    agent = BrowserAgent(browser, provider, max_steps=args.get("max_steps", 50), approval=approve, screenshot=screenshot, on_progress=on_progress, recovery_policy=self.recovery_policy, interpret_visual=args.get("interpret_visual", False))
                    result = await agent.run(args["tab_id"], args["goal"])
                    if result.get("status") == "approval_required" and result.get("approval"):
                        binding = {"session_id": sid, **result["approval"]}
                        cached = dict(result["observation"])
                        cached.pop("screenshot", None)
                        self.snapshots[(sid, cached["id"])] = cached
                        while len(self.snapshots) > 8:
                            self.snapshots.popitem(last=False)
                        expiry = time.time() + 300
                        key = (sid, binding["tab_id"], binding["observation_id"])
                        self.pending_actions[key] = {"binding": binding, "expires_at": expiry}
                        while len(self.pending_actions) > 8:
                            self.pending_actions.pop(next(iter(self.pending_actions)))
                        result["host_approval"] = {"binding": binding, "expires_at": expiry,
                            "host_command": "browser-agent approve --binding-file binding.json --approval-file /path/to/approvals.json",
                            "resume_tool": "approved_act"}
                    return result
                finally:
                    with anyio.move_on_after(5, shield=True):
                        await provider.close()
            if command in {"network_detail", "network_body", "network_calls"}:
                if args.get("include_sensitive", False) and os.environ.get("BROWSER_NETWORK_SENSITIVE") != "1":
                    raise ServiceError("host_policy_required", "Sensitive network disclosure requires host BROWSER_NETWORK_SENSITIVE=1; selected data is returned to the calling client/model")
                tab_id = args.pop("tab_id")
                return await getattr(browser, command)(tab_id, **args)
            if command in {"network_start_many", "network_list_many", "network_stop_many"}:
                return await getattr(browser, command)(**args)
            if command in {"network_call", "network_replay"}:
                tab_id = args.pop("tab_id")
                prepare_only = args.pop("prepare_only", False)
                if command == "network_replay" and args.get("target_tab_id"):
                    web_url(await browser.tab_url(args["target_tab_id"]))
                plan = await getattr(browser, command)(tab_id, **args)
                if not plan["approval_required"] and not prepare_only:
                    return await browser.network_execute(plan["plan_id"], approved=True)
                review = await browser.network_plan(plan["plan_id"])
                binding = {"session_id": sid, "operation": "network_execute", **review["binding"]}
                return {**plan, "binding": binding,
                        "host_command": "browser-agent approve --binding-file binding.json --approval-file /path/to/approvals.json",
                        "resume_tool": "network_execute"}
            if command == "network_execute":
                review = await browser.network_plan(args["plan_id"])
                binding = {"session_id": sid, "operation": "network_execute", **review["binding"]}
                web_url(await browser.tab_url(binding["target_tab_id"]))
                if not self._approve(binding, args["approval_token"]):
                    raise ServiceError("approval_required", "Exact host approval token required for this network plan")
                return await browser.network_execute(args["plan_id"], approved=True)
            if command in {"network_start", "network_list", "network_stop", "websocket_start", "websocket_list", "websocket_stop"}:
                kind, operation = command.split("_")
                tab_id = args.pop("tab_id")
                if operation == "start":
                    if args.get("payloads", False) and os.environ.get("BROWSER_MONITOR_PAYLOADS") != "1":
                        raise ServiceError("host_policy_required", "Text payload capture requires host BROWSER_MONITOR_PAYLOADS=1; metadata remains available")
                    return await browser.monitor_start(tab_id, kind, **args)
                if operation == "list":
                    return await browser.monitor_list(tab_id, kind, **args)
                return await browser.monitor_stop(tab_id, kind)
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
    code = getattr(exc, "code", None) or {KeyError: "invalid_argument", ValueError: "invalid_argument", TimeoutError: "timeout"}.get(type(exc), "operation_failed")
    result = {"code": code, "message": str(exc)}
    details = getattr(exc, "details", None)
    if isinstance(details, dict):
        result["details"] = details
    diagnostic = getattr(exc, "diagnostic", None)
    if isinstance(diagnostic, dict):
        result["diagnostic"] = diagnostic
    result["recommended_next_action"] = getattr(exc, "recommended_next_action", None) or ("reobserve" if code in {"stale_observation", "unknown_observation", "covered_target"} else "review_diagnostic")
    return result
