import json
import subprocess
import sys
import wave
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parents[1]))
import main
import pipeline
from timeline import estimated_words, scenes_from_words, split_script


def test_long_form_api_limits_and_storage_preflight(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DATA', tmp_path)
    monkeypatch.setattr(main, 'TOKEN', 'test-only-longform-token-at-least-32')
    auth = {'Authorization': 'Bearer ' + main.TOKEN}
    text = 'Rockets launch above the clouds. ' * 300
    assert len(text) > 6000
    model = main.CreateVideo(topic='Rockets', duration=900, script=text)
    assert model.duration == 900 and model.script == text
    with TestClient(main.app) as client:
        assert client.get('/api/capabilities', headers=auth).json()['max_duration'] == 900
        monkeypatch.setattr(main.shutil, 'disk_usage', lambda path: type('Usage', (), {'free': 2 * 1024**3})())
        response = client.post('/api/projects', headers=auth, json=model.model_dump(mode='json'))
        assert response.status_code == 507
        assert client.get('/api/projects', headers=auth).json() == []


def test_sentence_remains_one_scene_until_its_last_word():
    text = 'A red fox walks slowly across the snowy field beside the forest.'
    words = estimated_words(text, 0, 7)
    assert split_script(text) == [text]
    assert scenes_from_words(words, 7, text) == [{'text': text, 'start': 0, 'end': 7}]


def test_normalized_numbers_do_not_merge_different_visual_sentences():
    words = [{'text': text, 'start': index * .5, 'end': index * .5 + .4}
             for index, text in enumerate('Twelve rockets launch. Coral reefs grow.'.split())]
    scenes = scenes_from_words(words, 3, '12 rockets launch. Coral reefs grow.')
    assert [scene['text'] for scene in scenes] == ['Twelve rockets launch.', 'Coral reefs grow.']
    assert scenes[0]['end'] == scenes[1]['start'] == 1.5


def test_long_narration_joins_audio_and_offsets_every_word(tmp_path, monkeypatch):
    script = ('A red fox walks across the snowy field. ' * 85).strip()
    calls = []
    def speech(client, text, api_key, voice_id, folder):
        calls.append(text)
        audio = folder / 'voice.wav'
        with wave.open(str(audio), 'wb') as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16000)
            output.writeframes(b'\x00\x00' * 32000)
        words = estimated_words(text, .05, 1.95)
        return pipeline.Narration(audio, 2, words, scenes_from_words(words, 2, text), True)
    monkeypatch.setattr(pipeline, 'elevenlabs_narration', speech)
    stages = []
    result = pipeline.narrate_script(None, {'voice': 'elevenlabs', 'keys': {'elevenlabs': 'fixture-only'}},
                                    script, tmp_path, [], lambda **changes: stages.append(changes))
    assert len(calls) >= 2 and all(len(part) <= 2000 for part in calls)
    assert ' '.join(calls) == script
    assert pipeline.audio_duration(result.path) == len(calls) * 2
    assert result.duration == len(calls) * 2 and result.aligned
    assert ' '.join(word['text'] for word in result.words) == script
    assert all(a['end'] <= b['start'] for a, b in zip(result.words, result.words[1:]))
    assert result.words[-1]['end'] == pytest.approx(result.duration - .05)
    assert result.scenes[-1]['end'] == result.duration
    assert not list(tmp_path.glob('.speech-*'))


def test_paid_narration_failure_does_not_repeat_completed_parts(tmp_path, monkeypatch):
    calls = []
    def failure(client, text, api_key, voice_id, folder):
        calls.append(text)
        raise pipeline.PipelineError('Provider rejected this part.')
    monkeypatch.setattr(pipeline, 'elevenlabs_narration', failure)
    with pytest.raises(pipeline.PipelineError, match='No automatic retry'):
        pipeline.narrate_script(None, {'voice': 'elevenlabs', 'keys': {}},
                                'A red fox walks. ' * 200, tmp_path, [], lambda **changes: None)
    assert len(calls) == 1


def test_large_scene_plan_is_batched_and_keeps_context_and_alternatives(monkeypatch):
    scenes = [{'text': f'A red fox walks past marker {index}.'} for index in range(51)]
    batches = []
    def plan(client, data, prompt, structured=False):
        batch = json.loads(prompt.split('Scene data:\n', 1)[1])
        batches.append(batch)
        return json.dumps([{'id': row['id'], 'query': 'red fox walking snow',
                            'alternatives': ['fox snow', 'red fox forest']} for row in reversed(batch)])
    monkeypatch.setattr(pipeline, 'gemini', plan)
    result = pipeline.scene_queries(None, {'topic': 'Foxes', 'keys': {'gemini': 'fixture'}}, scenes, [])
    assert len(batches) == 3 and max(map(len, batches)) == 20
    assert batches[1][0]['previous'] == scenes[19]['text']
    assert result == ['red fox walking snow'] * 51
    assert all(scene['search_queries'] == ['red fox walking snow', 'fox snow', 'red fox forest'] for scene in scenes)


def test_long_script_is_written_in_coherent_chapters_before_voice(monkeypatch):
    calls = []
    def write(client, data, prompt, **kwargs):
        calls.append(prompt)
        return 'An outline.' if len(calls) == 1 else f'Rockets lift above launch pad {len(calls)}.'
    monkeypatch.setattr(pipeline, 'gemini', write)
    result = pipeline.write_script(None, {'duration': 900, 'topic': 'Rocket launches'}, lambda **changes: None)
    assert len(calls) == 6
    assert 'chapter 5 of 5' in calls[-1] and 'Conclude the whole story.' in calls[-1]
    assert 'Rockets lift above launch pad 5.' in calls[-1]
    assert len(result.split('\n\n')) == 5


def test_stock_relevance_ignores_creator_names_and_rejects_unrelated_subjects():
    unrelated = {'url': 'https://www.pexels.com/video/waterfall-in-green-forest-123/',
                 'user': {'name': 'Rocket Launch'}}
    related = {'url': 'https://www.pexels.com/video/rocket-launching-above-clouds-456/'}
    assert pipeline.stock_relevance(unrelated, 'rocket launch') < 0
    assert pipeline.stock_relevance(related, 'rocket launch') > 0
    assert pipeline.stock_relevance({'tags': 'coral reef, fish, underwater'}, 'coral reefs') > 0


def test_equivalent_video_search_is_tried_before_photo_fallback(tmp_path, monkeypatch):
    calls = []
    def provider(request):
        calls.append(request.url.path + '?' + request.url.params.get('query', ''))
        if request.url.path == '/videos/search':
            query = request.url.params['query']
            return httpx.Response(200, json={'videos': [] if query == 'red fox walking snow' else [{
                'id': 1, 'url': 'https://www.pexels.com/video/red-fox-walking-123/',
                'video_files': [{'file_type': 'video/mp4', 'width': 640, 'height': 360,
                                 'link': 'https://videos.pexels.com/fox.mp4'}]}]})
        return httpx.Response(200, content=b'fixture-video')
    monkeypatch.setattr(pipeline, 'check_stock_asset', lambda path: None)
    with httpx.Client(transport=httpx.MockTransport(provider)) as client:
        asset, credit = pipeline.stock(client, {'format': 'landscape', 'keys': {'pexels': 'fixture'}},
                                       tmp_path, 'red fox walking snow', 0, set(), {'remaining': 10000},
                                       alternatives=['red fox walking'])
    assert credit['query'] == 'red fox walking' and credit['kind'] == 'video'
    assert asset.read_bytes() == b'fixture-video'
    assert calls == ['/videos/search?red fox walking snow', '/videos/search?red fox walking', '/fox.mp4?']


def test_long_video_gets_scaled_stock_budget_but_honors_owner_override(tmp_path, monkeypatch):
    monkeypatch.delenv('STUDIO_STOCK_TOTAL_MB', raising=False)
    monkeypatch.setattr(pipeline.shutil, 'disk_usage', lambda path: type('Usage', (), {'free': 10 * 1024**3})())
    assert pipeline.stock_budget(tmp_path, 60)['remaining'] == 192 * 1024**2
    assert pipeline.stock_budget(tmp_path, 900)['remaining'] == 1350 * 1024**2
    monkeypatch.setenv('STUDIO_STOCK_TOTAL_MB', '500')
    assert pipeline.stock_budget(tmp_path, 900)['remaining'] == 500 * 1024**2


def test_fractional_scene_frames_do_not_accumulate_rounding_drift(tmp_path):
    from PIL import Image
    from encoder import encode_timeline
    audio = tmp_path / 'voice.wav'
    with wave.open(str(audio), 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b'\x00\x00' * 21120)
    for color in ('red', 'blue'):
        Image.new('RGB', (64, 64), color).save(tmp_path / (color + '.jpg'))
    scenes = [{'start': index * .11, 'end': (index + 1) * .11,
               'asset': tmp_path / ('red.jpg' if index % 2 == 0 else 'blue.jpg')}
              for index in range(12)]
    encode_timeline(audio, scenes, [], tmp_path, (64, 64), 1.32)
    metadata = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json',
                                          str(tmp_path / 'video.mp4')], check=True, capture_output=True, text=True).stdout)
    assert next(stream for stream in metadata['streams'] if stream['codec_type'] == 'video')['nb_frames'] == '32'
    pixel = subprocess.run(['ffmpeg', '-v', 'error', '-ss', '1.27', '-i', str(tmp_path / 'video.mp4'),
                            '-frames:v', '1', '-vf', 'crop=2:2:20:20,format=rgb24', '-f', 'rawvideo', '-'],
                           check=True, capture_output=True).stdout
    assert pixel[2] > 200 and pixel[0] < 30  # The final scene survives, rather than being cut off by accumulated padding.
    assert not list(tmp_path.glob('.render-*'))
