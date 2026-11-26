"""First-run help for the two local providers at their standard loopback ports."""
import asyncio
import json
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from uuid import uuid4

import httpx
import yaml

from core import direct_model_http, model_hardware
from core.fels_suite import _find_lms
from core.paths import CODE_ROOT
from core.state_gate import STATE

PORTS = {'lmstudio': 1234, 'ollama': 11434}
NAMES = {'lmstudio': 'LM Studio', 'ollama': 'Ollama'}
START_TIMEOUT = 20
MAX_PULL_LINE = 65536
ACTIVE = {'working', 'stopping'}


class SetupModelError(ValueError):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def supported(key, provider):
    port = PORTS.get(key)
    return bool(port and provider and provider.enabled and isinstance(provider.base_url, str) and provider.base_url.rstrip('/') in {
        f'http://127.0.0.1:{port}/v1', f'http://localhost:{port}/v1',
    })


def _url(key, path):
    return f'http://127.0.0.1:{PORTS[key]}{path}'


@cache
def catalog():
    try:
        rows = yaml.safe_load((CODE_ROOT / 'content/model_suggestions.yaml').read_text(encoding='utf-8'))
    except (OSError, ValueError, yaml.YAMLError):
        return ()
    if not isinstance(rows, list):
        return ()
    valid = []
    for row in rows:
        if (isinstance(row, dict) and all(isinstance(row.get(key), str) and row[key] for key in ('name', 'license'))
                and all(type(row.get(key)) in {int, float} and 0 < row[key] < 1024
                        for key in ('ram_gib', 'vram_gib', 'cpu_ram_gib', 'ollama_gb'))
                and re.fullmatch(r'[a-zA-Z0-9._-]+:[a-zA-Z0-9._-]+', str(row.get('ollama')))
                and not row['ollama'].lower().endswith((':cloud', '-cloud'))
                and re.fullmatch(r'https://lmstudio\.ai/models/[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+', str(row.get('lmstudio')))):
            valid.append(row)
    return tuple(valid)


def _suggestion(key):
    ram, vram = model_hardware.memory()
    for row in catalog():
        graphics = ram is not None and vram is not None and ram >= row['ram_gib'] and vram >= row['vram_gib']
        if graphics or (ram is not None and ram >= row['cpu_ram_gib']):
            basis = (f'{ram:.0f} GB RAM and {vram:.0f} GB graphics memory' if graphics else f'{ram:.0f} GB RAM')
            return {
                'name': row['name'], 'license': row['license'], 'basis': basis,
                'model': row['ollama'] if key == 'ollama' else None,
                'size_gb': row['ollama_gb'] if key == 'ollama' else None,
                'link': row['lmstudio'] if key == 'lmstudio' else None,
            }
    return None


def _probe(key, provider):
    path, field_name, id_field = ('/api/tags', 'models', 'name') if key == 'ollama' else ('/v1/models', 'data', 'id')
    url = _url(key, path)
    headers = {'Authorization': f'Bearer {provider.api_key}'} if key == 'lmstudio' and provider.api_key else {}
    try:
        with direct_model_http.create_transport(url) as client:
            response = client.get(url, headers=headers, timeout=1.5)
            response.raise_for_status()
            rows = response.json()[field_name]
            if isinstance(rows, list):
                return 'running', [row[id_field] for row in rows[:500] if isinstance(row, dict)
                                   and isinstance(row.get(id_field), str) and 0 < len(row[id_field]) <= 512]
    except (httpx.ConnectError, httpx.ConnectTimeout):
        return 'offline', []
    except httpx.HTTPStatusError as error:
        if error.response.status_code in (401, 403):
            return 'credentials', []
    except (httpx.HTTPError, ValueError, TypeError, KeyError):
        pass
    return 'unavailable', []


def _installation(key):
    command = _find_lms() if key == 'lmstudio' else shutil.which('ollama')
    if os.name == 'nt':
        appdata = os.environ.get('LOCALAPPDATA')
        if key == 'lmstudio':
            folders, binary = ('LM-Studio', 'Programs/LM Studio', 'Programs/LM-Studio'), 'LM Studio.exe'
        else:
            folders, binary = ('Programs/Ollama',), 'ollama.exe'
        candidates = [Path(appdata) / folder / binary for folder in folders] if appdata else []
        installed_binary = next((path for path in candidates if path.is_file()), None)
        if key == 'ollama' and not command:
            command = installed_binary
        if command and Path(command).suffix.lower() != '.exe':
            command = None
        installed = bool(command or installed_binary or (key == 'ollama' and _ollama_service()))
    else:
        installed = bool(command)
    return installed, str(Path(command).resolve()) if command else None


def _ollama_service():
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Services\Ollama'):
            return True
    except OSError:
        return False


@dataclass
class Job:
    action: str
    model: str | None = None
    id: str = field(default_factory=lambda: uuid4().hex)
    cancel: threading.Event = field(default_factory=threading.Event)
    state: str = 'working'
    message: str = ''
    percent: int | None = None

    def snapshot(self):
        return {'id': self.id, 'action': self.action, 'model': self.model, 'state': self.state,
                'message': self.message, 'percent': self.percent}


_lock = threading.RLock()
_jobs = {}


def inspect(key, provider):
    if not supported(key, provider):
        return {'supported': False}
    state, models = _probe(key, provider)
    command = None
    if state == 'offline':
        installed, command = _installation(key)
        state = 'stopped' if installed else 'missing'
    suggestion = _suggestion(key) if not models else None
    with _lock:
        job = _jobs.get(key)
        return {'supported': True, 'state': state, 'models': models, 'can_start': bool(command),
                'suggestion': suggestion, 'job': job.snapshot() if job else None}


def _launch(job, key, provider, command):
    if key == 'lmstudio':
        args = [command, 'server', 'start', '--port', '1234', '--bind', '127.0.0.1']
        env = None
    else:
        args = [command, 'serve']
        env = {**os.environ, 'OLLAMA_HOST': '127.0.0.1:11434', 'OLLAMA_NO_CLOUD': '1'}
    process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               env=env, start_new_session=os.name != 'nt',
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    deadline = time.monotonic() + START_TIMEOUT
    while time.monotonic() < deadline:
        state, _ = _probe(key, provider)
        if state in {'running', 'credentials'}:
            return f'{NAMES[key]} is running.'
        if process.poll() not in (None, 0):
            break
        time.sleep(0.4)
    raise SetupModelError(f'{NAMES[key]} did not answer. Open it and start its server, then check again.')


async def _pull_updates(job):
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=httpx.Timeout(45, connect=3),
                                 headers={'Accept-Encoding': 'identity'}) as client:
        async with client.stream('POST', _url('ollama', '/api/pull'), json={'model': job.model, 'stream': True}) as response:
            response.raise_for_status()
            if response.headers.get('content-encoding', 'identity').lower() != 'identity':
                raise ValueError('Unexpected encoding')
            pending = b''
            async for chunk in response.aiter_raw():
                lines = (pending + chunk).split(b'\n')
                pending = lines.pop()
                if len(pending) > MAX_PULL_LINE:
                    raise ValueError('Long download response')
                for line in lines:
                    if not line.strip():
                        continue
                    if len(line) > MAX_PULL_LINE:
                        raise ValueError('Long download response')
                    data = json.loads(line)
                    if not isinstance(data, dict) or 'error' in data:
                        raise ValueError('Download refused')
                    if data.get('status') == 'success':
                        return
                    total, completed = data.get('total'), data.get('completed')
                    with _lock:
                        if job.state != 'stopping':
                            if type(total) is int and total > 0 and type(completed) is int:
                                job.percent = min(100, max(0, completed * 100 // total))
                                job.message = 'Ollama is downloading model files.'
                            else:
                                job.percent = None
                                job.message = 'Ollama is preparing or checking model files.'
    raise ValueError('Download ended without success')


async def _wait_for_stop(job):
    while not job.cancel.is_set():
        await asyncio.sleep(0.15)


async def _pull(job):
    download = asyncio.create_task(_pull_updates(job))
    stopping = asyncio.create_task(_wait_for_stop(job))
    try:
        await asyncio.wait((download, stopping), timeout=3600, return_when=asyncio.FIRST_COMPLETED)
        if download.done():
            await download
            return 'The model is downloaded.'
        if stopping.done():
            return None
        raise TimeoutError()
    finally:
        download.cancel()
        stopping.cancel()
        await asyncio.gather(download, stopping, return_exceptions=True)


def _download(job, provider):
    message = asyncio.run(_pull(job))
    if message is None:
        return None
    with _lock:
        job.message, job.percent = 'Ollama is adding the model to its library.', None
    for _ in range(6):
        _, models = _probe('ollama', provider)
        if any(model.casefold() == job.model.casefold() for model in models):
            return message
        if job.cancel.wait(0.4):
            return None
    raise SetupModelError('Ollama downloaded the model files. Check again to see when the model is available.')


def _run(job, key, provider, command):
    state = 'ready'
    try:
        message = _launch(job, key, provider, command) if job.action == 'start' else _download(job, provider)
        if message is None:
            state, message = 'stopped', 'Download stopped. Choose Download again to resume.'
    except SetupModelError as error:
        state, message = 'failed', str(error)
    except Exception:
        state = 'failed'
        if job.action == 'start':
            message = f'{NAMES[key]} did not start. Open it from the Start menu and try again.'
        else:
            message = 'Ollama could not finish the download. Check your internet connection and free disk space, then try again.'
    finally:
        with _lock:
            job.state, job.message, job.percent = state, message, None


def begin(key, provider, action, model=None):
    if not supported(key, provider):
        raise SetupModelError('This action needs an enabled local provider on its usual port.', 400)
    if action not in {'start', 'download'} or (action == 'download' and (
        key != 'ollama' or not any(row['ollama'] == model for row in catalog())
    )):
        raise SetupModelError('Choose a model offered by Setup.', 400)
    state, models = _probe(key, provider)
    command = None
    if action == 'start':
        if state != 'offline':
            raise SetupModelError('Check the provider again or open it directly to see why it is already answering.')
        _, command = _installation(key)
        if not command:
            raise SetupModelError(f'Open {NAMES[key]} yourself, then check again.')
    elif state != 'running':
        raise SetupModelError('Start Ollama, then try downloading the model again.')
    elif model in models:
        raise SetupModelError('This model is already installed. Choose it above to use it.')
    message = (f'Starting {NAMES[key]}… This can take up to {START_TIMEOUT} seconds.'
               if action == 'start' else 'Connecting to Ollama to download the model…')
    with _lock:
        existing = _jobs.get(key)
        if existing and existing.state in ACTIVE:
            raise SetupModelError('An action is already running for this provider.')
        job = Job(action=action, model=model, message=message)
        STATE.start_job(_run, (job, key, provider, command), name=f'altera-setup-{action}')
        _jobs[key] = job
        return job.snapshot()


def job_status(key, job_id, *, stop=False):
    with _lock:
        job = _jobs.get(key)
        if not job or job.id != job_id:
            raise SetupModelError('This action is no longer available. Check the provider again.', 404)
        if stop and job.action == 'download' and job.state == 'working':
            job.state, job.message, job.percent = 'stopping', 'Stopping the download…', None
            job.cancel.set()
        return job.snapshot()
