from hashlib import sha256
from io import BytesIO
import json
from threading import Event
from types import SimpleNamespace

import pytest

from core import updates
from scripts import windows_install


def test_release_check_uses_the_published_version_and_exact_installer(monkeypatch):
    release = {'tag_name': 'v1.0.1', 'assets': [{'name': 'Altera-Setup-1.0.1.exe', 'size': 1000},
                                                {'name': 'SHA256SUMS'}]}
    monkeypatch.setattr(updates, 'VERSION', '1.0.0')
    monkeypatch.setattr(updates, 'read_small', lambda url, limit: json.dumps(release).encode())
    assert updates.check_release()[0] == {'version': '1.0.1', 'size': 1000}
    release['assets'][0]['name'] = 'Other-Setup.exe'
    assert updates.check_release()[0]['size'] is None
    release['tag_name'] = 'v0.9.2'
    assert updates.check_release()[0] is None


@pytest.mark.parametrize('url', [
    'http://github.com/Fel101/Altera', 'https://github.com:8080/Fel101/Altera',
    'https://github.com.example.test/Altera', 'https://example.test@github.com/Altera',
])
def test_download_redirects_stay_on_github_https(url):
    with pytest.raises(updates.UpdateError):
        updates.ReleaseRedirects().redirect_request(None, None, 302, '', None, url)


@pytest.mark.parametrize('outcome', ['ok', 'checksum', 'stop'])
def test_download_only_publishes_a_complete_approved_installer(tmp_path, monkeypatch, outcome):
    tmp_path = tmp_path.resolve()
    payload = b'version one installer'
    digest = sha256(payload).hexdigest()
    manifest = f'{digest}  Altera-Setup-1.0.1.exe\n'.encode()
    monkeypatch.setattr(updates, 'read_small', lambda *args: manifest)
    provided = payload if outcome != 'checksum' else b'?' * len(payload)
    monkeypatch.setattr(updates, 'open_release', lambda url: BytesIO(provided))
    stopped = Event()
    release = {'version': '1.0.1', 'size': len(payload)}

    def callback(total):
        if outcome == 'stop':
            stopped.set()

    if outcome == 'checksum':
        with pytest.raises(updates.UpdateError, match='checksum'):
            updates.download_release(tmp_path, release, callback, stopped)
    else:
        assert updates.download_release(tmp_path, release, callback, stopped) == (outcome == 'ok')
    folder = tmp_path / 'update'
    assert not list(folder.glob('*.partial'))
    if outcome == 'ok':
        assert json.loads((folder / 'pending.json').read_text()) == {'version': '1.0.1', 'sha256': digest}
        assert (folder / 'Altera-Setup-1.0.1.exe').read_bytes() == payload
    else:
        assert not list(folder.iterdir())


def test_failed_setup_keeps_the_active_release_and_undo_keeps_instance_data(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    for version in ['1.0.0', '1.0.1']:
        path = windows_install.installed_python(windows_install.release_path(root, version), windowless=True)
        path.parent.mkdir(parents=True)
        path.touch()
    monkeypatch.setattr(windows_install.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=0))
    windows_install.finish_install(root, '1.0.0', root / 'wheels')
    saved = root / 'instance' / 'saved-chat.txt'
    saved.write_text('Keep this conversation.', encoding='utf-8')
    with monkeypatch.context() as fail:
        fail.setattr(windows_install.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=1))
        with pytest.raises(ValueError, match='required packages'):
            windows_install.finish_install(root, '1.0.1', root / 'wheels')
    assert windows_install.read_installation(root) == {'current': '1.0.0', 'previous': None}
    windows_install.finish_install(root, '1.0.1', root / 'wheels')
    assert windows_install.read_installation(root) == {'current': '1.0.1', 'previous': '1.0.0'}
    with pytest.raises(ValueError, match='newer'):
        windows_install.plan_install(root, '1.0.0')
    windows_install.undo(root)
    assert windows_install.read_installation(root) == {'current': '1.0.0', 'previous': None}
    assert saved.read_text(encoding='utf-8') == 'Keep this conversation.'


def test_source_copy_checks_on_request_and_refuses_install(monkeypatch):
    from core.app import create_app

    checked = Event()

    def check_release():
        checked.set()
        return None, 'No Altera release.'

    monkeypatch.setattr(updates, 'check_release', check_release)
    client = create_app(autostart=False).test_client()
    assert client.get('/api/updates').json['managed'] is False
    assert not checked.is_set()
    assert client.post('/api/updates', json={'action': 'check'},
                       headers={'Origin': 'https://example.test'}).status_code == 403
    assert not checked.is_set()
    assert client.post('/api/updates', json={'action': 'check'}).status_code == 200
    assert checked.wait(3)
    assert client.post('/api/updates', json={'action': 'install'}).status_code == 409
