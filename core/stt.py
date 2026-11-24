"""
Altera — Speech-to-Text (STT) Module

Uses faster-whisper for local speech recognition. Accepts audio files
and returns transcribed text.
"""

import logging
from pathlib import Path
import threading

LOGGER = logging.getLogger(__name__)

# What the caller is told when transcription fails. The exception text carries file paths and library internals, and
# routes/engagement.py hands this straight back to the browser, so the detail stays in the log.
TRANSCRIPTION_FAILED = "Could not transcribe the audio. Check that the local speech model is installed."

# Loading a Whisper model takes seconds and a few hundred MB. The model was being constructed inside
# transcribe_audio(), so every spoken message paid that cost again — the comment there said "cached after", and it
# was not. One model per size, built once, reused for the life of the process.
_models = {}
_models_lock = threading.RLock()
_active = {}


def _local_model_path(model_size):
    """Resolve cached weights without allowing Whisper's tokenizer fallback to download."""
    path = Path(model_size)
    if not path.is_dir():
        from faster_whisper.utils import download_model
        path = Path(download_model(model_size, local_files_only=True))
    # faster-whisper otherwise calls Tokenizer.from_pretrained without the
    # local_files_only option, even when its model was loaded offline.
    if not (path / 'tokenizer.json').is_file():
        raise FileNotFoundError('The local speech model is missing its tokenizer.')
    return str(path)


def model_status(model_size):
    """Inspect local speech assets without loading a model or contacting a provider."""
    if not isinstance(model_size, str) or not model_size:
        return 'unavailable'
    if model_size in _models:
        return 'loaded'
    try:
        path = Path(_local_model_path(model_size))
        return 'cached' if all((path / name).is_file() for name in ('model.bin', 'config.json')) else 'unavailable'
    except Exception:
        # Status is advisory; do not expose local paths or library exceptions.
        return 'unavailable'


def _get_model(model_size, offline_only=True):
    """The WhisperModel for a size, loaded on first use and kept."""
    model = _models.get(model_size)
    if model is None:
        with _models_lock:
            model = _models.get(model_size)
            if model is None:
                from faster_whisper import WhisperModel
                options = {'local_files_only': True} if offline_only else {}
                model = WhisperModel(_local_model_path(model_size), device="cpu", compute_type="int8", **options)
                _models[model_size] = model
    return model


def reset_models():
    """Drop the cached models (tests, and a deliberate reload)."""
    with _models_lock:
        _models.clear()


def unload_gpu_models():
    """Drop cached GPU recognizers only, and report busy if one is transcribing."""
    with _models_lock:
        gpu = [size for size, model in _models.items()
               if str(getattr(getattr(model, "model", None), "device", "")).startswith("cuda")]
        if any(_active.get(id(_models[size])) for size in gpu):
            return False
        for size in gpu:
            del _models[size]
        return True


def transcribe_audio(audio_path, model_size="base", language=None, offline_only=True):
    """
    Transcribe an audio file using faster-whisper.

    Args:
        audio_path: Path to the audio file (WAV/MP3/WebM)
        model_size: Whisper model size ('tiny', 'base', 'small', 'medium', 'large')
        language: Language code (e.g., 'en'). None = auto-detect.

    Returns:
        Dict with 'text' key containing the transcription, or error message
    """
    try:
        with _models_lock:
            model = _get_model(model_size, offline_only=True) if offline_only else _get_model(model_size)
            key = id(model)
            _active[key] = _active.get(key, 0) + 1
        try:
            segments, info = model.transcribe(
                audio_path,
                language=language,
                beam_size=5,
                vad_filter=True  # Voice activity detection for cleaner results
            )
            text = "".join(segment.text for segment in segments)
        finally:
            del model
            with _models_lock:
                _active[key] -= 1
                if not _active[key]:
                    del _active[key]
        return {
            "text": text.strip(),
            "language": info.language,
            "confidence": info.language_probability
        }

    except Exception:
        LOGGER.exception("Error transcribing audio")
        return {"text": "", "error": TRANSCRIPTION_FAILED}


def transcribe_from_bytes(audio_bytes, model_size="base", language=None, offline_only=True):
    """
    Transcribe audio from raw bytes (for in-memory processing).

    Args:
        audio_bytes: Raw audio data (WebM/Opus from browser MediaRecorder)
        model_size: Whisper model size
        language: Language code

    Returns:
        Dict with 'text' key or error message
    """
    try:
        from core.private_paths import temporary_file
        # Keep the plaintext recording inside this instance. The context closes
        # the writer before Whisper reopens it and cleans up on every exit.
        with temporary_file(audio_bytes, prefix='stt-', suffix='.webm') as temp_path:
            return (transcribe_audio(str(temp_path), model_size, language, offline_only=True) if offline_only else
                    transcribe_audio(str(temp_path), model_size, language))

    except Exception:
        LOGGER.exception("Error transcribing from bytes")
        return {"text": "", "error": TRANSCRIPTION_FAILED}


# Default model config
DEFAULT_MODEL = "base"
