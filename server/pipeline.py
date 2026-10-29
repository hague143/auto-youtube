"""Bounded, single-job pipeline; all provider keys stay in the worker's memory."""
from __future__ import annotations

import asyncio
import html
import json
import math
import os
import shutil
import ssl
import subprocess
import textwrap
import wave
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx
import aiohttp
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from timeline import captions_from_words, estimated_words, scenes_from_words, split_script, visual_query

FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
SIZES = {'portrait': (720, 1280), 'landscape': (1280, 720), 'square': (720, 720)}


class PipelineError(Exception):
    pass


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


def download(client, url, path, suffixes=('pexels.com', 'pixabay.com'), budget=None):
    parsed = urlparse(url)
    host = parsed.hostname or ''
    if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443) or not any(host == suffix or host.endswith('.' + suffix) for suffix in suffixes):
        raise PipelineError('Stock provider returned an unsupported media address.')
    try:
        with client.stream('GET', url, follow_redirects=False, timeout=90) as response:
            response.raise_for_status()
            size = 0
            with path.open('wb') as target:
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    limit = min(40 * 1024 * 1024, budget['remaining']) if budget else 80 * 1024 * 1024
                    if size > limit:
                        raise PipelineError('Stock download limit reached. Try a shorter script or Studio backgrounds.')
                    target.write(chunk)
        if budget is not None:
            budget['remaining'] -= size
    except httpx.HTTPError:
        path.unlink(missing_ok=True)
        raise PipelineError('Could not download stock media. Try Studio backgrounds.') from None
    except PipelineError:
        path.unlink(missing_ok=True)
        raise


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


def stock(client, data, folder, query, index, used, budget):
    """Find one distinct visual for this scene's subject, not the overall topic."""
    if key(data, 'pexels'):
        headers = {'Authorization': key(data, 'pexels')}
        orientation = 'portrait' if data['format'] == 'portrait' else 'landscape'
        result = provider_json(client, 'GET', 'https://api.pexels.com/videos/search', headers=headers,
                               params={'query': query, 'per_page': 4, 'orientation': orientation})
        for item in result.get('videos', []):
            identity = ('Pexels video', item.get('id', item.get('url')))
            if identity in used:
                continue
            files = [f for f in item.get('video_files', []) if f.get('file_type') == 'video/mp4' and f.get('width') and f.get('height')]
            if not files:
                continue
            files.sort(key=lambda f: abs(max(f['width'], f['height']) - 1280))
            target = folder / f'stock-{index}.mp4'
            download(client, files[0]['link'], target, budget=budget)
            used.add(identity)
            return target, {'provider': 'Pexels', 'creator': item.get('user', {}).get('name', ''), 'url': item.get('url', '')}
        result = provider_json(client, 'GET', 'https://api.pexels.com/v1/search', headers=headers,
                               params={'query': query, 'per_page': 4, 'orientation': orientation})
        for item in result.get('photos', []):
            identity = ('Pexels photo', item.get('id', item.get('url')))
            if identity in used:
                continue
            target = folder / f'stock-{index}.jpg'
            download(client, item['src']['large'], target, budget=budget)
            used.add(identity)
            return target, {'provider': 'Pexels', 'creator': item.get('photographer', ''), 'url': item.get('url', '')}
    if key(data, 'pixabay'):
        result = provider_json(client, 'GET', 'https://pixabay.com/api/', params={
            'key': key(data, 'pixabay'), 'q': query, 'per_page': 4, 'safesearch': 'true'})
        for item in result.get('hits', []):
            identity = ('Pixabay', item.get('id', item.get('pageURL')))
            if identity in used:
                continue
            target = folder / f'stock-{index}.jpg'
            download(client, item['webformatURL'], target, budget=budget)
            used.add(identity)
            return target, {'provider': 'Pixabay', 'creator': item.get('user', ''), 'url': item.get('pageURL', '')}
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
        speech = narration(script, data['voice'], folder, warnings)
        if speech.duration > data['duration'] + .01:
            suggested = max(1, int(len(script.split()) * data['duration'] / speech.duration * .95))
            raise PipelineError(f'Narration takes {speech.duration:.1f}s for a {data["duration"]}s target. '
                                f'Choose a longer target or shorten to about {suggested} words. The narration was not cut.')
        scenes = speech.scenes
        progress(stage='Planning scenes', progress=32)
        queries = scene_queries(client, data, scenes, warnings) if data['visuals'] == 'stock' else [
            visual_query(scene['text'], data['topic']) for scene in scenes]
        used, budget = set(), {'remaining': 192 * 1024 * 1024}
        for index, (scene, query) in enumerate(zip(scenes, queries)):
            progress(stage=f'Preparing scene {index + 1}/{len(scenes)}', progress=35 + int(20 * index / len(scenes)))
            path, credit = stock(client, data, folder, query, index, used, budget) if data['visuals'] == 'stock' else (None, None)
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
