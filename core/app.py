"""The same application factory for python altera.py and WSGI hosts."""
import importlib

from flask import Flask, jsonify, send_from_directory
from werkzeug.exceptions import HTTPException

from core.constants import MAX_UPLOAD_BYTES, api_error
from core.paths import CODE_ROOT
from core.runtime import prepare, start_services


BLUEPRINTS = ('pages', 'sessions', 'core_api', 'engagement', 'admin', 'relationships', 'personality', 'search_export',
              'jokes_opinions', 'llm', 'agent_tools', 'companion', 'companions', 'backups', 'knowledge', 'system', 'voice_notes', 'presence',
              'episodes', 'reflection', 'setup', 'lock')


def create_app(*, autostart=True, addons_dir=None, load_addons=True):
    prepare()
    from core.config import get_secret_key
    from core.remote_access import install_remote_access
    from core.request_security import install_request_security
    app = Flask('altera', template_folder=str(CODE_ROOT / 'templates'), static_folder=str(CODE_ROOT / 'static'))
    app.secret_key = get_secret_key()
    app.config.update(MAX_CONTENT_LENGTH=MAX_UPLOAD_BYTES, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax')
    install_remote_access(app)
    install_request_security(app)
    from core.csp import install_content_security_policy
    install_content_security_policy(app)
    for name in BLUEPRINTS:
        app.register_blueprint(importlib.import_module('core.routes.' + name).bp)
    from core.routes.lock import install_lock_guard
    install_lock_guard(app)
    from core import addons
    addons.install(app, addons_dir, load=load_addons)

    @app.errorhandler(Exception)
    def handle_exception(error):
        if isinstance(error, HTTPException):
            messages = {400: 'Invalid request.', 404: 'Not found.', 405: 'Method not allowed.', 413: 'The upload is too large.',
                        415: 'Unsupported content type.', 429: 'Too many requests.'}
            return jsonify(error=messages.get(error.code, 'The request could not be completed.'),
                           code=error.name.lower().replace(' ', '_')), error.code
        app.logger.exception('Unhandled request error')
        return api_error('internal_error', 500)

    @app.get('/static/audio/tts/<path:filename>')
    def private_audio(filename):
        from core.tts import AUDIO_CACHE_DIR
        response = send_from_directory(AUDIO_CACHE_DIR, filename)
        response.headers['Cache-Control'] = 'no-store'
        return response

    if autostart:
        start_services(app)
    return app
