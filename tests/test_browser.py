"""Real Chromium tests. No site, provider key, or canned browser mock is used."""
import asyncio
import base64
import json
import os
import time
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import async_playwright

from browser_automation.browser import (
    BrowserError, BrowserSession, ConsentRequiredError, NotOwnedTabError, StaleObservationError, UnsafeActionError,
)


NAV_FIXTURE = b'''<!doctype html><html><head><title>Navigation fixture</title><style>body{font:18px sans-serif;margin:20px}a,button{display:block;margin:8px 0;padding:8px}</style></head><body>
<h1 id="state">Navigation start</h1>
<a href="/popup">Go to popup page</a>
<a href="#section">Jump to section</a>
<button onclick="history.pushState({},'','/nav/pushed');document.querySelector('#state').textContent='Pushed state'">Push state</button>
<button onclick="document.querySelector('#state').textContent='Stayed here'">Stay here</button>
<a href="/slow">Slow page</a>
<div id="section">Section anchor</div>
</body></html>'''


OVERLAY_FIXTURE = b'''<!doctype html><html><head><title>Overlay fixture</title><style>body{font:18px sans-serif;margin:10px}button{display:block;margin:0 0 60px 0;padding:8px}</style></head><body>
<button onclick="document.querySelector('#out').textContent='Under clicked'">Covered action</button>
<button onclick="document.querySelector('#out').textContent='Free clicked'">Free action</button>
<div id="out" role="status">Idle</div>
<p id="para" style="width:300px;font:18px monospace;margin:0">aaaaaaaaaaaaaaaaaa <a id="wrapped" href="#wrapped" onclick="document.querySelector('#out').textContent='Wrapped clicked'">bbbbbbb cccccccc</a> dddddddddddddddddd</p>
<div id="shade" style="position:absolute;left:0;top:0;width:400px;height:70px;background:rgba(0,0,0,.45)"></div>
</body></html>'''


# Rendered text changes every animation frame: until ~250 ms after script start, or forever.
SETTLING_FIXTURE = b'''<!doctype html><html><head><title>Settling fixture</title></head><body style="font:18px sans-serif">
<h1>Settling page</h1><p id="late">Loading</p><button>Stable control</button>
<script>const forever=location.search.includes('forever');const start=performance.now();let n=0;
function tick(){const done=!forever&&performance.now()-start>250;document.querySelector('#late').textContent=done?'Settled':'Frame '+(++n);if(!done)requestAnimationFrame(tick);}
requestAnimationFrame(tick);</script>
</body></html>'''
SMOOTH_FIXTURE = b'''<!doctype html><html style="scroll-behavior:smooth"><head><title>Smooth fixture</title></head><body style="margin:0;font:18px sans-serif">
<h1>Smooth top</h1><div style="height:4000px">Tall spacer</div><p>Bottom content</p><a href="/popup">Bottom link</a>
<div aria-label="Inner box" style="position:fixed;right:10px;top:10px;width:220px;height:120px;overflow:auto;scroll-behavior:smooth;background:#eee"><div style="height:1000px">Inner content</div></div>
</body></html>'''
POLICY_FIXTURE = b'''<!doctype html><html><head><title>Policy fixture</title><style>body{font:18px sans-serif;margin:10px}button,input{margin:5px;padding:6px}</style></head><body>
<h1 id="count">Count 0</h1>
<button onclick="n++;document.querySelector('#count').textContent='Count '+n">Add</button>
<button onclick="document.querySelector('#status').textContent='Deleted'">Delete</button>
<div id="status" role="status">Kept</div>
<form method="get" action="/policy"><input name="q" aria-label="Search query"></form>
<label>Password <input type="password" aria-label="Password"></label>
<script>let n=0</script>
</body></html>'''
LONG_FIXTURE = b'''<!doctype html><html><head><title>Long fixture</title></head><body style="font:18px sans-serif;margin:10px">
<h1>Top heading</h1><button aria-expanded="false" onclick="this.setAttribute('aria-expanded','true')">Menu toggle</button>
<div style="height:3000px">Spacer</div>
<p>Deep footer sentence beyond the viewport.</p><p style="opacity:0">Transparent hidden sentence</p><p style="display:none">Display none sentence</p>
<button>Bottom button</button>
</body></html>'''
MANY_FIXTURE = ('<!doctype html><html><head><title>Many fixture</title><style>body{font:11px sans-serif;margin:4px}a{margin:1px}</style></head><body>'
                + ''.join(f'<a href="/item/{n}">Item {n}</a>' for n in range(150))
                + '<label>Pick <select><option value="x">X</option><option value="y" selected>Y</option></select></label><input type="password" aria-label="Secret" value="hidden-value"></body></html>').encode()


@pytest_asyncio.fixture
async def site():
    port = 0
    main = '''<!doctype html><html><head><title>Browser acceptance fixture</title>
<style>body{font:18px sans-serif;margin:20px}button,input,select{margin:5px;padding:8px}iframe{width:360px;height:120px}#scroller{height:70px;width:230px;overflow:auto;border:1px solid}canvas{border:1px solid;display:block}#drop{display:inline-block;padding:20px;border:1px solid}</style></head><body>
<h1>Real browser fixture</h1><label>Name <input id="name" aria-label="Name"></label>
<input type="password" aria-label="Password" value="never-export-this">
<select aria-label="Plan"><option value="free">Free</option><option value="paid">Paid</option></select>
<button id="safe" onclick="document.querySelector('#out').textContent='Clicked safely'">Safe click</button>
<button id="popup" onclick="window.open('/popup','_blank')">Open popup</button>
<a href="/download" download="fixture.txt">Download fixture</a><input type="file" aria-label="Upload fixture">
<div id="out" role="status">Waiting</div><div id="shadow"></div>
<iframe src="http://localhost:PORT/frame"></iframe>
<div id="scroller" aria-label="Scroll container"><div style="height:500px">Scrollable content</div></div>
<div draggable="true" aria-label="Drag source" id="source" ondragstart="event.dataTransfer.setData('text/plain','payload')">Drag source</div>
<div id="drop" role="button" aria-label="Drop target" tabindex="0" ondragover="event.preventDefault()" ondrop="event.preventDefault();this.textContent='Dropped'">Drop target</div>
<canvas id="canvas" width="160" height="80" aria-label="Canvas" onclick="document.querySelector('#out').textContent='Canvas clicked'"></canvas>
<p>VISIBLE LONG TEXT CONTENT FOR CONTINUATION. This paragraph contains sufficient visible text to demonstrate explicit continuation without silent truncation.</p>
<script>const root=document.querySelector('#shadow').attachShadow({mode:'open'});const btn=document.createElement('button');btn.textContent='Shadow button';btn.onclick=()=>btn.textContent='Shadow clicked';root.appendChild(btn);const ctx=document.querySelector('canvas').getContext('2d');ctx.fillStyle='#087f5b';ctx.fillRect(0,0,160,80);ctx.fillStyle='white';ctx.font='18px sans-serif';ctx.fillText('Canvas target',12,45);</script>
</body></html>'''

    async def handle(reader, writer):
        try:
            request = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 30)
            route = request.split(b' ')[1].decode().split('?')[0]
            length = next((int(line.split(b':', 1)[1]) for line in request.split(b'\r\n') if line.lower().startswith(b'content-length:')), 0)
            payload = await reader.readexactly(length) if length else b''
            headers = ''
            if route == '/api':
                body = json.dumps({'method': request.split(b' ')[0].decode(), 'received': payload.decode()}).encode()
            elif route == '/smooth':
                body = SMOOTH_FIXTURE
            elif route == '/settling':
                body = SETTLING_FIXTURE
            elif route == '/policy':
                body = POLICY_FIXTURE
            elif route == '/frame':
                body = b'<html><body><button onclick="this.textContent=\'Frame clicked\'">Frame button</button></body></html>'
            elif route == '/popup':
                body = b'<html><head><title>Owned popup</title></head><body>Popup opened</body></html>'
            elif route == '/long':
                body = LONG_FIXTURE
            elif route == '/many':
                body = MANY_FIXTURE
            elif route == '/overlay':
                body = OVERLAY_FIXTURE
            elif route == '/nav':
                body = NAV_FIXTURE
            elif route == '/slow':
                await asyncio.sleep(2)
                body = b'<html><head><title>Slow page</title></head><body>Slow page loaded</body></html>'
            elif route == '/download':
                body = b'real downloadable fixture\n'
                headers = 'Content-Disposition: attachment; filename="fixture.txt"\r\n'
            else:
                body = main.replace('PORT', str(port)).encode()
            writer.write(f'HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {len(body)}\r\nConnection: close\r\n{headers}\r\n'.encode() + body)
            await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError, asyncio.TimeoutError):
            pass  # The browser may abandon a slow response or hold an idle preconnect socket.
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass

    server = await asyncio.start_server(handle, '0.0.0.0', 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield f'http://127.0.0.1:{port}/'
    finally:
        server.close()
        if hasattr(server, 'close_clients'):
            # An attached external Chrome may outlive this fixture with idle preconnect
            # sockets; Python 3.12+ wait_closed() would otherwise wait for them forever.
            server.close_clients()
        await server.wait_closed()


@pytest_asyncio.fixture
async def browser(site):
    session = await BrowserSession.launch(headless=True)
    tab = await session.new_tab(site)
    try:
        yield session, tab['id']
    finally:
        await session.close()


def element(observation, name):
    return next(e for e in observation['elements'] if e['name'] == name)


async def act(session, tab, name, operation='click', **kwargs):
    observation = await session.observe(tab)
    target = element(observation, name)
    return await session.act(tab, {'observation_id':observation['id'], 'target':target['id'], 'operation':operation, **kwargs})


@pytest.mark.asyncio
async def test_input_select_shadow_frame_popup(browser):
    session, tab = browser
    assert (await session.tab_url(tab)).startswith('http://127.0.0.1:')
    first = await session.observe(tab)
    second = await session.observe(tab)
    assert element(first, 'Name')['id'] == element(second, 'Name')['id']
    assert element(first, 'Password')['value'] == '[REDACTED]'
    assert 'never-export-this' not in json.dumps(first)
    await act(session, tab, 'Name', 'fill', text='Visible browser input')
    assert element(await session.observe(tab), 'Name')['value'] == 'Visible browser input'
    await act(session, tab, 'Plan', 'select', value='paid')
    assert element(await session.observe(tab), 'Plan')['value'] == 'paid'
    await act(session, tab, 'Shadow button')
    assert 'Shadow clicked' in (await session.observe(tab))['text']
    frame = element(await session.observe(tab), 'Frame button')
    assert frame['frame']['index'] != 0
    assert 'localhost' in frame['frame']['url']
    await act(session, tab, 'Frame button')
    assert 'Frame clicked' in (await session.observe(tab))['text']
    await act(session, tab, 'Open popup')
    for _ in range(50):
        tabs = await session.tabs()
        if len(tabs) > 1:
            break
        await asyncio.sleep(.02)
    assert len(tabs) == 2
    assert any(t['url'].endswith('/popup') for t in tabs)


@pytest.mark.asyncio
async def test_stale_covered_wrong_tab_and_semantic_guards(browser):
    session, tab = browser
    page = session._page(tab)  # Fixture-side changes only, never an external tool API.
    observation = await session.observe(tab)
    target = element(observation,'Safe click')['id']
    await page.evaluate("document.querySelector('#safe').textContent='Changed meaning'")
    with pytest.raises(StaleObservationError):
        await session.act(tab, {'operation':'click','target':target,'observation_id':observation['id']})
    await page.reload()
    observation = await session.observe(tab)
    target = element(observation,'Safe click')['id']
    await page.evaluate("const e=document.createElement('div');e.style='position:fixed;inset:0;background:rgba(0,0,0,.2);z-index:999999';document.body.append(e)")
    # A new overlay changes advertised operations, so the old revision is stale.
    with pytest.raises(StaleObservationError):
        await session.act(tab, {'operation':'click','target':target,'observation_id':observation['id']})
    observation = await session.observe(tab)
    covered = element(observation,'Safe click')
    assert covered['covered'] is True and covered['operations'] == []
    with pytest.raises(UnsafeActionError, match='unsupported'):
        await session.act(tab, {'operation':'click','target':covered['id'],'observation_id':observation['id']})
    await page.reload()
    # The guard stays authoritative at the actual input point: a corner overlay leaves
    # the default center actionable, but an explicit covered point is still rejected.
    await page.evaluate("const r=document.querySelector('#safe').getBoundingClientRect();const e=document.createElement('div');e.style=`position:fixed;left:${r.left}px;top:${r.top}px;width:6px;height:6px;z-index:999999`;document.body.append(e)")
    observation = await session.observe(tab)
    safe = element(observation,'Safe click')
    assert 'covered' not in safe and 'click' in safe['operations']
    with pytest.raises(UnsafeActionError, match='covered'):
        await session.act(tab, {'operation':'click','target':safe['id'],'observation_id':observation['id'],'x':safe['bounds']['x']+2,'y':safe['bounds']['y']+2})
    assert await page.locator('#out').inner_text() == 'Waiting'
    await page.reload()
    observation = await session.observe(tab)
    target = element(observation,'Safe click')['id']
    await page.evaluate("document.querySelector('#safe').remove()")
    with pytest.raises(StaleObservationError):
        await session.act(tab, {'operation':'click','target':target,'observation_id':observation['id']})
    other = await session.new_tab()
    with pytest.raises(UnsafeActionError, match='another tab'):
        await session.act(other['id'], {'operation':'wait','observation_id':observation['id']})
    await page.reload()
    with pytest.raises(StaleObservationError):
        await session.act(tab, {'operation':'wait','observation_id':observation['id']})
    observation = await session.observe(tab)
    await session.act(tab, {'operation':'wait','seconds':0,'observation_id':observation['id']})
    with pytest.raises(StaleObservationError):
        await session.act(tab, {'operation':'wait','observation_id':observation['id']})


@pytest.mark.asyncio
async def test_continuation_scroll_keyboard_hover_drag_history(browser, site):
    session, tab = browser
    page = session._page(tab)
    observation = await session.observe(tab,max_text=12)
    assert observation['truncated'] and observation['next_offset'] == 12
    continuation = await session.text_continuation(tab,observation['id'],12,1_000_000)
    full = await session.text_continuation(tab,observation['id'],0,1_000_000)
    assert observation['text']+continuation['text'] == full['text']
    await act(session,tab,'Name','fill',text='typing')
    await act(session,tab,'Name','press',key='End')
    await act(session,tab,'Safe click','hover')
    assert await page.locator('#safe').evaluate('(e)=>e.matches(":hover")')
    observation = await session.observe(tab)
    scroller = next(e for e in observation['elements'] if 'scroll' in e['operations'] and e['role'] == 'div')
    await session.act(tab,{'operation':'scroll','observation_id':observation['id'],'target':scroller['id'],'delta_y':200})
    await page.wait_for_function("document.querySelector('#scroller').scrollTop>0")
    observation = await session.observe(tab)
    await session.act(tab, {'operation':'drag','observation_id':observation['id'],'target':element(observation,'Drag source')['id'],'to_target':element(observation,'Drop target')['id']})
    assert await page.locator('#drop').inner_text() == 'Dropped'
    await page.goto(site+'popup')
    observation = await session.observe(tab)
    await session.act(tab, {'operation':'back','observation_id':observation['id']})
    assert (await session.observe(tab))['url'] == site
    observation = await session.observe(tab)
    await session.act(tab, {'operation':'forward','observation_id':observation['id']})
    assert (await session.observe(tab))['url'].endswith('/popup')


@pytest.mark.asyncio
async def test_screenshot_canvas_refined_visual_and_proof(browser, tmp_path):
    session, tab = browser
    page = session._page(tab)
    await act(session, tab, 'Name', 'fill', text='Rendered DOM proof')
    started = time.perf_counter()
    observation = await session.observe(tab,screenshot=True)
    observe_ms = (time.perf_counter()-started)*1000
    assert base64.b64decode(observation['screenshot']).startswith(b'\x89PNG')
    canvas = element(observation,'Canvas')
    x,y = canvas['bounds']['x']+30,canvas['bounds']['y']+35
    grid = next(e for e in observation['elements'] if e['role']=='visual-region' and e['bounds']['x']<=x<e['bounds']['x']+e['bounds']['width'] and e['bounds']['y']<=y<e['bounds']['y']+e['bounds']['height'])
    result = await session.act(tab, {'operation':'click','observation_id':observation['id'],'target':grid['id'],'x':x,'y':y})
    proof = await session.observe(tab,screenshot=True)
    assert 'Canvas clicked' in proof['text']
    assert element(proof,'Name')['value'] == 'Rendered DOM proof'
    destination = Path(os.environ.get('BROWSER_PROOF_DIR',tmp_path))
    destination.mkdir(parents=True,exist_ok=True)
    (destination/'browser-proof.png').write_bytes(base64.b64decode(proof['screenshot']))
    (destination/'browser-proof.json').write_text(json.dumps({'kind':'real_chromium_headless_browser_only','text':proof['text'],'name':element(proof,'Name')['value'],'canvas':'Canvas clicked','observe_screenshot_ms':observe_ms,'click_visual_ms':result['latency_ms'],'live_provider':False},indent=2))
    await page.evaluate("document.querySelector('canvas').getContext('2d').fillRect(0,0,160,80)")
    grid = next(e for e in proof['elements'] if e['role']=='visual-region' and e['bounds']['x']<=x<e['bounds']['x']+e['bounds']['width'] and e['bounds']['y']<=y<e['bounds']['y']+e['bounds']['height'])
    with pytest.raises(StaleObservationError):
        await session.act(tab, {'operation':'click','observation_id':proof['id'],'target':grid['id']})


@pytest.mark.asyncio
async def test_upload_download_scopes_and_approval(browser,tmp_path):
    session,tab = browser
    source = tmp_path/'upload.txt'
    source.write_text('approved file content')
    observation = await session.observe(tab)
    target = element(observation,'Upload fixture')['id']
    with pytest.raises(ConsentRequiredError):
        await session.upload(tab,observation['id'],target,[str(source)],allowed_directory=str(tmp_path))
    with pytest.raises(UnsafeActionError):
        await session.upload(tab,observation['id'],target,['/etc/passwd'],allowed_directory=str(tmp_path),approved=True)
    await session.upload(tab,observation['id'],target,[str(source)],allowed_directory=str(tmp_path),approved=True)
    assert await session._page(tab).locator('input[type=file]').evaluate('(e)=>e.files[0].name') == 'upload.txt'
    observation = await session.observe(tab)
    click = {'operation':'click','target':element(observation,'Download fixture')['id'],'observation_id':observation['id']}
    destination = tmp_path/'download.txt'
    await session.download(tab,click,str(destination),allowed_directory=str(tmp_path),approved=True)
    assert destination.read_text() == 'real downloadable fixture\n'


@pytest.mark.asyncio
async def test_navigate_owned_tab_renders_and_invalidates(browser, site, tmp_path):
    session, tab = browser
    page = session._page(tab)
    before = await session.observe(tab, screenshot=True)
    result = await session.navigate(tab, site + 'nav')
    assert result == {'tab': {'id': tab, 'url': site + 'nav', 'title': 'Navigation fixture'}, 'navigation_status': 'complete', 'wait_until': 'domcontentloaded'}
    with pytest.raises(StaleObservationError):
        await session.act(tab, {'operation': 'wait', 'seconds': 0, 'observation_id': before['id']})
    after = await session.observe(tab, screenshot=True)
    assert after['url'] == site + 'nav' and 'Navigation start' in after['text'] and 'Real browser fixture' not in after['text']
    assert await page.locator('#state').inner_text() == 'Navigation start'
    png = base64.b64decode(after['screenshot'])
    assert png.startswith(b'\x89PNG') and after['screenshot'] != before['screenshot']
    destination = Path(os.environ.get('BROWSER_PROOF_DIR', tmp_path))
    destination.mkdir(parents=True, exist_ok=True)
    (destination / 'navigate-proof.png').write_bytes(png)
    for url in ('file:///etc/passwd', 'chrome://settings', 'javascript:alert(1)', 'data:text/html,x', 'http:///nohost'):
        with pytest.raises(UnsafeActionError):
            await session.navigate(tab, url)
    with pytest.raises(BrowserError):
        await session.navigate(tab, site, wait_until='networkidle')
    with pytest.raises(BrowserError):
        await session.navigate(tab, site, timeout_ms=0)
    assert await session.tab_url(tab) == site + 'nav'
    popup_result = await act(session, tab, 'Go to popup page')
    assert popup_result['url'] == site + 'popup'
    timed = await session.navigate(tab, site + 'slow', timeout_ms=300)
    assert timed['navigation_status'] == 'timeout' and timed['diagnostic'] == {'code': 'navigation_timeout', 'timeout_ms': 300, 'message': 'Owned tab retained; observe the partially loaded page before choosing another action'}
    assert any(t['id'] == tab for t in await session.tabs())


@pytest.mark.asyncio
async def test_act_settle_reports_navigation_without_full_timeout(browser, site):
    session, tab = browser
    await session.navigate(tab, site + 'nav')
    observation = await session.observe(tab)
    started = time.perf_counter()
    stay = await session.act(tab, {'operation': 'click', 'observation_id': observation['id'], 'target': element(observation, 'Stay here')['id']})
    elapsed = time.perf_counter() - started
    # Default 1000 ms settle window only; never the 15000 ms navigation timeout.
    assert stay['navigation'] == {'started': False, 'status': 'none', 'url': site + 'nav'}
    assert elapsed < 4, elapsed
    assert 'Stayed here' in (await session.observe(tab))['text']
    observation = await session.observe(tab)
    started = time.perf_counter()
    quick = await session.act(tab, {'operation': 'click', 'observation_id': observation['id'], 'target': element(observation, 'Stay here')['id'], 'settle_ms': 0})
    assert quick['navigation']['started'] is False and time.perf_counter() - started < 2
    observation = await session.observe(tab)
    with pytest.raises(UnsafeActionError, match='settle_ms'):
        await session.act(tab, {'operation': 'click', 'observation_id': observation['id'], 'target': element(observation, 'Stay here')['id'], 'settle_ms': 10001})
    pushed = await session.act(tab, {'operation': 'click', 'observation_id': observation['id'], 'target': element(observation, 'Push state')['id']})
    assert pushed['navigation'] == {'started': True, 'status': 'complete', 'url': site + 'nav/pushed'}
    assert pushed['latency_ms'] < 900, pushed  # same-document commit ends the settle window early
    assert 'Pushed state' in (await session.observe(tab))['text']
    await session.navigate(tab, site + 'nav')
    observation = await session.observe(tab)
    jumped = await session.act(tab, {'operation': 'click', 'observation_id': observation['id'], 'target': element(observation, 'Jump to section')['id']})
    assert jumped['navigation'] == {'started': True, 'status': 'complete', 'url': site + 'nav#section'}
    observation = await session.observe(tab)
    linked = await session.act(tab, {'operation': 'click', 'observation_id': observation['id'], 'target': element(observation, 'Go to popup page')['id']})
    assert linked['navigation'] == {'started': True, 'status': 'complete', 'url': site + 'popup'} and linked['url'] == site + 'popup'
    with pytest.raises(StaleObservationError):
        await session.act(tab, {'operation': 'wait', 'seconds': 0, 'observation_id': observation['id']})
    rendered = await session.observe(tab)
    assert rendered['url'] == site + 'popup' and 'Popup opened' in rendered['text']
    await session.navigate(tab, site + 'nav')
    observation = await session.observe(tab)
    started = time.perf_counter()
    slow = await session.act(tab, {'operation': 'click', 'observation_id': observation['id'], 'target': element(observation, 'Slow page')['id'], 'timeout_ms': 400})
    assert time.perf_counter() - started < 1.9
    assert slow['navigation']['started'] is True and slow['navigation']['status'] == 'timeout'
    assert slow['navigation']['diagnostic']['code'] == 'navigation_timeout'
    assert any(t['id'] == tab for t in await session.tabs())


@pytest.mark.asyncio
async def test_covered_targets_flagged_without_operations_and_restored(browser, site, tmp_path):
    from browser_automation.providers import action_candidates
    session, tab = browser
    page = session._page(tab)
    await session.navigate(tab, site + 'overlay')
    observation = await session.observe(tab, screenshot=True)
    under, free = element(observation, 'Covered action'), element(observation, 'Free action')
    assert under['covered'] is True and under['operations'] == []
    assert 'covered' not in free and {'click', 'hover', 'press'} <= set(free['operations'])
    targets = {action.get('target') for action in action_candidates(observation).values()}
    assert under['id'] not in targets and free['id'] in targets
    destination = Path(os.environ.get('BROWSER_PROOF_DIR', tmp_path))
    destination.mkdir(parents=True, exist_ok=True)
    (destination / 'covered-proof.png').write_bytes(base64.b64decode(observation['screenshot']))
    with pytest.raises(UnsafeActionError, match='unsupported'):
        await session.act(tab, {'operation': 'click', 'observation_id': observation['id'], 'target': under['id']})
    await session.act(tab, {'operation': 'click', 'observation_id': observation['id'], 'target': free['id'], 'settle_ms': 0})
    assert await page.locator('#out').inner_text() == 'Free clicked'
    observation = await session.observe(tab)
    await page.evaluate("document.querySelector('#shade').remove()")
    # Removing the overlay restores operations, which also makes older revisions stale.
    with pytest.raises(StaleObservationError):
        await session.check_observation(tab, observation['id'])
    with pytest.raises(StaleObservationError):
        await session.act(tab, {'operation': 'click', 'observation_id': observation['id'], 'target': element(observation, 'Free action')['id']})
    restored = await session.observe(tab, screenshot=True)
    under = element(restored, 'Covered action')
    assert 'covered' not in under and 'click' in under['operations']
    await session.check_observation(tab, restored['id'], [under['id']])
    await session.act(tab, {'operation': 'click', 'observation_id': restored['id'], 'target': under['id'], 'settle_ms': 0})
    proof = await session.observe(tab, screenshot=True)
    assert 'Under clicked' in proof['text']
    (destination / 'uncovered-proof.png').write_bytes(base64.b64decode(proof['screenshot']))


@pytest.mark.asyncio
async def test_wrapped_inline_link_uses_fragment_input_point(browser, site):
    session, tab = browser
    page = session._page(tab)
    await session.navigate(tab, site + 'overlay')
    # Precondition: the link wraps and its bounding-box center is on unrelated paragraph text.
    probe = await page.evaluate("""()=>{const a=document.querySelector('#wrapped'),r=a.getBoundingClientRect();const h=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return {fragments:a.getClientRects().length,centerOnLink:a.contains(h)};}""")
    assert probe == {'fragments': 2, 'centerOnLink': False}
    observation = await session.observe(tab)
    link = element(observation, 'bbbbbbb cccccccc')
    assert 'covered' not in link and 'click' in link['operations']
    result = await session.act(tab, {'operation': 'click', 'observation_id': observation['id'], 'target': link['id']})
    assert result['navigation']['url'].endswith('#wrapped')
    assert await page.locator('#out').inner_text() == 'Wrapped clicked'


@pytest.mark.asyncio
async def test_document_text_scope_reads_beyond_viewport_without_digest_change(browser, site):
    session, tab = browser
    await session.navigate(tab, site + 'long')
    viewport = await session.observe(tab)
    assert 'Top heading' in viewport['text'] and 'Deep footer sentence' not in viewport['text']
    document = await session.observe(tab, text_scope='document', max_text=1_000_000)
    assert 'Deep footer sentence beyond the viewport.' in document['text'] and 'Top heading' in document['text']
    assert 'Transparent hidden sentence' not in document['text'] and 'Display none sentence' not in document['text']
    names = {e['name'] for e in document['elements']}
    assert 'Menu toggle' in names and 'Bottom button' not in names  # elements stay viewport-bound
    assert element(document, 'Menu toggle')['expanded'] is False
    # Offscreen text must not make the revision stale; continuation reads the document text.
    await session.check_observation(tab, document['id'])
    small = await session.observe(tab, text_scope='document', max_text=20)
    assert small['truncated'] and small['next_offset'] == 20
    rest = await session.text_continuation(tab, small['id'], 20, 1_000_000)
    assert 'Deep footer sentence' in rest['text']
    await session.act(tab, {'operation': 'wait', 'seconds': 0, 'observation_id': small['id']})
    with pytest.raises(BrowserError):
        await session.observe(tab, text_scope='page')


@pytest.mark.asyncio
async def test_service_compact_observation_is_smaller_and_actionable(site):
    from browser_automation.service import BrowserService
    service = BrowserService()
    try:
        sid = (await service.dispatch('launch', {'headless': True}))['session_id']
        tab = (await service.dispatch('new_tab', {'session_id': sid, 'url': site + 'many'}))['tab']['id']
        args = {'session_id': sid, 'tab_id': tab}
        full = await service.dispatch('observe', {**args, 'detail': 'full'})
        compact = await service.dispatch('observe', args)
        assert {'bounds', 'frame'} <= set(full['elements'][0]) and 'safety' in full
        assert set(compact) <= {'id', 'tab_id', 'url', 'title', 'text', 'truncated', 'next_offset', 'text_length', 'coverage', 'page_state', 'visible_alerts', 'screenshot_status', 'screenshot', 'diagnostic', 'visual_summary', 'visual_interpretation_error', 'omitted_elements', 'limitations', 'elements', 'visual_regions_omitted'}
        assert not any(key in e for e in compact['elements'] for key in ('bounds', 'frame', 'signature', 'operations', 'tag', 'input_type'))
        assert len(compact['elements']) == len(full['elements']) >= 150
        full_size, compact_size = len(json.dumps(full, separators=(',', ':'))), len(json.dumps(compact, separators=(',', ':')))
        assert compact_size <= 0.35 * full_size, (compact_size, full_size)
        link = next(e for e in compact['elements'] if e['name'] == 'Item 7')
        assert link == {'id': link['id'], 'role': 'link', 'name': 'Item 7', 'ops': ['click', 'hover', 'press', 'drag'], 'href': '/item/7'}  # links are natively draggable; same-origin href is a path
        select = next(e for e in compact['elements'] if 'select' in e['ops'])
        assert select['value'] == 'y' and select['options'] == [{'value': 'x', 'label': 'X'}, {'value': 'y', 'label': 'Y', 'selected': True}]
        secret = next(e for e in compact['elements'] if e['name'] == 'Secret')
        assert secret['sensitive'] is True and 'hidden-value' not in json.dumps(compact)
        # IDs from compact output act unchanged; the cache keeps full data for approval policy.
        assert service.snapshots[(sid, compact['id'])]['elements'][0].get('bounds')
        await service.dispatch('act', {**args, 'action': {'observation_id': compact['id'], 'operation': 'select', 'target': select['id'], 'value': 'x'}})
        after = await service.dispatch('observe', {**args, 'screenshot': True})
        assert next(e for e in after['elements'] if 'select' in e['ops'])['value'] == 'x'
        assert after['screenshot_status'] == 'captured' and after['visual_regions_omitted'] == 64
        assert not any(e['role'] == 'visual-region' for e in after['elements'])
        regions = await service.dispatch('observe', {**args, 'screenshot': True, 'visual_regions': True})
        assert sum(e['role'] == 'visual-region' for e in regions['elements']) == 64 and 'visual_regions_omitted' not in regions
        document = await service.dispatch('observe', {**args, 'text_scope': 'document', 'max_text': 5})
        assert document['truncated'] and document['next_offset'] == 5
        continued = await service.dispatch('text', {'session_id': sid, 'observation_id': document['id'], 'offset': 5})
        assert 'Item 149' in continued['text']
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_service_rejects_stale_approval_and_drops_tab_cache(site, monkeypatch):
    from browser_automation.service import BrowserService, ServiceError
    monkeypatch.setenv('BROWSER_APPROVAL_MODE', 'strict')  # 'Free action' is an ordinary control: pauses only in strict.
    service = BrowserService()
    try:
        sid = (await service.dispatch('launch', {'headless': True}))['session_id']
        tab = (await service.dispatch('new_tab', {'session_id': sid, 'url': site + 'overlay'}))['tab']['id']
        args = {'session_id': sid, 'tab_id': tab}
        observation = await service.dispatch('observe', args)
        click = {'operation': 'click', 'target': element(observation, 'Free action')['id'], 'observation_id': observation['id']}
        await service.sessions[sid]._page(tab).evaluate("document.querySelector('#out').textContent='Changed elsewhere'")
        with pytest.raises(StaleObservationError):
            await service.dispatch('act', {**args, 'action': click})
        assert not service.pending_actions and (sid, observation['id']) not in service.snapshots
        observation = await service.dispatch('observe', args)
        click['observation_id'] = observation['id']
        paused = await service.dispatch('act', {**args, 'action': click})
        assert paused['status'] == 'approval_required' and set(service.pending_actions) == {(sid, tab, observation['id'])}
        executed = await service.dispatch('act', {**args, 'action': {'observation_id': observation['id'], 'operation': 'wait', 'seconds': 0}})
        assert executed['navigation']['started'] is False
        assert not service.pending_actions and not any(key[0] == sid for key in service.snapshots)
        with pytest.raises(ServiceError) as old:
            await service.dispatch('act', {**args, 'action': {'observation_id': observation['id'], 'operation': 'wait', 'seconds': 0}})
        assert old.value.code == 'unknown_observation'
        assert await service.sessions[sid]._page(tab).locator('#out').inner_text() == 'Changed elsewhere'
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_service_approval_modes_on_real_browser(site, tmp_path, monkeypatch):
    from browser_automation.service import BrowserService
    from browser_automation.approval_policy import CUSTOM_REASON, CLICK_REASON, KEY_REASON
    approvals = tmp_path / 'approvals.json'
    approvals.write_text('[]')
    files = tmp_path / 'files'
    files.mkdir()
    (files / 'upload.txt').write_text('standing approval upload')
    monkeypatch.setenv('BROWSER_APPROVALS_FILE', str(approvals))
    monkeypatch.setenv('BROWSER_FILES_DIRECTORY', str(files))
    destination = Path(os.environ.get('BROWSER_PROOF_DIR', tmp_path))
    destination.mkdir(parents=True, exist_ok=True)
    service = BrowserService()
    try:
        sid = (await service.dispatch('launch', {'headless': True}))['session_id']
        tab = (await service.dispatch('new_tab', {'session_id': sid, 'url': site + 'policy'}))['tab']['id']
        args = {'session_id': sid, 'tab_id': tab}
        page = service.sessions[sid]._page(tab)
        async def act(name, operation='click', **extra):
            observation = await service.dispatch('observe', args)
            target = next(e for e in observation['elements'] if e['name'] == name)['id']
            return await service.dispatch('act', {**args, 'action': {'observation_id': observation['id'], 'operation': operation, 'target': target, **extra}})

        monkeypatch.setenv('BROWSER_APPROVAL_MODE', 'strict')  # Legacy behaviour: ordinary controls pause.
        assert (await act('Add'))['status'] == 'approval_required'
        assert await page.locator('#count').inner_text() == 'Count 0'

        monkeypatch.setenv('BROWSER_APPROVAL_MODE', 'standard')
        added = await act('Add', settle_ms=0)
        assert added['approval'] == {'source': 'host_policy', 'mode': 'standard', 'tier': 'ordinary', 'reason': CUSTOM_REASON}
        assert await page.locator('#count').inner_text() == 'Count 1'
        paused = await act('Delete')
        assert paused['status'] == 'approval_required' and paused['tier'] == 'consequential'
        assert await page.locator('#status').inner_text() == 'Kept'
        assert (await act('Password', 'fill', text='never-typed'))['tier'] == 'critical'
        filled = await act('Search query', 'fill', text='cats')
        assert 'approval' not in filled
        searched = await act('Search query', 'press', key='Enter')
        assert searched['approval']['tier'] == 'ordinary' and searched['approval']['reason'] == KEY_REASON
        assert searched['navigation']['started'] is True and searched['url'].endswith('/policy?q=cats')
        posted = await service.dispatch('network_call', {**args, 'url': site + 'api', 'method': 'POST', 'json_body': {'ordinary': 'value'}})
        assert posted['status'] == 'approval_required' and posted['approval_tier'] == 'consequential'
        observation = await service.dispatch('observe', {**args, 'detail': 'full'})
        await service.dispatch('navigate', {**args, 'url': site})
        upload_observation = await service.dispatch('observe', args)
        upload = {**args, 'observation_id': upload_observation['id'], 'target': next(e for e in upload_observation['elements'] if e['name'] == 'Upload fixture')['id'], 'paths': [str(files / 'upload.txt')]}
        assert (await service.dispatch('upload', upload))['status'] == 'approval_required'
        logout = await service.dispatch('navigate', {**args, 'url': site + 'logout'})
        assert logout['status'] == 'approval_required' and logout['tier'] == 'consequential'

        monkeypatch.setenv('BROWSER_APPROVAL_MODE', 'autonomous')
        uploaded = await service.dispatch('upload', upload)
        assert uploaded['count'] == 1 and uploaded['approval']['source'] == 'host_policy' and uploaded['approval']['tier'] == 'consequential'
        assert await page.locator('input[type=file]').evaluate('(e)=>e.files[0].name') == 'upload.txt'
        moved = await service.dispatch('navigate', {**args, 'url': site + 'policy'})
        assert 'approval' not in moved and moved['navigation_status'] == 'complete'
        deleted = await act('Delete', settle_ms=0)
        assert deleted['approval'] == {'source': 'host_policy', 'mode': 'autonomous', 'tier': 'consequential', 'reason': CLICK_REASON}
        assert await page.locator('#status').inner_text() == 'Deleted'
        proof = await service.dispatch('observe', {**args, 'screenshot': True})
        assert 'Deleted' in proof['text']
        (destination / 'autonomous-delete-proof.png').write_bytes(base64.b64decode(proof['screenshot']))
        critical = await act('Password', 'fill', text='never-typed')
        assert critical['status'] == 'approval_required' and critical['tier'] == 'critical'
        assert await page.locator('input[type=password]').input_value() == ''
        executed = await service.dispatch('network_call', {**args, 'url': site + 'api', 'method': 'POST', 'json_body': {'ordinary': 'value'}})
        assert executed['status'] == 200 and executed['approval']['tier'] == 'consequential' and executed['approval']['source'] == 'host_policy'
        body = await service.dispatch('network_body', {**args, 'request_id': executed['request_id']})
        assert json.loads(body['data']) == {'method': 'POST', 'received': '{"ordinary":"value"}'}
        credential = await service.dispatch('network_call', {**args, 'url': site + 'api', 'headers': {'Authorization': 'Bearer private'}})
        assert credential['status'] == 'approval_required' and credential['approval_tier'] == 'critical'
        auto_logout = await service.dispatch('navigate', {**args, 'url': site + 'logout'})
        assert auto_logout['approval']['source'] == 'host_policy' and auto_logout['tab']['url'] == site + 'logout'
        assert observation['id'] not in {key[1] for key in service.snapshots}
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_observe_retries_read_only_revalidation_while_page_settles(browser, site, tmp_path):
    session, tab = browser
    await session.navigate(tab, site + 'settling')
    started = time.perf_counter()
    observation = await session.observe(tab, screenshot=True)
    assert time.perf_counter() - started < 3
    # The first screenshot revalidation sees the per-frame text change; a fresh revision is returned.
    assert observation.get('settle_retries', 0) >= 1 and 'Settled' in observation['text']
    destination = Path(os.environ.get('BROWSER_PROOF_DIR', tmp_path))
    destination.mkdir(parents=True, exist_ok=True)
    (destination / 'settled-proof.png').write_bytes(base64.b64decode(observation['screenshot']))
    stable = element(observation, 'Stable control')
    await session.act(tab, {'operation': 'hover', 'observation_id': observation['id'], 'target': stable['id']})
    # Never-settling page: bounded attempts, then the stale error with a page_settling diagnostic.
    await session.navigate(tab, site + 'settling?forever=1')
    started = time.perf_counter()
    with pytest.raises(StaleObservationError) as error:
        await session.observe(tab, screenshot=True)
    assert time.perf_counter() - started < 4
    assert error.value.diagnostic['code'] == 'page_settling' and 2 <= error.value.diagnostic['observe_attempts'] <= 3
    # act stays fail-fast: no retry after (or instead of) input.
    await session.navigate(tab, site + 'settling')
    observation = await session.observe(tab)
    await session._page(tab).evaluate("document.querySelector('h1').textContent='Changed after observe'")
    started = time.perf_counter()
    with pytest.raises(StaleObservationError):
        await session.act(tab, {'operation': 'click', 'observation_id': observation['id'], 'target': element(observation, 'Stable control')['id']})
    assert time.perf_counter() - started < 1


@pytest.mark.asyncio
async def test_wheel_scroll_waits_until_offsets_settle(browser, site, tmp_path):
    session, tab = browser
    page = session._page(tab)
    await session.navigate(tab, site + 'smooth')
    observation = await session.observe(tab)
    assert 'Bottom content' not in observation['text']
    first = await session.act(tab, {'operation': 'scroll', 'observation_id': observation['id'], 'delta_y': 6000})
    assert first['scroll']['settled'] is True and first['scroll']['moved']['y'] > 0 and first['scroll']['moved']['x'] == 0
    assert first['scroll']['moved']['y'] == await page.evaluate('scrollY')  # actual, not the requested delta
    bottom = await session.observe(tab, screenshot=True)  # immediately, no client sleep
    assert 'Bottom content' in bottom['text'] and any(e['name'] == 'Bottom link' for e in bottom['elements'])
    destination = Path(os.environ.get('BROWSER_PROOF_DIR', tmp_path))
    destination.mkdir(parents=True, exist_ok=True)
    (destination / 'scroll-settled-proof.png').write_bytes(base64.b64decode(bottom['screenshot']))
    started = time.perf_counter()
    end = await session.act(tab, {'operation': 'scroll', 'observation_id': bottom['id'], 'delta_y': 6000})
    assert end['scroll'] == {'settled': True, 'moved': {'x': 0, 'y': 0}}  # end of page
    assert time.perf_counter() - started < 1.0
    observation = await session.observe(tab)
    box = element(observation, 'Inner box')
    window_before = await page.evaluate('scrollY')
    inner = await session.act(tab, {'operation': 'scroll', 'observation_id': observation['id'], 'target': box['id'], 'delta_y': 300})
    assert inner['scroll']['settled'] is True and inner['scroll']['moved']['y'] > 0
    assert inner['scroll']['moved']['y'] == await page.evaluate("document.querySelector('[aria-label=\"Inner box\"]').scrollTop")
    assert await page.evaluate('scrollY') == window_before


@pytest_asyncio.fixture
async def external_chrome(tmp_path):
    async with async_playwright() as pw:
        executable = pw.chromium.executable_path
    env = dict(os.environ)
    lib = os.environ.get('BROWSER_AGENT_LIBRARY_PATH') or str(Path.home() / '.local/lib/chromium/usr/lib/x86_64-linux-gnu')
    if Path(lib).is_dir() and lib not in env.get('LD_LIBRARY_PATH', '').split(':'):
        env['LD_LIBRARY_PATH'] = f"{lib}:{env.get('LD_LIBRARY_PATH', '')}".rstrip(':') if env.get('LD_LIBRARY_PATH') else lib
    process = await asyncio.create_subprocess_exec(executable,'--headless=new','--no-sandbox','--remote-debugging-port=0',f'--user-data-dir={tmp_path}', 'about:blank',stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL,env=env)
    try:
        for _ in range(100):
            active = tmp_path/'DevToolsActivePort'
            if active.exists():
                break
            if process.returncode is not None:
                raise RuntimeError('External Chrome exited before CDP was available')
            await asyncio.sleep(.1)
        lines = active.read_text().splitlines()
        endpoint = f'http://127.0.0.1:{int(lines[0])}'
        yield endpoint,tmp_path
    finally:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 5)
            except asyncio.TimeoutError:
                # Some headless Chrome builds ignore SIGTERM; never hang the fixture teardown.
                process.kill()
        await process.wait()


@pytest.mark.asyncio
async def test_attached_disconnect_preserves_user_tabs(external_chrome,site):
    endpoint,profile = external_chrome
    session = await BrowserSession.connect(endpoint)
    original = (await session.tabs())[0]
    with pytest.raises(UnsafeActionError):
        await session.close_tab(original['id'])
    owned = await session.new_tab(site)
    await session.close_tab(owned['id'])
    retained = await session.new_tab(site)
    await session.close()
    again = await BrowserSession.connect(endpoint)
    try:
        tabs = await again.tabs()
        assert len(tabs) == 2
        assert any(t['url']=='about:blank' for t in tabs)
        assert any(t['url']==site for t in tabs)
    finally:
        await again.close()
    async with httpx.AsyncClient() as client:
        assert (await client.get(endpoint+'/json/version')).status_code == 200


@pytest.mark.asyncio
async def test_navigate_refuses_preexisting_user_tab(external_chrome, site):
    endpoint, profile = external_chrome
    session = await BrowserSession.connect(endpoint)
    try:
        original = (await session.tabs())[0]
        with pytest.raises(NotOwnedTabError) as refused:
            await session.navigate(original['id'], site)
        assert refused.value.code == 'not_owned_tab' and refused.value.recommended_next_action == 'new_tab'
        assert await session.tab_url(original['id']) == 'about:blank'
        owned = await session.new_tab()
        moved = await session.navigate(owned['id'], site + 'nav')
        assert moved['navigation_status'] == 'complete' and moved['tab']['title'] == 'Navigation fixture'
        tabs = {t['id']: t['url'] for t in await session.tabs()}
        assert tabs[original['id']] == 'about:blank' and tabs[owned['id']] == site + 'nav'
        await session.close_tab(owned['id'])
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_native_discovery_requires_consent_and_chrome_optin(external_chrome):
    endpoint,profile = external_chrome
    with pytest.raises(ConsentRequiredError):
        await BrowserSession.connect_default(profile)
    # This changes ONLY the throwaway fixture profile to model Chrome's manual
    # opt-in marker. Native user-profile opt-in is deliberately never automated.
    state_path = profile/'Local State'
    state = json.loads(state_path.read_text())
    state.setdefault('devtools',{}).setdefault('remote_debugging',{})['user-enabled'] = True
    state_path.write_text(json.dumps(state))
    session = await BrowserSession.connect_default(profile,consent=True)
    assert len(await session.tabs()) >= 1
    await session.close()
