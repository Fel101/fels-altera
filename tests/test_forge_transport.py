"""Exercise Forge transport against disposable loopback-only HTTP fixtures."""
import json
from types import SimpleNamespace
import urllib.error

import pytest

from core import forge
from core import fels_suite

pytest_plugins = ('test_voice_http',)
NEW_HEALTH = {"fels": fels_suite.health_block("npc-forge", "1.1.0", 44)}
OLD_HEALTH = {"online": True, "gpu_rule": {}}
LAB_HEALTH = {"app": "Fel's Voice Lab", "lab_id": "fixture"}


def client_for(url):
    return forge.ForgeClient({'companion': {'forge': {'url': url}}})


def request_at(url, method, path, body=None):
    return client_for(url)._request_at(url, method, path, body)


@pytest.mark.parametrize("answers, url, error", [
    ({8766: NEW_HEALTH}, 8766, ""),
    ({8765: OLD_HEALTH}, 8765, ""),
    ({8765: LAB_HEALTH}, None, "Voice Lab answered"),
    ({}, None, "NPC Forge is not running"),
    ({8766: TimeoutError()}, None, "Could not reach NPC Forge"),
])
def test_discovers_current_and_legacy_forge_by_health(answers, url, error, monkeypatch):
    from urllib.parse import urlsplit
    monkeypatch.delenv("NPCFORGE_URL", raising=False)
    calls = []

    def respond(request, **kwargs):
        endpoint = urlsplit(request.full_url)
        calls.append((endpoint.port, request.method, endpoint.path))
        answer = (answers.get(endpoint.port, ConnectionRefusedError()) if endpoint.path == "/api/health"
                  else {"api_version": 44, "settings": {}, "online": True})
        if isinstance(answer, Exception):
            raise urllib.error.URLError(answer)
        return JsonReply(answer)

    client = forge.ForgeClient(opener=respond)
    status = client.status()
    assert status["online"] is bool(url)
    if url:
        assert status["url"] == f"http://127.0.0.1:{url}"
        assert (url, "GET", "/api/config") in calls
    else:
        assert error in status["message"]
        with pytest.raises(forge.ForgeError, match=error):
            client._call("POST", "/api/generate", {"synthetic": "private"})
        assert all(path == "/api/health" and method == "GET" for _, method, path in calls)


class JsonReply:
    headers = {}

    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit):
        return json.dumps(self.data).encode()[:limit]


def test_wrong_configured_app_is_reported_and_registered_forge_is_preferred(monkeypatch):
    monkeypatch.delenv("NPCFORGE_URL", raising=False)
    given, registered = "http://127.0.0.1:8765", "http://127.0.0.1:8768"
    fels_suite.write_address("npc-forge", registered, "1.1.0", 44, "fixture")
    client = client_for(given)
    calls = []

    def ask(url):
        calls.append(url)
        return LAB_HEALTH if url == given else NEW_HEALTH

    monkeypatch.setattr(client, "_ask_health", ask)
    result = client._discover()
    assert calls == [given, registered]
    assert result["url"] == registered
    assert result["notes"] == ["That address is Voice Lab, not NPC Forge."]


def test_direct_request_ignores_proxy_and_global_opener(recipients, monkeypatch):
    sink = recipients()
    target = recipients(body=b'{"api_version": 4}')
    for key in ('HTTP_PROXY', 'http_proxy', 'HTTPS_PROXY', 'https_proxy', 'ALL_PROXY', 'all_proxy'):
        monkeypatch.setenv(key, sink.url)
    for key in ('NO_PROXY', 'no_proxy'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(forge.urllib.request, '_opener', SimpleNamespace(
        open=lambda *a, **kw: pytest.fail('Global opener received a Forge request.')))
    assert request_at(target.url + '/forge', 'GET', '/api/config') == {'api_version': 4}
    assert target.received[0][:2] == ('GET', '/forge/api/config')
    assert not sink.received


@pytest.mark.parametrize('status', [301, 302, 303, 307, 308])
@pytest.mark.parametrize('method', ['GET', 'POST'])
def test_redirect_cannot_select_another_recipient(recipients, status, method):
    sink = recipients()
    target = recipients(status=status, location=sink.url + '/other', body=b'private details')
    with pytest.raises(forge.ForgeError, match=f'HTTP {status}') as error:
        request_at(target.url, method, '/api/generate', {'synthetic': 'private'} if method == 'POST' else None)
    assert len(target.received) == 1 and not sink.received
    assert 'private' not in str(error.value) and sink.url not in str(error.value)


@pytest.mark.parametrize('framing', [{}, {'chunked': True}, {'length': False}])
def test_response_size_is_bounded_for_each_framing(recipients, monkeypatch, framing):
    monkeypatch.setattr(forge, 'MAX_RESPONSE_BYTES', 32)
    target = recipients(body=b'x' * 33, **framing)
    with pytest.raises(forge.ForgeError, match='oversized'):
        request_at(target.url, 'GET', '/api/config')


def test_truncated_response_is_rejected(recipients):
    target = recipients(body=b'{}', declared=9)
    with pytest.raises(forge.ForgeError, match='invalid'):
        request_at(target.url, 'GET', '/api/config')


@pytest.mark.parametrize('url', ['file://localhost/x', 'http://user:secret@localhost:8765',
    'http://localhost:0', 'http://localhost:bad', 'http://localhost:70000',
    'http://localhost?token=secret', 'http://localhost#secret', 'http://localhost\\other',
    'http://localhost/\nother', 'http://example.com'])
def test_invalid_or_nonlocal_url_never_opens(url, monkeypatch):
    monkeypatch.setattr(forge.urllib.request, 'build_opener', lambda *a: pytest.fail('Unexpected transport.'))
    with pytest.raises(forge.ForgeError, match='this computer'):
        client_for(url)._call('GET', '/api/config')


def test_error_body_is_not_read_and_is_closed(monkeypatch):
    class Body:
        closed = False

        def read(self, *args):
            pytest.fail('Error body was read.')

        def close(self):
            self.closed = True

    body = Body()

    def refuse(*args, **kwargs):
        raise urllib.error.HTTPError('http://private.invalid/secret', 500, 'private', {}, body)

    with pytest.raises(forge.ForgeError, match='HTTP 500') as error:
        forge.ForgeClient({}, opener=refuse)._request_at(forge.DEFAULT_URL, 'GET', '/api/config')
    assert body.closed and 'private' not in str(error.value)


@pytest.mark.parametrize('job_id', ['../generate', 'x/y', '?x', '', '.', '..', 'x' * 129, None])
def test_cancel_rejects_non_identifiers_before_request(job_id):
    client = forge.ForgeClient({}, opener=lambda *a, **kw: pytest.fail('Unexpected request.'))
    client.cancel(job_id)


def test_cancel_posts_to_exact_job(recipients):
    target = recipients(body=b'{"online": true, "gpu_rule": {}}')
    client_for(target.url).cancel('job-42_a')
    assert target.received[1][:2] == ('POST', '/api/jobs/job-42_a/cancel')
    assert json.loads(target.received[1][2]) == {}
