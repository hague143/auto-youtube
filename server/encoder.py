"""Encode one scene at a time, then join without re-encoding the whole movie.

Only one stock decoder and one caption file are open at any time. Cumulative
frame boundaries prevent hundreds of short scenes accumulating timing drift.
"""
from __future__ import annotations

import math
import subprocess
import tempfile
from pathlib import Path

FPS = 24


class EncodingError(Exception):
    pass


def ass_time(seconds):
    ticks = max(0, round(seconds * 100))
    return f'{ticks // 360000}:{ticks // 6000 % 60:02}:{ticks // 100 % 60:02}.{ticks % 100:02}'


def scene_captions(path, segments, start, end, width, height):
    header = (
        '[Script Info]\nScriptType: v4.00+\n'
        f'PlayResX: {width}\nPlayResY: {height}\nWrapStyle: 0\n'
        '[V4+ Styles]\n'
        'Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,'
        'Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,'
        'Alignment,MarginL,MarginR,MarginV,Encoding\n'
        f'Style: Default,DejaVu Sans,{max(24, width // 25)},&H00FFFFFF,&H00FFFFFF,&H00140F0A,'
        f'&H80140F0A,1,0,0,0,100,100,0,0,3,5,0,2,32,32,{max(40, height // 12)},1\n'
        '[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n'
    )
    rows = []
    for begin, finish, text in segments:
        begin, finish = max(begin, start), min(finish, end)
        if finish <= begin:
            continue
        # Narration is plain text, never ASS markup or drawing commands.
        text = text.replace('\\', ' ').replace('{', '(').replace('}', ')').replace('\n', ' ')
        rows.append(f'Dialogue: 0,{ass_time(begin - start)},{ass_time(finish - start)},Default,,0,0,0,,{text}\n')
    path.write_text(header + ''.join(rows), encoding='utf-8')


def run_encoder(arguments, cwd, timeout):
    try:
        subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-y', *arguments],
                       cwd=cwd, capture_output=True, check=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise EncodingError('A scene took too long to render. Check server CPU resources.') from None
    except (subprocess.SubprocessError, OSError):
        raise EncodingError('Video encoding failed. Check server disk space and the stock media files.') from None


def encode_timeline(audio_path, scenes, segments, folder, size, duration, on_progress=None):
    if not math.isfinite(duration) or duration < .1 or not scenes:
        raise EncodingError('Narration audio or scene timing is empty.')
    if abs(scenes[0]['start']) > .01 or abs(scenes[-1]['end'] - duration) > .05:
        raise EncodingError('Scene timing does not cover the narration.')
    width, height = size
    previous_end, previous_frame = 0.0, 0
    output = folder / 'video.mp4'
    try:
        with tempfile.TemporaryDirectory(prefix='.render-', dir=folder) as temporary:
            working = Path(temporary)
            names = []
            for index, scene in enumerate(scenes):
                start, end = scene['start'], scene['end']
                if not math.isfinite(start) or not math.isfinite(end) or end <= start or abs(start - previous_end) > .01:
                    raise EncodingError('Scene timing contains a gap or overlap.')
                previous_end = end
                end_frame = round(end * FPS)
                frames = end_frame - previous_frame
                if frames <= 0:
                    raise EncodingError('A narration scene is shorter than one video frame.')
                frame_start, frame_end = previous_frame / FPS, end_frame / FPS
                previous_frame = end_frame
                name = f'scene-{index:04}.mp4'
                source = Path(scene['asset']).resolve()
                inputs = ['-stream_loop', '-1', '-i', str(source)] if source.suffix.lower() == '.mp4' else [
                    '-loop', '1', '-framerate', str(FPS), '-i', str(source)]
                filters = f'scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1,fps={FPS}'
                relevant = [segment for segment in segments if segment[0] < frame_end and segment[1] > frame_start]
                if relevant:
                    caption_name = f'scene-{index:04}.ass'
                    scene_captions(working / caption_name, relevant, frame_start, frame_end, width, height)
                    filters += ',subtitles=filename=' + caption_name
                run_encoder([
                    '-threads', '1', *inputs, '-map', '0:v:0', '-an', '-sn',
                    '-vf', filters, '-filter_threads', '1', '-frames:v', str(frames),
                    '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '25', '-maxrate', '2500k',
                    '-bufsize', '5000k', '-pix_fmt', 'yuv420p', '-r', str(FPS),
                    '-video_track_timescale', '24000', '-threads', '1', name,
                ], working, max(120, (end - start) * 45))
                names.append(name)
                if on_progress:
                    on_progress(index + 1, len(scenes))
            (working / 'join.txt').write_text(''.join(f"file '{name}'\n" for name in names))
            run_encoder([
                '-f', 'concat', '-safe', '1', '-i', 'join.txt', '-i', str(Path(audio_path).resolve()),
                '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '128k',
                '-t', str(duration), '-movflags', '+faststart', '-threads', '1', str(output.resolve()),
            ], working, max(120, duration * 2))
    except Exception:
        output.unlink(missing_ok=True)
        raise
    return duration
