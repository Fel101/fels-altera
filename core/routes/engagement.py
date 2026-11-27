"""Mood detection, voice layer, and proactive engagement routes extracted from altera.py (Session 6: Blueprints).

Routes for mood analysis, TTS/STT voice processing, and proactive engagement.
"""

import logging
from datetime import datetime
from flask import Blueprint, request, jsonify, session

from core.config import get_config
from core.constants import MAX_TTS_CHARS

from core.emotion import emotional_store, analyze_and_store_emotion
from core.profile import load_user_profile, proactive_settings, save_user_profile

LOGGER = logging.getLogger(__name__)

bp = Blueprint('engagement', __name__)


# ============================================================
# Routes — Mood Detection (Phase 4: LLM-powered emotion detection)
# ============================================================

@bp.route("/api/mood", methods=["POST"])
def detect_mood():
    """Detect mood from a message. Phase 4: LLM-powered emotion detection."""
    data = request.json or {}
    text = data.get("text", "")

    sid = data.get("session_id") or session.get('session_id')

    PHASE4_CONFIG = get_config().get('phase4', {})
    EMOTION_DETECTION_ENABLED = PHASE4_CONFIG.get('emotion_detection', True)

    from core import kids
    if not EMOTION_DETECTION_ENABLED or kids.kept_from_model(sid, text):     # kids mode: a restricted message stays local
        return _detect_mood_keyword_based(text, data)

    # Phase 4: LLM-powered emotion detection
    try:
        emotion_data = analyze_and_store_emotion(text, session_id=sid, role="user")

        profile = load_user_profile()
        if "mood_history" not in profile:
            profile["mood_history"] = []
        profile["mood_history"].append({
            "emotion": emotion_data.get("primary_emotion", "neutral"),
            "intensity": emotion_data.get("intensity", 0.5),
            "valence": emotion_data.get("valence", 0.0),
            "timestamp": datetime.now().isoformat(),
            "message_preview": text[:50]
        })
        profile["mood_history"] = profile["mood_history"][-30:]
        save_user_profile(profile)

        return jsonify({
            "emotion": emotion_data.get("primary_emotion", "neutral"),
            "intensity": emotion_data.get("intensity", 0.5),
            "valence": emotion_data.get("valence", 0.0),
            "secondary_emotions": emotion_data.get("secondary_emotions", []),
            "analysis": emotion_data.get("analysis", "")
        })
    except Exception:
        LOGGER.exception("LLM detection failed, falling back to keyword detection")
        return _detect_mood_keyword_based(text, data)


def _detect_mood_keyword_based(text, data):
    """Fallback keyword-based mood detection (Phase 2.5)."""
    text_lower = text.lower()

    positive_words = ["happy", "great", "love", "excited", "good", "amazing", "wonderful", "fantastic", "awesome"]
    negative_words = ["sad", "depressed", "angry", "frustrated", "stressed", "worried", "anxious", "tired", "exhausted"]

    mood = "neutral"
    score = 0

    for word in positive_words:
        if word in text_lower:
            score += 1
    for word in negative_words:
        if word in text_lower:
            score -= 1

    if score > 2:
        mood = "happy"
    elif score < -2:
        mood = "sad"
    elif score < 0:
        mood = "stressed"

    profile = load_user_profile()
    if "mood_history" not in profile:
        profile["mood_history"] = []
    profile["mood_history"].append({
        "emotion": mood,
        "intensity": abs(score) * 0.3,
        "valence": score / 5.0 if score != 0 else 0.0,
        "timestamp": datetime.now().isoformat(),
        "message_preview": data.get("text", "")[:50]
    })
    profile["mood_history"] = profile["mood_history"][-30:]
    save_user_profile(profile)

    return jsonify({"emotion": mood, "intensity": abs(score) * 0.3, "valence": score / 5.0 if score != 0 else 0.0})


@bp.route("/api/mood/trend", methods=["GET"])
def get_mood_trend():
    """Get mood trend over the last N days (Phase 4)."""
    days = request.args.get("days", 7, type=int)

    try:
        trend = emotional_store.get_mood_trend(days=days)
        return jsonify(trend)
    except Exception:
        LOGGER.exception("Trend detection failed")
        return jsonify({"trend": "unknown", "dominant_mood": "neutral", "count": 0})


@bp.route("/api/mood/history", methods=["GET"])
def get_mood_history():
    """Get recent emotional history (Phase 4)."""
    limit = request.args.get("limit", 30, type=int)

    try:
        history = emotional_store.get_emotional_history(limit=limit)
        return jsonify(history)
    except Exception:
        LOGGER.exception("History fetch failed")
        return jsonify([])


# ============================================================
# Routes — Voice Layer (Session 5: wired to core/voice_layer.py)
# ============================================================

@bp.route("/api/voice/status", methods=["GET"])
def get_voice_status():
    """Check if voice layer is enabled and available."""
    VOICE_TTS_ENABLED = get_config().get('voice', {}).get('tts_enabled', False)
    from core import tts as tts_module, voice_service
    tts_cfg = tts_module.settings()
    from core.profile import load_user_profile
    profile = load_user_profile()
    voice_preference = profile.get("preferences", {}).get("voice_enabled", True)
    effective_tts_enabled = bool(VOICE_TTS_ENABLED and voice_preference)
    import importlib.util
    stt_available = importlib.util.find_spec('faster_whisper') is not None
    stt_model = get_config().get('voice', {}).get('stt_model', 'base')
    if stt_available:
        from core.stt import model_status
        stt_model_state = model_status(stt_model)
    else:
        stt_model_state = 'not_installed'
    speech = voice_service.status(tts_cfg, ensure_started=False) if effective_tts_enabled else {'state': 'disabled', 'message': ''}

    return jsonify({
        "enabled": stt_available,
        "tts_enabled": effective_tts_enabled,
        "config_tts_enabled": VOICE_TTS_ENABLED,
        "voice_enabled": bool(voice_preference),
        "stt_available": stt_available,
        "voice_name": tts_module.voice_label(tts_cfg),
        "tts_engine": tts_cfg["engine"],
        "voice_state": speech['state'],
        "voice_message": speech['message'],
        "local_voice_installed": voice_service.installed(tts_cfg),
        "install_hint": voice_service.install_hint(),
        "stt_model": stt_model,
        "stt_model_state": stt_model_state
    })


@bp.get('/api/voice/lab')
def voice_lab_status():
    """Look for ready Voice Lab voices without starting a speech service."""
    from core import tts, voicelab
    cfg = tts.settings()
    info = voicelab.status(cfg['voicelab']['url'])
    selected = cfg['engine'] == 'voicelab'
    if not selected and info['message'] == voicelab.OFFLINE:
        info['message'] = 'Start Voice Lab to use its voices.'
    return jsonify({**info, 'selected': selected}), 200, {'Cache-Control': 'no-store'}


@bp.post('/api/voice/engine')
def select_voice_engine():
    """Select Voice Lab for speech after an explicit action, or switch back to local Kokoro."""
    import copy
    from core import tts, voice_service, voicelab
    from core.config import save_config
    data = request.get_json(silent=True)
    engine = data.get('engine') if isinstance(data, dict) else None
    if engine not in ('voicelab', 'kokoro'):
        return jsonify(error='Choose Voice Lab or Altera’s Kokoro.'), 400
    config = copy.deepcopy(get_config())
    cfg = tts.settings(config)
    if engine == 'voicelab':
        lab = voicelab.status(cfg['voicelab']['url'])
        if not lab['ready']:
            return jsonify(error=lab['message']), 503
    elif not voice_service.is_own_local(cfg):
        return jsonify(error='Choose your configured Kokoro server in config.yaml.'), 400

    voice = config.setdefault('voice', {})
    voice.update(tts_engine=engine, tts_enabled=True)
    if engine == 'kokoro':
        voice['kokoro'] = {**(voice.get('kokoro') or {}), 'autostart': True}
    save_config(config)
    profile = load_user_profile()
    profile.setdefault('preferences', {})['voice_enabled'] = True
    save_user_profile(profile)
    if engine == 'kokoro':
        voice_service.ensure_started_async()
    return jsonify(engine=engine, tts_enabled=True)


@bp.route("/api/voice/toggle", methods=["POST"])
def toggle_voice():
    """Toggle voice playback on/off for the current user."""
    data = request.json or {}
    enabled = data.get("enabled", False)
    if type(enabled) is not bool:
        return jsonify(error="enabled must be true or false"), 400

    session['voice_enabled'] = enabled
    from core.profile import load_user_profile, save_user_profile
    profile = load_user_profile()
    profile.setdefault("preferences", {})["voice_enabled"] = enabled
    save_user_profile(profile)
    if enabled:
        _switch_speech_on()

    return jsonify({"status": "ok", "enabled": enabled})


def _switch_speech_on():
    """The first time speech is switched on in Settings, turn it on in config.yaml too, so nobody has to edit the file.

    For Altera's own local voice or a previously selected Voice Lab, and only when
    voice.tts_enabled is off: a config that already has speech on is left exactly as it is, and an online voice is
    never switched on from here, because it sends every reply away. Autostart is set as well, and the service is
    started if it is installed. Switching off is only the person's preference and never changes config.yaml.
    """
    import copy
    from core import tts as tts_module, voice_service
    from core.config import save_config
    config = copy.deepcopy(get_config())
    voice = config['voice'] = dict(config.get('voice') or {})
    cfg = tts_module.settings(config)
    own_kokoro = cfg['engine'] == 'kokoro' and voice_service.is_own_local(cfg)
    if voice.get('tts_enabled') or not (own_kokoro or cfg['engine'] == 'voicelab'):
        return
    voice['tts_enabled'] = True
    if own_kokoro:
        voice['kokoro'] = {**(voice.get('kokoro') or {}), 'autostart': True}
    save_config(config)
    if own_kokoro and voice_service.installed():
        voice_service.ensure_started_async()


@bp.route("/api/voice/tts", methods=["POST"])
def text_to_speech():
    """Generate speech for given text. Returns audio URL."""
    data = request.json or {}
    text = data.get("text", "").strip()

    if not text:
        return jsonify({"error": "Empty text"}), 400
    if len(text) > MAX_TTS_CHARS:
        return jsonify({"error": f"Text too long to speak (max {MAX_TTS_CHARS} characters)"}), 413

    from core.voice_layer import get_tts_audio
    person_voice = None
    session_id = data.get("session_id")
    chosen = data.get("voice")
    if chosen is not None:                            # a sample of one listed voice (Home's Play a sample)
        from core import pals
        voices = pals.voices()
        if not voices['options'] and voices.get('message'):
            return jsonify(audio_url=None, message=voices['message'])
        if not isinstance(chosen, str) or chosen.strip() not in {o["id"] for o in voices["options"]}:
            return jsonify({"error": "Choose one of the listed voices."}), 400
        person_voice = chosen.strip()
    elif type(session_id) is int and session_id > 0:
        try:
            from core import companions
            person_voice = (companions.avatar_for_session(session_id) or {}).get("voice") or None
        except Exception:
            person_voice = None                       # the configured voice still speaks
    from core import tts, voice_service
    cfg = tts.settings()
    notices = []
    if cfg['engine'] == 'voicelab':
        audio_url = get_tts_audio(text, person_voice=person_voice, notice=notices.append, preview=chosen is not None)
    else:
        audio_url = get_tts_audio(text, person_voice=person_voice) if person_voice else get_tts_audio(text)

    if notices:
        return jsonify(audio_url=audio_url, message=notices[-1])

    if audio_url:
        return jsonify({"audio_url": audio_url})
    try:
        message = voice_service.problem(tts.with_person_voice(cfg, person_voice))
    except Exception:
        message = "The voice could not speak just now. Try again."
    return jsonify({"audio_url": None, "message": message})


@bp.route("/api/voice/stt", methods=["POST"])
def speech_to_text():
    """Receive audio data and transcribe it."""
    if 'file' not in request.files:
        audio_bytes = request.get_data()
        if not audio_bytes:
            return jsonify({"error": "No audio data"}), 400
    else:
        file = request.files['file']
        audio_bytes = file.read()

    from core.voice_layer import process_speech_to_text
    options = {'offline_only': True} if request.form.get('offline_only') == 'true' else {}
    result = process_speech_to_text(audio_bytes, model_size=get_config().get('voice', {}).get('stt_model', 'base'), **options)

    if "text" in result and result["text"]:
        return jsonify({
            "text": result["text"],
            "language": result.get("language", "unknown")
        })
    else:
        return jsonify({"error": result.get("error", "Transcription failed")}), 500


@bp.before_app_request
def bound_voice_activity_input():
    # Registered with this blueprint before backup/state-gate hooks. Reject
    # unsupported JSON without letting the shared epoch hook parse its body.
    if request.endpoint != 'engagement.voice_activity' or request.method != 'POST':
        return None
    from core.voice_activity import MAX_PCM_BYTES, SAMPLE_RATE
    if request.mimetype != 'audio/pcm' or request.headers.get('X-Audio-Sample-Rate') != str(SAMPLE_RATE):
        return jsonify(error='Send audio/pcm at 16000 Hz using X-Audio-Sample-Rate.', code='invalid_voice_activity'), 400
    if request.content_length is not None and request.content_length > MAX_PCM_BYTES:
        return jsonify(error='Speech activity frames may contain at most one second of audio.', code='voice_activity_too_large'), 413
    return None


@bp.after_app_request
def prevent_voice_activity_cache(response):
    if request.endpoint == 'engagement.voice_activity':
        response.headers['Cache-Control'] = 'no-store'
    return response


@bp.route('/api/voice/activity', methods=['POST'])
def voice_activity():
    """Check a short, explicitly owned PCM frame locally without transcribing it."""
    from core import companion, voice_activity as activity
    from core.datastore import get_datastore

    store = get_datastore()
    epoch = store.get_key('dataset_epoch') or {}
    supplied = request.headers.get('X-Altera-Dataset')
    if (epoch.get('required') or supplied is not None) and supplied != epoch.get('value'):
        return jsonify(error="Altera's data was restored. Reload Altera before listening again.", code='dataset_changed'), 409
    raw_sid = request.args.get('session_id', '')
    if not raw_sid.isascii() or not raw_sid.isdecimal() or not 1 <= len(raw_sid) <= 19:
        return jsonify(error='A positive integer session_id is required.', code='invalid_voice_activity'), 400
    sid = int(raw_sid)
    if not 0 < sid <= 9223372036854775807:
        return jsonify(error='A positive integer session_id is required.', code='invalid_voice_activity'), 400
    info = store.get_session(sid)
    if info is None:
        return jsonify(error='Conversation not found.', code='session_missing'), 404
    owner = info.avatar_id or None
    # Never resolve an unowned/deleted conversation through the globally active
    # companion. The browser retains this owner with its original frame context.
    if owner and not companion.get_services().avatars.get(owner):
        return jsonify(error='This conversation companion is unavailable.', code='session_missing'), 404
    if request.mimetype != 'audio/pcm' or request.headers.get('X-Audio-Sample-Rate') != str(activity.SAMPLE_RATE):
        return jsonify(error='Send audio/pcm at 16000 Hz using X-Audio-Sample-Rate.', code='invalid_voice_activity'), 400
    if request.content_length is not None and request.content_length > activity.MAX_PCM_BYTES:
        return jsonify(error='Speech activity frames may contain at most one second of audio.', code='voice_activity_too_large'), 413
    audio = request.stream.read(activity.MAX_PCM_BYTES + 1)
    if len(audio) > activity.MAX_PCM_BYTES:
        return jsonify(error='Speech activity frames may contain at most one second of audio.', code='voice_activity_too_large'), 413
    try:
        result = activity.detect_pcm16(audio)
    except activity.VoiceActivityError as error:
        failure = {'error': str(error), 'code': error.code}
        if error.status == 503:
            failure['available'] = False
        return jsonify(failure), error.status
    return jsonify(**result, session_id=sid, avatar_id=owner)


# ============================================================
# Routes — Proactive Engagement (check-in)
# ============================================================

@bp.route("/api/proactive/settings", methods=["GET"])
def get_proactive_settings():
    """Get proactive engagement settings (Phase 4)."""
    return jsonify(proactive_settings())


@bp.route("/api/proactive/settings", methods=["POST"])
def set_proactive_settings():
    """Update proactive engagement settings (Phase 4)."""
    data = request.json or {}

    profile = load_user_profile()
    if "config" not in profile:
        profile["config"] = {}
    if "phase4" not in profile["config"]:
        profile["config"]["phase4"] = {}

    if "enabled" in data:
        profile["config"]["phase4"]["proactive_enabled"] = bool(data["enabled"])
    if "interval_hours" in data:
        profile["config"]["phase4"]["proactive_interval_hours"] = max(1, min(48, int(data["interval_hours"])))

    save_user_profile(profile)

    return jsonify({"status": "ok"})


@bp.route("/api/proactive/checkin", methods=["GET"])
def proactive_checkin():
    """Compatibility for old tabs: polling never runs inference or inserts speech."""
    return jsonify(message=None, type='inbox', has_followups=False,
                   inbox_url='/api/presence/inbox', requires_opt_in=True)
