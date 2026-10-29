"""Verify the static Vercel export connects directly to a separate HTTPS backend."""
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
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
origin = 'http://127.0.0.1:' + str(server.server_port)
requests = []
projects = []
fixture = dict(id='b' * 24, title='Vercel connection fixture', stage='Ready to share', status='complete',
               duration=6.37, format='portrait', caption_mode='script', warnings=[], credits=[], script='Fixture narration.')
try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path='/usr/bin/chromium', args=['--no-sandbox'])
        page = browser.new_page(accept_downloads=True)
        errors, same_origin_api = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda request: same_origin_api.append(request.url) if request.url.startswith(origin + '/api/') else None)

        def backend(route):
            request = route.request
            headers = {'Access-Control-Allow-Origin': origin, 'Access-Control-Allow-Headers': 'Authorization, Content-Type',
                       'Access-Control-Allow-Methods': 'GET, POST, DELETE, OPTIONS'}
            if request.method == 'OPTIONS':
                route.fulfill(status=204, headers=headers)
                return
            assert request.headers.get('authorization') == 'Bearer test-only-vercel-fixture'
            requests.append(request.url)
            endpoint = request.url.removeprefix('https://backend.example')
            if endpoint == '/api/capabilities':
                route.fulfill(json={'ffmpeg': True, 'local_voice': True}, headers=headers)
            elif endpoint == '/api/projects' and request.method == 'GET':
                route.fulfill(json=projects, headers=headers)
            elif endpoint == '/api/projects' and request.method == 'POST':
                payload = request.post_data_json
                assert payload['voice'] == 'elevenlabs'
                assert payload['elevenlabs_voice_id'] == 'fixtureVoice'
                assert payload['keys']['elevenlabs'] == 'test-only-elevenlabs-key'
                projects.append(fixture)
                route.fulfill(status=202, json=fixture, headers=headers)
            elif endpoint == '/api/verify-key':
                assert request.post_data_json == {'provider': 'elevenlabs', 'key': 'test-only-elevenlabs-key'}
                route.fulfill(json={'ok': True, 'voices': [{'id': 'fixtureVoice', 'name': 'Fixture voice'}]}, headers=headers)
            elif endpoint.endswith('/files/video.mp4'):
                route.fulfill(body=(ROOT / 'artifacts/validated-oracle-package.mp4').read_bytes(), content_type='video/mp4', headers=headers)
            else:
                route.fulfill(status=404, json={'detail': 'Fixture not found.'}, headers=headers)

        page.route('https://backend.example/**', backend)
        page.goto(origin)
        page.get_by_role('button', name='Settings', exact=True).click()
        assert page.locator('#server').input_value() == ''
        page.locator('#token').fill('test-only-vercel-fixture')
        page.locator('#server').fill('http://147.224.195.12')
        page.get_by_role('button', name='Test connection', exact=True).click()
        page.get_by_role('status').filter(has_text='Use HTTPS').wait_for()
        assert not requests
        page.locator('#server').fill('https://backend.example/')
        page.get_by_role('button', name='Test connection', exact=True).click()
        page.get_by_role('status').filter(has_text='Local voice ready').wait_for()
        page.get_by_label('ElevenLabs API key', exact=True).fill('test-only-elevenlabs-key')
        page.get_by_role('button', name='Test ElevenLabs key', exact=True).click()
        page.get_by_role('status').filter(has_text='ElevenLabs key accepted').wait_for()
        assert page.get_by_label('ElevenLabs Voice ID', exact=True).input_value() == 'fixtureVoice'
        page.get_by_role('button', name='Apply settings', exact=False).click()
        page.get_by_role('button', name='Create', exact=True).click()
        assert page.locator('#voice').input_value() == 'elevenlabs'
        page.locator('#topic').fill(fixture['title'])
        page.locator('#script').fill('This tests a separate backend connection.')
        page.get_by_role('button', name='Create video', exact=False).click()
        page.get_by_role('button', name='Download MP4', exact=True).wait_for()
        with page.expect_download() as download:
            page.get_by_role('button', name='Download MP4', exact=True).click()
        assert download.value.failure() is None
        assert requests and all(url.startswith('https://backend.example/api/') for url in requests)
        assert not same_origin_api
        page.reload()
        page.get_by_role('button', name='Settings', exact=True).click()
        assert page.locator('#token').input_value() == ''
        assert page.get_by_label('ElevenLabs API key', exact=True).input_value() == ''
        assert page.locator('#server').input_value() == ''
        assert not errors, errors
        browser.close()
        print('Vercel static export browser check passed: ElevenLabs key test, account Voice ID selection, separate HTTPS backend, bearer auth, create/status, MP4 download, insecure endpoint rejection, session clearing; no API calls to the frontend origin.')
finally:
    server.shutdown()
    server.server_close()
