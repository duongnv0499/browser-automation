"""Host approval modes: strict parity with the frozen legacy classifier, tiers, and network tiering."""
import itertools
import re
from types import SimpleNamespace

import pytest

from browser_automation.agent import BrowserAgent
from browser_automation.approval_policy import (
    CLICK_REASON, CUSTOM_REASON, DRAG_REASON, KEY_REASON, SENSITIVE_REASON, MODES, ApprovalPolicyError,
    approval_mode_from_env, approval_reason, classify_action, classify_navigation, classify_network,
    classify_redirect, requires_approval,
)
from browser_automation.page_state import compose_page_state, reload_approval_reason


def legacy_approval_reason(action, observation, *, recovery_policy=None):
    """Frozen verbatim copy of BrowserAgent._approval_reason before approval modes (commit 5aae8c3)."""
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
        if element.get("role") not in {"link", "checkbox", "radio", "tab", "option", "menuitem"}:
            return "Button/custom control may have consequential side effects"
    if operation == "drag":
        return "Drag/drop may move, upload, or mutate content"
    return None


NAMES = ["", "Add", "Search", "Posts", "Orders", "Recorder", "Post comment", "Hypertext Transfer Protocol", "Next results", "Close", "Delete", "Delete account", "Buy now", "Pay", "Payment details",
         "Checkout", "Place order", "Sort order", "Log out", "Sign in", "Send message", "Transfer money", "Confirm",
         "Password", "Credit card number", "CVV", "API token", "Email", "Subscribe", "Show more", "Open menu"]
ROLES = ["button", "link", "checkbox", "radio", "tab", "option", "menuitem", "visual-region", "canvas", "div", "textbox", "combobox"]
FLAGS = [{}, {"is_submit": True}, {"input_type": "submit", "form_action": "https://example.test/post"}, {"form_method": "post"},
         {"form_method": "get", "form_action": "https://example.test/search"}, {"sensitive": True}, {"risky": True},
         {"input_type": "password"}, {"href": "https://example.test/account/delete"}, {"href": "https://example.test/articles"},
         {"href": "https://example.test/blog/posts/hello"}, {"href": "https://example.test/logout.php"}, {"form_action": "https://example.test/comments?action=publish"}]
ACTIONS = [{"operation": "click"}, {"operation": "fill", "text": "x"}, {"operation": "select", "value": "a"},
           {"operation": "press", "key": "Enter"}, {"operation": "press", "key": "Control+Enter"}, {"operation": "press", "key": "Space"},
           {"operation": "press", "key": "Escape"}, {"operation": "press", "key": "a"}, {"operation": "drag", "to_target": "e1"},
           {"operation": "hover"}, {"operation": "scroll", "delta": 600}, {"operation": "wait"}, {"operation": "back"}]


def cases():
    for name, role, flags, action in itertools.product(NAMES, ROLES, FLAGS, ACTIONS):
        element = {"id": "e1", "name": name, "role": role, "operations": ["click", "fill", "select", "press", "drag"], **flags}
        yield {**action, "target": "e1", "observation_id": "r1"}, {"id": "r1", "elements": [element]}
    # Targetless keys and unknown targets.
    for key in ("Enter", "Space", "Tab"):
        yield {"operation": "press", "key": key, "observation_id": "r1"}, {"id": "r1", "elements": []}


RELOAD_PAGES = [
    {"url": "https://fixture.test/", "text": "Something went wrong", "ready_state": "complete", "coverage": {"status": "complete"}, "elements": []},
    {"url": "https://fixture.test/", "text": "Something went wrong", "ready_state": "complete", "coverage": {"status": "complete"}, "safety": {"unsaved": True}, "elements": []},
    {"url": "https://other.test/", "text": "Something went wrong", "ready_state": "complete", "coverage": {"status": "complete"}, "elements": []},
    {"url": "https://fixture.test/", "text": "Ready", "ready_state": "complete", "coverage": {"status": "complete"}, "elements": [{"id": "x", "role": "textbox", "operations": ["fill"], "value": "draft"}]},
]
POLICY = {"reload_without_approval_origins": ["https://fixture.test"]}


def test_strict_mode_reproduces_every_legacy_decision_and_reason():
    count = 0
    for action, observation in cases():
        assert approval_reason(action, observation, mode="strict") == legacy_approval_reason(action, observation), (action, observation)
        assert BrowserAgent._approval_reason(action, observation) == legacy_approval_reason(action, observation)
        count += 1
    for page in RELOAD_PAGES:
        for policy in (POLICY, {}):
            assert approval_reason({"operation": "reload"}, page, mode="strict", recovery_policy=policy) == legacy_approval_reason({"operation": "reload"}, page, recovery_policy=policy)
    assert count > 30000


def test_modes_are_monotonic_and_none_matches_legacy_no_reason():
    for action, observation in cases():
        tier, reason = classify_action(action, observation)
        assert (tier == "none") == (legacy_approval_reason(action, observation) is None)
        decisions = [requires_approval(tier, mode) for mode in ("strict", "standard", "autonomous")]
        assert decisions == sorted(decisions, reverse=True)  # strict ⊇ standard ⊇ autonomous


@pytest.mark.parametrize("name,role,flags,action,expected", [
    ("Add", "button", {}, {"operation": "click"}, ("ordinary", CUSTOM_REASON)),
    ("Search", "button", {}, {"operation": "click"}, ("none", None)),
    ("Next page", "link", {}, {"operation": "click"}, ("none", None)),
    ("Delete", "button", {}, {"operation": "click"}, ("consequential", CLICK_REASON)),
    ("Save", "button", {"form_method": "post"}, {"operation": "click"}, ("consequential", CUSTOM_REASON)),
    ("Log out", "button", {}, {"operation": "click"}, ("consequential", CUSTOM_REASON)),
    ("Buy now", "button", {}, {"operation": "click"}, ("critical", CLICK_REASON)),
    ("Place order", "link", {}, {"operation": "click"}, ("consequential", CLICK_REASON)),  # plain link: GET, capped
    ("Place order", "link", {"form_method": "post"}, {"operation": "click"}, ("critical", CLICK_REASON)),
    ("Payment", "link", {"href": "https://en.wikipedia.org/wiki/Payment"}, {"operation": "click"}, ("consequential", CLICK_REASON)),
    ("How to buy a house", "link", {"href": "https://example.test/how-to-buy-x"}, {"operation": "click"}, ("consequential", CLICK_REASON)),
    ("Checkout", "link", {"href": "https://example.test/checkout"}, {"operation": "press", "key": "Enter"}, ("consequential", KEY_REASON)),
    ("Delete account", "button", {}, {"operation": "click"}, ("critical", CLICK_REASON)),
    ("Region", "visual-region", {}, {"operation": "click"}, ("ordinary", CUSTOM_REASON)),
    ("Email", "textbox", {}, {"operation": "fill"}, ("none", None)),
    ("Password", "textbox", {"sensitive": True}, {"operation": "fill"}, ("critical", SENSITIVE_REASON)),
    ("Card", "textbox", {"input_type": "password"}, {"operation": "fill"}, ("critical", SENSITIVE_REASON)),
    ("Search query", "textbox", {"form_method": "get"}, {"operation": "press", "key": "Enter"}, ("ordinary", KEY_REASON)),
    ("Comment", "textbox", {"form_method": "post"}, {"operation": "press", "key": "Enter"}, ("consequential", KEY_REASON)),
    ("Submit", "button", {"is_submit": True}, {"operation": "press", "key": "Space"}, ("consequential", KEY_REASON)),
    ("Checkout", "button", {}, {"operation": "press", "key": "Enter"}, ("critical", KEY_REASON)),
    ("Item", "div", {}, {"operation": "drag", "to_target": "e1"}, ("ordinary", DRAG_REASON)),
    ("Item", "div", {}, {"operation": "hover"}, ("none", None)),
    # Substring-only risk words are ordinary: they pause in strict (legacy) but not in standard.
    ("Hello world", "link", {"href": "https://example.test/blog/posts/hello"}, {"operation": "click"}, ("ordinary", CLICK_REASON)),
    ("Orders", "link", {}, {"operation": "click"}, ("ordinary", CLICK_REASON)),
    ("Recorder", "button", {}, {"operation": "click"}, ("ordinary", CLICK_REASON)),
    ("Posts", "link", {"href": "https://example.test/posts"}, {"operation": "press", "key": "Enter"}, ("ordinary", KEY_REASON)),
    ("Account settings", "link", {"href": "https://example.test/account/delete"}, {"operation": "click"}, ("consequential", CLICK_REASON)),
    ("Leave", "link", {"href": "https://example.test/logout.php"}, {"operation": "press", "key": "Enter"}, ("consequential", KEY_REASON)),
    ("Post comment", "button", {}, {"operation": "click"}, ("consequential", CLICK_REASON)),
    ("Sign in", "button", {}, {"operation": "click"}, ("consequential", CLICK_REASON)),
    # Links need the risk word to lead the name (imperative); content titles stay ordinary.
    ("Hypertext Transfer Protocol", "link", {"href": "https://en.wikipedia.org/wiki/Hypertext_Transfer_Protocol"}, {"operation": "click"}, ("ordinary", CLICK_REASON)),
    ("Delete comment", "link", {"href": "https://example.test/c/7"}, {"operation": "click"}, ("consequential", CLICK_REASON)),
    ("Hypertext Transfer Protocol", "button", {}, {"operation": "click"}, ("consequential", CLICK_REASON)),
])
def test_tier_table(name, role, flags, action, expected):
    observation = {"elements": [{"id": "e1", "name": name, "role": role, **flags}]}
    assert classify_action({**action, "target": "e1"}, observation) == expected
    tier = expected[0]
    assert [requires_approval(tier, mode) for mode in MODES] == [tier != "none", tier in {"consequential", "critical"}, tier == "critical"]


def test_targetless_activation_reload_tiers_and_recovery_candidates():
    assert classify_action({"operation": "press", "key": "Enter"}, {"elements": []}) == ("consequential", KEY_REASON)
    clean, unsaved, foreign = RELOAD_PAGES[0], RELOAD_PAGES[1], RELOAD_PAGES[2]
    assert classify_action({"operation": "reload"}, clean, recovery_policy=POLICY) == ("none", None)
    assert classify_action({"operation": "reload"}, unsaved, recovery_policy=POLICY)[0] == "consequential"
    assert classify_action({"operation": "reload"}, foreign, recovery_policy=POLICY)[0] == "ordinary"
    for mode, expected in (("strict", True), ("standard", False), ("autonomous", False)):
        candidate = compose_page_state(foreign, policy=POLICY, mode=mode)["recovery_candidates"][0]
        assert candidate["approval_required"] is expected
        assert ("standing approval" in candidate["policy_reason"]) is (not expected)
    assert compose_page_state(unsaved, policy=POLICY, mode="standard")["recovery_candidates"][0]["approval_required"] is True
    assert compose_page_state(unsaved, policy=POLICY, mode="autonomous")["recovery_candidates"][0]["approval_required"] is False


def test_mode_parsing_is_host_policy_and_never_silently_relaxed():
    assert approval_mode_from_env({}) == "standard"
    assert approval_mode_from_env({"BROWSER_APPROVAL_MODE": ""}) == "standard"
    assert approval_mode_from_env({"BROWSER_APPROVAL_MODE": " Autonomous "}) == "autonomous"
    for bad in ("auto", "yolo", "none", "1"):
        with pytest.raises(ApprovalPolicyError) as error:
            approval_mode_from_env({"BROWSER_APPROVAL_MODE": bad})
        assert error.value.code == "invalid_approval_mode"
    with pytest.raises(ValueError):
        BrowserAgent(object(), object(), approval_mode="yolo")


def test_navigation_and_network_tiers():
    assert classify_navigation("https://example.test/logout") == ("consequential", "URL names suggest a consequential action")
    assert classify_navigation("https://example.test/settings?action=unsubscribe") == ("consequential", "URL names suggest a consequential action")
    assert classify_navigation("https://example.test/blog/posts/hello") == ("ordinary", "URL names suggest a consequential action")
    assert classify_network(["URL names suggest a consequential action"], "https://example.test/api/posts?limit=5") == "ordinary"
    assert classify_navigation("https://example.test/articles?id=4") == ("none", None)
    assert classify_network([], "https://example.test/") == "none"
    assert classify_network(["Method is not an HTTP safe method", "Request includes a body"], "https://example.test/api") == "consequential"
    assert classify_network(["Explicit credential header modification"], "https://example.test/api") == "critical"
    assert classify_network(["URL names suggest a consequential action"], "https://example.test/checkout") == "critical"
    assert classify_network(["URL names suggest a consequential action"], "https://example.test/delete") == "consequential"
    assert classify_redirect("https://other.test/data", cross_origin=True, carries_credentials=False) == "consequential"
    assert classify_redirect("https://other.test/data", cross_origin=True, carries_credentials=True) == "critical"
    assert classify_redirect("https://example.test/pay", cross_origin=False, carries_credentials=False) == "critical"


@pytest.mark.parametrize("mode,spec,required,tier", [
    ("standard", {"url": "https://example.test/echo"}, False, "none"),
    ("standard", {"url": "https://example.test/echo", "method": "POST", "json_body": {"a": 1}}, True, "consequential"),
    ("autonomous", {"url": "https://example.test/echo", "method": "POST", "json_body": {"a": 1}}, False, "consequential"),
    ("autonomous", {"url": "https://other.test/echo"}, False, "consequential"),
    ("autonomous", {"url": "https://example.test/echo", "headers": {"Authorization": "x"}}, True, "critical"),
    ("autonomous", {"url": "https://example.test/checkout"}, True, "critical"),
    ("strict", {"url": "https://example.test/echo", "method": "DELETE"}, True, "consequential"),
    ("standard", {"url": "https://example.test/api/posts?limit=5"}, False, "ordinary"),
    ("strict", {"url": "https://example.test/api/posts?limit=5"}, True, "ordinary"),
    ("standard", {"url": "https://example.test/account/delete"}, True, "consequential"),
])
def test_network_plan_eligibility_by_mode(mode, spec, required, tier, monkeypatch):
    from browser_automation.browser_requests import RequestExecutor
    monkeypatch.delenv("BROWSER_NETWORK_REQUIRE_APPROVAL", raising=False)
    page = SimpleNamespace(url="https://example.test/app", context=object())  # Plan preparation only; nothing is sent.
    plan = RequestExecutor().prepare("t", page, spec, approval_mode=mode)
    assert (plan["approval_required"], plan["approval_tier"], plan["approval_mode"]) == (required, tier, mode)
    assert plan["status"] == ("approval_required" if required else "prepared")
    monkeypatch.setenv("BROWSER_NETWORK_REQUIRE_APPROVAL", "1")
    assert RequestExecutor().prepare("t", page, {"url": "https://example.test/echo"}, approval_mode="autonomous")["approval_required"]
    monkeypatch.setenv("BROWSER_APPROVAL_MODE", "autonomous")
    monkeypatch.delenv("BROWSER_NETWORK_REQUIRE_APPROVAL")
    assert RequestExecutor().prepare("t", page, {"url": "https://example.test/echo", "method": "PUT"})["approval_required"] is False
