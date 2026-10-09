"""Real local HTTP and Chromium request execution; no transport mocks."""
import asyncio
import base64
import json
from contextlib import asynccontextmanager

import pytest

from browser_automation.browser import BrowserSession


@asynccontextmanager
async def api_site():
    seen = []
    tasks = set()
    redirect = {'url': '/echo'}

    async def serve(reader, writer):
        task = asyncio.current_task()
        tasks.add(task)
        try:
            lines = (await reader.readuntil(b'\r\n\r\n')).decode().split('\r\n')
            method, path, _ = lines[0].split(' ', 2)
            headers = dict((n.lower(), v.strip()) for line in lines[1:] if ':' in line for n, v in [line.split(':', 1)])
            body = await reader.readexactly(int(headers.get('content-length', 0)))
            seen.append({'method': method, 'path': path, 'headers': headers, 'body': body.decode(errors='replace')})
            if path.startswith('/disconnect'):
                return
            if path.startswith('/slow'):
                await asyncio.sleep(30)
            status, extra = '200 OK', ''
            content_type = 'application/json'
            if path == '/':
                content_type = 'text/html'
                payload = b'''<title>Request fixture</title><h1>Quiet UI</h1><button id="send" onclick="fetch('/echo?ordinary=yes',{method:'POST',headers:{'Authorization':'Bearer fixture-only','X-CSRF-Token':'csrf-fixture','Content-Type':'application/json'},body:JSON.stringify({value:'original'})})">Send</button>'''
                extra = 'Set-Cookie: session=fixture-only; HttpOnly; Path=/\r\n'
            elif path.startswith('/redirect'):
                status, extra, payload = '307 Temporary Redirect', 'Location: ' + redirect['url'] + '\r\n', b''
            elif path.startswith('/binary'):
                content_type, payload = 'application/octet-stream', bytes(range(256)) * 512
            elif path.startswith('/large'):
                payload = json.dumps({'ordinary': 'x' * 150000}).encode()
            else:
                payload = json.dumps(seen[-1]).encode()
                if path.startswith('/503'):
                    status = '503 Service Unavailable'
                extra = 'Set-Cookie: updated=from-api; HttpOnly; Path=/\r\nSet-Cookie: second=value; Path=/\r\n'
            writer.write(f'HTTP/1.1 {status}\r\n{extra}Content-Type: {content_type}\r\nContent-Length: {len(payload)}\r\nConnection: close\r\n\r\n'.encode() + payload)
            await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()
            tasks.discard(task)

    server = await asyncio.start_server(serve, '127.0.0.1', 0)
    try:
        yield f'http://127.0.0.1:{server.sockets[0].getsockname()[1]}', seen, redirect
    finally:
        server.close()
        await server.wait_closed()
        for task in tuple(tasks):
            task.cancel()
        await asyncio.gather(*tuple(tasks), return_exceptions=True)


async def captured_post(browser, tab):
    for _ in range(100):
        events = (await browser.monitor_list(tab, 'network', limit=1000))['events']
        request = next((e for e in events if e.get('method') == 'POST' and e.get('request_id')), None)
        if request:
            return request['request_id']
        await asyncio.sleep(.02)
    raise AssertionError('POST was not captured')


@pytest.mark.asyncio
async def test_actual_cookie_authenticated_replay_and_exact_one_use_plan(tmp_path, monkeypatch):
    monkeypatch.setenv('BROWSER_NETWORK_SENSITIVE', '1')
    async with api_site() as (url, seen, _), await BrowserSession.launch(headless=True) as browser:
        tab = (await browser.new_tab(url))['id']
        target = (await browser.new_tab(url))['id']
        await browser.monitor_start(tab, 'network')
        await browser._page(tab).click('#send')
        source = await captured_post(browser, tab)
        await browser._page(target).context.add_cookies([{'name': 'session', 'value': 'jar-current', 'url': url, 'httpOnly': True}])
        before = len(seen)
        plan = await browser.network_replay(tab, source, target_tab_id=target, json_body={'value': 'edited'})
        assert plan['approval_required']
        assert len(seen) == before
        assert 'fixture-only' not in json.dumps(plan)
        assert await browser.network_plan(plan['plan_id']) == plan
        result = await browser.network_execute(plan['plan_id'], approved=True)
        assert result['status'] == 200 and result['tab_id'] == target
        assert seen[-1]['headers']['authorization'] == 'Bearer fixture-only'
        assert seen[-1]['headers']['x-csrf-token'] == 'csrf-fixture'
        assert 'session=jar-current' in seen[-1]['headers']['cookie']
        assert json.loads(seen[-1]['body']) == {'value': 'edited'}
        assert any(c['name'] == 'updated' for c in await browser._page(target).context.cookies())
        chunk = await browser.network_body(target, result['request_id'], include_sensitive=True)
        assert json.loads(chunk['data'])['body'] == '{"value":"edited"}'
        assert len([h for h in result['response_headers'] if h['name'].lower() == 'set-cookie']) == 2
        with pytest.raises(ValueError, match='consumed'):
            await browser.network_execute(plan['plan_id'], approved=True)
        plan = await browser.network_replay(tab, source)
        await browser.monitor_stop(tab, 'network')
        with pytest.raises(ValueError):
            await browser.network_plan(plan['plan_id'])
        assert await browser._page(tab).locator('h1').inner_text() == 'Quiet UI'
        await browser._page(tab).screenshot(path=str(tmp_path / 'request-ui-unchanged.png'))


@pytest.mark.asyncio
async def test_cross_origin_redirect_strips_credentials_and_safe_read_stops():
    async with api_site() as (a, seen_a, redirect), api_site() as (b, seen_b, _), await BrowserSession.launch(headless=True) as browser:
        tab = (await browser.new_tab(a))['id']
        redirect['url'] = b + '/echo'
        before = len(seen_b)
        safe = await browser.network_call(tab, url=a + '/redirect')
        assert not safe['approval_required']
        stopped = await browser.network_execute(safe['plan_id'], approved=True)
        assert stopped['diagnostic']['code'] == 'redirect_reapproval_required'
        assert len(seen_b) == before
        plan = await browser.network_call(tab, url=a + '/redirect', method='POST', headers={'Authorization': 'Bearer private', 'X-CSRF-Token': 'private'}, body='exact')
        result = await browser.network_execute(plan['plan_id'], approved=True)
        assert result['status'] == 307
        assert result['diagnostic']['code'] == 'redirect_reapproval_required'
        assert len(seen_b) == before  # Even exact-approved POST cannot authorize a new origin.
        follow = await browser.network_call(tab, url=b + '/echo', method='POST', body='exact')
        assert follow['approval_required']
        result = await browser.network_execute(follow['plan_id'], approved=True)
        assert result['status'] == 200
        assert 'authorization' not in seen_b[-1]['headers']
        assert 'x-csrf-token' not in seen_b[-1]['headers']
        assert seen_b[-1]['body'] == 'exact'
        assert len(seen_a) == 3


@pytest.mark.asyncio
async def test_http_error_no_retry_binary_chunks_eviction_and_custom_method(monkeypatch):
    monkeypatch.setenv('BROWSER_NETWORK_CACHE_LIMIT', '150000')
    async with api_site() as (url, seen, _), await BrowserSession.launch(headless=True) as browser:
        tab = (await browser.new_tab(url))['id']
        plan = await browser.network_call(tab, url=url + '/503')
        result = await browser.network_execute(plan['plan_id'], approved=True)
        assert result['status'] == 503
        assert sum(r['path'] == '/503' for r in seen) == 1
        plan = await browser.network_call(tab, url=url + '/binary')
        binary = await browser.network_execute(plan['plan_id'], approved=True)
        first = await browser.network_body(tab, binary['request_id'], limit=4096)
        assert first['encoding'] == 'base64'
        assert base64.b64decode(first['data']) == (bytes(range(256)) * 16)
        assert first['next_offset'] == 4096
        plan = await browser.network_call(tab, url=url + '/large')
        large = await browser.network_execute(plan['plan_id'], approved=True)
        chunk = await browser.network_body(tab, large['request_id'])
        assert chunk['truncated']
        assert not chunk['source_complete']
        assert chunk['unavailable_reason'] == 'cache_limit'
        evicted = await browser.network_body(tab, binary['request_id'])
        assert evicted['unavailable_reason'] == 'body_evicted'
        plan = await browser.network_call(tab, url=url + '/echo', method='REPORT', body_base64=base64.b64encode(b'raw').decode())
        result = await browser.network_execute(plan['plan_id'], approved=True)
        assert result['status'] == 200 and seen[-1]['method'] == 'REPORT'
        plan = await browser.network_call(tab, url=url + '/disconnect', method='POST')
        result = await browser.network_execute(plan['plan_id'], approved=True)
        assert result['unavailable_reason'] == 'transport_error'
        assert sum(r['path'] == '/disconnect' for r in seen) == 1


@pytest.mark.asyncio
async def test_plan_ttl_hash_and_context_credentials_not_imported(monkeypatch):
    async with api_site() as (url, seen, _), await BrowserSession.launch(headless=True) as browser:
        tab = (await browser.new_tab(url))['id']
        await browser.monitor_start(tab, 'network')
        await browser._page(tab).click('#send')
        source = await captured_post(browser, tab)
        context = await browser._browser.new_context()
        page = await context.new_page()
        await page.goto(url)
        browser._sync_pages()
        target = browser._ids[page]
        plan = await browser.network_replay(tab, source, target_tab_id=target)
        await browser.network_execute(plan['plan_id'], approved=True)
        assert 'authorization' not in seen[-1]['headers']
        assert 'x-csrf-token' not in seen[-1]['headers']
        first = await browser.network_call(tab, url=url + '/echo', method='POST', body='a')
        second = await browser.network_call(tab, url=url + '/echo', method='POST', body='b')
        assert first['plan_hash'] != second['plan_hash']
        from browser_automation import browser_requests
        monkeypatch.setattr(browser_requests.time, 'time', lambda: first['expires_at'] + 1)
        with pytest.raises(ValueError, match='expired'):
            await browser.network_plan(first['plan_id'])


@pytest.mark.asyncio
async def test_cancelled_request_consumes_plan_without_retry():
    async with api_site() as (url, seen, _), await BrowserSession.launch(headless=True) as browser:
        tab = (await browser.new_tab(url))['id']
        plan = await browser.network_call(tab, url=url + '/slow', method='POST', body='once')
        task = asyncio.create_task(browser.network_execute(plan['plan_id'], approved=True))
        for _ in range(100):
            if any(r['path'] == '/slow' for r in seen):
                break
            await asyncio.sleep(.01)
        assert sum(r['path'] == '/slow' for r in seen) == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(ValueError, match='consumed'):
            await browser.network_execute(plan['plan_id'], approved=True)
        assert sum(r['path'] == '/slow' for r in seen) == 1


@pytest.mark.asyncio
async def test_safety_classifier_host_policy_url_and_document_binding(monkeypatch):
    async with api_site() as (url, seen, _), await BrowserSession.launch(headless=True) as browser:
        tab = (await browser.new_tab(url))['id']
        for method in ('GET', 'HEAD', 'OPTIONS'):
            plan = await browser.network_call(tab, url=url + '/echo?ordinary=yes', method=method)
            assert not plan['approval_required']
        for spec in ({'method': 'DELETE'}, {'url': url + '/delete'}, {'url': url + '/echo?action=publish'}, {'headers': {'Authorization': 'fixture'}}, {'body': ''}):
            request = {'url': url + '/echo', **spec}
            assert (await browser.network_call(tab, **request))['approval_required']
        for unsafe in ('file:///tmp/private', 'chrome://settings', 'http://name:secret@localhost/'):
            with pytest.raises(ValueError):
                await browser.network_call(tab, url=unsafe)
        plan = await browser.network_call(tab, url=url + '/echo')
        await browser._page(tab).goto(url + '/other')
        before = len(seen)
        with pytest.raises(Exception, match='document URL changed'):
            await browser.network_execute(plan['plan_id'], approved=True)
        assert len(seen) == before
        monkeypatch.setenv('BROWSER_NETWORK_REQUIRE_APPROVAL', '1')
        assert (await browser.network_call(tab, url=url + '/echo'))['approval_required']


@pytest.mark.asyncio
async def test_form_text_and_parameter_overrides_preserve_exact_wire_payload():
    async with api_site() as (url, seen, _), await BrowserSession.launch(headless=True) as browser:
        tab = (await browser.new_tab(url))['id']
        plan = await browser.network_call(tab, url=url + '/echo?existing=yes&ordinary=old&ordinary=older', method='PATCH', params={'ordinary': 'a b'}, form={'value': 'space value', 'number': '3'})
        result = await browser.network_execute(plan['plan_id'], approved=True)
        assert result['status'] == 200
        assert seen[-1]['path'] == '/echo?existing=yes&ordinary=a+b'
        assert seen[-1]['body'] == 'value=space+value&number=3'
        assert seen[-1]['headers']['content-type'] == 'application/x-www-form-urlencoded'
        plan = await browser.network_call(tab, url=url + '/echo', method='PUT', headers={'Content-Type': 'text/plain'}, body='exact \u2603')
        await browser.network_execute(plan['plan_id'], approved=True)
        assert seen[-1]['body'] == 'exact \u2603'
        assert seen[-1]['headers']['content-type'] == 'text/plain'
        with pytest.raises(ValueError, match='mutually exclusive'):
            await browser.network_call(tab, url=url + '/echo', body='x', json_body={'value': 1})


@pytest.mark.asyncio
async def test_approved_same_origin_redirect_does_not_authorize_new_risky_path():
    async with api_site() as (url, seen, redirect), await BrowserSession.launch(headless=True) as browser:
        tab = (await browser.new_tab(url))['id']
        redirect['url'] = '/delete'
        plan = await browser.network_call(tab, url=url + '/redirect', method='POST', body='do not forward')
        result = await browser.network_execute(plan['plan_id'], approved=True)
        assert result['diagnostic']['code'] == 'redirect_reapproval_required'
        assert not any(r['path'] == '/delete' for r in seen)
