"""Actual Chromium target-local exact pixel safety, never action replay."""
import asyncio

import pytest

from browser_automation.browser import BrowserSession, StaleObservationError


@pytest.mark.asyncio
async def test_unrelated_animation_outside_target_does_not_block():
    async with await BrowserSession.launch(headless=True) as browser:
        tab = (await browser.new_tab())['id']
        page = browser._page(tab)
        await page.set_content('''<canvas id="target" width="100" height="80" aria-label="Stable target" style="position:absolute;left:20px;top:20px" onclick="document.body.dataset.clicked='yes'"></canvas>
        <canvas id="animation" width="100" height="80" style="position:absolute;left:700px;top:20px"></canvas>
        <script>const c=document.querySelector('#target').getContext('2d');c.fillStyle='#123456';c.fillRect(0,0,100,80);
        let n=0;window.timer=setInterval(()=>{const c=document.querySelector('#animation').getContext('2d');c.fillStyle=n++%2?'#ff0000':'#0000ff';c.fillRect(0,0,100,80)},40);</script>''')
        observation = await browser.observe(tab, screenshot=True)
        target = next(e for e in observation['elements'] if e['name'] == 'Stable target')
        await asyncio.sleep(.2)
        await browser.act(tab, {'operation': 'click', 'target': target['id'], 'observation_id': observation['id']})
        assert await page.get_attribute('body', 'data-clicked') == 'yes'


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', ['pixels', 'geometry', 'overlay'])
async def test_changed_target_pixels_geometry_and_occlusion_rejected(mutation):
    async with await BrowserSession.launch(headless=True) as browser:
        tab = (await browser.new_tab())['id']
        page = browser._page(tab)
        await page.set_content('''<canvas width="120" height="80" aria-label="Guarded target" style="position:absolute;left:20px;top:20px" onclick="document.body.dataset.clicked='yes'"></canvas>
        <script>const c=document.querySelector('canvas').getContext('2d');c.fillStyle='#123456';c.fillRect(0,0,120,80)</script>''')
        observation = await browser.observe(tab, screenshot=True)
        target = next(e for e in observation['elements'] if e['name'] == 'Guarded target')
        if mutation == 'pixels':
            await page.evaluate("const c=document.querySelector('canvas').getContext('2d');c.fillStyle='#abcdef';c.fillRect(0,0,120,80)")
        elif mutation == 'geometry':
            await page.evaluate("document.querySelector('canvas').style.left='40px'")
        else:
            await page.evaluate("const d=document.createElement('div');d.style='position:absolute;left:20px;top:20px;width:120px;height:80px;background:black;z-index:100';document.body.append(d)")
        with pytest.raises(StaleObservationError) as error:
            await browser.act(tab, {'operation': 'click', 'target': target['id'], 'observation_id': observation['id']})
        assert 'changed' in str(error.value).lower()
        if mutation == 'pixels':
            assert error.value.diagnostic['code'] == 'target_pixels_changed'
            assert error.value.diagnostic['changed_region']['width'] > 0
        assert await page.get_attribute('body', 'data-clicked') is None


@pytest.mark.asyncio
async def test_drag_checks_destination_pixels_not_only_source():
    async with await BrowserSession.launch(headless=True) as browser:
        tab = (await browser.new_tab())['id']
        page = browser._page(tab)
        await page.set_content('''<canvas id="source" width="80" height="80" aria-label="Source" style="position:absolute;left:20px;top:20px"></canvas>
        <canvas id="destination" width="80" height="80" aria-label="Destination" style="position:absolute;left:300px;top:20px"></canvas>
        <script>for(const e of document.querySelectorAll('canvas')){const c=e.getContext('2d');c.fillStyle='#123456';c.fillRect(0,0,80,80)}</script>''')
        observation = await browser.observe(tab, screenshot=True)
        targets = {e['name']: e['id'] for e in observation['elements'] if e['role'] == 'canvas'}
        await page.evaluate("const c=document.querySelector('#destination').getContext('2d');c.fillStyle='#abcdef';c.fillRect(0,0,80,80)")
        with pytest.raises(StaleObservationError) as error:
            await browser.act(tab, {'operation': 'drag', 'target': targets['Source'], 'to_target': targets['Destination'], 'observation_id': observation['id']})
        assert error.value.diagnostic['code'] == 'target_pixels_changed'
        assert error.value.diagnostic['target_bounds']['x'] == 300
