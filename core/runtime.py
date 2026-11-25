"""Shared instance setup and the Windows-friendly server launcher."""
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import shutil
import signal
import threading
from uuid import uuid4

from core import env, fels_suite
from core.launch_cli import parse_args
from core.paths import CODE_ROOT, data_path, root
from core.version import SUITE_API, VERSION

LOGGER = logging.getLogger(__name__)
_lock = threading.RLock()
_started = set()


class PrivateFormatter(logging.Formatter):
    def format(self, record):
        text = super().format(record)
        from core.config import get_config, PLACEHOLDER_KEYS
        def keys(value):
            if isinstance(value, dict):
                for name, item in value.items():
                    if str(name).casefold() in {'api_key', 'token', 'password', 'secret', 'secret_key'} and isinstance(item, str) and len(item) > 4 and item.casefold() not in PLACEHOLDER_KEYS:
                        yield item
                    else:
                        yield from keys(item)
        for secret in keys(get_config()):
            text = text.replace(secret, '[redacted]')
        return re.sub(r'(?i)(authorization[:=]\s*(?:bearer\s+)?|api_key[=:]\s*)([^\s,;]+)', r'\1[redacted]', text)


def configure_logging():
    from core.constants import LOG_FORMAT, LOG_LEVEL_DEFAULT
    logger = logging.getLogger()
    path = data_path('altera.log')
    path.parent.mkdir(parents=True, exist_ok=True)
    formatter = PrivateFormatter(LOG_FORMAT)
    for handler in list(logger.handlers):
        if getattr(handler, 'altera_file', False) and Path(handler.baseFilename) != path:
            logger.removeHandler(handler)
            handler.close()
    if not any(getattr(h, 'altera_file', False) for h in logger.handlers):
        handler = RotatingFileHandler(path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8', delay=True)
        handler.altera_file = True
        logger.addHandler(handler)
    if not any(getattr(h, 'altera_console', False) for h in logger.handlers):
        handler = logging.StreamHandler()
        handler.altera_console = True
        logger.addHandler(handler)
    for handler in logger.handlers:
        if getattr(handler, 'altera_file', False) or getattr(handler, 'altera_console', False):
            handler.setFormatter(formatter)
    level = getattr(logging, env.get('LOG_LEVEL', LOG_LEVEL_DEFAULT).upper(), logging.INFO)
    logger.setLevel(level)


def prepare():
    """Schema, search and instance files are identical for script and WSGI launches."""
    with _lock:
        home = root()
        home.mkdir(parents=True, exist_ok=True)
        config_path = home / 'config.yaml'
        if not config_path.exists():
            shutil.copyfile(CODE_ROOT / 'config.example.yaml', config_path)
        if home != CODE_ROOT:
            for source, target in [('personality.yaml', 'personality.yaml')]:
                path = home / target
                if not path.exists():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(CODE_ROOT / source, path)
        from dotenv import load_dotenv
        load_dotenv(home / '.env')
        configure_logging()
        from core.config import CONFIG_PATH, LOCAL_CONFIG_PATH, PERSONALITY_PATH
        import yaml
        for path in (CONFIG_PATH, LOCAL_CONFIG_PATH, PERSONALITY_PATH):
            if not path.exists() and path == LOCAL_CONFIG_PATH:
                continue
            try:
                value = yaml.safe_load(path.read_text(encoding='utf-8'))
                if value is None and path == LOCAL_CONFIG_PATH:
                    continue
                if not isinstance(value, dict):
                    raise ValueError()
            except (OSError, ValueError, yaml.YAMLError):
                raise RuntimeError(f'{path.name} must be a readable YAML mapping. Correct it before starting Altera.') from None
        from core.datastore import get_datastore
        store = get_datastore()
        store._ensure_schema()
        store.ensure_fts_table('messages_fts')
        from core.turns import get_turn_service
        turns = get_turn_service(store)  # Interrupted replies are repaired before the first browser request.
        try:
            from core.memory_recovery import recover_pending
            recover_pending(turns)
        except Exception:
            LOGGER.warning('Saved memory-recency recovery is unavailable; pending updates are preserved.')
        from core.presence import get_presence_service
        get_presence_service(store)  # Reconcile interrupted jobs before the scheduler or restore UI can run.
        from core.episodes import get_episode_service
        from core.reflection import get_reflection_service
        get_episode_service(store)
        get_reflection_service(store)


def start_services(app):
    """Once per process/instance; factory callers can disable all service startup."""
    from core.config import get_config
    from core.state_gate import STATE
    with _lock:
        identity = str(root())
        if identity in _started or env.get('DISABLE_AUTOSTART') == '1' or STATE.fault:
            return
        _started.add(identity)
        try:
            from core import companions
            companions.migrate_legacy()
        except Exception:
            LOGGER.exception('Could not migrate older conversations; they remain on disk')
        from core.profile import load_user_profile
        voice_on = (load_user_profile().get('preferences') or {}).get('voice_enabled', True)
        if (get_config().get('voice') or {}).get('tts_enabled') and voice_on:
            from core import voice_service
            voice_service.start_in_background()
        from core.presence import start_scheduler
        start_scheduler(app)
        from core.reflection import start_scheduler as start_reflection_scheduler
        start_reflection_scheduler(app)
        app.extensions['altera_services_started'] = True


def main(app=None, *, args=None):
    args = parse_args() if args is None else args
    if app is None:
        from core.app import create_app
        app = create_app()
    from core.config import get_config
    config = get_config()
    settings = config.get('app') or {}
    host = settings.get('host') or '127.0.0.1'
    port = int(settings.get('port', fels_suite.PORTS['altera']))
    if args.dev or settings.get('debug', False):
        from werkzeug.debug import DebuggedApplication
        from werkzeug.serving import make_server
        app.debug = True
        debugger = DebuggedApplication(app, evalex=True)
        debugger.trusted_hosts.append(host)
        server = make_server(host, port, debugger, threaded=True)
        bound_host, bound_port = server.server_address[:2]
        run, close = server.serve_forever, server.server_close
    else:
        from waitress.server import create_server
        from core.backups import MAX_ARCHIVE_BYTES
        # A page load asks for about thirty files at once, so a short queue is normal; waitress warns about every one.
        logging.getLogger('waitress.queue').setLevel(logging.ERROR)
        # Keep raw Serve headers for the loopback + host + HTTPS + identity checks in remote_access.
        # No proxy middleware rewrites remote_addr from an untrusted forwarded header.
        server = create_server(app, host=host, port=port, threads=8, max_request_body_size=MAX_ARCHIVE_BYTES + 65536,
                               clear_untrusted_proxy_headers=False)
        if hasattr(server, 'effective_listen'):
            bound_host, bound_port = server.effective_listen[0]
        else:
            bound_host, bound_port = server.effective_host, server.effective_port
        run, close = server.run, server.close

    url = _http_url(bound_host, bound_port)
    previous_sigterm = suite_env = None
    try:
        if threading.current_thread() is threading.main_thread():
            previous_sigterm = signal.signal(signal.SIGTERM, signal.default_int_handler)
        problem = fels_suite.suite_dir_problem()
        if problem:
            LOGGER.warning('%s', problem)
        suite_env = dict(os.environ, FELS_SUITE_DIR=str(fels_suite.suite_dir()))
        try:
            fels_suite.inbox_dir('altera', suite_env, make=True)
        except OSError:
            LOGGER.warning("Could not create the Fel's Studio inbox.", exc_info=True)
        try:
            fels_suite.write_address('altera', url, VERSION, SUITE_API, uuid4().hex, suite_env)
        except OSError:
            LOGGER.warning("Could not publish Altera's address for Fel's Studio.", exc_info=True)
        LOGGER.info('Altera %s listening at %s; instance %s; source %s', VERSION, url, root(), CODE_ROOT)
        if args.open:
            # Keep the configured browser origin so existing PWA storage and drafts are still available.
            browser_url = _http_url(host, bound_port)
            threading.Thread(target=_open_when_ready, args=(browser_url,), name='altera-browser', daemon=True).start()
        run()
    finally:
        try:
            close()
        finally:
            if previous_sigterm is not None:
                signal.signal(signal.SIGTERM, previous_sigterm)
            if suite_env is not None:
                fels_suite.remove_address('altera', suite_env)


def _http_url(host, port):
    local_host = {'0.0.0.0': '127.0.0.1', '::': '::1'}.get(host, host)
    if ':' in local_host:
        local_host = f'[{local_host}]'
    return f'http://{local_host}:{port}'


def _open_when_ready(url):
    import time
    import urllib.request
    import webbrowser

    for _ in range(30):
        try:
            with urllib.request.urlopen(url + '/api/offline/status', timeout=.5):
                webbrowser.open(url + '/home')
                return
        except Exception:
            time.sleep(.5)
