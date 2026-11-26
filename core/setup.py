"""First-run setup: is a chat model chosen, reachable and answering?

`core.health` lists the provider's models and says plainly that inference has not been tested. That is right for a
background health check, but it cannot tell a starter config (no model chosen) from a working chat. The setup check
spends a tiny completion on it, only when a person asks for it, and turns each failure into something to do.
"""
import logging
import time

LOGGER = logging.getLogger(__name__)

# One short question. It is sent only from the setup page and never carries anything a person wrote.
PROBE_PROMPT = 'Reply with the single word OK.'
PROBE_MAX_TOKENS = 2048         # room for a reasoning model to think before it says OK
PROBE_TIMEOUT_S = 60.0
DETAIL_CHARS = 220

# What to start when a local provider is not there. Both are named because either one is enough.
LOCAL_START = 'Choose LM Studio or Ollama above for help starting it and finding a chat model.'


def status() -> dict:
    """Whether the first run is finished, and what the person is called."""
    from core.profile import is_onboarding_complete, load_user_profile
    return {'complete': is_onboarding_complete(), 'name': (load_user_profile().get('name') or '').strip()}


def check(config=None, *, ask=None) -> dict:
    """Ask the selected model for one tiny reply.

    Returns `{ok: True, provider, model, reply, ms}` or `{ok: False, reason, detail, hint}`. It never raises: a
    provider that is missing, down, misconfigured or silent is a result the setup page can show.
    """
    from core.config import get_config
    from core.providers import get_provider_registry
    config = get_config() if config is None else config
    registry = get_provider_registry(config)
    provider = registry.get_active_provider()
    key = registry.get_active_provider_key()
    model = registry.get_active_model() or (provider.default_model if provider else '')
    name = (provider.name if provider else '') or key or 'the provider'
    result = {'ok': False, 'provider': key, 'provider_name': name, 'model': model or '',
              'provider_url': provider.base_url if provider else ''}
    if not provider or not provider.enabled:
        return {**result, 'reason': 'no_provider', 'detail': 'No chat provider is switched on.',
                'hint': 'Choose a provider above, then check again.'}
    if not model:
        return {**result, 'reason': 'no_model', 'detail': 'No model is selected.',
                'hint': f'Choose a model served by {name} above, then check again.'}

    started = time.monotonic()
    try:
        ask_once = ask or _ask
        reply = ask_once(provider, model)
        if not str(reply or '').strip():
            reply = ask_once(provider, model)   # a reasoning model's empty first answer, asked once more
    except Exception as error:
        LOGGER.info('The setup model check failed for %s: %s', model, error)
        reason = _reason(error)
        return {**result, 'reason': reason, 'detail': _detail(error), 'hint': _hint(reason, name, model, provider.base_url)}
    elapsed = int((time.monotonic() - started) * 1000)
    reply = (reply or '').strip()
    if not reply:
        return {**result, 'reason': 'empty', 'detail': f'{model} answered with nothing.',
                'hint': f'Try another model served by {name}.'}
    return {**result, 'ok': True, 'reply': reply[:120], 'ms': elapsed}


def _reason(error) -> str:
    status = getattr(error, 'status_code', None)
    if status in (401, 403):
        return 'credentials'
    detail = str(getattr(error, 'message', None) or error).lower()
    if status == 404 or (status == 400 and 'model' in detail):
        return 'missing_model'
    return 'unreachable'


def _detail(error) -> str:
    return ' '.join(str(getattr(error, 'message', None) or error).split())[:DETAIL_CHARS]


def _hint(reason, name, model, base_url) -> str:
    url = base_url or 'the provider address'
    if reason == 'credentials':
        return f'{name} refused the connection. Check its API key in Settings, then try again.'
    if reason == 'missing_model':
        if name.lower().startswith('ollama'):
            return f'Ollama is running but {model} is missing. Choose an installed model or download one above.'
        return (f'{name} is running but does not serve {model}. Open {name}, load a chat model, '
                'then choose it above.')
    if reason == 'empty':
        return f'Try another model served by {name}.'
    if name.lower().startswith(('lm studio', 'ollama')):
        return f'Altera could not reach {name} at {url}. {LOCAL_START}'
    return (f'Altera could not reach {name} at {url}. Check the address in Settings '
            'and make sure the service is running.')


def _thought(message) -> bool:
    """Whether the reply carried reasoning text: LM Studio and Ollama return it beside the content."""
    extra = getattr(message, 'model_extra', None) or {}
    return any(str(extra.get(key) or getattr(message, key, '') or '').strip() for key in ('reasoning_content', 'reasoning'))


def _ask(provider, model) -> str:
    """One tiny completion, with a bounded timeout and no retries: the setup page is waiting on it."""
    from openai import OpenAI
    client = OpenAI(base_url=provider.base_url, api_key=provider.api_key or 'not-needed',
                    timeout=PROBE_TIMEOUT_S, max_retries=0)
    try:
        answer = client.chat.completions.create(
            model=model,
            messages=[{'role': 'user', 'content': PROBE_PROMPT}],
            max_tokens=PROBE_MAX_TOKENS,
            temperature=0,
        )
        if not answer.choices:
            return ''
        message = answer.choices[0].message
        content = message.content or ''
        if not content.strip() and _thought(message):
            # A reasoning model that spent the whole budget thinking is still a model that answers.
            return 'OK (it was still thinking)'
        return content
    finally:
        try:
            client.close()
        except Exception:
            pass
