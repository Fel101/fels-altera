"""Start a managed Windows install and switch between prepared releases."""

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

APP_MUTEX = r'Local\AlteraManagedRuntime'
SETUP_MUTEX = r'Local\AlteraManagedSetup'
UPDATE_EXIT = 40
UNDO_EXIT = 41
VERSION_PATTERN = re.compile(r'(?:0|[1-9][0-9]{0,8})(?:\.(?:0|[1-9][0-9]{0,8})){2}\Z')
STATE_FILE = 'installed.json'
NOTICE_FILE = 'update-notice.txt'
SETUP_PREFIX = 'FelsAltera-Setup-'
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def version_key(version):
    if not isinstance(version, str) or not VERSION_PATTERN.fullmatch(version):
        raise ValueError('This release has an unreadable version.')
    return tuple(int(part) for part in version.split('.'))


def setup_name(version):
    """The file name of a release's Setup, as the release workflow publishes it."""
    return f'{SETUP_PREFIX}{version}.exe'


def atomic_write(path, text):
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=path.name + '-', suffix='.tmp')
    replacement = Path(name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            output.write(text)
        os.replace(replacement, path)
    finally:
        replacement.unlink(missing_ok=True)


def read_installation(root):
    try:
        state = json.loads((root / STATE_FILE).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {'current': None, 'previous': None}
    if not isinstance(state, dict) or set(state) != {'current', 'previous'}:
        raise ValueError('Altera could not read which version is installed.')
    version_key(state['current'])
    if state['previous'] is not None:
        version_key(state['previous'])
        if version_key(state['previous']) >= version_key(state['current']):
            raise ValueError('Altera could not read which version to restore.')
    return state


def release_path(root, version):
    version_key(version)
    path = root / 'releases' / version
    if path.resolve() != path:
        raise ValueError('The Altera release folder was moved. Run Setup again to repair it.')
    return path


def installed_python(release, *, windowless=False):
    return release / '.venv' / 'Scripts' / ('pythonw.exe' if windowless else 'python.exe')


def plan_install(root, version):
    release_path(root, version)
    state = read_installation(root)
    if state['current'] and version_key(version) < version_key(state['current']):
        raise ValueError('A newer Altera is already installed. Use Undo in Altera to return to the previous version.')
    return state


def managed_env(root):
    environment = {key: value for key, value in os.environ.items()
                   if key.upper() not in {'PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP'}}
    environment.update(ALTERA_HOME=str(root / 'instance'), ALTERA_INSTALLED_ROOT=str(root),
                       PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
    return environment


def finish_install(root, version, wheels, *, models=False, voice=False):
    """Set up the release before publishing it as current."""
    state = plan_install(root, version)
    release = release_path(root, version)
    instance = root / 'instance'
    instance.mkdir(parents=True, exist_ok=True)
    if instance.resolve() != instance:
        raise ValueError('Choose a location on this PC for Altera’s data folder.')
    bootstrap = root / 'python' / 'python.exe'
    setup = [str(bootstrap), '-I', '-B', str(release / 'scripts' / 'setup_altera.py'), '--home', str(instance)]
    environment = managed_env(root)
    warnings = []
    with (root / 'setup.log').open('w', encoding='utf-8') as log:
        def run(command, *, timeout, env=environment):
            return subprocess.run(command, cwd=release, env=env, stdout=log, stderr=subprocess.STDOUT,
                                  creationflags=NO_WINDOW, timeout=timeout).returncode

        if run([*setup, '--offline', '--locked', '--wheelhouse', str(wheels)], timeout=1500):
            raise ValueError('Altera’s required packages could not be installed. See setup.log and try Setup again.')
        if models and run([*setup, '--locked', '--models'], timeout=2000):
            warnings.append('The memory and microphone models could not be installed. Run Setup again to retry.')
        if voice:
            voice_env = {**environment, 'ALTERA_VOICE_ENV': str(instance / 'voice' / '.venv'),
                         'ALTERA_VOICE_BOOTSTRAP': str(bootstrap)}
            command = [os.environ.get('ComSpec', r'C:\Windows\System32\cmd.exe'), '/d', '/c',
                       str(release / 'voice' / 'setup.bat')]
            if run(command, timeout=3600, env=voice_env):
                warnings.append('The local Kokoro voice could not be installed. Run Setup again to retry.')
    if not installed_python(release, windowless=True).is_file():
        raise ValueError('Altera’s Python environment is incomplete. Run Setup again.')
    previous = state['current'] if state['current'] != version else state['previous']
    atomic_write(root / STATE_FILE, json.dumps({'current': version, 'previous': previous}))
    if warnings or previous:
        message = ' '.join(warnings) if warnings else f'Altera was updated to {version}.'
        atomic_write(root / NOTICE_FILE, message)
    return warnings


def undo(root):
    state = read_installation(root)
    previous = state['previous']
    if not previous or not installed_python(release_path(root, previous), windowless=True).is_file():
        raise ValueError('The previous version is unavailable. Run Setup to repair Altera.')
    atomic_write(root / STATE_FILE, json.dumps({'current': previous, 'previous': None}))
    return f'Altera returned to version {previous}.'


def file_digest(path):
    with path.open('rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()


def apply_download(root):
    """The application has exited. Launch the exact installer it downloaded and verified."""
    pending = root / 'update' / 'pending.json'
    try:
        contents = json.loads(pending.read_text(encoding='utf-8'))
        version_key(contents['version'])
        installer = pending.with_name(setup_name(contents['version']))
        if installer.resolve() != installer or file_digest(installer) != contents['sha256']:
            raise ValueError('The downloaded Setup file changed. Check for updates and download it again.')
        done = subprocess.run([str(installer), f'/DIR={root}', '/ALTERAUPDATE=1', '/NORESTART', '/TASKS='],
                              cwd=root, check=False)
        if done.returncode or read_installation(root)['current'] != contents['version']:
            return 'Setup did not finish. Altera reopened the installed version.'
        return None
    finally:
        pending.unlink(missing_ok=True)


def mutex_api():
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.OpenMutexW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
    kernel.OpenMutexW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    return kernel


def setup_running(kernel):
    handle = kernel.OpenMutexW(0x00100000, False, SETUP_MUTEX)
    if handle:
        kernel.CloseHandle(handle)
    return bool(handle)


@contextmanager
def runtime_mutex():
    import ctypes

    kernel = mutex_api()
    handle = kernel.CreateMutexW(None, False, APP_MUTEX)
    if not handle:
        raise OSError('Altera could not reserve its Windows session.')
    already_exists = ctypes.get_last_error() == 183
    try:
        if already_exists or setup_running(kernel):
            raise ValueError('Altera or Setup is already running. Return to its window or browser tab.')
        yield
    finally:
        kernel.CloseHandle(handle)


def launch(root):
    while True:
        with runtime_mutex():
            version = read_installation(root)['current']
            if not version:
                raise ValueError('Altera’s installation is incomplete. Run Setup again.')
            release = release_path(root, version)
            environment = managed_env(root)
            environment['ALTERA_MANAGED_LAUNCH'] = '1'
            with (root / 'launcher.log').open('a', encoding='utf-8') as log:
                done = subprocess.run([str(installed_python(release, windowless=True)), '-B', 'altera.py', '--open'],
                                      cwd=release, env=environment, stdout=log, stderr=subprocess.STDOUT, check=False)
            if done.returncode == UNDO_EXIT:
                try:
                    notice = undo(root)
                except (OSError, ValueError) as error:
                    notice = str(error)
                atomic_write(root / NOTICE_FILE, notice)
                continue
        if done.returncode != UPDATE_EXIT:
            if done.returncode:
                raise ValueError('Altera could not start. Open launcher.log in its installation folder for details.')
            return
        try:
            notice = apply_download(root)
        except (OSError, ValueError, KeyError, TypeError):
            notice = 'The downloaded update could not be opened. Check for updates and try again.'
        if notice:
            atomic_write(root / NOTICE_FILE, notice)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--plan')
    mode.add_argument('--finish')
    parser.add_argument('--wheels', type=Path)
    parser.add_argument('--models', action='store_true')
    parser.add_argument('--voice', action='store_true')
    parser.add_argument('--from-setup', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    try:
        if args.plan:
            plan_install(root, args.plan)
        elif args.finish:
            if not args.wheels:
                parser.error('--finish requires a local wheelhouse')
            finish_install(root, args.finish, args.wheels, models=args.models, voice=args.voice)
        else:
            if args.from_setup:
                kernel = mutex_api()
                deadline = time.monotonic() + 30
                while setup_running(kernel) and time.monotonic() < deadline:
                    time.sleep(.1)
            launch(root)
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        if args.plan or args.finish:
            atomic_write(root / 'setup-error.txt', str(error).encode('ascii', 'replace').decode('ascii'))
        else:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, str(error), 'Altera', 0x10)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
