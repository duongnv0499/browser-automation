"""Host approval policy: one source of truth for approval modes and risk tiers.

The mode is HOST policy (``BROWSER_APPROVAL_MODE``), never a tool argument and never
settable by a model or page. Tiers are ordered none < ordinary < consequential < critical.

- strict:      ordinary and above require per-action host approval (pre-mode behaviour).
- standard:    consequential and above require approval (default).
- autonomous:  only critical requires approval; the host grants standing approval for the rest.

Critical (payment, credential/sensitive input, account deletion, credential header edits)
always requires per-action approval. ``tier == "none"`` holds exactly when the legacy
classifier returned no reason, so strict mode reproduces every legacy decision and reason,
and strict ⊇ standard ⊇ autonomous by construction.
"""
from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import urlsplit

MODES = ("strict", "standard", "autonomous")
DEFAULT_MODE = "standard"
TIERS = ("none", "ordinary", "consequential", "critical")
_RANK = {tier: index for index, tier in enumerate(TIERS)}
_THRESHOLD = {"strict": "ordinary", "standard": "consequential", "autonomous": "critical"}

# Legacy reason strings: kept verbatim (bindings and strict parity depend on them).
KEY_REASON = "Keyboard activation may submit, confirm, or activate a consequential control"
SENSITIVE_REASON = "Sensitive field input"
CLICK_REASON = "Potential submission, disclosure, account change, payment, or destructive action"
CUSTOM_REASON = "Button/custom control may have consequential side effects"
DRAG_REASON = "Drag/drop may move, upload, or mutate content"
NAVIGATION_REASON = "URL names suggest a consequential action"
UPLOAD_REASON = "Local file upload discloses host files to the page"
DOWNLOAD_REASON = "Download writes a file into the host directory"

_SENSITIVE = re.compile(r"credit.?card|payment|social security|secret|token|cvv")
_CLICK_RISK = re.compile(r"buy|pay|purchase|checkout|order|delete|remove|send|submit|publish|post|transfer|confirm|accept|authorize|sign.?in|log.?in|upload|download|subscribe|unsubscribe")
_BENIGN = re.compile(r"(?:search|find|next|previous|back|expand|collapse|open menu|close menu|menu|show more|show less|close|cancel)(?:\s+results)?")
_URL_RISK = re.compile(r"buy|pay|purchase|checkout|order|delete|remove|send|submit|publish|post|transfer|confirm|accept|authorize|sign.?in|log.?in|log.?out|upload|download|subscribe|unsubscribe", re.I)
# Critical floor: payment-like intent and account deletion.
_PAYMENT = re.compile(r"\bbuy\b|\bpay(?:ment|ments)?\b|\bpurchase|check.?out|place.?(?:your.?)?order|transfer.?(?:money|funds)|send.?money", re.I)
_ACCOUNT_DELETION = re.compile(r"(?:delete|close|remove|deactivate|terminate)\W+(?:(?:my|your|the|this)\W+)?account", re.I)
_LOGOUT = re.compile(r"\b(?:log.?out|sign.?out)\b", re.I)
# Consequential (not merely substring-risky) evidence: a whole risk word in the accessible
# name ("Delete", "Post comment"; not "Posts"/"Orders"/"Recorder"), or an action-like URL
# path segment / query token. post/order/download/upload are too common in content URLs.
_RISK_WORDS = r"buy|pay|purchase|checkout|check.?out|order|delete|remove|send|submit|publish|post|transfer|confirm|accept|authorize|sign.?in|log.?in|log.?out|sign.?out|upload|download|subscribe|unsubscribe"
_NAME_RISK = re.compile(r"\b(?:" + _RISK_WORDS + r")\b", re.I)
# Links are mostly navigation with content titles ("Hypertext Transfer Protocol", "Post
# office"); for them the risk word must lead the name like an imperative ("Delete comment").
_NAME_RISK_LEADING = re.compile(r"^\W*(?:" + _RISK_WORDS + r")\b", re.I)
_URL_ACTION = re.compile(r"(?:^|[/?&=;])(?:delete|remove|logout|log-out|signout|sign-out|unsubscribe|subscribe|submit|publish|send|confirm|accept|authorize|transfer|checkout|purchase|buy|pay)(?:$|[/?&=;.#])", re.I)
_RELOAD_DATA_RISK = "Reload may discard"


class ApprovalPolicyError(ValueError):
    """Invalid host approval configuration; never silently relaxed."""
    code = "invalid_approval_mode"
    recommended_next_action = "fix_host_configuration"


def approval_mode_from_env(environ: dict | None = None) -> str:
    raw = (os.environ if environ is None else environ).get("BROWSER_APPROVAL_MODE")
    if raw is None or raw.strip() == "":
        return DEFAULT_MODE
    mode = raw.strip().lower()
    if mode not in MODES:
        raise ApprovalPolicyError("BROWSER_APPROVAL_MODE must be strict, standard, or autonomous (host configuration)")
    return mode


def requires_approval(tier: str, mode: str) -> bool:
    if mode not in _THRESHOLD:
        raise ApprovalPolicyError("Unknown approval mode")
    return _RANK[tier] >= _RANK[_THRESHOLD[mode]]


def max_tier(*tiers: str) -> str:
    return max(tiers, key=_RANK.__getitem__, default="none")


def audit(mode: str, tier: str, reason: str | None, source: str = "host_policy") -> dict[str, Any]:
    """Record why a non-none action ran: standing host policy or an exact host token."""
    return {"source": source, "mode": mode, "tier": tier, "reason": reason}


def _critical(text: str) -> bool:
    return bool(_PAYMENT.search(text) or _ACCOUNT_DELETION.search(text))


def _url_action(url: Any) -> bool:
    if not url:
        return False
    try:
        parsed = urlsplit(str(url))
    except ValueError:
        return True  # Unparseable destination: stay conservative.
    return bool(_URL_ACTION.search(parsed.path + ("?" + parsed.query if parsed.query else "")))


def _consequential_target(element: dict, *, submits: bool, post_form: bool) -> bool:
    name = str(element.get("name") or "")
    named = (_NAME_RISK_LEADING if element.get("role") == "link" else _NAME_RISK).search(name)
    return bool(submits or post_form or element.get("risky") or named
                or _url_action(element.get("href")) or _url_action(element.get("form_action")))


def classify_action(action: dict, observation: dict, *, recovery_policy: dict | None = None) -> tuple[str, str | None]:
    """Tier one snapshot-bound UI action. Ported from the legacy BrowserAgent classifier."""
    from .page_state import reload_approval_reason
    operation = action["operation"]
    if operation == "reload":
        reason = reload_approval_reason(observation, recovery_policy)
        if reason is None:
            return "none", None
        return ("consequential" if reason.startswith(_RELOAD_DATA_RISK) else "ordinary"), reason
    element = next((e for e in observation.get("elements", []) if e.get("id") == action.get("target")), {})
    description = " ".join(str(element.get(k, "")) for k in ("name", "role", "type", "input_type", "href", "form_action")).lower()
    risk_description = " ".join(str(element.get(k) or "") for k in ("name", "role", "href", "form_action")).lower()
    submits = bool(element.get("is_submit") or element.get("input_type") == "submit" and bool(element.get("form_action")))
    post_form = str(element.get("form_method") or "").lower() == "post"
    activation_key = str(action.get("key", "")).split("+")[-1].lower()
    # Activating a plain link is a GET navigation: it cannot itself complete a payment, so
    # payment-like link names/hrefs are capped at consequential (buttons/forms keep critical).
    plain_link = element.get("role") == "link" and not submits and not post_form and not element.get("risky")
    critical_tier = "consequential" if plain_link else "critical"
    if operation == "press" and activation_key in {"enter", "return", "space", "spacebar", " "}:
        if not element:
            return "consequential", KEY_REASON  # Unknown focused control could submit anything.
        if _critical(risk_description):
            return critical_tier, KEY_REASON
        if _consequential_target(element, submits=submits, post_form=post_form):
            return "consequential", KEY_REASON
        return "ordinary", KEY_REASON  # Includes substring-only risk words ("Posts", "/blog/posts/...").
    if operation in {"fill", "select"} and (element.get("sensitive") or "password" in description or _SENSITIVE.search(description)):
        return "critical", SENSITIVE_REASON
    if operation == "click":
        if submits or element.get("risky") or _CLICK_RISK.search(risk_description):
            if _critical(risk_description):
                return critical_tier, CLICK_REASON
            # Legacy reason kept (strict parity); substring-only matches are merely ordinary.
            return ("consequential" if _consequential_target(element, submits=submits, post_form=post_form) else "ordinary"), CLICK_REASON
        label = str(element.get("name", "")).strip().lower()
        if _BENIGN.fullmatch(label) and not element.get("risky") and not submits and not post_form:
            return "none", None
        # Custom controls and visual-only targets cannot be classified reliably.
        if element.get("role") not in {"link", "checkbox", "radio", "tab", "option", "menuitem"}:
            return ("consequential" if post_form or _LOGOUT.search(label) else "ordinary"), CUSTOM_REASON
    if operation == "drag":
        return "ordinary", DRAG_REASON
    return "none", None


def approval_reason(action: dict, observation: dict, *, mode: str = "strict", recovery_policy: dict | None = None) -> str | None:
    """Reason when this mode requires approval, else None. Strict == legacy classifier."""
    tier, reason = classify_action(action, observation, recovery_policy=recovery_policy)
    return reason if requires_approval(tier, mode) else None


def classify_navigation(url: str) -> tuple[str, str | None]:
    """Navigating can itself act (GET /logout, /delete?id=...); payment pages are only read."""
    parsed = urlsplit(url)
    if _url_action(url):
        return "consequential", NAVIGATION_REASON
    if _URL_RISK.search(parsed.path + "?" + parsed.query):
        return "ordinary", NAVIGATION_REASON  # Substring-only, e.g. /blog/posts/...
    return "none", None


def classify_transfer(kind: str) -> tuple[str, str]:
    return "consequential", UPLOAD_REASON if kind == "upload" else DOWNLOAD_REASON


# Network plan reasons (strings owned by browser_requests.py) mapped to tiers.
NETWORK_REASON_TIERS = {
    "Method is not an HTTP safe method": "consequential",
    "Destination differs from target tab origin": "consequential",
    "Request includes a body": "consequential",
    "Explicit credential header modification": "critical",
    "URL names suggest a consequential action": "consequential",
}


def classify_network(reasons: list[str], url: str) -> str:
    """Tier a prepared request from its reasons; payment-like URLs are critical."""
    tiers = [NETWORK_REASON_TIERS.get(reason, "consequential") for reason in reasons]
    if "URL names suggest a consequential action" in reasons and not _url_action(url):
        tiers[reasons.index("URL names suggest a consequential action")] = "ordinary"  # e.g. GET /api/posts?limit=5
    parsed = urlsplit(url)
    if "URL names suggest a consequential action" in reasons and _critical(parsed.path + "?" + parsed.query):
        tiers.append("critical")
    return max_tier(*tiers)


def classify_redirect(next_url: str, *, cross_origin: bool, carries_credentials: bool) -> str:
    """Tier one redirect hop the executor would otherwise stop at."""
    parsed = urlsplit(next_url)
    if cross_origin and carries_credentials or _critical(parsed.path + "?" + parsed.query):
        return "critical"
    return "consequential"


def recovery_requires_approval(reason: str | None, mode: str) -> bool:
    if reason is None:
        return False
    return requires_approval("consequential" if reason.startswith(_RELOAD_DATA_RISK) else "ordinary", mode)
