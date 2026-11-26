"""Setup tests use fake interpreters/processes and isolated instance files only."""
import builtins
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace as NS
import zipfile

import pytest

from scripts import setup_altera as setup


class FakeProcesses:
    def __init__(self, root, *, existing=True, missing=None, incompatible=None):
        self.root, self.calls = root, []
        self.python = root / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        self.version, self.bootstrap_version = [3, 11, 9], [3, 11, 9]
        self.missing, self.incompatible = list(missing or []), list(incompatible or [])
        self.installed = {'pip': '25.0', 'flask': '3.0', 'pyyaml': '6.0'}
        self.healthy, self.has_pip, self.virtual = True, True, True
        self.install_result, self.create_result, self.configuration = 0, 0, 'ok'
        self.metadata, self.constraints = 'listed', None
        self.models = {'memory': 'ready', 'speech': 'ready', 'speech_model': 'base'}
        if existing:
            self.make_python()

    def make_python(self):
        self.python.parent.mkdir(parents=True, exist_ok=True)
        self.python.write_bytes(b'fake interpreter, never executable')

    def __call__(self, command, **options):
        self.calls.append((command, dict(options)))
        if '-c' in command:
            script = command[command.index('-c') + 1]
            if script == setup.VERSION_SCRIPT:
                value = {'version': self.bootstrap_version}
            elif script == setup.ENVIRONMENT_SCRIPT:
                value = {'version': self.version, 'prefix': str(self.root / '.venv'), 'virtual': self.virtual,
                         'pip': self.has_pip, 'installed': dict(self.installed),
                         'missing': list(self.missing), 'incompatible': list(self.incompatible)}
            elif script == setup.CONFIGURATION_SCRIPT:
                payload = json.loads(options['input'])
                value = {'configuration': self.configuration, 'metadata': self.metadata if payload['probe'] else 'not_requested'}
            elif script == setup.MODELS_SCRIPT:
                value = dict(self.models)
            else:
                pytest.fail('Unexpected helper code')
            return NS(returncode=0, stdout=json.dumps(value), stderr='')
        if 'venv' in command:
            if self.create_result == 0:
                self.make_python()
            return NS(returncode=self.create_result, stdout='', stderr='synthetic venv result')
        if 'install' in command:
            if '--constraint' in command:
                self.constraints = Path(command[command.index('--constraint') + 1]).read_text(encoding='utf-8')
            if self.install_result == 0:
                self.missing, self.incompatible, self.healthy = [], [], True
            return NS(returncode=self.install_result, stdout='', stderr='synthetic package result')
        if command[-1] == 'check':
            return NS(returncode=0 if self.healthy else 1, stdout='', stderr='synthetic dependency check')
        pytest.fail('Unexpected command: ' + repr(command))


@pytest.fixture
def setup_world(tmp_path):
    root, home = tmp_path / 'source with spaces', tmp_path / 'separate instance'
    root.mkdir()
    (root / 'requirements.txt').write_text('flask>=3.0\nPyYAML>=6.0\n', encoding='utf-8')
    (root / 'requirements.lock.txt').write_text('flask==3.0\nPyYAML==6.0\n', encoding='utf-8')
    (root / 'config.example.yaml').write_text('llm:\n  provider: lmstudio\n', encoding='utf-8')
    (root / 'personality.yaml').write_text('name: Synthetic\n', encoding='utf-8')
    return NS(root=root, home=home, messages=[])


def invoke(w, processes, *args):
    return setup.main(['--home', str(w.home), *args], root=w.root, runner=processes, output=w.messages.append)


def installs(processes):
    return [command for command, _ in processes.calls if 'install' in command]


def test_read_only_missing_environment_and_dry_run_never_create_or_install(setup_world):
    w = setup_world
    processes = FakeProcesses(w.root, existing=False)
    assert invoke(w, processes, '--check') == 1
    assert not (w.root / '.venv').exists() and not w.home.exists()
    assert invoke(w, processes, '--dry-run') == 0
    assert not (w.root / '.venv').exists() and not w.home.exists()
    assert not installs(processes)
    assert all('-c' in command for command, _ in processes.calls)


def test_explicit_setup_creates_missing_venv_and_installs_without_service_or_model_commands(setup_world):
    w = setup_world
    processes = FakeProcesses(w.root, existing=False, missing=['flask', 'PyYAML'])
    assert invoke(w, processes) == 0
    assert processes.python.is_file()
    assert len(installs(processes)) == 1
    command = installs(processes)[0]
    assert command[command.index('--index-url') + 1] == setup.OFFICIAL_INDEX
    assert '--isolated' in command and '--only-binary=:all:' in command
    assert '--upgrade' not in command and '--force-reinstall' not in command
    assert 'flask==3.0' in processes.constraints and 'pip==25.0' in processes.constraints
    assert (w.home / 'config.yaml').read_bytes() == (w.root / 'config.example.yaml').read_bytes()
    assert (w.home / 'personality.yaml').read_bytes() == (w.root / 'personality.yaml').read_bytes()
    assert not (w.home / 'content/adult_tiers.yaml').exists()
    assert all(not any(str(part).endswith('altera.py') for part in command) for command, _ in processes.calls)


def test_repeat_setup_preserves_all_existing_private_files_and_never_reinstalls(setup_world):
    w = setup_world
    originals = {'config.yaml': b'llm:\n  provider: custom\n  model: selected-model\n',
                 'config.local.yaml': b'llm:\n  api_key: private-test-key\n', '.env': b'PRIVATE_TEST_KEY=value\n',
                 'personality.yaml': b'name: Existing\n', 'data/conversations.db': b'PRIVATE_SYNTHETIC_DATABASE',
                 'backups/saved.zip': b'PRIVATE_SYNTHETIC_ARCHIVE', 'user_profile.yaml': b'name: Existing user\n',
                 'content/adult_tiers.yaml': b'off: {}\n'}
    for name, content in originals.items():
        path = w.home / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    processes = FakeProcesses(w.root)
    for _ in range(2):
        assert invoke(w, processes) == 0
    assert not installs(processes)
    assert {name: (w.home / name).read_bytes() for name in originals} == originals
    assert 'private-test-key' not in '\n'.join(w.messages)


def test_check_is_read_only_even_when_starter_files_are_missing(setup_world):
    w = setup_world
    processes = FakeProcesses(w.root)
    assert invoke(w, processes, '--check', '--offline') == 0
    assert not w.home.exists() and not installs(processes)
    probe = [json.loads(options['input']) for command, options in processes.calls if setup.CONFIGURATION_SCRIPT in command]
    assert probe == [{'code_root': str(w.root), 'home': str(w.home), 'probe': False, 'allow_network_provider': False}]


@pytest.mark.parametrize('problem', ['python', 'bootstrap_python', 'missing_pip', 'not_virtual', 'incomplete'])
def test_missing_or_unsupported_prerequisites_fail_without_replacement(setup_world, problem):
    w = setup_world
    processes = FakeProcesses(w.root, existing=problem != 'bootstrap_python')
    if problem == 'python':
        processes.version = [3, 14, 0]
    elif problem == 'bootstrap_python':
        processes.bootstrap_version = [3, 10, 1]
    elif problem == 'missing_pip':
        processes.has_pip = False
    elif problem == 'not_virtual':
        processes.virtual = False
    else:
        processes.python.unlink()
    assert invoke(w, processes) == 1
    assert not installs(processes) and not w.home.exists()
    assert not [command for command, _ in processes.calls if 'venv' in command]


@pytest.mark.parametrize('bad_relationships', [False, True])
def test_existing_conflicts_require_explicit_package_change_permission(setup_world, bad_relationships):
    w = setup_world
    processes = FakeProcesses(w.root, incompatible=[] if bad_relationships else ['flask'])
    processes.healthy = not bad_relationships
    assert invoke(w, processes) == 1
    assert not installs(processes) and not w.home.exists()
    assert invoke(w, processes, '--allow-package-changes') == 0
    assert '--constraint' not in installs(processes)[0] and '--upgrade' not in installs(processes)[0]


def test_offline_setup_requires_local_wheels_and_never_uses_an_index(setup_world):
    w = setup_world
    processes = FakeProcesses(w.root, existing=False, missing=['flask'])
    assert invoke(w, processes, '--offline') == 1
    assert not (w.root / '.venv').exists()
    wheelhouse = w.root / 'prepared wheels'
    wheelhouse.mkdir()
    assert invoke(w, processes, '--offline', '--wheelhouse', str(wheelhouse)) == 0
    command = installs(processes)[0]
    assert '--no-index' in command and '--index-url' not in command
    assert command[command.index('--find-links') + 1] == str(wheelhouse)


def test_offline_dry_run_detects_missing_wheelhouse_for_existing_environment(setup_world):
    w = setup_world
    processes = FakeProcesses(w.root, missing=['flask'])
    assert invoke(w, processes, '--offline', '--dry-run') == 1
    assert not installs(processes) and not w.home.exists()


def test_offline_missing_wheel_failure_preserves_existing_instance(setup_world):
    w = setup_world
    w.home.mkdir()
    secret = w.home / 'config.local.yaml'
    secret.write_bytes(b'private: unchanged\n')
    wheelhouse = w.root / 'empty wheels'
    wheelhouse.mkdir()
    processes = FakeProcesses(w.root, missing=['flask'])
    processes.install_result = 1
    assert invoke(w, processes, '--offline', '--wheelhouse', str(wheelhouse)) == 1
    assert secret.read_bytes() == b'private: unchanged\n' and not (w.home / 'config.yaml').exists()
    assert processes.python.is_file()


def test_offline_wheels_cannot_bypass_no_index_with_direct_url_dependencies(setup_world):
    w = setup_world
    wheelhouse = w.root / 'prepared wheels'
    wheelhouse.mkdir()
    with zipfile.ZipFile(wheelhouse / 'synthetic-1.0-py3-none-any.whl', 'w') as archive:
        archive.writestr('synthetic-1.0.dist-info/METADATA',
                         'Metadata-Version: 2.1\nName: synthetic\nVersion: 1.0\nRequires-Dist: extra @ https://unapproved.invalid/extra.whl\n')
    processes = FakeProcesses(w.root, missing=['flask'])
    assert invoke(w, processes, '--offline', '--wheelhouse', str(wheelhouse)) == 1
    assert not installs(processes) and not w.home.exists()
    assert any('direct URL dependency' in line for line in w.messages)


def test_registry_wheel_metadata_can_be_inspected_without_installing(tmp_path):
    with zipfile.ZipFile(tmp_path / 'synthetic-1.0-py3-none-any.whl', 'w') as archive:
        archive.writestr('synthetic-1.0.dist-info/METADATA',
                         'Metadata-Version: 2.1\nName: synthetic\nVersion: 1.0\nRequires-Dist: flask>=3.0\n')
    setup.validate_wheelhouse(tmp_path)


def test_shipped_runtime_and_lock_requirements_are_registry_only():
    root = Path(__file__).resolve().parent.parent
    for locked in (False, True):
        _, lines = setup.requirements(root, locked)
        assert lines and all('://' not in line and not line.startswith('-') for line in lines)


def test_locked_requirements_are_explicit_and_checked_from_absolute_source_path(setup_world):
    w = setup_world
    processes = FakeProcesses(w.root, missing=['flask'])
    assert invoke(w, processes, '--locked') == 0
    assert installs(processes)[0][-1] == str(w.root / 'requirements.lock.txt')
    checks = [json.loads(options['input']) for command, options in processes.calls if setup.ENVIRONMENT_SCRIPT in command]
    assert checks[0]['requirements'] == ['flask==3.0', 'PyYAML==6.0']


def test_arbitrary_cwd_and_altera_home_keep_environment_beside_source(setup_world, tmp_path, monkeypatch):
    w = setup_world
    elsewhere = tmp_path / 'unrelated cwd'
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv('ALTERA_HOME', str(w.home))
    processes = FakeProcesses(w.root)
    assert setup.main([], root=w.root, runner=processes, output=w.messages.append) == 0
    assert not (elsewhere / '.venv').exists()
    assert all(options['cwd'] == str(w.root) for _, options in processes.calls)
    assert (w.home / 'config.yaml').is_file()


def test_ambient_private_pip_indexes_and_python_paths_are_not_used(setup_world, monkeypatch):
    w = setup_world
    monkeypatch.setenv('PIP_INDEX_URL', 'https://private-test:secret@example.invalid/simple')
    monkeypatch.setenv('PIP_EXTRA_INDEX_URL', 'https://unapproved.invalid/simple')
    monkeypatch.setenv('PYTHONPATH', 'untrusted_modules')
    processes = FakeProcesses(w.root, missing=['flask'])
    assert invoke(w, processes) == 0
    for _, options in processes.calls:
        env = options['env']
        assert 'PIP_INDEX_URL' not in env and 'PIP_EXTRA_INDEX_URL' not in env and 'PYTHONPATH' not in env
        assert env['PIP_CONFIG_FILE'] == os.devnull and env['ALTERA_DISABLE_AUTOSTART'] == '1'
        assert env['HF_HUB_OFFLINE'] == '1' and env['ALTERA_HOME'] == str(w.home)
    assert 'secret' not in '\n'.join(w.messages)


@pytest.mark.parametrize('line', ['package @ https://example.invalid/package.whl', '--extra-index-url https://example.invalid', '-r nested.txt'])
def test_non_registry_requirement_inputs_are_refused_before_subprocess(setup_world, line):
    w = setup_world
    (w.root / 'requirements.txt').write_text(line, encoding='utf-8')
    processes = FakeProcesses(w.root)
    assert invoke(w, processes) == 1
    assert processes.calls == [] and not w.home.exists()


def test_invalid_configuration_is_preserved_with_actionable_failure(setup_world):
    w = setup_world
    w.home.mkdir()
    path = w.home / 'config.yaml'
    path.write_text('invalid: [unfinished', encoding='utf-8')
    processes = FakeProcesses(w.root)
    processes.configuration = 'invalid'
    assert invoke(w, processes, '--check') == 1
    assert path.read_text(encoding='utf-8') == 'invalid: [unfinished'
    assert any('YAML mapping' in line for line in w.messages)


def test_provider_probe_is_explicit_bounded_and_forbidden_offline(setup_world):
    w = setup_world
    processes = FakeProcesses(w.root)
    assert invoke(w, processes, '--check', '--probe-models') == 0
    probe_calls = [(command, options) for command, options in processes.calls if setup.CONFIGURATION_SCRIPT in command]
    assert len(probe_calls) == 1 and probe_calls[0][1]['timeout'] == 6
    assert json.loads(probe_calls[0][1]['input'])['probe'] is True
    count = len(processes.calls)
    assert invoke(w, processes, '--offline', '--probe-models') == 1
    assert len(processes.calls) == count
    assert invoke(w, processes, '--allow-network-provider') == 1


@pytest.mark.parametrize('status', ['unreachable', 'model_not_listed', 'credentials_refused', 'network_approval_required'])
def test_metadata_readiness_failures_do_not_claim_inference_readiness(setup_world, status):
    w = setup_world
    processes = FakeProcesses(w.root)
    processes.metadata = status
    assert invoke(w, processes, '--check', '--probe-models') == 1
    assert not installs(processes) and not w.home.exists()
    assert not any('selected model is listed' in line for line in w.messages)


def test_helper_timeout_is_actionable_and_never_starts_a_service(setup_world):
    w = setup_world
    def expired(command, **options):
        raise subprocess.TimeoutExpired(command, options['timeout'])
    assert invoke(w, expired, '--check') == 1
    assert any('could not finish' in line for line in w.messages)
    assert not w.home.exists()


@pytest.mark.parametrize(('base_url', 'approved', 'expected'), [
    ('http://127.0.0.1:1234/v1', False, 'listed'),
    ('https://provider.invalid/v1', False, 'network_approval_required'),
    ('https://provider.invalid/v1', True, 'listed'),
])
@pytest.mark.parametrize('selection', [{'providers': {}}, {'provider': 'openrouter', 'model': 'selected-model'}])
@pytest.mark.parametrize('models_endpoint', ['/models', '/custom/model-list'])
def test_actual_metadata_helper_uses_fake_get_only_without_redirects_proxies_or_credentials_output(setup_world, monkeypatch, capsys, base_url, approved, expected, selection, models_endpoint):
    import urllib.request
    w = setup_world
    w.home.mkdir()
    (w.home / 'config.yaml').write_text('llm:\n  providers: {}\n', encoding='utf-8')
    calls, handlers = [], []
    provider = NS(base_url=base_url, api_key='synthetic-private-key', enabled=True, models_endpoint=models_endpoint)
    registry = NS(get_active_provider=lambda: provider, get_active_model=lambda: 'selected-model')
    monkeypatch.setitem(sys.modules, 'core.config', NS(get_config=lambda: {'llm': selection}))
    monkeypatch.setitem(sys.modules, 'core.providers', NS(ProviderRegistry=lambda config: registry))
    monkeypatch.setitem(sys.modules, 'dotenv', NS(load_dotenv=lambda *args, **kwargs: None))
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self, limit):
            assert limit == 65537
            return b'{"data":[{"id":"selected-model"}]}'
    class Opener:
        def open(self, request, timeout):
            calls.append(request)
            assert timeout == 2 and request.get_method() == 'GET'
            assert request.full_url == base_url + models_endpoint
            assert request.get_header('Authorization') == 'Bearer synthetic-private-key'
            return Response()
    def fake_opener(*chosen_handlers):
        handlers.extend(chosen_handlers)
        return Opener()
    monkeypatch.setattr(urllib.request, 'build_opener', fake_opener)
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps({'home': str(w.home), 'code_root': str(w.root), 'probe': True, 'allow_network_provider': approved})))
    monkeypatch.setattr(sys, 'path', list(sys.path))
    original_import = builtins.__import__
    def guarded_import(name, *args, **kwargs):
        assert name not in {'core.app', 'core.runtime', 'core.llm', 'core.datastore', 'openai'}
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded_import)
    try:
        exec(compile(setup.CONFIGURATION_SCRIPT, '<setup metadata helper>', 'exec'), {})
    except SystemExit as error:
        assert error.code == 0
    printed = capsys.readouterr().out
    assert json.loads(printed)['metadata'] == expected and 'synthetic-private-key' not in printed
    if expected == 'listed':
        assert len(calls) == 1 and handlers[0].proxies == {}
        assert handlers[1].redirect_request(None, None, None, None, None, None) is None
    else:
        assert calls == [] and handlers == []


def model_calls(processes):
    return [(json.loads(options['input']), options) for command, options in processes.calls if setup.MODELS_SCRIPT in command]


def test_setup_reports_missing_models_and_downloads_nothing_unless_asked(setup_world):
    w = setup_world
    processes = FakeProcesses(w.root)
    processes.models = {'memory': 'missing', 'speech': 'missing', 'speech_model': 'base'}
    assert invoke(w, processes) == 0
    [(payload, options)] = model_calls(processes)
    assert payload['download'] is False and options['env']['HF_HUB_OFFLINE'] == '1' and options['timeout'] == 60
    text = '\n'.join(w.messages)
    assert 'Memory model (MiniLM): not downloaded' in text and 'Whisper base): not downloaded' in text
    assert '--models' in text and setup.MODELS_SIZE in text


def test_models_flag_downloads_once_with_hugging_face_allowed_online(setup_world):
    w = setup_world
    processes = FakeProcesses(w.root)
    assert invoke(w, processes, '--models') == 0
    [(payload, options)] = model_calls(processes)
    assert payload['download'] is True and options['timeout'] == 1800
    assert 'HF_HUB_OFFLINE' not in options['env'] and 'TRANSFORMERS_OFFLINE' not in options['env']
    assert options['env']['ALTERA_DISABLE_AUTOSTART'] == '1'
    assert any('Memory model (MiniLM): ready' in line for line in w.messages)
    assert not any('Run setup again with --models' in line for line in w.messages)


def test_a_failed_model_download_is_reported_and_keeps_the_rest_of_setup(setup_world):
    w = setup_world
    processes = FakeProcesses(w.root)
    processes.models = {'memory': 'ready', 'speech': 'failed', 'speech_model': 'small'}
    assert invoke(w, processes, '--models') == 1
    assert (w.home / 'config.yaml').is_file()
    text = '\n'.join(w.messages)
    assert 'Whisper small): could not be downloaded' in text and 'text chat works without these models' in text


@pytest.mark.parametrize('other', ['--offline', '--check'])
def test_models_flag_is_refused_offline_and_in_read_only_checks(setup_world, other):
    w = setup_world
    processes = FakeProcesses(w.root)
    assert invoke(w, processes, '--models', other) == 1
    assert processes.calls == []


def test_dry_run_with_models_only_describes_the_download(setup_world):
    w = setup_world
    processes = FakeProcesses(w.root)
    assert invoke(w, processes, '--dry-run', '--models') == 0
    assert model_calls(processes) == []
    assert any('Would download any missing memory' in line for line in w.messages)


def test_setup_ends_by_naming_the_start_command_and_any_separate_instance(setup_world, tmp_path):
    w = setup_world
    assert invoke(w, FakeProcesses(w.root)) == 0
    assert w.messages[-2] == 'Setup is complete. Your saved conversations and settings were not opened.'
    assert w.messages[-1].startswith('Next, run ')
    assert not any('unlock' in line or 'probe' in line for line in w.messages)
    root, home = tmp_path / 'Altera', tmp_path / 'instance'
    assert setup.next_step(root, root, windows=True) == rf'Next, run .\start.bat in {root}.'
    assert setup.next_step(root, home, windows=False) == (
        f'Next, run .venv/bin/python altera.py --open in {root}. Set ALTERA_HOME to {home} first, so Altera uses this '
        'instance.')


@pytest.mark.parametrize(('memory_path', 'stt_model', 'download', 'expected'), [
    (None, 'base', False, {'memory': 'missing', 'speech': 'missing'}),
    (None, 'base', True, {'memory': 'ready', 'speech': 'ready'}),
    ('models/minilm', 'C:/models/whisper', True, {'memory': 'configured_missing', 'speech': 'custom_missing'}),
])
def test_actual_models_helper_downloads_only_when_asked_and_never_into_a_chosen_folder(setup_world, monkeypatch, capsys,
                                                                                       memory_path, stt_model, download, expected):
    state = {'memory': False, 'speech': False, 'downloads': []}
    def ready():
        if not state['memory']:
            raise ValueError('missing')
        return 'signature'
    class Chroma:
        def _download_model_if_not_exists(self):
            state['downloads'].append('minilm'); state['memory'] = True
    def download_model(size):
        state['downloads'].append(size); state['speech'] = True
    config = {'memory': {'embedding_model_path': memory_path}, 'voice': {'stt_model': stt_model}}
    monkeypatch.setitem(sys.modules, 'core.config', NS(get_config=lambda: config))
    monkeypatch.setitem(sys.modules, 'core.local_embeddings', NS(
        _source_path=lambda: Path('folder'), _signature=lambda folder: ready(), _read_assets=lambda folder, sig: {},
        _metadata=lambda values: None))
    monkeypatch.setitem(sys.modules, 'core.stt', NS(model_status=lambda size: 'cached' if state['speech'] else 'unavailable'))
    monkeypatch.setitem(sys.modules, 'chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2', NS(ONNXMiniLM_L6_V2=Chroma))
    monkeypatch.setitem(sys.modules, 'faster_whisper.utils', NS(download_model=download_model))
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps({'code_root': str(setup_world.root), 'download': download})))
    monkeypatch.setattr(sys, 'stdout', sys.stdout)   # the helper points stdout at stderr; this puts it back afterwards
    monkeypatch.setattr(sys, 'path', list(sys.path))
    exec(compile(setup.MODELS_SCRIPT, '<setup models helper>', 'exec'), {})
    result = json.loads(capsys.readouterr().out)
    assert {key: result[key] for key in expected} == expected
    assert state['downloads'] == (['minilm', 'base'] if expected['memory'] == 'ready' else [])


def test_launchers_use_their_own_source_location_and_forward_arguments():
    root = Path(__file__).resolve().parent.parent
    windows, unix = (root / 'setup.bat').read_text(), (root / 'setup.sh').read_text()
    assert '%~dp0scripts\\setup_altera.py' in windows and '%*' in windows
    assert '"$script_dir/scripts/setup_altera.py" "$@"' in unix and 'ALTERA_SETUP_PYTHON' in unix
    assert 'altera.py --open' not in windows and 'altera.py --open' not in unix
    # Every supported version is looked for, 3.12 first (the local voice runs on it too).
    assert 'for %%V in (3.12 3.13 3.11)' in windows and 'for version in 3.12 3.13 3.11' in unix
    assert {f'{major}.{minor}' for major, minor in setup.SUPPORTED_PYTHONS} == {'3.11', '3.12', '3.13'}


@pytest.mark.parametrize(('version', 'accepted'), [([3, 10, 14], False), ([3, 11, 9], True), ([3, 12, 10], True),
                                                   ([3, 13, 9], True), ([3, 14, 0], False)])
def test_setup_accepts_python_3_11_to_3_13_for_a_new_environment(setup_world, version, accepted):
    w = setup_world
    processes = FakeProcesses(w.root, existing=False)
    processes.bootstrap_version = processes.version = version
    assert invoke(w, processes) == (0 if accepted else 1)
    if accepted:
        assert f'Creating the missing .venv with Python {".".join(map(str, version))}.' in w.messages
    else:
        assert any('Python 3.11, 3.12 or 3.13 is required (3.12 if you want the local voice too)' in line
                   for line in w.messages) and not installs(processes)
