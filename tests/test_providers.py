"""Real socket recording tests; deterministic local replies, NOT live Luna evidence."""
import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from browser_automation.providers import (
    DecisionProvider, ProviderConfigurationError, ProviderProtocolError, ProviderRefusal,
)

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\nfixture").decode()


@pytest.fixture
def recording_server():
    records = []
    state = {"mode": "valid"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            records.append((self.path, payload, self.headers.get("Authorization")))
            if "messages" in payload:
                result = state.get("visual_result", {"text": "hello", "refusal": False})
                data = {"choices": [{"message": {"content": json.dumps(result)}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 7, "completion_tokens": 2}}
            else:
                router = "state" in payload
                questions = payload["questions"]
                name = next(iter(questions)) if router else questions[0]["name"]
                values = list(questions[name]["criteria"]) if router else [c["value"] for c in questions[0]["choices"]]
                selected = values[0]
                if state.get("operation") and name == "next_action":
                    descriptions = questions[name]["criteria"] if router else {c["value"]: c["description"] for c in questions[0]["choices"]}
                    selected = next(v for v, description in descriptions.items() if json.loads(description)["action"]["operation"] == state["operation"])
                probabilities = {v: 1.0 if v == selected else 0.0 for v in values}
                answer = {"type": "choice", "choice": selected, "confidence": .73,
                          "probabilities": probabilities if router else [{"value": v, "probability": p} for v, p in probabilities.items()]}
                if state["mode"] == "refusal":
                    answer = {"type": "refusal"}
                elif state["mode"] == "invalid":
                    answer["choice"] = "invented"
                elif state["mode"] == "optional":
                    answer.pop("probabilities")
                    answer.pop("confidence")
                elif state["mode"] == "bad_probability":
                    answer["probabilities"] = {v: -1 for v in values}
                if router:
                    answers = {name: answer}
                else:
                    answer["name"] = name
                    answers = [answer]
                data = {"answers": answers, "model": payload["model"], "usage": {"input_tokens": 11, "output_tokens": 2}}
            encoded = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", records, state
    server.shutdown()
    server.server_close()
    thread.join()


def observation(image=False):
    result = {"id": "r1", "tab_id": "t1", "text": "Page", "elements": [
        {"id": "e1", "role": "textbox", "name": "Search", "operations": ["fill"], "value": ""}]}
    if image:
        result["screenshot"] = PNG
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openrouter", "openai"])
async def test_exact_wire_and_field_text(recording_server, provider):
    url, records, _ = recording_server
    client = DecisionProvider(provider=provider, api_key="local-test-only", endpoint=url + "/decisions", text_endpoint=url + "/chat")
    try:
        result = await client.choose(observation(True), "Find hello", [])
        assert result["operation"] == "done"
        assert result["confidence"] == .73  # Separate confidence need not equal selected probability.
        body = records[0][1]
        if provider == "openrouter":
            assert isinstance(body["questions"], dict)
            assert isinstance(body["state"][0], str)
            assert body["state"][1] == {"type": "image_url", "image_url": {"url": "data:image/png;base64," + PNG}}
            assert "input" not in body
        else:
            assert isinstance(body["questions"], list)
            assert body["input"][0]["content"][1] == {"type": "input_image", "image_url": "data:image/png;base64," + PNG}
            assert "state" not in body
        assert await client.field_text(observation(True), "Find hello", "e1", []) == "hello"
        assert len(records) == 2
        assert records[1][1]["response_format"]["json_schema"]["strict"] is True
        assert records[1][1]["messages"][1]["content"][1] == {"type": "image_url", "image_url": {"url": "data:image/png;base64," + PNG}}
        verification = await client.verify(observation(), "Find hello", [])
        assert verification["satisfied"] and verification["probability"] == 1
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,exception", [("refusal", ProviderRefusal), ("invalid", ProviderProtocolError), ("bad_probability", ProviderProtocolError)])
async def test_malformed_and_refusal_stop(recording_server, mode, exception):
    url, records, state = recording_server
    state["mode"] = mode
    client = DecisionProvider(api_key="local", endpoint=url)
    try:
        with pytest.raises(exception):
            await client.choose(observation(), "goal", [])
        assert len(records) == 1
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_optional_router_probabilities_and_explicit_vision(recording_server):
    url, records, state = recording_server
    state["mode"] = "optional"
    client = DecisionProvider(api_key="local", endpoint=url, vision=False)
    try:
        result = await client.choose(observation(), "goal", [])
        assert result["confidence"] is None and result["probabilities"] is None
        with pytest.raises(ProviderProtocolError):
            await client.verify(observation(), "goal", [])
        with pytest.raises(ProviderConfigurationError):
            await client.choose(observation(True), "goal", [])
        assert len(records) == 2  # No image request sent to text-only transport.
    finally:
        await client.close()


def test_configuration_does_not_expose_secrets():
    with pytest.raises(ProviderConfigurationError) as exc:
        DecisionProvider(api_key="secret", endpoint="https://secret:secret@example.com")
    assert "secret" not in str(exc.value)

@pytest.mark.asyncio
async def test_select_value_is_decisions_choice_without_text_call(recording_server):
    url, records, state = recording_server
    state["operation"] = "select"
    page = observation()
    page["elements"] = [{"id": "s1", "role": "combobox", "name": "Color", "operations": ["select"],
                         "options": [{"value": "red-id", "label": "Red"}, {"value": "disabled-id", "label": "No", "disabled": True}]}]
    provider = DecisionProvider(api_key="local", endpoint=url)
    try:
        decision = await provider.choose(page, "Choose Red", [])
        assert decision["operation"] == "select" and decision["value"] == "red-id"
        assert len(records) == 1
        choices = records[0][1]["questions"]["next_action"]["criteria"]
        select = [json.loads(value) for value in choices.values() if json.loads(value)["action"]["operation"] == "select"]
        assert len(select) == 1 and select[0]["option_label"] == "Red"
    finally:
        await provider.close()


def test_large_action_windows_preserve_every_target_and_refine_points():
    from browser_automation.providers import action_window
    page = observation()
    page["elements"] = [{"id": f"e{i}", "role": "textbox", "operations": ["fill", "press"]} for i in range(200)]
    reached = set()
    while True:
        actions, metadata = action_window(page)
        assert len(actions) <= 48
        reached.update(a["target"] for a in actions.values() if "target" in a)
        if not any(a["operation"] == "next_actions" for a in actions.values()):
            break
        assert metadata["omitted_after"] > 0
        page["action_page"] = page.get("action_page", 0) + 1
    assert reached == {f"e{i}" for i in range(200)}
    canvas = {"elements": [{"id": "canvas", "role": "canvas", "operations": ["click", "drag", "scroll"],
                            "bounds": {"x": 0, "y": 0, "width": 300, "height": 300}},
                           {"id": "drop", "role": "visual-region", "operations": ["click"],
                            "bounds": {"x": 400, "y": 0, "width": 30, "height": 30}}]}
    actions, _ = action_window(canvas)
    refine = next(a for a in actions.values() if a["operation"] == "refine_point")
    canvas["point_refinements"] = {"canvas": {"bounds": refine["region"], "depth": refine["depth"]}}
    refined, _ = action_window(canvas)
    click = next(a for a in refined.values() if a["operation"] == "click" and "x" in a)
    assert 0 < click["x"] < 100 and 0 < click["y"] < 100
    assert any(a["operation"] == "drag" and a["to_target"] == "drop" for a in actions.values())
    assert any(a["operation"] == "scroll" and a.get("target") == "canvas" for a in actions.values())


@pytest.mark.asyncio
async def test_unprimed_visual_text_transport_and_region_validation(recording_server):
    url, records, state = recording_server
    state["visual_result"] = {"state": "error", "summary": "A recovery label is visible", "visible_text": ["ARBITRARY FIXTURE LABEL"],
        "region_targets": ["e1"], "recovery_recommended": True, "self_reported_confidence": .7, "refusal": False}
    client = DecisionProvider(api_key="deterministic-local-only", text_endpoint=url + "/chat")
    observed = observation(True)
    observed["elements"][0]["bounds"] = {"x": 0, "y": 0, "width": 80, "height": 30}
    try:
        result = await client.interpret_visual(observed)
        assert result["source"] == "vision" and result["calibrated"] is False
        assert result["visible_text"] == ["ARBITRARY FIXTURE LABEL"]
        assert result["regions"][0]["target"] == "e1"
        payload = records[0][1]
        assert payload["response_format"]["json_schema"]["name"] == "visual_summary"
        assert "ARBITRARY FIXTURE LABEL" not in json.dumps(payload)
        assert "Refresh" not in json.dumps(payload)
        assert payload["messages"][1]["content"][1]["type"] == "image_url"
        state["visual_result"]["region_targets"] = ["invented"]
        with pytest.raises(ProviderProtocolError, match="unobserved region"):
            await client.interpret_visual(observed)
        state["visual_result"]["region_targets"] = []
        state["visual_result"]["visible_text"] = ["x" * 241]
        with pytest.raises(ProviderProtocolError, match="bounded visual text"):
            await client.interpret_visual(observed)
    finally:
        await client.close()



@pytest.mark.asyncio
async def test_dense_dom_retains_all_visual_grid_and_reports_region_omission(recording_server):
    url, records, state = recording_server
    bounds = {"x": 0, "y": 0, "width": 10, "height": 10}
    observed = {"id": "dense", "screenshot": PNG, "elements": [
        {"id": f"dom{i}", "role": "button", "bounds": bounds} for i in range(2000)]
        + [{"id": "canvas", "role": "canvas", "bounds": bounds}]
        + [{"id": f"grid{i}", "role": "visual-region", "bounds": bounds} for i in range(64)]}
    state["visual_result"] = {"state": "error", "summary": "Deterministic arbitrary visual label", "visible_text": ["ZEBRA"],
        "region_targets": ["grid63"], "recovery_recommended": True, "self_reported_confidence": .7, "refusal": False}
    client = DecisionProvider(api_key="deterministic-local-only", text_endpoint=url + "/chat")
    try:
        result = await client.interpret_visual(observed)
        prompt = records[0][1]["messages"][1]["content"][0]["text"]
        regions = json.loads(prompt.split("Observed region identities: ")[1])
        ids = {r["target"] for r in regions}
        assert {f"grid{i}" for i in range(64)} <= ids and "canvas" in ids
        assert result["regions"][0]["target"] == "grid63"
        assert result["region_coverage"] == {"total": 2065, "offered": 256, "omitted": 1809,
            "limit": 256, "priority": "screenshot_grid_then_visual_then_dom"}
        assert '"omitted": 1809' in prompt
        assert "ZEBRA" not in prompt
    finally:
        await client.close()

