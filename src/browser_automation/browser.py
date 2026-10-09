"""Owned isolated Chromium or consented CDP control, with observation-bound input."""
from __future__ import annotations

import asyncio
import base64
import json
import io
import math
import os
import platform
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
import mimetypes
import tempfile

import httpx
from playwright.async_api import async_playwright, Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError
from PIL import Image, ImageChops
from .browser_monitor import TrafficMonitor
from .page_state import describe_dom
from .browser_files import ScopedFiles, FilePolicyError, MAX_FILE_BYTES, MAX_UPLOAD_BYTES, MAX_UPLOAD_FILES



class BrowserError(RuntimeError):
    """An explicit browser operation failure."""
    code = "browser_error"


class StaleObservationError(BrowserError):
    code = "stale_observation"


class UnsafeActionError(BrowserError):
    code = "unsafe_action"


class ProtectedUrlError(UnsafeActionError):
    """A document outside the browser automation navigation policy."""


class ConsentRequiredError(BrowserError):
    code = "consent_required"


@dataclass
class _Target:
    frame: Any
    document: str
    node: str | None
    operations: list[str]
    bounds: dict[str, float]
    visual: bool = False
    signature: str | None = None
    choices: tuple[str, ...] = ()
    multiple: bool = False
    navigation: tuple[str, ...] = ()
@dataclass
class _FrameRef:
    frame_id: str
    url: str
    page: Any
    session: Any
    parent_frame: Any = None



# Fixed implementation code only. The caller never supplies selectors or JS.
_GUARD = """({token,node,point,signature,clip}) => {
 const s=globalThis.__browserAutomationSnapshot_v1;
 if(!s || s.document!==document || s.token!==token) return {error:'wrong document'};
 const e=node?s.nodes.get(node):null;
 if(node && (!e || !e.isConnected)) return {error:'detached target'};
 if(e && signature!==s.fingerprint(e)) return {error:'target semantics changed'};
 const r=e?e.getBoundingClientRect():null;
 let left=e?Math.max(clip.left,r.left):clip.left,top=e?Math.max(clip.top,r.top):clip.top,right=e?Math.min(clip.right,r.right):clip.right,bottom=e?Math.min(clip.bottom,r.bottom):clip.bottom;
 for(let p=e?.parentElement||e?.getRootNode().host;p;p=p.parentElement||p.getRootNode().host){const style=getComputedStyle(p),pr=p.getBoundingClientRect();if(style.display==='none'||Number(style.opacity)===0)return {error:'disabled or hidden target'};const clips=s.clipsOverflow(p);if(clips&&/hidden|clip|auto|scroll/.test(style.overflowX)){left=Math.max(left,pr.left+p.clientLeft);right=Math.min(right,pr.left+p.clientLeft+p.clientWidth);}if(clips&&/hidden|clip|auto|scroll/.test(style.overflowY)){top=Math.max(top,pr.top+p.clientTop);bottom=Math.min(bottom,pr.top+p.clientTop+p.clientHeight);}}
 const x=point?point.x:(left+right)/2, y=point?point.y:(top+bottom)/2;
 if(right<=left||bottom<=top||x<left||x>=right||y<top||y>=bottom) return {error:'target clipped outside viewport'};
 if(!Number.isFinite(x)||!Number.isFinite(y)||x<0||y<0||x>=innerWidth||y>=innerHeight) return {error:'target outside viewport'};
 if(e && (x<r.left||x>r.right||y<r.top||y>r.bottom)) return {error:'point outside target'};
 let hit=document.elementFromPoint(x,y);
 while(hit?.shadowRoot){const inner=hit.shadowRoot.elementFromPoint(x,y);if(!inner||inner===hit)break;hit=inner;}
 const contains=(parent,child)=>{while(child){if(child===parent)return true;child=child.parentNode||child.host;}return false;};
 if(e && !contains(e,hit)) return {error:'covered target'};
 if(e && (e.disabled||e.closest('[inert]')||getComputedStyle(e).visibility==='hidden'||Number(getComputedStyle(e).opacity)===0)) return {error:'disabled or hidden target'};
 return {x,y,bounds:r?{x:r.x,y:r.y,width:r.width,height:r.height}:null,navigation:hit?.closest('a[href]')?.href||null};
}"""


class BrowserSession:
    """Sessions serialize operations. Attached sessions never own existing tabs."""

    def __init__(self, playwright: Any, browser: Any, *, attached: bool):
        self._playwright = playwright
        self._browser = browser
        self._attached = attached
        self._owned: set[Any] = set()
        self._pages: dict[str, Any] = {}
        self._ids: dict[Any, str] = {}
        self._snapshots: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._closed = False
        self._script = Path(__file__).with_name('snapshot.js').read_text()
        self._snapshot_limit = 8
        self._default_context = browser.contexts[0] if browser.contexts else None
        self._page_cdp: dict[Any, Any] = {}
        self._oop_cdp: dict[Any, Any] = {}
        self._local_frames: set[Any] = set()
        self._world_name = 'browser-automation-' + uuid.uuid4().hex
        self._monitor = TrafficMonitor()
        self._page_listeners: dict[Any, list[tuple[str, Any]]] = {}

    @staticmethod
    def _allowed_url(url: str, *, subframe: bool = False) -> bool:
        if url == 'about:blank' or subframe and url == 'about:srcdoc':
            return True
        try:
            parsed = urlsplit(url)
            return parsed.scheme in {'http','https'} and bool(parsed.hostname)
        except (ValueError, TypeError):
            return False

    def _ensure_safe(self, page: Any) -> None:
        if not self._allowed_url(page.url):
            raise ProtectedUrlError('Protected URL scheme: only HTTP(S), about:blank, and inherited srcdoc frames can be controlled or observed')

    async def _frames(self, page: Any) -> list[_FrameRef]:
        """Use native CDP IDs, never URL matching or private Playwright IDs."""
        self._ensure_safe(page)
        if page not in self._page_cdp:
            self._page_cdp[page] = await page.context.new_cdp_session(page)
        main = self._page_cdp[page]
        trees = [(main, (await main.send('Page.getFrameTree'))['frameTree'])]
        live = set(page.frames)
        for frame, session in list(self._oop_cdp.items()):
            if frame.page == page and frame not in live:
                self._oop_cdp.pop(frame)
                try:
                    await session.detach()
                except PlaywrightError:
                    pass
        for frame in page.frames:
            if frame == page.main_frame or frame in self._local_frames:
                continue
            session = self._oop_cdp.get(frame)
            if session is not None:
                try:
                    tree = (await session.send('Page.getFrameTree'))['frameTree']
                except PlaywrightError:
                    self._oop_cdp.pop(frame)
                    session = None
            if session is None:
                try:
                    session = await page.context.new_cdp_session(frame)
                except PlaywrightError as exc:
                    if 'does not have a separate CDP session' not in str(exc):
                        raise
                    self._local_frames.add(frame)
                    continue
                self._oop_cdp[frame] = session
                tree = (await session.send('Page.getFrameTree'))['frameTree']
            trees.append((session,tree))
        nodes: dict[str, tuple[dict[str,Any],Any,str|None]] = {}
        def collect(tree: dict[str, Any], session: Any, parent: str | None = None) -> None:
            data = tree['frame']
            previous = nodes.get(data['id'])
            parent_id = data.get('parentId') or parent or (previous[2] if previous else None)
            nodes[data['id']] = (data,session,parent_id)
            for child in tree.get('childFrames',[]):
                collect(child,session,data['id'])
        for session,tree in trees:
            collect(tree,session)
        refs = {key:_FrameRef(key,data['url'],page,session) for key,(data,session,parent) in nodes.items()}
        for key,(data,session,parent) in nodes.items():
            if parent is not None:
                if parent not in refs:
                    raise StaleObservationError('Native frame topology changed during collection')
                refs[key].parent_frame = refs[parent]
        root = trees[0][1]['frame']['id']
        return [refs[root]] + [ref for key,ref in refs.items() if key != root]

    async def _world(self, frame: _FrameRef) -> int:
        result = await frame.session.send('Page.createIsolatedWorld', {'frameId':frame.frame_id,'worldName':self._world_name})
        return result['executionContextId']

    @staticmethod
    def _runtime_value(result: dict[str, Any]) -> Any:
        if result.get('exceptionDetails'):
            desc = result['exceptionDetails'].get('exception', {}).get('description') or result['exceptionDetails'].get('text', 'unknown error')
            raise BrowserError('Trusted isolated browser script failed: ' + desc)
        value = result.get('result',{}).get('value')
        if isinstance(value,dict) and value.get('__protected_url'):
            raise ProtectedUrlError('Protected document cannot be evaluated or disclosed')
        return value

    async def _eval(self, frame: _FrameRef, script: str, argument: Any = None) -> Any:
        context = await self._world(frame)
        result = await frame.session.send('Runtime.callFunctionOn', {'executionContextId':context,'functionDeclaration':f'function(arg){{let proto="";try{{proto=new URL(location.href).protocol;}}catch(e){{}}const url=location.href||"";if(!(url==="about:blank"||url==="about:srcdoc"||proto==="http:"||proto==="https:"))return {{__protected_url:true}};const fn=({script});return typeof fn==="function"?fn(arg):fn;}}','arguments':[{'value':argument}],'returnByValue':True})
        return self._runtime_value(result)

    async def _eval_owner(self, frame: _FrameRef, script: str, argument: Any = None) -> Any:
        parent = frame.parent_frame
        if parent is None:
            raise BrowserError('Main frame has no iframe owner')
        if not self._allowed_url(parent.url, subframe=parent.parent_frame is not None):
            raise ProtectedUrlError('Protected ancestor frame cannot be evaluated')
        context = await self._world(parent)
        owner = await parent.session.send('DOM.getFrameOwner',{'frameId':frame.frame_id})
        remote = await parent.session.send('DOM.resolveNode',{'backendNodeId':owner['backendNodeId'],'executionContextId':context})
        object_id = remote['object']['objectId']
        try:
            result = await parent.session.send('Runtime.callFunctionOn',{'objectId':object_id,'functionDeclaration':f'function(arg){{const url=location.href||"";let proto="";try{{proto=new URL(url).protocol;}}catch(e){{}}if(!(url==="about:blank"||url==="about:srcdoc"||proto==="http:"||proto==="https:"))return {{__protected_url:true}};return ({script})(this,arg);}}','arguments':[{'value':argument}],'returnByValue':True})
            return self._runtime_value(result)
        finally:
            try:
                await parent.session.send('Runtime.releaseObject',{'objectId':object_id})
            except PlaywrightError:
                pass


    @classmethod
    async def connect(cls, endpoint: str, *, timeout: float = 30000) -> BrowserSession:
        if not isinstance(endpoint, str) or not endpoint.startswith(('http://', 'https://', 'ws://', 'wss://')):
            raise BrowserError('CDP endpoint must be an explicit HTTP(S) or WS(S) URL')
        pw = await async_playwright().start()
        try:
            browser = await pw.chromium.connect_over_cdp(endpoint, timeout=timeout)
            return cls(pw, browser, attached=True)
        except BaseException:
            await pw.stop()
            raise

    @classmethod
    async def connect_default(cls, profile_dir: str | Path | None = None, *, consent: bool = False, timeout: float = 120000) -> BrowserSession:
        """Discover Chrome's opted-in running profile; never enable or restart it.

        Consent authorizes a CDP connection, not automatic approval of Chrome's
        own per-connection permission dialog. The user must accept that dialog.
        """
        if not consent:
            raise ConsentRequiredError('Native Chrome requires consent=True and Chrome remote-debugging opt-in')
        if profile_dir is not None:
            candidates = [Path(profile_dir).expanduser()]
        elif platform.system() == 'Darwin':
            candidates = [Path.home() / 'Library/Application Support/Google/Chrome']
        elif platform.system() == 'Windows':
            candidates = [Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local'))) / 'Google/Chrome/User Data']
        else:
            candidates = [Path.home() / '.config/google-chrome', Path.home() / '.config/chromium', Path.home() / '.var/app/com.google.Chrome/config/google-chrome']
        failures = []
        async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
            for directory in candidates:
                try:
                    state = json.loads((directory / 'Local State').read_text())
                    if state.get('devtools', {}).get('remote_debugging', {}).get('user-enabled') is not True:
                        failures.append(f'{directory}: Chrome opt-in disabled or absent')
                        continue
                    lines = (directory / 'DevToolsActivePort').read_text().splitlines()
                    port = int(lines[0])
                    if not 0 < port < 65536:
                        raise ValueError('invalid port')
                    response = await client.get(f'http://127.0.0.1:{port}/json/version')
                    if response.status_code == 403:
                        raise ConsentRequiredError('Accept Chrome remote debugging Allow dialog, then retry')
                    if response.status_code == 404 and len(lines) > 1 and lines[1].startswith('/devtools/browser/'):
                        endpoint = f'ws://127.0.0.1:{port}{lines[1]}'
                    else:
                        response.raise_for_status()
                        endpoint = response.json()['webSocketDebuggerUrl']
                    return await cls.connect(endpoint, timeout=timeout)
                except ConsentRequiredError:
                    raise
                except (OSError, ValueError, IndexError, KeyError, httpx.HTTPError) as exc:
                    failures.append(f'{directory}: {type(exc).__name__}')
        raise BrowserError('No opted-in running Chrome found. Open chrome://inspect/#remote-debugging and enable Allow remote debugging for this browser instance. No browser was launched or restarted. ' + '; '.join(failures))

    @classmethod
    async def launch(cls, headless: bool = False, executable_path: str | None = None) -> BrowserSession:
        pw = await async_playwright().start()
        try:
            env = dict(os.environ)
            extra_lib = os.environ.get('BROWSER_AGENT_LIBRARY_PATH')
            if not extra_lib:
                detected = Path.home() / '.local/lib/chromium/usr/lib/x86_64-linux-gnu'
                if detected.is_dir():
                    extra_lib = str(detected)
            if extra_lib:
                current = env.get('LD_LIBRARY_PATH', '')
                if extra_lib not in current.split(':'):
                    env['LD_LIBRARY_PATH'] = f"{extra_lib}:{current}".rstrip(':') if current else extra_lib
            browser = await pw.chromium.launch(headless=headless, executable_path=executable_path, env=env)
            session = cls(pw, browser, attached=False)
            session._default_context = await browser.new_context(accept_downloads=True)
            return session
        except BaseException:
            await pw.stop()
            raise

    def _sync_pages(self) -> None:
        if self._closed:
            raise BrowserError('Session is closed')
        for context in self._browser.contexts:
            for page in context.pages:
                if page not in self._ids:
                    tab = uuid.uuid4().hex
                    self._ids[page] = tab
                    self._pages[tab] = page
                    listeners = [('popup', lambda popup, opener=page: self._owned.add(popup) if opener in self._owned else None),
                                 ('framenavigated', lambda frame: self._local_frames.discard(frame))]
                    self._page_listeners[page] = listeners
                    for event, callback in listeners:
                        page.on(event, callback)
        for tab, page in list(self._pages.items()):
            if page.is_closed():
                self._pages.pop(tab)
                self._ids.pop(page, None)
                self._owned.discard(page)
                for event, callback in self._page_listeners.pop(page, []):
                    page.remove_listener(event, callback)
                self._invalidate(tab)
                self._page_cdp.pop(page, None)
                self._local_frames.difference_update({frame for frame in self._local_frames if frame.page == page})

    def _page(self, tab_id: str, *, allow_protected: bool = False) -> Any:
        self._sync_pages()
        try:
            page = self._pages[tab_id]
        except KeyError:
            raise BrowserError('Unknown or closed tab') from None
        if not allow_protected:
            self._ensure_safe(page)
        return page

    async def tabs(self) -> list[dict[str, str]]:
        async with self._lock:
            self._sync_pages()
            return [{'id': tab, 'url': page.url, 'title': await page.title() if self._allowed_url(page.url) else '[Protected URL]'} for tab, page in self._pages.items()]
    async def tab_url(self, tab_id: str) -> str:
        async with self._lock:
            page = self._page(tab_id)
            return page.url


    @staticmethod
    def _navigation_options(wait_until: str, timeout_ms: int) -> None:
        if not isinstance(wait_until, str) or wait_until not in {'commit', 'domcontentloaded', 'load'}:
            raise BrowserError('wait_until must be commit, domcontentloaded, or load')
        if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or not 1 <= timeout_ms <= 120000:
            raise BrowserError('timeout_ms must be between 1 and 120000')

    async def new_tab(self, url: str = 'about:blank', wait_until: str = 'domcontentloaded', timeout_ms: int = 15000) -> dict[str, Any]:
        self._navigation_options(wait_until, timeout_ms)
        if not isinstance(url,str) or not self._allowed_url(url):
            raise UnsafeActionError('Navigation permits only HTTP(S) and about:blank')
        async with self._lock:
            self._sync_pages()
            if self._default_context is None:
                raise BrowserError('Browser has no usable context')
            page = await self._default_context.new_page()
            self._owned.add(page)
            self._sync_pages()
            status = 'complete'
            try:
                try:
                    await page.goto(url, wait_until=wait_until, timeout=timeout_ms)
                except PlaywrightTimeoutError:
                    status = 'timeout'
                self._ensure_safe(page)
            except BaseException:
                try:
                    await page.close()
                except Exception:
                    pass
                self._owned.discard(page)
                self._sync_pages()
                raise
            result = {'id': self._ids[page], 'url': page.url, 'title': await page.title(), 'navigation_status': status, 'wait_until': wait_until}
            if status == 'timeout':
                result['diagnostic'] = {'code': 'navigation_timeout', 'timeout_ms': timeout_ms, 'message': 'Owned tab retained; observe the partially loaded page before choosing another action'}
            return result

    async def close_tab(self, tab_id: str) -> None:
        async with self._lock:
            page = self._page(tab_id,allow_protected=True)
            if self._attached and page not in self._owned:
                raise UnsafeActionError('Cannot close a preexisting user tab')
            for kind in ('network', 'websocket'):
                await self._monitor.stop(tab_id, kind)
            self._invalidate(tab_id)
            await page.close()
            self._owned.discard(page)
            self._sync_pages()

    async def monitor_start(self, tab_id: str, kind: str, **options: Any) -> dict[str, Any]:
        async with self._lock:
            return await self._monitor.start(self._page(tab_id), tab_id, kind, **options)

    async def monitor_list(self, tab_id: str, kind: str, cursor: int | None = None, limit: int = 100) -> dict[str, Any]:
        async with self._lock:
            self._page(tab_id)
            return self._monitor.list(tab_id, kind, cursor=cursor, limit=limit)

    async def monitor_stop(self, tab_id: str, kind: str) -> dict[str, Any]:
        async with self._lock:
            return await self._monitor.stop(tab_id, kind)

    async def _frame_geometry(self, frame: _FrameRef) -> tuple[float, float, dict[str, float]]:
        view = (await self._eval(frame,'({width:innerWidth,height:innerHeight})')
                if self._allowed_url(frame.url, subframe=frame.parent_frame is not None)
                else {'width': 1000000, 'height': 1000000})
        clip = {'left':0.0,'top':0.0,'right':float(view['width']),'bottom':float(view['height'])}
        x = y = 0.0
        current = frame
        while current.parent_frame is not None:
            geometry = await self._eval_owner(current,'''(e)=>{
              const r=e.getBoundingClientRect();
              const root=document.documentElement,rs=getComputedStyle(root);
              const clipsOverflow=p=>{const s=getComputedStyle(p);return p!==root&&s.display!=='contents'&&s.display!=='inline'&&!(p===document.body&&root.tagName==='HTML'&&rs.overflowX==='visible'&&rs.overflowY==='visible'&&rs.contain==='none'&&s.contain==='none');};
              let left=Math.max(0,r.left+e.clientLeft),top=Math.max(0,r.top+e.clientTop),right=Math.min(innerWidth,r.left+e.clientLeft+e.clientWidth),bottom=Math.min(innerHeight,r.top+e.clientTop+e.clientHeight),transformed=false,hidden=false;
              for(let p=e;p;p=p.parentElement||p.getRootNode().host){const s=getComputedStyle(p),pr=p.getBoundingClientRect();transformed ||= s.transform!=='none'||s.zoom!=='1'&&s.zoom!=='normal';hidden ||= s.display==='none'||s.visibility==='hidden'||Number(s.opacity)===0;if(p!==e&&clipsOverflow(p)){if(/hidden|clip|auto|scroll/.test(s.overflowX)){left=Math.max(left,pr.left+p.clientLeft);right=Math.min(right,pr.left+p.clientLeft+p.clientWidth);}if(/hidden|clip|auto|scroll/.test(s.overflowY)){top=Math.max(top,pr.top+p.clientTop);bottom=Math.min(bottom,pr.top+p.clientTop+p.clientHeight);}}}
              return {x:r.x+e.clientLeft,y:r.y+e.clientTop,left,top,right,bottom,transformed,hidden};
            }''')
            if geometry['transformed']:
                raise UnsafeActionError('Transformed iframe requires screenshot visual targeting; DOM coordinates unsupported')
            if geometry['hidden'] or geometry['right'] <= geometry['left'] or geometry['bottom'] <= geometry['top']:
                raise UnsafeActionError('Iframe is hidden or outside its ancestor viewport')
            for edge in ('left','right'):
                clip[edge] += geometry['x']
            for edge in ('top','bottom'):
                clip[edge] += geometry['y']
            clip['left'] = max(clip['left'],geometry['left'])
            clip['right'] = min(clip['right'],geometry['right'])
            clip['top'] = max(clip['top'],geometry['top'])
            clip['bottom'] = min(clip['bottom'],geometry['bottom'])
            x += geometry['x']
            y += geometry['y']
            current = current.parent_frame
        if clip['right'] <= clip['left'] or clip['bottom'] <= clip['top']:
            raise UnsafeActionError('Iframe is clipped outside the visible viewport')
        return x, y, clip

    @staticmethod
    def _semantic_digest(data: dict[str, Any]) -> str:
        import hashlib
        context = {'text':data['text'],'fields':data['fields'],'elements':[(e['id'],e['signature'],e['operations'],e.get('options')) for e in data['elements']]}
        return hashlib.sha256(json.dumps(context,sort_keys=True,separators=(',',':')).encode()).hexdigest()

    def _frame_allowed(self, frame: _FrameRef) -> bool:
        current = frame
        while current is not None:
            if not self._allowed_url(current.url, subframe=current.parent_frame is not None):
                return False
            current = current.parent_frame
        return True

    async def _protected_frames(self, frames: list[_FrameRef]) -> tuple[list[dict[str, Any]], bool]:
        limitations = []
        withheld = False
        visibility_cache: dict[str, str] = {}
        for frame in frames:
            if self._frame_allowed(frame):
                continue
            # Determine visibility at the outermost protected owner. Its hidden
            # descendants need no evaluation and must not suppress parent pixels.
            boundary = frame
            current = frame.parent_frame
            while current is not None:
                if not self._allowed_url(current.url, subframe=current.parent_frame is not None):
                    boundary = current
                current = current.parent_frame
            visibility = visibility_cache.get(boundary.frame_id)
            if visibility is None:
                visibility = 'unknown'
                try:
                    await self._frame_geometry(boundary)
                    visibility = 'visible'
                except ProtectedUrlError:
                    pass
                except UnsafeActionError as exc:
                    if 'hidden' in str(exc) or 'clipped' in str(exc):
                        visibility = 'hidden'
                except PlaywrightError:
                    pass
                visibility_cache[boundary.frame_id] = visibility
            withheld |= visibility != 'hidden'
            limitations.append({'frame_id': frame.frame_id, 'scheme': urlsplit(frame.url).scheme,
                                'code': 'protected_subframe_skipped', 'visibility': visibility,
                                'reason': 'Protected frame content and targets are not disclosed'})
        return limitations, withheld

    async def _capture(self, page: Any) -> bytes:
        limitations, withheld = await self._protected_frames(await self._frames(page))
        if withheld:
            raise UnsafeActionError('Screenshot withheld: visible or unknown protected subframe')
        masks = [frame.locator('input[type="password"],input[autocomplete*="password"],input[autocomplete^="cc-"],input[autocomplete="one-time-code"]') for frame in page.frames if self._frame_allowed(frame)]
        self._ensure_safe(page)
        return await page.screenshot(type='png', full_page=False, mask=masks)


    async def observe(self, tab_id: str, screenshot: bool = False, max_text: int = 12000) -> dict[str, Any]:
        if not isinstance(max_text, int) or not 0 <= max_text <= 1_000_000:
            raise BrowserError('max_text must be between 0 and 1000000')
        async with self._lock:
            page = self._page(tab_id)
            revision = uuid.uuid4().hex
            targets: dict[str, _Target] = {}
            elements, sections, documents, limitations = [], [], [], []
            frames = await self._frames(page)
            all_frame_ids = {f.frame_id for f in frames}
            protected, screenshot_withheld = await self._protected_frames(frames)
            limitations.extend(protected)
            safety = {'editable_nonempty': False, 'sensitive_fields': False, 'unsaved': False}
            ready_state = 'unknown'
            collected_text_nodes = 0
            visible_alerts = []
            source_truncated = False
            if len(frames) > 64:
                limitations.append({'frame': 64, 'reason': f'Omitted {len(frames) - 64} frames beyond 64-frame limit'})
                frames = frames[:64]
                source_truncated = True
            total_elements_budget = 2000
            total_text_budget = 1000000
            omitted_elements = 0
            for index, frame in enumerate(frames):
                if not self._frame_allowed(frame):
                    continue
                try:
                    ox, oy, clip = await self._frame_geometry(frame)
                    remaining_text = max(0, total_text_budget - sum(len(s) for s in sections))
                    remaining_elements = max(0, total_elements_budget - len(elements))
                    data = await self._eval(frame,self._script, {'clip':{'left':clip['left']-ox,'right':clip['right']-ox,'top':clip['top']-oy,'bottom':clip['bottom']-oy},'max_text_chars':remaining_text,'max_elements':remaining_elements,'max_nodes':20000})
                except PlaywrightError:
                    limitations.append({'frame': index, 'reason': 'detached or unavailable frame'})
                    continue
                except ProtectedUrlError:
                    raise StaleObservationError('Frame navigated to a protected document during observation') from None
                except UnsafeActionError as exc:
                    limitations.append({'frame': index, 'reason': str(exc)})
                    continue
                if data.get('source_truncated'):
                    source_truncated = True
                for key in ('editable_nonempty', 'sensitive_fields'):
                    safety[key] |= data.get(key, False)
                collected_text_nodes += data.get('rendered_text_nodes', 0)
                if frame.parent_frame is None:
                    ready_state = data.get('ready_state', 'unknown')
                for alert in data.get('visible_alerts', []):
                    if len(visible_alerts) >= 8:
                        break
                    bounds = dict(alert['bounds'])
                    bounds['x'] += ox
                    bounds['y'] += oy
                    visible_alerts.append({**alert, 'bounds': bounds, 'frame_id': frame.frame_id})
                omitted_elements += data.get('omitted_elements', 0)
                documents.append((frame, data['document'], self._semantic_digest(data)))
                sections.append(f"[frame {index}: {data['url']}]\n{data['text']}")
                for element in data['elements']:
                    local = element['id']
                    public = f"f{data['document']}:{local}"
                    bounds = dict(element['bounds'])
                    bounds['x'] += ox
                    bounds['y'] += oy
                    if bounds['x']+bounds['width']<=clip['left'] or bounds['x']>=clip['right'] or bounds['y']+bounds['height']<=clip['top'] or bounds['y']>=clip['bottom']:
                        continue
                    target = _Target(frame, data['document'], local, element['operations'], bounds, screenshot and not screenshot_withheld and element['role'] == 'canvas', element.pop('signature'), tuple(o['value'] for o in element.get('options',[]) if not o['disabled']), element.get('multiple',False), tuple(url for url in (element.get('href'),element.get('form_action')) if url))
                    targets[public] = target
                    elements.append({**element, 'id': public, 'bounds': bounds, 'frame': {'index': index, 'url': data['url'], 'offset': {'x': ox, 'y': oy}, 'document': data['document']}})
            text = '\n\n'.join(sections)
            observation: dict[str, Any] = {'id': revision, 'tab_id': tab_id, 'url': page.url, 'title': await page.title(), 'text': text[:max_text], 'elements': elements, 'truncated': len(text) > max_text or source_truncated, 'source_truncated': source_truncated, 'text_length': len(text), 'next_offset': max_text if len(text) > max_text else None, 'omitted_elements': omitted_elements, 'timestamp': time.time(), 'limitations': limitations}
            observation['visible_alerts'] = visible_alerts
            coverage_reasons = [item.get('code', 'frame_unavailable') for item in limitations if item.get('visibility') != 'hidden']
            if source_truncated:
                coverage_reasons.append('collector_budget_exceeded')
            if not collected_text_nodes and not any(e['role'] != 'canvas' for e in elements):
                coverage_reasons.append('empty_rendered_dom')
            observation.update(ready_state=ready_state, safety=safety,
                               coverage={'status': 'partial' if coverage_reasons else 'complete',
                                         'reasons': list(dict.fromkeys(coverage_reasons)),
                                         'dom_elements': len(elements), 'rendered_text_nodes': collected_text_nodes})
            observation['page_state'] = describe_dom(observation)
            png = None
            if screenshot and screenshot_withheld:
                observation['screenshot_status'] = 'withheld'
                observation['diagnostic'] = {'code': 'protected_frame_screenshot_withheld', 'message': 'Main DOM remains available; protected subframe pixels cannot be disclosed'}
            if screenshot and not screenshot_withheld:
                png = await self._capture(page)
                observation['screenshot'] = base64.b64encode(png).decode('ascii')
                observation['screenshot_status'] = 'captured'
                viewport = await self._eval(frames[0],'({width:innerWidth,height:innerHeight})')
                observation['viewport'] = viewport
                main_doc = next((token for frame, token, digest in documents if frame.parent_frame is None), None)
                if main_doc is not None:
                    for row in range(8):
                        for col in range(8):
                            key = f'visual:{row}:{col}'
                            bounds = {'x': col * viewport['width']/8, 'y': row * viewport['height']/8, 'width': viewport['width']/8, 'height': viewport['height']/8}
                            targets[key] = _Target(frames[0], main_doc, None, ['click','hover','drag','scroll'], bounds, True)
                            elements.append({'id': key, 'role': 'visual-region', 'name': f'Screenshot region row {row} column {col}', 'value': '', 'operations': ['click','hover','drag','scroll'], 'bounds': bounds, 'frame': {'index': 0, 'document': main_doc}})
            self._snapshots[revision] = {'tab': tab_id, 'targets': targets, 'documents': documents, 'frame_ids': all_frame_ids, 'text': text, 'url': page.url, 'visual': png is not None, 'pixels': png, 'source_truncated': source_truncated, 'page': page}
            self._snapshots[revision]['viewport'] = observation.get('viewport')
            while len(self._snapshots) > self._snapshot_limit:
                self._snapshots.pop(next(iter(self._snapshots)))
            if screenshot:
                try:
                    await self._validate(self._snapshots[revision])
                except BaseException:
                    self._snapshots.pop(revision,None)
                    raise
            else:
                self._ensure_safe(page)
            return observation

    async def text_continuation(self, tab_id: str, observation_id: str, offset: int = 0, max_text: int = 12000) -> dict[str, Any]:
        if not isinstance(offset, int) or offset < 0 or not isinstance(max_text, int) or not 1 <= max_text <= 1_000_000:
            raise BrowserError('Invalid text continuation range')
        async with self._lock:
            snapshot = self._snapshot(tab_id, observation_id)
            text = snapshot['text']
            source_truncated = snapshot.get('source_truncated', False)
            end = min(len(text), offset + max_text)
            has_more = end < len(text) or (end == len(text) and source_truncated)
            return {'observation_id': observation_id, 'text': text[offset:end], 'offset': offset, 'next_offset': end if end < len(text) else None, 'text_length': len(text), 'truncated': has_more, 'source_truncated': source_truncated}

    def _snapshot(self, tab_id: str, revision: str) -> dict[str, Any]:
        snapshot = self._snapshots.get(revision)
        if snapshot is None:
            raise StaleObservationError('Observation expired or invalidated; observe again')
        if snapshot['tab'] != tab_id:
            raise UnsafeActionError('Observation belongs to another tab')
        self._page(tab_id)
        return snapshot

    async def _validate(self, snapshot: dict[str, Any], *, visual_targets: tuple[_Target, ...] = ()) -> None:
        page = snapshot['page']
        frames = await self._frames(page)
        if page.url != snapshot['url'] or {f.frame_id for f in frames} != snapshot['frame_ids']:
            raise StaleObservationError('Page URL or frame topology changed; observe again')
        for frame, token, digest in snapshot['documents']:
            try:
                ox, oy, clip = await self._frame_geometry(frame)
                data = await self._eval(frame,self._script, {'clip':{'left':clip['left']-ox,'right':clip['right']-ox,'top':clip['top']-oy,'bottom':clip['bottom']-oy}})
                valid = data['document']==token and self._semantic_digest(data)==digest
            except (PlaywrightError, UnsafeActionError):
                valid = False
            if not valid:
                raise StaleObservationError('Document, visible semantics, or form state changed; observe again')
        if visual_targets:
            if not snapshot['visual']:
                raise UnsafeActionError('Visual action requires a disclosed screenshot baseline')
            viewport = await self._eval(frames[0], '({width:innerWidth,height:innerHeight})')
            if viewport != snapshot['viewport']:
                raise StaleObservationError('Viewport geometry changed; observe again')
            pixels = await self._capture(page)
            self._compare_target_pixels(snapshot['pixels'], pixels, snapshot['viewport'], visual_targets)

    @staticmethod
    def _compare_target_pixels(baseline: bytes, current: bytes, viewport: dict[str, Any], targets: tuple[_Target, ...]) -> None:
        # Decode each coherent whole screenshot once; compare only the full observed
        # target rectangles (including drag destination), never an unrelated pixel.
        if max(len(baseline), len(current)) > 32 * 1024 * 1024:
            raise UnsafeActionError('Screenshot exceeds bounded visual guard byte budget')
        with Image.open(io.BytesIO(baseline)) as before, Image.open(io.BytesIO(current)) as after:
            if before.size != after.size:
                raise StaleObservationError('Screenshot dimensions changed; observe again')
            width, height = before.size
            if width * height > 8_388_608 or width <= 0 or height <= 0:
                raise UnsafeActionError('Screenshot exceeds bounded visual guard pixel budget')
            sx, sy = width / viewport['width'], height / viewport['height']
            for target in targets:
                b = target.bounds
                box = (max(0, math.floor(b['x'] * sx)), max(0, math.floor(b['y'] * sy)),
                       min(width, math.ceil((b['x'] + b['width']) * sx)),
                       min(height, math.ceil((b['y'] + b['height']) * sy)))
                if box[0] >= box[2] or box[1] >= box[3]:
                    raise StaleObservationError('Observed target region moved outside screenshot')
                with before.crop(box) as old_crop, after.crop(box) as new_crop:
                    with old_crop.convert('RGB') as old_rgb, new_crop.convert('RGB') as new_rgb:
                        with ImageChops.difference(old_rgb, new_rgb) as diff:
                            changed = diff.getbbox()
                if changed:
                    region = {'x': (box[0] + changed[0]) / sx, 'y': (box[1] + changed[1]) / sy,
                              'width': (changed[2] - changed[0]) / sx, 'height': (changed[3] - changed[1]) / sy}
                    error = StaleObservationError('Target pixels changed in region ' + json.dumps(region, sort_keys=True) + '; observe again before visual dispatch')
                    error.diagnostic = {'code': 'target_pixels_changed', 'changed_region': region, 'target_bounds': dict(b)}
                    raise error

    async def _point(self, target: _Target, action: dict[str, Any]) -> tuple[float, float]:
        ox, oy, clip = await self._frame_geometry(target.frame)
        explicit = 'x' in action or 'y' in action
        if explicit and not ('x' in action and 'y' in action):
            raise UnsafeActionError('Point requires both x and y')
        point = None
        if explicit or target.visual:
            b = target.bounds
            x = action.get('x', b['x'] + b['width']/2)
            y = action.get('y', b['y'] + b['height']/2)
            if not all(isinstance(n, (int, float)) and not isinstance(n, bool) and math.isfinite(n) for n in (x,y)):
                raise UnsafeActionError('Point must be finite numeric coordinates')
            if not b['x'] <= x < b['x']+b['width'] or not b['y'] <= y < b['y']+b['height']:
                raise UnsafeActionError('Point outside observed target region')
            point = {'x': x-ox, 'y': y-oy}
        try:
            result = await self._eval(target.frame,_GUARD, {'token': target.document, 'node': target.node, 'point': point, 'signature':target.signature, 'clip':{'left':clip['left']-ox,'right':clip['right']-ox,'top':clip['top']-oy,'bottom':clip['bottom']-oy}})
        except PlaywrightError as exc:
            raise StaleObservationError('Target frame disappeared') from exc
        if 'error' in result:
            if result['error'] in ('wrong document','detached target','target semantics changed'):
                raise StaleObservationError(result['error'])
            raise UnsafeActionError(result['error'])
        if target.visual and target.node and result.get('bounds'):
            live_bounds = {**result['bounds'], 'x': result['bounds']['x'] + ox, 'y': result['bounds']['y'] + oy}
            if live_bounds != target.bounds:
                raise StaleObservationError('Visual target geometry changed; observe again')
        if result.get('navigation') and not self._allowed_url(result['navigation']):
            raise UnsafeActionError('Point hits a protected navigation link')
        # Also hit-test each ancestor iframe; a parent overlay must not pass.
        current = target.frame
        px, py = result['x'], result['y']
        while current.parent_frame is not None:
            check = await self._eval_owner(current,'''(e,p)=>{const r=e.getBoundingClientRect();const x=r.x+e.clientLeft+p.x,y=r.y+e.clientTop+p.y;let h=document.elementFromPoint(x,y);while(h?.shadowRoot){const n=h.shadowRoot.elementFromPoint(x,y);if(!n||n===h)break;h=n;}return {x,y,ok:h===e};}''', {'x': px, 'y': py})
            if not check['ok']:
                raise UnsafeActionError('Iframe covered by parent content')
            px, py = check['x'], check['y']
            current = current.parent_frame
        return result['x']+ox, result['y']+oy


    def _invalidate(self, tab_id: str) -> None:
        for revision, snapshot in list(self._snapshots.items()):
            if snapshot['tab'] == tab_id:
                self._snapshots.pop(revision)

    async def act(self, tab_id: str, action: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            return await self._act(tab_id, action)

    async def _guard_protected_input(self, page: Any, points: list[tuple[float, float]], *, keyboard: bool = False) -> None:
        frames = await self._frames(page)
        protected, _ = await self._protected_frames(frames)
        if not protected:
            return
        if keyboard:
            focused_frame = await self._eval(frames[0], "(() => {let e=document.activeElement;while(e?.shadowRoot?.activeElement)e=e.shadowRoot.activeElement;return !!e&&['IFRAME','FRAME'].includes(e.tagName);})()")
            if focused_frame:
                raise UnsafeActionError('Unbound keyboard input into a frame is unsupported while protected frames are present; use an observed allowed target')
        hidden = {item['frame_id'] for item in protected if item['visibility'] == 'hidden'}
        for frame in frames:
            if self._frame_allowed(frame) or frame.frame_id in hidden:
                continue
            boundary = frame
            current = frame.parent_frame
            while current is not None:
                if not self._allowed_url(current.url, subframe=current.parent_frame is not None):
                    boundary = current
                current = current.parent_frame
            try:
                _, _, clip = await self._frame_geometry(boundary)
            except (PlaywrightError, UnsafeActionError):
                if points:
                    raise UnsafeActionError('Cannot prove input avoids an unavailable protected frame') from None
                continue
            if any(clip['left'] <= x < clip['right'] and clip['top'] <= y < clip['bottom'] for x, y in points):
                raise UnsafeActionError('Input point overlaps a skipped protected frame')
            if len(points) == 2:
                left, right = sorted((points[0][0], points[1][0]))
                top, bottom = sorted((points[0][1], points[1][1]))
                if left < clip['right'] and right >= clip['left'] and top < clip['bottom'] and bottom >= clip['top']:
                    raise UnsafeActionError('Drag path may overlap a skipped protected frame')

    async def _act(self, tab_id: str, action: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(action, dict):
            raise UnsafeActionError('Action must be an object')
        operation = action.get('operation')
        if operation not in {'click','fill','select','scroll','press','hover','drag','wait','back','forward','reload'}:
            raise UnsafeActionError('Unsupported operation')
        snapshot = self._snapshot(tab_id, action.get('observation_id', ''))
        target = snapshot['targets'].get(action.get('target'))
        destination = snapshot['targets'].get(action.get('to_target'))
        visual_targets = tuple(t for t in (target, destination) if t is not None and t.visual)
        await self._validate(snapshot, visual_targets=visual_targets)
        page = snapshot['page']
        if action.get('target') is not None and target is None:
            raise UnsafeActionError('Unknown target')
        if operation in {'click','fill','select','hover','drag'} and target is None:
            raise UnsafeActionError('Operation requires an observed target')
        if target and operation not in target.operations:
            raise UnsafeActionError('Operation unsupported for target')
        if target and operation in {'click','press'} and any(not self._allowed_url(url) for url in target.navigation):
            raise UnsafeActionError('Target would navigate to a protected URL scheme')
        text = action.get('text', action.get('value'))
        value = action.get('value')
        key = action.get('key')
        dx, dy = action.get('delta_x',0), action.get('delta_y',action.get('delta',600))
        seconds = action.get('seconds',0.25)
        if operation=='fill' and not isinstance(text,str):
            raise UnsafeActionError('fill requires text')
        if operation=='select' and (not isinstance(value,(str,list)) or isinstance(value,list) and not all(isinstance(v,str) for v in value)):
            raise UnsafeActionError('select requires option value or list of values')
        if operation=='select':
            selected = value if isinstance(value,list) else [value]
            if any(v not in target.choices for v in selected) or len(selected)>1 and not target.multiple:
                raise UnsafeActionError('Select value is not an observed enabled option')
        if operation=='press':
            import re
            named = {'Enter','Escape','Esc','Backspace','Delete','Tab','Space','Home','End','PageDown','PageUp','Insert','PrintScreen','Pause','CapsLock','NumLock','ScrollLock','ContextMenu','Clear','Help','Cancel','Meta','Shift','Control','Alt','ControlOrMeta','ArrowUp','ArrowDown','ArrowLeft','ArrowRight'}
            parts = key.split('+') if isinstance(key,str) else []
            if not parts or any(p not in {'Alt','Control','Meta','Shift','ControlOrMeta'} for p in parts[:-1]) or not (parts[-1] in named or len(parts[-1])==1 or re.fullmatch(r'F(?:[1-9]|1[0-9]|2[0-4])|Key[A-Z]|Digit[0-9]|Numpad[0-9]',parts[-1])):
                raise UnsafeActionError('press requires a supported Playwright key name')
        if operation=='scroll' and not all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) and abs(v)<=100000 for v in (dx,dy)):
            raise UnsafeActionError('scroll deltas must be finite and bounded')
        if operation=='wait' and (not isinstance(seconds,(int,float)) or isinstance(seconds,bool) or not math.isfinite(seconds) or not 0<=seconds<=10):
            raise UnsafeActionError('wait seconds must be between 0 and 10')
        if operation == 'reload':
            self._navigation_options(action.get('wait_until', 'domcontentloaded'), action.get('timeout_ms', 15000))
        if operation=='drag' and destination is None:
            raise UnsafeActionError('drag requires observed to_target')
        point = await self._point(target, action) if target else None
        end = await self._point(destination,{k[3:]:v for k,v in action.items() if k in ('to_x','to_y')}) if operation=='drag' else None
        input_points = [p for p in (point, end) if p is not None]
        if operation == 'scroll' and not input_points:
            view = await self._eval((await self._frames(page))[0], '({x:innerWidth/2,y:innerHeight/2})')
            input_points.append((view['x'], view['y']))
        if input_points or operation == 'press' and target is None:
            await self._guard_protected_input(page, input_points, keyboard=operation == 'press' and target is None)
        old_pages = set(page.context.pages)
        started = time.perf_counter()
        # Invalidate before dispatch, including failed/partial operations.
        self._invalidate(tab_id)
        navigation_status = None
        if operation == 'click':
            await page.mouse.click(*point)
        elif operation == 'hover':
            await page.mouse.move(*point)
        elif operation == 'fill':
            focused = await self._eval(target.frame,'''({token,node})=>{const s=globalThis.__browserAutomationSnapshot_v1;const e=s&&s.token===token?s.nodes.get(node):null;if(!e||!e.isConnected||e.disabled||e.closest('[inert]'))return false;if(typeof e.focus==='function')e.focus();let a=document.activeElement;while(a?.shadowRoot?.activeElement)a=a.shadowRoot.activeElement;return !!e&&(a===e||e.contains(a));}''', {'token':target.document,'node':target.node})
            if not focused:
                raise UnsafeActionError('Target could not be focused for text entry')
            await page.keyboard.press('Meta+A' if platform.system() == 'Darwin' else 'Control+A')
            if text:
                await page.keyboard.insert_text(text)
            else:
                await page.keyboard.press('Backspace')
        elif operation == 'select':
            selected = await self._eval(target.frame,'''({token,node,values,signature})=>{const s=globalThis.__browserAutomationSnapshot_v1;const e=s&&s.token===token?s.nodes.get(node):null;if(!e||!e.isConnected||s.fingerprint(e)!==signature)return false;for(const o of e.options)o.selected=values.includes(o.value);e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));return true;}''',{'token':target.document,'node':target.node,'values':value if isinstance(value,list) else [value],'signature':target.signature})
            if not selected:
                raise StaleObservationError('Select target changed before form input')
        elif operation == 'press':
            if target:
                focused = await self._eval(target.frame,'''({token,node})=>{
                  const s=globalThis.__browserAutomationSnapshot_v1,e=s&&s.token===token?s.nodes.get(node):null;
                  if(!e||!e.isConnected||e.disabled||e.closest('[inert]')) return false;
                  if(typeof e.focus==='function') e.focus();
                  let a=document.activeElement;while(a?.shadowRoot?.activeElement)a=a.shadowRoot.activeElement;
                  return !!e&&(a===e||e.contains(a));
                }''', {'token':target.document,'node':target.node})
                if not focused:
                    raise UnsafeActionError('Target could not be focused for keyboard input')
            await page.keyboard.press(key)
        elif operation == 'scroll':
            if point:
                await page.mouse.move(*point)
            else:
                main_frame = (await self._frames(page))[0]
                view = await self._eval(main_frame,'({x:innerWidth/2,y:innerHeight/2})')
                await page.mouse.move(view['x'],view['y'])
            await page.mouse.wheel(dx, dy)
        elif operation == 'drag':
            await page.mouse.move(*point)
            await page.mouse.down()
            try:
                await page.mouse.move(*end, steps=12)
            finally:
                await page.mouse.up()
        elif operation == 'wait':
            await asyncio.sleep(seconds)
        elif operation == 'back':
            await page.go_back()
        elif operation == 'forward':
            await page.go_forward()
        elif operation == 'reload':
            navigation_status = 'complete'
            try:
                await page.reload(wait_until=action.get('wait_until', 'domcontentloaded'), timeout=action.get('timeout_ms', 15000))
            except PlaywrightTimeoutError:
                navigation_status = 'timeout'
        self._ensure_safe(page)
        await asyncio.sleep(0)  # yield for popup notification; never a fabricated wait-for-success
        self._sync_pages()
        popup_ids = []
        for popup in set(page.context.pages) - old_pages:
            if await popup.opener() == page:
                if page in self._owned:
                    self._owned.add(popup)
                popup_ids.append(self._ids[popup])
        result = {'operation': operation, 'tab_id': tab_id, 'url': page.url, 'popup_tabs': popup_ids, 'latency_ms': (time.perf_counter()-started)*1000}
        if navigation_status is not None:
            result.update(navigation_status=navigation_status, wait_until=action.get('wait_until', 'domcontentloaded'))
            if navigation_status == 'timeout':
                result['diagnostic'] = {'code': 'navigation_timeout', 'timeout_ms': action.get('timeout_ms', 15000), 'message': 'Observe retained tab before choosing another action'}
        return result

    @staticmethod
    def _upload_payloads(paths: list[str], allowed_directory: str) -> list[dict[str,str]]:
        payloads = []
        remaining = MAX_UPLOAD_BYTES
        with ScopedFiles(allowed_directory) as scope:
            for path in paths:
                content = scope.read(path,min(MAX_FILE_BYTES,remaining))
                remaining -= len(content)
                payloads.append({'name':Path(path).name,'mime':mimetypes.guess_type(path)[0] or 'application/octet-stream','data':base64.b64encode(content).decode('ascii')})
        return payloads

    async def upload(self, tab_id: str, observation_id: str, target: str, paths: list[str], *, allowed_directory: str, approved: bool = False) -> dict[str, Any]:
        if not approved:
            raise ConsentRequiredError('Local file upload requires explicit approval')
        if not isinstance(paths,list) or not 1<=len(paths)<=MAX_UPLOAD_FILES or not all(isinstance(p,str) for p in paths):
            raise UnsafeActionError(f'Upload requires between 1 and {MAX_UPLOAD_FILES} file paths')
        async with self._lock:
            snapshot = self._snapshot(tab_id, observation_id)
            node = snapshot['targets'].get(target)
            if node is None or 'upload' not in node.operations or len(paths)>1 and not node.multiple:
                raise UnsafeActionError('Upload requires an observed compatible file input')
            try:
                payloads = await asyncio.to_thread(self._upload_payloads,paths,allowed_directory)
            except (OSError,FilePolicyError) as exc:
                raise UnsafeActionError(f'Secure upload rejected: {exc}') from exc
            await self._validate(snapshot)
            await self._point(node, {})
            self._invalidate(tab_id)
            selected = await self._eval(node.frame,'''({token,node,signature,files})=>{
              const s=globalThis.__browserAutomationSnapshot_v1,e=s&&s.token===token?s.nodes.get(node):null;
              if(!e||!e.isConnected||e.type!=='file'||e.disabled||s.fingerprint(e)!==signature)return false;
              const transfer=new DataTransfer();
              for(const file of files){const raw=atob(file.data),bytes=new Uint8Array(raw.length);for(let i=0;i<raw.length;i++)bytes[i]=raw.charCodeAt(i);transfer.items.add(new File([bytes],file.name,{type:file.mime}));}
              Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'files').set.call(e,transfer.files);
              e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));return true;
            }''',{'token':node.document,'node':node.node,'signature':node.signature,'files':payloads})
            if not selected:
                raise StaleObservationError('Upload target changed before form input')
            self._ensure_safe(snapshot['page'])
            return {'tab_id':tab_id,'operation':'upload','count':len(payloads)}

    async def download(self, tab_id: str, action: dict[str, Any], destination: str, *, allowed_directory: str, approved: bool = False, timeout: float = 30000) -> dict[str, Any]:
        if not approved:
            raise ConsentRequiredError('Saving a download requires explicit approval')
        if not isinstance(action,dict) or action.get('operation')!='click':
            raise UnsafeActionError('Download must be triggered by an observed click')
        async with self._lock:
            try:
                with ScopedFiles(allowed_directory) as scope, scope.destination(destination) as pinned:
                    try:
                        os.stat(pinned.name,dir_fd=pinned.parent,follow_symlinks=False)
                    except FileNotFoundError:
                        pass
                    else:
                        raise UnsafeActionError('Download destination already exists')
                    page = self._page(tab_id)
                    async with page.expect_download(timeout=timeout) as pending:
                        result = await self._act(tab_id,action)
                    download = await pending.value
                    failure = await download.failure()
                    if failure:
                        raise BrowserError(f'Download failed: {failure}')
                    with tempfile.TemporaryDirectory(prefix='browser-automation-download-') as private:
                        staging = Path(private)/'payload'
                        await download.save_as(staging)
                        copy_task = asyncio.create_task(asyncio.to_thread(pinned.save_from,staging))
                        cancelled = False
                        while True:
                            try:
                                size = await asyncio.shield(copy_task)
                                break
                            except asyncio.CancelledError:
                                cancelled = True
                        if cancelled:
                            raise asyncio.CancelledError
                    return {**result,'download':{'path':str(pinned.path),'suggested_filename':download.suggested_filename,'bytes':size,'directory_pinned':True}}
            except (OSError,FilePolicyError) as exc:
                raise UnsafeActionError(f'Secure download rejected: {exc}') from exc

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            await self._monitor.close()
            for page, listeners in self._page_listeners.items():
                for event, callback in listeners:
                    page.remove_listener(event, callback)
            self._page_listeners.clear()
            sessions = set(self._page_cdp.values()) | set(self._oop_cdp.values())
            for session in sessions:
                try:
                    await session.detach()
                except PlaywrightError:
                    pass
            self._page_cdp.clear()
            self._oop_cdp.clear()
            self._local_frames.clear()
            try:
                if not self._attached:
                    await self._browser.close()
                # Stopping Playwright disconnects CDP without Browser.close and
                # leaves user tabs AND owned tabs intact until explicitly closed.
            finally:
                await self._playwright.stop()
                self._snapshots.clear()

    async def __aenter__(self) -> BrowserSession:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()
