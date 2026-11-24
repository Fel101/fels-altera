"""The lock screen, the parent PIN, and the kids view's server-side gate.

The page and the API are always reachable, even when the app is locked: a browser needs somewhere to enter the PIN
or choose Kids. Everything else is refused for a browser in the kids view, so a typed URL cannot reach an adult
person, the settings, Memory, Search or adult mode (core/lock.py holds the PIN and the browser's state).
"""
import logging

from flask import Blueprint, jsonify, redirect, render_template, request, url_for

from core import kids, lock

bp = Blueprint("lock", __name__)
LOGGER = logging.getLogger(__name__)

# A kids view is the chat with kids pals and nothing else. These are the only application endpoints it may reach;
# every one is either harmless or checked against a kids pal below.
KIDS_ALLOWED = {
    "companions.list_companions", "companions.current_companion", "companions.companion_status",
    "companions.open_companion",
    "companion.list_avatars", "companion.avatar_portrait", "companion.avatar_living",
    "companion.avatar_living_frame", "companion.get_living_setting",
    "core_api.get_messages", "core_api.get_message_item", "core_api.chat", "core_api.chat_stream",
    "core_api.regenerate_chat", "core_api.chat_request_status", "core_api.stop_chat",
    "core_api.message_versions", "core_api.show_message_version",
    "core_api.onboarding_status",
    "admin.offline_status", "engagement.get_voice_status", "engagement.text_to_speech",
}

# Endpoints that look like an object but must be checked against a kids pal before they run.
_KIDS_OBJECT_CHECKS = {
    "companions.open_companion", "companions.companion_status", "companion.avatar_portrait",
    "companion.avatar_living", "companion.avatar_living_frame", "core_api.get_messages",
    "core_api.get_message_item", "core_api.chat", "core_api.chat_stream", "core_api.regenerate_chat",
    "core_api.chat_request_status", "core_api.stop_chat", "core_api.message_versions", "core_api.show_message_version",
}


def _no_store(payload, status=200):
    return jsonify(payload), status, {"Cache-Control": "no-store"}


def _error(message, status, code):
    return _no_store({"error": message, "code": code}, status)


def _cross_site() -> bool:
    from urllib.parse import urlparse

    if request.headers.get("Sec-Fetch-Site") == "cross-site":
        return True
    origin = request.headers.get("Origin")
    return bool(origin) and urlparse(origin).netloc != request.host


def _body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _pin_from(data) -> str:
    pin = data.get("pin")
    return pin if isinstance(pin, str) else ""


# ---- the page and the API -------------------------------------------------------------------------------------------
@bp.get("/lock")
def lock_page():
    """The lock screen: the PIN (the whole app) or Kids (only kids pals)."""
    return render_template("lock.html", **lock.state())


@bp.get("/api/lock")
def lock_status():
    return _no_store(lock.state())


@bp.post("/api/lock/unlock")
def unlock():
    """Enter the whole app with the PIN."""
    if _cross_site():
        return _error("Cross-site requests are not allowed.", 403, "cross_site_request")
    try:
        lock.verify_pin(_pin_from(_body()))
    except lock.LockError as error:
        return _error(str(error), error.status, error.code)
    lock.unlock()
    return _no_store({**lock.state(), "ok": True})


@bp.post("/api/lock/kids")
def enter_kids():
    """Enter the kids view. No PIN is needed to go in; a grown-up set the PIN when kids mode was set up.

    Opening a kids pal here also makes it the active one, so the chat page opens on a kids pal rather than whoever
    the grown-up was talking to.
    """
    if _cross_site():
        return _error("Cross-site requests are not allowed.", 403, "cross_site_request")
    if not kids.enabled():
        return _error("Kids mode is not set up on this Altera.", 409, "kids_inactive")
    if not lock.has_pin():
        return _error("A grown-up needs to set a PIN before the kids view can be used.", 409, "no_pin")
    pals = lock.kids_pals()
    if not pals:
        return _error("No pal is in kids mode yet. A grown-up can switch one on in its settings.", 409, "no_kids_pals")
    from core import companions

    wanted = _body().get("avatar_id")
    chosen = next((p for p in pals if p["id"] == wanted), None) if wanted else pals[0]
    if chosen is None:
        return _error("That pal is not in kids mode.", 403, "kids_view")
    lock.enter_kids_view()
    try:
        opened = lock.open_kids_pal(chosen["id"])
    except companions.CompanionError as error:
        lock.lock_browser()
        return _error(str(error), error.status, "kids_view")
    return _no_store({**lock.state(), "ok": True, "avatar": opened})


@bp.post("/api/lock/leave")
def leave_kids():
    """Leave the kids view. A grown-up proves who they are with the PIN; that also unlocks this browser."""
    if _cross_site():
        return _error("Cross-site requests are not allowed.", 403, "cross_site_request")
    try:
        lock.verify_pin(_pin_from(_body()))
    except lock.LockError as error:
        return _error(str(error), error.status, error.code)
    lock.leave_kids_view()
    lock.unlock()
    return _no_store({**lock.state(), "ok": True})


@bp.post("/api/lock/lock")
def lock_this_browser():
    """Lock this browser again. Anyone holding the tablet can lock it; only the PIN or Kids opens it."""
    if _cross_site():
        return _error("Cross-site requests are not allowed.", 403, "cross_site_request")
    lock.lock_browser()
    return _no_store({**lock.state(), "ok": True})


@bp.post("/api/lock/pin")
def change_pin():
    """Set or change the parent PIN. Changing one needs the current PIN."""
    if _cross_site():
        return _error("Cross-site requests are not allowed.", 403, "cross_site_request")
    if lock.in_kids_view():
        return _error("Leave the kids view before changing the PIN.", 409, "kids_view")
    data = _body()
    new = data.get("new")
    try:
        lock.set_pin(new, current=data.get("current"))
    except lock.LockError as error:
        return _error(str(error), error.status, error.code)
    lock.unlock()                           # the grown-up who set or changed it keeps this browser open
    return _no_store({**lock.state(), "ok": True})


# ---- the gate -------------------------------------------------------------------------------------------------------
def _kids_api_allowed(endpoint) -> bool:
    if endpoint not in KIDS_ALLOWED:
        return False
    if endpoint not in _KIDS_OBJECT_CHECKS:
        return True
    args = request.view_args or {}
    if endpoint == "companions.companion_status":
        return lock.avatar_is_kids(args.get("avatar_id"))
    if endpoint in {"companions.open_companion"}:
        return lock.avatar_is_kids(args.get("avatar_id"))
    if endpoint in {"companion.avatar_portrait", "companion.avatar_living", "companion.avatar_living_frame"}:
        return lock.avatar_is_kids(args.get("avatar_id"))
    if endpoint == "core_api.get_messages":
        return lock.session_is_kids(args.get("session_id"))
    if endpoint in {"core_api.get_message_item", "core_api.message_versions", "core_api.show_message_version"}:
        return lock.message_is_kids(args.get("message_id"))
    if endpoint in {"core_api.chat_request_status", "core_api.stop_chat"}:
        return lock.request_is_kids(args.get("request_id"))
    if endpoint in {"core_api.chat", "core_api.chat_stream"}:
        data = _body()
        if data.get("voice_note_id"):
            return False
        # Always a kids chat named by id: a chat with no session would start a new conversation that belongs to nobody,
        # and nobody falls back to the household's current person, who may be an adult.
        sid = data.get("session_id")
        return sid is not None and lock.session_is_kids(sid)
    if endpoint == "core_api.regenerate_chat":
        sid = _body().get("session_id")
        return sid is not None and lock.session_is_kids(sid)
    return False


def install_lock_guard(app):
    """Refuse the rest of the app for a locked browser or a browser in the kids view.

    App-level so it runs before any blueprint: a typed URL is refused, not merely missing from a page.
    """
    @app.before_request
    def enforce_lock():
        endpoint = request.endpoint or ""
        if endpoint in {"static", "pages.service_worker", "system.suite_health", "system.suite_unload"} or endpoint.startswith("lock."):
            return None
        if lock.in_kids_view():
            if endpoint == "pages.index":
                return None
            if endpoint.startswith("pages."):
                return redirect(url_for("pages.index"))
            if _kids_api_allowed(endpoint):
                return None
            return _error("Kids mode only shows kids pals. A grown-up can leave it with the PIN.", 403, "kids_view")
        if lock.is_locked():
            if endpoint.startswith("pages."):
                return redirect(url_for("lock.lock_page"))
            return _error("Altera is locked. Enter the PIN or choose Kids.", 423, "locked")
        return None
