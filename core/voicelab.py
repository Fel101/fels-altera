"""Voice Lab's catalogue and OpenAI-style WAV endpoint. Discovery sends no speech text."""

import base64
import io
import json
import os
import re
import wave
from dataclasses import dataclass
from hashlib import sha256

from core import fels_suite, voice_http

OFFLINE = 'Voice Lab is unavailable, so start it again to hear replies.'
FALLBACK = 'Voice Lab is unavailable, so Altera is using its own Kokoro voice.'
NO_VOICES = 'Voice Lab is running. Get a voice ready there to use it for speech.'
SPEECH_TIMEOUT_S = 120
_LICENCES = {'yes': 'Commercial use OK', 'conditions': 'With conditions', 'unclear': 'Unclear', 'no': 'Not cleared'}


class LabError(Exception):
    """A message that can be shown without exposing the Lab's responses or addresses."""


class LabUnavailable(LabError):
    pass


@dataclass(frozen=True)
class Catalogue:
    url: str
    library: str
    voices: list[dict]
    notice: str | None


def valid_name(name):
    return isinstance(name, str) and 0 < len(name) <= 320 and name == name.strip() and name.isprintable()


def _json(url, endpoint, limit):
    return json.loads(voice_http.read(url, endpoint, timeout=1.5, limit=limit))


def discover(given=''):
    """Only a person-supplied URL may be remote, even when the shared address file names one."""
    explicit = {url.rstrip('/') for url in (given, os.environ.get('VOICE_LAB_URL', '')) if url}
    wrong = None

    def ask(url):
        nonlocal wrong
        if not voice_http.is_loopback(url) and url not in explicit:
            return None
        try:
            answer = _json(url, '/api/health', 128 * 1024)
        except (voice_http.VoiceHTTPError, ValueError):
            return None
        app = fels_suite.identify(answer)
        if url in explicit and app != 'voice-lab':
            name = fels_suite.short_name(app) if app in fels_suite.APPS else 'another program'
            wrong = f'That address is {name}, not Voice Lab.'
        return answer

    found = fels_suite.find_app('voice-lab', ask=ask, given=given)
    if not found['url']:
        raise LabUnavailable(f'{wrong} Check the Voice Lab address.' if wrong else OFFLINE)
    library = found['health'].get('lab_id')
    return found['url'], library if isinstance(library, str) else '', wrong


def catalogue(given=''):
    url, library, notice = discover(given)
    try:
        answer = _json(url, '/api/voices', 2 * 1024 * 1024)
        rows = answer.get('voices') if isinstance(answer, dict) else None
        if not isinstance(rows, list):
            raise ValueError()
    except voice_http.VoiceHTTPUnavailable:
        raise LabUnavailable(OFFLINE) from None
    except (voice_http.VoiceHTTPError, ValueError):
        raise LabError('Voice Lab could not list its voices. Check Voice Lab and try again.') from None

    voices = {}
    for row in rows:
        if not (isinstance(row, dict) and row.get('ready') is True and valid_name(row.get('name'))):
            continue
        name = row['name']
        licence = row.get('licence')
        licence = licence if isinstance(licence, dict) else {}
        rating = licence.get('rating')
        reason = licence.get('reason')
        label = _LICENCES.get(rating, 'Unclear') if isinstance(rating, str) else 'Unclear'
        voices.setdefault(name, {'id': name, 'label': name, 'kind': 'cast' if row.get('kind') == 'cast' else 'preset',
                                 'voice_id': row.get('voice_id'), 'identity_id': row.get('identity_id'),
                                 'model': row.get('model'), 'licence': {'label': label, 'reason': reason[:400] if isinstance(reason, str) else ''}})
    return Catalogue(url, library, sorted(voices.values(), key=lambda voice: voice['kind'] != 'cast'), notice)


def default_voice(voices):
    return next((row['id'] for row in voices if row['kind'] == 'preset'), voices[0]['id'] if voices else '')


def status(given=''):
    try:
        found = catalogue(given)
    except LabError as error:
        return {'ready': False, 'count': 0, 'message': str(error)}
    count = len(found.voices)
    remote = not voice_http.is_loopback(found.url)
    message = NO_VOICES
    if count:
        place = ' on your configured server' if remote else ''
        noun = 'voice' if count == 1 else 'voices'
        message = f'Voice Lab is running{place} with {count} {noun}. Use it for speech?'
        if remote:
            message += ' Speech text will go to that server.'
    if found.notice:
        message = f'{found.notice} {message}'
    return {'ready': bool(count), 'count': count, 'remote': remote, 'message': message, 'notice': found.notice}


def _portable_json(url, endpoint, data, *, metadata=None):
    if not voice_http.is_loopback(url):
        raise LabError('To preview or import a voice file, start Voice Lab on this computer and select its local address.')
    try:
        args = {'reference_metadata': metadata} if metadata is not None else {}
        result = json.loads(voice_http.read(url, endpoint, data=data, timeout=20, limit=1024 * 1024, **args))
        if isinstance(result, dict):
            return result
    except voice_http.VoiceHTTPUnavailable:
        raise LabUnavailable('Voice Lab did not reply. Check its Cast and try again.') from None
    except (ValueError, voice_http.VoiceHTTPError):
        pass
    raise LabError('Voice Lab could not process this voice file. Check it in Voice Lab or open the character without it.')


def preview_persona(url, data):
    archive = _portable_json(url, '/api/portable/preview', data).get('archive')
    if isinstance(archive, dict) and archive.get('kind') == 'persona' and archive.get('archive_sha256') == sha256(data).hexdigest():
        rows = archive.get('voices')
        title = archive.get('title')
        if valid_name(title) and isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], dict):
            return title, rows[0]
    raise LabError('This file does not contain one verified Voice Lab voice. Ask for a new voice file.')


def import_persona(url, data):
    metadata = base64.urlsafe_b64encode(json.dumps({'archive_sha256': sha256(data).hexdigest(), 'permission': True})
                                        .encode('utf-8')).decode('ascii').rstrip('=')
    result = _portable_json(url, '/api/portable/import', data, metadata=metadata).get('imported')
    identity = result.get('identity') if isinstance(result, dict) and result.get('kind') == 'persona' else None
    if isinstance(identity, dict) and _valid_identity(identity.get('id')):
        return identity
    raise LabError('Voice Lab did not confirm the import. Check its Cast before trying again.')


def _valid_identity(value):
    return isinstance(value, str) and bool(re.fullmatch(r'[a-f0-9]{32}', value))


def approve_persona(url, identity, reference):
    if not _valid_identity(identity) or (reference is not None and not _valid_identity(reference)):
        raise LabError('Voice Lab did not return a valid voice. Review it in Cast.')
    body = {'status': 'approved'}
    if reference is not None:
        body['reference_version_id'] = reference
    _portable_json(url, f'/api/identities/{identity}/update', json.dumps(body).encode('utf-8'))


def wav(url, voice, text, *, limit):
    body = json.dumps({'input': text, 'voice': voice, 'response_format': 'wav'}).encode('utf-8')
    try:
        data = voice_http.read(url, '/v1/audio/speech', data=body, timeout=SPEECH_TIMEOUT_S, limit=limit)
    except voice_http.VoiceHTTPUnavailable as error:
        if error.timed_out:
            raise LabError('Voice Lab took too long to respond. Check its queue and try again.') from None
        raise LabUnavailable(OFFLINE) from None
    except voice_http.VoiceHTTPError as error:
        messages = {404: 'That voice is no longer available in Voice Lab. Choose another voice.',
                    409: 'Voice Lab is busy. Try again when its current job is done.',
                    503: 'Voice Lab could not load the voice. Check its model in Voice Lab.'}
        raise LabError(messages.get(error.status, 'Voice Lab could not make speech. Try again in Voice Lab.')) from None
    try:
        with wave.open(io.BytesIO(data), 'rb') as source:
            frame_bytes = source.getnchannels() * source.getsampwidth()
            frames = source.getnframes()
            if not 0 < frames * frame_bytes <= len(data):
                raise ValueError()
            if len(source.readframes(frames)) != frames * frame_bytes:
                raise ValueError()
    except (wave.Error, EOFError, ValueError):
        raise LabError('Voice Lab returned unreadable audio. Try speaking the line in Voice Lab.') from None
    return data
