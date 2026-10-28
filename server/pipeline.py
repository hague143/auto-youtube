"""Bounded, single-job pipeline; all provider keys stay in the worker's memory."""
from __future__ import annotations

import asyncio
import math
import os
import shutil
import subprocess
import textwrap
from pathlib import Path
from urllib.parse import urlparse

import httpx
import numpy as np
from PIL import Image, ImageDraw, ImageFont

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


def download(client, url, path, suffixes=('pexels.com', 'pixabay.com')):
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
                    if size > 80 * 1024 * 1024:
                        raise PipelineError('Stock asset exceeds the 80 MB download limit.')
                    target.write(chunk)
    except httpx.HTTPError:
        raise PipelineError('Could not download stock media. Try Studio backgrounds.') from None


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


def narration(script, voice, folder, warnings):
    audio = folder / 'voice.mp3'
    if voice != 'local-en':
        try:
            import edge_tts
            async def speak():
                await asyncio.wait_for(edge_tts.Communicate(script, voice).save(str(audio)), timeout=60)
            asyncio.run(speak())
            return audio
        except Exception:
            warnings.append('Edge TTS was unavailable; local eSpeak narration was used.')
    binary = shutil.which('espeak-ng')
    if not binary:
        raise PipelineError('Local voice is unavailable. Install espeak-ng or use a working Edge voice.')
    audio = folder / 'voice.wav'
    try:
        subprocess.run([binary, '-v', 'en-us', '-s', '155', '-w', str(audio), '--stdin'], input=script,
                       text=True, check=True, capture_output=True, timeout=90)
    except (subprocess.SubprocessError, OSError):
        raise PipelineError('Local voice generation failed.') from None
    return audio


def stock(client, data, folder):
    topic, assets, credits = data['topic'], [], []
    if key(data, 'pexels'):
        headers = {'Authorization': key(data, 'pexels')}
        orientation = 'portrait' if data['format'] == 'portrait' else 'landscape'
        result = provider_json(client, 'GET', 'https://api.pexels.com/videos/search', headers=headers,
                               params={'query': topic, 'per_page': 3, 'orientation': orientation})
        for item in result.get('videos', []):
            files = [f for f in item.get('video_files', []) if f.get('file_type') == 'video/mp4' and f.get('width') and f.get('height')]
            if not files:
                continue
            files.sort(key=lambda f: abs(max(f['width'], f['height']) - 1280))
            target = folder / f'stock-{len(assets)}.mp4'
            download(client, files[0]['link'], target)
            assets.append(target)
            credits.append({'provider': 'Pexels', 'creator': item.get('user', {}).get('name', ''), 'url': item.get('url', '')})
        if not assets:
            result = provider_json(client, 'GET', 'https://api.pexels.com/v1/search', headers=headers,
                                   params={'query': topic, 'per_page': 3, 'orientation': orientation})
            for item in result.get('photos', []):
                target = folder / f'stock-{len(assets)}.jpg'
                download(client, item['src']['large'], target)
                assets.append(target)
                credits.append({'provider': 'Pexels', 'creator': item.get('photographer', ''), 'url': item.get('url', '')})
    if not assets and key(data, 'pixabay'):
        result = provider_json(client, 'GET', 'https://pixabay.com/api/', params={
            'key': key(data, 'pixabay'), 'q': topic, 'per_page': 3, 'safesearch': 'true'})
        for item in result.get('hits', []):
            target = folder / f'stock-{len(assets)}.jpg'
            download(client, item['webformatURL'], target)
            assets.append(target)
            credits.append({'provider': 'Pixabay', 'creator': item.get('user', ''), 'url': item.get('pageURL', '')})
    if not assets:
        raise PipelineError('No stock results found. Try a simpler topic or Studio backgrounds.')
    return assets, credits


def srt_time(seconds):
    millis = max(0, round(seconds * 1000))
    return f'{millis // 3600000:02}:{millis // 60000 % 60:02}:{millis // 1000 % 60:02},{millis % 1000:03}'


def assemble(script, audio_path, assets, folder, format_name, caption_mode, maximum):
    from moviepy import AudioFileClip, ImageClip, VideoFileClip, CompositeVideoClip, concatenate_videoclips
    width, height = SIZES[format_name]
    opened = []
    try:
        audio = AudioFileClip(str(audio_path))
        opened.append(audio)
        duration = min(float(maximum), audio.duration)
        if duration < .1:
            raise PipelineError('Narration audio is empty.')
        clips, each = [], duration / len(assets)
        for path in assets:
            source = VideoFileClip(str(path), audio=False) if path.suffix == '.mp4' else ImageClip(np.array(Image.open(path).convert('RGB')))
            opened.append(source)
            if path.suffix == '.mp4' and source.duration < each:
                from moviepy import vfx
                source = source.with_effects([vfx.Loop(duration=each)])
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
            result = model.transcribe(str(audio_path), language='en', fp16=False)
            segments = [(float(s['start']), min(float(s['end']), duration), s['text'].strip()) for s in result['segments'] if s['start'] < duration]
        elif caption_mode == 'script':
            words = script.split()
            groups = [' '.join(words[i:i + 6]) for i in range(0, len(words), 6)]
            segments = [(i * duration / len(groups), (i + 1) * duration / len(groups), text) for i, text in enumerate(groups)] if groups else []
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
        video = CompositeVideoClip(layers, size=(width, height)).with_duration(duration).with_audio(audio.subclipped(0, duration))
        opened.append(video)
        video.write_videofile(str(folder / 'video.mp4'), fps=24, codec='libx264', audio_codec='aac',
                             threads=1, preset='ultrafast', logger=None, temp_audiofile=str(folder / 'mux.m4a'), remove_temp=True)
        return duration, audio.duration > duration + .2
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
            model = os.getenv('STUDIO_GEMINI_MODEL', 'gemini-2.5-flash')
            if not model.replace('-', '').replace('.', '').isalnum():
                raise PipelineError('Invalid server Gemini model setting.')
            result = provider_json(client, 'POST', f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',
                                   headers={'x-goog-api-key': key(data, 'gemini')}, json={'contents': [{'parts': [{'text':
                                   f'Write only a factual English voiceover script about {data["topic"]}. Around {round(data["duration"] * 2)} words. No headings, stage directions or markdown.'}]}]})
            try:
                script = ' '.join(p.get('text', '') for p in result['candidates'][0]['content']['parts']).strip()[:6000]
            except (KeyError, IndexError, TypeError):
                raise PipelineError('Gemini did not return a usable script.') from None
        if not script:
            raise PipelineError('Narration script is empty.')
        progress(stage='Creating narration', progress=20, script=script)
        audio = narration(script, data['voice'], folder, warnings)
        progress(stage='Preparing visuals', progress=35)
        if data['visuals'] == 'stock':
            assets, credits = stock(client, data, folder)
        else:
            assets = []
            for variant in range(3):
                path = folder / f'background-{variant}.jpg'
                title_image(data['topic'], SIZES[data['format']], variant).save(path, quality=90)
                assets.append(path)
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
        if data['captions'] == 'script':
            warnings.append('Script captions use estimated timing; they are not word-aligned transcription.')
        progress(stage='Rendering MP4', progress=60, warnings=warnings, credits=credits)
        duration, trimmed = assemble(script, audio, assets, folder, data['format'], data['captions'], data['duration'])
        if trimmed:
            warnings.append('Narration exceeded the selected length and was trimmed. Shorten your script for a complete ending.')
    # Keep only final artifacts and metadata, not downloaded stock or working audio.
    for path in folder.iterdir():
        if path.name not in ('project.json', 'project.tmp', 'video.mp4', 'thumbnail.jpg', 'captions.srt') and path.is_file():
            path.unlink()
    return {'duration': round(duration, 2), 'script': script, 'warnings': warnings, 'credits': credits}
