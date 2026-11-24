"""
Altera — Voice Layer (TTS/STT)

Owns all text-to-speech and speech-to-text operations.
altera.py routes call these functions instead of inline imports + logic.

get_tts_audio() makes one audio file for a piece of text. SpeechStream makes a reply audible while it is still being
written: it cuts the streamed text into sentences and turns each into a clip, in order.
"""

import concurrent.futures
import logging
import os
import time

from core.serial_jobs import OwnedSerialExecutor

LOGGER = logging.getLogger(__name__)


def get_tts_audio(text: str, voice_name: str = None, allow_cloud: bool = None, person_voice: str = None,
                  *, notice=None, preview=False) -> str | None:
    """Speech for text as a URL under /static/audio/tts, or None (nothing to say, or the voice is unavailable).

    voice_name only matters for the edge (Microsoft cloud) engine. allow_cloud says whether the cloud voice may be used as a
    fallback for this text; None means "ask adult mode" (never allowed while it is on).
    """
    from core import tts
    try:
        options = {'notice': notice, 'preview': preview} if notice or preview else {}
        path = tts.synthesize_long(text, allow_cloud=allow_cloud, voice_name=voice_name, person_voice=person_voice, **options)
    except tts.TTSUnavailable:
        return None                                   # core.tts has already said so, once
    except Exception:
        LOGGER.exception("Error generating speech")
        return None
    if not path:
        return None
    return "/static/audio/tts/" + os.path.basename(path)


class SpeechStream:
    """Turns a reply that is still being written into audio clips, so Altera can start on the first sentence.

    feed(text)      take the next streamed piece of the reply
    ready()         URLs of the clips finished so far, in order (never blocks)
    finish()        the reply is complete: make clips of whatever is left
    drain_iter()    wait for the remaining clips and yield each URL as soon as its clip is ready (drain() returns a list)
    close()         stop (cancels clips not started yet)

    One worker makes the clips one after the other, so they come out in reading order and the speech engine is not asked for
    two things at once. If a clip cannot be made, the rest of that reply stays silent: speaking on after a gap (or playing
    some later clip that happened to be cached) would be worse than stopping. The text is on screen either way.
    """

    FIRST_MIN_CHARS = 25       # the first clip starts as soon as one finished sentence has this much speakable text
    MIN_CHARS = 90             # later clips gather sentences up to about this much (fewer, smoother clips)

    def __init__(self, allow_cloud=None, synth=None, cancel_event=None, person_voice=None, voice_notices=False):
        self._allow_cloud = allow_cloud
        self._person_voice = person_voice
        self._synth = synth
        self._voice_notices = voice_notices
        self.notice = None
        self._cancel_event = cancel_event
        self._buffer = ""
        self._pending = []                 # finished sentences not yet handed to the worker
        self._sent_any = False
        self._futures = []
        self._next = 0
        self._closed = False
        self._failed = False               # a clip could not be made: nothing after it is spoken
        self._pool = OwnedSerialExecutor(name='altera-speech-worker')

    # -- input ---------------------------------------------------------------------------------------------------------

    def feed(self, text):
        if self._closed or self._cancelled() or not text:
            return
        try:
            from core import tts
            sentences, self._buffer = tts.split_sentences(self._buffer + text)
            self._pending.extend(sentences)
            self._flush(final=False)
        except Exception:
            LOGGER.exception("speech stream: feed failed")

    def finish(self):
        if self._closed or self._cancelled():
            return
        try:
            rest, self._buffer = self._buffer.strip(), ""
            if rest:
                self._pending.append(rest)
            self._flush(final=True)
        except Exception:
            LOGGER.exception("speech stream: finish failed")

    def _flush(self, final):
        from core import tts
        while self._pending:
            if not self._sent_any:
                # The first clip takes as few sentences as it needs to reach FIRST_MIN_CHARS, however much text arrived,
                # so the first sound comes as soon as possible.
                take = 0
                for count in range(1, len(self._pending) + 1):
                    if len(tts.clean_for_speech(" ".join(self._pending[:count]))) >= self.FIRST_MIN_CHARS:
                        take = count
                        break
                if not take:
                    if not final:
                        return
                    take = len(self._pending)
                group, self._pending = self._pending[:take], self._pending[take:]
                self._submit(" ".join(group))
                continue
            if not final and len(tts.clean_for_speech(" ".join(self._pending))) < self.MIN_CHARS:
                return
            group, self._pending = self._pending, []
            self._submit(" ".join(group))

    def _submit(self, text):
        from core import tts, voice_service
        if self._failed or self._closed or self._cancelled():
            return
        for piece in tts.chunk_for_speech(text):
            from core.state_gate import STATE
            lease = STATE.enter('altera-speech')
            try:
                finished = voice_service.reserve_speech()
                try:
                    future = self._pool.submit(self._make_clip, piece)
                except BaseException:
                    finished()
                    raise
                # close(wait=False) can outlive the reply. Keep cleanup blocked until
                # the actual writer finishes; cancellation also runs this callback.
                future.add_done_callback(lambda finished, owned=lease: owned.release())
                future.add_done_callback(lambda future, release=finished: release())
                self._futures.append(future)
            except BaseException:
                lease.release()
                raise
            self._sent_any = True

    def _make_clip(self, text):
        if self._cancelled():
            return None
        if self._synth is not None:
            return self._synth(text)
        if self._voice_notices:
            return get_tts_audio(text, allow_cloud=self._allow_cloud, person_voice=self._person_voice,
                                 notice=self._record_notice)
        if self._person_voice:
            return get_tts_audio(text, allow_cloud=self._allow_cloud, person_voice=self._person_voice)
        return get_tts_audio(text, allow_cloud=self._allow_cloud)      # looked up now, so tests can replace it

    def _record_notice(self, message):
        if self.notice is None:
            self.notice = message

    # -- output --------------------------------------------------------------------------------------------------------

    def _collect(self):
        if self._closed or self._cancelled():
            self.close()
            return []
        urls = []
        while self._next < len(self._futures) and self._futures[self._next].done():
            future = self._futures[self._next]
            self._next += 1
            try:
                url = future.result()
            except Exception:
                LOGGER.exception("speech stream: a clip failed")
                url = None
            if not url:
                self._failed = True
            if url and not self._failed:
                urls.append(url)
        return urls

    def ready(self):
        return self._collect()

    def drain_iter(self, timeout=30.0):
        """Yield the URLs of the remaining clips, each as soon as it is finished (in order). Gives up after timeout."""
        deadline = time.monotonic() + timeout
        yield from self._collect()
        while self._next < len(self._futures):
            if self._closed or self._cancelled():
                self.close()
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                LOGGER.warning("speech stream: gave up waiting for %d clip(s)", len(self._futures) - self._next)
                return
            # Do not hold the turn open for a running synthesizer after Stop.
            # Its state lease still protects the file writer until it exits.
            concurrent.futures.wait([self._futures[self._next]], timeout=min(remaining, .05))
            yield from self._collect()

    def drain(self, timeout=30.0):
        return list(self.drain_iter(timeout))

    def close(self):
        self._closed = True
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _cancelled(self):
        return bool(self._cancel_event is not None and self._cancel_event.is_set())


def process_speech_to_text(audio_bytes: bytes, model_size: str = "base", offline_only=False) -> dict:
    """Process uploaded audio via STT. Returns dict with 'text' key."""
    try:
        from core.stt import transcribe_from_bytes
        result = (transcribe_from_bytes(audio_bytes, model_size=model_size, offline_only=True) if offline_only else
                  transcribe_from_bytes(audio_bytes, model_size=model_size))
        return result
    except Exception:
        LOGGER.exception("Speech-to-text failed")
        return {"text": "", "error": "Speech recognition failed. Please try again."}
