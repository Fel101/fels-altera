"""Source-folder independence, shared startup and honest read-only health probes."""
import io
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
from threading import Event
import time
from types import SimpleNamespace
import urllib.error
import urllib.request

import pytest
import yaml

from core.app import create_app
from core.health import HealthService
from core.paths import CODE_ROOT, data_path


def test_shared_suite_vectors():
    from core.fels_suite import check_vectors
    failures = check_vectors(Path(__file__).with_name('suite_vectors.json'))
    assert not failures, '\n'.join(failures)


@pytest.fixture
def runtime_config(altera_config):
    altera_config.write_text(yaml.safe_dump({'app': {'host': '127.0.0.1', 'port': 8767, 'debug': False},
                                        'llm': {'provider': 'lmstudio', 'model': 'unit-model'}, 'voice': {'tts_enabled': False},
                                        'memory': {'top_k': 5}, 'imagegen': {'enabled': False}, 'tools': {'enabled': False}}), 'utf-8')
    return altera_config


def test_default_paths_follow_instance_not_cwd(runtime_config, tmp_path, monkeypatch):
    from core.datastore import DataStore
    from core.memory import MemoryStore
    from core.profile import load_user_profile, save_user_profile
    outside = tmp_path / 'outside'
    outside.mkdir()
    monkeypatch.chdir(outside)
    store = DataStore()
    assert store.db_path == str(data_path('conversations.db'))
    sid = store.create_session('Portable')
    save_user_profile({'name': 'Unicode — 茶'})
    monkeypatch.chdir(tmp_path)
    assert store.get_session(sid).name == 'Portable'
    assert load_user_profile()['name'] == 'Unicode — 茶'
    assert MemoryStore().persist_directory == str(data_path('chroma_db'))
    assert not (outside / 'data').exists() and not (outside / 'user_profile.yaml').exists()


def test_factory_initializes_schema_and_preserves_session_key(runtime_config):
    from core.datastore import get_datastore
    first = create_app(autostart=False)
    second = create_app(autostart=False)
    assert first.secret_key == second.secret_key
    with get_datastore()._connect() as conn:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {'messages', 'sessions', 'messages_fts', 'chat_turns'} <= names
    assert first.test_client().get('/api/offline/status').json['online'] is True


def test_bad_config_fails_before_serving_with_a_safe_message(runtime_config):
    runtime_config.write_text('llm: [broken', 'utf-8')
    with pytest.raises(RuntimeError, match='config.yaml must be a readable YAML mapping'):
        create_app(autostart=False)




@pytest.mark.parametrize('existing', [None, 'app:\n  port: 59876\n', 'app: [broken'])
def test_in_place_startup_seeds_only_missing_config(runtime_config, monkeypatch, existing):
    """Use a temporary source folder so no test reads or rewrites the owner's config."""
    from core import runtime
    home = runtime_config.parent
    monkeypatch.setattr(runtime, 'CODE_ROOT', home)
    starter = b'app:\n  port: 8767\nvoice:\n  tts_enabled: false\n'
    (home / 'config.example.yaml').write_bytes(starter)
    if existing is None:
        runtime_config.unlink()
        expected = starter
    else:
        runtime_config.write_text(existing, 'utf-8')
        expected = runtime_config.read_bytes()
    if existing == 'app: [broken':
        with pytest.raises(RuntimeError, match='config.yaml must be a readable YAML mapping'):
            runtime.prepare()
        assert runtime_config.read_bytes() == expected
        assert not (home / 'data/conversations.db').exists()
    else:
        runtime.prepare()
        assert runtime_config.read_bytes() == expected
        runtime.prepare()
        assert runtime_config.read_bytes() == expected
    assert (home / 'config.example.yaml').read_bytes() == starter


def test_service_startup_is_shared_idempotent_and_respects_opt_out(runtime_config, monkeypatch):
    from core import runtime, companions, voice_service, presence, reflection
    calls = []
    monkeypatch.setattr(companions, 'migrate_legacy', lambda: calls.append('migration'))
    monkeypatch.setattr(voice_service, 'start_in_background', lambda: calls.append('voice'))
    # The owned schedulers are daemon threads that outlive the test. Left running, a later test's datastore can be
    # touched at any moment (a reflection tick calls the shared store). This test is about migration and voice only.
    monkeypatch.setattr(presence, 'start_scheduler', lambda app: None)
    monkeypatch.setattr(reflection, 'start_scheduler', lambda app: None)
    runtime_config.write_text(runtime_config.read_text('utf-8').replace('tts_enabled: false', 'tts_enabled: true'), 'utf-8')
    monkeypatch.setattr(runtime, '_started', set())
    monkeypatch.delenv('ALTERA_DISABLE_AUTOSTART')
    create_app()
    create_app()
    assert calls == ['migration', 'voice']
    monkeypatch.setenv('ALTERA_DISABLE_AUTOSTART', '1')
    monkeypatch.setattr(runtime, '_started', set())
    create_app()
    assert calls == ['migration', 'voice']


def test_rotating_log_is_idempotent_and_redacts_configured_credentials(runtime_config):
    from core.runtime import configure_logging
    runtime_config.write_text(runtime_config.read_text('utf-8') + '\nsecret: fixture-private-key\n', 'utf-8')
    configure_logging()
    configure_logging()
    handlers = [h for h in logging.getLogger().handlers if getattr(h, 'altera_file', False)]
    assert len(handlers) == 1 and handlers[0].maxBytes == 5 * 1024 * 1024 and handlers[0].backupCount == 3
    logging.getLogger('altera.test').warning('Credential fixture-private-key; api_key=also-private')
    handlers[0].flush()
    text = data_path('altera.log').read_text('utf-8')
    assert 'fixture-private-key' not in text and 'also-private' not in text and '[redacted]' in text


def test_health_lists_models_without_inference_closes_client_and_counts_all_memories(runtime_config, monkeypatch):
    import openai
    from core import memory
    calls = []
    class Client:
        def __init__(self, **options):
            calls.append(options)
            self.models = SimpleNamespace(list=lambda: SimpleNamespace(data=[SimpleNamespace(id='unit-model')]))
        def close(self):
            calls.append('closed')
    monkeypatch.setattr(openai, 'OpenAI', Client)
    monkeypatch.setattr(memory, 'get_memory_manager', lambda: SimpleNamespace(store=SimpleNamespace(collection=SimpleNamespace(count=lambda: 23))))
    health = HealthService()
    first, second = health.check(), health.check()
    assert first['llm']['status'] == 'ok' and first['memory']['count'] == 23
    assert first['llm'] == second['llm'] and len(calls) == 2
    assert calls[0]['timeout'] == 2 and calls[0]['max_retries'] == 0 and calls[1] == 'closed'
    assert 'inference has not been tested' in first['llm']['message']


def test_health_never_calls_a_failed_memory_store_ready(runtime_config, monkeypatch):
    from core import memory
    def broken():
        raise OSError('unreadable')
    monkeypatch.setattr(memory, 'get_memory_manager', lambda: SimpleNamespace(store=SimpleNamespace(collection=SimpleNamespace(count=broken))))
    health = HealthService()
    monkeypatch.setattr(health, '_model', lambda *a: {'status': 'warning', 'message': 'Model not listed'})
    assert health.check()['memory']['status'] == 'error'


def test_private_audio_is_served_from_instance_cache_without_browser_caching(runtime_config):
    from core.tts import AUDIO_CACHE_DIR
    path = Path(AUDIO_CACHE_DIR) / 'fixture.wav'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'fake audio')
    response = create_app(autostart=False).test_client().get('/static/audio/tts/fixture.wav')
    assert response.data == b'fake audio' and response.headers['Cache-Control'] == 'no-store'
    response.close()


def test_voice_child_output_uses_the_application_logger(runtime_config, caplog):
    from types import SimpleNamespace
    from core.owned_process import OwnedChildProcess
    from core.voice_service import _log_line
    stream = io.StringIO('Ready for speech\n')
    owner = OwnedChildProcess(lambda parent: [], cwd='', environment={}, output=_log_line)
    owner._process = SimpleNamespace(stdout=stream)
    with caplog.at_level(logging.INFO):
        owner._read_output()
    assert stream.closed and owner._reader_closed and 'Voice service: Ready for speech' in caplog.text


def test_real_waitress_wsgi_startup_from_another_directory(tmp_path):
    home, outside = tmp_path / 'instance', tmp_path / 'outside'
    outside.mkdir()
    home.mkdir()
    original_config = (CODE_ROOT / 'config.example.yaml').read_bytes() + b'\n# Preserve this existing configuration.\n'
    original_local = yaml.safe_dump({'remote_access': {'enabled': True, 'tailscale_host': 'altera.unit.ts.net', 'allowed_logins': ['owner@unit.test']}}).encode('utf-8')
    original_profile = b'name: Synthetic owner\npreferences:\n  voice_enabled: false\n'
    (home / 'config.yaml').write_bytes(original_config)
    (home / 'config.local.yaml').write_bytes(original_local)
    (home / 'user_profile.yaml').write_bytes(original_profile)
    receipt = tmp_path / 'server.json'
    code = """
import json,os
from pathlib import Path
import openai
def blocked(*a, **k):
    raise RuntimeError('Inference is forbidden in this startup test')
openai.OpenAI = blocked
from core.app import create_app
from waitress.server import create_server
server = create_server(create_app(autostart=False), host='127.0.0.1', port=0, clear_untrusted_proxy_headers=False)
receipt = Path(os.environ['ALTERA_TEST_RECEIPT'])
receipt.with_suffix('.tmp').write_text(json.dumps({'port': server.effective_port}))
os.replace(receipt.with_suffix('.tmp'), receipt)  # never seen half-written
server.run()
"""
    env = {**os.environ, 'ALTERA_HOME': str(home), 'ALTERA_DISABLE_AUTOSTART': '1', 'ALTERA_TEST_RECEIPT': str(receipt), 'PYTHONPATH': str(CODE_ROOT)}
    process = subprocess.Popen([sys.executable, '-c', code], cwd=outside, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        deadline = time.monotonic() + 15
        while not receipt.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.05)
        assert receipt.exists(), process.stderr.read().decode() if process.poll() is not None else 'Server did not become ready'
        port = json.loads(receipt.read_text())['port']
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/offline/status', timeout=3) as response:
            assert json.load(response)['online']
        assert (home / 'data/conversations.db').exists() and not (outside / 'data').exists()
        assert (home / 'config.yaml').read_bytes() == original_config
        assert (home / 'config.local.yaml').read_bytes() == original_local
        assert (home / 'user_profile.yaml').read_bytes() == original_profile
        request = urllib.request.Request(f'http://127.0.0.1:{port}/api/profile', method='POST', headers={'Origin': 'https://other.example'})
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request, timeout=3)
        assert error.value.code == 403
        def proxy(headers):
            return urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{port}/api/offline/status', headers=headers), timeout=3)
        trusted = {'Host': 'altera.unit.ts.net', 'X-Forwarded-Proto': 'https', 'X-Forwarded-For': '100.64.1.2', 'Tailscale-User-Login': 'owner@unit.test'}
        with proxy(trusted) as response:
            assert response.status == 200
        for headers in ({**trusted, 'Tailscale-User-Login': 'someone@unit.test'}, {**trusted, 'X-Forwarded-Proto': 'http'}, {'Host': 'altera.unit.ts.net'}):
            with pytest.raises(urllib.error.HTTPError) as error:
                proxy(headers)
            assert error.value.code == 403
    finally:
        process.terminate()
        process.communicate(timeout=5)


def test_health_metadata_warning_auth_failure_and_cache_invalidation(runtime_config, monkeypatch):
    import openai
    calls = []
    class Client:
        def __init__(self, **options):
            calls.append(options)
            self.models = SimpleNamespace(list=lambda: SimpleNamespace(data=[]))
        def close(self):
            pass
    monkeypatch.setattr(openai, 'OpenAI', Client)
    health = HealthService()
    assert health.check()['llm']['status'] == 'warning'
    health.check()
    assert len(calls) == 1
    runtime_config.write_text(runtime_config.read_text('utf-8').replace('unit-model', 'changed-model'), 'utf-8')
    health.check()
    assert len(calls) == 2
    class AuthError(Exception):
        status_code = 401
    def blocked(**options):
        raise AuthError('credential=must-never-leak')
    monkeypatch.setattr(openai, 'OpenAI', blocked)
    result = HealthService().check()['llm']
    assert result['status'] == 'error' and result['reachable'] is True
    assert 'credentials' in result['message'] and 'must-never-leak' not in str(result)


def test_health_respects_speech_preference_without_starting_service(runtime_config, monkeypatch):
    from core import voice_service
    from core.profile import save_user_profile
    runtime_config.write_text(runtime_config.read_text('utf-8').replace('tts_enabled: false', 'tts_enabled: true'), 'utf-8')
    save_user_profile({'preferences': {'voice_enabled': False}})
    monkeypatch.setattr(voice_service, 'status', lambda **kw: pytest.fail('Disabled speech must not be probed'))
    service = HealthService()
    monkeypatch.setattr(service, '_model', lambda *a: {'status': 'warning', 'message': 'Fixture'})
    assert service.check()['voice']['status'] == 'disabled'


def test_provider_settings_use_config_before_the_first_chat(runtime_config, monkeypatch):
    from core import llm
    monkeypatch.setattr(llm, '_provider_registry', None)
    registry = llm.get_provider_registry_instance()
    assert registry.get_active_model() == 'unit-model'


@pytest.mark.parametrize('configured_port, expected_port', [(None, 8767), (5001, 5001), (0, 59876)])
def test_launcher_advertises_the_bound_address_until_exit(runtime_config, monkeypatch, configured_port, expected_port):
    from waitress import server
    from core import fels_suite, runtime
    from core.backups import MAX_ARCHIVE_BYTES
    from core.version import SUITE_API, VERSION

    settings = yaml.safe_load(runtime_config.read_text('utf-8'))
    settings['app']['host'] = 'localhost'
    settings['app'].pop('port')
    if configured_port is not None:
        settings['app']['port'] = configured_port
    runtime_config.write_text(yaml.safe_dump(settings), 'utf-8')
    address = fels_suite.address_path('altera')
    inbox = fels_suite.inbox_dir('altera')
    app = create_app(autostart=False)
    closed = []
    opened_urls = []
    opened = Event()

    def open_browser(url):
        opened_urls.append(url)
        opened.set()

    def run():
        entry = json.loads(address.read_text('utf-8'))
        assert fels_suite.read_address('altera') == entry
        assert entry['url'] == f'http://127.0.0.1:{expected_port}'
        assert (entry['app'], entry['version'], entry['api'], entry['pid']) == ('altera', VERSION, SUITE_API, os.getpid())
        assert entry['instance'] and entry['started']
        assert inbox.is_dir()

    def bind(application, **options):
        assert application is app and not address.exists()
        assert options['host'] == 'localhost'
        assert options['port'] == (8767 if configured_port is None else configured_port)
        assert options['threads'] > 1 and options['clear_untrusted_proxy_headers'] is False
        assert options['max_request_body_size'] >= MAX_ARCHIVE_BYTES
        return SimpleNamespace(effective_host='127.0.0.1', effective_port=expected_port, run=run,
                               close=lambda: closed.append(True))

    monkeypatch.setattr(server, 'create_server', bind)
    monkeypatch.setattr(runtime, '_open_when_ready', open_browser)
    runtime.main(app, args=SimpleNamespace(dev=False, open=True))
    assert opened.wait(timeout=3) and opened_urls == [f'http://localhost:{expected_port}']
    assert closed and inbox.is_dir() and not address.exists()
    assert yaml.safe_load(runtime_config.read_text('utf-8')) == settings


def test_suite_health_stays_public_while_dependency_checks_keep_their_guards(runtime_config, monkeypatch):
    from core import lock
    from core.state_gate import STATE
    from core.version import VERSION

    calls = []
    details = {'llm': {'model': 'private-model'}, 'memory': {'count': 5}}

    def check(self, session_id):
        calls.append(session_id)
        return details

    monkeypatch.setattr(HealthService, 'check', check)
    client = create_app(autostart=False).test_client()
    expected = {'ok': True, 'fels': {'contract': 1, 'app': 'altera', 'version': VERSION, 'api': 1}}
    detail_path = '/api/health/details?session_id=42'
    assert client.get(detail_path).json == details
    lock.set_pin('1234')
    for kids_view, denied_status in [(False, 423), (True, 403)]:
        with client.session_transaction() as browser:
            browser['kids_view'] = kids_view
        response = client.get('/api/health?session_id=42')
        assert response.status_code == 200 and response.json == expected
        assert response.headers['Cache-Control'] == 'no-store'
        assert client.get(detail_path).status_code == denied_status
    with monkeypatch.context() as restore:
        restore.setattr(STATE, 'enter', lambda *args: pytest.fail('Discovery must not acquire a data lease'))
        assert client.get('/api/health').json == expected
    assert calls == [42]
    assert client.get('/api/health', base_url='http://other.example').status_code == 403
