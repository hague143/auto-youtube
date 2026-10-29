import io
import json
import subprocess
import sys
import wave
from pathlib import Path

import httpx
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parents[1]))
import pipeline


class UnreadBody(httpx.SyncByteStream):
    def __iter__(self):
        raise AssertionError('A known oversized response body must not be read.')
        yield b''


@pytest.fixture
def video_bytes(tmp_path):
    target = tmp_path / 'fixture.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                    'color=c=blue:s=64x64:r=24:d=2', '-c:v', 'libx264', '-threads', '1',
                    '-preset', 'ultrafast', str(target)], check=True, capture_output=True)
    return target.read_bytes()


def test_large_hd_is_skipped_without_reading_and_sd_succeeds(tmp_path, video_bytes):
    urls = []
    def provider(request):
        urls.append(request.url.path)
        if request.url.path == '/videos/search':
            return httpx.Response(200, json={'videos': [{'id': 1, 'duration': 8, 'url': 'https://www.pexels.com/video/1',
                'video_files': [
                    {'file_type': 'video/mp4', 'width': 1280, 'height': 720, 'link': 'https://videos.pexels.com/hd.mp4'},
                    {'file_type': 'video/mp4', 'width': 640, 'height': 360, 'link': 'https://videos.pexels.com/sd.mp4'}]}]})
        if request.url.path == '/hd.mp4':
            return httpx.Response(200, headers={'Content-Length': '99999999'}, stream=UnreadBody())
        return httpx.Response(200, content=video_bytes)
    budget = {'remaining': 5 * 1024 * 1024, 'image_reserve': 100000}
    warnings = []
    with httpx.Client(transport=httpx.MockTransport(provider)) as client:
        asset, credit = pipeline.stock(client, {'format': 'portrait', 'keys': {'pexels': 'fixture-key'}},
                                       tmp_path, 'ocean', 0, set(), budget, warnings=warnings)
    assert asset.read_bytes() == video_bytes and credit['kind'] == 'video'
    assert urls == ['/videos/search', '/hd.mp4', '/sd.mp4']
    assert budget['remaining'] == 5 * 1024 * 1024 - len(video_bytes)
    assert len(warnings) == 1


def test_partial_failed_transfers_count_against_budget_and_are_removed(tmp_path):
    class ChunkedBody(httpx.SyncByteStream):
        def __iter__(self):
            yield b'1234'
            yield b'567890'
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=ChunkedBody()))) as client:
        budget = {'remaining': 100}
        with pytest.raises(pipeline.StockAssetSkipped):
            pipeline.download(client, 'https://videos.pexels.com/fixture.mp4', tmp_path / 'partial.mp4',
                              budget=budget, max_bytes=8)
    assert budget['remaining'] == 90
    assert not (tmp_path / 'partial.mp4').exists()


def test_pexels_failure_uses_pixabay_video_and_disables_repeated_failed_api(tmp_path, video_bytes):
    urls = []
    def provider(request):
        urls.append((request.url.host, request.url.path))
        if request.url.host == 'api.pexels.com':
            return httpx.Response(429, json={'error': 'fixture-rate-limit'})
        if request.url.path == '/api/videos/':
            assert request.url.params['key'] == 'pixabay-fixture-key'
            query = request.url.params['q']
            return httpx.Response(200, json={'hits': [{'id': query, 'duration': 6, 'user': 'Fixture',
                'pageURL': 'https://pixabay.com/videos/fixture/', 'videos': {
                    'large': {'width': 1920, 'height': 1080, 'size': 999999999, 'url': 'https://cdn.pixabay.com/large.mp4'},
                    'small': {'width': 640, 'height': 360, 'size': len(video_bytes), 'url': 'https://cdn.pixabay.com/small.mp4'}}}]})
        assert 'authorization' not in request.headers
        return httpx.Response(200, content=video_bytes)
    data = {'format': 'portrait', 'keys': {'pexels': 'pexels-fixture-key', 'pixabay': 'pixabay-fixture-key'}}
    budget, used, warnings = {'remaining': 5 * 1024 * 1024}, set(), []
    with httpx.Client(transport=httpx.MockTransport(provider)) as client:
        for index, query in enumerate(('ocean', 'space')):
            asset, credit = pipeline.stock(client, data, tmp_path, query, index, used, budget, warnings=warnings)
            assert credit['provider'] == 'Pixabay' and credit['kind'] == 'video'
            assert asset.suffix == '.mp4'
    assert urls.count(('api.pexels.com', '/videos/search')) == 1
    assert urls.count(('pixabay.com', '/api/videos/')) == 2
    assert not any(path == '/large.mp4' for host, path in urls)
    assert any('unavailable' in warning for warning in warnings)


def test_search_cache_is_24_hours_and_separated_by_key_without_persisting_it(tmp_path, monkeypatch):
    now = [100000.0]
    monkeypatch.setattr(pipeline.time, 'time', lambda: now[0])
    requests = []
    def provider(request):
        requests.append(request)
        return httpx.Response(200, json={'videos': [], 'reflected': request.headers['Authorization']})
    first_key, second_key = 'sentinel-private-key-one', 'sentinel-private-key-two'
    with httpx.Client(transport=httpx.MockTransport(provider)) as client:
        for api_key in (first_key, first_key, second_key):
            pipeline.stock_search(client, 'pexels', 'https://api.pexels.com/videos/search',
                                  {'keys': {'pexels': api_key}}, {'query': 'ocean'},
                                  {'remaining': 1000}, [], tmp_path)
        assert len(requests) == 2
        now[0] += 86401
        pipeline.stock_search(client, 'pexels', 'https://api.pexels.com/videos/search',
                              {'keys': {'pexels': first_key}}, {'query': 'ocean'},
                              {'remaining': 1000}, [], tmp_path)
    assert len(requests) == 3
    content = ''.join(file.read_text() for file in tmp_path.glob('*.json'))
    assert first_key not in content and second_key not in content
    assert len(list(tmp_path.glob('*.json'))) == 2


@pytest.mark.parametrize('exhausted', [False, True])
def test_render_finishes_with_photos_or_cards_when_stock_cannot_fit(tmp_path, monkeypatch, exhausted):
    audio = tmp_path / 'voice.wav'
    with wave.open(str(audio), 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b'\x00\x00' * 32000)
    scenes = [{'text': 'Rockets.', 'start': 0, 'end': 1}, {'text': 'Coral.', 'start': 1, 'end': 2}]
    words = [{'text': 'Rockets.', 'start': .1, 'end': .8}, {'text': 'Coral.', 'start': 1.1, 'end': 1.8}]
    monkeypatch.setattr(pipeline, 'narration', lambda *args: pipeline.Narration(audio, 2, words, scenes, True))
    monkeypatch.setattr(pipeline, 'stock_budget', lambda folder: {
        'remaining': 0 if exhausted else 30000, 'asset_limit': 5000, 'image_reserve': 16000})
    photo = io.BytesIO()
    Image.new('RGB', (64, 64), 'blue').save(photo, format='JPEG')
    requests = []
    def provider(request):
        requests.append(request)
        assert not exhausted, 'An exhausted budget must not make stock HTTP requests.'
        if request.url.path == '/videos/search':
            return httpx.Response(200, json={'videos': [{'id': 1, 'duration': 4, 'video_files': [
                {'file_type': 'video/mp4', 'width': 1280, 'height': 720, 'link': 'https://videos.pexels.com/oversized.mp4'}]}]})
        if request.url.path == '/oversized.mp4':
            return httpx.Response(200, headers={'Content-Length': '20000'}, stream=UnreadBody())
        if request.url.path == '/v1/search':
            identity = request.url.params['query']
            return httpx.Response(200, json={'photos': [{'id': identity, 'url': 'https://www.pexels.com/photo/fixture/',
                'src': {'large': 'https://images.pexels.com/fixture.jpg'}}]})
        return httpx.Response(200, content=photo.getvalue())
    original = httpx.Client
    monkeypatch.setattr(pipeline.httpx, 'Client', lambda **kwargs: original(transport=httpx.MockTransport(provider), **kwargs))
    folder = tmp_path / 'project'
    result = pipeline.render({'topic': 'Rockets and coral', 'script': 'Rockets. Coral.', 'voice': 'en-US-AriaNeural',
                              'duration': 15, 'format': 'square', 'visuals': 'stock', 'captions': 'script',
                              'thumbnail': False, 'keys': {'pexels': 'fixture-key'}}, folder, lambda **changes: None)
    assert result['duration'] == 2 and (folder / 'video.mp4').is_file()
    if exhausted:
        assert not requests and all(scene['source'] == 'Studio' for scene in result['scenes'])
        assert any('allowance' in warning for warning in result['warnings'])
    else:
        assert all(scene['source'] == 'Pexels' for scene in result['scenes'])
        assert all(credit['kind'] == 'photo' for credit in result['credits'])
        assert any('matching photos' in warning for warning in result['warnings'])


def test_stock_budget_preserves_disk_headroom_and_bounds_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv('STUDIO_STOCK_TOTAL_MB', '999999')
    monkeypatch.setenv('STUDIO_STOCK_ASSET_MB', '999999')
    usage = type('Usage', (), {'free': (512 + 20) * 1024 * 1024})()
    monkeypatch.setattr(pipeline.shutil, 'disk_usage', lambda folder: usage)
    budget = pipeline.stock_budget(tmp_path)
    assert budget['remaining'] == 20 * 1024 * 1024
    assert budget['asset_limit'] == 256 * 1024 * 1024
