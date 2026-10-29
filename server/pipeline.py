"""Bounded, single-job pipeline; all provider keys stay in the worker's memory."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import hmac
import json
import math
import os
import re
import shutil
import ssl
import subprocess
import tempfile
import textwrap
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx
import aiohttp
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from timeline import captions_from_words, estimated_words, scenes_from_words, split_script, visual_query, words_from_characters

FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
SIZES = {'portrait': (720, 1280), 'landscape': (1280, 720), 'square': (720, 720)}


class PipelineError(Exception):
    pass


class StockAssetSkipped(PipelineError):
    """This candidate is unsuitable; stock search can try another asset."""


def stock_budget(folder):
    def megabytes(name, default, maximum):
        try:
            value = int(os.getenv(name, str(default)))
        except ValueError:
            value = default
        return max(1, min(value, maximum)) * 1024 * 1024
    remaining = min(megabytes('STUDIO_STOCK_TOTAL_MB', 192, 1024),
                    max(0, shutil.disk_usage(folder).free - 512 * 1024 * 1024))
    return {'remaining': remaining, 'asset_limit': megabytes('STUDIO_STOCK_ASSET_MB', 40, 256),
            'image_reserve': min(12 * 1024 * 1024, remaining // 8), 'disabled': set()}


def stock_warning(warnings, message):
    if warnings is not None and message not in warnings:
        warnings.append(message)


def key(data, provider):
    value = data['keys'].get(provider, '')
    return value.get_secret_value().strip() if hasattr(value, 'get_secret_value') else str(value).strip()


def provider_json(client, method, url, **kwargs):
    try:
        response = client.request(method, url, **kwargs)
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError):
        raise PipelineError('Provider request failed. Check the API key, model access and quota.') from None


def download(client, url, path, suffixes=('pexels.com', 'pixabay.com'), budget=None, max_bytes=None, reserve=0):
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ''
        if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443) or not any(host == suffix or host.endswith('.' + suffix) for suffix in suffixes):
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise StockAssetSkipped('Stock provider returned an unsupported media address.') from None
    limit = max_bytes if max_bytes is not None else (budget.get('asset_limit', 40 * 1024 * 1024) if budget is not None else 80 * 1024 * 1024)
    if budget is not None:
        limit = min(limit, max(0, budget['remaining'] - reserve))
    if limit <= 0:
        raise StockAssetSkipped('Stock download limit reached for this candidate.')
    size = 0
    try:
        with client.stream('GET', url, follow_redirects=False, timeout=90) as response:
            response.raise_for_status()
            length = response.headers.get('Content-Length', '')
            if length.isdigit() and int(length) > limit:
                raise StockAssetSkipped('Stock download limit reached for this candidate.')
            with path.open('wb') as target:
                for chunk in response.iter_bytes(chunk_size=64 * 1024):
                    size += len(chunk)
                    if size > limit:
                        raise StockAssetSkipped('Stock download limit reached for this candidate.')
                    target.write(chunk)
            if not size:
                raise StockAssetSkipped('Stock asset was empty.')
    except httpx.HTTPError:
        path.unlink(missing_ok=True)
        raise StockAssetSkipped('Stock asset was unavailable; another candidate can be used.') from None
    except StockAssetSkipped:
        path.unlink(missing_ok=True)
        raise
    finally:
        if budget is not None:
            budget['remaining'] = max(0, budget['remaining'] - size)


def stock_search(client, provider, endpoint, data, params, budget, warnings, cache_dir=None):
    """Cache API searches for 24h per credential without storing that credential."""
    api_key = key(data, provider)
    identity = json.dumps([endpoint, {name: value for name, value in params.items() if name != 'key'}], sort_keys=True)
    fingerprint = hmac.new(api_key.encode(), identity.encode(), hashlib.sha256).hexdigest()
    path = cache_dir / (fingerprint + '.json') if cache_dir is not None else None
    if path is not None:
        try:
            cached = json.loads(path.read_text())
            if 0 <= time.time() - cached['saved'] < 86400 and isinstance(cached['data'], dict):
                return cached['data']
        except (OSError, ValueError, KeyError, TypeError):
            pass
    if provider in budget.setdefault('disabled', set()):
        return {}
    try:
        result = provider_json(client, 'GET', endpoint,
                               headers={'Authorization': api_key} if provider == 'pexels' else {}, params=params)
        if not isinstance(result, dict):
            raise PipelineError('Unreadable stock search response.')
    except PipelineError:
        budget['disabled'].add(provider)
        stock_warning(warnings, f'{provider.title()} search was unavailable; another source or scene cards were used.')
        return {}
    if path is not None:
        # Stock responses are public metadata, but never persist a reflected key.
        def clean(value):
            if isinstance(value, str):
                return value.replace(api_key, '[redacted]') if api_key else value
            if isinstance(value, list):
                return [clean(item) for item in value]
            if isinstance(value, dict):
                return {name: clean(item) for name, item in value.items()
                        if name.lower() not in ('key', 'api_key', 'authorization', 'token', 'secret')}
            return value
        temporary = None
        try:
            cache_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            content = json.dumps({'saved': time.time(), 'data': clean(result)})
            if len(content) <= 2 * 1024 * 1024:
                with tempfile.NamedTemporaryFile(mode='w', dir=cache_dir, suffix='.tmp', delete=False) as target:
                    temporary = Path(target.name)
                    target.write(content)
                temporary.replace(path)
                files = sorted(cache_dir.glob('*.json'), key=lambda file: file.stat().st_mtime, reverse=True)
                for old in files[128:]:
                    old.unlink(missing_ok=True)
        except OSError:
            stock_warning(warnings, 'Stock search caching was unavailable on this server.')
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return result


def title_image(title, size, variant=0):
    width, height = size
    colors = [((14, 31, 48), (30, 76, 58)), ((39, 25, 63), (22, 69, 88)), ((47, 38, 25), (15, 52, 64))]
    top, bottom = colors[variant % len(colors)]
    canvas = Image.new('RGB', size)
    draw = ImageDraw.Draw(canvas)
    for y in range(height):
        ratio = y / max(height - 1, 1)
        draw.line((0, y, width, y), fill=tuple(round(a * (1 - ratio) + b * ratio) for a, b in zip(top, bottom)))
    font = ImageFont.truetype(FONT, max(22, width // 19))
    lines = textwrap.wrap(title, width=25 if width < 900 else 38)[:6]
    text = '\n'.join(lines)
    bounds = draw.multiline_textbbox((0, 0), text, font=font, spacing=12, align='center')
    draw.multiline_text(((width - bounds[2]) / 2, height * .3), text, font=font, fill='#edf5ee', spacing=12, align='center')
    return canvas


def audio_duration(path):
    try:
        result = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                                 '-of', 'default=noprint_wrappers=1:nokey=1', str(path)],
                                capture_output=True, text=True, check=True, timeout=30)
        duration = float(result.stdout.strip())
        if not math.isfinite(duration) or duration < .1:
            raise ValueError
        return duration
    except (subprocess.SubprocessError, OSError, ValueError):
        raise PipelineError('Narration audio is empty or unreadable.') from None


@dataclass
class Narration:
    path: Path
    duration: float
    words: list
    scenes: list
    aligned: bool


def elevenlabs_narration(client, script, api_key, voice_id, folder):
    """One paid-provider synthesis request; keep audio and alignment together."""
    if not api_key:
        raise PipelineError('Add your ElevenLabs API key in Settings.')
    if not isinstance(voice_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', voice_id):
        raise PipelineError('Enter a valid ElevenLabs Voice ID in Settings.')
    audio = folder / 'voice.mp3'
    try:
        with client.stream('POST', f'https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps',
                           headers={'xi-api-key': api_key}, params={'output_format': 'mp3_44100_128'},
                           json={'text': script, 'model_id': 'eleven_multilingual_v2'},
                           timeout=90, follow_redirects=False) as response:
            content, size = [], 0
            limit = 16 * 1024 * 1024 if response.is_success else 64 * 1024
            for chunk in response.iter_bytes(chunk_size=64 * 1024):
                size += len(chunk)
                if size > limit:
                    raise PipelineError('ElevenLabs response exceeds the supported size.')
                content.append(chunk)
            try:
                result = json.loads(b''.join(content))
            except (ValueError, UnicodeError):
                result = {}
            if not response.is_success:
                detail = result.get('detail', {}) if isinstance(result, dict) else {}
                reason = detail.get('status') if isinstance(detail, dict) else None
                if reason in ('quota_exceeded', 'insufficient_credits', 'credit_balance_too_low'):
                    raise PipelineError('ElevenLabs credits are exhausted. Check your account allowance.')
                if response.status_code == 401:
                    raise PipelineError('ElevenLabs rejected the API key (HTTP 401). Check the key in Settings.')
                if response.status_code == 403:
                    raise PipelineError('ElevenLabs denied this voice or key permission (HTTP 403). Enable Text-to-Speech and choose an available Voice ID.')
                if response.status_code == 404:
                    raise PipelineError('ElevenLabs voice was not found. Check the Voice ID in Settings.')
                if response.status_code == 429:
                    raise PipelineError('ElevenLabs rate or account limit reached (HTTP 429). Check credits and retry later.')
                raise PipelineError(f'ElevenLabs could not synthesize this narration (HTTP {response.status_code}). Check voice and model access.')
        if not isinstance(result, dict) or not isinstance(result.get('audio_base64'), str):
            raise PipelineError('ElevenLabs returned no usable audio.')
        audio.write_bytes(base64.b64decode(result['audio_base64'], validate=True))
        duration = audio_duration(audio)
        alignment = result.get('normalized_alignment') or result.get('alignment')
        if not isinstance(alignment, dict):
            raise PipelineError('ElevenLabs returned audio without character timings.')
        words = words_from_characters(alignment, duration)
        return Narration(audio, duration, words, scenes_from_words(words, duration, script), True)
    except PipelineError:
        audio.unlink(missing_ok=True)
        raise
    except httpx.HTTPError:
        audio.unlink(missing_ok=True)
        raise PipelineError('Could not connect securely to ElevenLabs. Check server connectivity and retry later.') from None
    except (ValueError, TypeError, KeyError, OSError):
        audio.unlink(missing_ok=True)
        raise PipelineError('ElevenLabs returned unreadable audio or invalid character timings.') from None


def natural_voice_error(error):
    """Expose useful categories, never provider URLs, headers or raw exceptions."""
    status = getattr(error, 'status', None)
    if type(status) is int and status == 403:
        return ('Natural voice service rejected the connection (HTTP 403). Update the backend to the pinned '
                'edge-tts 7.3.1 client and check the server clock. If it persists, this server cannot currently use Edge speech.')
    if type(status) is int and status == 429:
        return 'Natural voice service is rate limited (HTTP 429). Wait before creating another video.'
    if isinstance(error, (ssl.SSLError, aiohttp.ClientSSLError)):
        return 'Natural voice TLS certificate verification failed. Check the server clock and trusted CA certificates.'
    if isinstance(error, aiohttp.ClientConnectorDNSError):
        return 'Natural voice DNS lookup failed. Check outbound DNS and HTTPS connectivity on the rendering server.'
    if isinstance(error, TimeoutError):
        return 'Natural voice request timed out. Check the rendering server network and retry later.'
    if isinstance(error, aiohttp.ClientConnectorError):
        return 'Natural voice connection failed. Check outbound HTTPS connectivity on the rendering server.'
    if type(status) is int and 400 <= status <= 599:
        return f'Natural voice service returned HTTP {status}. Check service availability and retry later.'
    return ('Natural voice or its timing service is unavailable. Retry later. '
            'Local English is a separate robotic offline option; it was not substituted.')


def narration(script, voice, folder, warnings):
    if voice != 'local-en':
        audio, metadata = folder / 'voice.mp3', folder / 'speech.jsonl'
        try:
            import edge_tts
            async def speak():
                speaker = edge_tts.Communicate(script, voice, boundary='WordBoundary')
                await asyncio.wait_for(speaker.save(str(audio), str(metadata)), timeout=90)
            asyncio.run(speak())
            duration = audio_duration(audio)
            words = []
            for line in metadata.read_text(encoding='utf-8').splitlines():
                event = json.loads(line)
                if event.get('type') != 'WordBoundary':
                    continue
                start = max(0.0, float(event['offset']) / 10_000_000)
                end = min(duration, start + float(event['duration']) / 10_000_000)
                text = html.unescape(event['text']).strip()
                if text and math.isfinite(start) and math.isfinite(end) and end > start:
                    words.append({'text': text, 'start': start, 'end': end})
            if not words or any(b['start'] < a['start'] for a, b in zip(words, words[1:])):
                raise ValueError('Missing speech timings')
            return Narration(audio, duration, words, scenes_from_words(words, duration, script), True)
        except PipelineError:
            raise
        except Exception as error:
            raise PipelineError(natural_voice_error(error)) from None
    binary = shutil.which('espeak-ng')
    if not binary:
        raise PipelineError('Local voice is unavailable. Install espeak-ng or use a working Edge voice.')
    audio = folder / 'voice.wav'
    words, scenes, position = [], [], 0.0
    try:
        with wave.open(str(audio), 'wb') as output:
            for index, text in enumerate(split_script(script)):
                part = folder / f'voice-part-{index}.wav'
                subprocess.run([binary, '-v', 'en-us', '-s', '155', '-w', str(part), '--stdin'], input=text,
                               text=True, check=True, capture_output=True, timeout=30)
                with wave.open(str(part), 'rb') as source:
                    if index == 0:
                        output.setparams(source.getparams())
                    elif source.getparams()[:3] != output.getparams()[:3]:
                        raise ValueError('Inconsistent narration format')
                    duration = source.getnframes() / source.getframerate()
                    output.writeframes(source.readframes(source.getnframes()))
                scenes.append({'text': text, 'start': position, 'end': position + duration})
                words.extend(estimated_words(text, position, position + duration))
                position += duration
                part.unlink()
    except (subprocess.SubprocessError, OSError, wave.Error, ValueError):
        raise PipelineError('Local voice generation failed.') from None
    warnings.append('Local English uses robotic eSpeak narration. Select Aria, Guy or Sonia for a natural voice.')
    return Narration(audio, position, words, scenes, False)


def check_stock_asset(path):
    try:
        if path.suffix == '.mp4':
            result = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                                     '-show_entries', 'stream=width,height:format=duration', '-of', 'json', str(path)],
                                    check=True, capture_output=True, text=True, timeout=20)
            metadata = json.loads(result.stdout)
            stream = metadata['streams'][0]
            duration = float(metadata['format']['duration'])
            if not math.isfinite(duration) or duration <= 0 or not 0 < max(stream['width'], stream['height']) <= 1920:
                raise ValueError
        else:
            with Image.open(path) as image:
                if image.width * image.height > 4096 * 4096:
                    raise ValueError
                image.verify()
    except (subprocess.SubprocessError, OSError, ValueError, KeyError, IndexError, TypeError, Image.DecompressionBombError):
        raise StockAssetSkipped('Stock asset was unreadable or too large to decode.') from None


def stock(client, data, folder, query, index, used, budget, *, warnings=None, cache_dir=None, scene_duration=4):
    """Try bounded HD/SD candidates, another provider, then matching photos."""
    orientation = 'portrait' if data['format'] == 'portrait' else 'landscape'
    reserve = budget.get('image_reserve', 0)

    def video_allowance():
        share = max(0, budget['remaining'] - reserve) // max(1, budget.get('scenes_left', 1))
        return min(budget.get('asset_limit', 40 * 1024 * 1024), share)

    def search(provider, endpoint, params):
        return stock_search(client, provider, endpoint, data, params, budget, warnings, cache_dir)

    def fetch(url, target, video):
        try:
            download(client, url, target, budget=budget,
                     max_bytes=video_allowance() if video else 8 * 1024 * 1024,
                     reserve=reserve if video else 0)
            check_stock_asset(target)
            return True
        except StockAssetSkipped:
            target.unlink(missing_ok=True)
            stock_warning(warnings, 'Some stock files were skipped because of size or availability; other clips or photos were tried.')
            return False

    def files_for_scene(files):
        available = video_allowance()
        candidates = []
        for file in files:
            width, height = file.get('width'), file.get('height')
            size = file.get('size', file.get('file_size'))
            if not isinstance(width, (int, float)) or not isinstance(height, (int, float)):
                continue
            if not 0 < min(width, height) or max(width, height) > 1920:
                continue
            if isinstance(size, (int, float)) and size > available:
                continue
            candidates.append(file)
        return sorted(candidates, key=lambda file: (max(file['width'], file['height']) > 1280,
                                                     abs(max(file['width'], file['height']) - 1280)))

    def duration_order(item):
        duration = item.get('duration', 30)
        if not isinstance(duration, (int, float)) or not math.isfinite(duration):
            duration = 30
        return (duration < scene_duration, abs(duration - scene_duration))

    if budget['remaining'] > reserve and key(data, 'pexels'):
        result = search('pexels', 'https://api.pexels.com/videos/search',
                        {'query': query, 'per_page': 4, 'orientation': orientation})
        attempts = 0
        for item in sorted(result.get('videos', []), key=duration_order):
            identity = ('Pexels video', item.get('id', item.get('url')))
            if identity in used:
                continue
            files = [file for file in item.get('video_files', []) if file.get('file_type') == 'video/mp4']
            for file in files_for_scene(files):
                if attempts >= 4:
                    break
                attempts += 1
                target = folder / f'stock-{index}.mp4'
                if fetch(file.get('link'), target, True):
                    used.add(identity)
                    return target, {'provider': 'Pexels', 'kind': 'video', 'creator': item.get('user', {}).get('name', ''), 'url': item.get('url', '')}

    if budget['remaining'] > reserve and key(data, 'pixabay'):
        result = search('pixabay', 'https://pixabay.com/api/videos/',
                        {'key': key(data, 'pixabay'), 'q': query, 'per_page': 4, 'safesearch': 'true'})
        attempts = 0
        for item in sorted(result.get('hits', []), key=duration_order):
            identity = ('Pixabay video', item.get('id', item.get('pageURL')))
            if identity in used:
                continue
            files = [file for file in item.get('videos', {}).values() if isinstance(file, dict)]
            for file in files_for_scene(files):
                if attempts >= 4:
                    break
                attempts += 1
                target = folder / f'stock-{index}.mp4'
                if fetch(file.get('url'), target, True):
                    used.add(identity)
                    return target, {'provider': 'Pixabay', 'kind': 'video', 'creator': item.get('user', ''), 'url': item.get('pageURL', '')}

    if budget['remaining'] <= 0:
        stock_warning(warnings, 'Stock transfer allowance is used; remaining scenes use their narration text cards.')
        return None, None

    if key(data, 'pexels'):
        result = search('pexels', 'https://api.pexels.com/v1/search',
                        {'query': query, 'per_page': 4, 'orientation': orientation})
        for item in result.get('photos', []):
            identity = ('Pexels photo', item.get('id', item.get('url')))
            if identity in used:
                continue
            for quality in ('large', 'medium'):
                url = item.get('src', {}).get(quality)
                if not url:
                    continue
                target = folder / f'stock-{index}.jpg'
                if fetch(url, target, False):
                    used.add(identity)
                    stock_warning(warnings, 'Some scenes use matching photos when a suitable video could not be downloaded.')
                    return target, {'provider': 'Pexels', 'kind': 'photo', 'creator': item.get('photographer', ''), 'url': item.get('url', '')}
    if key(data, 'pixabay'):
        result = search('pixabay', 'https://pixabay.com/api/',
                        {'key': key(data, 'pixabay'), 'q': query, 'per_page': 4, 'safesearch': 'true'})
        for item in result.get('hits', []):
            identity = ('Pixabay photo', item.get('id', item.get('pageURL')))
            if identity in used:
                continue
            target = folder / f'stock-{index}.jpg'
            if fetch(item.get('webformatURL'), target, False):
                used.add(identity)
                stock_warning(warnings, 'Some scenes use matching photos when a suitable video could not be downloaded.')
                return target, {'provider': 'Pixabay', 'kind': 'photo', 'creator': item.get('user', ''), 'url': item.get('pageURL', '')}
    return None, None


def response_text(result):
    try:
        return ' '.join(part.get('text', '') for part in result['candidates'][0]['content']['parts']
                        if not part.get('thought')).strip()
    except (KeyError, IndexError, TypeError):
        raise PipelineError('Gemini did not return a usable response.') from None


def gemini(client, data, prompt, structured=False):
    model = os.getenv('STUDIO_GEMINI_MODEL', 'gemini-2.5-flash')
    if not model.replace('-', '').replace('.', '').isalnum():
        raise PipelineError('Invalid server Gemini model setting.')
    payload = {'contents': [{'parts': [{'text': prompt}]}]}
    if structured:
        payload['generationConfig'] = {'responseMimeType': 'application/json'}
    return response_text(provider_json(
        client, 'POST', f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',
        headers={'x-goog-api-key': key(data, 'gemini')}, json=payload))


def scene_queries(client, data, scenes, warnings):
    queries = [visual_query(scene['text'], data['topic']) for scene in scenes]
    if not key(data, 'gemini'):
        return queries
    try:
        prompt = ('Plan stock-footage searches for these narration scenes. For EACH scene, choose the concrete '
                  'visible subject and action described in that scene, rather than a generic video topic or mood. '
                  'Use 2-6 English search words. Return only a JSON array of objects with integer id and string query. '
                  'Keep every id exactly once. Scene data:\n' + json.dumps(
                      [{'id': index, 'text': scene['text']} for index, scene in enumerate(scenes)]))
        planned = json.loads(gemini(client, data, prompt, structured=True))
        if not isinstance(planned, list) or len(planned) != len(scenes):
            raise ValueError
        seen = set()
        for item in planned:
            index, query = item['id'], item['query']
            if type(index) is not int or index not in range(len(scenes)) or index in seen or not isinstance(query, str):
                raise ValueError
            query = ' '.join(query.split())
            if not query or len(query) > 90 or len(query.split()) > 8 or '://' in query:
                raise ValueError
            seen.add(index)
            queries[index] = query
        return queries
    except (PipelineError, ValueError, KeyError, TypeError):
        warnings.append('Gemini scene planning was unavailable; searches use each scene\'s script keywords.')
        return [visual_query(scene['text'], data['topic']) for scene in scenes]


def srt_time(seconds):
    millis = max(0, round(seconds * 1000))
    return f'{millis // 3600000:02}:{millis // 60000 % 60:02}:{millis // 1000 % 60:02},{millis % 1000:03}'


def assemble(script, audio_path, scenes, folder, format_name, caption_mode, maximum, word_cues=None):
    from moviepy import AudioFileClip, ImageClip, VideoFileClip, CompositeVideoClip, concatenate_videoclips
    width, height = SIZES[format_name]
    opened = []
    try:
        audio = AudioFileClip(str(audio_path))
        opened.append(audio)
        duration = audio.duration
        if duration < .1:
            raise PipelineError('Narration audio is empty.')
        if duration > float(maximum) + .01:
            raise PipelineError('Narration exceeds the selected length. Choose a longer target or shorten the script.')
        if not scenes or abs(scenes[0]['start']) > .01 or abs(scenes[-1]['end'] - duration) > .05:
            raise PipelineError('Scene timing does not cover the narration.')
        clips, previous_end = [], 0.0
        for scene in scenes:
            path = scene['asset']
            each = scene['end'] - scene['start']
            if each <= 0 or abs(scene['start'] - previous_end) > .01:
                raise PipelineError('Scene timing contains a gap or overlap.')
            previous_end = scene['end']
            source = VideoFileClip(str(path), audio=False) if path.suffix == '.mp4' else ImageClip(np.array(Image.open(path).convert('RGB')))
            opened.append(source)
            if path.suffix == '.mp4':
                if source.duration < each:
                    from moviepy import vfx
                    source = source.with_effects([vfx.Loop(duration=each)])
                else:
                    source = source.subclipped(0, each)
            source = source.with_duration(each)
            scale = max(width / source.w, height / source.h)
            source = source.resized(scale).cropped(x_center=source.w * scale / 2, y_center=source.h * scale / 2, width=width, height=height)
            clips.append(source)
        background = concatenate_videoclips(clips, method='compose').with_duration(duration)
        opened.append(background)
        segments = []
        if caption_mode == 'whisper':
            import whisper
            model = whisper.load_model(os.getenv('STUDIO_WHISPER_MODEL', 'tiny'))
            result = model.transcribe(str(audio_path), language='en', fp16=False, word_timestamps=True)
            words = [{'start': float(word['start']), 'end': min(float(word['end']), duration),
                      'text': word['word'].strip()} for segment in result['segments']
                     for word in segment.get('words', []) if word['start'] < duration]
            segments = captions_from_words(words, scenes)
        elif caption_mode == 'script':
            words = word_cues if word_cues is not None else [
                word for scene in scenes for word in estimated_words(scene['text'], scene['start'], scene['end'])]
            segments = captions_from_words(words, scenes)
        layers = [background]
        subtitle_rows = []
        for index, (start, end, text) in enumerate(segments, 1):
            if end <= start:
                continue
            subtitle_rows.append(f'{index}\n{srt_time(start)} --> {srt_time(end)}\n{text}\n')
            image = Image.new('RGBA', (width, 160), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            text = '\n'.join(textwrap.wrap(text, width=28 if width < 900 else 48))
            font = ImageFont.truetype(FONT, max(24, width // 25))
            draw.rounded_rectangle((18, 8, width - 18, 150), radius=16, fill=(5, 10, 15, 210))
            draw.multiline_text((width / 2, 80), text, font=font, fill='white', anchor='mm', align='center', spacing=7, stroke_width=1, stroke_fill='black')
            layers.append(ImageClip(np.array(image)).with_start(start).with_duration(end - start).with_position(('center', height - 210)))
        if caption_mode != 'none':
            (folder / 'captions.srt').write_text('\n'.join(subtitle_rows), encoding='utf-8')
        video = CompositeVideoClip(layers, size=(width, height)).with_duration(duration).with_audio(audio)
        opened.append(video)
        video.write_videofile(str(folder / 'video.mp4'), fps=24, codec='libx264', audio_codec='aac',
                             threads=1, preset='ultrafast', logger=None, temp_audiofile=str(folder / 'mux.m4a'), remove_temp=True)
        return duration
    finally:
        for clip in reversed(opened):
            clip.close()


def render(data, folder, progress):
    folder.mkdir(parents=True, exist_ok=True)
    warnings, credits = [], []
    script = data['script'].strip()
    with httpx.Client(timeout=40, follow_redirects=False) as client:
        if not script:
            progress(stage='Writing script', progress=8)
            script = gemini(client, data,
                            f'Write only a factual English voiceover script about {data["topic"]}. '
                            f'At most {round(data["duration"] * 1.9)} words for a {data["duration"]}-second video. '
                            'Use short concrete sentences with visible subjects and actions. '
                            'Finish the story within that word budget. No headings, stage directions or markdown.')
        if not script:
            raise PipelineError('Narration script is empty.')
        if len(script) > 6000 or len(script.split()) > data['duration'] * 4:
            raise PipelineError('Script is too long for the selected length. Shorten it or choose a longer target.')
        progress(stage='Creating narration', progress=20, script=script)
        if data['voice'] == 'elevenlabs':
            speech = elevenlabs_narration(client, script, key(data, 'elevenlabs'),
                                         data.get('elevenlabs_voice_id', 'JBFqnCBsd6RMkjVDRZzb'), folder)
        else:
            speech = narration(script, data['voice'], folder, warnings)
        if speech.duration > data['duration'] + .01:
            suggested = max(1, int(len(script.split()) * data['duration'] / speech.duration * .95))
            raise PipelineError(f'Narration takes {speech.duration:.1f}s for a {data["duration"]}s target. '
                                f'Choose a longer target or shorten to about {suggested} words. The narration was not cut.')
        scenes = speech.scenes
        progress(stage='Planning scenes', progress=32)
        queries = scene_queries(client, data, scenes, warnings) if data['visuals'] == 'stock' else [
            visual_query(scene['text'], data['topic']) for scene in scenes]
        used, budget = set(), stock_budget(folder)
        for index, (scene, query) in enumerate(zip(scenes, queries)):
            progress(stage=f'Preparing scene {index + 1}/{len(scenes)}', progress=35 + int(20 * index / len(scenes)))
            budget['scenes_left'] = len(scenes) - index
            path, credit = stock(client, data, folder, query, index, used, budget,
                                 warnings=warnings, cache_dir=folder.parent / '.stock-cache',
                                 scene_duration=scene['end'] - scene['start']) if data['visuals'] == 'stock' else (None, None)
            if path is None:
                path = folder / f'background-{index}.jpg'
                title_image(scene['text'], SIZES[data['format']], index).save(path, quality=90)
                if data['visuals'] == 'stock':
                    warnings.append(f'No distinct stock match for scene {index + 1} ({query}); a scene text card was used.')
            if credit and credit not in credits:
                credits.append(credit)
            scene.update(asset=path, query=query, source=credit['provider'] if credit else 'Studio')
        title_image(data['topic'], (1280, 720)).save(folder / 'thumbnail.jpg', quality=92)
        if data['thumbnail']:
            try:
                response = client.post('https://router.huggingface.co/hf-inference/models/stabilityai/stable-diffusion-xl-base-1.0',
                                       headers={'Authorization': 'Bearer ' + key(data, 'huggingface')},
                                       json={'inputs': f'Cinematic cover image, no text, about {data["topic"]}'}, timeout=90)
                response.raise_for_status()
                if len(response.content) > 20 * 1024 * 1024:
                    raise ValueError('Image too large')
                from io import BytesIO
                Image.open(BytesIO(response.content)).convert('RGB').resize((1280, 720)).save(folder / 'thumbnail.jpg', quality=92)
            except Exception:
                warnings.append('AI thumbnail inference was unavailable; a title cover is included.')
        if data['captions'] == 'script' and not speech.aligned:
            warnings.append('Offline captions are estimated within each measured narration scene. Natural voices provide word timings.')
        public_scenes = [{field: scene[field] for field in ('text', 'start', 'end', 'query', 'source')} for scene in scenes]
        progress(stage='Rendering MP4', progress=60, warnings=warnings, credits=credits,
                 scenes=public_scenes, voice_used=data['voice'])
        duration = assemble(script, speech.path, scenes, folder, data['format'], data['captions'], data['duration'], speech.words)
    # Keep only final artifacts and metadata, not downloaded stock or working audio.
    for path in folder.iterdir():
        if path.name not in ('project.json', 'project.tmp', 'video.mp4', 'thumbnail.jpg', 'captions.srt') and path.is_file():
            path.unlink()
    return {'duration': round(duration, 2), 'script': script, 'warnings': warnings, 'credits': credits,
            'scenes': public_scenes, 'voice_used': data['voice'], 'word_aligned': speech.aligned}
