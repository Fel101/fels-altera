"""Core API routes extracted from altera.py (Session 6: Blueprints).

Routes for chat, messages, memory CRUD, profile, and onboarding.
"""

import json
import logging
import math
import re
import uuid
from flask import Blueprint, request, jsonify, Response, current_app, after_this_request

from core.config import get_config
from core.constants import MAX_MESSAGE_LENGTH
from core.datastore import get_datastore
from core.memory import get_memory_manager
from core.knowledge import KnowledgeError, KnowledgeService
from core.owned_iterator import close_owned, owned_iterator

bp = Blueprint('core_api', __name__)
LOGGER = logging.getLogger(__name__)


# ============================================================
# Helper: lazy-load chat engine (avoids circular import with altera.py)
# ============================================================

def _get_chat_engine():
    """The shared chat engine (core/chat.py owns it; this module used to keep a second, never-refreshed copy)."""
    from core.chat import get_chat_engine

    return get_chat_engine()


# ============================================================
# Helper: input validation (same logic as altera.py)
# ============================================================

# Control characters that have no place in a message, so they cannot corrupt the database, the prompt or the page:
# everything below a space except tab, newline and carriage return, plus DEL. Written as an explicit set of ranges --
# the earlier pattern began "[-\b...", and a leading '-' inside a character class is a LITERAL HYPHEN, not the start of
# a range. It therefore stripped every hyphen from every message ("well-known" -> "wellknown", "2024-01-01" ->
# "20240101") before the message was sent to the model or written to the database, while letting NUL through untouched.
_CONTROL_CHARS = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')


def validate_message_input(text, max_length=MAX_MESSAGE_LENGTH):
    """Validate and sanitize user message input. Returns (sanitized_text, error_response)."""
    if text is None:
        return "", ({"error": "Message is required"}, 400)
    if not isinstance(text, str):
        return "", ({"error": "Message must be text"}, 400)
    stripped = text.strip()
    if not stripped:
        return "", ({"error": "Message cannot be empty"}, 400)
    if len(stripped) > max_length:
        return "", ({"error": f"Message too long (max {max_length} characters)"}, 413)
    sanitized = _CONTROL_CHARS.sub('', stripped)
    if not sanitized.strip():
        return "", ({"error": "Message cannot be empty"}, 400)
    try:
        sanitized.encode('utf-8')
    except UnicodeError:
        return "", ({"error": "Message contains invalid text."}, 400)
    return sanitized, None


def resolve_chat_session(session_id):
    """Return a valid session ID or an error tuple for explicit invalid IDs."""
    store = get_datastore()
    if session_id is None:
        return store.create_session("New Conversation"), None
    try:
        session_id = int(session_id)
    except (TypeError, ValueError):
        return None, ({"error": "Invalid session_id"}, 400)
    if not store.get_session(session_id):
        return None, ({"error": "Session not found"}, 404)
    return session_id, None


# ============================================================
# Routes — Chat/Messages
# ============================================================

@bp.route("/api/messages/<int:session_id>", methods=["GET"])
def get_messages(session_id):
    """Get all messages for a session."""
    store = get_datastore()
    messages = store.get_messages(session_id, limit=10000)
    from core.turns import get_turn_service
    service = get_turn_service(store)
    with service.connection() as conn:
        drafts = conn.execute("""SELECT d.* FROM chat_turns d WHERE session_id=? AND status != 'saved'
                                 AND NOT EXISTS (SELECT 1 FROM chat_turns s WHERE s.session_id=d.session_id
                                     AND s.status='saved' AND s.rowid>d.rowid AND
                                     ((d.target_message_id IS NOT NULL AND s.target_message_id=d.target_message_id)
                                      OR (d.target_message_id IS NULL AND d.user_message_id IS NOT NULL
                                          AND s.user_message_id=d.user_message_id)))
                                 ORDER BY d.rowid""", (session_id,)).fetchall()
    by_user = {}
    unattached = []
    for draft in drafts:
        anchor = draft["user_message_id"] or draft["target_message_id"]
        item = {
                "id": None, "session_id": session_id, "role": "assistant", "content": draft["response"],
                "timestamp": draft["created_at"], "reply_state": draft["status"], "draft": True,
                "request_id": draft["request_id"], "error": draft["error"] or None,
                "retry_message_id": anchor, "replacement_for": draft["target_message_id"],
            }
        if anchor:
            by_user.setdefault(anchor, []).append(item)
        else:
            unattached.append(item)
    result = []
    # How many answers a message has, so the page can offer "2 of 3" without asking for every version up front. A message
    # with no rows here has one answer (its own text); otherwise the rows are all of them, including the current one.
    counts = {}
    with service.connection() as conn:
        for row in conn.execute("SELECT message_id, COUNT(*) AS n FROM message_versions GROUP BY message_id"):
            counts[row["message_id"]] = row["n"]
    for message in messages:
        count = counts.get(message.id, 0)
        versions = count or 1
        result.append({**message.__dict__, "reply_state": "saved",
                       "versions": versions, "other_versions": versions - 1})
        result.extend(by_user.get(message.id, []))
    result.extend(unattached)
    return jsonify(result), 200, {"Cache-Control": "no-store"}


@bp.route("/api/chat", methods=["POST"])
def chat():
    """Handle a chat message -- returns full response. Thin wrapper around ChatEngine."""
    data = request.json or {}
    if not isinstance(data, dict):
        return jsonify(error="Expected a JSON object"), 400
    if "request_id" in data or "voice_note_id" in data:
        return chat_stream(_json_response=True)
    epoch_error = _check_dataset_epoch(data)
    if epoch_error:
        return epoch_error
    session_id = data.get("session_id") or None
    user_message_raw = data.get("message", "")

    validated, err = validate_message_input(user_message_raw)
    if err:
        return jsonify(err[0]), err[1]
    user_message = validated

    session_id, session_err = resolve_chat_session(session_id)
    if session_err:
        return jsonify(session_err[0]), session_err[1]

    engine = _get_chat_engine()
    from core.suite_gpu import GpuWaitError
    try:
        response_text, photos = engine.send_message(user_message, session_id, return_photos=True)
    except GpuWaitError as error:
        return jsonify(error=str(error), code="gpu_busy"), 503

    return jsonify({
        "response": response_text,
        "session_id": session_id,
        "photos": photos,
    })


@bp.route("/api/chat/stream", methods=["POST"])
def chat_stream(_json_response=False, _regenerate=False):
    """Handle a chat message -- returns streaming response. Thin wrapper around ChatEngine."""
    data = request.json or {}
    if not isinstance(data, dict):
        return jsonify(error="Expected a JSON object"), 400
    epoch_error = _check_dataset_epoch(data)
    if epoch_error:
        return epoch_error
    voice_note_service, voice_note_id, note_submitted = None, None, False
    if 'voice_note_id' in data:
        if _regenerate or data.get('greet'):
            return jsonify(error='A voice note must send its own reviewed transcript.'), 400
        from core.voice_notes import VoiceNoteError, get_voice_note_service
        voice_note_service = get_voice_note_service()
        voice_note_id = data['voice_note_id']
        try:
            prepared = voice_note_service.prepare_send(voice_note_id, data.get('session_id'),
                data.get('dataset_epoch', request.headers.get('X-Altera-Dataset')), data.get('reviewed_revision'))
        except VoiceNoteError as error:
            return jsonify(error=str(error), code='voice_note_error'), error.status
        data = {**data, **prepared}

        @after_this_request
        def release_voice_note_reservation(response):
            if not note_submitted:
                try:
                    voice_note_service.abort_send(voice_note_id)
                except Exception:
                    LOGGER.exception('Could not settle a voice-note submission reservation')
            return response
    session_id = data.get("session_id") or None
    user_message_raw = data.get("message", "")
    greet = data.get("greet") is True                      # a new companion speaks first: there is no message from the person
    regeneration = None
    if _regenerate:
        from core.turns import TurnError
        try:
            session_id, regeneration = _regeneration_target(data)
        except TurnError as e:
            return jsonify(error=str(e)), e.status
        user_message_raw = regeneration["user_text"]
        greet = False
        data.setdefault("request_id", uuid.uuid4().hex)

    if greet or (regeneration and regeneration.get("greet")):
        user_message = ""
    else:
        # Input validation (Fix #9)
        validated, err = validate_message_input(user_message_raw)
        if err:
            return jsonify(err[0]), err[1]
        user_message = validated

        if not user_message:
            return jsonify({"error": "Empty message"}), 400

    reliable = "request_id" in data
    if reliable:
        from core import safety
        from core.chat import REFUSAL
        if not greet and safety.blocks_message(user_message):
            return jsonify(error=REFUSAL, code="message_blocked"), 422
        if session_id is not None:
            try:
                if isinstance(session_id, bool):
                    raise ValueError()
                session_id = int(session_id)
            except (TypeError, ValueError):
                return jsonify(error="Invalid session_id"), 400
    else:
        session_id, session_err = resolve_chat_session(session_id)
        if session_err:
            return jsonify(session_err[0]), session_err[1]
    if not reliable and greet and get_datastore().get_message_count(session_id):
        return jsonify({"error": "She has already said hello."}), 409

    engine = _get_chat_engine()

    # Extract session values before entering generator (session needs request context in Flask 3.x)
    voice_enabled = False
    allow_cloud = False
    person_voice = None
    try:
        cfg = get_config()          # cached; this used to re-read and re-parse config.yaml on every streamed turn
        from core.profile import load_user_profile
        profile = load_user_profile()
        user_voice_enabled = profile.get("preferences", {}).get("voice_enabled", True)
        voice_enabled = bool(cfg.get('voice', {}).get('tts_enabled', False) and user_voice_enabled)
        if voice_enabled:
            from core import companions as companions_module
            person_voice = (companions_module.avatar_for_session(session_id) or {}).get('voice') or None
            from core import tts as tts_module
            if tts_module.settings(cfg)["cloud_fallback"]:
                allow_cloud = tts_module.cloud_allowed()     # adult mode is looked up here, in the request thread
    except Exception:
        # Voice is optional, so a failure here must not stop the reply — but swallowing it silently meant a broken
        # voice setup looked exactly like a voice that was switched off.
        LOGGER.exception("Could not work out the voice settings for this turn; continuing without speech")

    def generate():
        LOGGER.debug("SSE generator started: session_id=%r", session_id)
        if greet:
            # Her first moments: she looks at her own face once, then finds her words (the page shows her waking up meanwhile)
            yield f"data: {json.dumps({'status': 'looking'})}\n\n"
            try:
                from core import companion, companions, selfview
                record = companions.avatar_for_session(session_id)
                from core.avatars import kind_of
                if record and kind_of(record) == "companion":       # only an NPC Forge companion looks at herself
                    selfview.ensure(companion.get_services().avatars, record["id"], companion.get_services().config)
            except Exception:
                LOGGER.exception("Could not get her view of herself")
        yield f"data: {json.dumps({'status': 'typing'})}\n\n"

        # Altera speaks while she is still writing: finished sentences become audio clips, sent as 'audio' events in order.
        speech = None
        if voice_enabled:
            from core.turns import CURRENT_TURN
            from core.voice_layer import SpeechStream
            turn = CURRENT_TURN.get()
            speech = SpeechStream(allow_cloud=allow_cloud, cancel_event=turn.speech_cancel if turn else None,
                                  person_voice=person_voice)
        clip_count = 0

        def audio_events(urls):
            nonlocal clip_count
            for url in urls:
                clip_count += 1
                yield f"data: {json.dumps({'status': 'audio', 'audio_url': url, 'seq': clip_count})}\n\n"

        full_response = ""
        stream_failed = False
        try:
            try:
                LOGGER.debug("Calling streaming chat engine: session_id=%r", session_id)
                response_gen = engine.send_message(user_message, session_id, stream=True, greet=greet)
                LOGGER.debug("Streaming response generator type: %s", type(response_gen).__name__)

                chunk_count = 0
                with owned_iterator(response_gen) as chunks:
                    for chunk in chunks:
                        if chunk is None:
                            LOGGER.debug("Received stream completion sentinel after %s chunks", chunk_count)
                            break
                        if isinstance(chunk, str):
                            chunk_count += 1
                            full_response += chunk
                            yield f"data: {json.dumps({'response': chunk})}\n\n"
                            if speech:
                                speech.feed(chunk)
                                yield from audio_events(speech.ready())
                        elif isinstance(chunk, dict) and chunk.get("photo"):
                            yield f"data: {json.dumps({'photo': chunk['photo']})}\n\n"
                        elif isinstance(chunk, dict) and chunk.get("notes"):
                            yield f"data: {json.dumps({'status': 'notes', 'state': chunk['notes']})}\n\n"
                        elif isinstance(chunk, dict) and chunk.get("status") in {"gpu_wait", "typing"}:
                            yield f"data: {json.dumps(chunk)}\n\n"

                resp_len = len(full_response)
                LOGGER.debug("Streaming LLM returned chars=%s chunks=%s", resp_len, chunk_count)
            except InterruptedError:
                raise
            except Exception as error:
                LOGGER.exception("SSE stream error")
                stream_failed = error

            # The rest of the reply is spoken before the (slower) emotion analysis, not after it.
            from core.turns import CURRENT_TURN
            current_turn = CURRENT_TURN.get()
            stopped = bool(current_turn and current_turn.cancel.is_set())
            if speech and not stopped and not getattr(current_turn, 'cleanup_unconfirmed', False):
                try:
                    speech.finish()
                    yield from audio_events(speech.drain_iter())      # each clip is sent the moment it is ready
                except Exception:
                    LOGGER.exception("TTS generation error")

            turn = CURRENT_TURN.get()
            stopped = bool(turn and (turn.cancel.is_set() or turn.speech_cancel.is_set()))

            # Emotion analysis once the reply is complete (so it never pulls on the model at the same time), in the
            # background: the reply is finished without waiting for it
            from core import kids as kids_module
            if (not stream_failed and not greet and not regeneration and not stopped
                    and not kids_module.kept_from_model(session_id, user_message)):   # kids mode: it never reaches the model
                try:
                    from core.emotion import analyze_later
                    analyze_later(user_message, session_id=session_id, role="user")
                except Exception:
                    LOGGER.exception("Emotion analysis error after stream")
        finally:
            if speech:
                close_owned(speech)

        if stream_failed:
            from core.suite_gpu import GpuWaitError
            from core.turns import EmptyReply
            if isinstance(stream_failed, GpuWaitError) and CURRENT_TURN.get():
                raise stream_failed
            said = (str(stream_failed) if isinstance(stream_failed, (EmptyReply, GpuWaitError)) else
                    'The reply could not be completed. Check the saved conversation before retrying.')
            yield f"data: {json.dumps({'status': 'failed', 'error': said})}\n\n"
            return

        # Final event. 'audio_url' is kept (always null now: the clips came as 'audio' events) for older pages.
        final_event = {'status': 'final', 'audio_url': None}
        yield f"data: {json.dumps(final_event)}\n\n"
        LOGGER.debug("SSE generator yielding done event")
        yield f"data: {json.dumps({'status': 'done'})}\n\n"

    if reliable:
        from core.turns import get_turn_service, TurnError
        from core.voice_notes import VoiceNoteError
        service = get_turn_service()

        def produce(resolved_session):
            nonlocal session_id
            session_id = resolved_session
            with owned_iterator(generate()) as wires:
                for wire in wires:
                    yield json.loads(wire.removeprefix("data: ").strip())

        try:
            with service.lock:
                if voice_note_service is not None:
                    voice_note_service.prepare_send(voice_note_id, session_id,
                        data.get('dataset_epoch', request.headers.get('X-Altera-Dataset')), data.get('reviewed_revision'))
                turn = service.submit(data["request_id"], session_id, user_message, greet, produce,
                                      current_app._get_current_object(), regenerate=regeneration,
                                      dataset_epoch=data.get("dataset_epoch", request.headers.get("X-Altera-Dataset")))
        except (TurnError, VoiceNoteError) as e:
            return jsonify(error=str(e)), e.status
        if voice_note_service is not None:
            voice_note_service.mark_submitted(voice_note_id, turn)
            note_submitted = True
        if _json_response:
            with owned_iterator(service.events(turn["request_id"])) as events:
                for _ in events:
                    pass
            result = service.get(turn["request_id"])
            return jsonify(response=result["response"], session_id=result["session_id"],
                           status=result["status"], request_id=result["request_id"],
                           error=result["error"] or None), 200 if result["status"] == "saved" else 503
        return _turn_stream(service, turn["request_id"])
    return Response(generate(), mimetype="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


def _check_dataset_epoch(data):
    epoch = get_datastore().get_key("dataset_epoch") or {}
    supplied = data.get("dataset_epoch", request.headers.get("X-Altera-Dataset"))
    if (epoch.get("required") or supplied is not None) and supplied != epoch.get("value"):
        return jsonify(error="Altera's data was restored. Reload Altera and review any unsent drafts before sending.", code="dataset_changed"), 409
    return None


def _regeneration_target(data):
    from core.turns import TurnError
    store = get_datastore()
    try:
        raw_sid, raw_mid = data.get("session_id"), data.get("message_id")
        if isinstance(raw_sid, bool) or not isinstance(raw_sid, (int, str)):
            raise ValueError()
        sid = int(raw_sid)
        if raw_mid is not None and (isinstance(raw_mid, bool) or not isinstance(raw_mid, (int, str))):
            raise ValueError()
        mid = int(raw_mid) if raw_mid is not None else None
    except (ValueError, TypeError):
        raise TurnError("session_id and message_id must be valid integers.", 400) from None
    if not store.get_session(sid):
        raise TurnError("Session not found", 404)
    recent = store.get_messages(sid, limit=1)
    target = store.get_message(mid) if mid is not None else (recent[0] if recent else None)
    if not target or target.session_id != sid or target.role not in {"user", "assistant"}:
        raise TurnError("Message not found in this conversation.", 404)
    if target.role == "user":
        # Retrying a failed/stopped request uses the original user row. If an answer exists, replace that answer.
        following = store.get_messages_after(sid, target.id)
        assistant = next((m for m in following if m.role == "assistant"), None)
        next_user = next((m for m in following if m.role == "user"), None)
        if assistant and next_user and assistant.id > next_user.id:
            assistant = None
        if not assistant and next_user:
            raise TurnError("Choose an existing answer, or retry the latest unanswered message.")
        return sid, {"user_message_id": target.id, "assistant_message_id": assistant.id if assistant else None,
                     "user_text": target.content, "cutoff": target.id + 1, "greet": False}
    preceding = store.get_messages_before(sid, target.id, limit=10000)
    user = next((m for m in reversed(preceding) if m.role == "user"), None)
    return sid, {"user_message_id": user.id if user else None, "assistant_message_id": target.id,
                 "user_text": user.content if user else "", "cutoff": target.id, "greet": user is None}


@bp.route("/api/chat/regenerate", methods=["POST"])
def regenerate_chat():
    return chat_stream(_regenerate=True)


@bp.route("/api/chat/requests/<request_id>/stop", methods=["POST"])
def stop_chat(request_id):
    from core.turns import get_turn_service, TurnError
    try:
        return jsonify(status=get_turn_service().stop(request_id))
    except TurnError as e:
        return jsonify(error=str(e)), e.status


@bp.route("/api/messages/item/<int:message_id>", methods=["GET"])
def get_message_item(message_id):
    record = get_datastore().get_message(message_id)
    if record is None:
        return jsonify(error="Message not found"), 404
    return jsonify(record.__dict__), 200, {"Cache-Control": "no-store"}


@bp.route("/api/messages/<int:message_id>", methods=["PATCH", "DELETE"])
def change_message_item(message_id):
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify(error="Expected a JSON object"), 400
    raw_sid = data.get("session_id", request.args.get("session_id"))
    try:
        if isinstance(raw_sid, bool) or not isinstance(raw_sid, (str, int)):
            raise ValueError()
        session_id = int(raw_sid)
        if not 0 < session_id < 2 ** 63 or not 0 < message_id < 2 ** 63:
            raise ValueError()
    except (ValueError, TypeError):
        return jsonify(error="A valid session_id is required."), 400
    content = None
    if request.method == "PATCH":
        content, error = validate_message_input(data.get("content"))
        if error:
            return jsonify(error[0]), error[1]
        from core import safety
        from core.chat import REFUSAL
        if safety.blocks_message(content):
            return jsonify(error=REFUSAL), 422
    from core.turns import get_turn_service
    service = get_turn_service()
    expected_content = data.get('expected_content')
    dataset_epoch = data.get('dataset_epoch')
    expected_owner = data.get('avatar_id')
    if ((expected_content is not None and (not isinstance(expected_content, str) or len(expected_content) > 100000))
            or (dataset_epoch is not None and (not isinstance(dataset_epoch, str) or not dataset_epoch))
            or (expected_owner is not None and (not isinstance(expected_owner, str) or len(expected_owner) > 128))):
        return jsonify(error='Invalid message change context.'), 400
    try:
        for value in (expected_content, dataset_epoch, expected_owner):
            if value is not None:
                value.encode('utf-8')
    except UnicodeError:
        return jsonify(error='Invalid message change context.'), 400
    try:
        # Submission and mutation share this lock so a new turn cannot start during the write.
        with service.lock:
            get_datastore().change_message(message_id, session_id, content=content, delete=request.method == "DELETE",
                expected_content=expected_content, dataset_epoch=dataset_epoch, expected_owner=expected_owner)
    except LookupError as e:
        return jsonify(error=str(e)), 404
    except ValueError as e:
        return jsonify(error=str(e)), 409
    return jsonify(status="ok", knowledge_rebuild_pending=True, **({'content': content} if request.method == 'PATCH' else {}))


@bp.route("/api/messages/<int:message_id>/versions", methods=["GET"])
def message_versions(message_id):
    """The earlier answers kept for one message, oldest first. Each is {version_id, content, current}; `current` marks
    the one showing. A message that has never been regenerated has a single version, its own id."""
    versions = get_datastore().message_versions(message_id)
    if not versions:
        return jsonify(error="Message not found"), 404
    return jsonify(message_id=message_id, versions=versions), 200, {"Cache-Control": "no-store"}


@bp.route("/api/messages/<int:message_id>/versions/<int:version_id>", methods=["POST"])
def show_message_version(message_id, version_id):
    """Show an earlier answer again. The message keeps its id; the chosen text becomes its content, and the text it held
    is kept as another version. Marks companion knowledge for review/rebuild, like a regenerated answer."""
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify(error="Expected a JSON object"), 400
    raw_sid = data.get("session_id", request.args.get("session_id"))
    try:
        if isinstance(raw_sid, bool) or not isinstance(raw_sid, (str, int)):
            raise ValueError()
        session_id = int(raw_sid)
        if not 0 < session_id < 2 ** 63 or not 0 < version_id < 2 ** 63:
            raise ValueError()
    except (ValueError, TypeError):
        return jsonify(error="A valid session_id is required."), 400
    expected_content = data.get("expected_content")
    dataset_epoch = data.get("dataset_epoch")
    expected_owner = data.get("avatar_id")
    if ((expected_content is not None and (not isinstance(expected_content, str) or len(expected_content) > 100000))
            or (dataset_epoch is not None and (not isinstance(dataset_epoch, str) or not dataset_epoch))
            or (expected_owner is not None and (not isinstance(expected_owner, str) or len(expected_owner) > 128))):
        return jsonify(error="Invalid version change context."), 400
    try:
        result = get_datastore().show_message_version(message_id, session_id, version_id,
                                                     expected_content=expected_content,
                                                     dataset_epoch=dataset_epoch, expected_owner=expected_owner)
    except LookupError as error:
        return jsonify(error=str(error)), 404
    except ValueError as error:
        return jsonify(error=str(error)), 409
    return jsonify(status="ok", **result), 200, {"Cache-Control": "no-store"}


def _turn_stream(service, request_id):
    def events():
        row = service.get(request_id)
        yield f"data: {json.dumps({'status': 'accepted', 'request_id': request_id, 'session_id': row['session_id']})}\n\n"
        with owned_iterator(service.events(request_id)) as events:
            for event in events:
                yield f"data: {json.dumps(event)}\n\n"
    return Response(events(), mimetype="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@bp.route("/api/chat/requests/<request_id>", methods=["GET"])
def chat_request_status(request_id):
    from core.turns import get_turn_service
    row = get_turn_service().get(request_id)
    if row is None:
        return jsonify(error="Request not found"), 404
    # Keep source fingerprints, recovery evidence and process IDs internal.
    public = {"request_id", "session_id", "status", "response", "error", "user_message_id", "assistant_message_id",
              "target_message_id", "first_text_ms", "elapsed_ms", "diagnostics", "created_at", "updated_at"}
    return jsonify({k: v for k, v in row.items() if k in public}), 200, {"Cache-Control": "no-store"}


# ============================================================
# Routes — Memory Management (Phase 2)
# ============================================================

@bp.route("/api/memory", methods=["POST"])
def add_memory():
    """Manually add a memory to ChromaDB."""
    data = request.json or {}
    if not isinstance(data, dict):
        return jsonify(error="Expected a JSON object"), 400
    error = _validate_memory(data, require_content=True)
    if error:
        return jsonify(error=error), 400
    from core import safety
    if safety.blocks_message(data['content']):
        return jsonify(error='That content cannot be saved as a memory.'), 422
    avatar_id, error = _memory_owner(data)
    if error:
        return jsonify(error=error), 400
    mem_mgr = get_memory_manager()
    mem_id = _knowledge_manager(mem_mgr).remember(avatar_id, data['content'].strip(), data.get('type', 'fact'),
                                                 float(data.get('confidence', .8)), data.get('source_message_id'), data.get('session_id'))
    return jsonify({"status": "ok", "id": mem_id})


@bp.route("/api/memory/<memory_id>", methods=["PUT"])
def update_memory(memory_id):
    """Update an existing memory."""
    data = request.json or {}
    if not isinstance(data, dict):
        return jsonify(error="Expected a JSON object"), 400
    error = _validate_memory(data)
    if error:
        return jsonify(error=error), 400
    if 'content' in data:
        from core import safety
        if safety.blocks_message(data['content']):
            return jsonify(error='That content cannot be saved as a memory.'), 422
    mem_mgr = get_memory_manager()

    old = mem_mgr.store.collection.get(ids=[memory_id])
    if not old or not old["metadatas"]:
        return jsonify({"error": "Memory not found"}), 404

    owner = (old['metadatas'][0] or {}).get('avatar_id') or ''
    new_owner = None
    if "avatar_id" in data or "session_id" in data:
        new_owner, error = _memory_owner(data)
        if error:
            return jsonify(error=error), 400
    service = _knowledge_manager(mem_mgr)
    if 'content' in data or new_owner is not None:
        return jsonify(service.change(owner, memory_id, content=data['content'].strip() if 'content' in data else None,
                                      confidence=float(data['confidence']) if 'confidence' in data else None, new_owner=new_owner))
    if 'confidence' not in data:
        return jsonify(error='Provide content, confidence or an explicit owner.'), 400
    with service.changing(owner):
        mem_mgr.store.update_memory(memory_id, confidence=float(data['confidence']))
    return jsonify(status='ok')


def _knowledge_manager(manager=None):
    from core.memory import get_memory_writer
    from core.notes import get_notes_service
    return KnowledgeService(get_datastore(), manager or get_memory_manager(), get_notes_service(),
                            writer=get_memory_writer())


@bp.errorhandler(KnowledgeError)
def memory_error(error):
    return jsonify(error=str(error), code='knowledge_error'), error.status


@bp.route('/api/memory', methods=['GET'])
def list_memories():
    owner, error = _memory_owner({'avatar_id': request.args.get('avatar_id')} if 'avatar_id' in request.args else {})
    if error:
        return jsonify(error=error), 400
    return jsonify(_knowledge_manager().inventory(owner)['memories']), 200, {'Cache-Control': 'no-store'}


def _validate_memory(data, require_content=False):
    if require_content or "content" in data:
        content = data.get("content")
        if not isinstance(content, str) or not content.strip():
            return "Memory content must be nonempty text."
        if len(content) > MAX_MESSAGE_LENGTH:
            return "Memory content is too long."
    if "confidence" in data:
        try:
            confidence = float(data["confidence"])
            if isinstance(data["confidence"], bool) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
                raise ValueError()
        except (TypeError, ValueError, OverflowError):
            return "Confidence must be a number between 0 and 1."
    if "type" in data and (not isinstance(data["type"], str) or data["type"] not in {"fact", "preference", "event", "relationship", "emotion"}):
        return "Unknown memory type."
    return None


def _memory_owner(data):
    avatar_id = data.get("avatar_id")
    if avatar_id is not None and not isinstance(avatar_id, str):
        return None, "avatar_id must be text."
    if data.get("session_id") is not None:
        raw = data["session_id"]
        try:
            if isinstance(raw, bool):
                raise ValueError()
            session_info = get_datastore().get_session(int(raw))
        except (ValueError, TypeError):
            return None, "Invalid session_id."
        if not session_info:
            return None, "Session not found."
        owner = session_info.avatar_id or ""
        if avatar_id is not None and avatar_id != owner:
            return None, "The memory and conversation must belong to the same companion."
        return owner, None
    from core.companion import get_services
    avatars = get_services().avatars
    if avatar_id:
        if not avatars.get(avatar_id):
            return None, "Companion not found."
        return avatar_id, None
    return (avatar_id if avatar_id is not None else avatars.active_id()) or "", None


@bp.route("/api/memory/<memory_id>", methods=["DELETE"])
def delete_memory(memory_id):
    """Delete a memory."""
    mem_mgr = get_memory_manager()
    old = mem_mgr.store.collection.get(ids=[memory_id])
    if not old or not old.get('ids'):
        return jsonify(error='Memory not found'), 404
    owner = (old['metadatas'][0] or {}).get('avatar_id') or ''
    return jsonify(_knowledge_manager(mem_mgr).change(owner, memory_id, forget=True))


@bp.route("/api/memory/clear", methods=["POST"])
def clear_all_memories():
    """Clear all memories from ChromaDB. Requires confirmation."""
    data = request.json or {}
    if data.get("confirm") != "yes":
        return jsonify({"error": "Confirmation required: send {\"confirm\": \"yes\"}"}), 400

    mem_mgr = get_memory_manager()
    service = _knowledge_manager(mem_mgr)
    from core.turns import get_turn_service
    owners = {(m.get('metadata') or {}).get('avatar_id') or '' for m in mem_mgr.store.get_all()}
    owners.update(s.avatar_id or '' for s in get_datastore().list_sessions(archived=None))
    with get_turn_service().lock:
        for owner in owners:
            service.clear(owner)
    return jsonify({"status": "ok"})


# ============================================================
# Routes — User Profile (Session 5: wired to core/profile.py)
# ============================================================

@bp.route("/api/profile", methods=["GET"])
def get_profile():
    """Get user profile."""
    from core.profile import load_user_profile
    return jsonify(load_user_profile())


# ============================================================
# Routes — Onboarding (Phase 6 + Session 5 wiring)
# ============================================================

@bp.route("/api/onboarding/status", methods=["GET"])
def onboarding_status():
    """Check if onboarding is complete, and what the person is called."""
    from core.setup import status
    return jsonify(status())


@bp.route("/api/onboarding/complete", methods=["POST"])
def complete_user_onboarding():
    """Finish the first run and keep the name the person gave. A blank name keeps whatever was saved before."""
    from core.profile import complete_onboarding, load_user_profile, save_user_profile
    data = request.json or {}
    profile = load_user_profile()
    name = str(data.get("name") or "").strip()[:50]
    if name:
        profile["name"] = name
    if "voice_enabled" in data:
        profile.setdefault("preferences", {})["voice_enabled"] = data["voice_enabled"]
    save_user_profile(profile)
    complete_onboarding()
    return jsonify({"status": "ok"})


@bp.route("/api/profile/last-session", methods=["PUT"])
def update_last_active_session():
    """Update the last active session ID."""
    data = request.json or {}
    session_id = data.get("session_id")
    if session_id is None:
        return jsonify({"error": "session_id is required"}), 400

    store = get_datastore()
    store.set_last_active_session(session_id)
    return jsonify({"status": "ok"})
