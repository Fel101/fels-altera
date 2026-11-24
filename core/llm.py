"""
Altera — LLM Client (Session 2 of Architecture Refactor)

Shared OpenAI-compatible client with retry logic, used by all core modules.
Swap LM Studio for Ollama or another backend in one place.
Supports dynamic model/provider switching via ProviderRegistry.
"""

import json
import logging
import re
import time
import threading
from contextlib import ExitStack
from urllib.parse import urlsplit

from core.providers import ProviderRegistry, get_provider_registry
from core.owned_iterator import close_owned, owned_iterator
from core import suite_gpu


# ============================================================
# Configuration defaults (mirrors config.py; can be overridden)
# ============================================================

DEFAULT_BASE_URL = "http://localhost:1234/v1"
DEFAULT_API_KEY = "lm-studio"
DEFAULT_MODEL = ""   # no model until the person chooses one (the setup page asks)
DEFAULT_TEMPERATURE = 0.85
DEFAULT_MAX_TOKENS = 8192
LOGGER = logging.getLogger(__name__)

# Retry settings
MAX_RETRIES = 3
RETRY_DELAY_S = 2

# ask(): extra tokens on top of the answer's length, so a reasoning model can think before it answers (the picture age check
# allows 4096 for the same reason)
ANSWER_ROOM = 2048
_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)


# Global provider registry (initialized from config)
_provider_registry: ProviderRegistry | None = None
_registry_lock = threading.Lock()


class StreamComplete(str):
    """Final stream aggregate, distinct from a text delta even when their contents match.

    It stays a string for callers that use the aggregate as text. Consumers that
    assemble deltas must recognize this marker rather than compare string values.
    """


def get_provider_registry_instance(config: dict | None = None) -> ProviderRegistry:
    """Get or create the global ProviderRegistry instance."""
    global _provider_registry
    if _provider_registry is None:
        with _registry_lock:
            if _provider_registry is None:
                if config is None:
                    from core.config import get_config
                    config = get_config()
                _provider_registry = get_provider_registry(config)
    elif config is not None:
        # Refresh from new config
        _provider_registry = get_provider_registry(config)
    return _provider_registry


def refresh_provider_registry() -> ProviderRegistry:
    """Force refresh the provider registry from disk (config.yaml)."""
    global _provider_registry
    from core.config import reload_config

    with _registry_lock:
        _provider_registry = get_provider_registry(reload_config())
    return _provider_registry


def reset_provider_registry():
    """Reset the global provider registry."""
    global _provider_registry
    with _registry_lock:
        _provider_registry = None


# ProviderRegistry, ProviderConfig, get_provider_registry, and reset_provider_registry
# are imported from core.providers (canonical implementation)


def _sampling_from(config: dict) -> dict:
    """The optional sampler settings that are set in config.yaml (llm.sampling)."""
    from core.sampling import app_settings
    return app_settings(config)["sampling"]


class LLMClient:
    """Wraps OpenAI-compatible calls with retry logic and shared configuration.

    Every core module uses this instead of creating its own OpenAI client inline.
    Swap LM Studio for Ollama or another backend in one place.
    Supports dynamic model/provider switching via ProviderRegistry.
    """

    _instance = None
    _lock = threading.Lock()

    def __init__(self, base_url: str = DEFAULT_BASE_URL, api_key: str = DEFAULT_API_KEY,
                 model: str = DEFAULT_MODEL, temperature: float = DEFAULT_TEMPERATURE,
                 max_tokens: int = DEFAULT_MAX_TOKENS, provider_key: str = 'custom', sampling: dict | None = None):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.provider_key = provider_key
        # The optional sampler settings (core/sampling.py), sent only when set. Chat replies only: get_client() and ask()
        # leave them out, so the background helpers keep their own fixed settings.
        self.sampling = dict(sampling or {})

    def for_person(self, overrides: dict) -> 'LLMClient':
        """A copy for one reply with this person's own temperature and reply length (core/sampling.py). No overrides: self."""
        if not overrides:
            return self
        return LLMClient(base_url=self.base_url, api_key=self.api_key, model=self.model,
                         temperature=overrides.get('temperature', self.temperature),
                         max_tokens=overrides.get('max_tokens', self.max_tokens),
                         provider_key=self.provider_key, sampling=self.sampling)

    def _sampling_options(self, kwargs: dict) -> dict:
        """kwargs with the optional sampler settings that are set (a server that does not know one usually ignores it)."""
        if not self.sampling:
            return kwargs
        from core.sampling import request_options
        extra_kwargs, extra_body = request_options(self.provider_key, self.sampling)
        kwargs.update(extra_kwargs)
        if extra_body:
            kwargs["extra_body"] = extra_body
        return kwargs

    @classmethod
    def from_config(cls, config: dict) -> 'LLMClient':
        """Create LLMClient from altera.py's CONFIG dict.

        Supports both old flat config and new nested provider config.
        """
        llm_cfg = config.get('llm', {})

        # Check if new nested provider config exists
        if 'providers' in llm_cfg:
            registry = get_provider_registry_instance(config)
            active_provider = registry.get_active_provider()
            if active_provider:
                return cls(
                    base_url=active_provider.base_url,
                    api_key=active_provider.api_key,
                    model=registry.get_active_model() or active_provider.default_model,
                    temperature=llm_cfg.get('temperature', DEFAULT_TEMPERATURE),
                    max_tokens=llm_cfg.get('max_tokens', DEFAULT_MAX_TOKENS),
                    provider_key=registry._active_provider_key,
                    sampling=_sampling_from(config),
                )

        # Fallback to old flat config
        return cls(
            base_url=llm_cfg.get('base_url', DEFAULT_BASE_URL),
            api_key=llm_cfg.get('api_key', DEFAULT_API_KEY),
            model=llm_cfg.get('model', DEFAULT_MODEL),
            temperature=llm_cfg.get('temperature', DEFAULT_TEMPERATURE),
            max_tokens=llm_cfg.get('max_tokens', DEFAULT_MAX_TOKENS),
            provider_key=llm_cfg.get('provider', 'custom'),
            sampling=_sampling_from(config),
        )

    @classmethod
    def get_instance(cls, config: dict | None = None) -> 'LLMClient':
        """Singleton accessor — creates once from config, returns same instance."""
        if cls._instance is None and config is not None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls.from_config(config)
        return cls._instance

    @classmethod
    def refresh_from_config(cls, config: dict) -> 'LLMClient':
        """Force refresh the singleton instance from new config (for runtime switching)."""
        with cls._lock:
            cls._instance = cls.from_config(config)
        return cls._instance

    def update_config(self, base_url: str = None, api_key: str = None, model: str = None,
                      temperature: float = None, max_tokens: int = None) -> None:
        """Update client configuration at runtime."""
        if base_url is not None:
            self.base_url = base_url
        if api_key is not None:
            self.api_key = api_key
        if model is not None:
            self.model = model
        if temperature is not None:
            self.temperature = temperature
        if max_tokens is not None:
            self.max_tokens = max_tokens

    def chat(self, messages: list[dict], stream: bool = False, tools=None, tool_executor=None):
        """Send a chat completion request.

        Args:
            messages: List of {role, content} dicts including system prompt.
            stream: If True, returns generator of text chunks. If False, returns full string.
            tools: Optional list of tool definitions for function calling.
            tool_executor: Optional callable receiving (tool_name, arguments) and returning JSON text.

        Returns:
            Full response string (non-stream) or generator of chunk strings (stream).
        """
        from openai import OpenAI
        from core.turns import CURRENT_TURN
        turn = CURRENT_TURN.get()
        if turn:
            turn.check_cancelled()

        # A local server ignores the key, but the OpenAI client refuses to be constructed without one — and a key
        # can legitimately be blank here (supplied through the environment instead, or simply not needed).
        def create_client():
            transport = None
            try:
                kwargs = {'http_client': transport, 'max_retries': 0} if transport is not None else {}
                client = OpenAI(base_url=self.base_url, api_key=self.api_key or "not-needed", **kwargs)
            except BaseException:
                if transport is not None:
                    transport.close()
                raise
            if turn:
                turn.upstream = client
            return client, transport

        def release(client, transport):
            try:
                close_owned(client)
            finally:
                try:
                    if transport is not None:
                        close_owned(transport)
                finally:
                    if turn and turn.upstream is client:
                        turn.upstream = None

        if stream:
            # Tools used to switch streaming off: the "stream" was a generator that yielded the whole blocking
            # response in one piece, so the reader watched a typing indicator for the entire generation and then got
            # the reply all at once — and the sentence-by-sentence speech in core/voice_layer.py had nothing to work
            # with. _chat_stream runs the tool rounds itself and keeps streaming through them.
            def managed():
                if turn:
                    turn.check_cancelled()
                with ExitStack() as stack:
                    suite_gpu.protect_model(self, stack)
                    client, transport = create_client()
                    stack.callback(release, client, transport)
                    if turn:
                        turn.check_cancelled()
                    yield from self._chat_stream(client, messages, tools=tools, tool_executor=tool_executor)
            return managed()
        with ExitStack() as stack:
            suite_gpu.protect_model(self, stack)
            client, transport = create_client()
            stack.callback(release, client, transport)
            return self._chat_with_retry(client, messages, tools=tools, tool_executor=tool_executor)

    def _chat_with_retry(self, client, messages: list[dict], tools=None, tool_executor=None) -> str:
        """Chat with retry logic for transient failures."""
        last_error = None
        for attempt in range(MAX_RETRIES):
            from core.turns import CURRENT_TURN
            turn = CURRENT_TURN.get()
            if turn:
                turn.check_cancelled()
            try:
                return self._chat_once_or_with_tools(client, messages, tools=tools, tool_executor=tool_executor)
            except Exception as e:
                last_error = e
                if attempt < MAX_RETRIES - 1:
                    if turn:
                        turn.cancel.wait(RETRY_DELAY_S * (attempt + 1))
                    else:
                        time.sleep(RETRY_DELAY_S * (attempt + 1))
        raise RuntimeError(f"LLM call failed after {MAX_RETRIES} attempts: {last_error}")

    def _chat_once_or_with_tools(self, client, messages: list[dict], tools=None, tool_executor=None) -> str:
        """Run a chat completion, executing tool calls and continuing the model turn."""
        working_messages = list(messages)
        max_tool_rounds = 3

        for _ in range(max_tool_rounds + 1):
            kwargs = {
                "model": self.model,
                "messages": working_messages,
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
            }
            if tools is not None:
                kwargs["tools"] = tools
            self._sampling_options(kwargs)

            from core.diagnostics import begin_call, record_usage
            measurement = begin_call(self, working_messages, tools)
            response = client.chat.completions.create(**kwargs)
            record_usage(measurement, getattr(response, 'usage', None))
            message = response.choices[0].message
            tool_calls = getattr(message, "tool_calls", None) or []

            if tool_calls and tool_executor is not None:
                working_messages.append(self._assistant_tool_message(message, tool_calls))
                for tool_call in tool_calls:
                    name = getattr(tool_call.function, "name", "")
                    raw_arguments = getattr(tool_call.function, "arguments", "{}") or "{}"
                    try:
                        arguments = json.loads(raw_arguments)
                    except json.JSONDecodeError:
                        arguments = {"raw_arguments": raw_arguments}
                    try:
                        tool_output = tool_executor(name, arguments)
                    except Exception as exc:
                        LOGGER.exception("Tool execution failed for %s", name)
                        tool_output = json.dumps({"error": f"Tool execution failed: {type(exc).__name__}"})
                    working_messages.append({
                        "role": "tool",
                        "tool_call_id": getattr(tool_call, "id", ""),
                        "content": tool_output,
                    })
                continue

            content = getattr(message, "content", None)
            # Reasoning is not the model's visible answer. Match the streaming
            # path and let callers handle an absent final response explicitly.
            return content or ""

        return "I searched, but I could not finish composing a reliable answer from the tool results."

    @staticmethod
    def _assistant_tool_message(message, tool_calls) -> dict:
        """Convert an SDK message with tool calls into OpenAI-compatible dict form."""
        return {
            "role": "assistant",
            "content": getattr(message, "content", None),
            "tool_calls": [
                {
                    "id": getattr(tool_call, "id", ""),
                    "type": getattr(tool_call, "type", "function"),
                    "function": {
                        "name": getattr(tool_call.function, "name", ""),
                        "arguments": getattr(tool_call.function, "arguments", "{}") or "{}",
                    },
                }
                for tool_call in tool_calls
            ],
        }

    @staticmethod
    def _collect_tool_call_delta(pending: dict, delta) -> None:
        """Fold one streamed tool-call fragment into `pending`.

        A tool call arrives across many chunks: the id and name usually in the first, the JSON arguments a few
        characters at a time after that, each fragment tagged with the index of the call it belongs to.
        """
        for fragment in (getattr(delta, "tool_calls", None) or []):
            index = getattr(fragment, "index", 0) or 0
            slot = pending.setdefault(index, {"id": "", "name": "", "arguments": ""})
            if getattr(fragment, "id", None):
                slot["id"] = fragment.id
            function = getattr(fragment, "function", None)
            if function is not None:
                if getattr(function, "name", None):
                    slot["name"] += function.name
                if getattr(function, "arguments", None):
                    slot["arguments"] += function.arguments

    def _chat_stream(self, client, messages: list[dict], tools=None, tool_executor=None, max_tool_rounds: int = 3):
        """Stream a chat completion, running any tool calls the model asks for without stopping streaming.

        Yields each piece of text as it arrives, then a StreamComplete containing the
        whole reply. The typed marker cannot be confused with a repeated text delta.

        When the model asks for a tool it says nothing first, so the round produces no text: the tool runs, its
        result is appended, and the next round streams the answer the reader actually sees.
        """
        LOGGER.debug("Starting stream: model=%s max_tokens=%s tools=%s", self.model, self.max_tokens, bool(tools))
        working_messages = list(messages)
        full_response = ""

        for round_number in range(max_tool_rounds + 1):
            kwargs = {
                "model": self.model,
                "messages": working_messages,
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
                "stream": True,
            }
            if tools is not None:
                kwargs["tools"] = tools
            self._sampling_options(kwargs)
            # Local/custom servers retain their previous request shape. OpenRouter sends usage automatically.
            if urlsplit(self.base_url).hostname == 'api.openai.com':
                kwargs['stream_options'] = {'include_usage': True}
            from core.diagnostics import begin_call, record_usage
            measurement = begin_call(self, working_messages, tools)
            response = client.chat.completions.create(**kwargs)

            from core.turns import CURRENT_TURN
            turn = CURRENT_TURN.get()
            if turn:
                turn.upstream = response
            try:
                with owned_iterator(response) as chunks:
                    if turn:
                        turn.check_cancelled()
                    pending_tool_calls = {}
                    chunk_count = 0
                    for chunk in chunks:
                        chunk_count += 1
                        record_usage(measurement, getattr(chunk, 'usage', None))
                        if not getattr(chunk, "choices", None):
                            continue
                        choice = chunk.choices[0]
                        delta = getattr(choice, "delta", None)
                        if delta is None:
                            continue
                        content = getattr(delta, "content", None)
                        if content:
                            full_response += content
                            yield content
                        self._collect_tool_call_delta(pending_tool_calls, delta)

            finally:
                if turn and turn.upstream is response:
                    turn.upstream = None
            if turn:
                turn.check_cancelled()

            LOGGER.debug("Stream round %s: chunks=%s chars=%s tool_calls=%s",
                         round_number, chunk_count, len(full_response), len(pending_tool_calls))

            if not pending_tool_calls or tool_executor is None:
                break
            if round_number == max_tool_rounds:
                LOGGER.info("Stopping after %s tool rounds", max_tool_rounds)
                break

            calls = [pending_tool_calls[index] for index in sorted(pending_tool_calls)]
            working_messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": call["id"] or f"call_{position}", "type": "function",
                     "function": {"name": call["name"], "arguments": call["arguments"] or "{}"}}
                    for position, call in enumerate(calls)
                ],
            })
            for position, call in enumerate(calls):
                try:
                    arguments = json.loads(call["arguments"] or "{}")
                except json.JSONDecodeError:
                    arguments = {"raw_arguments": call["arguments"]}
                try:
                    output = tool_executor(call["name"], arguments)
                except Exception as exc:
                    LOGGER.exception("Tool execution failed for %s", call["name"])
                    output = json.dumps({"error": f"Tool execution failed: {type(exc).__name__}"})
                working_messages.append({
                    "role": "tool",
                    "tool_call_id": call["id"] or f"call_{position}",
                    "content": output,
                })

        LOGGER.debug("Stream done: chars=%s", len(full_response))

        # Yield the complete response as a final chunk so callers can use it
        yield StreamComplete(full_response)


def get_client() -> LLMClient:
    """Create a fresh LLMClient for a background helper (the proactive check-ins and the like).

    It talks to the model that is answering chat and never asks for another one. LM Studio loads whatever model a request
    names, so a helper that asked for a fixed default model made it swap the chat model out, and the picture age
    check that was waiting on the chat model failed. Before the first chat, the saved configuration chooses the model.
    For shared/singleton usage, prefer LLMClient.get_instance(config).
    """
    chosen = LLMClient._instance
    if chosen is None and _provider_registry is not None:
        provider = _provider_registry.get_active_provider()
        if provider:
            chosen = LLMClient(base_url=provider.base_url, api_key=provider.api_key,
                               model=_provider_registry.get_active_model() or provider.default_model,
                               provider_key=getattr(_provider_registry, '_active_provider_key', 'custom'))
    if chosen is None:
        from core.config import get_config
        chosen = LLMClient.from_config(get_config())
    return LLMClient(
        base_url=chosen.base_url if chosen else DEFAULT_BASE_URL,
        api_key=chosen.api_key if chosen else DEFAULT_API_KEY,
        model=chosen.model if chosen else DEFAULT_MODEL,
        temperature=DEFAULT_TEMPERATURE,
        max_tokens=DEFAULT_MAX_TOKENS,
        provider_key=chosen.provider_key,
    )


def ask(system: str, prompt: str, temperature: float | None = None, max_tokens: int | None = None) -> str:
    """One background question to the model that is answering chat; returns its reply as text.

    For the helpers that read a message and answer with JSON (jokes, opinions, events, relationships). It goes through
    get_client(), so it never names another model. `max_tokens` is the length of the ANSWER: a reasoning model gets ANSWER_ROOM
    more so it can think first. Reasoning text (<think>...</think>) is removed from the reply. Raises RuntimeError when the model
    cannot be reached (after the usual retries): callers catch it and use their fallback.
    """
    client = get_client()
    if temperature is not None:
        client.temperature = temperature
    if max_tokens is not None:
        client.max_tokens = max_tokens + ANSWER_ROOM
    reply = client.chat([{"role": "system", "content": system}, {"role": "user", "content": prompt}])
    return _THINK.sub("", reply or "").strip()


def unload_models(timeout: float = 20.0) -> bool:
    """Best-effort local LM Studio and Ollama cleanup before an image request."""
    results = suite_gpu.unload_outside(timeout)
    for result in results:
        if result["problem"]:
            LOGGER.warning("%s", result["problem"])
    return any(result["done"] for result in results)


def reset_llm_instance():
    """Reset singleton — useful for testing or config reload."""
    with LLMClient._lock:
        LLMClient._instance = None
