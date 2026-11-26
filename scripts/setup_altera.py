"""Explicit, standard-library setup. Importing this file does no work."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

# 3.12 is the one to recommend: the local voice (Kokoro) runs on 3.10 to 3.12, so it covers both.
SUPPORTED_PYTHONS = ((3, 11), (3, 12), (3, 13))
SUPPORTED_TEXT = 'Python 3.11, 3.12 or 3.13'
SUPPORTED_RECOMMENDATION = '3.12 if you want the local voice too'
OFFICIAL_INDEX = 'https://pypi.org/simple'
CODE_ROOT = Path(__file__).resolve().parents[1]


VERSION_SCRIPT = "import json,sys; print(json.dumps({'version':list(sys.version_info[:3])}))"
ENVIRONMENT_SCRIPT = r'''
import importlib.metadata as metadata
import json, re, sys
payload = json.load(sys.stdin)
result = {'version': list(sys.version_info[:3]), 'prefix': sys.prefix,
          'virtual': sys.prefix != sys.base_prefix, 'pip': False,
          'installed': {}, 'missing': [], 'incompatible': [], 'direct_references': []}
try:
    from pip._vendor.packaging.requirements import Requirement
    from pip._vendor.packaging.version import Version
except ImportError:
    print(json.dumps(result)); raise SystemExit(0)
result['pip'] = True
for distribution in metadata.distributions():
    name, version = distribution.metadata.get('Name', ''), distribution.version
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', name):
        continue
    Version(version)
    result['installed'][re.sub(r'[-_.]+', '-', name).lower()] = version
    for dependency in distribution.requires or []:
        parsed = Requirement(dependency)
        if parsed.url and (not parsed.marker or parsed.marker.evaluate()):
            result['direct_references'].append(name)
for line in payload['requirements']:
    requirement = Requirement(line)
    if requirement.url:
        raise ValueError('Only registry requirements are supported')
    if requirement.marker and not requirement.marker.evaluate():
        continue
    key = re.sub(r'[-_.]+', '-', requirement.name).lower()
    version = result['installed'].get(key)
    if version is None:
        result['missing'].append(requirement.name)
    elif not requirement.specifier.contains(version, prereleases=True):
        result['incompatible'].append(requirement.name)
print(json.dumps(result))
'''

CONFIGURATION_SCRIPT = r'''
import ipaddress, json, os, pathlib, sys, urllib.error, urllib.parse, urllib.request
payload = json.load(sys.stdin)
home, source = pathlib.Path(payload['home']), pathlib.Path(payload['code_root'])
try:
    import yaml
    for name in ('config.yaml', 'config.local.yaml', 'personality.yaml'):
        path = home / name
        if not path.exists():
            continue
        if path.stat().st_size > 1024 * 1024:
            raise ValueError('Oversized configuration')
        value = yaml.safe_load(path.read_text(encoding='utf-8'))
        if (value is None and name == 'config.local.yaml'):
            continue
        if not isinstance(value, dict):
            raise ValueError('Expected mapping')
except Exception:
    print(json.dumps({'configuration': 'invalid'})); raise SystemExit(0)
result = {'configuration': 'ok', 'metadata': 'not_requested'}
if not payload['probe']:
    print(json.dumps(result)); raise SystemExit(0)
if not (home / 'config.yaml').is_file():
    result['metadata'] = 'configuration_missing'
    print(json.dumps(result)); raise SystemExit(0)
try:
    # These modules only read configuration; app/runtime/LLM and data stores are
    # deliberately never imported. Load dotenv as ordinary startup does.
    sys.path.insert(0, str(source))
    from dotenv import load_dotenv
    load_dotenv(home / '.env')
    from core.config import get_config
    from core.providers import ProviderRegistry
    config = get_config()
    llm = config.get('llm') or {}
    if 'provider' in llm or 'providers' in llm:
        registry = ProviderRegistry(config)
        provider = registry.get_active_provider()
        if not provider or not provider.enabled:
            result['metadata'] = 'provider_disabled'
            print(json.dumps(result)); raise SystemExit(0)
        base, key, model = provider.base_url, provider.api_key, registry.get_active_model()
        endpoint = provider.models_endpoint
    else:
        base, key, model = llm.get('base_url', 'http://localhost:1234/v1'), llm.get('api_key', ''), llm.get('model', '')
        endpoint = '/models'
    parsed = urllib.parse.urlsplit(base)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Invalid endpoint')
    if (not isinstance(endpoint, str) or not endpoint.startswith('/') or endpoint.startswith('//') or len(endpoint) > 2000
            or urllib.parse.urlsplit(endpoint).path != endpoint or '\\' in endpoint):
        raise ValueError('Invalid models endpoint')
    try:
        local = ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        local = parsed.hostname.casefold() == 'localhost'
    if not local and not payload['allow_network_provider']:
        result['metadata'] = 'network_approval_required'
        print(json.dumps(result)); raise SystemExit(0)
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    headers = {'Accept': 'application/json'}
    if key:
        headers['Authorization'] = 'Bearer ' + key
    request = urllib.request.Request(base.rstrip('/') + endpoint, headers=headers, method='GET')
    with opener.open(request, timeout=2) as response:
        raw = response.read(65537)
    if len(raw) > 65536:
        raise ValueError('Oversized metadata')
    value = json.loads(raw)
    models = value.get('data') if isinstance(value, dict) else None
    if not isinstance(models, list) or len(models) > 1000:
        raise ValueError('Invalid metadata')
    names = [item.get('id') for item in models if isinstance(item, dict) and isinstance(item.get('id'), str)]
    result.update(metadata='listed' if model and model in names else 'model_not_listed', model_listed=bool(model and model in names))
except urllib.error.HTTPError as error:
    result['metadata'] = 'credentials_refused' if error.code in (401, 403) else 'unreachable'
except Exception:
    result['metadata'] = 'unreachable'
print(json.dumps(result))
'''
# The two models Altera reads but never downloads itself: MiniLM for memories (Chroma's own archive, checked against
# Chroma's SHA-256) and the configured Whisper size for the microphone (Hugging Face). It only downloads when asked to
# (payload 'download'), and only into the places Altera reads them from. Library progress goes to stderr, so stdout
# holds the JSON answer alone.
MODELS_SCRIPT = r'''
import json, pathlib, sys
answer, sys.stdout = sys.stdout, sys.stderr
payload = json.load(sys.stdin)
sys.path.insert(0, payload['code_root'])
result = {'memory': 'failed', 'speech': 'failed', 'speech_model': 'base'}
try:
    from core.config import get_config
    config = get_config()
except Exception:
    print(json.dumps(result), file=answer); raise SystemExit(0)

def memory_ready():
    from core.local_embeddings import _metadata, _read_assets, _signature, _source_path
    try:
        folder = _source_path()
        _metadata(_read_assets(folder, _signature(folder)))
        return True
    except Exception:
        return False

try:
    configured = (config.get('memory') or {}).get('embedding_model_path')
    if memory_ready():
        result['memory'] = 'ready'
    elif configured:
        result['memory'] = 'configured_missing'
    elif payload['download']:
        from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2
        ONNXMiniLM_L6_V2()._download_model_if_not_exists()
        result['memory'] = 'ready' if memory_ready() else 'failed'
    else:
        result['memory'] = 'missing'
except Exception:
    result['memory'] = 'failed'

try:
    from core.stt import model_status
    size = (config.get('voice') or {}).get('stt_model') or 'base'
    result['speech_model'] = size if isinstance(size, str) and len(size) <= 100 else 'custom'
    if model_status(size) in ('cached', 'loaded'):
        result['speech'] = 'ready'
    elif pathlib.Path(size).is_absolute() or '/' in size or '\\' in size:
        result['speech'] = 'custom_missing'
    elif payload['download']:
        from faster_whisper.utils import download_model
        download_model(size)
        result['speech'] = 'ready' if model_status(size) == 'cached' else 'failed'
    else:
        result['speech'] = 'missing'
except Exception:
    result['speech'] = 'failed'
print(json.dumps(result), file=answer)
'''
MODELS_SIZE = 'about 230 MB'


class SetupError(RuntimeError):
    pass


def safe_environment(home):
    """Ignore inherited pip indexes/config and Python path injection."""
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith('PIP_') and key.upper() not in {'PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP'}}
    env.update(PIP_CONFIG_FILE=os.devnull, PIP_DISABLE_PIP_VERSION_CHECK='1',
               PYTHONDONTWRITEBYTECODE='1', ALTERA_HOME=str(home), ALTERA_DISABLE_AUTOSTART='1',
               HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
    return env


def next_step(root, home, windows=os.name == 'nt'):
    """What to run after setup: start.bat on Windows, the environment's Python elsewhere (there is no start.sh)."""
    start = r'.\start.bat' if windows else '.venv/bin/python altera.py --open'
    text = f'Next, run {start} in {root}.'
    if Path(home) != Path(root):
        text += f' Set ALTERA_HOME to {home} first, so Altera uses this instance.'
    return text


def requirements(root, locked=False):
    path = root / ('requirements.lock.txt' if locked else 'requirements.txt')
    try:
        if path.stat().st_size > 1024 * 1024:
            raise SetupError('The requirements file is too large to inspect.')
        lines = [line.split('#', 1)[0].strip() for line in path.read_text(encoding='utf-8').splitlines()]
    except OSError:
        raise SetupError(f'Missing readable {path.name}. Restore the project source before running setup.') from None
    lines = [line for line in lines if line]
    if not lines or len(lines) > 2000 or any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.<>=!~ ,;\'"()\[\]-]*', line) for line in lines):
        raise SetupError('Requirements must contain package specifications only; URLs, includes and pip options are refused.')
    return path, lines


def validate_wheelhouse(folder):
    """Refuse wheel dependencies that could bypass --no-index via a URL."""
    for wheel in folder.glob('*.whl'):
        try:
            with zipfile.ZipFile(wheel) as archive:
                if len(archive.infolist()) > 100000:
                    raise ValueError('Oversized wheel directory')
                # Only the wheel's own metadata can introduce pip dependencies.
                metadata = [item for item in archive.infolist()
                            if item.filename.count('/') == 1 and item.filename.endswith('.dist-info/METADATA')]
                if len(metadata) != 1 or metadata[0].file_size > 1024 * 1024:
                    raise ValueError('Missing or oversized wheel metadata')
                from email.parser import BytesParser
                fields = BytesParser().parsebytes(archive.read(metadata[0]), headersonly=True)
                if any('@' in entry or '://' in entry for entry in fields.get_all('Requires-Dist', [])):
                    raise SetupError('A local wheel declares a direct URL dependency. Offline setup refuses it; prepare registry-only dependency wheels.')
        except SetupError:
            raise
        except (OSError, ValueError, RuntimeError, NotImplementedError, zipfile.BadZipFile):
            raise SetupError('A local wheel has unreadable or oversized metadata. Prepare valid wheels for this Python version before installing.') from None


class Setup:
    def __init__(self, root=CODE_ROOT, *, home=None, runner=None, output=None, bootstrap_python=None):
        self.root = Path(root).expanduser().resolve()
        self.requested_home = Path(home or os.environ.get('ALTERA_HOME') or self.root).expanduser().absolute()
        self.home = self.requested_home.resolve()
        self.venv = self.root / '.venv'
        self.python = self.venv / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        selected_python = str(bootstrap_python or sys.executable)
        self.bootstrap_python = str(Path(selected_python).expanduser().resolve()) if ('/' in selected_python or '\\' in selected_python) else selected_python
        self.runner, self.output = runner or subprocess.run, output or (lambda message: print(message, flush=True))
        self.env = safe_environment(self.home)

    def run(self, command, *, data=None, timeout=30, env=None):
        try:
            return self.runner([str(item) for item in command], input=json.dumps(data) if data is not None else None,
                               cwd=str(self.root), env=env or self.env, capture_output=True, text=True,
                               timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired):
            raise SetupError('The prerequisite command could not finish. Check the Python installation and try again.') from None

    def json_command(self, python, script, data=None, *, timeout=30, env=None):
        completed = self.run([python, '-I', '-B', '-c', script], data=data, timeout=timeout, env=env)
        try:
            value = json.loads(completed.stdout) if completed.returncode == 0 else None
            if isinstance(value, dict):
                return value
        except (TypeError, ValueError):
            pass
        raise SetupError(f'Python could not inspect this environment. Check that it is {SUPPORTED_TEXT} with its bundled '
                         'pip and venv.')

    def inspect(self, lines):
        info = self.json_command(self.python, ENVIRONMENT_SCRIPT, {'requirements': lines})
        if tuple(info.get('version', [])[:2]) not in SUPPORTED_PYTHONS:
            raise SetupError(f'This environment is not {SUPPORTED_TEXT}. Existing files were left intact.')
        if not info.get('virtual') or Path(info.get('prefix', '')).resolve() != self.venv.resolve():
            raise SetupError('The .venv interpreter does not belong to this virtual environment. It was left intact.')
        if not info.get('pip'):
            raise SetupError('The existing environment has no usable pip. Run its Python with -m ensurepip, then rerun setup.')
        return info

    def pip_check(self):
        return self.run([self.python, '-I', '-B', '-m', 'pip', '--isolated', 'check']).returncode == 0

    def install(self, path, info, args):
        if args.offline and args.wheelhouse is None:
            raise SetupError('Offline installation needs --wheelhouse pointing to predownloaded local wheels; nothing was downloaded.')
        if info.get('direct_references'):
            raise SetupError('Installed dependency metadata contains direct URLs. Inspect that environment separately; setup installs registry-only requirements.')
        if args.offline:
            validate_wheelhouse(args.wheelhouse)
        command = [self.python, '-I', '-B', '-m', 'pip', '--isolated', 'install', '--disable-pip-version-check',
                   '--no-input', '--only-binary=:all:']
        if args.offline:
            command += ['--no-index', '--find-links', str(args.wheelhouse)]
        else:
            command += ['--index-url', OFFICIAL_INDEX]
        # Freeze every existing distribution unless replacement is explicitly
        # requested. Missing transitives cannot silently upgrade an installed one.
        with tempfile.TemporaryDirectory(prefix='altera-setup-') as folder:
            if not args.allow_package_changes:
                constraints = Path(folder) / 'installed.txt'
                entries = []
                for name, version in sorted(info['installed'].items()):
                    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', name) or not re.fullmatch(r'[A-Za-z0-9_.+!-]+', version):
                        raise SetupError('An installed package has unreadable metadata; inspect that environment before installing.')
                    entries.append(f'{name}=={version}')
                constraints.write_text('\n'.join(entries) + '\n', encoding='utf-8')
                command += ['--constraint', str(constraints)]
            command += ['-r', str(path)]
            self.output('Installing declared Python dependencies ' + ('from local wheels.' if args.offline else 'from official PyPI.'))
            if self.run(command, timeout=1200).returncode != 0:
                raise SetupError('Dependency installation failed. Check connectivity/local wheels and wheel availability for this Python version. '
                                 'Existing versions are constrained; use --allow-package-changes only if you intend to replace conflicting packages. '
                                 'No configuration or saved data was changed.')

    def starter_files(self, *, readonly=False):
        copies = [('config.example.yaml', 'config.yaml')]
        if self.home != self.root:
            copies.append(('personality.yaml', 'personality.yaml'))
        for source_name, target_name in copies:
            source, target = self.root / source_name, self.home / target_name
            if target.exists() or target.is_symlink():
                self.output(f'Preserved existing {target_name}.')
                continue
            if not source.is_file():
                raise SetupError(f'Missing portable starter {source_name}; restore the source checkout.')
            if readonly:
                self.output(f'Would create missing {target_name} in the instance directory.')
                continue
            if not target.parent.resolve().is_relative_to(self.home):
                raise SetupError('A starter file parent points outside the selected instance; no file was copied.')
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with target.open('xb') as destination, source.open('rb') as origin:
                    shutil.copyfileobj(origin, destination)
            except FileExistsError:
                self.output(f'Preserved concurrently created {target_name}.')
            else:
                self.output(f'Created missing {target_name}.')

    def configuration(self, *, probe=False, allow_network=False):
        result = self.json_command(self.python, CONFIGURATION_SCRIPT,
            {'code_root': str(self.root), 'home': str(self.home), 'probe': probe,
             'allow_network_provider': allow_network}, timeout=6 if probe else 30)
        if result.get('configuration') != 'ok':
            raise SetupError('An existing configuration/personality file is not a readable YAML mapping. Correct it manually; setup kept its contents.')
        if probe:
            status = result.get('metadata')
            if status == 'listed':
                self.output('The selected model is listed by the provider. Inference and speech were not tested.')
            else:
                messages = {'configuration_missing': 'Run setup before probing the selected provider.',
                            'provider_disabled': 'No enabled model provider is selected. Choose one in Settings.',
                            'network_approval_required': 'The selected provider is not loopback. Use --allow-network-provider only to approve this metadata request.',
                            'model_not_listed': 'The provider answered, but the configured model is not listed. Select its served model in Settings.',
                            'credentials_refused': 'The provider refused its configured credentials. Correct them in Settings.',
                            'unreachable': 'Provider metadata is unavailable. Start your existing model server separately and verify its endpoint.'}
                raise SetupError(messages.get(status, 'The provider metadata response was unavailable.'))

    def models(self, *, download=False):
        """Report the memory and speech-recognition models, downloading the missing ones only when asked."""
        env = None
        if download:
            # The one step that goes online on purpose: Hugging Face must not be told to stay offline.
            env = {key: value for key, value in self.env.items() if key not in {'HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE'}}
            self.output(f'Downloading the missing memory and speech-recognition models ({MODELS_SIZE}). This can take a few minutes.')
        try:
            result = self.json_command(self.python, MODELS_SCRIPT, {'code_root': str(self.root), 'download': download},
                                       timeout=1800 if download else 60, env=env)
        except SetupError:
            result = {}
        memory, speech, size = result.get('memory'), result.get('speech'), result.get('speech_model') or 'base'
        self.output({'ready': 'Memory model (MiniLM): ready.',
                     'missing': 'Memory model (MiniLM): not downloaded. Companions cannot keep memories without it.',
                     'configured_missing': 'Memory model (MiniLM): not found in memory.embedding_model_path. '
                                           'Setup does not download into a folder you chose; fix the path or remove it.'}
                    .get(memory, 'Memory model (MiniLM): could not be ' + ('downloaded.' if download else 'checked.')))
        self.output({'ready': f'Speech-recognition model (Whisper {size}): ready.',
                     'missing': f'Speech-recognition model (Whisper {size}): not downloaded. The microphone needs it.',
                     'custom_missing': 'Speech-recognition model: voice.stt_model names a folder that does not hold one.'}
                    .get(speech, f'Speech-recognition model (Whisper {size}): could not be ' + ('downloaded.' if download else 'checked.')))
        if download and (memory != 'ready' or speech != 'ready'):
            raise SetupError('A model could not be downloaded. Check the connection and run setup with --models again; '
                             'everything else is set up, and text chat works without these models.')
        if not download and 'missing' in (memory, speech):
            self.output(f'Run setup again with --models to download them once ({MODELS_SIZE}). Text chat works without them.')

    def execute(self, args):
        path, lines = requirements(self.root, args.locked)
        if args.wheelhouse is not None:
            args.wheelhouse = Path(args.wheelhouse).expanduser().resolve()
            if not args.offline or not args.wheelhouse.is_dir():
                raise SetupError('--wheelhouse requires --offline and an existing local directory.')
        if args.offline and args.probe_models:
            raise SetupError('--offline forbids model metadata requests; omit --probe-models.')
        if args.allow_network_provider and not args.probe_models:
            raise SetupError('--allow-network-provider requires an explicit --probe-models request.')
        if args.models and (args.offline or args.check):
            raise SetupError('--models downloads, so it cannot be combined with --offline or --check.')
        self.output(f'Source: {self.root}\nInstance: {self.home}\nRequirements: {path.name}')
        if self.venv.is_symlink():
            raise SetupError('The .venv directory is a link. Use a dedicated environment before setup; linked environments were left intact.')
        if not self.python.is_file():
            if self.venv.exists():
                raise SetupError('An incomplete .venv already exists. Inspect or repair it first; setup will not replace it.')
            info = self.json_command(self.bootstrap_python, VERSION_SCRIPT)
            if tuple(info.get('version', [])[:2]) not in SUPPORTED_PYTHONS:
                raise SetupError(f'{SUPPORTED_TEXT} is required ({SUPPORTED_RECOMMENDATION}). Install it from python.org, '
                                 'then run setup again; setup never downloads Python.')
            version = '.'.join(map(str, info['version'][:3]))
            if args.check:
                raise SetupError('No .venv exists. Run setup without --check to create the environment and install dependencies.')
            if args.offline and args.wheelhouse is None:
                raise SetupError('Offline setup needs --wheelhouse with all required Python wheels before creating .venv.')
            if args.dry_run:
                self.output(f'Would create .venv with Python {version} and install the selected requirements ' + ('from local wheels.' if args.offline else 'from official PyPI.'))
                self.starter_files(readonly=True)
                if args.models:
                    self.output(f'Would download the memory and speech-recognition models ({MODELS_SIZE}).')
                self.output('Dry run complete. No files, packages, services or models were changed.')
                return 0
            self.output(f'Creating the missing .venv with Python {version}.')
            if self.run([self.bootstrap_python, '-I', '-B', '-m', 'venv', str(self.venv)], timeout=120).returncode != 0:
                raise SetupError('Python could not create .venv. Install its venv/ensurepip support separately; setup did not replace an existing environment.')
        info = self.inspect(lines)
        compatible = not info['incompatible']
        healthy = self.pip_check()
        needs_install = bool(info['missing'] or not compatible or not healthy)
        if info['missing']:
            self.output('Missing declared packages: ' + ', '.join(info['missing']))
        if not compatible:
            self.output('Incompatible declared packages: ' + ', '.join(info['incompatible']))
        if not healthy:
            self.output('pip check found missing or incompatible dependency relationships.')
        if needs_install and args.check:
            raise SetupError('Dependency checks failed. Run setup to add missing packages, or explicitly allow package changes when conflicts require replacement.')
        if (not compatible or not healthy) and not args.allow_package_changes:
            raise SetupError('Existing packages need attention. Rerun with --allow-package-changes only if you intend to permit replacements; no packages were changed.')
        if needs_install and args.offline and args.wheelhouse is None:
            raise SetupError('Offline installation needs --wheelhouse pointing to predownloaded local wheels; nothing was downloaded.')
        if args.dry_run:
            if needs_install:
                self.output('Would install selected requirements and permit existing package replacements.' if args.allow_package_changes else
                            'Would install missing dependencies with existing versions constrained.')
            else:
                self.output('Dependencies already satisfy the selected requirements; no install is needed.')
            self.starter_files(readonly=True)
            if args.models:
                self.output(f'Would download any missing memory and speech-recognition models ({MODELS_SIZE}).')
            self.output('Dry run complete. No files, packages, services or models were changed.')
            return 0
        if needs_install:
            self.install(path, info, args)
            info = self.inspect(lines)
            if info['missing'] or info['incompatible'] or not self.pip_check():
                raise SetupError('Dependencies remain unhealthy after installation. Review this environment before starting Altera; configuration and saved data were preserved.')
        else:
            self.output('Dependencies already satisfy the selected requirements; existing packages were kept.')
        self.starter_files(readonly=args.check)
        self.configuration(probe=args.probe_models, allow_network=args.allow_network_provider)
        if not args.probe_models:
            self.output('Your chat model was not contacted; the first run in the browser checks it.')
        self.models(download=args.models)
        self.output('Setup is complete. Your saved conversations and settings were not opened.')
        self.output(next_step(self.root, self.home))
        return 0


def parser():
    result = argparse.ArgumentParser(description='Set up Altera explicitly, preserving existing settings and saved data.')
    mode = result.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true', help='Read-only dependency/configuration checks; no installs or bootstrap writes.')
    mode.add_argument('--dry-run', action='store_true', help='Describe setup actions without changing files or packages.')
    result.add_argument('--offline', action='store_true', help='Forbid network access; installation requires local wheels.')
    result.add_argument('--wheelhouse', help='Local directory of dependency wheels, used only with --offline.')
    result.add_argument('--locked', action='store_true', help='Explicitly use requirements.lock.txt instead of runtime version floors.')
    result.add_argument('--allow-package-changes', action='store_true', help='Explicitly permit dependency resolution to replace existing packages.')
    result.add_argument('--home', help='Instance directory (otherwise ALTERA_HOME or the source checkout).')
    result.add_argument('--python', help='Existing Python executable used only to create a missing .venv.')
    result.add_argument('--probe-models', action='store_true', help='Explicit bounded GET of the configured provider model metadata; no inference.')
    result.add_argument('--allow-network-provider', action='store_true', help='Also approve metadata requests to a configured non-loopback provider.')
    result.add_argument('--models', action='store_true', help=f'Download the missing memory (MiniLM) and speech-recognition (Whisper) models once, {MODELS_SIZE}.')
    return result


def main(argv=None, *, root=CODE_ROOT, runner=None, output=None):
    args = parser().parse_args(argv)
    try:
        setup = Setup(root, home=args.home, bootstrap_python=args.python, runner=runner, output=output)
        return setup.execute(args)
    except SetupError as error:
        (output or (lambda message: print(message, flush=True)))('Setup needs attention: ' + str(error))
        return 1
    except OSError:
        (output or (lambda message: print(message, flush=True)))('Setup needs attention: a selected path could not be read or written. Check its location and permissions; existing files were not replaced.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
