"""
Altera — Text-to-Speech (TTS)

Engines chosen by voice.tts_engine in config.yaml:

  kokoro  A local voice (Kokoro, e.g. af_heart) made by Altera's voice service in voice/ (installed separately with
          voice/setup.bat or voice/setup.sh), which Altera starts itself (core/voice_service.py). The default endpoint is
          loopback; an explicitly configured remote URL receives speech text.
  edge    Microsoft's online read-aloud service through edge-tts. Every reply is sent to Microsoft to be spoken.
  voicelab  A ready Cast or preset voice from Voice Lab, using its WAV endpoint.

With the kokoro engine, when the local voice is down Altera stays quiet, unless voice.cloud_fallback is true. Even then the cloud
voice is never used while adult mode is on.

Audio files are cached in the instance's data/cache/tts under a name that includes the engine, voice, speed and text, so
changing the voice never replays an old one. Files older than 7 days are deleted (speech is made again on demand).
"""

from core.paths import data_path
from core.private_paths import temporary_file
import logging
import asyncio
import hashlib
import json
import os
import re
import threading
import time
import wave
from pathlib import Path
from urllib.parse import urlsplit

from core import voice_http, voicelab


LOGGER = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
AUDIO_CACHE_DIR = str(data_path('cache', 'tts'))
CACHE_MAX_AGE_DAYS = 7          # a local voice writes WAV (about 50 KB a second), so the cache is kept small

DEFAULT_VOICE = "en-US-AriaNeural"          # the edge voice
DEFAULT_KOKORO_VOICE = "af_heart"
DEFAULT_KOKORO_URL = "http://127.0.0.1:8770"
MAX_CLIP_CHARS = 400                        # longest text sent to the voice in one go
MAX_WAV_BYTES = 16 * 1024 * 1024             # bound provider output before publishing any cache file
DOWN_RETRY_S = 15                           # after a failed call, skip the local voice this long before trying again


class TTSError(Exception):
    """The voice service answered, but could not make the speech."""


class TTSUnavailable(TTSError):
    """The local voice service cannot be reached (not started yet, still loading, or stopped)."""


# ============================================================
# Settings
# ============================================================

def clear_cache():
    """Erase generated speech, including the legacy instance-local cache; errors must be reported."""
    from core.paths import root
    for directory in {Path(AUDIO_CACHE_DIR), root() / 'static/audio/tts'}:
        if directory.exists():
            for path in directory.iterdir():
                if path.is_file() or path.is_symlink():
                    path.unlink()


def settings(config=None):
    """The voice settings from config.yaml with every default filled in. Read fresh each time (the file is tiny)."""
    if config is None:
        from core.config import get_config
        config = get_config()
    voice = config.get("voice") or {}
    kokoro = voice.get("kokoro") or {}
    lab = voice.get("voicelab") or {}
    engine = str(voice.get("tts_engine") or "edge").strip().lower()
    if engine not in ("kokoro", "edge", "voicelab"):
        engine = "edge"
    try:
        speed = min(2.0, max(0.5, float(kokoro.get("speed", 1.0))))
    except (TypeError, ValueError):
        speed = 1.0
    return {
        "engine": engine,
        "edge_voice": voice.get("voice_name") or DEFAULT_VOICE,
        "cloud_fallback": engine == "kokoro" and bool(voice.get("cloud_fallback", False)),
        "voicelab": {"url": str(lab.get("url") or "").strip().rstrip("/"),
                     "voice": str(lab.get("voice") or "").strip()},
        "kokoro": {
            "voice": str(kokoro.get("voice") or DEFAULT_KOKORO_VOICE),
            "speed": speed,
            "url": str(kokoro.get("url") or DEFAULT_KOKORO_URL).rstrip("/"),
            "autostart": bool(kokoro.get("autostart", True)),
            "python": kokoro.get("python") or None,
            "script": kokoro.get("script") or None,
            "timeout_s": 120,
        },
    }


def voice_label(cfg=None):
    """A short name for the voice in use, for the status line."""
    cfg = cfg or settings()
    if cfg["engine"] == "voicelab":
        name = cfg["voicelab"]["voice"]
        return f"Voice Lab · {name}" if name else "Voice Lab"
    if cfg["engine"] == "kokoro":
        try:
            local = urlsplit(cfg['kokoro'].get('url', DEFAULT_KOKORO_URL)).hostname in {'127.0.0.1', 'localhost', '::1'}
        except ValueError:
            local = False
        return f"Kokoro {cfg['kokoro']['voice']} ({'local' if local else 'configured server'})"
    return f"{cfg['edge_voice']} (Microsoft cloud)"


# ============================================================
# Making the text speakable, and cutting it into pieces
# ============================================================

_EMOJI = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF\U00002190-\U000021FF"
                    "\U00002300-\U000023FF︎️‍⃣]")
_PHOTO_TAG = re.compile(r"\[\[.*?\]\]", re.S)
_LINK = re.compile(r"\[([^\]\n]+)\]\((?:[^)\s]+)\)")
_CUE = re.compile(r"\[[^\[\]\n]{1,40}\](?!\()")             # [laughs]
_URL = re.compile(r"https?://\S+|www\.\S+")
_BOLD = re.compile(r"\*\*(.+?)\*\*", re.S)
_STAR = re.compile(r"(?<![*\w])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![*\w])")
_UNDERSCORE = re.compile(r"(?<!\w)_([^_\n]+)_(?!\w)")
_LINE_MARK = re.compile(r"(?m)^[ \t]*(?:[-•]|#{1,6}|>)[ \t]+")
_ABBREV = {"mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc"}
# One-word stage directions. A single word between asterisks is emphasis ("I *really* mean it") unless it is one of these
# or stands at the start or end of a sentence.
_ACTION_WORDS = {
    "laughs", "laughing", "giggles", "giggling", "chuckles", "chuckling", "smiles", "smiling", "grins", "grinning",
    "smirks", "beams", "winks", "blushes", "blushing", "nods", "shrugs", "sighs", "sighing", "gasps", "hums", "whispers",
    "pauses", "blinks", "yawns", "stretches", "sniffles", "sobs", "cries", "snorts", "coughs", "frowns", "pouts",
    "squeals", "shivers", "trembles", "hugs", "kisses", "leans", "bites", "moans", "purrs",
}
_BOUNDARY = re.compile("([.!?…]+[\"'”’)\\]]*)(\\s+)(?=[A-Z0-9\"“‘'(\\[*\U0001F000-\U0001FAFF☀-➿])")


def _star_span(match):
    """*smiles softly* is a stage direction and is not spoken; an inline *really* keeps its word."""
    inner = match.group(1).strip()
    before = match.string[:match.start()]
    after = match.string[match.end():]
    starts_sentence = re.search(r"(?:^|[.!?…\n])\s*$", before) is not None
    ends_sentence = re.match(r"\s*(?:[.!?…,;:]|$)", after) is not None
    if len(inner.split()) >= 2 or starts_sentence or ends_sentence or inner.lower() in _ACTION_WORDS:
        return " "
    return inner


def clean_for_speech(text):
    """Strip what a voice would read out loud by mistake: emoji, markdown, links, stage directions, photo tags."""
    if not text:
        return ""
    text = _PHOTO_TAG.sub(" ", text)
    text = _LINK.sub(r"\1", text)
    text = _URL.sub(" ", text)
    text = _BOLD.sub(r"\1", text)
    text = _STAR.sub(_star_span, text)
    text = _UNDERSCORE.sub(r"\1", text)
    text = _CUE.sub(" ", text)
    text = _EMOJI.sub("", text)
    text = text.replace("~~", "")
    text = re.sub(r"[*`]+", "", text)
    text = _LINE_MARK.sub("", text)
    text = re.sub(r"([^.!?…:;,\s])[ \t]*\n+", r"\1. ", text)      # a line that ends without punctuation is a sentence end
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([.,!?;:…])", r"\1", text)
    text = text.strip()
    return text if re.search(r"\w", text) else ""


def split_sentences(text):
    """Split the finished sentences off the front of text. Returns (sentences, remainder).

    A sentence counts as finished once the next one has started (a capital letter, digit or quote follows the full stop), so
    "Come here... sit with me." stays whole and streamed text is never cut in the middle of a sentence.
    """
    sentences, start = [], 0
    for m in _BOUNDARY.finditer(text):
        word = re.search(r"(\S+)$", text[start:m.start(1)])
        w = word.group(1).strip("\"'“‘([").rstrip(".") if word else ""
        if m.group(1).startswith(".") and (w.lower() in _ABBREV or (len(w) == 1 and w.isupper())):
            continue                                            # Dr. Smith, J. Smith
        sentences.append(text[start:m.end(1)].strip())
        start = m.end()
    return sentences, text[start:]          # not stripped: a streamed piece may end right after a full stop and a space


def hard_split(sentence, limit=MAX_CLIP_CHARS):
    """Cut a sentence that is longer than limit at its last comma or space before the limit."""
    pieces = []
    while len(sentence) > limit:
        cut = max(sentence.rfind(", ", 0, limit), sentence.rfind("; ", 0, limit), sentence.rfind(" ", 0, limit))
        if cut <= 0:
            cut = limit
        pieces.append(sentence[:cut + 1].strip())
        sentence = sentence[cut + 1:].strip()
    if sentence:
        pieces.append(sentence)
    return pieces


def chunk_for_speech(text, limit=MAX_CLIP_CHARS):
    """Clean text and pack its sentences into pieces of at most limit characters."""
    cleaned = clean_for_speech(text)
    if not cleaned:
        return []
    sentences, rest = split_sentences(cleaned)
    if rest.strip():
        sentences.append(rest.strip())
    chunks, current = [], ""
    for sentence in sentences:
        for piece in hard_split(sentence, limit):
            if current and len(current) + 1 + len(piece) > limit:
                chunks.append(current)
                current = piece
            else:
                current = f"{current} {piece}".strip()
    if current:
        chunks.append(current)
    return chunks


# ============================================================
# The audio cache
# ============================================================

_cache_lock = threading.Lock()
_pruned = False


def _cache_dir():
    os.makedirs(AUDIO_CACHE_DIR, exist_ok=True)
    return AUDIO_CACHE_DIR


def _cache_path(engine, voice, extra, text, ext):
    key = hashlib.sha1(f"{engine}\x1f{voice}\x1f{extra}\x1f{text}".encode()).hexdigest()[:20]
    return os.path.join(_cache_dir(), key + ext)


def prune_cache(max_age_days=CACHE_MAX_AGE_DAYS, directory=None):
    """Delete cached speech older than max_age_days. Returns how many files went."""
    directory = directory or AUDIO_CACHE_DIR
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    try:
        entries = list(os.scandir(directory))
    except OSError:
        return 0
    for entry in entries:
        try:
            if entry.is_file() and entry.name.lower().endswith((".wav", ".mp3")) and entry.stat().st_mtime < cutoff:
                os.remove(entry.path)
                removed += 1
        except OSError:
            pass
    return removed


def _maybe_prune():
    global _pruned
    with _cache_lock:
        if _pruned:
            return
        _pruned = True
    prune_cache()


def _write_atomic(path, data):
    # Atomic publication requires the cache and instance temporary directory to
    # share a volume. Never fall back to copying private audio across volumes.
    with temporary_file(data, prefix='tts-', suffix='.wav') as tmp:
        os.replace(tmp, path)
    _maybe_prune()


def _concat_wavs(paths):
    """One WAV file made of several, in order (all clips come from the same voice, so their format matches)."""
    key = hashlib.sha1("|".join(os.path.basename(p) for p in paths).encode("utf-8")).hexdigest()[:20]
    out = os.path.join(_cache_dir(), key + ".wav")
    if os.path.exists(out):
        return out
    frames, fmt = [], None
    for p in paths:
        with wave.open(p, "rb") as w:
            this = (w.getnchannels(), w.getsampwidth(), w.getframerate())
            if fmt is None:
                fmt = this
            elif this != fmt:
                raise TTSError("clips have different audio formats")
            frames.append(w.readframes(w.getnframes()))
    with temporary_file(prefix='tts-', suffix='.wav') as tmp:
        with wave.open(str(tmp), "wb") as o:
            o.setnchannels(fmt[0])
            o.setsampwidth(fmt[1])
            o.setframerate(fmt[2])
            for chunk in frames:
                o.writeframes(chunk)
        os.replace(tmp, out)
    return out


# ============================================================
# The edge engine (Microsoft's online service)
# ============================================================

async def generate_speech_async(text, voice_name=DEFAULT_VOICE, rate="+0%"):
    """Speech from edge-tts (Microsoft's servers). Returns the path of the cached MP3."""
    path = _cache_path("edge", voice_name, rate, text, ".mp3")
    if os.path.exists(path):
        return path
    import edge_tts
    communicate = edge_tts.Communicate(text, voice_name, rate=rate)
    with temporary_file(prefix='tts-', suffix='.mp3') as tmp:
        await communicate.save(str(tmp))
        os.replace(tmp, path)
    _maybe_prune()
    return path


def generate_speech(text, voice_name=DEFAULT_VOICE, rate="+0%"):
    """Sync wrapper for generate_speech_async."""
    return asyncio.run(generate_speech_async(text, voice_name, rate))


async def list_available_voices():
    """List the English female edge-tts voices. Useful for discovery."""
    try:
        import edge_tts
        result = await edge_tts.list_voices()
        return [v for v in result if v['Locale'] == 'en-US' and v['Gender'] == 'Female']
    except Exception as error:
        LOGGER.warning("The cloud voice list could not be fetched (%s)", type(error).__name__)
        return []


# ============================================================
# The kokoro engine (the local voice service)
# ============================================================

_state_lock = threading.Lock()
_down_until = 0.0
_last_outage_note = 0.0
_service_ready = False        # true once a clip has been made; until then the service is asked whether it has loaded


def _reset_state():
    """Forget everything learned about the service (for tests)."""
    global _down_until, _last_outage_note, _pruned, _service_ready
    with _state_lock:
        _down_until = 0.0
        _last_outage_note = 0.0
        _service_ready = False
    _pruned = False


def _mark_down(reason, retry_s=DOWN_RETRY_S):
    global _down_until, _last_outage_note, _service_ready
    now = time.time()
    with _state_lock:
        _down_until = now + retry_s
        _service_ready = False
        note = now - _last_outage_note > 60
        if note:
            _last_outage_note = now
    if note:
        LOGGER.warning(f"The local voice is not available ({reason}). Altera stays quiet until it is.")


def _mark_up():
    global _down_until, _last_outage_note, _service_ready
    with _state_lock:
        _down_until = 0.0
        _last_outage_note = 0.0
        _service_ready = True


def _kokoro_wav(text, cfg):
    from core.voice_service import reserve_speech
    k = cfg["kokoro"]
    body = json.dumps({"engine": "kokoro", "text": text, "voice": k["voice"], "speed": k["speed"]}).encode("utf-8")
    finished = reserve_speech()
    try:
        return voice_http.read(k['url'], '/api/speak', data=body, timeout=k['timeout_s'], limit=MAX_WAV_BYTES)
    except voice_http.VoiceHTTPUnavailable as exc:
        raise TTSUnavailable(str(exc)) from None
    except voice_http.VoiceHTTPError as exc:
        raise TTSError(str(exc)) from None
    finally:
        finished()


def _kokoro_clip(text, cfg):
    """Path of the WAV for text (already clean and at most MAX_CLIP_CHARS long). Raises TTSUnavailable if the service is down."""
    k = cfg["kokoro"]
    path = _cache_path("kokoro", k["voice"], k["speed"], text, ".wav")
    if os.path.exists(path):
        return path
    if time.time() < _down_until:
        raise TTSUnavailable("the voice service is down; will try again shortly")
    try:
        if not _service_ready:
            # Altera never waits for a voice that is not ready: a reply made while it loads is text only.
            from core import voice_service
            data = voice_service.info(cfg)
            if data is None:
                raise TTSUnavailable("cannot reach the voice service")
            if "kokoro" not in (data.get("loaded") or []):
                _mark_down("the voice is still loading", retry_s=3)
                raise TTSUnavailable("the voice is still loading")
        wav = _kokoro_wav(text, cfg)
    except TTSUnavailable as exc:
        if _down_until <= time.time():                          # not already marked as "still loading"
            _mark_down(exc)
        try:
            from core import voice_service
            voice_service.ensure_started_async(cfg)
        except Exception:
            pass
        raise
    _mark_up()
    _write_atomic(path, wav)
    return path


# ============================================================
# Entry points
# ============================================================

def cloud_allowed(allow_cloud=None):
    """May this reply be sent to Microsoft's cloud voice? Not while adult mode is on. Any doubt means no."""
    if allow_cloud is not None:
        return bool(allow_cloud)
    try:
        from core import adult
        return adult.effective_level() == "off"
    except Exception:
        return False


def with_person_voice(cfg, person_voice):
    """The settings with this person's own voice (a pal's choice) in place of the configured one. A voice that does not
    belong to the chosen engine is ignored, so changing the engine in Settings never breaks speech."""
    from core.pals import valid_voice
    if not person_voice or not valid_voice(person_voice, cfg["engine"]):
        return cfg
    if cfg["engine"] == "kokoro":
        return {**cfg, "kokoro": {**cfg["kokoro"], "voice": person_voice}}
    if cfg["engine"] == "voicelab":
        return {**cfg, "voicelab": {**cfg["voicelab"], "voice": person_voice}}
    return {**cfg, "edge_voice": person_voice}


def _lab_clips(chunks, cfg, person_voice, *, preview=False):
    lab = cfg['voicelab']
    found = voicelab.catalogue(lab['url'])
    voices = found.voices
    if not voices:
        raise voicelab.LabUnavailable(voicelab.NO_VOICES)
    names = {row['id'] for row in voices}
    if preview and person_voice not in names:
        raise voicelab.LabError('That voice is no longer ready in Voice Lab. Choose another voice.')
    chosen = next((name for name in (person_voice, lab['voice']) if name in names), voicelab.default_voice(voices))
    paths = []
    for chunk in chunks:
        path = _cache_path('voicelab', chosen, (found.url, found.library), chunk, '.wav')
        if not os.path.exists(path):
            data = voicelab.wav(found.url, chosen, chunk, limit=MAX_WAV_BYTES)
            _write_atomic(path, data)
        paths.append(path)
    return paths


def _lab_or_kokoro(chunks, cfg, person_voice, notice, preview):
    from core import voice_service
    try:
        return _lab_clips(chunks, cfg, person_voice, preview=preview)
    except voicelab.LabUnavailable as error:
        local = {**cfg, 'engine': 'kokoro'}
        if not preview and voice_service.kokoro_fallback_ready(local):
            try:
                paths = [_kokoro_clip(chunk, local) for chunk in chunks]
            except TTSError:
                pass
            else:
                if notice:
                    notice(voicelab.FALLBACK)
                return paths
        raise TTSUnavailable(str(error)) from None
    except voicelab.LabError as error:
        raise TTSUnavailable(str(error)) from None


def synthesize_long(text, allow_cloud=None, voice_name=None, person_voice=None, *, notice=None, preview=False):
    """Path of one audio file that speaks text (any length), or None if there is nothing to say.

    person_voice is the voice of whoever is speaking (empty: the configured voice). Raises TTSError when the chosen engine
    cannot make it (and the cloud fallback is off or not allowed).
    """
    cfg = settings()
    chunks = chunk_for_speech(text)
    if not chunks:
        return None
    if cfg['engine'] == 'voicelab':
        try:
            paths = _lab_or_kokoro(chunks, cfg, person_voice, notice, preview)
        except TTSUnavailable as error:
            if notice:
                notice(str(error))
            raise
        return paths[0] if len(paths) == 1 else _concat_wavs(paths)
    cfg = with_person_voice(cfg, person_voice)
    if cfg["engine"] == "edge":
        if not cloud_allowed(allow_cloud):
            raise TTSUnavailable("Microsoft speech is unavailable while adult mode is on.")
        return generate_speech(" ".join(chunks), voice_name=voice_name or cfg["edge_voice"])
    try:
        paths = [_kokoro_clip(chunk, cfg) for chunk in chunks]
    except TTSUnavailable:
        if cfg["cloud_fallback"] and cloud_allowed(allow_cloud):
            LOGGER.warning("Using the Microsoft cloud voice for this reply because the local voice is unavailable.")
            return generate_speech(" ".join(chunks), voice_name=cfg["edge_voice"])
        raise
    return paths[0] if len(paths) == 1 else _concat_wavs(paths)
