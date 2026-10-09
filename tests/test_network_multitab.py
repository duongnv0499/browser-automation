"""Browser-session capture scope, future pages and native disconnect regression."""
import asyncio

import pytest
from playwright.async_api import async_playwright

from browser_automation.browser import BrowserSession
from test_network_requests import api_site


async def wait_for_capture(browser, tab):
    for _ in range(100):
        listing = await browser.network_list_many()
        capture = next((c for c in listing['captures'] if c['tab_id'] == tab and c['active']), None)
        if capture is not None:
            return capture
        await asyncio.sleep(.02)
    raise AssertionError('Future tab capture did not attach')


@pytest.mark.asyncio
async def test_existing_and_future_popup_capture_independent_cursors_and_stop(tmp_path):
    async with api_site() as (url, _, _), await BrowserSession.launch(headless=True) as browser:
        first = (await browser.new_tab(url))['id']
        second = (await browser.new_tab(url))['id']
        start = await browser.network_start_many(include_new_tabs=True)
        assert {c['tab_id'] for c in start['captures']} == {first, second}
        assert start['initial_request_race']
        await browser._page(first).click('#send')
        await browser._page(second).click('#send')
        async with browser._page(first).expect_popup() as popup_info:
            await browser._page(first).evaluate('window.open("/", "capture-popup")')
        popup = await popup_info.value
        await popup.wait_for_load_state()
        browser._sync_pages()
        third = browser._ids[popup]
        await wait_for_capture(browser, third)
        await popup.click('#send')
        for _ in range(100):
            listing = await browser.network_list_many(limit=1000)
            if all(any(e.get('method') == 'POST' for e in c['events']) for c in listing['captures']):
                break
            await asyncio.sleep(.02)
        assert {c['tab_id'] for c in listing['captures']} == {first, second, third}
        assert all(any(e.get('method') == 'POST' for e in c['events']) for c in listing['captures'])
        cursors = {c['tab_id']: c['next_cursor'] for c in listing['captures']}
        await asyncio.sleep(.05)
        final = await browser.network_list_many(limit=1000)
        cursors = {c['tab_id']: c['next_cursor'] for c in final['captures']}
        assert all(not c['events'] for c in (await browser.network_list_many(cursors=cursors))['captures'])
        await popup.screenshot(path=str(tmp_path / 'multitab-popup.png'))
        stopped = await browser.network_stop_many()
        assert not stopped['include_new_tabs']
        assert all(not c['active'] for c in stopped['captures'])
        fourth = (await browser.new_tab(url))['id']
        listing = await browser.network_list_many([fourth])
        assert not listing['captures'] and listing['failures']


@pytest.mark.asyncio
async def test_selected_context_future_scope_and_closed_target_plan_rejection():
    async with api_site() as (url, seen, _), await BrowserSession.launch(headless=True) as browser:
        first = (await browser.new_tab(url))['id']
        other_context = await browser._browser.new_context()
        other = await other_context.new_page()
        await other.goto(url)
        browser._sync_pages()
        await browser.network_start_many([first], include_new_tabs=True)
        isolated_future = await other_context.new_page()
        await isolated_future.goto(url)
        browser._sync_pages()
        isolated_id = browser._ids[isolated_future]
        assert (await browser.network_list_many([isolated_id]))['failures']
        plan = await browser.network_call(first, url=url + '/echo', method='POST', body='must not send')
        before = len(seen)
        await browser.close_tab(first)
        with pytest.raises(Exception, match='Unknown or closed tab'):
            await browser.network_execute(plan['plan_id'], approved=True)
        assert len(seen) == before


@pytest.mark.asyncio
async def test_native_disconnect_preserves_preexisting_tabs(tmp_path):
    # Launch an actual opted-in fixture browser, then attach via its public CDP
    # endpoint. This is not a claim of access to a personal logged-in profile.
    from pathlib import Path
    import os
    async with api_site() as (url, _, _), async_playwright() as playwright:
        env = dict(os.environ)
        library = os.environ.get('BROWSER_AGENT_LIBRARY_PATH')
        if library:
            env['LD_LIBRARY_PATH'] = library + ':' + env.get('LD_LIBRARY_PATH', '')
        fixture = await playwright.chromium.launch(headless=True, args=['--remote-debugging-port=0', '--enable-automation'], env=env)
        try:
            context = await fixture.new_context()
            page = await context.new_page()
            await page.goto(url)
            cdp = await fixture.new_browser_cdp_session()
            version = await cdp.send('Browser.getBrowserCommandLine')
            profile_arg = next(arg for arg in version['arguments'] if arg.startswith('--user-data-dir='))
            active = (Path(profile_arg.split('=', 1)[1]) / 'DevToolsActivePort').read_text().splitlines()
            endpoint = f'http://127.0.0.1:{active[0]}'
            session = await BrowserSession.connect(endpoint)
            tabs = await session.tabs()
            tab = next(t['id'] for t in tabs if t['url'] == url + '/')
            await session.network_start_many(include_new_tabs=True)
            await session._page(tab).click('#send')
            await page.screenshot(path=str(tmp_path / 'native-network-preexisting.png'))
            await session.close()
            assert fixture.is_connected() and not page.is_closed()
            assert await page.locator('h1').inner_text() == 'Quiet UI'
            await cdp.detach()
        finally:
            await fixture.close()
