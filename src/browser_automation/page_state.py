"""Bounded, explicitly heuristic page evidence and host-only recovery policy."""
from __future__ import annotations

import json
import os
import re
from urllib.parse import urlsplit

STATES = {"loading", "error", "login", "captcha", "ready", "unknown"}
WARNING = "Reload can discard unsaved data and repeat a previous request."


def _origin(url: str) -> str | None:
    try:
        p = urlsplit(url)
        if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
            return None
        port = p.port
        host = f"[{p.hostname}]" if ":" in p.hostname else p.hostname
        return f"{p.scheme}://{host}" + (f":{port}" if port and port != (443 if p.scheme == "https" else 80) else "")
    except ValueError:
        return None


def recovery_policy_from_env() -> dict:
    raw = os.getenv("BROWSER_RECOVERY_POLICY", "{}")
    try:
        policy = json.loads(raw)
    except ValueError:
        raise ValueError("BROWSER_RECOVERY_POLICY must be a JSON object") from None
    if not isinstance(policy, dict) or set(policy) - {"reload_without_approval_origins"}:
        raise ValueError("Invalid BROWSER_RECOVERY_POLICY fields")
    origins = policy.get("reload_without_approval_origins", [])
    if not isinstance(origins, list) or len(origins) > 128 or any(not isinstance(x, str) or _origin(x) != x for x in origins):
        raise ValueError("Recovery policy requires canonical HTTP(S) origins, not URLs or wildcards")
    return {"reload_without_approval_origins": list(dict.fromkeys(origins))}


def describe_dom(observation: dict) -> dict:
    elements = observation.get("elements", [])
    text = str(observation.get("text", ""))[:24000]
    # This is evidence classification, not OCR or permission from page content.
    evidence = []
    alerts = []
    for e in elements:
        if e.get("role") in {"alert", "status"} and e.get("name"):
            alerts.append({"target": e.get("id"), "text": str(e["name"])[:240], "source": "dom"})
    haystack = (text + "\n" + "\n".join(str(e.get("name", ""))[:240] for e in elements)).lower()
    patterns = [("captcha", r"\bcaptcha\b|verify (?:that )?you are human|human verification"),
                ("error", r"something went wrong|failed to load|unable to load|an error occurred|service unavailable|page (?:isn't|is not) working"),
                ("login", r"sign in to continue|log in to continue|authentication required"),
                ("loading", r"\bloading\b|please wait")]
    state = "unknown"
    for candidate, pattern in patterns:
        match = re.search(pattern, haystack)
        if match:
            state = candidate
            evidence.append({"kind": "visible_text_match", "text": match.group(0)[:120], "source": "dom"})
            break
    if state == "unknown" and any(e.get("input_type") == "password" or e.get("type") == "password" for e in elements):
        state = "login"
        evidence.append({"kind": "password_control", "source": "dom"})
    ready = observation.get("ready_state", observation.get("metadata", {}).get("ready_state"))
    if state == "unknown" and ready == "loading":
        state = "loading"
        evidence.append({"kind": "document_ready_state", "value": ready, "source": "dom"})
    coverage = observation.get("coverage", {}).get("status", "unknown")
    if state == "unknown" and coverage == "complete" and (text.strip() or elements) and ready in {"interactive", "complete"}:
        state = "ready"
        evidence.append({"kind": "rendered_content", "source": "dom"})
    result = {"state": state, "summary": {"unknown": "Insufficient DOM evidence to classify page state.", "ready": "Rendered content is available; task success is not established.", "loading": "DOM evidence suggests loading.", "error": "DOM evidence suggests a page error.", "login": "DOM evidence suggests authentication is required.", "captcha": "DOM evidence suggests human verification is required."}[state],
              "source": "dom", "confidence": {"kind": "heuristic", "calibrated": False},
              "evidence": evidence[:8], "visible_alerts": alerts[:8], "recovery_candidates": [],
              "coverage_status": coverage, "capture_scope": "page_viewport_not_desktop"}
    if state == "error":
        result["recovery_candidates"] = [_reload_candidate("dom")]
    return result


def _reload_candidate(source: str) -> dict:
    return {"action": {"operation": "reload"}, "reason": "Reobserve after an explicitly chosen page reload; success is not guaranteed.",
            "expected_rendered_result": "A fresh document observation, potentially still showing the error.",
            "data_loss_warning": WARNING, "approval_required": True, "provenance": source}


def reload_approval_reason(observation: dict, policy: dict | None = None) -> str | None:
    policy = policy if policy is not None else recovery_policy_from_env()
    dom = describe_dom(observation)
    safety = observation.get("safety", {})
    risky = any(observation.get(k) or safety.get(k) for k in ("editable_nonempty", "sensitive", "sensitive_fields", "unsaved", "has_unsaved_data", "has_sensitive_fields"))
    for e in observation.get("elements", []):
        if e.get("sensitive") or ("fill" in e.get("operations", []) and str(e.get("value") or "")) or e.get("input_type") == "password":
            risky = True
    if risky:
        return "Reload may discard nonempty editable, sensitive, or unsaved data. " + WARNING
    if dom["state"] != "error" or dom["coverage_status"] != "complete" or observation.get("truncated"):
        return "Reload requires host approval: sufficient complete DOM error evidence is unavailable. " + WARNING
    if _origin(str(observation.get("url", ""))) not in policy.get("reload_without_approval_origins", []):
        return "Reload origin is not explicitly approved by host recovery policy. " + WARNING
    return None


def compose_page_state(observation: dict, visual: dict | None = None, policy: dict | None = None) -> dict:
    result = describe_dom(observation)
    if visual is not None:
        result = {**result, "dom": dict(result), "vision": visual, "source": "dom+vision"}
        if result["state"] == "unknown" and visual.get("state") in STATES:
            result["state"] = visual["state"]
            result["summary"] = "Visual interpretation available; see separately attributed vision evidence."
        if visual.get("recovery_recommended") and not result["recovery_candidates"]:
            result["recovery_candidates"] = [_reload_candidate("vision")]
    reason = reload_approval_reason(observation, policy) if result["recovery_candidates"] else None
    for candidate in result["recovery_candidates"]:
        candidate["approval_required"] = reason is not None
        candidate["policy_reason"] = reason or "Host approved this origin; complete DOM error observation has no detected editable/sensitive evidence."
    return result
