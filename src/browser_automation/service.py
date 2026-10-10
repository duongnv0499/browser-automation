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


from .approval_policy import ApprovalPolicyError, approval_mode_from_env, audit, classify_action, classify_navigation, classify_transfer, max_tier, requires_approval


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

    def _forget_tab(self, sid: str, *tab_ids: str | None) -> None:
        """Drop cached observations and paused approvals bound to tabs whose document may have changed."""
        tabs = {tab for tab in tab_ids if tab}
        for key in [key for key, snapshot in self.snapshots.items() if key[0] == sid and snapshot.get("tab_id") in tabs]:
            del self.snapshots[key]
        for key in [key for key in self.pending_actions if key[0] == sid and key[1] in tabs]:
            del self.pending_actions[key]

    async def _execute_act(self, sid: str, browser, tab_id: str, action: dict) -> dict:
        """Run a snapshot-bound action; forget the tab's cache once input may have been dispatched."""
        try:
            result = await browser.act(tab_id, action)
        except Exception as exc:
            # The engine invalidates its revisions right before input; a revision it
            # still holds (and that is not stale) proves no input was dispatched.
            holds = getattr(browser, "holds_observation", None)
            if getattr(exc, "code", None) == "stale_observation" or holds is None or not holds(tab_id, action.get("observation_id")):
                self._forget_tab(sid, tab_id)
            raise
        self._forget_tab(sid, tab_id)
        return result

    async def dispatch(self, command: str, arguments: dict | None = None, *, on_progress=None) -> dict:
        args = dict(arguments or {})
        from .mcp import validate_arguments
        validate_arguments(command, args)
        if command == "doctor":
            try:
                mode, mode_error = approval_mode_from_env(), None
            except ApprovalPolicyError as exc:
                mode, mode_error = None, str(exc)
            return {"python_playwright": importlib.util.find_spec("playwright") is not None,
                    "approval_mode": mode, **({"approval_mode_error": mode_error} if mode_error else {}),
                    "openrouter_key": bool(os.environ.get("OPENROUTER_API_KEY")),
                    "openai_key": bool(os.environ.get("OPENAI_API_KEY")),
                    "transport": "local stdio", "sessions": list(self.sessions),
                    "native_consent": os.environ.get("BROWSER_NATIVE_CONSENT") == "1",
                    "native_guidance": "Enable Chrome remote debugging consent; set BROWSER_NATIVE_CONSENT=1 for connect_default. Explicit loopback CDP connect never falls back to isolated launch."}
        # Host policy, re-read per call; an invalid value fails every tool call (never relaxed).
        mode = approval_mode_from_env()
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
            if command == "navigate":
                url = web_url(args["url"])
                tier, reason = classify_navigation(url)
                approval = audit(mode, tier, reason) if tier != "none" else None
                if requires_approval(tier, mode):
                    binding = {"session_id": sid, "operation": "navigate", "tab_id": args["tab_id"], "url": url, "reason": reason}
                    if not self._approve(binding, args.get("approval_token")):
                        return {"status": "approval_required", "binding": binding, "expires_at": time.time() + 300, "tier": tier, "approval_mode": mode,
                                "host_command": "browser-agent approve --binding-file binding.json --approval-file /path/to/approvals.json",
                                "resume_tool": "navigate (same arguments plus approval_token)"}
                    approval = audit(mode, tier, reason, source="host_token")
                try:
                    result = await browser.navigate(args["tab_id"], url, wait_until=args.get("wait_until", "domcontentloaded"), timeout_ms=args.get("timeout_ms", 15000))
                except Exception as exc:
                    if getattr(exc, "code", None) not in {"not_owned_tab", "invalid_argument"}:
                        self._forget_tab(sid, args["tab_id"])
                    raise
                self._forget_tab(sid, args["tab_id"])
                if approval is not None:
                    result["approval"] = approval
                return result
            if command == "observe":
                limit = args.get("max_text", 12000)
                if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100000:
                    raise ServiceError("invalid_argument", "max_text must be 1..100000")
                interpret_visual = args.get("interpret_visual", False)
                scope = args.get("text_scope", "viewport")
                observation = await browser.observe(args["tab_id"], screenshot=args.get("screenshot", False) or interpret_visual, max_text=limit, **({"text_scope": scope} if scope != "viewport" else {}))
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
                observation["page_state"] = compose_page_state(observation, visual=visual, policy=self.recovery_policy, mode=mode)
                # The cache always keeps the FULL observation: approval classification
                # needs href/form/submit metadata that compact output omits.
                full = dict(observation)
                full.pop("screenshot", None)
                key = (sid, observation["id"])
                self.snapshots[key] = full
                self.snapshots.move_to_end(key)
                while len(self.snapshots) > 8:
                    self.snapshots.popitem(last=False)
                if args.get("detail", "compact") == "full":
                    return observation
                return compact_observation(observation, visual_regions=args.get("visual_regions", False))
            if command == "text":
                snapshot = self.snapshots.get((sid, args["observation_id"]))
                if snapshot is None:
                    raise ServiceError("unknown_observation", "Snapshot expired; observe again")
                offset, limit = args.get("offset", 0), args.get("limit", 12000)
                if any(not isinstance(x, int) or isinstance(x, bool) for x in (offset, limit)) or offset < 0 or not 1 <= limit <= 100000:
                    raise ServiceError("invalid_argument", "offset must be nonnegative and limit 1..100000")
                return await browser.text_continuation(snapshot["tab_id"], args["observation_id"], offset=offset, max_text=limit)
            if command == "act":
                action = args["action"]
                snapshot = self.snapshots.get((sid, action["observation_id"]))
                if snapshot is None or snapshot["tab_id"] != args["tab_id"]:
                    raise ServiceError("unknown_observation", "Observe this tab before acting")
                tier, reason = classify_action(action, snapshot, recovery_policy=self.recovery_policy)
                if requires_approval(tier, mode):
                    # Revalidate (no input) before minting a pending approval: a stale
                    # observation fails now instead of after the human approves it.
                    try:
                        await browser.check_observation(args["tab_id"], action["observation_id"], targets=[t for t in (action.get("target"), action.get("to_target")) if isinstance(t, str)])
                    except Exception as exc:
                        if getattr(exc, "code", None) == "stale_observation":
                            self.snapshots.pop((sid, action["observation_id"]), None)
                            self.pending_actions.pop((sid, args["tab_id"], action["observation_id"]), None)
                        raise
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
                    self.pending_actions[key] = {"binding": binding, "expires_at": expiry, "tier": tier, "mode": mode}
                    while len(self.pending_actions) > 8:
                        self.pending_actions.pop(next(iter(self.pending_actions)))
                    return {"status": "approval_required", "binding": binding, "expires_at": expiry, "tier": tier, "approval_mode": mode,
                            "host_command": "browser-agent approve --binding-file binding.json --approval-file /path/to/approvals.json", "resume_tool": "approved_act"}
                result = await self._execute_act(sid, browser, args["tab_id"], action)
                if tier != "none":
                    result["approval"] = audit(mode, tier, reason)
                return result
            if command == "approved_act":
                key = (sid, args["tab_id"], args["observation_id"])
                pending = self.pending_actions.get(key)
                if pending is None or pending["expires_at"] <= time.time():
                    raise ServiceError("approval_expired", "No matching pending action; run again")
                if not self._approve(pending["binding"], args["approval_token"]):
                    raise ServiceError("approval_required", "Exact host approval token required")
                del self.pending_actions[key]
                action = pending["binding"]["action"]
                tier = pending.get("tier")
                if tier is None:  # Paused by run: tier the exact cached action.
                    cached = self.snapshots.get((sid, args["observation_id"]))
                    tier = classify_action(action, cached, recovery_policy=self.recovery_policy)[0] if cached else "ordinary"
                result = await self._execute_act(sid, browser, args["tab_id"], action)
                result["approval"] = audit(pending.get("mode", mode), tier, pending["binding"].get("reason"), source="host_token")
                return result
            if command == "close_tab":
                await browser.close_tab(args["tab_id"])
                self._forget_tab(sid, args["tab_id"])
                return {"closed_tab": args["tab_id"]}
            if command in {"upload", "download"}:
                if not self.files_directory:
                    raise ServiceError("file_policy_required", "Host must set BROWSER_FILES_DIRECTORY")
                binding = {"session_id": sid, "operation": command, "tab_id": args["tab_id"]}
                if command == "upload":
                    binding.update({"observation_id": args["observation_id"], "target": args["target"], "paths": args["paths"]})
                else:
                    binding.update({"action": args["action"], "destination": args["destination"]})
                tier, reason = classify_transfer(command)
                if command == "download":
                    # The triggering click is tiered too: a critical control never rides standing approval.
                    cached = self.snapshots.get((sid, args["action"].get("observation_id")))
                    click_tier, click_reason = classify_action(args["action"], cached or {}, recovery_policy=self.recovery_policy)
                    if max_tier(tier, click_tier) != tier:
                        tier, reason = click_tier, click_reason
                token = args.get("approval_token")
                if requires_approval(tier, mode):
                    if not self._approve(binding, token):
                        return {"status": "approval_required", "binding": binding, "expires_at": time.time() + 300, "tier": tier, "approval_mode": mode,
                                "host_command": "browser-agent approve --binding-file binding.json --approval-file /path/to/approvals.json"}
                    approval = audit(mode, tier, reason, source="host_token")
                elif token is not None and self._approve(binding, token):
                    approval = audit(mode, tier, reason, source="host_token")
                else:
                    # Autonomous standing approval; directory scope is still enforced by the engine.
                    approval = audit(mode, tier, reason)
                try:
                    if command == "upload":
                        result = await browser.upload(args["tab_id"], args["observation_id"], args["target"], args["paths"], allowed_directory=self.files_directory, approved=True)
                    else:
                        result = await browser.download(args["tab_id"], args["action"], args["destination"], allowed_directory=self.files_directory, approved=True)
                    return {**result, "approval": approval}
                finally:
                    # Approved transfers may set files or click; prior revisions are no longer trusted.
                    self._forget_tab(sid, args["tab_id"])
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
                    agent = BrowserAgent(browser, provider, max_steps=args.get("max_steps", 50), approval=approve, screenshot=screenshot, on_progress=on_progress, recovery_policy=self.recovery_policy, interpret_visual=args.get("interpret_visual", False), approval_mode=mode)
                    try:
                        result = await agent.run(args["tab_id"], args["goal"])
                    except BaseException:
                        self._forget_tab(sid, args["tab_id"])
                        raise
                    # Forget every tab the run may have driven BEFORE caching its paused state.
                    touched = {args["tab_id"], result.get("active_tab")}
                    for step in result.get("steps", []):
                        outcome = step.get("result") if isinstance(step, dict) else None
                        if isinstance(outcome, dict):
                            touched.update(outcome.get("popup_tabs", []))
                            touched.add(outcome.get("active_tab"))
                    self._forget_tab(sid, *touched)
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
                    if args.get("detail", "compact") != "full" and isinstance(result.get("observation"), dict):
                        result["observation"] = compact_observation(result["observation"])
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
                plan = await getattr(browser, command)(tab_id, approval_mode=mode, **args)
                if not plan["approval_required"] and not prepare_only:
                    result = await browser.network_execute(plan["plan_id"], approved=True)
                    if plan.get("approval_tier", "none") != "none":
                        result["approval"] = audit(mode, plan["approval_tier"], plan.get("approval_reason"))
                    return result
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
                result = await browser.network_execute(args["plan_id"], approved=True)
                result["approval"] = audit(review.get("approval_mode", mode), review.get("approval_tier", "consequential"), review.get("approval_reason"), source="host_token")
                return result
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


_VALUE_INPUT_TYPES_EXCLUDED = {"checkbox", "radio", "submit", "button", "reset", "image", "file", "hidden"}


def compact_observation(observation: dict, *, visual_regions: bool = False) -> dict:
    """Agent-facing observation: same revision/target IDs, without geometry, frames or signatures.

    Only non-default element state is included. Screenshot-grid targets stay valid in the
    engine but are listed only with visual_regions=True. Use detail="full" for bounds.
    """
    compact = {key: observation[key] for key in ("id", "tab_id", "url", "title", "text", "truncated", "next_offset", "text_length") if key in observation}
    coverage = observation.get("coverage")
    if isinstance(coverage, dict):
        compact["coverage"] = {"status": coverage.get("status", "unknown")}
        if coverage.get("status") == "partial":
            compact["coverage"]["reasons"] = list(coverage.get("reasons", []))
    page_state = observation.get("page_state")
    if isinstance(page_state, dict):
        compact["page_state"] = {key: page_state[key] for key in ("state", "summary", "source") if key in page_state}
    if observation.get("visible_alerts"):
        compact["visible_alerts"] = [{"role": alert.get("role"), "text": alert.get("text")} for alert in observation["visible_alerts"]]
    for key in ("screenshot_status", "screenshot", "diagnostic", "visual_summary", "visual_interpretation_error"):
        if key in observation:
            compact[key] = observation[key]
    compact["omitted_elements"] = observation.get("omitted_elements", 0)
    compact["limitations"] = len(observation.get("limitations") or [])
    elements, hidden_regions = [], 0
    page = urlsplit(str(observation.get("url") or ""))
    origin = f"{page.scheme}://{page.netloc}" if page.scheme in {"http", "https"} and page.netloc else None
    for element in observation.get("elements", []):
        role = element.get("role")
        if role == "visual-region" and not visual_regions:
            hidden_regions += 1
            continue
        operations = list(element.get("operations") or [])
        name = str(element.get("name") or "")
        if not operations and not name:
            continue
        item = {"id": element.get("id"), "role": role, "name": name[:120], "ops": operations}
        value = element.get("value")
        if value not in (None, "") and element.get("tag") in {"input", "textarea", "select"} and element.get("input_type") not in _VALUE_INPUT_TYPES_EXCLUDED:
            item["value"] = str(value)[:200]
        for flag in ("checked", "selected", "disabled", "covered", "sensitive", "multiple"):
            if element.get(flag) is True:
                item[flag] = True
        if isinstance(element.get("expanded"), bool):
            item["expanded"] = element["expanded"]
        if role == "link" and element.get("href"):
            href = str(element["href"])
            # Same-origin links as origin-relative paths (lossless against `url`): the
            # repeated origin was the largest remaining compact contributor on real pages.
            if origin and href.startswith(origin + "/"):
                href = href[len(origin):]
            item["href"] = href[:200]
        options = element.get("options") or []
        if options:
            item["options"] = [{"value": str(option.get("value", ""))[:200],
                                **({"label": str(option["label"])[:80]} if option.get("label") and option.get("label") != option.get("value") else {}),
                                **({"selected": True} if option.get("selected") else {}),
                                **({"disabled": True} if option.get("disabled") else {})} for option in options[:20]]
            omitted = max(0, len(options) - 20) + int(element.get("omitted_options") or 0)
            if omitted:
                item["omitted_options"] = omitted
        elements.append(item)
    compact["elements"] = elements
    if hidden_regions:
        compact["visual_regions_omitted"] = hidden_regions
    return compact


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
