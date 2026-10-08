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


class BrowserAgent:
    def __init__(self, session, provider, max_steps: int = 50, *,
                 approval: Callable | None = None, screenshot: bool = False,
                 max_text: int = 12000, history_limit: int = 8,
                 no_progress_limit: int = 3, verification_threshold: float = 0.9,
                 stale_limit: int = 3):
        if max_steps < 1 or history_limit < 1 or no_progress_limit < 1 or stale_limit < 0:
            raise ValueError("Agent bounds must be positive")
        if not 0 <= verification_threshold <= 1:
            raise ValueError("Verification threshold must be in [0, 1]")
        self.session = session
        self.provider = provider
        self.max_steps = max_steps
        self.approval = approval
        self.screenshot = screenshot
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
                 "elements": [{k: e.get(k) for k in ("role", "name", "value", "bounds", "operations")}
                              for e in observation.get("elements", [])]}
        return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()

    @staticmethod
    def _approval_reason(action: dict, observation: dict) -> str | None:
        operation = action["operation"]
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
            return result

        try:
            observation = await observe()
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
                action = {k: decision[k] for k in ("operation", "target", "key", "delta", "value", "x", "y", "to_target", "region", "depth") if k in decision}
                if action not in candidates.values():
                    status, error = "blocked", "Provider chose an unobserved or unsupported action"
                    break
                operation = action["operation"]
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
                    observation = await observe()
                    verification = await self.provider.verify(observation, goal, list(history))
                    account(verification)
                    status = "success" if verification.get("satisfied") is True and verification.get("probability", 0) >= self.verification_threshold else "verification_failed"
                    break
                action["observation_id"] = observation["id"]
                if operation == "fill":
                    text = await self.provider.field_text(observation, goal, action["target"], list(history))
                    account(getattr(self.provider, "last_metrics", {}))
                    action["text"] = text
                reason = self._approval_reason(action, observation)
                if reason:
                    binding = hashlib.sha256(json.dumps({"tab_id": tab_id, "action": action}, sort_keys=True).encode()).hexdigest()
                    pending = {"tab_id": tab_id, "observation_id": observation["id"],
                               "action": copy.deepcopy(action), "reason": reason, "binding": binding}
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
                        observation = await observe()
                        continue
                    status, error = "blocked", "Browser action failed; not replayed: " + type(exc).__name__
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
                observation = await observe()
        except asyncio.CancelledError:
            status = "cancelled"
        except ProviderRefusal:
            status, error = "refused", "Provider refused; no further actions executed"
        except ProviderError as exc:
            status, error = "provider_error", str(exc)
        except Exception as exc:
            status, error = "blocked", "Execution stopped: " + type(exc).__name__
        finally:
            self._running = False
        metrics["elapsed_ms"] = (time.perf_counter() - started) * 1000
        result = {"status": status, "steps": steps, "observation": observation,
                  "verification": verification, "metrics": metrics}
        if pending is not None:
            result["approval"] = pending
        if error is not None:
            result["error"] = error
        return result
