"""Real Chromium regressions for partial observations and bounded navigation."""
import asyncio

import pytest

from browser_automation.browser import BrowserSession, ProtectedUrlError, UnsafeActionError


@pytest.mark.asyncio
@pytest.mark.parametrize('scheme', ['data', 'blob'])
async def test_hidden_protected_frame_keeps_parent_and_pixels(scheme):
    async with await BrowserSession.launch(headless=True) as browser:
        tab = (await browser.new_tab())['id']
        page = browser._page(tab)
        await page.set_content('<button>Parent control</button><p>Public parent text</p>')
        await page.evaluate('''scheme => new Promise(resolve => {const f=document.createElement('iframe');f.hidden=true;f.onload=resolve;
          const html='<p>PRIVATE-FRAME-SECRET</p><iframe src="data:text/html,PRIVATE-NESTED-SECRET"></iframe>';
          f.src=scheme==='data'?'data:text/html,'+encodeURIComponent(html):URL.createObjectURL(new Blob([html],{type:'text/html'}));document.body.append(f);})''', scheme)
        for pixels in (False, True):
            observation = await browser.observe(tab, screenshot=pixels)
            assert 'Public parent text' in observation['text']
            assert 'PRIVATE-FRAME-SECRET' not in str(observation)
            assert 'PRIVATE-NESTED-SECRET' not in str(observation)
            skipped = next(x for x in observation['limitations'] if x.get('scheme') == scheme)
            assert skipped['code'] == 'protected_subframe_skipped'
            assert skipped['visibility'] == 'hidden'
            assert bool(observation.get('screenshot')) == pixels
            assert all(x['visibility'] == 'hidden' for x in observation['limitations'] if x.get('code') == 'protected_subframe_skipped')


@pytest.mark.asyncio
async def test_visible_protected_frame_withholds_pixels_not_parent():
    async with await BrowserSession.launch(headless=True) as browser:
        tab = (await browser.new_tab())['id']
        page = browser._page(tab)
        await page.set_content('<button>Parent control</button><iframe src="data:text/html,PRIVATE-FRAME-SECRET"></iframe>')
        observation = await browser.observe(tab, screenshot=True)
        assert observation['screenshot_status'] == 'withheld'
        assert 'screenshot' not in observation
        assert 'PRIVATE-FRAME-SECRET' not in str(observation)
        assert observation['coverage']['status'] == 'partial'
        assert any(e['name'] == 'Parent control' for e in observation['elements'])
        assert not any(e['role'] == 'visual-region' for e in observation['elements'])
        with pytest.raises(UnsafeActionError):
            await browser.act(tab, {'observation_id': observation['id'], 'operation': 'click', 'target': 'visual:0:0'})
        await page.goto('data:text/html,PRIVATE-TOP-SECRET')
        with pytest.raises(ProtectedUrlError):
            await browser.observe(tab, screenshot=True)


@pytest.mark.asyncio
async def test_boxless_custom_shadow_and_clipped_semantics():
    async with await BrowserSession.launch(headless=True) as browser:
        tab = (await browser.new_tab())['id']
        page = browser._page(tab)
        await page.set_content('''<style>custom-shell{display:contents;overflow:hidden}
        .clip{height:25px;width:200px;overflow:hidden}</style>
        <custom-shell><input aria-label="Search"><a href="https://example.com/">Home</a>
        Boxless visible text<div id="shadow"></div></custom-shell>
        <zero-host style="overflow:hidden"><div><button>Zero box host control</button></div></zero-host>
        <div style="height:0;overflow:hidden"><button>Zero height clipped control</button></div>
        <div class="clip"><button style="position:relative;top:80px">Clipped hidden control</button></div>
        <div hidden><button>Ancestor hidden control</button></div>
        <script>document.querySelector('#shadow').attachShadow({mode:'open'}).innerHTML='<custom-shell><button>Shadow video</button></custom-shell>'</script>''')
        observation = await browser.observe(tab, screenshot=True)
        names = {e['name'] for e in observation['elements']}
        assert {'Search', 'Home', 'Shadow video', 'Zero box host control'} <= names
        assert 'Clipped hidden control' not in names
        assert 'Ancestor hidden control' not in names
        assert 'Zero height clipped control' not in names
        assert 'Zero height clipped control' not in observation['text']
        assert 'Clipped hidden control' not in observation['text']
        assert 'Boxless visible text' in observation['text']
        assert observation['coverage']['status'] == 'complete'
        target = next(e for e in observation['elements'] if e['name'] == 'Shadow video')
        await browser.act(tab, {'operation': 'hover', 'target': target['id'], 'observation_id': observation['id']})
        await page.set_content('<canvas width="300" height="100"></canvas>')
        empty = await browser.observe(tab)
        assert empty['coverage']['status'] == 'partial'
        assert 'empty_rendered_dom' in empty['coverage']['reasons']
        await page.set_content('<textarea style="position:absolute;top:3000px">Offscreen unsaved draft</textarea><div contenteditable="plaintext-only" style="position:absolute;top:3100px">Another draft</div><input type="password" style="display:none" value="private-secret"><button>Visible</button>')
        safety = await browser.observe(tab)
        assert safety['safety']['editable_nonempty'] is True
        assert safety['safety']['sensitive_fields'] is True
        assert 'private-secret' not in str(safety)
        assert 'Offscreen unsaved draft' not in safety['text']


@pytest.mark.asyncio
async def test_load_timeout_retains_observable_owned_tab():
    release = asyncio.Event()
    async def handle(reader, writer):
        try:
            request = await reader.readuntil(b'\r\n\r\n')
            if request.split(b' ')[1] == b'/pending':
                await release.wait()
                body = b'late image'
            else:
                body = b'<title>Partial page</title><button>Rendered before load</button><img src="/pending">'
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: ' + str(len(body)).encode() + b'\r\nConnection: close\r\n\r\n' + body)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
    server = await asyncio.start_server(handle, '127.0.0.1', 0)
    try:
        async with await BrowserSession.launch(headless=True) as browser:
            port = server.sockets[0].getsockname()[1]
            tab = await browser.new_tab(f'http://127.0.0.1:{port}/', wait_until='load', timeout_ms=500)
            assert tab['navigation_status'] == 'timeout'
            assert tab['diagnostic']['code'] == 'navigation_timeout'
            observation = await browser.observe(tab['id'])
            assert any(e['name'] == 'Rendered before load' for e in observation['elements'])
            result = await browser.act(tab['id'], {'operation': 'reload', 'observation_id': observation['id'], 'wait_until': 'load', 'timeout_ms': 500})
            assert result['navigation_status'] == 'timeout'
            await browser.close_tab(tab['id'])
            assert not any(t['id'] == tab['id'] for t in await browser.tabs())
    finally:
        release.set()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_body_overflow_propagates_to_viewport_not_zero_body_box():
    async with await BrowserSession.launch(headless=True) as browser:
        tab = (await browser.new_tab())['id']
        page = browser._page(tab)
        await page.set_content('''<!doctype html><style>html{overflow:visible}body{margin:0;overflow-y:scroll}app-shell{position:absolute;inset:0;display:block}</style>
        <app-shell><input aria-label="Search"><button onclick="document.body.dataset.clicked='yes'">Viewport control</button><p>Rendered under zero-height body</p></app-shell>''')
        assert await page.evaluate('document.body.clientHeight') == 0
        observation = await browser.observe(tab, screenshot=True)
        assert any(e['name'] == 'Search' for e in observation['elements'])
        target = next(e for e in observation['elements'] if e['name'] == 'Viewport control')
        assert 'Rendered under zero-height body' in observation['text']
        await browser.act(tab, {'operation': 'click', 'target': target['id'], 'observation_id': observation['id']})
        assert await page.get_attribute('body', 'data-clicked') == 'yes'


@pytest.mark.asyncio
async def test_hidden_alert_descendants_not_rendered_evidence():
    async with await BrowserSession.launch(headless=True) as browser:
        tab = (await browser.new_tab())['id']
        page = browser._page(tab)
        await page.set_content('''<div role="alert" style="width:200px;height:40px"><span hidden>Something went wrong PRIVATE-ALERT-SECRET</span></div>
        <div role="status" style="width:200px;height:40px;overflow:hidden"><span style="position:relative;top:80px">PRIVATE-CLIPPED-SECRET</span></div>
        <button>Continue</button>''')
        hidden = await browser.observe(tab)
        assert hidden['visible_alerts'] == []
        assert 'PRIVATE-ALERT-SECRET' not in str(hidden)
        assert 'PRIVATE-CLIPPED-SECRET' not in str(hidden)
        assert hidden['page_state']['state'] != 'error'
        await page.set_content('<div role="alert">Something went wrong. Try again.</div><button>Continue</button>')
        visible = await browser.observe(tab)
        assert visible['visible_alerts'][0]['text'] == 'Something went wrong. Try again.'
        assert visible['visible_alerts'][0]['source'] == 'dom'
        assert visible['page_state']['state'] == 'error'
