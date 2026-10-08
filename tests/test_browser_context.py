"""Additional adversarial browser fixtures, run only during integration verification."""
import os
from pathlib import Path
import pytest
from browser_automation.browser import BrowserSession, StaleObservationError, UnsafeActionError, ProtectedUrlError, ConsentRequiredError


@pytest.mark.asyncio
async def test_other_form_field_changes_block_unchanged_submit():
    async with await BrowserSession.launch(headless=True) as browser:
        tab = (await browser.new_tab())['id']
        page = browser._page(tab)
        await page.set_content('<form onsubmit="event.preventDefault();document.body.dataset.submitted=\'yes\'"><label>Recipient <input name="recipient" value="Alice"></label><label>Amount <input name="amount" value="10"></label><button>Submit transfer</button></form>')
        observation = await browser.observe(tab)
        target = next(e for e in observation['elements'] if e['name']=='Submit transfer')
        assert target['is_submit'] and target['input_type']=='submit'
        await page.locator('input[name=amount]').fill('900')
        with pytest.raises(StaleObservationError):
            await browser.act(tab,{'operation':'click','target':target['id'],'observation_id':observation['id']})
        assert await page.get_attribute('body','data-submitted') is None


@pytest.mark.asyncio
async def test_clipped_control_visible_portion_can_receive_click():
    async with await BrowserSession.launch(headless=True) as browser:
        tab = (await browser.new_tab())['id']
        page = browser._page(tab)
        await page.set_content('<div style="width:100px;height:80px;overflow:hidden"><button style="width:800px;height:60px" aria-label="Wide clipped button" onclick="document.body.dataset.clicked=\'yes\'">Button</button></div>')
        observation = await browser.observe(tab)
        target = next(e for e in observation['elements'] if e['name']=='Wide clipped button')
        await browser.act(tab,{'operation':'click','target':target['id'],'observation_id':observation['id']})
        assert await page.get_attribute('body','data-clicked') == 'yes'


@pytest.mark.asyncio
async def test_hidden_offscreen_and_clipped_iframes_are_not_advertised():
    async with await BrowserSession.launch(headless=True) as browser:
        tab = (await browser.new_tab())['id']
        page = browser._page(tab)
        await page.set_content('''<iframe style="display:none" srcdoc="<button>Hidden frame control</button>"></iframe><iframe style="position:absolute;top:3000px" srcdoc="<button>Offscreen frame control</button>"></iframe><div style="height:15px;width:250px;overflow:hidden"><iframe style="width:220px;height:200px" srcdoc="<button style='position:absolute;top:100px'>Clipped frame control</button>"></iframe></div><button>Visible control</button>''')
        await page.wait_for_load_state()
        observation = await browser.observe(tab)
        names = {e['name'] for e in observation['elements']}
        assert 'Visible control' in names
        assert not names & {'Hidden frame control','Offscreen frame control','Clipped frame control'}
        assert len(observation['limitations']) >= 2


@pytest.mark.asyncio
async def test_invalid_payload_does_not_invalidate_or_dispatch():
    async with await BrowserSession.launch(headless=True) as browser:
        tab = (await browser.new_tab())['id']
        page = browser._page(tab)
        await page.set_content('<input aria-label="Name"><button onclick="document.body.dataset.clicked=\'yes\'">Click</button>')
        observation = await browser.observe(tab)
        target = next(e for e in observation['elements'] if e['name']=='Name')
        with pytest.raises(UnsafeActionError):
            await browser.act(tab,{'operation':'fill','target':target['id'],'observation_id':observation['id'],'text':42})
        with pytest.raises(UnsafeActionError):
            await browser.act(tab,{'operation':'press','target':target['id'],'observation_id':observation['id'],'key':'NotAKey'})
        assert await page.evaluate('document.activeElement.tagName') == 'BODY'
        await browser.act(tab,{'operation':'fill','target':target['id'],'observation_id':observation['id'],'text':'Valid after rejected payload'})
        assert await page.locator('input').input_value() == 'Valid after rejected payload'


@pytest.mark.asyncio
async def test_hostile_page_tampering_cannot_substitute_target_or_bypass_guard():
    async with await BrowserSession.launch(headless=True) as browser:
        tab = (await browser.new_tab())['id']
        page = browser._page(tab)
        # Webpage tries to hijack globals, elementFromPoint, and any main-world snapshot objects
        await page.set_content('''
        <button id="real" onclick="document.body.dataset.real='clicked'">Real target</button>
        <button id="trap" onclick="document.body.dataset.trap='clicked'">Malicious trap</button>
        <script>
          // Attempt to hijack main-world globals
          document.elementFromPoint = () => document.getElementById('trap');
          window.__browserAutomationSnapshot_v1 = { fake: true };
          Object.freeze(Object.prototype);
        </script>
        ''')
        observation = await browser.observe(tab)
        # Isolated CDP world must not see main-world pollution or hijacked elementFromPoint
        real_target = next(e for e in observation['elements'] if e['name'] == 'Real target')
        await browser.act(tab, {'operation': 'click', 'target': real_target['id'], 'observation_id': observation['id']})
        assert await page.get_attribute('body', 'data-real') == 'clicked'
        assert await page.get_attribute('body', 'data-trap') is None


@pytest.mark.asyncio
async def test_protected_url_schemes_refused():
    async with await BrowserSession.launch(headless=True) as browser:
        with pytest.raises(UnsafeActionError):
            await browser.new_tab('file:///etc/passwd')
        with pytest.raises(UnsafeActionError):
            await browser.new_tab('javascript:alert(1)')
        tab = (await browser.new_tab())['id']
        page = browser._page(tab)
        await page.set_content('<a href="file:///etc/passwd">Leak secret</a><a href="http://127.0.0.1/safe">Safe link</a>')
        observation = await browser.observe(tab)
        leak_target = next(e for e in observation['elements'] if e['name'] == 'Leak secret')
        with pytest.raises(UnsafeActionError, match='protected URL'):
            await browser.act(tab, {'operation': 'click', 'target': leak_target['id'], 'observation_id': observation['id']})


@pytest.mark.asyncio
async def test_image_input_classified_as_submit_and_no_fill_operation():
    async with await BrowserSession.launch(headless=True) as browser:
        tab = (await browser.new_tab())['id']
        page = browser._page(tab)
        await page.set_content('<form action="/submit"><input type="image" src="about:blank" aria-label="Pay now"></form>')
        observation = await browser.observe(tab)
        target = next(e for e in observation['elements'] if e['name'] == 'Pay now')
        assert target['is_submit'] is True
        assert target['input_type'] == 'image'
        assert 'fill' not in target['operations']
        assert 'click' in target['operations']


@pytest.mark.asyncio
async def test_file_symlink_and_ancestor_traversal_refused(tmp_path):
    async with await BrowserSession.launch(headless=True) as browser:
        tab = (await browser.new_tab())['id']
        page = browser._page(tab)
        await page.set_content('<input type="file" aria-label="File upload">')
        observation = await browser.observe(tab)
        target = next(e for e in observation['elements'] if e['name'] == 'File upload')
        # Create outside secret and symlink inside tmp_path
        secret = tmp_path.parent / 'outside_secret.txt'
        secret.write_text('super-secret')
        symlink = tmp_path / 'symlink.txt'
        try:
            os.symlink(secret, symlink)
        except OSError:
            pytest.skip('Symlink creation unsupported on this environment')
        with pytest.raises(UnsafeActionError):
            await browser.upload(tab, observation['id'], target['id'], [str(symlink)], allowed_directory=str(tmp_path), approved=True)


@pytest.mark.asyncio
async def test_tab_url_api_and_failed_goto_cleanup():
    async with await BrowserSession.launch(headless=True) as browser:
        tab = (await browser.new_tab())['id']
        url = await browser.tab_url(tab)
        assert url == 'about:blank'
        initial_count = len(await browser.tabs())
        with pytest.raises(UnsafeActionError):
            await browser.new_tab('file:///invalid')
        assert len(await browser.tabs()) == initial_count
