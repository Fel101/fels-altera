"""Exercise suite lock coordination with disposable holders and model stand-ins."""
import json
from types import SimpleNamespace
import threading

import pytest

from core import fels_suite, stt, suite_gpu, voice_service
from core.voice_layer import SpeechStream
from test_blueprints import create_app
from test_companion_api import engine, tmp_store, world  # noqa: F401
from test_polish_reliability import reliable  # noqa: F401


@pytest.mark.parametrize("provider, port, stop", [("lmstudio", 1234, False), ("ollama", 11434, True)])
def test_a_durable_local_reply_waits_and_resumes_or_stops(reliable, engine, monkeypatch, provider, port, stop):
    engine.llm.base_url = f"http://127.0.0.1:{port}/v1"
    engine.llm.provider_key = provider
    release_before_picture = []

    def start_photos(*args):
        release_before_picture.append(fels_suite.gpu_holders())
        return []

    monkeypatch.setattr(engine, "_start_photos", start_photos)
    picture = fels_suite.GpuLock("npc-forge")
    assert picture.acquire("pictures", alone=True, timeout=0)

    def text():
        assert [(row["app"], row["alone"]) for row in fels_suite.gpu_holders()] == [("altera", False)]
        yield "A synthetic answer."

    engine.llm.chunks = text()
    key = "waiting-local-model"
    response = reliable.post("/api/chat/stream", json={"message": "Hello", "request_id": key}, buffered=False)
    try:
        events = (json.loads(chunk.decode()[6:]) for chunk in response.response if chunk.startswith(b"data: "))
        for event in events:
            if event.get("status") == "gpu_wait":
                break
        assert event == {"status": "gpu_wait", "message": suite_gpu.FORGE_WAIT}
        assert not engine.llm.calls
        if stop:
            assert reliable.post(f"/api/chat/requests/{key}/stop", json={}).status_code == 200
            assert list(events)[-1]["status"] == "interrupted"
            assert not engine.llm.calls
        else:
            picture.release()
            remaining = [event for event in events if event.get("status") != "heartbeat"]
            assert remaining[0]["status"] == "typing"
            assert remaining[-1]["status"] == "saved"
            assert release_before_picture == [[]]
    finally:
        picture.release()
        response.close()


def test_a_pending_exclusive_request_blocks_new_shared_work():
    voice_release = suite_gpu.try_use("Kokoro voice")
    forge = fels_suite.GpuLock("npc-forge")
    try:
        assert forge.want_alone("pictures")
        assert suite_gpu.try_use("Ollama chat") is None
    finally:
        voice_release()
        forge.release()
    assert fels_suite.gpu_holders() == []


def test_unload_only_accepts_direct_local_non_browser_json(monkeypatch):
    client = create_app("system").test_client()
    calls = []
    monkeypatch.setattr(voice_service, "unload_gpu", lambda: calls.append(True))
    for headers in [{"Origin": "https://website.example"}, {"Origin": "http://localhost"},
                    {"Sec-Fetch-Mode": "cors"}, {"Referer": "http://localhost/"},
                    {"X-Forwarded-For": "100.88.1.1"}]:
        assert client.post("/api/unload", json={}, headers=headers).status_code == 403
    assert client.post("/api/unload", data="{}").status_code == 415
    assert not calls
    assert client.post("/api/unload", json={}).json == {"ok": True}
    assert calls == [True]


def test_unload_frees_owned_gpu_voice_and_keeps_cpu_whisper(monkeypatch):
    client = create_app("system").test_client()
    release = suite_gpu.try_use("Kokoro voice")
    closed = []
    cpu = SimpleNamespace(model=SimpleNamespace(device="cpu"))
    cuda = SimpleNamespace(model=SimpleNamespace(device="cuda"))
    monkeypatch.setattr(stt, "_models", {"base": cpu, "small": cuda})
    monkeypatch.setattr(voice_service, "_gpu_release", release)
    monkeypatch.setattr(voice_service, "_owner", SimpleNamespace(close=lambda *, timeout: closed.append(timeout) or True))
    monkeypatch.setattr(voice_service, "_last_start", 5)
    try:
        assert client.get("/api/gpu").json["holders"][0]["what"] == "Kokoro voice"
        assert client.post("/api/unload", json={}).json == {"ok": True}
        assert closed == [2] and voice_service._last_start == 0
        assert stt._models == {"base": cpu}
        assert client.get("/api/gpu").json == {"state": "free", "holders": [], "waiting": None}
    finally:
        release()


def test_unload_answers_busy_while_reply_speech_is_running():
    client = create_app("system").test_client()
    speaking, finish = threading.Event(), threading.Event()

    def synth(text):
        speaking.set()
        assert finish.wait(4)
        return "/static/audio/tts/synthetic.wav"

    stream = SpeechStream(synth=synth)
    try:
        stream.feed("This example clip is long enough to speak.")
        stream.finish()
        assert speaking.wait(4)
        result = client.post("/api/unload", json={})
        assert result.status_code == 409
        assert result.json["code"] == "busy" and result.json["doing"] == "speaking a reply"
        finish.set()
        assert stream.drain() == ["/static/audio/tts/synthetic.wav"]
    finally:
        finish.set()
        stream.close()
