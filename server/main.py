"""Single-owner video studio. Public health/UI, authenticated private projects."""
from __future__ import annotations

import asyncio
import hmac
import json
import os
import re
import secrets
import shutil
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
import truststore
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, SecretStr

truststore.inject_into_ssl()
load_dotenv(Path(__file__).parent.parent / '.env')
DATA = Path(os.getenv('STUDIO_DATA_DIR', str(Path(__file__).parent / 'data'))).resolve()
TOKEN = os.getenv('STUDIO_ACCESS_TOKEN', '')
JOBS: dict[str, dict] = {}
LOCK = threading.RLock()
TASKS: set[asyncio.Task] = set()


class Keys(BaseModel):
    gemini: SecretStr = SecretStr('')
    pexels: SecretStr = SecretStr('')
    pixabay: SecretStr = SecretStr('')
    huggingface: SecretStr = SecretStr('')


class CreateVideo(BaseModel):
    topic: str = Field(min_length=3, max_length=180)
    script: str = Field(default='', max_length=6000)
    duration: Literal[15, 30, 60] = 30
    format: Literal['portrait', 'landscape', 'square'] = 'portrait'
    voice: Literal['en-US-AriaNeural', 'en-US-GuyNeural', 'en-GB-SoniaNeural', 'en-IN-NeerjaNeural', 'local-en'] = 'en-US-AriaNeural'
    visuals: Literal['studio', 'stock'] = 'studio'
    captions: Literal['script', 'whisper', 'none'] = 'script'
    thumbnail: bool = False
    keys: Keys = Field(default_factory=Keys)


class VerifyKey(BaseModel):
    provider: Literal['gemini', 'pexels', 'pixabay', 'huggingface']
    key: SecretStr


def auth(credentials: HTTPAuthorizationCredentials | None = Depends(HTTPBearer(auto_error=False))):
    if not TOKEN or not credentials or not hmac.compare_digest(credentials.credentials, TOKEN):
        raise HTTPException(401, 'Invalid server access token.')


def persist(job):
    folder = DATA / job['id']
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / 'project.tmp'
    temporary.write_text(json.dumps(job))
    temporary.replace(folder / 'project.json')


def update(job_id, **changes):
    with LOCK:
        JOBS[job_id].update(changes, updated=time.time())
        persist(JOBS[job_id])


@asynccontextmanager
async def lifespan(app):
    if len(TOKEN) < 32:
        raise RuntimeError('Set a private STUDIO_ACCESS_TOKEN of at least 32 characters.')
    DATA.mkdir(parents=True, exist_ok=True)
    with LOCK:
        JOBS.clear()
        for path in DATA.glob('*/project.json'):
            try:
                job = json.loads(path.read_text())
                if not re.fullmatch('[a-f0-9]{24}', job['id']) or job['id'] != path.parent.name:
                    continue
                if job['status'] in ('running', 'queued'):
                    job.update(status='failed', error='Server restarted during rendering. Create a new video.')
                    persist(job)
                JOBS[job['id']] = job
            except (OSError, ValueError, KeyError, TypeError):
                continue
    yield
    if TASKS:
        await asyncio.gather(*list(TASKS), return_exceptions=True)


app = FastAPI(title='Faceless Studio', lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_middleware(CORSMiddleware, allow_origins=os.getenv('STUDIO_CORS_ORIGINS', 'https://localhost,http://localhost,capacitor://localhost,http://localhost:5173,http://127.0.0.1:5173').split(','), allow_methods=['GET', 'POST', 'DELETE'], allow_headers=['Authorization', 'Content-Type'])


@app.middleware('http')
async def headers_and_limits(request: Request, call_next):
    if request.method == 'POST':
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > 48000:
                return JSONResponse({'detail': 'Request too large.'}, status_code=413)
            chunks.append(chunk)
        request._body = b''.join(chunks)
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    return JSONResponse({'detail': 'Invalid request. Check your topic, duration and options.'}, status_code=422)


@app.get('/health')
def health():
    return {'status': 'ok', 'service': 'faceless-studio'}


@app.get('/api/capabilities', dependencies=[Depends(auth)])
def capabilities():
    import importlib.util
    return {'ffmpeg': bool(shutil.which('ffmpeg')), 'local_voice': bool(shutil.which('espeak-ng')),
            'whisper': importlib.util.find_spec('whisper') is not None, 'max_duration': 60}


@app.post('/api/verify-key', dependencies=[Depends(auth)])
async def verify_key(data: VerifyKey):
    key = data.key.get_secret_value().strip()
    if not key:
        raise HTTPException(422, 'Enter this provider key first.')
    endpoints = {
        'gemini': ('https://generativelanguage.googleapis.com/v1beta/models', {'x-goog-api-key': key}, {}),
        'pexels': ('https://api.pexels.com/v1/search', {'Authorization': key}, {'query': 'nature', 'per_page': 1}),
        'pixabay': ('https://pixabay.com/api/', {}, {'key': key, 'q': 'nature', 'per_page': 3}),
        'huggingface': ('https://huggingface.co/api/whoami-v2', {'Authorization': 'Bearer ' + key}, {}),
    }
    url, headers, params = endpoints[data.provider]
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            response = await client.get(url, headers=headers, params=params)
        response.raise_for_status()
    except (httpx.HTTPError, ValueError):
        raise HTTPException(502, 'Provider did not accept the request. Check key, network and quota.') from None
    return {'ok': True}


@app.get('/api/projects', dependencies=[Depends(auth)])
def projects():
    with LOCK:
        return sorted(JOBS.values(), key=lambda job: job['created'], reverse=True)


@app.get('/api/projects/{job_id}', dependencies=[Depends(auth)])
def project(job_id: str):
    with LOCK:
        if job_id not in JOBS:
            raise HTTPException(404, 'Project not found.')
        return JOBS[job_id].copy()


@app.post('/api/projects', status_code=202, dependencies=[Depends(auth)])
async def create(data: CreateVideo):
    import importlib.util
    if not shutil.which('ffmpeg'):
        raise HTTPException(503, 'FFmpeg is missing on the server.')
    if not data.script.strip() and not data.keys.gemini.get_secret_value().strip():
        raise HTTPException(422, 'Add a Gemini key or paste your own script.')
    if data.visuals == 'stock' and not (data.keys.pexels.get_secret_value().strip() or data.keys.pixabay.get_secret_value().strip()):
        raise HTTPException(422, 'Add a stock provider key or choose Studio backgrounds.')
    if data.thumbnail and not data.keys.huggingface.get_secret_value().strip():
        raise HTTPException(422, 'Add a Hugging Face token or turn off AI thumbnail.')
    if data.captions == 'whisper' and importlib.util.find_spec('whisper') is None:
        raise HTTPException(422, 'Whisper is not installed. Select script captions.')
    with LOCK:
        if any(job['status'] in ('queued', 'running') for job in JOBS.values()):
            raise HTTPException(409, 'A video is already rendering. Wait for it to finish.')
        if len(JOBS) >= 50:
            raise HTTPException(409, 'Delete old projects first. Library limit is 50.')
        if shutil.disk_usage(DATA).free < 1024 ** 3:
            raise HTTPException(507, 'Server storage is low. Delete old videos.')
        job_id = secrets.token_hex(12)
        job = dict(id=job_id, title=data.topic.strip(), status='queued', stage='Queued', progress=0,
                   duration=data.duration, format=data.format, caption_mode=data.captions, script='',
                   error=None, warnings=[], credits=[], created=time.time(), updated=time.time())
        JOBS[job_id] = job
        persist(job)
    task = asyncio.create_task(run_job(job_id, data))
    TASKS.add(task)
    task.add_done_callback(TASKS.discard)
    return job.copy()


async def run_job(job_id, data):
    from pipeline import render, PipelineError
    update(job_id, status='running', stage='Preparing', progress=2)
    try:
        result = await asyncio.to_thread(render, data.model_dump(mode='python'), DATA / job_id,
                                         lambda **changes: update(job_id, **changes))
        update(job_id, **result, status='complete', stage='Ready to share', progress=100)
    except PipelineError as error:
        update(job_id, status='failed', stage='Failed', error=str(error))
    except Exception:
        # Provider exceptions can contain URLs and credentials. Never persist them.
        update(job_id, status='failed', stage='Failed', error='Rendering failed. Check server resources and provider access.')


@app.get('/api/projects/{job_id}/files/{filename}', dependencies=[Depends(auth)])
def artifact(job_id: str, filename: str):
    if job_id not in JOBS or JOBS[job_id]['status'] != 'complete':
        raise HTTPException(404, 'Finished project not found.')
    if filename not in ('video.mp4', 'thumbnail.jpg', 'captions.srt'):
        raise HTTPException(404, 'File not found.')
    path = DATA / job_id / filename
    if not path.is_file():
        raise HTTPException(404, 'File not found.')
    return FileResponse(path, filename=filename)


@app.delete('/api/projects/{job_id}', status_code=204, dependencies=[Depends(auth)])
def delete(job_id: str):
    with LOCK:
        if job_id not in JOBS:
            raise HTTPException(404, 'Project not found.')
        if JOBS[job_id]['status'] in ('queued', 'running'):
            raise HTTPException(409, 'Wait for rendering to finish.')
        shutil.rmtree(DATA / job_id)
        del JOBS[job_id]
    return Response(status_code=204)


STATIC = Path(__file__).with_name('static')
if not STATIC.is_dir():
    # Local source checkout; Docker copies these same sources into /app/static.
    STATIC = Path(__file__).resolve().parent.parent / 'web' / 'src'
app.mount('/assets', StaticFiles(directory=STATIC), name='assets')


@app.get('/')
def index():
    return FileResponse(STATIC / 'index.html')
