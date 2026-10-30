"""Real Chromium checks: durable settings, safe rendering, edit and erase."""
import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(ROOT / 'web' / 'dist')))
threading.Thread(target=server.serve_forever, daemon=True).start()
origin = f'http://127.0.0.1:{server.server_port}'
backend = 'https://backend.example'
token = 'test-only-persistent-server-token'
keys = {name: 'test-only-saved-' + name for name in ('gemini', 'elevenlabs', 'pexels', 'pixabay', 'huggingface')}
requests, projects = [], []
try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path='/usr/bin/chromium', args=['--no-sandbox'])
        context = browser.new_context()
        errors = []
        context.on('page', lambda page: page.on('pageerror', lambda error: errors.append(str(error))))

        def api(route):
            request = route.request
            cors = {'Access-Control-Allow-Origin': origin, 'Access-Control-Allow-Headers': 'Authorization, Content-Type',
                    'Access-Control-Allow-Methods': 'GET, POST, DELETE, OPTIONS'}
            if request.method == 'OPTIONS':
                route.fulfill(status=204, headers=cors)
                return
            requests.append(request)
            assert request.headers['authorization'] in ('Bearer ' + token, 'Bearer changed-test-only-token')
            if request.url.endswith('/api/capabilities'):
                route.fulfill(json={'ffmpeg': True, 'max_duration': 900}, headers=cors)
            elif request.url.endswith('/api/projects') and request.method == 'POST':
                body = request.post_data_json
                assert body['duration'] == 900 and body['format'] == 'landscape'
                assert body['voice'] == 'elevenlabs' and body['elevenlabs_voice_id'] == 'savedVoice'
                assert body['keys'] == keys and len(body['script']) > 6000
                project = dict(id='a' * 24, title=body['topic'], status='complete', stage='Ready to share',
                               format='landscape', duration=900, caption_mode='script', progress=100,
                               script=body['script'], warnings=[], credits=[], scenes=[{
                                   'start': 0, 'end': 8, 'text': '<img src=x onerror=alert(1)>',
                                   'query': '<script>alert(1)</script>', 'source': 'Pexels',
                                   'media_kind': 'video', 'source_url': 'javascript:alert(1)'}])
                projects.append(project)
                route.fulfill(status=202, json=project, headers=cors)
            elif request.url.endswith('/api/projects'):
                route.fulfill(json=projects, headers=cors)
            else:
                route.fulfill(status=404, json={'detail': 'Fixture file unavailable.'}, headers=cors)

        context.route(backend + '/**', api)
        page = context.new_page()
        page.goto(origin)
        page.wait_for_function('document.documentElement.dataset.settingsReady === "true"')
        page.get_by_role('button', name='Settings', exact=True).click()
        page.locator('#server').fill(backend)
        page.locator('#token').fill(token)
        for name, value in keys.items():
            page.locator('#key-' + name).fill(value)
        page.locator('#elevenlabs-voice').fill('savedVoice')
        page.get_by_role('button', name='Save settings', exact=True).click()
        page.get_by_role('status').filter(has_text='Settings saved in this browser').wait_for()
        page.get_by_role('button', name='Create', exact=True).click()
        page.locator('#duration').select_option('900')
        page.locator('#format').select_option('landscape')
        page.locator('#visuals').select_option('stock')
        page.wait_for_function('async () => (await STUDIO_SETTINGS.load()).preferences.duration === "900"')
        page.reload()
        page.wait_for_function('document.documentElement.dataset.settingsReady === "true"')
        assert page.locator('#duration').input_value() == '900', {'message': page.locator('#message').inner_text(), 'stored_preferences': page.evaluate('async () => (await STUDIO_SETTINGS.load())?.preferences')}
        assert page.locator('#format').input_value() == 'landscape'
        assert page.locator('#visuals').input_value() == 'stock'
        page.get_by_role('button', name='Settings', exact=True).click()
        assert page.locator('#server').input_value() == backend
        assert page.locator('#token').input_value() == token
        assert page.locator('#elevenlabs-voice').input_value() == 'savedVoice'
        for name, value in keys.items():
            assert page.locator('#key-' + name).input_value() == value
            assert page.locator('#key-' + name).get_attribute('type') == 'password'
        assert page.evaluate('localStorage.length === 0 && sessionStorage.length === 0')
        stored = page.evaluate('''async () => {
          const db = await new Promise((resolve,reject) => {const q=indexedDB.open('faceless-studio-settings',1);q.onsuccess=()=>resolve(q.result);q.onerror=reject});
          const record = await new Promise((resolve,reject) => {const q=db.transaction('settings').objectStore('settings').get('connection');q.onsuccess=()=>resolve(q.result);q.onerror=reject});
          return {extractable:record.key.extractable, raw:new TextDecoder().decode(record.cipher), fields:Object.keys(record)};
        }''')
        assert not stored['extractable'] and sorted(stored['fields']) == ['cipher', 'iv', 'key']
        assert all(secret not in stored['raw'] for secret in [token, *keys.values()])
        # A second tab in the same browser profile restores the connection too.
        page.close()
        page = context.new_page()
        page.goto(origin)
        page.wait_for_function('document.documentElement.dataset.settingsReady === "true"')
        page.locator('#topic').fill('A fifteen-minute story')
        page.locator('#script').fill('A rocket launches above the clouds. ' * 200)
        page.get_by_role('button', name='Create video', exact=False).click()
        page.get_by_role('button', name='Download MP4', exact=True).wait_for()
        page.get_by_text('Scene timeline · 1 scenes', exact=True).click()
        assert page.locator('.scene-timeline img, .scene-timeline script, .scene-timeline a').count() == 0
        assert '<img src=x' in page.locator('.scene-timeline').inner_text()
        page.get_by_role('button', name='Settings', exact=True).click()
        page.locator('#token').fill('changed-test-only-token')
        page.get_by_role('button', name='Save settings', exact=True).click()
        page.get_by_role('status').filter(has_text='Settings saved in this browser').wait_for()
        page.reload()
        page.wait_for_function('document.documentElement.dataset.settingsReady === "true"')
        page.get_by_role('button', name='Settings', exact=True).click()
        assert page.locator('#token').input_value() == 'changed-test-only-token'
        page.get_by_role('button', name='Clear saved settings', exact=True).click()
        page.get_by_role('status').filter(has_text='cleared from this browser').wait_for()
        page.reload()
        page.wait_for_function('document.documentElement.dataset.settingsReady === "true"')
        assert page.locator('#duration').input_value() == '15'
        page.get_by_role('button', name='Settings', exact=True).click()
        assert page.locator('#server').input_value() == '' and page.locator('#token').input_value() == ''
        assert all(page.locator('#key-' + name).input_value() == '' for name in keys)
        assert page.evaluate('async () => await STUDIO_SETTINGS.load()') is None
        assert not errors, errors
        assert any(request.method == 'POST' for request in requests)
        context.close()
        browser.close()
        print('Browser persistence passed: all keys/server/Voice ID, refresh, reopened tab, preferences, changed token, encrypted browser record, complete erasure, 900-second create payload, safe scene timeline.')
finally:
    server.shutdown()
    server.server_close()
