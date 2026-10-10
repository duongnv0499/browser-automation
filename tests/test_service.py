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
    async def tabs(self):
        return [{"id": "t", "url": "https://example.com", "title": "Test"}]
    async def tab_url(self, tab_id):
        return "https://example.com"
    async def close(self):
        self.closed = True
    async def close_tab(self, tab_id):
        pass
    stale = False
    async def check_observation(self, tab_id, observation_id, targets=()):
        self.checked = (tab_id, observation_id, list(targets))
        if self.stale:
            from browser_automation.browser import StaleObservationError
            raise StaleObservationError("Document, visible semantics, or form state changed; observe again")
    def holds_observation(self, tab_id, observation_id):
        return False
    async def navigate(self, tab_id, url, wait_until="domcontentloaded", timeout_ms=15000):
        self.navigated = (tab_id, url, wait_until, timeout_ms)
        return {"tab": {"id": tab_id, "url": url, "title": "Moved"}, "navigation_status": "complete", "wait_until": wait_until}


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

@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["file:///tmp/secret", "data:text/plain,secret", "chrome://settings", "javascript:alert(1)"])
async def test_external_prohibited_url_denied_before_navigation(url):
    service, session = service_with_session()
    with pytest.raises(ServiceError, match="only HTTP") as error:
        await service.dispatch("new_tab", {"session_id": "s", "url": url})
    assert error.value.code == "prohibited_url"

@pytest.mark.asyncio
async def test_existing_attached_local_file_not_observed():
    service, session = service_with_session()
    async def tab_url(tab_id):
        return "file:///tmp/private-key"
    session.tab_url = tab_url
    with pytest.raises(ServiceError) as error:
        await service.dispatch("observe", {"session_id": "s", "tab_id": "t"})
    assert error.value.code == "prohibited_url"
    assert session.maximum == 0

@pytest.mark.asyncio
async def test_closed_sessions_release_locks_and_pending_state():
    service, session = service_with_session()
    await service.dispatch("observe", {"session_id": "s", "tab_id": "t"})
    await service.dispatch("act", {"session_id": "s", "tab_id": "t", "action": {"observation_id": "revision", "operation": "click", "target": "button"}})
    assert service.pending_actions and service.snapshots and service.locks
    await service.dispatch("close", {"session_id": "s"})
    assert not service.sessions and not service.locks and not service.snapshots and not service.pending_actions

@pytest.mark.asyncio
async def test_targeted_url_check_does_not_enumerate_other_tabs():
    service, session = service_with_session()
    async def forbidden_tabs():
        raise AssertionError("Unrelated tab title reads are not needed")
    session.tabs = forbidden_tabs
    await service.dispatch("observe", {"session_id": "s", "tab_id": "t"})


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["file:///tmp/secret", "data:text/plain,secret", "chrome://settings", "javascript:alert(1)"])
async def test_navigate_prohibited_url_denied_before_browser(url):
    service, session = service_with_session()
    with pytest.raises(ServiceError) as error:
        await service.dispatch("navigate", {"session_id": "s", "tab_id": "t", "url": url})
    assert error.value.code == "prohibited_url"
    assert not hasattr(session, "navigated")


@pytest.mark.asyncio
async def test_navigate_drops_tab_observations_and_paused_approvals():
    from browser_automation.mcp import validate_arguments
    service, session = service_with_session()
    await service.dispatch("observe", {"session_id": "s", "tab_id": "t"})
    paused = await service.dispatch("act", {"session_id": "s", "tab_id": "t", "action": {"observation_id": "revision", "operation": "click", "target": "button", "settle_ms": 0}})
    assert paused["status"] == "approval_required" and service.pending_actions
    moved = await service.dispatch("navigate", {"session_id": "s", "tab_id": "t", "url": "https://example.com/next", "timeout_ms": 500})
    assert moved["tab"]["url"] == "https://example.com/next" and session.navigated == ("t", "https://example.com/next", "domcontentloaded", 500)
    assert not service.snapshots and not service.pending_actions
    with pytest.raises(ServiceError) as stale:
        await service.dispatch("act", {"session_id": "s", "tab_id": "t", "action": {"observation_id": "revision", "operation": "wait"}})
    assert stale.value.code == "unknown_observation"
    for bad in ({"session_id": "s", "tab_id": "t"}, {"session_id": "s", "tab_id": "t", "url": "https://example.com", "wait_until": "networkidle"}):
        with pytest.raises(ServiceError) as invalid:
            validate_arguments("navigate", bad)
        assert invalid.value.code == "invalid_argument"
    for settle in (-1, 10001, 1.5, True):
        with pytest.raises(ServiceError):
            validate_arguments("act", {"session_id": "s", "tab_id": "t", "action": {"observation_id": "r", "operation": "click", "target": "x", "settle_ms": settle}})
    validate_arguments("act", {"session_id": "s", "tab_id": "t", "action": {"observation_id": "r", "operation": "click", "target": "x", "settle_ms": 0}})


@pytest.mark.asyncio
async def test_stale_observation_rejected_before_minting_approval():
    service, session = service_with_session()
    await service.dispatch("observe", {"session_id": "s", "tab_id": "t"})
    session.stale = True
    with pytest.raises(Exception) as stale:
        await service.dispatch("act", {"session_id": "s", "tab_id": "t", "action": {"observation_id": "revision", "operation": "click", "target": "button"}})
    assert stale.value.code == "stale_observation"
    assert session.checked == ("t", "revision", ["button"])
    assert not service.pending_actions and ("s", "revision") not in service.snapshots
    session.stale = False
    await service.dispatch("observe", {"session_id": "s", "tab_id": "t"})
    paused = await service.dispatch("act", {"session_id": "s", "tab_id": "t", "action": {"observation_id": "revision", "operation": "click", "target": "button"}})
    assert paused["status"] == "approval_required" and len(service.pending_actions) == 1


@pytest.mark.asyncio
async def test_tab_cache_dropped_after_input_commands():
    service, session = service_with_session()
    await service.dispatch("observe", {"session_id": "s", "tab_id": "t"})
    executed = await service.dispatch("act", {"session_id": "s", "tab_id": "t", "action": {"observation_id": "revision", "operation": "wait", "seconds": 0}})
    assert executed["executed"]["operation"] == "wait" and not service.snapshots
    with pytest.raises(ServiceError) as old:
        await service.dispatch("act", {"session_id": "s", "tab_id": "t", "action": {"observation_id": "revision", "operation": "wait"}})
    assert old.value.code == "unknown_observation"
    async def failing_act(tab_id, action):
        raise RuntimeError("input may have been dispatched")
    session.act = failing_act
    await service.dispatch("observe", {"session_id": "s", "tab_id": "t"})
    with pytest.raises(RuntimeError):
        await service.dispatch("act", {"session_id": "s", "tab_id": "t", "action": {"observation_id": "revision", "operation": "wait"}})
    assert not service.snapshots
    await service.dispatch("observe", {"session_id": "s", "tab_id": "t"})
    await service.dispatch("close_tab", {"session_id": "s", "tab_id": "t"})
    assert not service.snapshots


@pytest.mark.asyncio
async def test_preinput_failure_keeps_cache_when_engine_still_holds_revision():
    service, session = service_with_session()
    from browser_automation.browser import UnsafeActionError
    async def covered(tab_id, action):
        raise UnsafeActionError("covered target")
    session.act = covered
    session.holds_observation = lambda tab_id, observation_id: True
    await service.dispatch("observe", {"session_id": "s", "tab_id": "t"})
    with pytest.raises(UnsafeActionError):
        await service.dispatch("act", {"session_id": "s", "tab_id": "t", "action": {"observation_id": "revision", "operation": "wait"}})
    assert ("s", "revision") in service.snapshots


@pytest.mark.asyncio
async def test_paused_run_keeps_fresh_snapshot_and_pending_action(monkeypatch):
    from browser_automation.agent import BrowserAgent
    from browser_automation.providers import DecisionProvider
    class Provider:
        capabilities = {"vision": False}
        async def close(self):
            pass
    monkeypatch.setattr(DecisionProvider, "from_env", classmethod(lambda cls, **kwargs: Provider()))
    paused_observation = {"id": "fresh", "tab_id": "t", "text": "x", "elements": [], "screenshot": "PNG"}
    async def run(self, tab_id, goal):
        return {"status": "approval_required", "steps": [{"action": {"operation": "click"}, "result": {"popup_tabs": ["popup"]}}],
                "observation": paused_observation, "active_tab": tab_id,
                "approval": {"tab_id": tab_id, "observation_id": "fresh", "action": {"observation_id": "fresh", "operation": "click", "target": "button"}, "reason": "risky"}}
    monkeypatch.setattr(BrowserAgent, "run", run)
    service, session = service_with_session()
    await service.dispatch("observe", {"session_id": "s", "tab_id": "t"})
    service.snapshots[("s", "popup-old")] = {"id": "popup-old", "tab_id": "popup"}
    result = await service.dispatch("run", {"session_id": "s", "tab_id": "t", "goal": "Pause for approval"})
    assert result["host_approval"]["binding"]["observation_id"] == "fresh"
    assert set(service.snapshots) == {("s", "fresh")} and "screenshot" not in service.snapshots[("s", "fresh")]
    assert set(service.pending_actions) == {("s", "t", "fresh")}
