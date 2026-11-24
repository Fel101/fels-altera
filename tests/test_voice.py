"""Altera's voice: making text speakable, cutting it into clips, the local voice service, the cloud-voice rules, and the speech
events in the chat stream.

No GPU, LLM, network or real voice service is used. The local service is a tiny fake on 127.0.0.1, the speech engines are
replaced, and every file goes to a temporary folder.
"""
import io
import json
import os
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from core import tts, voice_layer, voice_service
from core.voice_layer import SpeechStream
from test_blueprints import create_app
from test_companion_api import tmp_store  # noqa: F401  (a fixture: a temporary conversation database)


# ---------------------------------------------------------------- helpers

def make_wav(frames=2400, rate=24000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * frames)
    return buf.getvalue()


def wav_frames(path):
    with wave.open(str(path), "rb") as w:
        return w.getnframes()


class FakeVoiceService:
    """Answers like voice/serve.py: GET /api/info and POST /api/speak (a WAV whose length follows the text)."""

    def __init__(self, loaded=("kokoro",)):
        self.loaded = list(loaded)
        self.requests = []
        self.fail_with = None                     # (http status, message)
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _reply(self, code, body, ctype="application/json"):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/api/info":
                    self._reply(200, json.dumps({"device": "cuda", "loaded": outer.loaded}).encode())
                else:
                    self._reply(404, b"{}")

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
                outer.requests.append(body)
                if outer.fail_with:
                    return self._reply(outer.fail_with[0], json.dumps({"error": outer.fail_with[1]}).encode())
                self._reply(200, make_wav(len(body["text"]) * 10), "audio/wav")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.config = {"voice": {"tts_engine": "kokoro", "kokoro": {"url": self.url, "autostart": False}}}
        threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.02), daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def voice(tmp_path, monkeypatch):
    """A fake local voice service, settings that point at it, and an empty audio cache."""
    fake = FakeVoiceService()
    real_settings = tts.settings
    monkeypatch.setattr(tts, "AUDIO_CACHE_DIR", str(tmp_path / "audio"))
    monkeypatch.setattr(tts, "settings", lambda config=None: real_settings(fake.config if config is None else config))
    tts._reset_state()
    yield fake
    fake.close()
    tts._reset_state()


def eventually(fn, timeout=3.0):
    """Call fn until it returns something truthy (the clips are made on another thread)."""
    end = time.time() + timeout
    while time.time() < end:
        got = fn()
        if got:
            return got
        time.sleep(0.01)
    return fn()


class Recorder:
    """A stand-in for the speech engine: remembers what it was asked to say and returns a fake clip URL."""

    def __init__(self, fail_on=()):
        self.calls = []
        self.fail_on = fail_on

    def __call__(self, text):
        self.calls.append(text)
        for word in self.fail_on:
            if word in text:
                raise RuntimeError("engine failed")
        return f"/static/audio/tts/clip{len(self.calls)}.wav"


# ---------------------------------------------------------------- what gets read out loud

@pytest.mark.parametrize("raw, spoken", [
    ("Hey you! 😊", "Hey you!"),
    ("I missed you ❤️ so much", "I missed you so much"),
    ("*smiles softly* Hey there.", "Hey there."),
    ("Oh, *laughs* you are funny.", "Oh, you are funny."),
    ("I *really* mean it.", "I really mean it."),
    ("That is **so** good.", "That is so good."),
    ("See [my page](https://example.com/a) or https://example.com/b now.", "See my page or now."),
    ("Look at this [[photo: in a cafe]] one.", "Look at this one."),
    ("Ha [laughs] okay.", "Ha okay."),
    ("- one\n- two", "one. two"),
    ("First line\nSecond line.", "First line. Second line."),
    ("Wait...   what?", "Wait... what?"),
    ("😊😊", ""),
    ("*smiles*", ""),
    ("...", ""),
    ("", ""),
])
def test_text_is_cleaned_for_the_voice(raw, spoken):
    assert tts.clean_for_speech(raw) == spoken


def test_none_is_nothing_to_say():
    assert tts.clean_for_speech(None) == ""


def test_a_sentence_is_finished_only_once_the_next_one_has_started():
    sentences, rest = tts.split_sentences("Hey, you're back. I was hoping you'd stop by. How was")
    assert sentences == ["Hey, you're back.", "I was hoping you'd stop by."] and rest == "How was"


def test_the_space_after_a_full_stop_is_kept_for_the_next_streamed_piece():
    sentences, rest = tts.split_sentences("Hey, you're back. ")
    assert sentences == [] and rest == "Hey, you're back. "
    sentences, rest = tts.split_sentences(rest + "I missed you.")
    assert sentences == ["Hey, you're back."] and rest == "I missed you."


def test_ellipses_and_abbreviations_do_not_split_a_sentence():
    assert tts.split_sentences("Come here... sit with me. Okay")[0] == ["Come here... sit with me."]
    assert tts.split_sentences("Dr. Smith is here. Hi")[0] == ["Dr. Smith is here."]
    assert tts.split_sentences("Ask J. Smith about it. Then")[0] == ["Ask J. Smith about it."]


def test_a_sentence_may_start_with_an_emoji_or_an_action():
    sentences, rest = tts.split_sentences("Hey, you're back. *smiles* I missed you. 😊 How was your day? Tell")
    assert sentences == ["Hey, you're back.", "*smiles* I missed you.", "😊 How was your day?"] and rest == "Tell"


def test_long_text_is_cut_at_sentence_ends_and_no_piece_is_too_long():
    text = " ".join(f"This is sentence number {n} and it goes on for a little while longer than a short one." for n in range(1, 21))
    chunks = tts.chunk_for_speech(text)
    assert len(chunks) > 1 and all(len(c) <= tts.MAX_CLIP_CHARS for c in chunks)
    assert " ".join(chunks) == tts.clean_for_speech(text)
    assert all(c.endswith(".") for c in chunks)


def test_one_endless_sentence_is_cut_at_commas_or_spaces():
    text = ", ".join(["a very long stretch of words without any full stop"] * 30)
    chunks = tts.chunk_for_speech(text)
    assert all(len(c) <= tts.MAX_CLIP_CHARS for c in chunks) and len(chunks) > 1
    assert " ".join(chunks).replace(",", "").split() == text.replace(",", "").split()


# ---------------------------------------------------------------- settings

def test_settings_default_to_the_cloud_voice_so_an_old_config_behaves_as_before():
    cfg = tts.settings({"voice": {"tts_enabled": True, "voice_name": "en-GB-SoniaNeural"}})
    assert cfg["engine"] == "edge" and cfg["edge_voice"] == "en-GB-SoniaNeural" and cfg["cloud_fallback"] is False
    assert tts.settings({})["engine"] == "edge"


def test_settings_fill_in_and_bound_the_local_voice_options():
    cfg = tts.settings({"voice": {"tts_engine": "Kokoro", "kokoro": {"speed": 9, "url": "http://127.0.0.1:8770/"}}})
    assert cfg["engine"] == "kokoro" and cfg["kokoro"]["voice"] == "af_heart" and cfg["kokoro"]["speed"] == 2.0
    assert cfg["kokoro"]["url"] == "http://127.0.0.1:8770" and cfg["kokoro"]["autostart"] is True
    assert tts.settings({"voice": {"tts_engine": "nonsense"}})["engine"] == "edge"
    assert tts.settings({"voice": {"kokoro": {"speed": "fast"}}})["kokoro"]["speed"] == 1.0


def test_the_shipped_config_uses_the_local_voice_and_no_cloud_fallback(altera_config):
    import yaml
    cfg = tts.settings(yaml.safe_load(altera_config.read_text(encoding='utf-8')))
    assert cfg["engine"] == "kokoro" and cfg["kokoro"]["voice"] == "af_heart" and cfg["cloud_fallback"] is False


# ---------------------------------------------------------------- the local voice (kokoro engine)

def test_the_local_voice_makes_a_wav_and_caches_it(voice):
    path = tts.synthesize_long("Hey, you're back. How was your day?")
    assert path.endswith(".wav") and wav_frames(path) > 0
    assert len(voice.requests) == 1
    assert voice.requests[0] == {"engine": "kokoro", "text": "Hey, you're back. How was your day?", "voice": "af_heart", "speed": 1.0}
    assert tts.synthesize_long("Hey, you're back. How was your day?") == path
    assert len(voice.requests) == 1                                    # the second time came from the cache


def test_a_different_voice_or_speed_never_replays_an_old_file(voice):
    first = tts.synthesize_long("Same words.")
    voice.config["voice"]["kokoro"]["voice"] = "af_bella"
    second = tts.synthesize_long("Same words.")
    voice.config["voice"]["kokoro"]["speed"] = 1.2
    third = tts.synthesize_long("Same words.")
    assert len({first, second, third}) == 3
    assert [r["voice"] for r in voice.requests] == ["af_heart", "af_bella", "af_bella"]
    assert [r["speed"] for r in voice.requests] == [1.0, 1.0, 1.2]


def test_long_text_is_spoken_in_pieces_and_joined_into_one_file(voice):
    text = " ".join(f"Sentence number {n} is here to make this reply long enough to need more than one piece of speech." for n in range(1, 15))
    path = tts.synthesize_long(text)
    assert len(voice.requests) > 1
    assert wav_frames(path) == sum(len(r["text"]) * 10 for r in voice.requests)
    assert all(len(r["text"]) <= tts.MAX_CLIP_CHARS for r in voice.requests)


def test_nothing_speakable_means_no_file_and_no_request(voice):
    assert tts.synthesize_long("😊 *smiles*") is None
    assert voice.requests == []


def test_an_error_from_the_service_is_an_error_not_an_outage(voice):
    voice.fail_with = (500, "RuntimeError: out of memory")
    with pytest.raises(tts.TTSError, match="HTTP 500") as caught:
        tts.synthesize_long("Hello there.")
    assert not isinstance(caught.value, tts.TTSUnavailable)
    assert voice_layer.get_tts_audio("Hello there.") is None            # the chat carries on without speech


def test_an_unreachable_service_is_remembered_so_replies_do_not_wait(voice, monkeypatch):
    probes = []
    monkeypatch.setattr(voice_service, "info", lambda cfg=None, timeout=0.7: probes.append(1))   # always unreachable
    with pytest.raises(tts.TTSUnavailable):
        tts.synthesize_long("Hello there.")
    with pytest.raises(tts.TTSUnavailable):
        tts.synthesize_long("And another one.")
    assert len(probes) == 1                                             # the second reply did not even look
    assert voice.requests == []
    assert voice_layer.get_tts_audio("Third.") is None


def test_a_voice_that_is_still_loading_is_skipped_not_waited_for(voice):
    voice.loaded = []
    with pytest.raises(tts.TTSUnavailable, match="still loading"):
        tts.synthesize_long("Hello there.")
    assert voice.requests == []
    voice.loaded = ["kokoro"]
    tts._reset_state()
    assert tts.synthesize_long("Hello there.").endswith(".wav")


def test_speech_that_is_already_cached_needs_no_service(voice):
    path = tts.synthesize_long("Cached line.")
    voice.close()
    tts._reset_state()
    assert tts.synthesize_long("Cached line.") == path


# ---------------------------------------------------------------- the cloud voice

@pytest.fixture
def cloud(voice, monkeypatch):
    """The local voice is down; record what the cloud voice is asked to say."""
    calls = []
    monkeypatch.setattr(tts, "generate_speech", lambda text, voice_name=tts.DEFAULT_VOICE, rate="+0%": calls.append((text, voice_name)) or "cloud.mp3")
    monkeypatch.setattr(voice_service, "info", lambda cfg=None, timeout=0.7: None)
    voice.cloud_calls = calls
    return voice


def test_the_local_voice_never_falls_back_to_the_cloud_unless_asked(cloud):
    with pytest.raises(tts.TTSUnavailable):
        tts.synthesize_long("Private words.", allow_cloud=True)
    assert cloud.cloud_calls == []


def test_the_cloud_fallback_when_switched_on(cloud):
    cloud.config["voice"]["cloud_fallback"] = True
    cloud.config["voice"]["voice_name"] = "en-GB-SoniaNeural"
    assert tts.synthesize_long("Hello there.", allow_cloud=True) == "cloud.mp3"
    assert cloud.cloud_calls == [("Hello there.", "en-GB-SoniaNeural")]


def test_the_cloud_fallback_asks_adult_mode_and_is_refused_while_it_is_on(cloud, monkeypatch):
    cloud.config["voice"]["cloud_fallback"] = True
    monkeypatch.setattr("core.adult.effective_level", lambda store=None: "off")
    assert tts.synthesize_long("Hello there.") == "cloud.mp3"
    tts._reset_state()
    for level in ("romantic", "suggestive", "explicit"):
        monkeypatch.setattr("core.adult.effective_level", lambda store=None, level=level: level)
        tts._reset_state()
        with pytest.raises(tts.TTSUnavailable):
            tts.synthesize_long("Something private.")
    assert len(cloud.cloud_calls) == 1


def test_if_adult_mode_cannot_be_read_the_cloud_is_not_used(cloud, monkeypatch):
    cloud.config["voice"]["cloud_fallback"] = True

    def broken(store=None):
        raise RuntimeError("database locked")

    monkeypatch.setattr("core.adult.effective_level", broken)
    with pytest.raises(tts.TTSUnavailable):
        tts.synthesize_long("Hello there.")
    assert cloud.cloud_calls == []


def test_choosing_the_cloud_engine_speaks_through_it(tmp_path, monkeypatch):
    calls = []
    real_settings = tts.settings
    monkeypatch.setattr(tts, "settings", lambda config=None: real_settings({"voice": {"tts_engine": "edge", "voice_name": "en-US-JennyNeural"}}))
    monkeypatch.setattr(tts, "generate_speech", lambda text, voice_name=tts.DEFAULT_VOICE, rate="+0%": calls.append((text, voice_name)) or "x.mp3")
    assert tts.synthesize_long("Hello 😊 there.") == "x.mp3"
    assert calls == [("Hello there.", "en-US-JennyNeural")]


def test_the_audio_cache_name_includes_the_voice_and_engine():
    a = tts._cache_path("edge", "en-US-AriaNeural", "+0%", "Hi", ".mp3")
    assert a != tts._cache_path("edge", "en-US-JennyNeural", "+0%", "Hi", ".mp3")
    assert a != tts._cache_path("kokoro", "en-US-AriaNeural", "+0%", "Hi", ".mp3")
    assert a == tts._cache_path("edge", "en-US-AriaNeural", "+0%", "Hi", ".mp3")


def test_old_cached_speech_is_deleted_and_recent_speech_is_kept(tmp_path):
    old, recent, other = tmp_path / "old.wav", tmp_path / "recent.mp3", tmp_path / "notes.txt"
    for f in (old, recent, other):
        f.write_bytes(b"x")
    long_ago = time.time() - 40 * 86400
    os.utime(old, (long_ago, long_ago))
    os.utime(other, (long_ago, long_ago))
    assert tts.prune_cache(30, str(tmp_path)) == 1
    assert not old.exists() and recent.exists() and other.exists()


# ---------------------------------------------------------------- the service Altera starts

def test_status_says_what_the_voice_is_doing(voice, monkeypatch):
    voice.loaded = []
    assert voice_service.status()["state"] == "warming"
    voice.loaded = ["kokoro"]
    assert voice_service.status()["state"] == "ready" and "af_heart" in voice_service.status()["message"]
    monkeypatch.setattr(voice_service, "info", lambda cfg=None, timeout=0.7: None)       # the service cannot be reached
    monkeypatch.setattr(voice_service, "_proc", None)
    assert voice_service.status()["state"] == "down"
    voice.config = {"voice": {"tts_engine": "edge"}}
    assert voice_service.status()["state"] == "cloud"


def test_status_does_not_restart_a_service_without_an_explicit_start_request(voice, monkeypatch):
    tried = []
    monkeypatch.setattr(voice_service, "info", lambda cfg=None, timeout=0.7: None)
    monkeypatch.setattr(voice_service, "_proc", None)
    monkeypatch.setattr(voice_service, "ensure_started_async", lambda cfg=None: tried.append(1))
    assert voice_service.status()["state"] == "down" and tried == []
    assert voice_service.status(ensure_started=True)["state"] == "down" and tried == [1]
    voice.config = {"voice": {"tts_engine": "edge"}}                     # the cloud engine has nothing to start
    assert voice_service.status()["state"] == "cloud" and tried == [1]


def test_the_health_light_colours():
    assert [voice_service.light(s) for s in ("ready", "cloud", "warming", "starting", "down", "?")] == \
        ["ok", "ok", "warming", "warming", "error", "error"]


def test_the_service_is_not_started_when_autostart_is_off_or_it_is_already_running(voice):
    assert voice_service.start() == "disabled"
    voice.config["voice"]["kokoro"]["autostart"] = True
    assert voice_service.start() == "running"


def test_the_service_is_never_started_for_an_address_that_is_not_this_pc(voice):
    voice.config["voice"]["kokoro"].update(autostart=True, url="http://voice.example.com:8770")
    assert voice_service.start() == "disabled"


def test_a_missing_python_or_script_is_reported_not_crashed_on(voice, tmp_path, monkeypatch):
    monkeypatch.setattr(voice_service, "info", lambda cfg=None, timeout=0.7: None)       # not running yet
    voice.config["voice"]["kokoro"].update(autostart=True, python=str(tmp_path / "nope.exe"), script=str(tmp_path / "nope.py"))
    assert voice_service.start() == "no-launcher"


def test_the_service_is_started_with_the_right_arguments_and_only_once(voice, tmp_path, monkeypatch):
    monkeypatch.setenv('ALTERA_SYNTHETIC_PROVIDER_API_KEY', 'synthetic-private-credential')
    monkeypatch.setattr(voice_service, "info", lambda cfg=None, timeout=0.7: None)       # not running yet
    python, script = tmp_path / "python.exe", tmp_path / "serve.py"
    python.write_bytes(b"")
    script.write_bytes(b"")
    voice.config["voice"]["kokoro"].update(autostart=True, url="http://127.0.0.1:8899", python=str(python), script=str(script))
    launched = []

    class FakeProcess:
        def poll(self):
            return None

    class FakeOwner:
        closed = stopping = starting = False
        running = True
        process = FakeProcess()
        def __init__(self, command, **kwargs):
            launched.append((command(1777), kwargs))
        def start(self):
            return self
        def close(self, *, timeout):
            self.closed = True
            launched.append('closed')
            return True
    monkeypatch.setattr(voice_service, 'OwnedChildProcess', FakeOwner)
    monkeypatch.setattr(voice_service, '_owner', None)
    monkeypatch.setattr(voice_service, '_owner_key', None)
    monkeypatch.setattr(voice_service, '_closing', False)
    monkeypatch.setattr(voice_service, "_proc", None)
    monkeypatch.setattr(voice_service, "_last_start", 0.0)
    monkeypatch.setattr(voice_service, "_atexit_registered", True)          # do not register a real exit hook in a test
    monkeypatch.setattr(tts, "PROJECT_ROOT", tmp_path)

    assert voice_service.start() == "started"
    cmd, kwargs = launched[0]
    assert cmd[:2] == [str(python), str(script)]
    assert cmd[cmd.index("--port") + 1] == "8899" and cmd[cmd.index("--idle-minutes") + 1] == "0"
    assert cmd[cmd.index("--preload") + 1] == "kokoro" and int(cmd[cmd.index("--parent-pid") + 1]) > 0
    assert kwargs['cwd'] == str(tmp_path) and kwargs['environment']['PYTHONUTF8'] == '1'
    assert 'ALTERA_SYNTHETIC_PROVIDER_API_KEY' not in kwargs['environment']
    assert kwargs['environment']['HF_HUB_OFFLINE'] == kwargs['environment']['TRANSFORMERS_OFFLINE'] == '1'
    assert kwargs['environment']['PYTHONDONTWRITEBYTECODE'] == '1'
    assert cmd[cmd.index('--parent-pid') + 1] == '1777'

    assert voice_service.start() == "started"                                # already spawned: not launched again
    assert len(launched) == 1
    assert voice_service.stop() and launched[-1] == 'closed'


# ---------------------------------------------------------------- speaking while she is still writing

def test_the_first_clip_starts_before_the_reply_is_finished():
    rec = Recorder()
    stream = SpeechStream(synth=rec)
    stream.feed("Hey, you're back. ")
    assert rec.calls == []                                            # one short sentence is not enough yet
    stream.feed("I was hoping you'd stop by tonight. How was your")
    urls = eventually(stream.ready)
    assert rec.calls[0] == "Hey, you're back. I was hoping you'd stop by tonight." and len(urls) == 1
    stream.feed(" day?")
    stream.finish()
    urls += stream.drain()
    assert rec.calls == ["Hey, you're back. I was hoping you'd stop by tonight.", "How was your day?"]
    assert urls == ["/static/audio/tts/clip1.wav", "/static/audio/tts/clip2.wav"]
    stream.close()


def test_clips_come_out_in_reading_order_however_the_text_arrives():
    reply = ("Hey, you're back. I was hoping you'd stop by tonight. How was your day? Mine was slow, honestly. "
             "I kept thinking about that story you told me. It made me smile more than I want to admit. Come here.")
    for size in (1, 3, 7, 50, len(reply)):
        rec = Recorder()
        stream = SpeechStream(synth=rec)
        urls = []
        for i in range(0, len(reply), size):
            stream.feed(reply[i:i + size])
            urls += stream.ready()
        stream.finish()
        urls += stream.drain()
        stream.close()
        assert " ".join(rec.calls) == reply
        assert urls == [f"/static/audio/tts/clip{n}.wav" for n in range(1, len(rec.calls) + 1)]
        assert all(len(call) <= tts.MAX_CLIP_CHARS for call in rec.calls)


def test_a_big_block_of_text_still_starts_with_a_short_first_clip():
    rec = Recorder()
    stream = SpeechStream(synth=rec)
    stream.feed("The first sentence is short and sweet. The second is a little longer than that one was. "
                "The third and the fourth make this block of text arrive all at once, as a model that does not stream would. Next")
    assert eventually(lambda: rec.calls)[0] == "The first sentence is short and sweet."      # not the whole block
    stream.finish()
    stream.drain()
    stream.close()
    assert " ".join(rec.calls) == ("The first sentence is short and sweet. The second is a little longer than that one was. "
                                   "The third and the fourth make this block of text arrive all at once, as a model that does not stream would. Next")


def test_later_clips_gather_a_few_sentences_instead_of_one_clip_each():
    rec = Recorder()
    stream = SpeechStream(synth=rec)
    stream.feed("First sentence is right here for you. Second sentence follows it closely. Third sentence is next. "
                "Fourth sentence is after that. Fifth sentence comes last. ")
    stream.finish()
    stream.drain()
    stream.close()
    assert 1 < len(rec.calls) < 5


def test_the_remaining_clips_are_yielded_as_each_one_finishes_not_all_at_the_end():
    gates = [threading.Event() for _ in range(10)]
    made = []

    def synth(text):
        n = len(made)
        made.append(text)
        assert gates[n].wait(10)
        return f"/static/audio/tts/g{n}.wav"

    stream = SpeechStream(synth=synth)
    stream.feed("Sentence one is right here for you. Sentence two follows it closely and carries on for a while longer. "
                "Sentence three comes next and is also fairly long for a sentence. Four is short. Five")
    stream.finish()
    assert len(stream._futures) >= 3
    remaining = stream.drain_iter(timeout=10)
    gates[0].set()
    first = next(remaining)                         # returns although the later clips are still being made
    assert first == "/static/audio/tts/g0.wav" and not gates[-1].is_set()
    for gate in gates:
        gate.set()
    assert [first] + list(remaining) == [f"/static/audio/tts/g{n}.wav" for n in range(len(made))]
    stream.close()


def test_once_a_clip_fails_the_rest_of_the_reply_stays_silent():
    """No gap in the middle, and no later clip that merely happens to be cached, is played after a failure."""
    made = []

    def synth(text):
        made.append(text)
        if "fail" in text:
            raise RuntimeError("engine failed")
        return f"/static/audio/tts/ok{len(made)}.wav"

    stream = SpeechStream(synth=synth)
    stream.feed("The first sentence is long enough to be a clip on its own. ")
    stream.feed("This second sentence will fail to be made, as the service went away. " * 2 + "Then a third sentence, which is fine. ")
    stream.feed("And a fourth one that comes after the failure, which was cached earlier. ")
    stream.finish()
    urls = stream.drain()
    stream.close()
    assert urls == ["/static/audio/tts/ok1.wav"]                       # only what came before the failure


def test_a_clip_with_no_url_is_a_failure_too():
    stream = SpeechStream(synth=lambda text: None)
    stream.feed("This one has nothing to play. Neither does the next one. ")
    stream.finish()
    assert stream.drain() == []
    stream.close()


def test_nothing_more_is_made_after_a_failure():
    calls = []

    def synth(text):
        calls.append(text)
        return None

    stream = SpeechStream(synth=synth)
    stream.feed("The first sentence is long enough to be a clip on its own. Then")     # "Then" finishes the first sentence
    assert eventually(lambda: calls)                                   # the first clip was tried
    assert stream.drain() == []                                        # and it failed
    stream.feed(" another sentence arrives after the failure and is long enough to be a clip on its own too. Then")
    stream.finish()
    stream.drain()
    stream.close()
    assert len(calls) == 1


def test_nothing_speakable_makes_no_clips():
    rec = Recorder()
    stream = SpeechStream(synth=rec)
    stream.feed("*smiles* 😊 ")
    stream.finish()
    assert stream.drain() == [] and rec.calls == []
    stream.close()


def test_a_stream_that_is_closed_ignores_more_text():
    rec = Recorder()
    stream = SpeechStream(synth=rec)
    stream.close()
    stream.feed("This is far too late to be spoken. Really. ")
    stream.finish()
    assert stream.drain() == [] and rec.calls == []


def test_the_stream_uses_get_tts_audio_and_passes_the_cloud_permission(monkeypatch):
    asked = []
    monkeypatch.setattr(voice_layer, "get_tts_audio", lambda text, voice_name=None, allow_cloud=None: asked.append((text, allow_cloud)) or "/static/audio/tts/a.wav")
    stream = SpeechStream(allow_cloud=False)
    stream.feed("Just one short reply for you.")
    stream.finish()
    assert stream.drain() == ["/static/audio/tts/a.wav"]
    stream.close()
    assert asked == [("Just one short reply for you.", False)]


def test_get_tts_audio_returns_a_url_under_static(voice):
    url = voice_layer.get_tts_audio("Hello there.")
    assert url.startswith("/static/audio/tts/") and url.endswith(".wav")
    assert voice_layer.get_tts_audio("😊") is None


# ---------------------------------------------------------------- the chat stream and the voice routes

class FakeChat:
    def __init__(self, chunks):
        self.chunks = chunks

    def send_message(self, message, session_id, stream=False, **kwargs):
        return iter(self.chunks)


def stream_events(client, sid):
    response = client.post("/api/chat/stream", json={"session_id": sid, "message": "hello"})
    return [json.loads(line[6:]) for line in response.get_data(as_text=True).splitlines() if line.startswith("data: ")]


@pytest.fixture
def chat_stream(tmp_path, tmp_store, monkeypatch, altera_config):
    """The streaming route with a fake model, a voice that is switched on, and speech that is a recorder."""
    import core.emotion
    import core.routes.core_api as core_api
    altera_config.write_text("voice:\n  tts_enabled: true\n  tts_engine: kokoro\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)                                        # user_profile.yaml is read from here
    order = []                                                         # what happened, in order: "tts" per clip, "emotion" once
    monkeypatch.setattr(core.emotion, "analyze_and_store_emotion", lambda *a, **k: order.append("emotion"))
    said = []
    monkeypatch.setattr(voice_layer, "get_tts_audio",
                        lambda text, voice_name=None, allow_cloud=None: said.append(text) or order.append("tts") or f"/static/audio/tts/clip{len(said)}.wav")

    def start(chunks):
        monkeypatch.setattr(core_api, "_get_chat_engine", lambda: FakeChat(chunks))
        return create_app("core_api").test_client(), tmp_store.create_session("t")

    start.said = said
    start.order = order
    return start


REPLY = ["Hey, you're back. ", "I was hoping you'd stop by tonight. ", "How was ", "your day? ", "Tell me everything, ", "I have time."]


def test_the_stream_sends_speech_clips_in_order_before_the_final_event(chat_stream):
    client, sid = chat_stream(REPLY)
    events = stream_events(client, sid)
    audio = [e for e in events if e.get("status") == "audio"]
    assert len(audio) == len(chat_stream.said) >= 2
    assert [e["seq"] for e in audio] == list(range(1, len(audio) + 1))
    assert [e["audio_url"] for e in audio] == [f"/static/audio/tts/clip{n}.wav" for n in range(1, len(audio) + 1)]
    assert "".join(e.get("response", "") for e in events) == "".join(REPLY)
    assert " ".join(chat_stream.said) == "".join(REPLY).strip()
    final = events.index({"status": "final", "audio_url": None})
    assert final > max(events.index(e) for e in audio) and events[-1] == {"status": "done"}


def test_the_first_clip_is_sent_while_the_reply_is_still_streaming(chat_stream):
    client, sid = chat_stream(REPLY)
    events = stream_events(client, sid)
    first_audio = next(i for i, e in enumerate(events) if e.get("status") == "audio")
    last_text = max(i for i, e in enumerate(events) if "response" in e)
    assert first_audio < last_text


def test_the_whole_reply_is_spoken_before_the_slower_emotion_analysis_runs(chat_stream):
    client, sid = chat_stream(REPLY)
    events = stream_events(client, sid)
    audio_count = sum(1 for e in events if e.get("status") == "audio")
    assert audio_count >= 2
    assert chat_stream.order == ["tts"] * audio_count + ["emotion"]


def test_no_speech_when_the_user_has_turned_the_voice_off(chat_stream, tmp_path):
    from core.profile import save_user_profile
    save_user_profile({'preferences': {'voice_enabled': False}})
    client, sid = chat_stream(REPLY)
    events = stream_events(client, sid)
    assert not any(e.get("status") == "audio" for e in events) and chat_stream.said == []
    assert events[-2:] == [{"status": "final", "audio_url": None}, {"status": "done"}]


def test_no_speech_when_the_voice_is_off_in_the_config(chat_stream, altera_config):
    altera_config.write_text("voice:\n  tts_enabled: false\n", encoding="utf-8")
    client, sid = chat_stream(REPLY)
    assert not any(e.get("status") == "audio" for e in stream_events(client, sid)) and chat_stream.said == []


def test_a_reply_with_nothing_to_say_sends_no_clips(chat_stream):
    client, sid = chat_stream(["*smiles* ", "😊"])
    events = stream_events(client, sid)
    assert not any(e.get("status") == "audio" for e in events) and chat_stream.said == []


def test_a_speech_failure_never_breaks_the_reply(chat_stream, monkeypatch):
    def broken(text, voice_name=None, allow_cloud=None):
        raise RuntimeError("engine exploded")

    monkeypatch.setattr(voice_layer, "get_tts_audio", broken)
    client, sid = chat_stream(REPLY)
    events = stream_events(client, sid)
    assert "".join(e.get("response", "") for e in events) == "".join(REPLY)
    assert events[-1] == {"status": "done"} and not any(e.get("status") == "audio" for e in events)


def test_stream_says_once_when_kokoro_takes_over_from_voice_lab(chat_stream, altera_config, monkeypatch):
    from core.voicelab import FALLBACK
    altera_config.write_text("voice:\n  tts_enabled: true\n  tts_engine: voicelab\n", encoding="utf-8")

    def fallback(text, **kwargs):
        kwargs['notice'](FALLBACK)
        return "/static/audio/tts/kokoro.wav"

    monkeypatch.setattr(voice_layer, 'get_tts_audio', fallback)
    client, sid = chat_stream(REPLY)
    events = stream_events(client, sid)
    notices = [event for event in events if event.get('status') == 'voice_notice']
    assert notices == [{'status': 'voice_notice', 'message': FALLBACK}]
    assert events.index(notices[0]) < next(i for i, event in enumerate(events) if event.get('status') == 'audio')
    assert events[-1] == {"status": "done"}


def test_the_cloud_permission_is_looked_up_once_and_only_when_the_fallback_is_on(chat_stream, altera_config, monkeypatch):
    seen = []
    monkeypatch.setattr(voice_layer, "get_tts_audio",
                        lambda text, voice_name=None, allow_cloud=None: seen.append(allow_cloud) or "/static/audio/tts/x.wav")
    monkeypatch.setattr("core.adult.effective_level", lambda store=None: "explicit")
    client, sid = chat_stream(REPLY)
    stream_events(client, sid)
    assert seen and set(seen) == {False}                               # fallback off: cloud not allowed
    seen.clear()
    altera_config.write_text("voice:\n  tts_enabled: true\n  tts_engine: kokoro\n  cloud_fallback: true\n", encoding="utf-8")
    stream_events(client, sid)
    assert seen and set(seen) == {False}                               # fallback on, but adult mode is on: still not allowed
    seen.clear()
    monkeypatch.setattr("core.adult.effective_level", lambda store=None: "off")
    stream_events(client, sid)
    assert seen and set(seen) == {True}


def test_voice_status_names_the_voice_and_its_state(monkeypatch, tmp_path, altera_config):
    import core.profile
    altera_config.write_text('voice:\n  tts_enabled: true\n  tts_engine: kokoro\n  kokoro:\n    autostart: false\n', encoding='utf-8')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(core.profile, "load_user_profile", lambda: {"preferences": {"voice_enabled": True}})
    monkeypatch.setattr(voice_service, "status", lambda cfg=None, ensure_started=False: {"state": "warming", "message": "loading"})
    real_settings = tts.settings
    monkeypatch.setattr(tts, "settings", lambda config=None: real_settings({"voice": {"tts_engine": "kokoro", "kokoro": {"autostart": False}}}))
    data = create_app("engagement").test_client().get("/api/voice/status").get_json()
    assert data["voice_name"] == "Kokoro af_heart (local)" and data["tts_engine"] == "kokoro" and data["voice_state"] == "warming"


def test_the_single_file_route_still_works_for_proactive_messages_and_replay(monkeypatch):
    monkeypatch.setattr(voice_layer, "get_tts_audio", lambda text, voice_name=None, allow_cloud=None: "/static/audio/tts/one.wav")
    client = create_app("engagement").test_client()
    assert client.post("/api/voice/tts", json={"text": "Hello there."}).get_json() == {"audio_url": "/static/audio/tts/one.wav"}
    assert client.post("/api/voice/tts", json={"text": "  "}).status_code == 400
