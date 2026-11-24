"""Coordinate Altera's use of the graphics card with the other studio apps."""
from contextlib import closing
from contextvars import ContextVar
import http.client
import json
import threading
import time
from urllib.parse import urlsplit

from core import fels_suite


FORGE_WAIT = ("NPC Forge is making pictures (One model at a time is on in NPC Forge). "
              "Your reply starts when it is done.")
WAIT_SECONDS = 300
VOICE = "Kokoro voice"
_lock = fels_suite.GpuLock("altera")
_mutex = threading.Lock()
_users = {}
_unloading_outside = False
_reply_scope = ContextVar("altera_gpu_reply", default=False)


class GpuWaitError(RuntimeError):
    """A local model could not get a turn on the GPU."""


def try_use(what):
    """Take shared ownership and return its release callback, or None if Forge has priority."""
    with _mutex:
        if _unloading_outside or _lock.alone_wanted() or not _lock.acquire(next(iter(_users.values()), what), timeout=0):
            return None
        if not _users and _lock.alone_wanted():
            _lock.release()
            return None
        key = object()
        _users[key] = what

    def release():
        with _mutex:
            if _users.pop(key, None) is not None:
                if _users:
                    _lock.acquire(next(iter(_users.values())), timeout=0)
                else:
                    _lock.release()

    return release


def chat_in_use():
    with _mutex:
        return any(what != VOICE for what in _users.values())


def local_provider(client):
    try:
        url = urlsplit(getattr(client, "base_url", ""))
        if url.hostname not in {"127.0.0.1", "localhost", "::1"}:
            return None
        key = getattr(client, "provider_key", "custom")
        if key == "lmstudio" or (key == "custom" and url.port == fels_suite.PORTS["lm-studio"]):
            return "LM Studio"
        if key == "ollama" or (key == "custom" and url.port == fels_suite.PORTS["ollama"]):
            return "Ollama"
    except (TypeError, ValueError):
        return None
    return None


def wait_for_reply(client, turn, stack):
    """Yield one wait event and a resume event; release with the caller's model scope."""
    provider = local_provider(client)
    if not provider:
        return
    cancel = turn.cancel if turn else threading.Event()
    deadline = time.monotonic() + WAIT_SECONDS
    waited = False
    while not cancel.is_set():
        release = try_use(f"{provider} chat")
        if release:
            stack.callback(release)
            stack.callback(_reply_scope.reset, _reply_scope.set(True))
            if cancel.is_set():
                break
            if waited:
                yield {"status": "typing"}
            return
        if not waited:
            holder = fels_suite.alone_wanted() or next(iter(fels_suite.gpu_holders()), None)
            message = (FORGE_WAIT if holder and holder["app"] == "npc-forge" else
                       "The GPU is busy. Your reply starts when it is available.")
            yield {"status": "gpu_wait", "message": message}
            waited = True
        if time.monotonic() >= deadline:
            raise GpuWaitError("The GPU is still busy. Send your message again when it is available.")
        cancel.wait(0.2)
    raise InterruptedError("Reply stopped")


def protect_model(client, stack):
    """Background inference must also leave Forge's turn undisturbed."""
    if _reply_scope.get():
        return
    provider = local_provider(client)
    if provider:
        release = try_use(f"{provider} model")
        if release is None:
            raise GpuWaitError("The GPU is busy. Try again when it is available.")
        stack.callback(release)


def unload_outside(timeout):
    # An existing reply (or Forge's request for exclusive use) takes precedence over a legacy image request.
    global _unloading_outside
    with _mutex:
        if _unloading_outside or any(what != VOICE for what in _users.values()) or fels_suite.alone_wanted():
            return []
        _unloading_outside = True
    try:
        return fels_suite.unload_outside(ask_json=_ask_local_model, timeout=timeout)
    finally:
        with _mutex:
            _unloading_outside = False


def _ask_local_model(method, url, body=None):
    """Use a bounded, direct connection to the helper's loopback ports."""
    target = urlsplit(url)
    if target.scheme != "http" or target.hostname != "127.0.0.1" or target.port not in {1234, 11434}:
        return None
    try:
        with closing(http.client.HTTPConnection(target.hostname, target.port, timeout=3)) as connection:
            connection.request(method, target.path, json.dumps(body) if body is not None else None,
                               {"Content-Type": "application/json"})
            with connection.getresponse() as response:
                if not 200 <= response.status < 300:
                    return None
                content = response.read(1024 * 1024 + 1)
    except (OSError, http.client.HTTPException):
        return None
    if len(content) > 1024 * 1024:
        return None
    try:
        data = json.loads(content)
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}
