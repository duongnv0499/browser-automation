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
                data = {"choices": [{"message": {"content": json.dumps({"text": "hello", "refusal": False})}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 7, "completion_tokens": 2}}
            else:
                router = "state" in payload
                questions = payload["questions"]
                name = next(iter(questions)) if router else questions[0]["name"]
                values = list(questions[name]["criteria"]) if router else [c["value"] for c in questions[0]["choices"]]
                selected = values[0]
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
        assert await client.field_text(observation(), "Find hello", "e1", []) == "hello"
        assert len(records) == 2
        assert records[1][1]["response_format"]["json_schema"]["strict"] is True
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
