"""Evidence classification and host-only recovery authority, not OCR accuracy."""
import pytest

from browser_automation.agent import BrowserAgent
from browser_automation.page_state import compose_page_state, describe_dom, recovery_policy_from_env, reload_approval_reason
from browser_automation.providers import action_candidates


def page(text="Something went wrong", **extra):
    return {"id": "r1", "url": "https://fixture.test/error", "text": text,
            "ready_state": "complete", "coverage": {"status": "complete"}, "elements": [], **extra}


@pytest.mark.parametrize("text,state", [("Something went wrong", "error"), ("Loading", "loading"),
    ("Sign in to continue", "login"), ("Verify you are human", "captcha"), ("Useful content", "ready")])
def test_classification_has_explicit_dom_provenance(text, state):
    result = describe_dom(page(text))
    assert result["state"] == state
    assert result["source"] == "dom"
    assert result["confidence"] == {"kind": "heuristic", "calibrated": False}
    assert result["capture_scope"] == "page_viewport_not_desktop"


def test_empty_partial_page_is_not_ready():
    assert describe_dom(page("", coverage={"status": "partial"}))["state"] == "unknown"


def test_reload_host_policy_does_not_make_refresh_button_benign():
    policy = {"reload_without_approval_origins": ["https://fixture.test"]}
    observation = page(elements=[{"id": "refresh", "name": "Refresh", "role": "button", "operations": ["click"]}])
    assert reload_approval_reason(observation, policy) is None
    assert BrowserAgent._approval_reason({"operation": "click", "target": "refresh"}, observation, recovery_policy=policy)
    assert BrowserAgent._approval_reason({"operation": "reload"}, observation, recovery_policy={})
    observation["page_state"] = compose_page_state(observation, policy=policy)
    assert {"operation": "reload"} in action_candidates(observation).values()
    assert observation["page_state"]["recovery_candidates"][0]["approval_required"] is False


@pytest.mark.parametrize("risk", [{"editable_nonempty": True}, {"sensitive_fields": True}, {"unsaved": True},
    {"elements": [{"id": "input", "operations": ["fill"], "value": "private"}]},
    {"coverage": {"status": "partial"}}, {"truncated": True}])
def test_reload_risk_always_needs_approval(risk):
    assert reload_approval_reason(page(**risk), {"reload_without_approval_origins": ["https://fixture.test"]})


def test_vision_recovery_never_manufactures_dom_or_permission():
    observation = page("", coverage={"status": "unknown"})
    visual = {"state": "error", "summary": "A recovery control is visible", "visible_text": ["REFRESH"],
              "source": "vision", "self_reported_confidence": .8, "calibrated": False,
              "recovery_recommended": True, "regions": []}
    result = compose_page_state(observation, visual, {"reload_without_approval_origins": ["https://fixture.test"]})
    assert result["dom"]["state"] == "unknown" and result["vision"] == visual
    assert result["recovery_candidates"][0]["provenance"] == "vision"
    assert result["recovery_candidates"][0]["approval_required"] is True
    assert observation["text"] == "" and observation["elements"] == []


@pytest.mark.parametrize("raw", ['{"reload_without_approval_origins":["https://fixture.test/error"]}',
    '{"reload_without_approval_origins":["*"]}', '{"unsafe":true}', '[]', 'invalid'])
def test_invalid_host_policy_is_explicit(monkeypatch, raw):
    monkeypatch.setenv("BROWSER_RECOVERY_POLICY", raw)
    with pytest.raises(ValueError):
        recovery_policy_from_env()


@pytest.mark.asyncio
async def test_real_viewport_local_http_visual_recovery_consumer(tmp_path):
    """Actual Chromium screenshot and HTTP parser; deterministic reply is NOT live OCR."""
    import base64
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from browser_automation.browser import BrowserSession
    from browser_automation.providers import DecisionProvider

    records = []
    events = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            body = (b'<!doctype html><title>Canvas evidence fixture</title><canvas width="500" height="150"></canvas><div style="display:grid;grid-template-columns:repeat(30,22px);gap:1px">'
                    + b''.join(f'<button aria-label="Item {i}" style="width:22px;height:20px">.</button>'.encode() for i in range(300))
                    + b'</div><script>const c=document.querySelector("canvas").getContext("2d");c.font="48px sans-serif";c.fillText("RECONNECT ZEBRA",20,80);</script>')
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            records.append(request)
            if "messages" in request:
                regions_prompt = request["messages"][1]["content"][0]["text"]
                regions = json.loads(regions_prompt.split("Observed region identities: ")[1])
                result = {"state": "error", "summary": "Deterministic fixture recovery evidence", "visible_text": ["RECONNECT ZEBRA"],
                          "region_targets": [regions[0]["target"]] if regions else [], "recovery_recommended": True,
                          "self_reported_confidence": .7, "refusal": False}
                response = {"choices": [{"message": {"content": json.dumps(result)}, "finish_reason": "stop"}]}
            else:
                name, question = next(iter(request["questions"].items()))
                selected = next(k for k, v in question["criteria"].items() if json.loads(v)["action"]["operation"] == "reload")
                response = {"answers": {name: {"type": "choice", "choice": selected}}}
            encoded = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    session = None
    provider = DecisionProvider(api_key="local-deterministic-only", endpoint=url + "/decisions", text_endpoint=url + "/chat")
    try:
        session = await BrowserSession.launch(headless=True)
        tab = await session.new_tab(url)
        result = await BrowserAgent(session, provider, interpret_visual=True, on_progress=events.append).run(tab["id"], "Describe available recovery")
        assert result["status"] == "approval_required"
        observed = result["observation"]
        assert "RECONNECT ZEBRA" not in observed["text"]
        assert observed["visual_summary"]["visible_text"] == ["RECONNECT ZEBRA"]
        offered_regions = json.loads(records[0]["messages"][1]["content"][0]["text"].split("Observed region identities: ")[1])
        grid_ids = {e["id"] for e in observed["elements"] if e["role"] == "visual-region"}
        assert len(grid_ids) == 64
        assert grid_ids <= {r["target"] for r in offered_regions}
        assert observed["visual_summary"]["regions"][0]["target"] in grid_ids
        coverage = observed["visual_summary"]["region_coverage"]
        assert coverage["total"] > 256 and coverage["offered"] == 256 and coverage["omitted"] > 0
        assert observed["page_state"]["recovery_candidates"][0]["approval_required"] is True
        assert events[0]["page_state"]["recovery_available"] and events[1]["checkpoint"] == "needs_user"
        assert "RECONNECT ZEBRA" not in json.dumps(records[0])  # unprimed arbitrary visual readback request
        image = base64.b64decode(observed["screenshot"])
        assert image.startswith(b"\x89PNG\r\n\x1a\n")
        (tmp_path / "deterministic-viewport-visual.png").write_bytes(image)
        (tmp_path / "visual-evidence.json").write_text(json.dumps({"fixture": "local deterministic provider, not live OCR", "vision": observed["visual_summary"], "events": events}))
        assert result["steps"] == []  # never clicked or reloaded before approval
    finally:
        await provider.close()
        if session is not None:
            await session.close()
        server.shutdown()
        server.server_close()
        thread.join()



def test_noninteractive_collected_alerts_have_dom_provenance():
    observation = page("", visible_alerts=[{"role": "alert", "text": "Something went wrong", "frame_id": "frame-safe",
        "bounds": {"x": 10, "y": 10, "width": 200, "height": 30}}])
    result = describe_dom(observation)
    assert result["state"] == "error"
    assert result["visible_alerts"][0]["source"] == "dom"
    assert result["visible_alerts"][0]["frame_id"] == "frame-safe"
    assert result["visible_alerts"][0]["bounds"] == observation["visible_alerts"][0]["bounds"]
    assert observation["elements"] == []  # alert is not manufactured into an action target



@pytest.mark.parametrize("role", ["alert", "status", "button"])
def test_accessible_only_error_label_cannot_waive_reload_approval(role):
    observation = page("Actual rendered content", elements=[{"id": "e1", "role": role,
        "name": "Something went wrong", "operations": ["click"]}], visible_alerts=[])
    policy = {"reload_without_approval_origins": ["https://fixture.test"]}
    result = describe_dom(observation)
    assert result["visible_alerts"] == []
    assert not any(e["kind"] == "rendered_text_match" for e in result["evidence"])
    assert reload_approval_reason(observation, policy) is not None
    if role == "button":
        assert result["state"] == "error"
        assert result["evidence"][0]["kind"] == "accessible_control_label_match"
    else:
        assert result["state"] == "ready"



@pytest.mark.parametrize("element", [
    {"id": "select", "role": "combobox", "operations": ["select"], "selected_values": ["changed"]},
    {"id": "checkbox", "role": "checkbox", "input_type": "checkbox", "checked": True},
    {"id": "radio", "role": "radio", "input_type": "radio", "checked": False},
])
def test_nontext_form_controls_require_reload_approval_without_safety_metadata(element):
    assert reload_approval_reason(page(elements=[element]),
        {"reload_without_approval_origins": ["https://fixture.test"]}) is not None

