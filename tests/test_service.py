import asyncio
import json
import time
import pytest
from browser_automation.service import BrowserService, ServiceError, local_endpoint
from browser_automation.mcp import tool_result


class Session:
    def __init__(self):
        self.closed = False
        self.active = 0
        self.maximum = 0
    async def observe(self, tab_id, screenshot=False, max_text=12000):
        self.active += 1
        self.maximum = max(self.maximum, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        return {"id": "revision", "tab_id": tab_id, "text": "abcdefghij"[:max_text], "text_length": 10, "truncated": max_text < 10, "elements": [{"id": "button", "role": "button", "name": "Delete record"}], "screenshot": "PNG" if screenshot else None}
    async def text_continuation(self, tab_id, observation_id, offset=0, max_text=12000):
        end = min(offset + max_text, 10)
        return {"text": "abcdefghij"[offset:end], "next_offset": end if end < 10 else None}
    async def act(self, tab_id, action):
        return {"executed": action}
    async def close(self):
        self.closed = True
    async def close_tab(self, tab_id):
        pass


def service_with_session():
    service = BrowserService()
    session = Session()
    service.sessions["s"] = session
    service.locks["s"] = asyncio.Lock()
    return service, session


@pytest.mark.parametrize("url", ["http://127.0.0.1:9222", "ws://[::1]:9222/devtools/browser/x", "http://localhost:9222"])
def test_loopback_endpoints(url):
    assert local_endpoint(url) == url


@pytest.mark.parametrize("url", ["https://example.com", "file:///tmp/socket", "http://user:secret@127.0.0.1:9222", "http://127.0.0.1.evil.invalid", "http://0.0.0.0:9222"])
def test_remote_endpoints_rejected(url):
    with pytest.raises(ServiceError):
        local_endpoint(url)


@pytest.mark.asyncio
async def test_cached_continuation_serialization_close():
    service, session = service_with_session()
    a, b = await asyncio.gather(*[service.dispatch("observe", {"session_id": "s", "tab_id": "t", "max_text": 3}) for _ in range(2)])
    assert a["text"] == "abc" and a["truncated"]
    assert session.maximum == 1
    continuation = await service.dispatch("text", {"session_id": "s", "observation_id": "revision", "offset": 3, "limit": 4})
    assert continuation["text"] == "defg" and continuation["next_offset"] == 7
    assert await service.dispatch("close_tab", {"session_id": "s", "tab_id": "t"}) == {"closed_tab": "t"}
    await service.close()
    assert session.closed


def test_mcp_image_separate():
    result = tool_result({"id": "r", "screenshot": "PNG", "text": "hello"})
    assert result["content"][1] == {"type": "image", "mimeType": "image/png", "data": "PNG"}
    assert "screenshot" not in result["structuredContent"]
    assert "PNG" not in result["content"][0]["text"]


def test_exact_expiring_host_approval(tmp_path, monkeypatch):
    path = tmp_path / "approvals.json"
    binding = {"operation": "upload", "target": "x", "paths": ["/allowed/a"]}
    path.write_text(json.dumps([{"token": "secret", "binding": binding, "expires_at": time.time() + 60}]))
    monkeypatch.setenv("BROWSER_APPROVALS_FILE", str(path))
    service = BrowserService()
    assert not service._approve({**binding, "target": "other"}, "secret")
    assert not service._approve(binding, "different")
    assert service._approve(binding, "secret")
    assert not service._approve(binding, "secret")
    second = BrowserService()
    path.write_text(json.dumps([{"token": "secret", "binding": binding, "expires_at": time.time() - 1}]))
    assert not second._approve(binding, "secret")

@pytest.mark.asyncio
async def test_direct_risky_action_requires_exact_host_approval(tmp_path, monkeypatch):
    path = tmp_path / "approvals.json"
    path.write_text("[]")
    monkeypatch.setenv("BROWSER_APPROVALS_FILE", str(path))
    service, session = service_with_session()
    await service.dispatch("observe", {"session_id": "s", "tab_id": "t"})
    action = {"observation_id": "revision", "operation": "click", "target": "button"}
    paused = await service.dispatch("act", {"session_id": "s", "tab_id": "t", "action": action})
    assert paused["status"] == "approval_required"
    path.write_text(json.dumps([{"token": "host", "binding": paused["binding"], "expires_at": time.time() + 60}]))
    resumed = await service.dispatch("approved_act", {"session_id": "s", "tab_id": "t", "observation_id": "revision", "approval_token": "host"})
    assert resumed["executed"] == action
    with pytest.raises(ServiceError):
        await service.dispatch("approved_act", {"session_id": "s", "tab_id": "t", "observation_id": "revision", "approval_token": "host"})


@pytest.mark.asyncio
async def test_unknown_session_error():
    with pytest.raises(ServiceError, match="retain session_id"):
        await BrowserService().dispatch("tabs", {"session_id": "gone"})

@pytest.mark.asyncio
async def test_jsonlines_cancel_preserves_worker(monkeypatch, capsys):
    from browser_automation.cli import json_lines
    import io
    import sys
    cancelled = asyncio.Event()
    class Worker:
        closed = False
        async def dispatch(self, command, args):
            if command == "slow":
                try:
                    await asyncio.sleep(60)
                except asyncio.CancelledError:
                    cancelled.set()
                    raise
            return {"alive": True}
        async def close(self):
            self.closed = True
    worker = Worker()
    requests = '\n'.join([json.dumps({"id": 1, "command": "slow"}), json.dumps({"command": "cancel", "arguments": {"request_id": 1}}), json.dumps({"id": 2, "command": "doctor"})]) + '\n'
    monkeypatch.setattr(sys, "stdin", io.StringIO(requests))
    await json_lines(worker)
    results = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert cancelled.is_set()
    assert any(result.get("id") == 1 and result["error"]["code"] == "cancelled" for result in results)
    assert any(result.get("id") == 2 and result.get("result", {}).get("alive") for result in results)
    assert not worker.closed
