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
        await page.evaluate('''scheme => {const f=document.createElement('iframe');f.hidden=true;
          f.src=scheme==='data'?'data:text/html,PRIVATE-FRAME-SECRET':URL.createObjectURL(new Blob(['PRIVATE-FRAME-SECRET'],{type:'text/html'}));document.body.append(f);}''', scheme)
        await page.wait_for_load_state('load')
        for pixels in (False, True):
            observation = await browser.observe(tab, screenshot=pixels)
            assert 'Public parent text' in observation['text']
            assert 'PRIVATE-FRAME-SECRET' not in str(observation)
            skipped = next(x for x in observation['limitations'] if x.get('scheme') == scheme)
            assert skipped['code'] == 'protected_subframe_skipped'
            assert skipped['visibility'] == 'hidden'
            assert bool(observation.get('screenshot')) == pixels


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
        <div class="clip"><button style="position:relative;top:80px">Clipped hidden control</button></div>
        <div hidden><button>Ancestor hidden control</button></div>
        <script>document.querySelector('#shadow').attachShadow({mode:'open'}).innerHTML='<custom-shell><button>Shadow video</button></custom-shell>'</script>''')
        observation = await browser.observe(tab, screenshot=True)
        names = {e['name'] for e in observation['elements']}
        assert {'Search', 'Home', 'Shadow video'} <= names
        assert 'Clipped hidden control' not in names
        assert 'Ancestor hidden control' not in names
        assert 'Clipped hidden control' not in observation['text']
        assert 'Boxless visible text' in observation['text']
        assert observation['coverage']['status'] == 'complete'
        target = next(e for e in observation['elements'] if e['name'] == 'Shadow video')
        await browser.act(tab, {'operation': 'hover', 'target': target['id'], 'observation_id': observation['id']})
        await page.set_content('<canvas width="300" height="100"></canvas>')
        empty = await browser.observe(tab)
        assert empty['coverage']['status'] == 'partial'
        assert 'empty_rendered_dom' in empty['coverage']['reasons'] or empty['coverage']['dom_elements'] > 0


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
