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
    session = await BrowserSession.launch(headless=True)
    provider = DecisionProvider(api_key="local-fixture-only", endpoint=url + "/decisions", text_endpoint=url + "/text")
    try:
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
    finally:
        await provider.close()
        await session.close()
        server.shutdown()
        server.server_close()
        thread.join()
