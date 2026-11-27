"""Voice Lab integration against the frozen OpenAPI document. The transport sends no real requests."""

import io
import json
import wave
from pathlib import Path

import pytest

from core import fels_suite, pals, tts, voice_http, voicelab
from test_blueprints import create_app

SPEC = json.loads(Path(__file__).with_name('voice_lab_openapi.json').read_text(encoding='utf-8'))['paths']
PORT = 'http://127.0.0.1:8765'
HEALTH = {'fels': {'contract': 1, 'app': 'voice-lab', 'version': '0.4.0', 'api': 1}}
VOICES = [
    {'name': 'af_heart', 'kind': 'preset', 'model': 'kokoro', 'ready': True, 'licence': {'rating': 'yes'}},
    {'name': 'Fable (Warm)', 'kind': 'cast', 'model': 'turbo', 'ready': True,
     'licence': {'rating': 'conditions', 'reason': 'Synthetic demo voice.'}},
    {'name': 'Draft', 'kind': 'cast', 'model': 'turbo', 'ready': False},
]


def wav_bytes():
    stream = io.BytesIO()
    with wave.open(stream, 'wb') as writer:
        writer.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
        writer.writeframes(b'\x01\x00' * 240)
    return stream.getvalue()


@pytest.fixture
def lab(monkeypatch, altera_config):
    monkeypatch.delenv('VOICE_LAB_URL', raising=False)
    monkeypatch.setattr(tts, 'AUDIO_CACHE_DIR', str(altera_config.parent / 'speech'))
    calls = []
    routes = {(PORT, '/api/health'): HEALTH, (PORT, '/api/voices'): {'voices': VOICES},
              (PORT, '/v1/audio/speech'): wav_bytes()}

    def read(url, endpoint, *, timeout, limit, data=None):
        body = json.loads(data) if data is not None else None
        if endpoint not in {'/api/info', '/api/speak'}:
            operation = SPEC[endpoint]['post' if data is not None else 'get']
            assert operation['x-fels-suite-api'] == 1
        if endpoint == '/v1/audio/speech':
            schema = operation['requestBody']['content']['application/json']['schema']
            assert set(schema['required']) <= set(body) <= set(schema['properties'])
        calls.append((url, endpoint, body))
        answer = routes.get((url, endpoint))
        if answer is None:
            raise voice_http.VoiceHTTPUnavailable()
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, bytes) else json.dumps(answer).encode()

    monkeypatch.setattr(voice_http, 'read', read)
    return calls, routes


def test_voice_catalogue_contract_and_selection(lab):
    expected = SPEC['/api/voices']['get']['responses']['200']['content']['application/json']['schema']
    fields = expected['properties']['voices']['items']['properties']
    assert {'ready', 'name', 'licence', 'kind', 'model', 'made_from'} <= fields.keys()
    assert {'rating', 'reason'} <= fields['licence']['properties'].keys()
    cfg = tts.settings({'voice': {'tts_engine': 'voicelab'}})
    options = pals.voices(cfg)
    assert [entry['id'] for entry in options['options']] == ['Fable (Warm)', 'af_heart']
    assert options['default'] == 'af_heart'
    assert options['options'][0]['licence'] == {'label': 'With conditions', 'reason': 'Synthetic demo voice.'}
    assert voicelab.status()['count'] == 2


def test_discovery_prefers_the_address_file_and_checks_legacy_lab_identity(lab):
    calls, routes = lab
    address = 'http://127.0.0.1:17665'
    assert fels_suite.write_address('voice-lab', address, '0.4.0', 1, 'test')
    routes[(address, '/api/health')] = {'app': "Fel's Voice Lab", 'lab_id': 'stand-in'}
    assert voicelab.discover() == (address, 'stand-in', None)
    assert calls == [(address, '/api/health', None)]


def test_wrong_program_is_named_and_receives_no_speech(lab):
    calls, routes = lab
    address = 'http://127.0.0.1:17665'
    routes[(address, '/api/health')] = {'online': True, 'gpu_rule': {}}
    url, _, notice = voicelab.discover(address)
    assert url == PORT and notice == 'That address is NPC Forge, not Voice Lab.'
    assert calls == [(address, '/api/health', None), (PORT, '/api/health', None)]


def test_only_an_explicit_address_can_send_speech_to_a_remote_lab(lab, monkeypatch):
    calls, routes = lab
    address = 'https://lab.example'
    routes[(address, '/api/health')] = HEALTH
    routes[(address, '/api/voices')] = {'voices': VOICES}
    assert fels_suite.write_address('voice-lab', address, '0.4.0', 1, 'test')
    assert voicelab.status()['remote'] is False and not any(target == address for target, _, _ in calls)
    monkeypatch.setenv('VOICE_LAB_URL', address)
    found = voicelab.status()
    assert found['remote'] is True and 'Speech text will go to that server.' in found['message']


def test_cast_clip_uses_its_name_on_the_wav_route_and_existing_chunk_limit(lab, altera_config):
    altera_config.write_text('voice:\n  tts_engine: voicelab\n', encoding='utf-8')
    calls, _ = lab
    text = ' '.join(f'At marker {index:02d} a gentle breeze lifts the leaves in the courtyard.' for index in range(20))
    path = tts.synthesize_long(text, person_voice='Fable (Warm)')
    assert path.endswith('.wav')
    posts = [body for _, endpoint, body in calls if endpoint == '/v1/audio/speech']
    assert [p['input'] for p in posts] == tts.chunk_for_speech(text)
    assert len(posts) > 1 and all(len(p['input']) <= 400 for p in posts)
    assert all(p['voice'] == 'Fable (Warm)' and p['response_format'] == 'wav' for p in posts)
    assert tts.synthesize_long(text, person_voice='Fable (Warm)') == path
    assert len([x for x in calls if x[2] is not None]) == len(posts)


def test_fallback_is_local_only_and_says_which_engine_spoke(lab, altera_config, monkeypatch):
    altera_config.write_text('voice:\n  tts_engine: voicelab\n  cloud_fallback: true\n', encoding='utf-8')
    calls, routes = lab
    routes.pop((PORT, '/api/health'))
    routes[(tts.DEFAULT_KOKORO_URL, '/api/info')] = {'loaded': ['kokoro']}
    routes[(tts.DEFAULT_KOKORO_URL, '/api/speak')] = wav_bytes()
    monkeypatch.setattr(tts, 'generate_speech', lambda *_a, **_k: pytest.fail('Lab must never fall back to Microsoft'))
    tts._reset_state()
    try:
        messages = []
        result = tts.synthesize_long('A new line for Kokoro.', person_voice='Fable (Warm)', notice=messages.append)
        assert result.endswith('.wav') and messages == [voicelab.FALLBACK]
        assert [body['voice'] for _, endpoint, body in calls if endpoint == '/api/speak'] == ['af_heart']
        with pytest.raises(tts.TTSUnavailable, match='Voice Lab is unavailable'):
            tts.synthesize_long('A sample.', person_voice='Fable (Warm)', preview=True)
        altera_config.write_text('voice:\n  tts_engine: voicelab\n  kokoro:\n    url: https://remote.example\n', encoding='utf-8')
        with pytest.raises(tts.TTSUnavailable, match='Voice Lab is unavailable'):
            tts.synthesize_long('This must stay here.')
        assert not any(url == 'https://remote.example' for url, _, _ in calls)
    finally:
        tts._reset_state()


CLONED = [
    {'name': 'af_heart', 'kind': 'preset', 'model': 'kokoro', 'ready': True},
    {'name': 'Rosa', 'kind': 'cast', 'model': 'turbo', 'ready': True, 'made_from': 'recording'},
    {'name': 'Ash', 'kind': 'cast', 'model': 'turbo', 'ready': True, 'made_from': 'words'},
    {'name': 'Old', 'kind': 'cast', 'model': 'turbo', 'ready': True},                       # Voice Lab 1.0.0 does not say
    {'name': 'Odd', 'kind': 'cast', 'model': 'turbo', 'ready': True, 'made_from': 'a mystery'},
]


def test_the_catalogue_keeps_what_the_lab_says_each_voice_is_made_from(lab):
    _, routes = lab
    routes[(PORT, '/api/voices')] = {'voices': CLONED}
    made = {row['id']: row['made_from'] for row in voicelab.catalogue().voices}
    assert made == {'af_heart': 'model', 'Rosa': 'recording', 'Ash': 'words', 'Old': None, 'Odd': None}
    assert [name for name, value in made.items() if voicelab.refusal({'made_from': value})] == ['Rosa', 'Old', 'Odd']


def test_an_optional_level_never_speaks_with_a_voice_cloned_from_a_recording(lab, altera_config, monkeypatch):
    from core import adult
    altera_config.write_text('voice:\n  tts_engine: voicelab\n', encoding='utf-8')
    calls, routes = lab
    routes[(PORT, '/api/voices')] = {'voices': CLONED}

    def spoken():
        voices = [body['voice'] for _, endpoint, body in calls if endpoint == '/v1/audio/speech']
        calls.clear()
        return voices

    assert tts.synthesize_long('Hello there.', person_voice='Rosa') and spoken() == ['Rosa']   # with no level on
    monkeypatch.setattr(adult, 'effective_level', lambda store=None: 'romantic')
    messages = []
    with pytest.raises(tts.TTSUnavailable, match="cloned from a person's recording"):
        tts.synthesize_long('Hello there.', person_voice='Rosa', notice=messages.append)    # not even from the cache
    with pytest.raises(tts.TTSUnavailable, match='does not say whether'):
        tts.synthesize_long('Hello there.', person_voice='Old', notice=messages.append)
    assert messages == [voicelab.PERSON_VOICE, voicelab.UNTRACED_VOICE] and spoken() == []
    assert tts.synthesize_long('Hello there.', person_voice='Ash') and spoken() == ['Ash']
    assert tts.synthesize_long('Hello there.', person_voice='af_heart') and spoken() == ['af_heart']

    def broken(store=None):
        raise RuntimeError('the saved level cannot be read')

    monkeypatch.setattr(adult, 'effective_level', broken)
    assert tts.person_voices_allowed() is False                                             # any doubt means no


def test_bad_speech_is_not_cached_or_sent_elsewhere(lab, altera_config):
    _, routes = lab
    routes[(PORT, '/v1/audio/speech')] = b'not a WAV'
    altera_config.write_text('voice:\n  tts_engine: voicelab\n', encoding='utf-8')
    with pytest.raises(tts.TTSUnavailable, match='unreadable audio'):
        tts.synthesize_long('Hello.')
    assert not list(Path(tts.AUDIO_CACHE_DIR).glob('*.wav'))


def test_setup_offers_lab_and_switches_only_after_opt_in(lab, altera_config):
    altera_config.write_text('voice:\n  tts_engine: kokoro\n  tts_enabled: false\n', encoding='utf-8')
    client = create_app('engagement').test_client()
    offer = client.get('/api/voice/lab').json
    assert offer['ready'] is True and offer['count'] == 2 and offer['selected'] is False
    assert tts.settings()['engine'] == 'kokoro'
    result = client.post('/api/voice/engine', json={'engine': 'voicelab'})
    assert result.json == {'engine': 'voicelab', 'tts_enabled': True}
    assert tts.settings()['engine'] == 'voicelab' and client.get('/api/voice/lab').json['selected'] is True


def test_explicit_microsoft_voice_still_obeys_adult_mode(monkeypatch, altera_config):
    altera_config.write_text('voice:\n  tts_engine: edge\n', encoding='utf-8')
    monkeypatch.setattr('core.adult.effective_level', lambda: 'romantic')
    monkeypatch.setattr(tts, 'generate_speech', lambda *_a, **_k: pytest.fail('No cloud during adult mode'))
    with pytest.raises(tts.TTSUnavailable, match='adult mode'):
        tts.synthesize_long('An otherwise ordinary line.')
