"""Avatar, picture and adult-mode routes.

Avatars are characters imported from NPC Forge. Pictures are edits of an avatar's reference images. Adult mode is the opt-in
that decides how far conversation and pictures may go. Every picture is served through a no-store route, and every
state-changing route refuses requests that come from another website.
"""

import base64
import binascii
import copy
import hashlib
import hmac
import json
import logging
from urllib.parse import urlparse

from flask import Blueprint, Response, current_app, jsonify, request, send_file

from core import addons, adult, avatars, companion, companions, forge, kids, living, photos
from core.constants import api_error
from core.profile import living_portrait_enabled, set_living_portrait
from core.datastore import get_datastore

bp = Blueprint("companion", __name__)
LOGGER = logging.getLogger(__name__)


# ============================================================
# Helpers
# ============================================================

def _cross_site() -> bool:
    """True for a request that a browser made on behalf of another website."""
    if request.headers.get("Sec-Fetch-Site") == "cross-site":
        return True
    origin = request.headers.get("Origin")
    return bool(origin) and urlparse(origin).netloc != request.host


def _forbidden():
    return jsonify({"error": "Cross-site requests are not allowed."}), 403


def _image(path):
    response = send_file(path, mimetype="image/png", max_age=0)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


# ============================================================
# Avatars
# ============================================================

@bp.route("/api/avatars", methods=["GET"])
def list_avatars():
    from core import kids, lock
    s = companion.get_services()
    records = s.avatars.list()
    active = s.avatars.active_id()
    if lock.in_kids_view():
        records = [r for r in records if kids.is_on(r)]
        if not any(r["id"] == active for r in records):
            active = records[0]["id"] if records else None
    return jsonify({
        "avatars": [s.avatars.public(r) for r in records],
        "active": active,
        "forge_available": s.avatars.forge_available(),
        "photos_ready": companion.photos_ready(),
    })


@bp.route("/api/avatars/forge", methods=["GET"])
def list_forge_characters():
    s = companion.get_services()
    try:
        return jsonify({"available": s.avatars.forge_library_available(), "characters": s.avatars.forge_choices()})
    except forge.ForgeError as error:
        return jsonify(error=str(error)), 503


@bp.route("/api/forge/<npc_id>/thumb", methods=["GET"])
def forge_thumb(npc_id):
    from core import characters
    store = companion.get_services().avatars
    if store.forge_mode() == "cards":
        try:
            package = characters.read_package(npc_id + ".png", store.forge_client().card(npc_id, "png"))
        except (characters.CharacterError, forge.ForgeError):
            return api_error("not_found", 404)
        return Response(package.portrait, mimetype="image/png",
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
    path = store.forge_file(npc_id, "thumb") or store.forge_file(npc_id, "portrait")
    if not path:
        return api_error("not_found", 404)
    return _image(path)


@bp.route("/api/avatars/import", methods=["POST"])
def import_avatar():
    if _cross_site():
        return _forbidden()
    data = request.get_json(silent=True) or {}
    s = companion.get_services()
    from core import characters
    try:
        record = characters.import_from_forge(s, data.get("npc_id"), name=(data.get("name") or "").strip() or None,
                                              age=data.get("age"))
    except (avatars.AvatarError, characters.CharacterError) as e:
        return jsonify({"error": str(e)}), 422
    except forge.ForgeError as error:
        return jsonify(error=str(error)), 503
    except Exception:
        LOGGER.exception("Avatar import failed")
        return api_error("internal_error", 500)
    return jsonify(record)


@bp.route("/api/avatars/<avatar_id>/activate", methods=["POST"])
def activate_avatar(avatar_id):
    if _cross_site():
        return _forbidden()
    s = companion.get_services()
    try:
        s.avatars.activate(avatar_id)
    except avatars.AvatarError as e:
        return jsonify({"error": str(e)}), 404
    return jsonify({"status": "ok", "active": avatar_id})


@bp.route("/api/avatars/<avatar_id>", methods=["DELETE"])
def delete_avatar(avatar_id):
    if _cross_site():
        return _forbidden()
    try:
        return jsonify({"status": "ok", **companions.remove(avatar_id)})
    except companions.CompanionError as e:
        return jsonify({"error": str(e)}), e.status


@bp.route("/api/avatars/<avatar_id>/portrait", methods=["GET"])
def avatar_portrait(avatar_id):
    store = companion.get_services().avatars
    path = store.image_path(avatar_id, "portrait")
    if not path:
        record = store.get(avatar_id)
        if record and (avatars.kind_of(record) in ("pal", "character") or avatars.written_here(record)):
            # no picture (a pal, a .json card, or a companion written here): its initial
            from core import pals
            return Response(pals.portrait_svg(record), mimetype="image/svg+xml",
                            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                                     "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})
        return api_error("not_found", 404)
    return _image(path)


@bp.route("/api/avatars/<avatar_id>/portrait", methods=["POST"])
def upload_portrait(avatar_id):
    """A picture the person uploads for a companion written in Altera. It goes through the same age check as a Forge portrait:
    one that reads as under age is refused, and the initial stays."""
    if _cross_site():
        return _forbidden()
    s = companion.get_services()
    upload = request.files.get("file")
    if upload is None:
        return jsonify({"error": "Choose a PNG picture to upload."}), 400
    data = upload.stream.read(avatars.MAX_PORTRAIT_BYTES + 1)
    if len(data) > avatars.MAX_PORTRAIT_BYTES:
        return jsonify({"error": f"The picture must be smaller than {avatars.MAX_PORTRAIT_BYTES // (1024 * 1024)} MB."}), 413
    try:
        record = s.avatars.set_uploaded_portrait(avatar_id, data, checker=s.checker)
    except avatars.AvatarError as error:
        return jsonify({"error": str(error)}), 422
    except Exception:
        LOGGER.exception("Portrait upload failed")
        return api_error("internal_error", 500)
    try:                                                     # frames of an earlier portrait do not fit this one
        s.living.forget(avatar_id)
    except Exception:
        LOGGER.exception("Could not forget the living portrait after an upload")
    return jsonify(record)


@bp.route("/api/avatars/<avatar_id>/portrait", methods=["DELETE"])
def remove_uploaded_portrait(avatar_id):
    """Take away a picture uploaded for a companion written here; the initial comes back."""
    if _cross_site():
        return _forbidden()
    from core import portraits
    s = companion.get_services()
    try:
        return jsonify(portraits.remove_uploaded(s.avatars, avatar_id, s.living))
    except portraits.PortraitError as error:
        return jsonify({"error": str(error)}), error.status, {"Cache-Control": "no-store"}


# ============================================================
# Living portrait (frames made in NPC Forge: she blinks, talks, changes expression)
# ============================================================

@bp.route("/api/living", methods=["GET"])
def get_living_setting():
    return jsonify({"enabled": living_portrait_enabled()})


@bp.route("/api/living", methods=["POST"])
def post_living_setting():
    if _cross_site():
        return _forbidden()
    data = request.get_json(silent=True) or {}
    if not isinstance(data.get("enabled"), bool):
        return jsonify({"error": "enabled must be true or false."}), 400
    set_living_portrait(data["enabled"])
    return jsonify({"enabled": living_portrait_enabled()})


@bp.route("/api/avatars/<avatar_id>/living", methods=["GET"])
def avatar_living(avatar_id):
    s = companion.get_services()
    if not s.avatars.get(avatar_id):
        return api_error("not_found", 404)
    return jsonify({"enabled": living_portrait_enabled(), **s.living.status(avatar_id)})


@bp.route("/api/avatars/<avatar_id>/living/sync", methods=["POST"])
def avatar_living_sync(avatar_id):
    if _cross_site():
        return _forbidden()
    s = companion.get_services()
    if not s.avatars.get(avatar_id):
        return api_error("not_found", 404)
    return jsonify({"started": s.living.sync_async(avatar_id)})


@bp.route("/api/avatars/<avatar_id>/living/<frame>", methods=["GET"])
def avatar_living_frame(avatar_id, frame):
    path = companion.get_services().living.frame_path(avatar_id, frame) if frame in living.FRAMES else None
    if not path:
        return api_error("not_found", 404)
    return _image(path)


# ============================================================
# Ready-made pictures (approved in NPC Forge's studio, imported one at a time so the UI can show progress)
# ============================================================

@bp.route("/api/avatars/<avatar_id>/premade", methods=["GET"])
def list_premade(avatar_id):
    if not addons.picture_source():
        return api_error("not_found", 404)
    s = companion.get_services()
    if not s.avatars.get(avatar_id):
        return jsonify({"error": "That avatar was not found."}), 404
    npc_id = avatars.forge_id(s.avatars.get(avatar_id))
    have = s.photos.premade_keys(avatar_id)
    hidden = not adult.get_state()["enabled"]             # while adult mode is off, its pictures stay out of sight
    items = []
    for item in s.avatars.forge_premade(npc_id):
        if not adult.visible(item.get("level")) or (hidden and item.get("level") != "off"):
            continue
        photo = have.get(f"{npc_id}:{item['item_id']}")
        items.append({**item, "imported": bool(photo), "photo_id": photo,
                      "image_url": f"/api/photos/{photo}/image" if photo else None})
    return jsonify({"items": items, "photos": s.photos.list_premade(avatar_id)})


@bp.route("/api/avatars/<avatar_id>/premade/import", methods=["POST"])
def import_premade(avatar_id):
    """Import one approved picture through the core checks."""
    if not addons.picture_source():
        return api_error("not_found", 404)
    if _cross_site():
        return _forbidden()
    s = companion.get_services()
    record = s.avatars.get(avatar_id)
    if not record:
        return jsonify({"error": "That avatar was not found."}), 404
    npc_id = avatars.forge_id(record)
    item_id = (request.get_json(silent=True) or {}).get("item_id")
    item = next((i for i in s.avatars.forge_premade(npc_id) if i["item_id"] == item_id), None)
    path = s.avatars.forge_premade_file(npc_id, item_id) if item else None
    if not item or not path:
        return jsonify({"error": "That picture is not among the ones approved in NPC Forge."}), 404
    try:
        photo = s.photos.import_premade(avatar_id, path if isinstance(path, bytes) else path.read_bytes(), item["scene"], item["level"],
                                        key=f"{npc_id}:{item_id}", label=item["label"])
    except photos.PhotoBlocked as e:
        return jsonify({"error": str(e), "blocked": True, "required_level": e.required_level}), 422
    except photos.PhotoError as e:
        return jsonify({"error": str(e)}), 400
    except Exception:
        LOGGER.exception("Premade import failed")
        return api_error("internal_error", 500)
    return jsonify(photo)


# ============================================================
# Pictures
# ============================================================

def _shared_recipients(services, scope):
    """Capture approved recipients and a detached copy of the image backend."""
    from core import imagegen
    from core.direct_model_http import validate_url
    if kids.is_on(services.avatars.get(scope[1])):
        raise photos.PhotoBlocked(photos.KIDS_PICTURES_OFF)
    if services.imagegen_config.get('enabled', True) is False:
        raise ValueError('Picture generation is disabled in settings.')
    vision = services.checker._target()
    if not isinstance(vision, tuple) or len(vision) != 3:
        raise ValueError('The configured vision provider is unavailable.')
    base, key, model = vision
    validate_url(base)
    if (not isinstance(model, str) or not model.strip() or len(model) > 512
            or (key is not None and not isinstance(key, str))):
        raise ValueError('The configured vision provider is unavailable.')
    image_url = services.comfy.base
    validate_url(image_url)

    def review_backend(item):
        if isinstance(item, imagegen.FallbackBackend):
            primary = review_backend(item.primary)
            fallback = review_backend(item.fallback)
            if primary is not None and fallback is not None:
                return imagegen.FallbackBackend(primary, fallback)
            return primary or fallback
        if not isinstance(item, (imagegen.MageFlowBackend, imagegen.WorkflowBackend)) or item.comfy is not services.comfy:
            raise ValueError('The configured image backend cannot be reviewed.')
        if isinstance(item, imagegen.WorkflowBackend) and not item.accepts_shared_references():
            return None
        return item

    backend = review_backend(services.photos.backend_for('off'))
    if backend is None:
        raise ValueError('The shared picture workflow must use both image reference placeholders in its node inputs.')
    backend = copy.deepcopy(backend)
    material = json.dumps([scope, base, key, model, image_url], ensure_ascii=True, separators=(',', ':')).encode('ascii')
    secret = current_app.secret_key
    if isinstance(secret, str):
        secret = secret.encode('utf-8')
    if not isinstance(secret, bytes) or not secret:
        raise ValueError('Recipient review is unavailable.')
    fingerprint = hmac.new(secret, material, hashlib.sha256).hexdigest()
    return vision, backend, {'vision_endpoint': base, 'vision_model': model,
                             'image_endpoint': image_url, 'provider_fingerprint': fingerprint}


@bp.route('/api/shared-scenes/context', methods=['POST'])
def shared_photo_context():
    """Review recipients without transmitting an image or contacting a provider."""
    if _cross_site():
        return _forbidden()
    data = request.get_json(silent=True)
    fields = ('session_id', 'avatar_id', 'dataset_epoch')
    if not isinstance(data, dict) or set(data) != set(fields):
        return jsonify(error='The shared picture context is invalid.'), 400
    scope = tuple(data[key] for key in fields)
    try:
        get_datastore().check_shared_photo_scope(*scope)
        _, _, review = _shared_recipients(companion.get_services(), scope)
    except photos.PhotoBlocked as error:
        return jsonify(error=str(error), blocked=True), 422
    except ValueError as error:
        return jsonify(error=str(error)), 409
    except Exception:
        LOGGER.exception('Shared picture recipient review failed')
        return jsonify(error='The configured picture providers could not be reviewed.'), 409
    response = jsonify(review)
    response.headers['Cache-Control'] = 'no-store'
    return response


@bp.route('/api/shared-scenes', methods=['POST'])
def create_shared_photo():
    """Explicit one-shot user reference for an imagined shared picture."""
    if _cross_site():
        return _forbidden()
    from core.shared_reference import MAX_BYTES
    limit = ((MAX_BYTES + 2) // 3) * 4 + 8192
    if not request.is_json or (request.content_length is not None and request.content_length > limit):
        return jsonify(error='Use a bounded JSON shared-picture request.'), 400
    data = request.get_json(silent=True)
    fields = {'session_id', 'avatar_id', 'dataset_epoch', 'request_id', 'scene', 'reference_png',
              'self_reference', 'adult_confirmation', 'provider_confirmation', 'provider_fingerprint'}
    if not isinstance(data, dict) or set(data) != fields:
        return jsonify(error='Review all shared-picture fields before submitting.'), 400
    if any(data[key] is not True for key in ('self_reference', 'adult_confirmation', 'provider_confirmation')):
        return jsonify(error='Confirm the reference, adult status and configured image recipients.'), 400
    encoded = data['reference_png']
    if not isinstance(encoded, str) or len(encoded) > ((MAX_BYTES + 2) // 3) * 4:
        return jsonify(error='The selected reference image is unavailable or too large.'), 400
    try:
        reference = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        return jsonify(error='The selected reference image is invalid.'), 400
    store = get_datastore()
    sid, owner, epoch, request_id = (data[key] for key in ('session_id', 'avatar_id', 'dataset_epoch', 'request_id'))
    services = companion.get_services()
    if services.imagegen_config.get('enabled', True) is False:
        return jsonify(error='Picture generation is disabled in settings.'), 409
    message_ids = []
    try:
        store.check_shared_photo_scope(sid, owner, epoch)
        vision, backend, review = _shared_recipients(services, (sid, owner, epoch))
        approval = data['provider_fingerprint']
        if not isinstance(approval, str) or not hmac.compare_digest(approval, review['provider_fingerprint']):
            return jsonify(error='Picture providers changed. Review their addresses before trying again.',
                           code='shared_providers_changed'), 409
        record = services.photos.create_shared(data['scene'], reference, avatar_id=owner, session_id=sid,
            dataset_epoch=epoch, request_id=request_id, self_attested=True,
            vision_target=vision, image_backend=backend, approval_id=approval,
            before_new=lambda: store.require_new_shared_photo(sid, owner, epoch, request_id),
            on_accept=lambda pid: message_ids.append(store.record_shared_photo(sid, owner, epoch, request_id, pid)))
    except photos.PhotoBlocked as e:
        return jsonify(error=str(e), blocked=True), 422
    except photos.PhotoError as e:
        return jsonify(error=str(e)), 400
    except ValueError as e:
        return jsonify(error=str(e), code='shared_context_changed'), 409
    except Exception:
        LOGGER.exception('Shared picture could not be accepted')
        return jsonify(error='The shared picture could not be accepted.'), 409
    return jsonify({**record, 'message_id': message_ids[0]}), 202


@bp.route("/api/photos", methods=["POST"])
def create_photo():
    """The camera button: ask for a picture directly. The scene is checked against the hard rules and the current level."""
    if _cross_site():
        return _forbidden()
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify(error='Expected a JSON object.'), 400
    s = companion.get_services()
    session_id = data.get("session_id")
    if session_id is not None:
        if type(session_id) is not int or not 0 < session_id < 2 ** 63:
            return jsonify(error='session_id must be a positive integer.'), 400
        if not get_datastore().get_session(session_id):
            return jsonify(error='Session not found.'), 404
    try:
        avatar = companions.avatar_for_session(session_id)
        record = s.photos.create(data.get("scene") or "", level=adult.effective_level(),
                                 session_id=session_id, avatar_id=avatar['id'] if avatar else None, source="user")
    except companions.CompanionError as e:
        return jsonify(error=str(e)), e.status
    except photos.PhotoBlocked as e:
        return jsonify({"error": str(e), "blocked": True, "required_level": e.required_level}), 422
    except photos.PhotoError as e:
        return jsonify({"error": str(e)}), 400
    if isinstance(session_id, int) and get_datastore().get_session(session_id):
        get_datastore().save_message(session_id, "assistant", f"[[photo:{record['id']}]]")
    return jsonify(record), 202


@bp.route("/api/photos", methods=["GET"])
def list_photos():
    s = companion.get_services()
    avatar_id = request.args.get("avatar") or s.avatars.active_id()
    try:
        limit = max(1, min(int(request.args.get("limit", 60)), 200))
    except ValueError:
        limit = 60
    return jsonify({"photos": s.photos.list(avatar_id, limit)})


@bp.route("/api/photos/<photo_id>", methods=["GET"])
def get_photo(photo_id):
    record = companion.get_services().photos.get(photo_id)
    if not record:
        return api_error("not_found", 404)
    return jsonify(record)


@bp.route("/api/photos/<photo_id>/image", methods=["GET"])
def photo_image(photo_id):
    path = companion.get_services().photos.image_path(photo_id)
    if not path:
        return api_error("not_found", 404)
    return _image(path)


@bp.route("/api/photos/<photo_id>/reroll", methods=["POST"])
def reroll_photo(photo_id):
    if _cross_site():
        return _forbidden()
    s = companion.get_services()
    try:
        record = s.photos.reroll(photo_id, level=adult.effective_level())
    except photos.PhotoBlocked as e:
        return jsonify({"error": str(e), "blocked": True, "required_level": e.required_level}), 422
    except photos.PhotoError as e:
        return jsonify({"error": str(e)}), 404
    return jsonify(record), 202


@bp.route("/api/photos/<photo_id>", methods=["DELETE"])
def delete_photo(photo_id):
    if _cross_site():
        return _forbidden()
    try:
        if not companion.get_services().photos.delete(photo_id):
            return api_error("not_found", 404)
    except photos.PhotoError as e:
        return jsonify(error=str(e)), 409
    return jsonify({"status": "ok"})


# ============================================================
# Status
# ============================================================

@bp.route("/api/imagegen/status", methods=["GET"])
def imagegen_status():
    """Is ComfyUI reachable, and which vision model does the age check use? (Makes a short network call.)"""
    s = companion.get_services()
    cfg = s.imagegen_config
    try:
        from core.llm import LLMClient
        model = (cfg.get("age_check") or {}).get("model") or LLMClient.get_instance(s.config).model
    except Exception:
        model = None
    return jsonify({
        "comfy": s.comfy.health(),
        "quality": s.photos.quality,
        "age_check_model": model,
        "forge_available": s.avatars.forge_available(),
    })
