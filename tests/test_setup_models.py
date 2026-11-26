"""Discovery, consent, process arguments and cancellation for first-run local models."""
import asyncio
import json
import threading
from types import SimpleNamespace

import httpx
import pytest

from core import setup_models
from core.request_security import install_request_security
from core.state_gate import STATE
from test_blueprints import create_app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(setup_models, '_jobs', {})
    app = create_app('setup')
    install_request_security(app)
    return app.test_client()


def test_recommends_by_ram_and_graphics_memory(monkeypatch):
    monkeypatch.setattr(setup_models.model_hardware, 'memory', lambda: (16, 8))
    assert setup_models._suggestion('ollama')['model'] == 'qwen3:4b-instruct-2507-q4_K_M'
    monkeypatch.setattr(setup_models.model_hardware, 'memory', lambda: (8, None))
    suggestion = setup_models._suggestion('lmstudio')
    assert suggestion['name'] == 'Granite 4.0 Micro' and suggestion['license'] == 'Apache 2.0'
    assert suggestion['link'] == 'https://lmstudio.ai/models/ibm/granite-4-micro'
    monkeypatch.setattr(setup_models.model_hardware, 'memory', lambda: (4, None))
    assert setup_models._suggestion('ollama') is None


def test_installation_is_distinct_from_an_empty_server(client, monkeypatch):
    monkeypatch.setattr(setup_models, '_probe', lambda *_: ('offline', []))
    monkeypatch.setattr(setup_models, '_installation', lambda *_: (False, None))
    assert client.get('/api/setup/local/ollama').json['state'] == 'missing'
    monkeypatch.setattr(setup_models, '_installation', lambda *_: (True, 'ollama.exe'))
    stopped = client.get('/api/setup/local/ollama')
    assert stopped.json['state'] == 'stopped' and stopped.json['can_start']
    assert stopped.headers['Cache-Control'] == 'no-store'
    monkeypatch.setattr(setup_models, '_probe', lambda *_: ('running', []))
    running = client.get('/api/setup/local/ollama').json
    assert running['state'] == 'running' and not running['can_start'] and running['models'] == []


def test_windows_can_time_out_before_refusing_an_empty_loopback_port(monkeypatch):
    def on_connect(request):
        raise httpx.ConnectTimeout('The loopback port has no listener', request=request)

    transport = httpx.MockTransport(on_connect)
    monkeypatch.setattr(setup_models.direct_model_http, 'create_transport', lambda _: httpx.Client(transport=transport))
    assert setup_models._probe('ollama', None) == ('offline', [])


@pytest.mark.parametrize('action', ['start', 'download'])
def test_side_effect_requires_a_json_confirmation(client, monkeypatch, action):
    monkeypatch.setattr(setup_models, '_probe', lambda *_: pytest.fail('Probed before confirmation'))
    path = f'/api/setup/local/ollama/{action}'
    assert client.post(path).status_code == 400
    assert client.post(path, json={'confirmed': 'yes'}).status_code == 400
    assert client.post(path, json={'confirmed': True}, headers={'Sec-Fetch-Site': 'cross-site'}).status_code == 403


def test_download_refuses_arbitrary_tags_and_provider_urls(client, monkeypatch):
    from core.routes import setup
    monkeypatch.setattr(setup_models, '_probe', lambda *_: pytest.fail('Probed a disallowed model or URL'))
    path = '/api/setup/local/ollama/download'
    assert client.post(path, json={'confirmed': True, 'model': 'arbitrary:cloud'}).status_code == 400
    monkeypatch.setattr(setup, '_provider', lambda _: SimpleNamespace(enabled=True, base_url='http://192.168.1.1:11434/v1'))
    assert client.get('/api/setup/local/ollama').json == {'supported': False}
    assert client.post(path, json={'confirmed': True, 'model': 'granite4:micro'}).status_code == 400


@pytest.mark.parametrize(('key', 'args'), [
    ('lmstudio', ['server', 'start', '--port', '1234', '--bind', '127.0.0.1']),
    ('ollama', ['serve']),
])
def test_confirmed_start_uses_the_expected_command_and_stays_on_loopback(client, monkeypatch, key, args):
    saved = []
    probes = iter([('offline', []), ('running', [])])
    monkeypatch.setattr(setup_models, '_probe', lambda *_: next(probes))
    monkeypatch.setattr(setup_models, '_installation', lambda _: (True, 'provider.exe'))
    monkeypatch.setenv('OLLAMA_HOST', '0.0.0.0')
    monkeypatch.setenv('OLLAMA_MODELS', 'scratch-models')

    def launch(argv, **kwargs):
        saved.append((argv, kwargs))
        return SimpleNamespace(poll=lambda: None)

    monkeypatch.setattr(setup_models.subprocess, 'Popen', launch)
    result = client.post(f'/api/setup/local/{key}/start', json={'confirmed': True})
    assert result.status_code == 202 and STATE.join_jobs(timeout=3)
    argv, kwargs = saved[0]
    assert argv == ['provider.exe', *args] and not kwargs.get('shell')
    if key == 'ollama':
        assert kwargs['env']['OLLAMA_HOST'] == '127.0.0.1:11434'
        assert kwargs['env']['OLLAMA_MODELS'] == 'scratch-models'
        assert kwargs['env']['OLLAMA_NO_CLOUD'] == '1'
    status = client.get(f"/api/setup/local/{key}/jobs/{result.json['id']}").json
    assert status['state'] == 'ready'


class PullStream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk


@pytest.mark.parametrize(('last', 'state'), [(b'{"status":"success"}\n', 'ready'), (b'', 'failed')])
def test_download_stream_uses_a_fixed_loopback_url_and_needs_success(client, monkeypatch, last, state):
    model = 'granite4:micro'
    inventories = iter([[], [], [model]])
    monkeypatch.setattr(setup_models, '_probe', lambda *_: ('running', next(inventories)))

    def incoming(request):
        assert str(request.url) == 'http://127.0.0.1:11434/api/pull'
        assert json.loads(request.content) == {'model': model, 'stream': True}
        return httpx.Response(200, stream=PullStream([b'{"status":"downloading","comple',
                                                      b'ted":1,"total":2}\n', last]))

    client_type = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: client_type(transport=httpx.MockTransport(incoming), **kwargs))
    result = client.post('/api/setup/local/ollama/download', json={'confirmed': True, 'model': model})
    assert result.status_code == 202 and STATE.join_jobs(timeout=3)
    status = client.get(f"/api/setup/local/ollama/jobs/{result.json['id']}").json
    assert status['state'] == state and status['model'] == model


def test_stop_closes_a_quiet_download_and_rejects_stale_ids(client, monkeypatch):
    entered, closed = threading.Event(), threading.Event()

    async def quiet(_job):
        try:
            entered.set()
            await asyncio.Event().wait()
        finally:
            closed.set()

    monkeypatch.setattr(setup_models, '_pull_updates', quiet)
    monkeypatch.setattr(setup_models, '_probe', lambda *_: ('running', []))
    path = '/api/setup/local/ollama/download'
    payload = {'confirmed': True, 'model': 'granite4:micro'}
    started = client.post(path, json=payload)
    assert started.status_code == 202 and entered.wait(2)
    assert client.post(path, json=payload).status_code == 409
    assert client.delete('/api/setup/local/ollama/jobs/old').status_code == 404
    url = f"/api/setup/local/ollama/jobs/{started.json['id']}"
    assert client.delete(url).json['state'] == 'stopping'
    assert closed.wait(2) and STATE.join_jobs(timeout=2)
    assert client.get(url).json['state'] == 'stopped'
