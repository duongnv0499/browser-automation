"""Bounded agent execution with independent verification and exact-action consent."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
import json
import re
import time
from collections import deque
from typing import Callable

from .providers import ProviderError, ProviderRefusal, action_candidates
from .page_state import compose_page_state, recovery_policy_from_env, reload_approval_reason

class _ProgressDeliveryError(RuntimeError):
    """A failed host callback stops the run without exposing callback contents."""



class BrowserAgent:
    def __init__(self, session, provider, max_steps: int = 50, *,
                 approval: Callable | None = None, screenshot: bool = False,
                 max_text: int = 12000, history_limit: int = 8,
                 no_progress_limit: int = 3, verification_threshold: float = 0.9,
                 stale_limit: int = 3, interpret_visual: bool = False,
                 on_progress: Callable | None = None,
                 recovery_policy: dict | None = None):
        if max_steps < 1 or history_limit < 1 or no_progress_limit < 1 or stale_limit < 0 or max_text < 1:
            raise ValueError("Agent bounds must be positive")
        if not 0 <= verification_threshold <= 1:
            raise ValueError("Verification threshold must be in [0, 1]")
        self.session = session
        self.provider = provider
        self.max_steps = max_steps
        self.approval = approval
        self.interpret_visual = interpret_visual
        self.screenshot = screenshot or self.interpret_visual
        self.on_progress = on_progress
        self.recovery_policy = copy.deepcopy(recovery_policy) if recovery_policy is not None else recovery_policy_from_env()
        self.max_text = max_text
        self.history_limit = history_limit
        self.no_progress_limit = no_progress_limit
        self.verification_threshold = verification_threshold
        self.stale_limit = stale_limit
        self._running = False

    async def _observe(self, tab_id: str) -> dict:
        return await self.session.observe(tab_id, screenshot=self.screenshot, max_text=self.max_text)

    @staticmethod
    def _fingerprint(observation: dict) -> str:
        # Revisions/timestamps change even when the page does not. IDs may be revision-local.
        state = {"url": observation.get("url"), "text": observation.get("text"),
                 "action_page": observation.get("action_page", 0),
                 "point_refinements": observation.get("point_refinements", {}),
                 "text_offset": observation.get("text_offset", 0),
                 "screenshot_hash": hashlib.sha256(observation.get("screenshot", "").encode()).hexdigest(),
                 "evidence_offsets": observation.get("verification_evidence", {}).get("retained_offsets", []),
                 "elements": [{k: e.get(k) for k in ("role", "name", "value", "bounds", "operations", "selected_values", "options")}
                              for e in observation.get("elements", [])]}
        return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()

    @staticmethod
    def _approval_reason(action: dict, observation: dict, *, recovery_policy: dict | None = None) -> str | None:
        operation = action["operation"]
        if operation == "reload":
            return reload_approval_reason(observation, recovery_policy)
        element = next((e for e in observation.get("elements", []) if e.get("id") == action.get("target")), {})
        description = " ".join(str(element.get(k, "")) for k in ("name", "role", "type", "input_type", "href", "form_action")).lower()
        activation_key = str(action.get("key", "")).split("+")[-1].lower()
        if operation == "press" and activation_key in {"enter", "return", "space", "spacebar", " "}:
            return "Keyboard activation may submit, confirm, or activate a consequential control"
        if operation in {"fill", "select"} and (element.get("sensitive") or "password" in description or re.search(r"credit.?card|payment|social security|secret|token|cvv", description)):
            return "Sensitive field input"
        if operation == "click":
            risk_description = " ".join(str(element.get(k) or "") for k in ("name", "role", "href", "form_action")).lower()
            submits = element.get("is_submit") or element.get("input_type") == "submit" and bool(element.get("form_action"))
            if submits or element.get("risky") or re.search(r"buy|pay|purchase|checkout|order|delete|remove|send|submit|publish|post|transfer|confirm|accept|authorize|sign.?in|log.?in|upload|download|subscribe|unsubscribe", risk_description):
                return "Potential submission, disclosure, account change, payment, or destructive action"
            label = str(element.get("name", "")).strip().lower()
            benign = re.fullmatch(r"(?:search|find|next|previous|back|expand|collapse|open menu|close menu|menu|show more|show less|close|cancel)(?:\s+results)?", label)
            if benign and not element.get("risky") and not submits and str(element.get("form_method") or "").lower() != "post":
                return None
            # Custom controls and visual-only targets cannot be classified reliably.
            if element.get("role") not in {"link", "checkbox", "radio", "tab", "option", "menuitem"}:
                return "Button/custom control may have consequential side effects"
        if operation == "drag":
            return "Drag/drop may move, upload, or mutate content"
        return None

    @staticmethod
    def _is_preinput_stale(exc: Exception) -> bool:
        return getattr(exc, "code", None) in {"stale_observation", "stale"} or type(exc).__name__ == "StaleObservationError"

    async def run(self, tab_id: str, goal: str) -> dict:
        if self._running:
            raise RuntimeError("BrowserAgent cannot run concurrently")
        if not isinstance(goal, str) or not goal.strip():
            raise ValueError("Goal must be a nonempty string")
        self._running = True
        started = time.perf_counter()
        history: deque = deque(maxlen=self.history_limit)
        steps: list[dict] = []
        observation = None
        verification = None
        metrics = {"browser_ms": 0.0, "provider_ms": 0.0, "context_chars": 0,
                   "provider_calls": 0, "input_tokens": 0, "output_tokens": 0,
                   "stale_reobservations": 0}
        pending = None
        error = None
        status = "step_limit"
        last_fingerprint = None
        unchanged = 0
        stale_count = 0
        known_tabs = {tab_id: {"id": tab_id}}
        text_offsets: dict[int, int] = {}
        evidence_budget = max(0, getattr(self.provider, "max_context_chars", 48000) - self.max_text - len(goal) - 8000)

        progress_seq = 0
        last_event = None
        last_event_at = 0.0
        progress_disabled = False
        error_diagnostic = None

        def browser_diagnostic(exc: Exception) -> dict:
            from .browser import BrowserError
            from .service import error_payload
            if isinstance(exc, BrowserError):
                return error_payload(exc)
            return {"code": "browser_action_failed", "message": type(exc).__name__,
                    "recommended_next_action": "reobserve_and_review_no_replay"}

        async def emit(checkpoint: str, page: dict | None = None, *, terminal_status: str | None = None) -> None:
            nonlocal progress_seq, last_event, last_event_at, progress_disabled
            if self.on_progress is None or progress_disabled:
                return
            descriptor = (page or {}).get("page_state", {})
            # No page/model text, URL, screenshot, or typed action values cross this channel.
            semantic = {"state": descriptor.get("state", "unknown"),
                        "source": descriptor.get("source", "dom"),
                        "coverage_status": descriptor.get("coverage_status", "unknown"),
                        "recovery_available": bool(descriptor.get("recovery_candidates")),
                        "approval_required": any(c.get("approval_required", True) for c in descriptor.get("recovery_candidates", [])),
                        "capture_scope": "page_viewport_not_desktop"}
            signature = (checkpoint, terminal_status, json.dumps(semantic, sort_keys=True), len(steps))
            now = time.monotonic()
            if signature == last_event and now - last_event_at < 0.25:
                return
            progress_seq += 1
            last_event, last_event_at = signature, now
            event = {"seq": progress_seq, "checkpoint": checkpoint, "step": len(steps),
                     "status": terminal_status or "running", "page_state": semantic}
            try:
                callback_result = self.on_progress(event)
                if inspect.isawaitable(callback_result):
                    await callback_result
            except asyncio.CancelledError:
                progress_disabled = True
                raise
            except Exception:
                progress_disabled = True
                raise _ProgressDeliveryError("Progress callback failed; run stopped without further input") from None
        def evidence_metadata(page: dict) -> None:
            page["verification_evidence"] = {"retained_offsets": list(text_offsets),
                "retained_chars": sum(text_offsets.values()), "continuation_budget_chars": evidence_budget,
                "over_budget": sum(text_offsets.values()) > evidence_budget,
                "instructions": "All inspected continuation windows are retained until explicitly drop_evidence. Before DONE discard irrelevant windows if over budget; omitted evidence cannot prove a goal condition."}

        def account(result: dict) -> None:
            metrics["provider_calls"] += 1
            metrics["provider_ms"] += result.get("latency_ms", 0)
            metrics["context_chars"] += result.get("context_chars", 0)
            usage = result.get("usage") or {}
            metrics["input_tokens"] += usage.get("input_tokens", usage.get("prompt_tokens", 0))
            metrics["output_tokens"] += usage.get("output_tokens", usage.get("completion_tokens", 0))

        async def observe() -> dict:
            begin = time.perf_counter()
            result = await self._observe(tab_id)
            metrics["browser_ms"] += (time.perf_counter() - begin) * 1000
            if self.interpret_visual:
                visual = await self.provider.interpret_visual(result)
                account(getattr(self.provider, "last_metrics", {}))
                result["visual_summary"] = visual
            result["page_state"] = compose_page_state(result, result.get("visual_summary"), self.recovery_policy)
            result["text_offset"] = 0
            known_tabs[tab_id] = {"id": tab_id, "url": result.get("url"), "title": result.get("title")}
            result["available_tabs"] = [tab for id_, tab in known_tabs.items() if id_ != tab_id]
            text_offsets.clear()
            evidence_metadata(result)
            await emit("observe", result)
            return result

        async def read_text(offset: int) -> dict:
            begin = time.perf_counter()
            chunk = await self.session.text_continuation(tab_id, observation["id"], offset=offset, max_text=self.max_text)
            metrics["browser_ms"] += (time.perf_counter() - begin) * 1000
            return chunk

        try:
            observation = await observe()
            known_tabs[tab_id] = {"id": tab_id, "url": observation.get("url"), "title": observation.get("title")}
            for _ in range(self.max_steps):
                fingerprint = self._fingerprint(observation)
                unchanged = unchanged + 1 if fingerprint == last_fingerprint else 0
                last_fingerprint = fingerprint
                if unchanged >= self.no_progress_limit:
                    status = "no_progress"
                    break
                decision = await self.provider.choose(observation, goal, list(history))
                account(decision)
                # Validate even custom providers: no model JS/selectors/extra arguments.
                candidates = action_candidates(observation, getattr(self.provider, "max_choices", 256))
                action = {k: decision[k] for k in ("operation", "target", "key", "delta", "value", "x", "y", "to_target", "region", "depth", "tab_id", "offset") if k in decision}
                if action not in candidates.values():
                    status, error = "blocked", "Provider chose an unobserved or unsupported action"
                    break
                operation = action["operation"]
                if operation in {"next_text", "previous_text"}:
                    offset = observation["next_offset"] if operation == "next_text" else max(0, observation.get("text_offset", 0) - self.max_text)
                    chunk = await read_text(offset)
                    observation.update({k: v for k, v in chunk.items() if k not in {"observation_id", "offset"}})
                    observation["text_offset"] = offset
                    if offset > 0:
                        text_offsets[offset] = len(chunk["text"])
                    evidence_metadata(observation)
                    history.append({"operation": operation, "offset": offset, "result": "read same-revision text without browser input"})
                    continue
                if operation == "drop_evidence":
                    text_offsets.pop(action["offset"], None)
                    evidence_metadata(observation)
                    history.append({"operation": operation, "offset": action["offset"], "result": "explicitly excluded from verifier evidence"})
                    continue
                if operation == "switch_tab":
                    tab_id = action["tab_id"]
                    observation = await observe()
                    steps.append({"action": action, "result": {"active_tab": tab_id}})
                    history.append({"operation": operation, "tab_id": tab_id, "result": "explicitly selected observed popup"})
                    continue
                if operation in {"next_actions", "previous_actions"}:
                    observation["action_page"] = max(0, observation.get("action_page", 0) + (1 if operation == "next_actions" else -1))
                    history.append({"operation": operation, "action_page": observation["action_page"], "result": "inspected without browser input"})
                    continue
                if operation == "refine_point":
                    observation.setdefault("point_refinements", {})[action["target"]] = {"bounds": action["region"], "depth": action["depth"]}
                    history.append({"operation": operation, "target": action["target"], "region": action["region"], "result": "refined without browser input"})
                    continue
                if operation == "blocked":
                    status = "blocked"
                    break
                if operation == "done":
                    # Fresh evidence and a separate request, not decision confidence.
                    offsets = list(text_offsets)
                    if sum(text_offsets.values()) > evidence_budget:
                        status, error = "blocked", "Verification evidence exceeds explicit budget; choose drop_evidence for irrelevant windows before DONE"
                        break
                    observation = await observe()
                    windows = []
                    for offset in offsets:
                        if offset > 0:
                            chunk = await read_text(offset)
                            windows.append({"offset": offset, "text": chunk["text"], "next_offset": chunk.get("next_offset"), "source_truncated": chunk.get("source_truncated", False)})
                    if sum(len(window["text"]) for window in windows) > evidence_budget:
                        status, error = "blocked", "Fresh verification evidence exceeds explicit character budget"
                        break
                    if windows:
                        observation["text_windows"] = windows
                    observation["verification_text_offsets"] = offsets
                    verification = await self.provider.verify(observation, goal, list(history))
                    account(verification)
                    status = "success" if verification.get("satisfied") is True and verification.get("probability", 0) >= self.verification_threshold else "verification_failed"
                    break
                action["observation_id"] = observation["id"]
                if operation == "fill":
                    text = await self.provider.field_text(observation, goal, action["target"], list(history))
                    account(getattr(self.provider, "last_metrics", {}))
                    action["text"] = text
                reason = self._approval_reason(action, observation, recovery_policy=self.recovery_policy)
                if reason:
                    binding = hashlib.sha256(json.dumps({"tab_id": tab_id, "action": action}, sort_keys=True).encode()).hexdigest()
                    pending = {"tab_id": tab_id, "observation_id": observation["id"],
                               "action": copy.deepcopy(action), "reason": reason, "binding": binding}
                    if operation == "reload":
                        pending["recovery"] = copy.deepcopy(observation["page_state"]["recovery_candidates"])
                    await emit("needs_user", observation, terminal_status="approval_required")
                    if self.approval is None:
                        status = "approval_required"
                        break
                    granted = self.approval(copy.deepcopy(pending))
                    if inspect.isawaitable(granted):
                        granted = await granted
                    if granted is not True:
                        status = "approval_required"
                        break
                    pending = None
                begin = time.perf_counter()
                try:
                    result = await self.session.act(tab_id, action)
                except Exception as exc:
                    metrics["browser_ms"] += (time.perf_counter() - begin) * 1000
                    if self._is_preinput_stale(exc) and stale_count < self.stale_limit:
                        stale_count += 1
                        metrics["stale_reobservations"] += 1
                        history.append({"operation": operation, "result": "stale_before_input; reobserve without replay"})
                        await emit("reobserve", observation)
                        observation = await observe()
                        continue
                    status, error = "blocked", "Browser action failed; not replayed: " + type(exc).__name__
                    error_diagnostic = browser_diagnostic(exc)
                    break
                metrics["browser_ms"] += (time.perf_counter() - begin) * 1000
                stale_count = 0
                if isinstance(result, dict) and result.get("ok") is False:
                    status, error = "blocked", "Browser action rejected; not replayed"
                    break
                record = {"action": copy.deepcopy(action), "result": result,
                          "confidence": decision.get("confidence")}
                steps.append(record)
                # Bounded compact history never includes screenshots or arbitrary result bodies.
                history.append({"operation": operation, "target": action.get("target"),
                                "url": observation.get("url"), "result": "executed"})
                popup_ids = result.get("popup_tabs", []) if isinstance(result, dict) else []
                if popup_ids:
                    for tab in await self.session.tabs():
                        if tab["id"] in popup_ids:
                            known_tabs[tab["id"]] = tab
                observation = await observe()
        except asyncio.CancelledError:
            status = "cancelled"
        except _ProgressDeliveryError:
            status, error = "progress_error", "Progress callback failed; run stopped without further input"
            error_diagnostic = {"code": "progress_delivery_failed", "message": error,
                                "recommended_next_action": "repair_progress_consumer_then_reobserve"}
        except ProviderRefusal:
            status, error = "refused", "Provider refused; no further actions executed"
        except ProviderError as exc:
            status, error = "provider_error", str(exc)
        except Exception as exc:
            status, error = "blocked", "Execution stopped: " + type(exc).__name__
            error_diagnostic = browser_diagnostic(exc)
        finally:
            self._running = False
        metrics["elapsed_ms"] = (time.perf_counter() - started) * 1000
        try:
            await emit("cancelled" if status == "cancelled" else "needs_user" if status == "approval_required" else "blocked" if status in {"blocked", "no_progress", "refused", "provider_error", "verification_failed", "step_limit", "progress_error"} else "done", observation, terminal_status=status)
        except _ProgressDeliveryError:
            status, error = "progress_error", "Final progress delivery failed; no further input executed"
            error_diagnostic = {"code": "progress_delivery_failed", "message": error,
                                "recommended_next_action": "repair_progress_consumer_then_reobserve"}
        except asyncio.CancelledError:
            status = "cancelled"
        result = {"status": status, "steps": steps, "observation": observation,
                  "verification": verification, "metrics": metrics, "active_tab": tab_id}
        if pending is not None:
            result["approval"] = pending
        if error is not None:
            result["error"] = error
        if error_diagnostic is not None:
            result["error_diagnostic"] = error_diagnostic
        return result
