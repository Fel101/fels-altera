"""Companion routes: who you can talk to, opening a chat, her notes, meeting someone new through NPC Forge.

A companion is an avatar with a chat, notes and memories of her own (core/companions.py). Every route that changes something
refuses requests that come from another website, like the avatar routes do.
"""

import logging
from urllib.parse import urlparse

from flask import Blueprint, jsonify, request

from core import adult, avatars, companion, companions, forge, notes
from core.constants import sanitized_error_response
from core.datastore import get_datastore

bp = Blueprint("companions", __name__)
LOGGER = logging.getLogger(__name__)

MAX_NOTE_CHARS = 20000


def _cross_site() -> bool:
    if request.headers.get("Sec-Fetch-Site") == "cross-site":
        return True
    origin = request.headers.get("Origin")
    return bool(origin) and urlparse(origin).netloc != request.host


def _forbidden():
    return jsonify({"error": "Cross-site requests are not allowed."}), 403


def _forge() -> forge.ForgeClient:
    return forge.ForgeClient(companion.get_services().config)


def _session_of(avatar_id):
    return get_datastore().get_avatar_session_id(avatar_id)


# ============================================================
# Who you can talk to
# ============================================================

@bp.route("/api/companions", methods=["GET"])
def list_companions():
    s = companion.get_services()
    rows = companions.list_companions()
    active = s.avatars.active_id()
    if _kids_view():
        from core import lock
        rows = [row for row in rows if row.get("kids") is True]
        active = (lock.ensure_kids_active().get("avatar") or {}).get("id") if rows else None
    return jsonify({"companions": rows, "active": active,
                    "forge_library": s.avatars.forge_available()})


@bp.route("/api/companions/current", methods=["GET"])
def current_companion():
    try:
        companions.migrate_legacy()
    except Exception:
        LOGGER.exception("Could not bring the old chats over to a companion")
    from core import lock
    if lock.in_kids_view():
        return jsonify(lock.ensure_kids_active())
    return jsonify(companions.current())


@bp.route("/api/people", methods=["GET"])
def list_people():
    """The start screen's data: everyone, counts per kind (pal, character, companion) and who to continue with."""
    try:
        companions.migrate_legacy()
        companions.migrate_kinds()
    except Exception:
        LOGGER.exception("Could not bring older people up to date")
    try:
        data = companions.people(request.args.get("kind") or None)
    except companions.CompanionError as e:
        return jsonify({"error": str(e)}), e.status
    _recheck_portraits()
    return jsonify({**data, "forge_library": companion.get_services().avatars.forge_available()}), 200, {"Cache-Control": "no-store"}


def _replies_payload(avatar_id):
    from core import sampling
    from core.config import get_config
    app = sampling.app_settings(get_config())
    return {"own": sampling.person_settings(companion.get_services().avatars, avatar_id),
            "app": {"temperature": app["temperature"], "max_tokens": app["max_tokens"]},
            "limits": {key: sampling.limits()[key] for key in sampling.PERSON_KEYS}}


@bp.route("/api/people/<avatar_id>/replies", methods=["GET"])
def person_replies(avatar_id):
    """This person's own temperature and reply length (null: the app-wide one), and the app-wide values."""
    if not companion.get_services().avatars.get(avatar_id):
        return jsonify({"error": "That person was not found."}), 404, {"Cache-Control": "no-store"}
    return jsonify(_replies_payload(avatar_id)), 200, {"Cache-Control": "no-store"}


@bp.route("/api/people/<avatar_id>/replies", methods=["PUT"])
def update_person_replies(avatar_id):
    """{temperature?, max_tokens?}: a number, or null for the app-wide value again."""
    if _cross_site():
        return _forbidden()
    from core import sampling
    try:
        sampling.set_person(companion.get_services().avatars, avatar_id, request.get_json(silent=True))
    except sampling.SamplingError as error:
        return jsonify({"error": str(error)}), error.status, {"Cache-Control": "no-store"}
    return jsonify(_replies_payload(avatar_id)), 200, {"Cache-Control": "no-store"}


def _recheck_portraits(avatar_id=None) -> None:
    """Age-check, in the background, pal and character portraits that came in while the check could not run (core/portraits.py)."""
    try:
        from core import portraits
        s = companion.get_services()
        portraits.recheck_soon(s.avatars, s.checker, s.living, None if avatar_id is None else [avatar_id])
    except Exception:
        LOGGER.exception("Could not start the later age check of portraits")


# ============================================================
# Companions written in Altera, without NPC Forge (core/written.py)
# ============================================================

WRITTEN_FIELDS = {"name", "age", "gender", "bio", "hobbies", "speech", "demeanor", "quirk", "goal", "peeve", "voice"}


@bp.route("/api/companions", methods=["POST"])
def write_companion():
    """{name, age, gender?, bio?, hobbies?, speech?, demeanor?, quirk?, goal?, peeve?, voice?} -> the new companion, opened.
    The age must be 18 or older, and the text passes the same safety rules as an import."""
    if _cross_site():
        return _forbidden()
    from core import written
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict) or not set(data) <= WRITTEN_FIELDS:
        return jsonify({"error": "Send name, age and the card fields only."}), 400
    try:
        made = written.create(companion.get_services().avatars, **data)
        return jsonify(companions.open_companion(made["id"])), 201
    except written.WrittenError as error:
        return jsonify({"error": str(error)}), error.status
    except companions.CompanionError as error:
        return jsonify({"error": str(error)}), error.status


@bp.route("/api/companions/<avatar_id>", methods=["GET"])
def written_companion_settings(avatar_id):
    """A written companion's card fields ({written: true, ...}). A companion from NPC Forge is changed in NPC Forge, so it
    gets only {written: false, id, name, voice, features}: the settings window then offers what Altera keeps itself."""
    from core import written
    from core.avatars import features_of, kind_of
    store = companion.get_services().avatars
    try:
        return jsonify({**written.settings(store, avatar_id), "written": True}), 200, {"Cache-Control": "no-store"}
    except written.WrittenError as error:
        record = store.get(avatar_id) if avatars.valid_id(avatar_id) else None
        if record and kind_of(record) == "companion":
            return (jsonify({"id": avatar_id, "name": record.get("name") or "", "voice": record.get("voice") or "",
                             "features": features_of(record), "written": False}), 200, {"Cache-Control": "no-store"})
        return jsonify({"error": str(error)}), error.status, {"Cache-Control": "no-store"}


@bp.route("/api/companions/<avatar_id>", methods=["PUT"])
def update_written_companion(avatar_id):
    """Change a written companion's card: {name?, age?, gender?, bio?, hobbies?, speech?, demeanor?, quirk?, goal?, peeve?,
    voice?} -> its settings. Only the fields sent change."""
    if _cross_site():
        return _forbidden()
    from core import written
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict) or not set(data) <= WRITTEN_FIELDS:
        return jsonify({"error": "Send name, age and the card fields only."}), 400
    try:
        return (jsonify(written.update(companion.get_services().avatars, avatar_id, **data)), 200,
                {"Cache-Control": "no-store"})
    except written.WrittenError as error:
        return jsonify({"error": str(error)}), error.status, {"Cache-Control": "no-store"}


# ============================================================
# Pals: light personas made from a name, a vibe and a voice (core/pals.py)
# ============================================================

def _pal_error(error):
    return jsonify({"error": str(error)}), error.status, {"Cache-Control": "no-store"}


def _kids_view() -> bool:
    from core import lock
    return lock.in_kids_view()


def _lock_error(error):
    return jsonify({"error": str(error), "code": error.code}), error.status, {"Cache-Control": "no-store"}


@bp.route("/api/pals/options", methods=["GET"])
def pal_options():
    """The vibes (content/pals/) and the voices for the speech engine chosen in Settings."""
    from core import pals
    shown = [{k: v[k] for k in ("id", "label", "description")} for v in pals.vibes()]
    return jsonify({"vibes": shown, "voices": pals.voices()}), 200, {"Cache-Control": "no-store"}


@bp.route("/api/pals", methods=["POST"])
def make_pal():
    """{name, vibe, voice?, kids?} -> the new pal, opened (so the chat at / shows it)."""
    if _cross_site():
        return _forbidden()
    from core import pals
    data = request.get_json(silent=True) or {}
    try:
        made = pals.create(companion.get_services().avatars, data.get("name"), data.get("vibe"), data.get("voice") or "",
                           kids=data.get("kids", False))
        return jsonify(companions.open_companion(made["id"])), 201
    except pals.PalError as error:
        return _pal_error(error)
    except companions.CompanionError as error:
        return jsonify({"error": str(error)}), error.status


@bp.route("/api/pals/<avatar_id>", methods=["GET"])
def pal_settings(avatar_id):
    from core import pals
    try:
        return jsonify(pals.settings(companion.get_services().avatars, avatar_id)), 200, {"Cache-Control": "no-store"}
    except pals.PalError as error:
        return _pal_error(error)


@bp.route("/api/pals/<avatar_id>", methods=["PUT"])
def update_pal(avatar_id):
    """{name?, voice?, persona?: {about?, traits?, speech_style?, interests?, quirks?}, features?: {memory?, notes?, story?,
    presence?}, kids?} -> the pal's settings."""
    if _cross_site():
        return _forbidden()
    from core import pals
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict) or not set(data) <= {"name", "voice", "persona", "features", "kids", "pin"}:
        return jsonify({"error": "Only name, voice, persona, features and kids can be changed."}), 400
    if "kids" in data:
        # Switching a pal into or out of kids mode is a grown-up's decision: it needs the parent PIN.
        from core import lock
        try:
            lock.verify_pin(data.get("pin"))
        except lock.LockError as error:
            return _lock_error(error)
    store = companion.get_services().avatars
    try:
        pals.update(store, avatar_id, name=data.get("name"), voice=data.get("voice"), features=data.get("features"),
                    kids=data.get("kids"), persona=data.get("persona"))
        return jsonify(pals.settings(store, avatar_id)), 200, {"Cache-Control": "no-store"}
    except pals.PalError as error:
        return _pal_error(error)


# ============================================================
# Kids mode: locked until enabled here, then a switch per pal (core/kids.py)
# ============================================================

def _kids_payload():
    from core import kids, lock
    store = companion.get_services().avatars
    return {**kids.get_state(), "pin_set": lock.has_pin(),
            "pals": sorted(r.get("name") or r["id"] for r in store.list() if kids.is_on(r))}


@bp.route("/api/kids", methods=["GET"])
def kids_state():
    return jsonify(_kids_payload()), 200, {"Cache-Control": "no-store"}


@bp.route("/api/kids", methods=["POST"])
def set_kids_state():
    """{enabled: true, understood: true, pin} unlocks kids mode and sets the parent PIN; {enabled: false, pin} locks it
    again (refused while a pal uses it). Both need the PIN once one exists: the lock is a grown-up's decision."""
    if _cross_site():
        return _forbidden()
    from core import kids, lock
    data = request.get_json(silent=True) or {}
    if (not isinstance(data, dict) or type(data.get("enabled")) is not bool
            or not set(data) <= {"enabled", "understood", "pin"}):
        return jsonify({"error": "Send enabled: true (with understood: true and a PIN) or enabled: false (with the PIN)."}), 400
    try:
        if data["enabled"]:
            if data.get("understood") is not True:
                raise kids.KidsError("Confirm that you understand kids mode is a guardrail, not a guarantee.", 400)
            if lock.has_pin():
                lock.verify_pin(data.get("pin"))
            else:
                lock.set_pin(data.get("pin"))
            kids.enable(True)
            lock.unlock()                       # the grown-up who just set this up keeps this browser open
        else:
            lock.verify_pin(data.get("pin"))
            kids.disable(companion.get_services().avatars)
    except kids.KidsError as error:
        return jsonify({"error": str(error)}), error.status, {"Cache-Control": "no-store"}
    except lock.LockError as error:
        return _lock_error(error)
    return jsonify(_kids_payload()), 200, {"Cache-Control": "no-store"}


# ============================================================
# Characters: imported character cards (core/characters.py, core/charcard.py)
# ============================================================

@bp.route("/api/characters/import", methods=["POST"])
def import_character():
    """multipart file: a .png card or a .json card -> the new character, opened."""
    if _cross_site():
        return _forbidden()
    from core import charcard, characters
    upload = request.files.get("file")
    if upload is None:
        return jsonify({"error": "Choose a character card file to import."}), 400
    data = upload.stream.read(charcard.MAX_FILE_BYTES + 1)
    if len(data) > charcard.MAX_FILE_BYTES:
        return jsonify({"error": "The card is larger than 20 MB."}), 413
    services = companion.get_services()
    try:
        made = characters.import_card(services.avatars, upload.filename or "", data, checker=services.checker)
        return jsonify(companions.open_companion(made["id"])), 201
    except characters.CharacterError as error:
        return jsonify({"error": str(error)}), error.status, {"Cache-Control": "no-store"}
    except companions.CompanionError as error:
        return jsonify({"error": str(error)}), error.status


@bp.route("/api/characters/<avatar_id>", methods=["GET"])
def character_settings(avatar_id):
    from core import characters
    try:
        return jsonify(characters.settings(companion.get_services().avatars, avatar_id)), 200, {"Cache-Control": "no-store"}
    except characters.CharacterError as error:
        return jsonify({"error": str(error)}), error.status, {"Cache-Control": "no-store"}


@bp.route("/api/characters/<avatar_id>", methods=["PUT"])
def update_character(avatar_id):
    """{voice?, features?, card_instructions?, lorebook?} -> the character's settings."""
    if _cross_site():
        return _forbidden()
    from core import characters
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict) or not set(data) <= {"voice", "features", "card_instructions", "lorebook"}:
        return jsonify({"error": "Only voice, features, card_instructions and lorebook can be changed."}), 400
    try:
        return (jsonify(characters.update(companion.get_services().avatars, avatar_id, voice=data.get("voice"),
                                          features=data.get("features"), card_instructions=data.get("card_instructions"),
                                          lorebook=data.get("lorebook"))),
                200, {"Cache-Control": "no-store"})
    except characters.CharacterError as error:
        return jsonify({"error": str(error)}), error.status, {"Cache-Control": "no-store"}


@bp.route("/api/companions/<avatar_id>/open", methods=["POST"])
def open_companion(avatar_id):
    if _cross_site():
        return _forbidden()
    from core import lock
    kids_view = lock.in_kids_view()
    if kids_view and not lock.avatar_is_kids(avatar_id):
        return jsonify({"error": "Kids mode only shows kids pals.", "code": "kids_view"}), 403
    try:
        opened = lock.open_kids_pal(avatar_id) if kids_view else companions.open_companion(avatar_id)
        _recheck_portraits(avatar_id)
        return jsonify(opened)
    except companions.CompanionError as e:
        return jsonify({"error": str(e)}), e.status


@bp.route("/api/companions/<avatar_id>", methods=["DELETE"])
def remove_companion(avatar_id):
    if _cross_site():
        return _forbidden()
    try:
        return jsonify({"status": "ok", **companions.remove(avatar_id)})
    except companions.CompanionError as e:
        return jsonify({"error": str(e)}), e.status


# ============================================================
# Her notes
# ============================================================

def _notes_payload(avatar_id):
    svc = notes.get_notes_service()
    return {"notes": svc.store.read(avatar_id), "status": svc.status(avatar_id, _session_of(avatar_id))}


@bp.route("/api/companions/<avatar_id>/notes", methods=["GET"])
def get_notes(avatar_id):
    if not companion.get_services().avatars.get(avatar_id):
        return jsonify({"error": "That companion was not found."}), 404
    return jsonify(_notes_payload(avatar_id))


@bp.route("/api/companions/<avatar_id>/notes", methods=["PUT"])
def put_notes(avatar_id):
    """The person corrects what she wrote. Both notes are replaced by what is sent (a missing one is left alone)."""
    if _cross_site():
        return _forbidden()
    if not companion.get_services().avatars.get(avatar_id):
        return jsonify({"error": "That companion was not found."}), 404
    data = request.get_json(silent=True) or {}
    changes = {}
    for key in notes.NOTE_FILES:
        if key in data:
            if not isinstance(data[key], str) or len(data[key]) > MAX_NOTE_CHARS:
                return jsonify({"error": f"The {key.replace('_', ' ')} note must be text under {MAX_NOTE_CHARS} characters."}), 400
            changes[key] = data[key]
    svc = notes.get_notes_service()
    with svc._lock:
        if avatar_id in svc._busy:
            return jsonify(error='Wait for the notes update to finish before editing.'), 409
        svc.store.write(avatar_id, **changes)
        svc.store.update_state(avatar_id, source_kind='user-edited', includes_user_edits=True)
    return jsonify(_notes_payload(avatar_id))


@bp.route("/api/companions/<avatar_id>/notes/update", methods=["POST"])
def update_notes_now(avatar_id):
    """Ask her to write up the conversation so far now. It runs in the background; poll the status."""
    if _cross_site():
        return _forbidden()
    if not companion.get_services().avatars.get(avatar_id):
        return jsonify({"error": "That companion was not found."}), 404
    session_id = _session_of(avatar_id)
    if not session_id:
        return jsonify({"error": "You have not talked yet."}), 400
    svc = notes.get_notes_service()
    if svc.busy(avatar_id):
        return jsonify({"started": False, "error": "She is already writing things down."}), 409
    from core.state_gate import start_job
    start_job(svc.run_now, (avatar_id, session_id, False), name="altera-notes-now")
    return jsonify({"started": True}), 202


@bp.route("/api/companions/<avatar_id>/status", methods=["GET"])
def companion_status(avatar_id):
    s = companion.get_services()
    if not s.avatars.get(avatar_id):
        return jsonify({"error": "That companion was not found."}), 404
    svc = notes.get_notes_service()
    return jsonify({"notes": svc.status(avatar_id, _session_of(avatar_id)), "pictures": s.photos.sync_status(avatar_id),
                    "gallery": len(s.photos.gallery(avatar_id, adult.effective_level()))})


@bp.route("/api/companions/<avatar_id>/pictures/sync", methods=["POST"])
def sync_pictures(avatar_id):
    """Fetch the pictures approved in NPC Forge's studio (in the background)."""
    if _cross_site():
        return _forbidden()
    s = companion.get_services()
    if not s.avatars.get(avatar_id):
        return jsonify({"error": "That companion was not found."}), 404
    started = s.photos.sync_premade_async(avatar_id)
    return jsonify({"started": started, **s.photos.sync_status(avatar_id)}), 202


# ============================================================
# Meeting someone new: NPC Forge makes her
# ============================================================

@bp.route("/api/forge/status", methods=["GET"])
def forge_status():
    s = companion.get_services()
    return jsonify({**_forge().status(), "library": s.avatars.forge_available()})


@bp.route("/api/forge/start", methods=["POST"])
def forge_start():
    if _cross_site():
        return _forbidden()
    return jsonify(forge.start(companion.get_services().config))


@bp.route("/api/companions/make", methods=["POST"])
def make_someone():
    """Roll an adult and queue their portrait. Body: {gender?, origin?, hair_color?, age_group?}."""
    if _cross_site():
        return _forbidden()
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(_forge().make_someone(data)), 202
    except forge.ForgeError as e:
        return jsonify({"error": str(e)}), 503


@bp.route("/api/companions/make/<npc_id>/<job_id>", methods=["GET"])
def make_someone_status(npc_id, job_id):
    """Check the finished portrait's age before importing and opening the companion."""
    s = companion.get_services()
    if not avatars.valid_id(npc_id):
        return jsonify({"error": "Not a character id."}), 400
    if s.avatars.get(npc_id):
        return jsonify({"state": "ready", "companion": companions.open_companion(npc_id)})
    try:
        job = _forge().job(job_id)
    except forge.ForgeError as e:
        return jsonify({"state": "error", "note": str(e)})
    if job["state"] != "done":
        return jsonify(job)
    try:
        s.avatars.import_from_forge(npc_id, checker=s.checker)
    except avatars.AvatarError as e:
        return jsonify({"state": "rejected", "note": str(e)})
    except Exception:
        LOGGER.exception("Could not import the new character")
        return jsonify({"state": "error", "note": sanitized_error_response("internal_error", 500)[0]["error"]})
    return jsonify({"state": "ready", "companion": companions.open_companion(npc_id)})


@bp.route("/api/companions/make/<npc_id>/<job_id>", methods=["DELETE"])
def make_someone_cancel(npc_id, job_id):
    if _cross_site():
        return _forbidden()
    _forge().cancel(job_id)
    return jsonify({"status": "ok"})


@bp.route("/api/companions/make/<npc_id>/<job_id>/retry", methods=["POST"])
def make_someone_retry(npc_id, job_id):
    if _cross_site():
        return _forbidden()
    if not avatars.valid_id(npc_id):
        return jsonify({"error": "Not a character id."}), 400
    client = _forge()
    try:
        job = client.job(job_id, npc_id=npc_id)
        if job["state"] != "error" or job.get("code") != "gpu_busy":
            return jsonify({"error": "That portrait is not waiting for the GPU."}), 409
        client.retry_gpu_job(job_id)
    except forge.ForgeError as error:
        return jsonify({"error": str(error)}), 503
    return jsonify({"state": "queued"}), 202


# ============================================================
# A portrait from NPC Forge for a pal or a character (core/portraits.py)
# ============================================================

def _portrait_llm():
    from core.routes.core_api import _get_chat_engine
    return _get_chat_engine().llm_client


def _portrait_call(action, *, write=True):
    """Run a portrait action with the origin check and return errors as JSON."""
    if write and _cross_site():
        return _forbidden()
    from core import portraits
    try:
        return action(portraits, companion.get_services())
    except portraits.PortraitError as error:
        return _pal_error(error)


@bp.route("/api/portraits/<avatar_id>", methods=["GET"])
def portrait_state(avatar_id):
    """Where this person's portrait comes from, what can be done, and the words to paint from."""
    def action(portraits, s):
        record = portraits._person(s.avatars, avatar_id)
        if portraits.unchecked(s.avatars, record):
            _recheck_portraits(avatar_id)
            record = portraits._person(s.avatars, avatar_id)
        return jsonify(portraits.summary(s.avatars, record, s.living)), 200, {"Cache-Control": "no-store"}
    return _portrait_call(action, write=False)


@bp.route("/api/portraits/<avatar_id>/describe", methods=["POST"])
def portrait_describe(avatar_id):
    """Ask the chat model how a character looks, from its card: {description}."""
    def action(portraits, s):
        record = portraits._person(s.avatars, avatar_id)
        from core.avatars import kind_of, written_here
        if kind_of(record) != "character" and not written_here(record):
            raise portraits.PortraitError("Only a character or a companion written here has words to describe them from.")
        return jsonify({"description": portraits.describe_with_model(record, _portrait_llm())})
    return _portrait_call(action)


@bp.route("/api/portraits/<avatar_id>", methods=["POST"])
def portrait_start(avatar_id):
    """{from: description|card, description?, style, setting, gender?, image_model?, edit_model?} -> NPC Forge starts
    painting (202). An engine left out or '' is NPC Forge's own default."""
    def action(portraits, s):
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or not set(data) <= {"from", "description", "style", "setting", "gender",
                                                           "image_model", "edit_model"}:
            raise portraits.PortraitError("Send from, description, style, setting, gender and the engines only.")
        made = portraits.start(s.avatars, avatar_id, _forge(), source=data.get("from"),
                               description=data.get("description") or "", style=data.get("style") or portraits.DEFAULT_STYLE,
                               setting=data.get("setting") or portraits.DEFAULT_SETTING, gender=data.get("gender") or "",
                               image_model=data.get("image_model") or "", edit_model=data.get("edit_model") or "",
                               living=s.living)
        return jsonify(made), 202
    return _portrait_call(action)


@bp.route("/api/portraits/<avatar_id>/job", methods=["GET"])
def portrait_job(avatar_id):
    """{state, note, portrait}: a finished portrait is age-checked and brought in here."""
    def action(portraits, s):
        return jsonify(portraits.check(s.avatars, avatar_id, _forge(), s.checker, s.living)), 200, {"Cache-Control": "no-store"}
    return _portrait_call(action, write=False)


@bp.route("/api/portraits/<avatar_id>/job", methods=["DELETE"])
def portrait_job_cancel(avatar_id):
    def action(portraits, s):
        return jsonify(portraits.cancel(s.avatars, avatar_id, _forge(), s.living))
    return _portrait_call(action)


@bp.route("/api/portraits/<avatar_id>/living", methods=["POST"])
def portrait_bring_to_life(avatar_id):
    """{edit_model?} -> NPC Forge makes the living portrait from the portrait it painted (202). Without edit_model, the
    engine chosen for the portrait."""
    def action(portraits, s):
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict) or not set(data) <= {"edit_model"}:
            raise portraits.PortraitError("Send edit_model only.")
        return jsonify(portraits.bring_to_life(s.avatars, avatar_id, _forge(), s.living, data.get("edit_model"))), 202
    return _portrait_call(action)


@bp.route("/api/portraits/<avatar_id>/library", methods=["POST"])
def portrait_from_library(avatar_id):
    """{npc_id} -> use the portrait of a character already in NPC Forge's library (age-checked, then copied in)."""
    def action(portraits, s):
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or set(data) != {"npc_id"} or not isinstance(data["npc_id"], str):
            raise portraits.PortraitError("Send the npc_id of a character in NPC Forge's library.")
        return jsonify(portraits.from_library(s.avatars, avatar_id, data["npc_id"], s.checker, s.living))
    return _portrait_call(action)


@bp.route("/api/portraits/<avatar_id>/forge", methods=["DELETE"])
def portrait_stop_using(avatar_id):
    """Back to the card's picture, or the initial."""
    def action(portraits, s):
        return jsonify(portraits.stop_using(s.avatars, avatar_id, s.living))
    return _portrait_call(action)
