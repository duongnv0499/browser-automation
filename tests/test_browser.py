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
    BrowserSession, ConsentRequiredError, StaleObservationError, UnsafeActionError,
)


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
            request = await reader.readuntil(b'\r\n\r\n')
            route = request.split(b' ')[1].decode().split('?')[0]
            headers = ''
            if route == '/frame':
                body = b'<html><body><button onclick="this.textContent=\'Frame clicked\'">Frame button</button></body></html>'
            elif route == '/popup':
                body = b'<html><head><title>Owned popup</title></head><body>Popup opened</body></html>'
            elif route == '/download':
                body = b'real downloadable fixture\n'
                headers = 'Content-Disposition: attachment; filename="fixture.txt"\r\n'
            else:
                body = main.replace('PORT', str(port)).encode()
            writer.write(f'HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {len(body)}\r\nConnection: close\r\n{headers}\r\n'.encode() + body)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handle, '0.0.0.0', 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield f'http://127.0.0.1:{port}/'
    finally:
        server.close()
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
    with pytest.raises(UnsafeActionError, match='covered'):
        await session.act(tab, {'operation':'click','target':target,'observation_id':observation['id']})
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
    grid = next(e for e in proof['elements'] if e['role']=='visual-region')
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
