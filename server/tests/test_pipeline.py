import json
import subprocess
import sys
import wave
from pathlib import Path

import httpx
import aiohttp
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
import pipeline
from timeline import captions_from_words, scenes_from_words, split_script, visual_query


def write_audio(path, seconds):
    with wave.open(str(path), 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b'\x00\x00' * round(seconds * 16000))


def test_scene_boundaries_follow_sentences_and_word_times():
    words = [{'text': text, 'start': start, 'end': end} for text, start, end in [
        ('Rockets', .1, .4), ('launch.', .45, .7), ('Coral', 1.1, 1.4),
        ('reefs', 1.5, 1.9), ('grow.', 2.0, 2.4)]]
    scenes = scenes_from_words(words, 2.8, 'Rockets launch. Coral reefs grow.')
    assert [(scene['start'], scene['end']) for scene in scenes] == [(0, 1.1), (1.1, 2.8)]
    assert captions_from_words(words, scenes) == [(.1, .7, 'Rockets launch.'), (1.1, 2.4, 'Coral reefs grow.')]
    assert visual_query(scenes[0]['text'], 'Unrelated topic') == 'Rockets launch'
    assert 'Coral' in visual_query(scenes[1]['text'], 'Unrelated topic')


def test_long_sentences_keep_all_words_and_short_shots():
    text = ' '.join('word' + str(index) for index in range(40))
    assert ' '.join(split_script(text)) == text
    words = [{'text': token, 'start': index * .5, 'end': index * .5 + .4}
             for index, token in enumerate(text.split())]
    scenes = scenes_from_words(words, 20, text)
    assert ' '.join(scene['text'] for scene in scenes) == text
    assert all(scene['end'] - scene['start'] <= 4.5 for scene in scenes)
    assert all(a['end'] == b['start'] for a, b in zip(scenes, scenes[1:]))


def test_natural_voice_failure_never_silently_uses_espeak(tmp_path, monkeypatch):
    import edge_tts
    def unavailable(*args, **kwargs):
        raise RuntimeError('sentinel-private-provider-value')
    monkeypatch.setattr(edge_tts, 'Communicate', unavailable)
    warnings = []
    with pytest.raises(pipeline.PipelineError, match='Natural voice') as error:
        pipeline.narration('A short story.', 'en-US-AriaNeural', tmp_path, warnings)
    assert 'sentinel-private' not in str(error.value)
    assert not (tmp_path / 'voice.wav').exists()
    assert not warnings


@pytest.mark.parametrize(('status', 'message'), [(403, 'HTTP 403'), (429, 'rate limited'), (503, 'HTTP 503')])
def test_voice_service_errors_report_status_without_request_secrets(tmp_path, monkeypatch, status, message):
    import edge_tts
    def unavailable(*args, **kwargs):
        raise aiohttp.WSServerHandshakeError(None, (), status=status, message='sentinel-private-value')
    monkeypatch.setattr(edge_tts, 'Communicate', unavailable)
    with pytest.raises(pipeline.PipelineError, match=message) as error:
        pipeline.narration('A short story.', 'en-US-AriaNeural', tmp_path, [])
    assert 'sentinel-private' not in str(error.value)


def test_voice_timeout_is_distinct_from_service_rejection():
    assert 'timed out' in pipeline.natural_voice_error(TimeoutError('sentinel-private-value'))
    assert 'sentinel-private' not in pipeline.natural_voice_error(TimeoutError('sentinel-private-value'))


def test_natural_voice_preserves_provider_word_boundaries(tmp_path, monkeypatch):
    import edge_tts
    class Speaker:
        def __init__(self, script, voice, **kwargs):
            assert kwargs['boundary'] == 'WordBoundary'
        async def save(self, audio, metadata):
            write_audio(audio, 2)
            events = [{'type': 'WordBoundary', 'text': text, 'offset': offset, 'duration': duration}
                      for text, offset, duration in [('Rockets.', 2_000_000, 4_000_000),
                                                    ('Coral.', 11_000_000, 6_000_000)]]
            Path(metadata).write_text('\n'.join(json.dumps(event) for event in events))
    monkeypatch.setattr(edge_tts, 'Communicate', Speaker)
    speech = pipeline.narration('Rockets. Coral.', 'en-US-AriaNeural', tmp_path, [])
    assert speech.aligned
    assert speech.words[1]['text'] == 'Coral.'
    assert speech.words[1]['start'] == pytest.approx(1.1)
    assert speech.words[1]['end'] == pytest.approx(1.7)
    assert speech.scenes[0]['end'] == speech.scenes[1]['start'] == 1.1


def test_offline_scenes_use_measured_audio_not_equal_shares(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline.shutil, 'which', lambda binary: '/fixture/espeak-ng')
    original = subprocess.run
    lengths = iter([.6, 1.4])
    def speak(args, **kwargs):
        if args[0] == '/fixture/espeak-ng':
            write_audio(Path(args[args.index('-w') + 1]), next(lengths))
            return subprocess.CompletedProcess(args, 0)
        return original(args, **kwargs)
    monkeypatch.setattr(pipeline.subprocess, 'run', speak)
    speech = pipeline.narration('Rockets launch. Coral reefs grow.', 'local-en', tmp_path, [])
    assert not speech.aligned
    assert speech.duration == 2
    assert [(s['start'], s['end']) for s in speech.scenes] == [(0, .6), (.6, 2)]
    assert pipeline.audio_duration(speech.path) == 2


def test_bad_partial_gemini_plan_reverts_to_each_scenes_keywords(monkeypatch):
    scenes = [{'text': 'Rockets launch.'}, {'text': 'Coral reefs grow.'}]
    monkeypatch.setattr(pipeline, 'gemini', lambda *args, **kwargs: json.dumps([
        {'id': 0, 'query': 'wrong replacement'}, {'id': 99, 'query': 'invalid scene'}]))
    warnings = []
    queries = pipeline.scene_queries(None, {'topic': 'Unrelated topic', 'keys': {'gemini': 'fixture'}}, scenes, warnings)
    assert queries == ['Rockets launch', 'Coral reefs grow']
    assert len(warnings) == 1


def test_stock_download_budget_removes_partial_files(tmp_path):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b'12345678'))) as client:
        path = tmp_path / 'stock.jpg'
        with pytest.raises(pipeline.PipelineError, match='download limit'):
            pipeline.download(client, 'https://images.pexels.com/fixture.jpg', path, budget={'remaining': 4})
        assert not path.exists()


def test_gemini_plan_maps_scene_ids_even_when_returned_out_of_order(monkeypatch):
    monkeypatch.setattr(pipeline, 'gemini', lambda *args, **kwargs: json.dumps([
        {'id': 1, 'query': 'underwater coral reef'}, {'id': 0, 'query': 'rocket launch'}]))
    scenes = [{'text': 'Rockets launch.'}, {'text': 'Coral reefs grow.'}]
    assert pipeline.scene_queries(None, {'topic': 'Topic', 'keys': {'gemini': 'fixture'}}, scenes, []) == [
        'rocket launch', 'underwater coral reef']


def test_no_stock_results_do_not_reuse_an_unrelated_visual(tmp_path):
    queries = []
    def empty(request):
        queries.append(request.url.params['query'])
        return httpx.Response(200, json={'videos': [], 'photos': []})
    with httpx.Client(transport=httpx.MockTransport(empty)) as client:
        asset, credit = pipeline.stock(client, {'format': 'portrait', 'keys': {'pexels': 'fixture'}},
                                       tmp_path, 'rocket launch', 0, set(), {'remaining': 1000})
    assert asset is None and credit is None
    assert queries == ['rocket launch', 'rocket launch']


def test_long_narration_fails_before_stock_search_instead_of_cutting(tmp_path, monkeypatch):
    speech = pipeline.Narration(tmp_path / 'voice.wav', 16, [], [], True)
    monkeypatch.setattr(pipeline, 'narration', lambda *args: speech)
    with pytest.raises(pipeline.PipelineError, match='16.0s for a 15s target'):
        pipeline.render({'script': 'A complete story.', 'topic': 'Story', 'duration': 15,
                         'voice': 'en-US-AriaNeural', 'keys': {}}, tmp_path, lambda **changes: None)
    assert not (tmp_path / 'video.mp4').exists()


def test_scene_specific_stock_trim_and_captions_in_real_export(tmp_path, monkeypatch):
    from moviepy import VideoFileClip
    audio = tmp_path / 'speech.wav'
    write_audio(audio, 2)
    for color in ('red', 'blue'):
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                        f'color=c={color}:s=64x64:r=24:d=8', '-c:v', 'libx264',
                        '-threads', '1', '-preset', 'ultrafast', str(tmp_path / f'{color}.mp4')],
                       check=True, capture_output=True)
    scenes = [{'text': 'Rockets launch.', 'start': 0, 'end': .5},
              {'text': 'Coral reefs grow.', 'start': .5, 'end': 2}]
    words = [{'text': 'Rockets launch.', 'start': .1, 'end': .45},
             {'text': 'Coral reefs grow.', 'start': .55, 'end': 1.8}]
    monkeypatch.setattr(pipeline, 'narration', lambda *args: pipeline.Narration(audio, 2, words, scenes, True))
    queries = []
    def provider(request):
        if request.url.path == '/videos/search':
            query = request.url.params['query']
            queries.append(query)
            color = 'red' if 'Rockets' in query else 'blue'
            return httpx.Response(200, json={'videos': [{'id': color, 'url': f'https://www.pexels.com/video/{color}',
                'user': {'name': 'Fixture'}, 'video_files': [{'file_type': 'video/mp4', 'width': 64, 'height': 64,
                'link': f'https://videos.pexels.com/{color}.mp4'}]}]})
        return httpx.Response(200, content=(tmp_path / request.url.path.lstrip('/')).read_bytes())
    original = httpx.Client
    monkeypatch.setattr(pipeline.httpx, 'Client', lambda **kwargs: original(transport=httpx.MockTransport(provider), **kwargs))
    folder = tmp_path / 'project'
    result = pipeline.render({'topic': 'A generic topic', 'script': 'Rockets launch. Coral reefs grow.',
                              'voice': 'en-US-AriaNeural', 'duration': 15, 'format': 'square',
                              'visuals': 'stock', 'captions': 'script', 'thumbnail': False,
                              'keys': {'pexels': 'fixture-key'}}, folder, lambda **changes: None)
    assert queries == ['Rockets launch', 'Coral reefs grow']
    assert result['duration'] == 2 and result['word_aligned']
    assert [(s['start'], s['end']) for s in result['scenes']] == [(0, .5), (.5, 2)]
    with VideoFileClip(str(folder / 'video.mp4')) as video:
        assert abs(video.duration - 2) < .1
        red, blue = video.get_frame(.25)[40, 40], video.get_frame(.75)[40, 40]
        assert red[0] > 200 and red[2] < 30
        assert blue[2] > 200 and blue[0] < 30
    subtitles = (folder / 'captions.srt').read_text()
    assert '00:00:00,100 --> 00:00:00,450' in subtitles
    assert '00:00:00,550 --> 00:00:01,800' in subtitles
    assert set(path.name for path in folder.iterdir()) == {'video.mp4', 'thumbnail.jpg', 'captions.srt'}
