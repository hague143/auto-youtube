import base64
import io
import json
import sys
import time
import wave
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parents[1]))
import main
import pipeline
from timeline import words_from_characters

TOKEN = 'test-only-elevenlabs-server-token'
AUTH = {'Authorization': 'Bearer ' + TOKEN}


def alignment_fixture():
    characters, starts, ends = [], [], []
    for text, start, duration in [('Rockets.', .1, .6), (' ', .7, 0), ('Coral.', 1.1, .7)]:
        for index, character in enumerate(text):
            characters.append(character)
            starts.append(start + duration * index / len(text))
            ends.append(start + duration * (index + 1) / len(text))
    return {'characters': characters, 'character_start_times_seconds': starts, 'character_end_times_seconds': ends}


def speech_fixture():
    audio = io.BytesIO()
    with wave.open(audio, 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b'\x00\x00' * 32000)
    return {'audio_base64': base64.b64encode(audio.getvalue()).decode(),
            'normalized_alignment': alignment_fixture(), 'alignment': None}


def test_elevenlabs_audio_and_character_times_stay_together(tmp_path):
    requests = []
    def provider(request):
        requests.append(request)
        assert request.url == 'https://api.elevenlabs.io/v1/text-to-speech/fixtureVoice/with-timestamps?output_format=mp3_44100_128'
        assert request.headers['xi-api-key'] == 'fixture-private-key'
        assert json.loads(request.content) == {'text': 'Rockets. Coral.', 'model_id': 'eleven_multilingual_v2'}
        return httpx.Response(200, json=speech_fixture())
    with httpx.Client(transport=httpx.MockTransport(provider)) as client:
        speech = pipeline.elevenlabs_narration(client, 'Rockets. Coral.', 'fixture-private-key', 'fixtureVoice', tmp_path)
    assert len(requests) == 1 and speech.aligned
    assert speech.duration == 2
    assert [word['text'] for word in speech.words] == ['Rockets.', 'Coral.']
    assert speech.words[0]['start'] == .1 and speech.words[1]['start'] == 1.1
    assert [(s['start'], s['end']) for s in speech.scenes] == [(0, 1.1), (1.1, 2)]


@pytest.mark.parametrize(('status', 'reason', 'message'), [
    (401, 'quota_exceeded', 'credits are exhausted'),
    (401, 'invalid_api_key', 'API key'),
    (403, 'missing_permissions', 'permission'),
    (404, 'voice_not_found', 'Voice ID'),
    (429, 'rate_limit', 'limit reached'),
])
def test_elevenlabs_errors_are_redacted_and_never_automatically_retried(tmp_path, status, reason, message):
    requests = []
    def provider(request):
        requests.append(request)
        return httpx.Response(status, json={'detail': {'status': reason, 'message': 'sentinel-private-key'}})
    with httpx.Client(transport=httpx.MockTransport(provider)) as client:
        with pytest.raises(pipeline.PipelineError, match=message) as error:
            pipeline.elevenlabs_narration(client, 'Rockets.', 'sentinel-private-key', 'fixtureVoice', tmp_path)
    assert len(requests) == 1
    assert 'sentinel-private-key' not in str(error.value)
    assert not (tmp_path / 'voice.mp3').exists()


def test_elevenlabs_bad_voice_id_never_makes_a_request(tmp_path):
    def unexpected(request):
        pytest.fail('An invalid voice ID must not reach the provider.')
    with httpx.Client(transport=httpx.MockTransport(unexpected)) as client:
        with pytest.raises(pipeline.PipelineError, match='Voice ID'):
            pipeline.elevenlabs_narration(client, 'Rockets.', 'fixture-key', '../../other-endpoint', tmp_path)


@pytest.mark.parametrize('change', ['length', 'backwards', 'nan', 'outside'])
def test_invalid_character_alignment_is_rejected(change):
    alignment = alignment_fixture()
    if change == 'length':
        alignment['characters'].pop()
    elif change == 'backwards':
        alignment['character_start_times_seconds'][2] = 0
    elif change == 'nan':
        alignment['character_end_times_seconds'][2] = float('nan')
    else:
        alignment['character_end_times_seconds'][-1] = 20
    with pytest.raises(ValueError):
        words_from_characters(alignment, 2)


def test_api_requires_elevenlabs_key_and_rejects_voice_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DATA', tmp_path)
    monkeypatch.setattr(main, 'TOKEN', TOKEN)
    body = {'topic': 'Rockets and coral', 'script': 'Rockets. Coral.', 'voice': 'elevenlabs'}
    with TestClient(main.app) as client:
        response = client.post('/api/projects', headers=AUTH, json=body)
        assert response.status_code == 422 and 'ElevenLabs API key' in response.text
        body.update(elevenlabs_voice_id='../sentinel-private-key', keys={'elevenlabs': 'sentinel-private-key'})
        response = client.post('/api/projects', headers=AUTH, json=body)
        assert response.status_code == 422 and 'sentinel-private-key' not in response.text
        assert client.get('/api/projects', headers=AUTH).json() == []


def test_key_test_reads_voices_without_synthesizing_or_exposing_extra_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DATA', tmp_path)
    monkeypatch.setattr(main, 'TOKEN', TOKEN)
    def provider(request):
        assert request.method == 'GET' and request.url.path == '/v1/voices'
        assert request.headers['xi-api-key'] == 'fixture-private-key'
        return httpx.Response(200, json={'voices': [{'voice_id': 'fixtureVoice', 'name': 'A voice',
                                                     'extra_private_field': 'sentinel-private-key'}]})
    original = httpx.AsyncClient
    monkeypatch.setattr(main.httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(provider), **kwargs))
    with TestClient(main.app) as client:
        response = client.post('/api/verify-key', headers=AUTH, json={'provider': 'elevenlabs', 'key': 'fixture-private-key'})
    assert response.json() == {'ok': True, 'voices': [{'id': 'fixtureVoice', 'name': 'A voice'}]}
    assert 'sentinel-private-key' not in response.text


def test_elevenlabs_job_exports_real_mp4_without_persisting_provider_key(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DATA', tmp_path)
    monkeypatch.setattr(main, 'TOKEN', TOKEN)
    def provider(request):
        assert request.url.host == 'api.elevenlabs.io' and '/with-timestamps' in request.url.path
        return httpx.Response(200, json=speech_fixture())
    original = httpx.Client
    monkeypatch.setattr(pipeline.httpx, 'Client', lambda **kwargs: original(transport=httpx.MockTransport(provider), **kwargs))
    with TestClient(main.app) as client:
        response = client.post('/api/projects', headers=AUTH, json={
            'topic': 'Rockets and coral', 'script': 'Rockets. Coral.', 'voice': 'elevenlabs',
            'elevenlabs_voice_id': 'fixtureVoice', 'duration': 15, 'format': 'square',
            'keys': {'elevenlabs': 'sentinel-private-key'}})
        assert response.status_code == 202
        identifier = response.json()['id']
        for attempt in range(200):
            job = client.get('/api/projects/' + identifier, headers=AUTH).json()
            if job['status'] in ('complete', 'failed'):
                break
            time.sleep(.05)
        assert job['status'] == 'complete', job
        assert job['voice_used'] == 'elevenlabs' and job['word_aligned']
        assert job['duration'] == 2 and len(job['scenes']) == 2
        assert job['scenes'][0]['end'] == job['scenes'][1]['start'] == 1.1
        assert client.get('/api/projects/' + identifier + '/files/video.mp4', headers=AUTH).status_code == 200
        assert 'sentinel-private-key' not in json.dumps(job)
        assert 'sentinel-private-key' not in (tmp_path / identifier / 'project.json').read_text()
        assert set(path.name for path in (tmp_path / identifier).iterdir()) == {
            'video.mp4', 'thumbnail.jpg', 'captions.srt', 'project.json'}
