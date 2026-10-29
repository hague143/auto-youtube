import importlib
import json
import math
import struct
import subprocess
import sys
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parents[1]))
import main
from pipeline import assemble, title_image


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DATA', tmp_path)
    monkeypatch.setattr(main, 'TOKEN', 'test-only-token-never-use-in-production')
    with TestClient(main.app) as test:
        yield test


AUTH = {'Authorization': 'Bearer test-only-token-never-use-in-production'}


def test_private_endpoints_require_auth(client):
    assert client.get('/health').json()['status'] == 'ok'
    assert client.get('/api/projects').status_code == 401
    assert client.get('/api/projects', headers=AUTH).json() == []
    assert client.get('/api/projects', headers={'Authorization': 'Bearer incorrect'}).status_code == 401


def test_ui_and_no_request_value_echo(client):
    assert 'Your tools. Your keys.' in client.get('/').text
    assert client.get('/assets/app.js').status_code == 200
    secret = 'sentinel-provider-secret'
    result = client.post('/api/projects', headers=AUTH, json={'topic': secret, 'duration': 999, 'keys': {'gemini': secret}})
    assert result.status_code == 422
    assert secret not in result.text
    assert client.post('/api/projects', headers=AUTH, content=b'x' * 48001).status_code == 413


def test_provider_requirements_and_download_allowlist(client):
    assert client.post('/api/projects', headers=AUTH, json={'topic': 'Ocean mysteries'}).status_code == 422
    assert client.post('/api/projects', headers=AUTH, json={'topic': 'Ocean mysteries', 'script': 'A short story.', 'visuals': 'stock'}).status_code == 422
    assert client.get('/api/projects/not-a-project/files/project.json', headers=AUTH).status_code == 404


def test_restart_recovery_and_delete(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DATA', tmp_path)
    monkeypatch.setattr(main, 'TOKEN', 'test-only-token-never-use-in-production')
    job = {'id': 'a' * 24, 'status': 'running', 'created': 1, 'title': 'Private test'}
    main.persist(job)
    with TestClient(main.app) as test:
        recovered = test.get('/api/projects', headers=AUTH).json()[0]
        assert recovered['status'] == 'failed'
        assert test.get('/api/projects/' + job['id'] + '/files/project.json', headers=AUTH).status_code == 404
        assert test.delete('/api/projects/' + job['id'], headers=AUTH).status_code == 204
        assert not (tmp_path / job['id']).exists()


def test_real_ffmpeg_export_with_captions(tmp_path):
    audio = tmp_path / 'tone.wav'
    with wave.open(str(audio), 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b''.join(struct.pack('<h', round(9000 * math.sin(2 * math.pi * 440 * n / 16000))) for n in range(16000)))
    background = tmp_path / 'background.jpg'
    title_image('A real export fixture', (720, 720)).save(background)
    scenes = [{'text': 'A real export fixture.', 'asset': background, 'start': 0, 'end': 1}]
    duration = assemble('A real export fixture.', audio, scenes, tmp_path, 'square', 'script', 1)
    assert .9 < duration <= 1.01
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(tmp_path / 'video.mp4')], capture_output=True, text=True, check=True)
    streams = json.loads(result.stdout)['streams']
    assert {s['codec_name'] for s in streams} >= {'h264', 'aac'}
    assert 'A real export fixture.' in (tmp_path / 'captions.srt').read_text()
