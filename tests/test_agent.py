"""Agent safety tests and actual browser loop with a deterministic local service."""
import asyncio
import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from browser_automation.agent import BrowserAgent
from browser_automation.providers import DecisionProvider


def obs(revision="r1", text="Ready", role="button"):
    return {"id": revision, "tab_id": "t1", "url": "http://local/", "text": text,
            "elements": [{"id": "e1", "name": "Submit", "role": role, "operations": ["click"]}]}


class Session:
    def __init__(self):
        self.calls = []
        self.failure = None

    async def observe(self, tab_id, **kwargs):
        return obs("r" + str(len(self.calls) + 1))

    async def act(self, tab_id, action):
        self.calls.append(action)
        if self.failure:
            raise self.failure
        return {"operation": action["operation"]}


class Provider:
    last_metrics = {}

    def __init__(self, operation="done", satisfied=False):
        self.operation = operation
        self.satisfied = satisfied
        self.calls = []

    async def choose(self, observation, goal, history):
        self.calls.append((observation, history))
        return {"operation": self.operation, **({"target": "e1"} if self.operation == "click" else {})}

    async def verify(self, observation, goal, history):
        return {"satisfied": self.satisfied, "probability": .99 if self.satisfied else .01}


@pytest.mark.asyncio
async def test_done_requires_independent_verification():
    session = Session()
    result = await BrowserAgent(session, Provider()).run("t1", "Submit")
    assert result["status"] == "verification_failed"
    assert not session.calls
    assert result["metrics"]["provider_calls"] == 2


@pytest.mark.asyncio
async def test_exact_approval_binding_and_no_progress():
    session = Session()
    provider = Provider("click")
    paused = await BrowserAgent(session, provider).run("t1", "Submit")
    assert paused["status"] == "approval_required" and not session.calls
    approval = paused["approval"]
    assert approval["action"] == {"operation": "click", "target": "e1", "observation_id": "r1"}
    seen = []

    async def approve(request):
        seen.append(request)
        request["action"]["target"] = "tampered"
        return True

    result = await BrowserAgent(session, provider, approval=approve, no_progress_limit=2, history_limit=1).run("t1", "Submit")
    assert result["status"] == "no_progress"
    assert all(action["target"] == "e1" for action in session.calls)
    assert all(len(history) <= 1 for _, history in provider.calls)
    assert seen[0]["binding"] == approval["binding"]


@pytest.mark.asyncio
async def test_only_guaranteed_preinput_stale_reobserves():
    class StaleObservationError(RuntimeError):
        code = "stale_observation"

    stale = Session()
    stale.failure = StaleObservationError()
    result = await BrowserAgent(stale, Provider("click"), approval=lambda _: True, stale_limit=1).run("t1", "Submit")
    assert result["metrics"]["stale_reobservations"] == 1
    assert len(stale.calls) == 2
    uncertain = Session()
    uncertain.failure = TimeoutError("input may already have happened")
    result = await BrowserAgent(uncertain, Provider("click"), approval=lambda _: True).run("t1", "Submit")
    assert result["status"] == "blocked" and len(uncertain.calls) == 1


@pytest.mark.asyncio
async def test_cancelled_run_stops_future_actions():
    class CancelProvider(Provider):
        async def choose(self, *args):
            raise asyncio.CancelledError()

    session = Session()
    result = await BrowserAgent(session, CancelProvider()).run("t1", "Submit")
    assert result["status"] == "cancelled" and not session.calls


@pytest.mark.asyncio
async def test_real_browser_local_deterministic_provider_loop(tmp_path):
    # No mocks of BrowserSession/provider transport. This server implements a
    # deterministic fixture policy, not Luna, and receives actual rendered DOM.
    from browser_automation.browser import BrowserSession

    records = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            page = b'''<!doctype html><title>Local agent fixture</title><label>Query <input aria-label="Query"></label><button onclick="document.querySelector('output').textContent='Submitted: '+document.querySelector('input').value">Submit</button><output></output>'''
            self.send_response(200)
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            records.append(body)
            if "messages" in body:
                response = {"choices": [{"message": {"content": json.dumps({"text": "hello", "refusal": False})}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
            else:
                state = body["state"]
                context = json.loads(state[0] if isinstance(state, list) else state)
                observation = context["untrusted_observation"]
                name, question = next(iter(body["questions"].items()))
                choices = question["criteria"]
                if name == "goal_verification":
                    selected = "satisfied" if "Submitted: hello" in observation["text"] else "unsatisfied"
                else:
                    desired = "done" if "Submitted: hello" in observation["text"] else "click" if any(e.get("value") == "hello" for e in observation["elements"]) else "fill"
                    selected = next(key for key, description in choices.items() if json.loads(description)["action"]["operation"] == desired and (desired != "click" or json.loads(description)["name"] == "Submit"))
                response = {"answers": {name: {"type": "choice", "choice": selected,
                            "confidence": .98, "probabilities": {key: 1 if key == selected else 0 for key in choices}}}, "usage": {"input_tokens": 1, "output_tokens": 1}}
            data = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    session = None
    provider = DecisionProvider(api_key="local-fixture-only", endpoint=url + "/decisions", text_endpoint=url + "/text")
    try:
        session = await BrowserSession.launch(headless=True)
        tab = await session.new_tab(url)
        tab_id = tab["id"] if isinstance(tab, dict) else tab
        result = await BrowserAgent(session, provider, approval=lambda _: True, screenshot=True).run(tab_id, "Enter hello and submit; verify Submitted: hello appears")
        assert result["status"] == "success", result
        assert "Submitted: hello" in result["observation"]["text"]
        assert [s["action"]["operation"] for s in result["steps"]] == ["fill", "click"]
        assert len(records) == 5  # choose/fill-text/choose/choose/independent verifier
        screenshot = base64.b64decode(result["observation"]["screenshot"])
        assert screenshot.startswith(b"\x89PNG\r\n\x1a\n")
        (tmp_path / "deterministic-agent-outcome.png").write_bytes(screenshot)
        (tmp_path / "deterministic-agent-outcome.json").write_text(json.dumps({"status": result["status"], "text": result["observation"]["text"], "metrics": result["metrics"]}))
        (tmp_path / "deterministic-agent-request-evidence.json").write_text(json.dumps({
            "transport": "local deterministic HTTP Decisions fixture (not live Luna)",
            "requests": [{"kind": "field_text" if "messages" in body else next(iter(body["questions"])),
                          "model": body["model"], "image_parts": sum(part.get("type") == "image_url" for part in (body["messages"][1]["content"] if "messages" in body else body.get("state", [])) if isinstance(part, dict)),
                          "input_chars": len(json.dumps(body)), "question_names": list(body.get("questions", {}))} for body in records]}))
    finally:
        await provider.close()
        if session is not None:
            await session.close()
        server.shutdown()
        server.server_close()
        thread.join()

@pytest.mark.asyncio
async def test_agent_pages_to_late_target_without_browser_input():
    from browser_automation.providers import action_candidates

    class LargeSession(Session):
        async def observe(self, tab_id, **kwargs):
            page = obs(text="Searched" if self.calls else "Ready")
            page["elements"] = [{"id": f"e{i}", "role": "button", "name": "Search", "operations": ["click"]} for i in range(120)]
            return page

    class PagingProvider(Provider):
        async def choose(self, observation, goal, history):
            if observation["text"] == "Searched":
                return {"operation": "done"}
            choices = action_candidates(observation)
            return next((a for a in choices.values() if a.get("target") == "e119"), {"operation": "next_actions"})

    session = LargeSession()
    result = await BrowserAgent(session, PagingProvider(satisfied=True), max_steps=10).run("t1", "Search using final target")
    assert result["status"] == "success"
    assert len(session.calls) == 1 and session.calls[0]["target"] == "e119"


@pytest.mark.asyncio
async def test_exact_pending_action_can_resume_then_verify_and_stale_is_rejected():
    class RevisionSession(Session):
        revision = "r1"

        async def observe(self, tab_id, **kwargs):
            return obs(self.revision, "Submitted" if self.calls else "Ready")

        async def act(self, tab_id, action):
            if action["observation_id"] != self.revision:
                class StaleObservationError(RuntimeError):
                    code = "stale_observation"
                raise StaleObservationError()
            return await super().act(tab_id, action)

    class ResumingProvider(Provider):
        async def choose(self, observation, goal, history):
            return {"operation": "done"} if observation["text"] == "Submitted" else {"operation": "click", "target": "e1"}

    session = RevisionSession()
    agent = BrowserAgent(session, ResumingProvider(satisfied=True))
    paused = await agent.run("t1", "Submit")
    assert paused["status"] == "approval_required"
    await session.act("t1", paused["approval"]["action"])  # Explicit host approval of exact pending action.
    resumed = await agent.run("t1", "Submit")
    assert resumed["status"] == "success" and len(session.calls) == 1
    session.calls.clear()
    paused = await agent.run("t1", "Submit")
    session.revision = "r2"
    with pytest.raises(RuntimeError):
        await session.act("t1", paused["approval"]["action"])
    assert not session.calls


def test_benign_buttons_and_activation_keys_follow_explicit_policy():
    page = obs()
    page["elements"][0].update(name="Search", form_method=None, input_type="submit", is_submit=False, form_action=None)
    assert BrowserAgent._approval_reason({"operation": "click", "target": "e1"}, page) is None
    page["elements"][0]["is_submit"] = True
    assert BrowserAgent._approval_reason({"operation": "click", "target": "e1"}, page)
    for key in ("Control+Enter", "Return", "Space", "spacebar"):
        assert BrowserAgent._approval_reason({"operation": "press", "target": "e1", "key": key}, page)

@pytest.mark.asyncio
async def test_agent_executes_observed_select_value_without_field_helper():
    class SelectSession(Session):
        async def observe(self, tab_id, **kwargs):
            page = obs(text="Red selected" if self.calls else "Choose color")
            page["elements"] = [{"id": "color", "role": "combobox", "name": "Color", "operations": ["select"],
                                 "options": [{"value": "red-id", "label": "Red"}]}]
            return page

    class SelectProvider(Provider):
        async def choose(self, observation, goal, history):
            return {"operation": "done"} if observation["text"] == "Red selected" else {"operation": "select", "target": "color", "value": "red-id"}

        async def field_text(self, *args):
            raise AssertionError("Select must never generate free-text options")

    session = SelectSession()
    result = await BrowserAgent(session, SelectProvider(satisfied=True)).run("t1", "Select Red")
    assert result["status"] == "success"
    assert session.calls == [{"operation": "select", "target": "color", "value": "red-id", "observation_id": "r1"}]

@pytest.mark.asyncio
async def test_screenshot_only_progress_does_not_trigger_no_progress():
    class VisualSession(Session):
        async def observe(self, tab_id, **kwargs):
            page = obs()
            page["screenshot"] = "pixels-" + str(len(self.calls))
            return page

    class VisualProvider(Provider):
        async def choose(self, observation, goal, history):
            return {"operation": "done"} if observation["screenshot"] == "pixels-5" else {"operation": "click", "target": "e1"}

    session = VisualSession()
    result = await BrowserAgent(session, VisualProvider(satisfied=True), approval=lambda _: True, no_progress_limit=2).run("t1", "Advance canvas five times")
    assert result["status"] == "success" and len(session.calls) == 5


@pytest.mark.asyncio
async def test_multi_select_adds_options_without_replacing_existing_selection():
    from browser_automation.providers import action_candidates

    class MultiSession(Session):
        selected = []

        async def observe(self, tab_id, **kwargs):
            page = obs()
            page["elements"] = [{"id": "colors", "role": "combobox", "name": "Colors", "multiple": True,
                                 "selected_values": self.selected, "operations": ["select"],
                                 "options": [{"value": v, "label": v, "selected": v in self.selected} for v in ("red", "blue", "green")]}]
            return page

        async def act(self, tab_id, action):
            self.calls.append(action)
            self.selected = action["value"]
            return {}

    class MultiProvider(Provider):
        async def choose(self, observation, goal, history):
            selected = observation["elements"][0]["selected_values"]
            if set(selected) == {"red", "blue"}:
                return {"operation": "done"}
            wanted = "red" if "red" not in selected else "blue"
            return next(a for a in action_candidates(observation).values() if a["operation"] == "select" and wanted in a["value"] and len(a["value"]) == len(selected) + 1)

    session = MultiSession()
    result = await BrowserAgent(session, MultiProvider(satisfied=True)).run("t1", "Select red and blue")
    assert result["status"] == "success"
    assert [a["value"] for a in session.calls] == [["red"], ["red", "blue"]]


@pytest.mark.asyncio
async def test_popup_requires_explicit_observed_tab_switch():
    class PopupSession(Session):
        async def observe(self, tab_id, **kwargs):
            return {**obs(), "tab_id": tab_id, "text": "Popup completed" if tab_id == "popup" else "Ready"}

        async def act(self, tab_id, action):
            self.calls.append((tab_id, action))
            return {"popup_tabs": ["popup"]}

        async def tabs(self):
            return [{"id": "t1", "url": "http://local/"}, {"id": "popup", "url": "http://local/popup", "title": "Next stage"}, {"id": "unsolicited", "url": "http://local/unrelated"}]

    class PopupProvider(Provider):
        async def choose(self, observation, goal, history):
            if observation["tab_id"] == "popup":
                return {"operation": "done"}
            if observation["available_tabs"]:
                assert [t["id"] for t in observation["available_tabs"]] == ["popup"]
                return {"operation": "switch_tab", "tab_id": "popup"}
            return {"operation": "click", "target": "e1"}

    session = PopupSession()
    result = await BrowserAgent(session, PopupProvider(satisfied=True), approval=lambda _: True).run("t1", "Complete popup stage")
    assert result["status"] == "success" and result["active_tab"] == "popup"
    assert len(session.calls) == 1 and session.calls[0][0] == "t1"
    assert result["steps"][1]["action"] == {"operation": "switch_tab", "tab_id": "popup"}


@pytest.mark.asyncio
async def test_text_continuation_is_reachable_and_refreshed_for_verifier():
    class TextSession(Session):
        revision = 0
        reads = []

        async def observe(self, tab_id, **kwargs):
            self.revision += 1
            return {"id": f"r{self.revision}", "tab_id": tab_id, "text": "Beginning", "elements": [],
                    "truncated": True, "next_offset": 10, "text_length": 20, "source_truncated": False}

        async def text_continuation(self, tab_id, observation_id, offset, max_text):
            self.reads.append((observation_id, offset))
            assert observation_id == f"r{self.revision}"
            return {"observation_id": observation_id, "text": "Goal visibly achieved", "offset": offset,
                    "next_offset": None, "text_length": 20, "truncated": False, "source_truncated": False}

    class TextProvider(Provider):
        async def choose(self, observation, goal, history):
            return {"operation": "done"} if "Goal visibly achieved" in observation["text"] else {"operation": "next_text"}

        async def verify(self, observation, goal, history):
            assert observation["text"] == "Beginning"
            assert observation["text_windows"][0]["text"] == "Goal visibly achieved"
            return {"satisfied": True, "probability": .99}

    session = TextSession()
    result = await BrowserAgent(session, TextProvider(), max_text=10).run("t1", "Verify later visible text")
    assert result["status"] == "success" and not session.calls
    assert session.reads == [("r1", 10), ("r2", 10)]

@pytest.mark.asyncio
async def test_text_only_secondary_multiselect_changes_are_progress():
    from browser_automation.providers import action_candidates

    class ManySelectSession(Session):
        selected = ["red"]

        async def observe(self, tab_id, **kwargs):
            page = obs(text="Colors")
            page["elements"] = [{"id": "colors", "role": "combobox", "name": "Colors", "value": "red", "multiple": True,
                                 "selected_values": list(self.selected), "operations": ["select"],
                                 "options": [{"value": v, "selected": v in self.selected} for v in ("red", "blue", "green", "orange", "purple")]}]
            return page

        async def act(self, tab_id, action):
            self.calls.append(action)
            self.selected = action["value"]
            return {}

    class ManySelectProvider(Provider):
        async def choose(self, observation, goal, history):
            selected = observation["elements"][0]["selected_values"]
            if len(selected) == 5:
                return {"operation": "done"}
            return next(a for a in action_candidates(observation).values() if a["operation"] == "select" and len(a["value"]) == len(selected) + 1)

    session = ManySelectSession()
    result = await BrowserAgent(session, ManySelectProvider(satisfied=True), screenshot=False, no_progress_limit=2).run("t1", "Select all five colors")
    assert result["status"] == "success" and len(session.calls) == 4


@pytest.mark.asyncio
async def test_four_inspected_evidence_windows_are_all_freshly_verified():
    class FourWindowSession(Session):
        revision = 0
        reads = []

        async def observe(self, tab_id, **kwargs):
            self.revision += 1
            return {"id": f"r{self.revision}", "tab_id": tab_id, "text": "prefix", "elements": [],
                    "next_offset": 10, "text_length": 50, "truncated": True}

        async def text_continuation(self, tab_id, observation_id, offset, max_text):
            assert observation_id == f"r{self.revision}"
            self.reads.append((observation_id, offset))
            return {"observation_id": observation_id, "text": f"proof{offset}", "offset": offset,
                    "next_offset": offset + 10 if offset < 40 else None, "text_length": 50, "truncated": offset < 40}

    class FourWindowProvider(Provider):
        async def choose(self, observation, goal, history):
            return {"operation": "done"} if observation.get("next_offset") is None else {"operation": "next_text"}

        async def verify(self, observation, goal, history):
            assert observation["verification_text_offsets"] == [10, 20, 30, 40]
            assert [w["text"] for w in observation["text_windows"]] == ["proof10", "proof20", "proof30", "proof40"]
            return {"satisfied": True, "probability": .99}

    session = FourWindowSession()
    result = await BrowserAgent(session, FourWindowProvider(), max_text=10).run("t1", "Verify four separate proofs")
    assert result["status"] == "success"
    assert session.reads == [(revision, offset) for revision in ("r1", "r2") for offset in (10, 20, 30, 40)]


@pytest.mark.asyncio
async def test_progress_order_needs_user_and_no_private_values():
    events = []

    async def receive(event):
        await asyncio.sleep(0)
        events.append(event)

    session = Session()
    result = await BrowserAgent(session, Provider("click"), on_progress=receive).run("t1", "Submit secret-goal")
    assert result["status"] == "approval_required"
    assert [e["checkpoint"] for e in events] == ["observe", "needs_user"]
    assert [e["seq"] for e in events] == [1, 2]
    assert events[1]["status"] == "approval_required"
    assert "Submit" not in json.dumps(events) and "secret-goal" not in json.dumps(events)
    assert not session.calls


@pytest.mark.asyncio
async def test_progress_cancellation_does_not_close_or_replay_session():
    events = []

    class CancellingProvider(Provider):
        async def choose(self, *args):
            raise asyncio.CancelledError()

    session = Session()
    result = await BrowserAgent(session, CancellingProvider(), on_progress=events.append).run("t1", "Stop safely")
    assert result["status"] == "cancelled" and session.calls == []
    assert events[-1]["checkpoint"] == "cancelled"


@pytest.mark.asyncio
async def test_visual_recovery_is_observed_but_never_autoapproved():
    events = []

    class VisualSession(Session):
        async def observe(self, tab_id, **kwargs):
            assert kwargs["screenshot"] is True
            return {"id": "r1", "tab_id": tab_id, "url": "https://fixture.test/", "text": "", "elements": [],
                    "coverage": {"status": "unknown"}, "screenshot": "fixture image"}

    class VisualProvider(Provider):
        async def interpret_visual(self, observation):
            return {"state": "error", "source": "vision", "visible_text": ["REFRESH"], "recovery_recommended": True,
                    "summary": "Visual recovery control", "self_reported_confidence": .8, "calibrated": False, "regions": []}

    session = VisualSession()
    result = await BrowserAgent(session, VisualProvider("reload"), interpret_visual=True, on_progress=events.append,
        recovery_policy={"reload_without_approval_origins": ["https://fixture.test"]}).run("t1", "Recover if safe")
    assert result["status"] == "approval_required" and not session.calls
    assert result["observation"]["visual_summary"]["visible_text"] == ["REFRESH"]
    assert result["approval"]["recovery"][0]["provenance"] == "vision"
    assert events[0]["page_state"]["source"] == "dom+vision"
    assert events[0]["page_state"]["recovery_available"] is True


@pytest.mark.asyncio
async def test_repeated_same_observation_progress_is_debounced():
    events = []
    result = await BrowserAgent(Session(), Provider("wait"), max_steps=4, on_progress=events.append).run("t1", "Wait")
    # Executed steps are meaningful; identical same-step verifier observations are suppressed.
    assert len(events) <= 6
    assert all(a["seq"] < b["seq"] for a, b in zip(events, events[1:]))
    assert result["status"] in {"no_progress", "step_limit"}



@pytest.mark.asyncio
@pytest.mark.parametrize("fail_checkpoint", ["observe", "done"])
async def test_progress_delivery_failure_returns_result_without_repeating_callback(fail_checkpoint):
    events = []

    async def fail(event):
        events.append(event)
        if event["checkpoint"] == fail_checkpoint:
            raise RuntimeError("private callback secret must not leak")

    session = Session()
    result = await BrowserAgent(session, Provider(satisfied=True), on_progress=fail).run("t1", "Read ready page")
    assert result["status"] == "progress_error"
    assert result["error_diagnostic"]["code"] == "progress_delivery_failed"
    assert sum(e["checkpoint"] == fail_checkpoint for e in events) == 1
    assert "private callback secret" not in json.dumps(result)
    assert session.calls == []


@pytest.mark.asyncio
async def test_browser_changed_region_diagnostic_survives_blocked_run():
    from browser_automation.browser import StaleObservationError
    session = Session()
    session.failure = StaleObservationError("Target pixels changed; observe again before visual dispatch")
    session.failure.diagnostic = {"code": "target_pixels_changed", "changed_region": {"x": 10, "y": 20, "width": 30, "height": 40}}
    result = await BrowserAgent(session, Provider("click"), approval=lambda _: True, stale_limit=0).run("t1", "Submit")
    assert result["status"] == "blocked" and len(session.calls) == 1
    assert result["error_diagnostic"]["code"] == "stale_observation"
    assert result["error_diagnostic"]["diagnostic"] == session.failure.diagnostic
    assert result["error_diagnostic"]["recommended_next_action"] == "reobserve"
    assert "Target pixels changed" in result["error_diagnostic"]["message"]

