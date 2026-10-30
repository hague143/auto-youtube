"""Export a real 900-second 720p movie using isolated speech/stock fixtures.

No paid synthesis or external stock requests. This tests duration, encoding,
scene switches and subtitle timing, not a provider's visual-search quality.
"""
import json
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'server'))
import pipeline
from timeline import estimated_words

output = ROOT / 'artifacts' / 'longform-check'
output.mkdir(parents=True, exist_ok=True)
started = time.perf_counter()
with tempfile.TemporaryDirectory(prefix='faceless-long-fixture-') as temporary:
    fixture = Path(temporary)
    audio = fixture / 'voice.wav'
    with wave.open(str(audio), 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        for _ in range(900):
            stream.writeframes(b'\x00\x00' * 16000)
    media = {}
    for color in ('red', 'blue'):
        path = fixture / (color + '.mp4')
        subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-f', 'lavfi', '-i',
                        f'color=c={color}:s=64x64:r=24:d=1', '-c:v', 'libx264', '-threads', '1',
                        '-preset', 'ultrafast', str(path)], capture_output=True, check=True)
        media[color] = path.read_bytes()
    scenes, words, colors = [], [], {}
    for index in range(90):
        marker = 'site' + chr(97 + index // 26) + chr(97 + index % 26)
        text = f'Rockets launch beside {marker}.'
        start, end = index * 10, (index + 1) * 10
        scenes.append({'text': text, 'start': start, 'end': end})
        words.extend(estimated_words(text, start + .1, end - .1))
        colors[pipeline.visual_query(text, 'Rocket launches')] = 'red' if index % 2 == 0 else 'blue'
    script = ' '.join(scene['text'] for scene in scenes)
    requests = []

    def provider(request):
        requests.append(str(request.url))
        if request.url.host == 'api.pexels.com' and request.url.path == '/videos/search':
            query = request.url.params['query']
            color = colors[query]
            return httpx.Response(200, json={'videos': [
                {'id': 'unrelated', 'url': 'https://www.pexels.com/video/waterfall-in-forest-1/', 'video_files': []},
                {'id': query, 'duration': 10, 'url': 'https://www.pexels.com/video/' + query.replace(' ', '-') + '/',
                 'user': {'name': 'Fixture creator'}, 'video_files': [{'file_type': 'video/mp4', 'width': 64,
                 'height': 64, 'link': f'https://videos.pexels.com/{color}.mp4'}]},
            ]})
        if request.url.host == 'videos.pexels.com':
            return httpx.Response(200, content=media[Path(request.url.path).stem])
        raise AssertionError('Unexpected external request in isolated fixture.')

    def progress(**changes):
        stage = changes.get('stage', '')
        if stage.startswith('Rendering scene ') and int(stage.split()[2].split('/')[0]) % 15 == 0:
            print(stage, flush=True)

    client = httpx.Client
    with patch.object(pipeline, 'narrate_script', return_value=pipeline.Narration(audio, 900, words, scenes, True)), \
            patch.object(pipeline.httpx, 'Client', side_effect=lambda **kwargs: client(transport=httpx.MockTransport(provider), **kwargs)):
        result = pipeline.render({'topic': 'Rocket launches', 'script': script, 'voice': 'elevenlabs',
                                  'duration': 900, 'format': 'landscape', 'visuals': 'stock',
                                  'captions': 'script', 'thumbnail': False,
                                  'keys': {'pexels': 'test-only-stock-fixture'}}, output, progress)
    probe = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json',
                            str(output / 'video.mp4')], capture_output=True, text=True, check=True)
    metadata = json.loads(probe.stdout)
    video = next(stream for stream in metadata['streams'] if stream['codec_type'] == 'video')
    assert video['codec_name'] == 'h264' and (video['width'], video['height']) == (1280, 720)
    assert int(video['nb_frames']) == 21600
    assert any(stream['codec_name'] == 'aac' for stream in metadata['streams'])
    assert abs(float(metadata['format']['duration']) - 900) < .1
    assert result['duration'] == 900 and len(result['scenes']) == 90
    assert result['scenes'][-1]['end'] == 900
    assert all(a['end'] == b['start'] for a, b in zip(result['scenes'], result['scenes'][1:]))
    assert all(scene['media_kind'] == 'video' for scene in result['scenes'])
    for second, color in ((1, 'red'), (11, 'blue'), (899, 'blue')):
        frame = subprocess.run(['ffmpeg', '-v', 'error', '-ss', str(second), '-i', str(output / 'video.mp4'),
                                '-frames:v', '1', '-vf', 'crop=2:2:20:20,format=rgb24', '-f', 'rawvideo', '-'],
                               capture_output=True, check=True).stdout
        red, _, blue = frame[:3]
        assert (red > 200 and blue < 30) if color == 'red' else (blue > 200 and red < 30)
    assert '00:14:59,900' in (output / 'captions.srt').read_text()
    assert not list(output.glob('.render-*'))
    assert not list(output.glob('stock-*'))
    assert 'test-only-stock-fixture' not in json.dumps(result)
    print('900-second 1280×720 H.264/AAC export passed: 90 stock scenes, 21,600 frames, correct first/next/final visuals, captions to the final scene, no cumulative timing drift, temporary files removed.')
    print(f'Fixture elapsed time on this cloud machine: {time.perf_counter() - started:.1f}s. This is not an Oracle capacity benchmark.')
