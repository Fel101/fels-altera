"""Explicit Windows updates from one GitHub release, through the bundled installer."""

from hashlib import sha256
from http.client import HTTPException
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from core import env
from core.paths import CODE_ROOT
from core.routes.agent_tools import _PublicHTTPSHandler
from core.version import UPDATE_REPOSITORY, VERSION
from scripts.windows_install import NOTICE_FILE, atomic_write, read_installation, release_path, setup_name, version_key

RELEASE_PAGE = f'https://github.com/{UPDATE_REPOSITORY}/releases'
API_URL = f'https://api.github.com/repos/{UPDATE_REPOSITORY}/releases/latest'
ALLOWED_HOSTS = {'api.github.com', 'github.com', 'release-assets.githubusercontent.com',
                 'objects.githubusercontent.com', 'github-releases.githubusercontent.com'}
DOWNLOAD_LIMIT = 512 * 1024 * 1024


class UpdateError(ValueError):
    pass


def check_url(url):
    parts = urlsplit(url)
    if (parts.scheme != 'https' or parts.hostname not in ALLOWED_HOSTS or parts.port not in (443, None)
            or parts.username is not None or parts.password is not None or parts.fragment):
        raise UpdateError('Altera downloads releases from GitHub over HTTPS.')


class ReleaseRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_release(url):
    check_url(url)
    # The shared HTTPS handler resolves once and connects only to validated public IPs.
    opener = build_opener(ProxyHandler({}), _PublicHTTPSHandler(), ReleaseRedirects())
    headers = {'User-Agent': f'Altera/{VERSION}'}
    if url == API_URL:
        headers['Accept'] = 'application/vnd.github+json'
    return opener.open(Request(url, headers=headers), timeout=12)


def read_small(url, limit):
    with open_release(url) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise UpdateError('GitHub returned more update information than expected. Try again later.')
    return data


def check_release():
    try:
        record = json.loads(read_small(API_URL, 1024 * 1024))
    except HTTPError as error:
        if error.code == 404:
            return None, f'No Altera release has been published yet. You have version {VERSION}.'
        raise
    if not isinstance(record, dict) or record.get('draft') or record.get('prerelease'):
        raise UpdateError('GitHub did not return a published Altera release. Try again later.')
    tag = record.get('tag_name')
    if not isinstance(tag, str) or not tag.startswith('v'):
        raise UpdateError('Altera could not read the release version. Open the releases page for details.')
    version = tag[1:]
    try:
        latest = version_key(version)
    except ValueError:
        raise UpdateError('Altera could not read the release version. Open the releases page for details.') from None
    if latest <= version_key(VERSION):
        return None, f'You already have version {VERSION}. There is no newer release.'
    name = setup_name(version)
    assets = record.get('assets')
    found = {asset.get('name'): asset.get('size') for asset in assets if isinstance(asset, dict)
             and isinstance(asset.get('name'), str)} if isinstance(assets, list) else {}
    size = found.get(name)
    if type(size) is not int or not 0 < size <= DOWNLOAD_LIMIT or 'SHA256SUMS' not in found:
        size = None
    return {'version': version, 'size': size}, f'Altera {version} is available.'


def download_release(root, release, progress, stopped):
    version, size = release['version'], release['size']
    name = setup_name(version)
    base = f'{RELEASE_PAGE}/download/v{version}/'
    manifest = read_small(base + 'SHA256SUMS', 8192).decode('ascii')
    hashes = re.findall(rf'^([a-fA-F0-9]{{64}}) [ *]{re.escape(name)}\r?$', manifest, re.M)
    if len(hashes) != 1:
        raise UpdateError('The release has no readable checksum for Setup. Try again later.')
    folder = root / 'update'
    folder.mkdir(exist_ok=True)
    if folder.resolve() != folder:
        raise UpdateError('Altera’s update folder was moved. Run Setup again to repair it.')
    digest = sha256()
    total = 0
    deadline = time.monotonic() + 600
    with tempfile.NamedTemporaryFile(dir=folder, prefix='setup-', suffix='.partial', delete=False) as output:
        partial = Path(output.name)
        try:
            with open_release(base + name) as response:
                while not stopped.is_set():
                    if time.monotonic() > deadline:
                        raise UpdateError('The download took too long. Check your connection and try again.')
                    chunk = response.read1(256 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > size:
                        raise UpdateError('The Setup download was larger than expected. Check for updates and try again.')
                    digest.update(chunk)
                    output.write(chunk)
                    progress(total)
            if stopped.is_set():
                return False
            if total != size or digest.hexdigest() != hashes[0].lower():
                raise UpdateError('The Setup download did not match its checksum. Try the download again.')
            output.close()
            os.replace(partial, folder / name)
            atomic_write(folder / 'pending.json', json.dumps({'version': version, 'sha256': digest.hexdigest()}))
            return True
        finally:
            output.close()
            partial.unlink(missing_ok=True)


def managed_root():
    install_root = env.get('INSTALLED_ROOT')
    if os.name == 'nt' and install_root and env.get('MANAGED_LAUNCH') == '1':
        try:
            root = Path(install_root).resolve()
            if release_path(root, VERSION) == CODE_ROOT and read_installation(root)['current'] == VERSION:
                return root
        except (OSError, ValueError):
            pass
    return None


class UpdateService:
    def __init__(self, root):
        self.root = root
        self.lock = threading.Lock()
        self.release = None
        self.phase = 'idle'
        self.message = ''
        self.received = 0
        self.stopped = threading.Event()

    def status(self):
        with self.lock:
            installed = read_installation(self.root) if self.root else {}
            notice = ''
            if self.root and (self.root / NOTICE_FILE).is_file():
                notice = (self.root / NOTICE_FILE).read_text(encoding='utf-8')[:1024]
            return {'current': VERSION, 'release': self.release, 'page': RELEASE_PAGE, 'phase': self.phase,
                    'message': self.message, 'notice': notice, 'received': self.received, 'managed': self.root is not None,
                    'previous': installed.get('previous')}

    def start(self, action):
        with self.lock:
            if self.phase in {'checking', 'downloading', 'restarting'}:
                raise UpdateError('An update action is in progress. Wait for it to finish or stop the download.')
            self.received = 0
            self.stopped.clear()
            if action == 'check':
                self.release = None
                self.phase, self.message = 'checking', 'Checking GitHub for a newer version…'
                work = self._check
            else:
                if not self.root or not self.release or not self.release['size']:
                    raise UpdateError('Check for an available Windows update before downloading.')
                self.phase, self.message = 'downloading', 'Downloading Setup…'
                work = self._download
            threading.Thread(target=self._run, args=(work,), name='altera-update', daemon=True).start()

    def _run(self, work):
        try:
            work()
        except UpdateError as error:
            self._finish('idle', str(error))
        except (OSError, URLError, ValueError, HTTPException):
            self._finish('idle', 'Altera could not read the update. Check your connection and try again.')

    def _finish(self, phase, message):
        with self.lock:
            self.phase, self.message = phase, message

    def _check(self):
        release, message = check_release()
        with self.lock:
            self.release = release
            self.phase, self.message = 'idle', message

    def _download(self):
        def progress(total):
            with self.lock:
                self.received = total

        complete = download_release(self.root, self.release, progress, self.stopped)
        if complete:
            self._finish('downloaded', 'Setup is ready. Altera will reopen after you finish Setup.')
        else:
            self._finish('idle', 'Download stopped. You can try it again when you’re ready.')

    def stop(self):
        with self.lock:
            if self.phase == 'downloading':
                self.stopped.set()
                self.message = 'Stopping the download…'

    def authorize_restart(self, mode):
        with self.lock:
            if not self.root:
                raise UpdateError('Open the Windows installation from the Start menu to use this action.')
            if mode == 'install' and self.phase != 'downloaded':
                raise UpdateError('Download the update before opening Setup.')
            if mode == 'undo' and (self.phase in {'checking', 'downloading', 'restarting'}
                                   or not read_installation(self.root)['previous']):
                raise UpdateError('There is no previous version ready to restore. Try again after the current action.')
            self.phase = 'restarting'
            self.message = 'Altera is closing. It will reopen when it is ready.'
