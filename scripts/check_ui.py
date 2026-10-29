"""Exercise redesigned UI behavior against isolated API fixtures and local assets."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / 'artifacts'
OUTPUT.mkdir(exist_ok=True)

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(executable_path='/usr/bin/chromium', args=['--no-sandbox'])
    try:
        for width in (1440, 1024, 768, 600, 390, 320):
            page = browser.new_page(viewport={'width': width, 'height': 1000})
            errors, external = [], []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('request', lambda request: external.append(request.url) if not request.url.startswith('http://127.0.0.1:18002') and not request.url.startswith('blob:') else None)

            def api(route):
                path = route.request.url.split('/api/', 1)[1]
                if path == 'capabilities':
                    route.fulfill(json={'ffmpeg': True, 'local_voice': True, 'whisper': False})
                elif path == 'projects':
                    route.fulfill(json=[])
                else:
                    route.fulfill(status=404, json={'detail': 'Fixture unavailable.'})

            page.route('**/api/**', api)
            page.goto('http://127.0.0.1:18002')
            page.evaluate('document.fonts.ready')
            assert page.locator('#connection-label').inner_text() == 'Not connected'
            assert page.locator('.nav-button[aria-current="page"]').inner_text().strip().startswith('Create')
            if width in (1440, 390):
                page.screenshot(path=str(OUTPUT / f'ui-redesign-{width}.png'), full_page=True, animations='disabled')

            page.get_by_role('button', name='Space discoveries', exact=False).click()
            assert page.locator('#preview-title').inner_text() == 'Surprising discoveries about space'
            page.locator('#topic').fill('<img src=x onerror=alert(1)>')
            assert page.locator('#preview-title').inner_text() == '<img src=x onerror=alert(1)>'
            assert page.locator('#preview-title img').count() == 0
            page.locator('#format').select_option('landscape')
            assert page.locator('#preview-poster').get_attribute('data-format') == 'landscape'
            assert page.locator('#preview-format-tag').inner_text() == '16:9'
            page.locator('#duration').select_option('60')
            assert page.locator('#recipe-duration').inner_text() == '60 seconds'
            page.locator('#captions').select_option('none')
            assert page.locator('#preview-caption').is_hidden()
            page.get_by_role('button', name='Use a sample story', exact=False).click()
            assert page.locator('#voice').input_value() == 'local-en'
            assert page.locator('#visuals').input_value() == 'studio'
            assert page.locator('#script').input_value().startswith('Every great story')
            assert page.locator('#preview-caption').is_visible()
            assert page.locator('#script-count').inner_text().startswith('26 words')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'Create overflow at {width}'

            page.get_by_role('button', name='My videos', exact=True).click()
            assert page.locator('#page-label').inner_text() == 'My videos'
            page.get_by_role('button', name='Connect server', exact=False).click()
            assert page.locator('#page-label').inner_text() == 'Settings'
            assert page.locator('.nav-button[aria-current="page"]').inner_text() == 'Settings'
            for provider in ('Gemini', 'ElevenLabs', 'Pexels', 'Pixabay', 'Hugging Face'):
                field = page.get_by_label(provider + ' API key', exact=True)
                assert field.get_attribute('type') == 'password'
            page.locator('#token').fill('test-only-ui-fixture')
            page.locator('#key-gemini').fill('test-only-provider-fixture')
            page.get_by_label('Show credentials', exact=True).check()
            assert page.locator('#key-gemini').get_attribute('type') == 'text'
            page.get_by_label('Show credentials', exact=True).uncheck()
            page.get_by_role('button', name='Test connection', exact=True).click()
            page.get_by_role('status').filter(has_text='Local voice ready').wait_for()
            assert page.locator('#connection-label').inner_text() == 'Server connected'
            page.get_by_role('button', name='Apply settings', exact=False).click()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'Settings overflow at {width}'
            if width in (1440, 390):
                # Clear secrets before screenshots, even though they are only test fixtures.
                page.get_by_role('button', name='Clear credentials', exact=True).click()
                page.locator('#message').evaluate('(element) => element.hidden = true')
                page.evaluate('window.scrollTo(0, 0)')
                page.screenshot(path=str(OUTPUT / f'ui-settings-{width}.png'), full_page=True, animations='disabled')
            page.reload()
            page.get_by_role('button', name='Settings', exact=True).click()
            assert page.locator('#token').input_value() == ''
            assert not external, external
            assert not errors, errors
            print(f'UI passed at {width}px: live preview, sample story, safe text, navigation, keys, connection, refresh clearing, no overflow/external dependencies.')
            page.close()

        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        page.goto('http://127.0.0.1:18002')
        poster = page.locator('#preview-poster')
        poster.scroll_into_view_if_needed()
        before = poster.evaluate('(element) => getComputedStyle(element).transform')
        poster.hover(position={'x': 30, 'y': 80})
        page.wait_for_function('(before) => getComputedStyle(document.getElementById("preview-poster")).transform !== before', arg=before)
        page.close()
        print('Desktop pointer check passed: actual 3D preview transform responds to mouse movement.')

        page = browser.new_page(viewport={'width': 1440, 'height': 900}, reduced_motion='reduce')
        page.goto('http://127.0.0.1:18002')
        assert page.locator('.floating-tag').first.evaluate('(element) => getComputedStyle(element).animationName') == 'none'
        page.locator('#preview-poster').hover()
        page.mouse.move(1300, 760)
        assert page.locator('#preview-poster').evaluate('(element) => element.style.getPropertyValue("--tilt-x")') == ''
        page.close()
        print('Reduced-motion check passed: decorative motion and pointer tilt disabled.')

        context = browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
        page = context.new_page()
        page.goto('http://127.0.0.1:18002')
        assert not page.evaluate('matchMedia("(hover: hover) and (pointer: fine)").matches')
        page.locator('#preview-poster').tap()
        assert page.locator('#preview-poster').evaluate('(element) => element.style.getPropertyValue("--tilt-x")') == ''
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        context.close()
        print('Touch-device check passed: no pointer tilt, responsive layout without overflow.')
    finally:
        browser.close()
