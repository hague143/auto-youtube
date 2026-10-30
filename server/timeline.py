"""Scene and caption timing derived from the narration, rather than stock length."""
import math
import re

MAX_SCENE_WORDS = 24
MAX_SCENE_SECONDS = 12
STOP_WORDS = set('a an the and or but if so as at by for from in into of on to with '
                 'is are was were be been being it its this that these those '
                 'i we you they he she our your their his her them us my '
                 'can could will would should do does did have has had '
                 'not no just very more most also about how why what when '
                 'where which who every each one something'.split())


def split_script(script):
    """Keep sentence changes; split long sentences into short visual beats."""
    beats = []
    for sentence in re.split(r'(?<=[.!?;])\s+|\n+', script.strip()):
        words = sentence.split()
        for offset in range(0, len(words), MAX_SCENE_WORDS):
            beats.append(' '.join(words[offset:offset + MAX_SCENE_WORDS]))
    return beats


def estimated_words(text, start, end):
    """Offline eSpeak has no word events; estimate only within its measured scene."""
    tokens = text.split()
    weights = [max(2, len(re.sub(r'\W', '', token))) for token in tokens]
    total, position, cues = sum(weights), start, []
    for token, weight in zip(tokens, weights):
        finish = position + (end - start) * weight / total
        cues.append({'text': token, 'start': position, 'end': min(finish, end)})
        position = finish
    return cues


def words_from_characters(alignment, duration):
    """Convert ElevenLabs' character alignment into timed spoken words."""
    characters = alignment['characters']
    starts = alignment['character_start_times_seconds']
    ends = alignment['character_end_times_seconds']
    if not isinstance(characters, list) or not characters or len(characters) > 60000:
        raise ValueError('Invalid character alignment')
    if not isinstance(starts, list) or not isinstance(ends, list) or len(characters) != len(starts) or len(starts) != len(ends):
        raise ValueError('Mismatched character alignment')
    words, text, start, end, previous = [], '', 0.0, 0.0, 0.0
    for character, begin, finish in zip(characters, starts, ends):
        if not isinstance(character, str) or len(character) != 1:
            raise ValueError('Invalid alignment character')
        if type(begin) not in (int, float) or type(finish) not in (int, float):
            raise ValueError('Invalid alignment time')
        if not math.isfinite(begin) or not math.isfinite(finish) or begin < previous or finish < begin or finish > duration + .25:
            raise ValueError('Invalid alignment order')
        previous = begin
        if character.isspace():
            if text and end > start:
                words.append({'text': text, 'start': start, 'end': end})
            text = ''
        else:
            if not text:
                start = begin
            text += character
            end = min(finish, duration)
    if text and end > start:
        words.append({'text': text, 'start': start, 'end': end})
    if not words:
        raise ValueError('No spoken word timings')
    return words


def scenes_from_words(words, duration, script=''):
    """Cut at the next spoken word, never at a fraction of a stock clip."""
    normalize = lambda text: ''.join(character.lower() for character in text if character.isalnum())
    breaks = set()
    if normalize(script) == ''.join(normalize(word['text']) for word in words):
        boundaries, position = [], 0
        for text in split_script(script)[:-1]:
            position += len(normalize(text))
            boundaries.append(position)
        position = 0
        for index, word in enumerate(words):
            position += len(normalize(word['text']))
            if boundaries and position >= boundaries[0]:
                breaks.add(index + 1)
                while boundaries and position >= boundaries[0]:
                    boundaries.pop(0)
    scenes, group, start = [], [], 0.0
    for index, word in enumerate(words):
        # Normalized TTS can spell "12" as "twelve", so exact script matching
        # is not always possible. Preserve punctuation in the spoken transcript.
        spoken_break = index > 0 and re.search(r'[.!?;]["\')\]]*$', words[index - 1]['text'])
        if group and (index in breaks or spoken_break or word['end'] - start > MAX_SCENE_SECONDS or len(group) >= MAX_SCENE_WORDS):
            scenes.append({'text': ' '.join(item['text'] for item in group),
                           'start': start, 'end': word['start']})
            group, start = [], word['start']
        group.append(word)
    if group:
        scenes.append({'text': ' '.join(item['text'] for item in group),
                       'start': start, 'end': duration})
    return scenes


def captions_from_words(words, scenes):
    """Small captions follow spoken word times and stay within their scene."""
    captions = []
    for scene in scenes:
        group = []
        for word in words:
            if not scene['start'] <= word['start'] < scene['end']:
                continue
            if group and (len(group) >= 5 or word['end'] - group[0]['start'] > 2.5):
                captions.append((group[0]['start'], min(group[-1]['end'], scene['end']),
                                 ' '.join(item['text'] for item in group)))
                group = []
            group.append(word)
        if group:
            captions.append((group[0]['start'], min(group[-1]['end'], scene['end']),
                             ' '.join(item['text'] for item in group)))
    return captions


def visual_query(text, topic):
    terms = re.findall(r"[A-Za-z][A-Za-z'-]*", text)
    useful = [term for term in terms if len(term) > 2 and term.lower() not in STOP_WORDS]
    return ' '.join(dict.fromkeys(useful))[:90] if useful else topic
