"""Real local-voice/browser smoke test. Uses only its own temporary container."""
import json
import os
import secrets
import subprocess
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parent.parent
output = root / 'artifacts'
output.mkdir(exist_ok=True)
name = 'faceless-validation-' + secrets.token_hex(4)
token = secrets.token_urlsafe(48)
environment = os.environ.copy()
environment['STUDIO_ACCESS_TOKEN'] = token
started = False
try:
    subprocess.run(['docker', 'run', '--rm', '-d', '--name', name, '-p', '127.0.0.1:18000:8000',
                    '-e', 'STUDIO_ACCESS_TOKEN', os.getenv('STUDIO_TEST_IMAGE', 'faceless-studio:oracle-test')],
                   env=environment, check=True, capture_output=True, text=True)
    started = True
    for attempt in range(40):
        try:
            response = httpx.get('http://127.0.0.1:18000/health', timeout=2)
            if response.status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(.5)
    else:
        logs = subprocess.run(['docker', 'logs', '--tail', '30', name], capture_output=True, text=True)
        raise RuntimeError('Temporary container did not become healthy. ' + logs.stderr)
    assert httpx.get('http://127.0.0.1:18000/api/projects').status_code == 401
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path='/usr/bin/chromium', args=['--no-sandbox'])
        try:
            page = browser.new_page(viewport={'width': 1366, 'height': 900}, accept_downloads=True)
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto('http://127.0.0.1:18000')
            page.wait_for_function('document.documentElement.dataset.settingsReady === "true"')
            page.get_by_role('button', name='Settings', exact=True).click()
            for provider in ('Gemini', 'ElevenLabs', 'Pexels', 'Pixabay', 'Hugging Face'):
                assert page.get_by_label(provider + ' API key', exact=False).get_attribute('type') == 'password'
            page.locator('#token').fill(token)
            page.get_by_role('button', name='Test connection', exact=True).click()
            page.get_by_role('status').filter(has_text='Local voice ready').wait_for()
            page.get_by_role('button', name='Save settings', exact=True).click()
            page.get_by_role('status').filter(has_text='Settings saved in this browser').wait_for()
            page.get_by_role('button', name='Create', exact=True).click()
            page.locator('#voice').select_option('local-en')
            page.locator('#topic').fill('A small idea')
            page.locator('#script').fill('A great story begins with one small idea. Turn your curiosity into something worth sharing.')
            page.get_by_role('button', name='Create video', exact=False).click()
            page.get_by_role('button', name='Download MP4', exact=True).wait_for(timeout=180000)
            page.get_by_role('button', name='Preview', exact=True).click()
            page.locator('video').wait_for()
            page.wait_for_function("document.querySelector('video').readyState >= 1")
            with page.expect_download() as download:
                page.get_by_role('button', name='Download MP4', exact=True).click()
            video = output / 'validated-oracle-package.mp4'
            download.value.save_as(video)
            probe = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(video)], capture_output=True, text=True, check=True)
            metadata = json.loads(probe.stdout)
            assert {s['codec_name'] for s in metadata['streams']} >= {'h264', 'aac'}
            assert 1 < float(metadata['format']['duration']) <= 15.5
            project = httpx.get('http://127.0.0.1:18000/api/projects',
                                headers={'Authorization': 'Bearer ' + token}).json()[0]
            assert project['voice_used'] == 'local-en' and not project['word_aligned']
            scenes = project['scenes']
            assert len(scenes) >= 2 and scenes[0]['start'] == 0
            assert abs(scenes[-1]['end'] - project['duration']) < .05
            assert all(a['end'] == b['start'] for a, b in zip(scenes, scenes[1:]))
            assert ' '.join(scene['text'] for scene in scenes) == project['script']
            page.screenshot(path=str(output / 'validated-desktop.png'), full_page=True)
            page.reload()
            page.wait_for_function('document.documentElement.dataset.settingsReady === "true"')
            page.get_by_role('button', name='Settings', exact=True).click()
            assert page.locator('#token').input_value() == token
            page.get_by_role('button', name='My videos', exact=True).click()
            page.get_by_role('button', name='A small idea', exact=False).wait_for()
            page.set_viewport_size({'width': 390, 'height': 844})
            page.get_by_role('button', name='A small idea', exact=False).click()
            page.get_by_role('button', name='Download MP4', exact=True).wait_for()
            assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
            page.screenshot(path=str(output / 'validated-mobile.png'), full_page=True)
            page.on('dialog', lambda dialog: dialog.accept())
            page.get_by_role('button', name='Delete project and files', exact=True).click()
            page.get_by_role('status').filter(has_text='Project deleted.').wait_for()
            assert not errors, errors
            print('Container + browser smoke passed: auth, settings, real eSpeak narration, measured scene timeline, sequential FFmpeg MP4, preview, download, refresh credential persistence, persistent project list, mobile layout, deletion.')
            print('Export duration:', metadata['format']['duration'])
        finally:
            browser.close()
finally:
    if started:
        subprocess.run(['docker', 'stop', '--time', '30', name], check=False, capture_output=True)
