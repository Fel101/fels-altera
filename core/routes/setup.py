"""First-run setup: one tiny model check the setup page can show.

The page itself is rendered by `core.routes.pages`; whether the first run is finished comes from
`/api/onboarding/status`, which already owns that state.
"""
from flask import Blueprint, jsonify, request

from core import setup_models
from core.setup import check as check_model

bp = Blueprint('setup', __name__)


@bp.post('/api/setup/check')
def setup_check():
    """Ask the selected model for one tiny reply. A model that is missing or down is a result, not an error."""
    return jsonify(check_model()), 200, {'Cache-Control': 'no-store'}


def _provider(key):
    from core.llm import get_provider_registry_instance
    return get_provider_registry_instance().providers.get(key)


def _reply(data, status=200):
    return jsonify(data), status, {'Cache-Control': 'no-store'}


@bp.get('/api/setup/local/<provider>')
def setup_local(provider):
    return _reply(setup_models.inspect(provider, _provider(provider)))


@bp.post('/api/setup/local/<provider>/start', defaults={'action': 'start'})
@bp.post('/api/setup/local/ollama/download', defaults={'provider': 'ollama', 'action': 'download'})
def setup_local_begin(provider, action):
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or data.get('confirmed') is not True:
        return _reply({'error': 'Confirm the action in Setup first.'}, 400)
    try:
        return _reply(setup_models.begin(provider, _provider(provider), action, data.get('model')), 202)
    except setup_models.SetupModelError as error:
        return _reply({'error': str(error)}, error.status)


@bp.route('/api/setup/local/<provider>/jobs/<job_id>', methods=['GET', 'DELETE'])
def setup_local_job(provider, job_id):
    try:
        return _reply(setup_models.job_status(provider, job_id, stop=request.method == 'DELETE'))
    except setup_models.SetupModelError as error:
        return _reply({'error': str(error)}, error.status)
