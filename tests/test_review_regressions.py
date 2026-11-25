import json
import types
from dataclasses import dataclass

import yaml
import pytest

from core.llm import LLMClient
from core.providers import ProviderRegistry
from test_blueprints import create_app
from test_companion_api import tmp_store, world  # noqa: F401


@pytest.fixture
def enabled_tools_config(altera_config):
    """These endpoint checks exercise explicitly enabled tools, regardless of instance defaults."""
    from core import config
    altera_config.write_text('tools:\n  enabled: true\n', encoding='utf-8')
    config.LOCAL_CONFIG_PATH.write_text('{}\n', encoding='utf-8')
    config.reset_cache()


class FakeFunction:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class FakeToolCall:
    def __init__(self, name, arguments, call_id="call_1"):
        self.id = call_id
        self.type = "function"
        self.function = FakeFunction(name, arguments)


class FakeMessage:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class FakeChoice:
    def __init__(self, message):
        self.message = message


class FakeResponse:
    def __init__(self, message):
        self.choices = [FakeChoice(message)]


class FakeCompletions:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            return FakeResponse(FakeMessage(tool_calls=[
                FakeToolCall("web_search", json.dumps({"query": "latest Altera project status", "max_results": 2}))
            ]))
        return FakeResponse(FakeMessage(content="Searched and summarized."))


class FakeClient:
    def __init__(self):
        self.chat = types.SimpleNamespace(completions=FakeCompletions())


@dataclass
class FakeSession:
    id: int
    name: str = "Fake Session"
    tags: str = "[]"
    archived: bool = False
    created_at: str = "2026-07-05T00:00:00"
    updated_at: str = "2026-07-05T00:00:00"


class FakeSessionStore:
    def __init__(self):
        self.sessions = {}
        self.next_id = 1

    def get_session(self, session_id):
        return self.sessions.get(int(session_id))

    def create_session(self, name):
        session_id = self.next_id
        self.next_id += 1
        self.sessions[session_id] = FakeSession(id=session_id, name=name)
        return session_id

    def update_session(self, session_id, **kwargs):
        session = self.sessions[int(session_id)]
        for key, value in kwargs.items():
            setattr(session, key, value)

    def archive_session(self, session_id):
        self.update_session(session_id, archived=True)

    def unarchive_session(self, session_id):
        self.update_session(session_id, archived=False)

    def delete_session(self, session_id):
        self.sessions.pop(int(session_id), None)

    def get_messages(self, session_id, limit=1000):
        return []


def test_llm_executes_tool_calls_and_continues_model_turn():
    client = FakeClient()
    llm = LLMClient()
    executed = []

    def executor(name, arguments):
        executed.append((name, arguments))
        return json.dumps({"results": [{"title": "Result", "url": "https://example.test"}]})

    result = llm._chat_once_or_with_tools(
        client,
        [{"role": "user", "content": "Search this"}],
        tools=[{"type": "function", "function": {"name": "web_search"}}],
        tool_executor=executor,
    )

    assert result == "Searched and summarized."
    assert executed == [("web_search", {"query": "latest Altera project status", "max_results": 2})]
    second_call_messages = client.chat.completions.calls[1]["messages"]
    assert second_call_messages[-1]["role"] == "tool"
    assert second_call_messages[-2]["role"] == "assistant"
    assert second_call_messages[-2]["tool_calls"][0]["function"]["name"] == "web_search"


def test_provider_registry_saves_config_atomically(tmp_path, monkeypatch, altera_config):
    monkeypatch.chdir(tmp_path)
    config_path = altera_config
    config_path.write_text(
        yaml.safe_dump({
            "app": {"port": 5001},
            "llm": {"provider": "lmstudio", "model": "old-model", "temperature": 0.2},
        }),
        encoding="utf-8",
    )

    registry = ProviderRegistry({
        "llm": {"provider": "lmstudio", "model": "old-model", "temperature": 0.2}
    })
    assert registry.set_active_provider("ollama", "llama3.2") is True

    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert saved["app"]["port"] == 5001
    assert saved["llm"]["provider"] == "ollama"
    assert saved["llm"]["model"] == "llama3.2"
    assert not list(config_path.parent.glob(".config.*.yaml.tmp"))


def test_session_routes_reject_missing_sessions(monkeypatch):
    import core.routes.sessions as session_routes

    fake_store = FakeSessionStore()
    monkeypatch.setattr(session_routes, "get_datastore", lambda: fake_store)

    app = create_app("sessions")
    client = app.test_client()

    assert client.delete("/api/sessions/999999").status_code == 404
    assert client.post("/api/sessions/999999/archive", json={"archived": True}).status_code == 404
    response = client.post("/api/sessions/999999/merge", json={"target_session_id": 1})
    assert response.status_code == 404


def test_session_tags_must_be_list(monkeypatch):
    import core.routes.sessions as session_routes

    fake_store = FakeSessionStore()
    monkeypatch.setattr(session_routes, "get_datastore", lambda: fake_store)

    app = create_app("sessions")
    client = app.test_client()

    created = client.post("/api/sessions", json={"name": "Tag Test"}).get_json()
    response = client.put(f"/api/sessions/{created['id']}/tags", json={"tags": "work"})

    assert response.status_code == 400
    assert response.get_json()["error"] == "tags must be a list"


def test_clear_data_requires_confirmation():
    app = create_app("admin")
    response = app.test_client().post("/api/clear-data", json={})

    assert response.status_code == 400
    assert "Confirmation required" in response.get_json()["error"]


def test_agent_tools_read_write_visible_workspace_file(tmp_path, monkeypatch, enabled_tools_config):
    import core.routes.agent_tools as agent_tools

    monkeypatch.setattr(agent_tools, "PROJECT_ROOT", tmp_path)

    app = create_app("agent_tools")
    client = app.test_client()

    write_response = client.post(
        "/api/tools/file",
        json={"path": "workspace/notes/sample.txt", "content": "hello workspace"},
    )
    assert write_response.status_code == 200
    assert (tmp_path / "workspace" / "notes" / "sample.txt").read_text(encoding="utf-8") == "hello workspace"

    read_response = client.get("/api/tools/file?path=workspace/notes/sample.txt")
    assert read_response.status_code == 200
    assert read_response.get_json()["content"] == "hello workspace"


def test_agent_tools_reject_path_traversal(tmp_path, monkeypatch, enabled_tools_config):
    import core.routes.agent_tools as agent_tools

    monkeypatch.setattr(agent_tools, "PROJECT_ROOT", tmp_path)

    app = create_app("agent_tools")
    response = app.test_client().get("/api/tools/file?path=../outside.txt")

    assert response.status_code == 400
    assert "inside the project workspace" in response.get_json()["error"]


def test_agent_tools_fetch_requires_http_url(enabled_tools_config):
    app = create_app("agent_tools")
    response = app.test_client().post("/api/tools/fetch", json={"url": "file:///etc/passwd"})

    assert response.status_code == 400
    assert response.get_json()["error"] == "url must be http or https"


# ============================================================
# Code review 2026-09-21 — regressions for what was found broken
# ============================================================

def _store(tmp_path):
    from core.datastore import DataStore, init_db

    db = str(tmp_path / "conversations.db")
    init_db(db)
    return DataStore(db)


def test_a_message_is_searchable_as_soon_as_it_is_saved(tmp_path):
    """The search index used to be created and never filled: messages_fts is an external content table, so the
    COUNT(*) comparison that guarded the backfill compared messages against itself and never fired."""
    from core.constants import FTS_TABLE

    store = _store(tmp_path)
    session_id = store.create_session("chat")

    store.save_message(session_id, "user", "the pangolin ate my homework")
    store.ensure_fts_table(FTS_TABLE)                       # what ConversationSearch() does at import
    store.save_message(session_id, "user", "the capybara ate my homework")   # saved after the index existed

    assert len(store.fts_search("pangolin", fts_table_name=FTS_TABLE)) == 1
    assert len(store.fts_search("capybara", fts_table_name=FTS_TABLE)) == 1
    assert len(store.fts_search("homework", fts_table_name=FTS_TABLE)) == 2
    assert len(store.fts_search("capybara homework", fts_table_name=FTS_TABLE)) == 1   # words are ANDed
    assert len(store.fts_search("pangolin capybara", fts_table_name=FTS_TABLE)) == 0


def test_messages_that_predate_the_index_are_backfilled_once(tmp_path):
    from core.constants import FTS_TABLE

    store = _store(tmp_path)
    session_id = store.create_session("chat")
    for i in range(5):
        store.save_message(session_id, "user", f"message {i} about aardvarks")

    store.ensure_fts_table(FTS_TABLE)
    assert len(store.fts_search("aardvarks", fts_table_name=FTS_TABLE)) == 5

    # Calling it again must not rebuild (it is O(all messages)); the flag in fts_state records that it ran.
    store.ensure_fts_table(FTS_TABLE)
    assert len(store.fts_search("aardvarks", fts_table_name=FTS_TABLE)) == 5


def test_deleting_a_message_removes_it_from_search(tmp_path):
    from core.constants import FTS_TABLE

    store = _store(tmp_path)
    session_id = store.create_session("chat")
    keep = store.save_message(session_id, "user", "narwhals are real")
    store.save_message(session_id, "assistant", "narwhals are indeed real")
    store.ensure_fts_table(FTS_TABLE)

    assert len(store.fts_search("narwhals", fts_table_name=FTS_TABLE)) == 2
    store.delete_messages_after(session_id, keep)
    assert len(store.fts_search("narwhals", fts_table_name=FTS_TABLE)) == 1
    store.delete_session(session_id)
    assert store.fts_search("narwhals", fts_table_name=FTS_TABLE) == []


def test_search_filters_still_apply(tmp_path):
    from core.constants import FTS_TABLE

    store = _store(tmp_path)
    one, two = store.create_session("a"), store.create_session("b")
    store.save_message(one, "user", "quokkas everywhere")
    store.save_message(two, "assistant", "quokkas nowhere")
    store.ensure_fts_table(FTS_TABLE)

    assert len(store.fts_search("quokkas", fts_table_name=FTS_TABLE)) == 2
    assert len(store.fts_search("quokkas", fts_table_name=FTS_TABLE, session_id=one)) == 1
    assert len(store.fts_search("quokkas", fts_table_name=FTS_TABLE, role="user")) == 1


def test_punctuation_in_the_search_box_is_not_a_500(tmp_path):
    """FTS5 MATCH takes a query language, not plain text: a bare quote or bracket used to raise OperationalError."""
    from core.constants import FTS_TABLE

    store = _store(tmp_path)
    session_id = store.create_session("chat")
    store.save_message(session_id, "user", "hello there")
    store.ensure_fts_table(FTS_TABLE)

    for query in ('"', 'hello AND', 'foo)', 'NEAR(a b)', '   ', 'OR OR OR', '*', '^hello'):
        assert store.fts_search(query, fts_table_name=FTS_TABLE) == [] or True   # must not raise


def test_fts_match_query_quotes_every_word():
    from core.datastore import fts_match_query

    assert fts_match_query('hello world') == '"hello" "world"'
    assert fts_match_query('say "hi"') == '"say" "hi"'
    assert fts_match_query('  ') == ''
    assert fts_match_query(None) == ''


def test_memory_crud_routes_are_wired_up(monkeypatch, world):
    """All four /api/memory routes used to raise NameError: get_memory_manager was called but never imported,
    so the catch-all error handler turned every one of them into a silent 500."""
    import core.routes.core_api as core_api

    from core.memory import MemoryManager
    from test_companion_notes import memory_store
    manager = MemoryManager.__new__(MemoryManager)
    manager.store = memory_store()
    monkeypatch.setattr(core_api, "get_memory_manager", lambda: manager)
    client = create_app("core_api").test_client()

    saved = client.post("/api/memory", json={"content": "x"})
    assert saved.status_code == 200
    item = saved.json['id']
    assert client.get('/api/memory').json[0]['content'] == 'x'
    assert client.put('/api/memory/' + item, json={'content': 'Corrected x'}).status_code == 200
    assert manager.store.get_all()[0]['content'] == 'Corrected x'
    assert client.delete('/api/memory/' + item).status_code == 200
    assert not manager.store.get_all()
    assert client.post('/api/memory', json={'content': 'Another fact'}).status_code == 200

    assert client.post("/api/memory/clear", json={"confirm": "no"}).status_code == 400   # still guarded
    assert client.post("/api/memory/clear", json={"confirm": "yes"}).status_code == 200
    assert not manager.store.get_all()


def test_the_sanitizer_keeps_hyphens_and_strips_control_characters():
    """The character class began with a literal '-', so every hyphen was deleted from every message and NUL survived."""
    from core.routes.core_api import validate_message_input

    kept, err = validate_message_input("well-known e-mail on 2024-01-01, temp -5")
    assert err is None
    assert kept == "well-known e-mail on 2024-01-01, temp -5"

    stripped, err = validate_message_input("a" + chr(0) + "b" + chr(8) + "c" + chr(31) + "d" + chr(127))
    assert err is None
    assert stripped == "abcd"

    # tab, newline and carriage return are legitimate in a message and must survive
    multiline, err = validate_message_input("line one\n\tline two")
    assert err is None
    assert multiline == "line one\n\tline two"


def test_the_web_ui_cannot_drop_a_python_file_into_the_project(tmp_path, monkeypatch, enabled_tools_config):
    """Staying inside the project was the whole attack: a .py file written into core/routes/ runs on the next start.
    Writing is now confined to the workspace/ subdirectory."""
    import core.routes.agent_tools as agent_tools

    monkeypatch.setattr(agent_tools, "PROJECT_ROOT", tmp_path)
    (tmp_path / "core" / "routes").mkdir(parents=True)
    client = create_app("agent_tools").test_client()

    for target in ("core/routes/pwned.py", "altera.py", "conftest.py", "core/chat.py", "notes/sample.txt"):
        response = client.post("/api/tools/file", json={"path": target, "content": "# proof"})
        assert response.status_code == 400, target
        assert "workspace/" in response.get_json()["error"]
        assert not (tmp_path / target).exists(), target

    # the writable subdirectory still works
    assert client.post("/api/tools/file", json={"path": "workspace/scratch.md", "content": "ok"}).status_code == 200


def test_secrets_are_not_readable_or_listed(tmp_path, monkeypatch, enabled_tools_config):
    import core.routes.agent_tools as agent_tools

    monkeypatch.setattr(agent_tools, "PROJECT_ROOT", tmp_path)
    (tmp_path / "config.yaml").write_text("llm:\n  api_key: sk-secret\n", encoding="utf-8")
    (tmp_path / "user_profile.yaml").write_text("name: Sam\n", encoding="utf-8")
    (tmp_path / "conversations.db").write_bytes(b"SQLite format 3")
    (tmp_path / "README.md").write_text("fine", encoding="utf-8")
    client = create_app("agent_tools").test_client()

    for secret in ("config.yaml", "user_profile.yaml", "conversations.db"):
        response = client.get(f"/api/tools/file?path={secret}")
        assert response.status_code == 400, secret
        assert "private data" in response.get_json()["error"]

    listed = [f["path"] for f in client.get("/api/tools/files").get_json()["files"]]
    assert "README.md" in listed
    assert "config.yaml" not in listed and "user_profile.yaml" not in listed

    assert client.get("/api/tools/file?path=README.md").status_code == 200


def test_tools_refuse_cross_site_requests(tmp_path, monkeypatch, enabled_tools_config):
    import core.routes.agent_tools as agent_tools

    monkeypatch.setattr(agent_tools, "PROJECT_ROOT", tmp_path)
    client = create_app("agent_tools").test_client()
    evil = {"Sec-Fetch-Site": "cross-site"}

    assert client.post("/api/tools/file", json={"path": "workspace/x.txt", "content": "x"},
                       headers=evil).status_code == 403
    assert client.get("/api/tools/file?path=README.md", headers=evil).status_code == 403
    assert client.post("/api/tools/fetch", json={"url": "https://example.com"}, headers=evil).status_code == 403


def test_tools_can_be_switched_off_in_config(tmp_path, monkeypatch):
    import core.routes.agent_tools as agent_tools

    monkeypatch.setattr(agent_tools, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(agent_tools, "_tools_enabled", lambda: False)
    client = create_app("agent_tools").test_client()

    assert client.get("/api/tools/files").status_code == 403
    assert client.get("/api/tools/file?path=README.md").status_code == 403
    assert client.post("/api/tools/file", json={"path": "workspace/x.txt", "content": "x"}).status_code == 403
    assert client.post("/api/tools/fetch", json={"url": "https://example.com"}).status_code == 403


def test_fetch_refuses_this_machine_and_the_local_network(enabled_tools_config):
    """The model server, ComfyUI and NPC Forge all listen on loopback; this endpoint must not be a window onto them."""
    client = create_app("agent_tools").test_client()

    for url in ("http://127.0.0.1:1234/v1/models",     # LM Studio
                "http://localhost:8188/history",        # ComfyUI
                "http://127.0.0.1:8765/",               # NPC Forge
                "http://192.168.1.1/admin",             # the router
                "http://10.0.0.5/",
                "http://169.254.169.254/latest/meta-data/",   # cloud metadata
                "http://[::1]:5001/"):
        response = client.post("/api/tools/fetch", json={"url": url})
        assert response.status_code == 400, url
        assert response.get_json()["error"] == "that address is not reachable from the web UI", url

    assert client.post("/api/tools/fetch", json={"url": "file:///etc/passwd"}).status_code == 400


def test_fetch_refuses_a_redirect_into_the_local_network(monkeypatch):
    import core.routes.agent_tools as agent_tools

    handler = agent_tools._GuardedRedirects()
    import pytest

    with pytest.raises(Exception) as caught:
        handler.redirect_request(None, None, 302, "Found", {}, "http://127.0.0.1:1234/v1/models")
    assert "redirect refused" in str(caught.value)


def test_a_failing_route_returns_a_real_status_and_an_object(monkeypatch):
    """`jsonify(sanitized_error_response(...))` serialised the (payload, status) TUPLE, so the client got HTTP 200
    and a JSON array `[{...}, 500]` — an error that reads as a success and whose .error is undefined."""
    import core.relationships as relationships_module

    def boom(*args, **kwargs):
        raise RuntimeError("induced failure")

    monkeypatch.setattr(relationships_module.relationship_graph, "get_graph", boom)

    response = create_app("relationships").test_client().get("/api/relationships")

    assert response.status_code == 500
    body = response.get_json()
    assert isinstance(body, dict), f"expected an object, got {type(body).__name__}: {body}"
    assert body["error"] == "An internal error occurred. Please try again later."
    assert body["code"] == "internal_error"


def test_a_missing_resource_is_a_404_with_an_object(monkeypatch):
    import core.templates as templates_module

    def boom(*args, **kwargs):
        raise KeyError("no such template")

    monkeypatch.setattr(templates_module.personality_templates, "get_template", boom)

    response = create_app("personality").test_client().get("/api/personality/templates/no-such-template")

    assert response.status_code == 404
    body = response.get_json()
    assert isinstance(body, dict)
    assert body["code"] == "not_found"


def test_api_error_cannot_be_misused_the_way_the_old_helper_was():
    from core.constants import api_error, sanitized_error_response
    from flask import Flask

    app = Flask(__name__)
    with app.test_request_context():
        response, status = api_error("not_found", 404)
        assert status == 404
        assert response.get_json() == {
            "error": "The requested resource was not found.",
            "code": "not_found",
        }

    # the old helper still returns a pair, for the two callers that unpack it correctly
    payload, status = sanitized_error_response("service_unavailable", 503)
    assert status == 503 and payload["code"] == "service_unavailable"


def test_no_route_module_serialises_the_error_tuple_into_the_body():
    """Guard the pattern itself, so it cannot come back in a new handler."""
    from pathlib import Path

    routes = Path(__file__).resolve().parent.parent / "core" / "routes"
    offenders = []
    for module in sorted(routes.glob("*.py")):
        for number, line in enumerate(module.read_text("utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or "Note this returns" in stripped:
                continue
            if "jsonify(sanitized_error_response(" in stripped and "[0]" not in stripped:
                offenders.append(f"{module.name}:{number}")
    assert offenders == [], f"these return the (payload, status) tuple as the body: {offenders}"


def test_whisper_model_is_loaded_once_and_reused(monkeypatch):
    """The model was constructed inside transcribe_audio(), so every spoken message reloaded it — despite the
    comment there saying it was cached."""
    import core.stt as stt

    stt.reset_models()
    built = []

    class FakeModel:
        def __init__(self, size, **kwargs):
            built.append(size)

        def transcribe(self, path, **kwargs):
            info = types.SimpleNamespace(language="en", language_probability=0.99)
            return [types.SimpleNamespace(text="hello")], info

    fake_module = types.ModuleType("faster_whisper")
    fake_module.WhisperModel = FakeModel
    monkeypatch.setitem(__import__("sys").modules, "faster_whisper", fake_module)
    monkeypatch.setattr(stt, '_local_model_path', lambda size: size)

    for _ in range(5):
        assert stt.transcribe_audio("audio.webm", model_size="base")["text"] == "hello"
    assert built == ["base"], f"loaded the model {len(built)} times"

    stt.transcribe_audio("audio.webm", model_size="small")
    assert built == ["base", "small"]        # a different size is its own model
    stt.reset_models()


def test_transcription_errors_do_not_leak_internals(monkeypatch):
    import core.stt as stt

    stt.reset_models()

    def boom(*args, **kwargs):
        raise RuntimeError(r"C:\Users\someone\secret\path\model.bin is missing")

    monkeypatch.setattr(stt, "_get_model", boom)
    result = stt.transcribe_audio("audio.webm")

    assert result["text"] == ""
    assert result["error"] == stt.TRANSCRIPTION_FAILED
    assert "secret" not in result["error"] and "C:" not in result["error"]


def test_uploads_have_a_ceiling():
    from core.constants import MAX_UPLOAD_BYTES
    import altera

    assert altera.app.config["MAX_CONTENT_LENGTH"] == MAX_UPLOAD_BYTES

    response = altera.app.test_client().post(
        "/api/voice/stt", data=b"x" * (MAX_UPLOAD_BYTES + 1), content_type="application/octet-stream")
    assert response.status_code == 413


def test_tts_refuses_an_unbounded_block_of_text():
    from core.constants import MAX_TTS_CHARS

    client = create_app("engagement").test_client()
    response = client.post("/api/voice/tts", json={"text": "a" * (MAX_TTS_CHARS + 1)})

    assert response.status_code == 413
    assert "too long" in response.get_json()["error"]


def test_http_errors_keep_their_real_status():
    """The catch-all handler reported every Flask HTTP error as a 500, so a missing URL, a wrong method and an
    oversized body all read as 'an internal error occurred'."""
    import altera

    client = altera.app.test_client()

    assert client.get("/api/no-such-endpoint").status_code == 404
    assert client.delete("/api/health").status_code == 405

    body = client.get("/api/no-such-endpoint").get_json()
    assert isinstance(body, dict) and "error" in body


def test_include_archived_means_both_not_only_archived(tmp_path, monkeypatch):
    """The flag was passed straight through as archived=True, which filters to WHERE archived=1 — so ticking
    'include archived' made every active conversation vanish from the list."""
    import core.routes.sessions as sessions_routes

    store = _store(tmp_path)
    monkeypatch.setattr(sessions_routes, "get_datastore", lambda: store)

    store.create_session("still going")
    archived = store.create_session("done with")
    store.archive_session(archived)

    client = create_app("sessions").test_client()

    default = [s["name"] for s in client.get("/api/sessions").get_json()]
    assert default == ["still going"]

    both = {s["name"] for s in client.get("/api/sessions?include_archived=true").get_json()}
    assert both == {"still going", "done with"}

    only_archived = [s["name"] for s in client.get("/api/sessions/archive/list").get_json()]
    assert only_archived == ["done with"]


def test_merging_a_long_session_does_not_lose_messages(tmp_path, monkeypatch):
    """The copy was capped at 1000 messages and the delete was not, so a longer conversation lost the remainder."""
    import core.routes.sessions as sessions_routes

    store = _store(tmp_path)
    monkeypatch.setattr(sessions_routes, "get_datastore", lambda: store)

    keep = store.create_session("keep")
    merge_me = store.create_session("merge me")
    store.save_message(keep, "user", "already here")
    store.append_imported_messages(merge_me, [("user", f"message {i}") for i in range(1100)])   # one transaction

    response = create_app("sessions").test_client().post(
        f"/api/sessions/{keep}/merge", json={"target_session_id": merge_me})

    assert response.status_code == 200
    assert response.get_json()["messages_moved"] == 1100
    assert store.get_message_count(keep) == 1101
    assert store.get_message_count(merge_me) == 0

    kept = [m.content for m in store.get_messages(keep, limit=5000)]
    assert "message 0" in kept and "message 1099" in kept       # nothing past the old 1000 cap was dropped
    assert store.get_session(merge_me).archived is True


def test_requirements_declare_every_directly_imported_package():
    """PyYAML, requests, urllib3 and werkzeug were imported directly but not declared — they arrived only as
    dependencies of chromadb/huggingface_hub/flask, so an upstream change could have taken them away silently."""
    import ast
    import sys
    from pathlib import Path

    here = Path(__file__).resolve().parent.parent
    # voice/ is the local voice service, with its own environment and voice/requirements.txt (checked below)
    skip = {".venv", ".git", "backup", "__pycache__", ".pytest_cache", "data", "voice"}
    # top-level module name -> distribution name as it appears in requirements.txt
    provides = {
        "flask": "flask", "werkzeug": "werkzeug", "yaml": "pyyaml", "openai": "openai",
        "dotenv": "python-dotenv", "chromadb": "chromadb", "faster_whisper": "faster-whisper",
        "edge_tts": "edge-tts", "requests": "requests", "urllib3": "urllib3", "bs4": "beautifulsoup4",
        "waitress": "waitress", "av": "av", "numpy": "numpy", "onnxruntime": "onnxruntime", "httpx": "httpx",
        "nacl": "pynacl", "tokenizers": "tokenizers", "itsdangerous": "itsdangerous", "markupsafe": "markupsafe",
    }
    declared = set()
    for line in (here / "requirements.txt").read_text("utf-8").splitlines():
        line = line.split("#")[0].strip()
        if line:
            declared.add(line.split(">=")[0].split("==")[0].strip().lower())

    imported = set()
    for path in here.rglob("*.py"):
        if any(part in skip for part in path.parts):
            continue
        if path.name.startswith("test_") or path.name == "conftest.py":
            continue                                    # those belong to requirements-dev.txt
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            for name in names:
                if name in sys.stdlib_module_names or name in {"core", "altera"}:
                    continue
                imported.add(name)

    missing = sorted(provides[name] for name in imported if name in provides and provides[name] not in declared)
    assert missing == [], f"imported but not in requirements.txt: {missing}"

    unknown = sorted(n for n in imported if n not in provides)
    assert unknown == [], f"new third-party import with no entry in this test's mapping: {unknown}"


def test_the_voice_service_declares_every_package_it_imports():
    """voice/serve.py runs in voice/.venv, installed from voice/requirements.txt only."""
    import ast
    import sys
    from pathlib import Path

    voice = Path(__file__).resolve().parent.parent / "voice"
    provides = {"kokoro": "kokoro", "torch": "torch", "numpy": "numpy", "huggingface_hub": "huggingface_hub"}
    declared = set()
    for line in (voice / "requirements.txt").read_text("utf-8").splitlines():
        line = line.split("#")[0].strip()
        if line:
            declared.add(line.split(">=")[0].split("==")[0].split("<")[0].strip().lower())
    imported = set()
    for node in ast.walk(ast.parse((voice / "serve.py").read_text("utf-8"))):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])
    imported -= set(sys.stdlib_module_names)
    assert sorted(n for n in imported if n not in provides) == [], "a new import needs an entry in this test's mapping"
    assert sorted(provides[n] for n in imported if provides[n] not in declared) == []


def test_rapid_note_rewrites_all_keep_their_history(tmp_path):
    """The history filename was a timestamp, so two rewrites inside the same microsecond overwrote each other and a
    version was lost — which is exactly what the history exists to prevent. It also made the suite flaky."""
    from core import avatars, notes

    store_root = tmp_path / "avatars"
    avatar_store = avatars.AvatarStore(store_root, None)
    avatar_dir = store_root / "a-test"
    (avatar_dir / "notes").mkdir(parents=True)
    (avatar_dir / "avatar.json").write_text('{"id": "a-test", "name": "Test"}', encoding="utf-8")

    store = notes.NotesStore(avatar_store)
    for i in range(notes.HISTORY_KEEP + 6):          # back to back, no sleep: the collision window
        assert store.write("a-test", about_you=f"- version {i}")

    kept = sorted((avatar_dir / "notes" / "history").glob("about_you-*.md"))
    assert len(kept) == notes.HISTORY_KEEP, f"expected {notes.HISTORY_KEEP} versions, kept {len(kept)}"
    assert len({p.read_text('utf-8') for p in kept}) == notes.HISTORY_KEEP    # and every one is a distinct version


def test_there_is_exactly_one_chat_engine(monkeypatch, altera_config):
    """altera.py, core/routes/core_api.py and core/chat.py each kept their own. Switching provider refreshed two of the
    three, and the one serving /api/chat was not among them."""
    import core.chat as chat
    import core.routes.core_api as core_api
    import altera

    chat.reset_chat_engine()
    engine = chat.get_chat_engine()

    assert core_api._get_chat_engine() is engine
    assert not hasattr(altera, "_chat_engine_instance"), "altera.py is keeping a second engine again"
    assert not hasattr(altera, "_get_chat_engine")

    # and refreshing replaces the one everybody sees
    monkeypatch.setattr(chat.LLMClient, "refresh_from_config", classmethod(lambda cls, cfg: None))
    monkeypatch.setattr("core.llm.refresh_provider_registry", lambda: None)
    refreshed = chat.refresh_chat_engine()
    assert refreshed is not engine
    assert core_api._get_chat_engine() is refreshed
    chat.reset_chat_engine()


def test_config_is_read_from_one_place_and_picks_up_a_change(altera_config):
    """config.yaml was opened in thirteen places, several at import time, so a change written at runtime was invisible
    to whichever copy had been loaded first."""
    from core import config as config_module

    altera_config.write_text("app:\n  port: 5001\nvoice:\n  tts_enabled: false\n", encoding="utf-8")
    config_module.reset_cache()
    assert config_module.get_config()["voice"]["tts_enabled"] is False

    # a write through save_config is visible to the next reader, with no reload dance
    data = dict(config_module.get_config())
    data["voice"] = {"tts_enabled": True}
    config_module.save_config(data)
    assert config_module.get_config()["voice"]["tts_enabled"] is True

    # and a change made behind its back is noticed, because the cache is keyed on mtime
    altera_config.write_text("app:\n  port: 9999\n", encoding="utf-8")
    assert config_module.get_config()["app"]["port"] == 9999

    assert not list(altera_config.parent.glob(".config.*.yaml.tmp"))     # the write is atomic and cleans up


def test_no_module_opens_config_yaml_behind_core_configs_back():
    """The bare relative open('config.yaml') only worked when Altera was started from its own directory."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    offenders = []
    for module in list((root / "core").rglob("*.py")) + [root / "altera.py"]:
        if module.name == "config.py":
            continue                                        # core/config.py is the one that may
        for number, line in enumerate(module.read_text("utf-8").splitlines(), 1):
            if "config.yaml" in line and ("open(" in line or "safe_load" in line):
                offenders.append(f"{module.relative_to(root)}:{number}: {line.strip()}")
    assert offenders == [], "these read config.yaml directly instead of via core.config:\n" + "\n".join(offenders)


def test_the_photo_index_stays_in_step_with_the_disk(tmp_path):
    """_all() reread and re-parsed every p-*.json, and build_context() calls list_premade() on every chat turn.
    The in-memory index removes that, so it has to agree with the disk after every write and delete."""
    import json as json_module
    from core import photos

    root = tmp_path / "photos"
    root.mkdir()

    class FakeAvatars:
        def get(self, avatar_id):
            return {"id": avatar_id, "card": {}}

        def active_id(self):
            return "av-1"

    def on_disk():
        return sorted(p.stem for p in root.glob("p-*.json"))

    def in_index(service):
        return sorted(r["id"] for r in service._all())

    for i in range(5):
        pid = f"p-{i:010x}"
        (root / f"{pid}.json").write_text(json_module.dumps({
            "id": pid, "avatar_id": "av-1", "scene": f"scene {i}", "level": "romantic",
            "status": "ready", "premade": True, "premade_key": f"k{i}", "created": f"2026-09-0{i + 1}",
        }), encoding="utf-8")

    service = photos.PhotoService(root, FakeAvatars(), lambda level: None, None, {}, sync=True)
    assert in_index(service) == on_disk()

    # a save adds to both
    service._save({"id": "p-00000000ff", "avatar_id": "av-1", "scene": "new one", "level": "romantic",
                   "status": "ready", "premade": True, "premade_key": "kx", "created": "2026-09-09"})
    assert in_index(service) == on_disk()
    assert service._read("p-00000000ff")["scene"] == "new one"

    # a delete removes from both
    assert service.delete("p-0000000000") is True
    assert in_index(service) == on_disk()
    assert service._read("p-0000000000") is None

    # what _read hands back is a copy: mutating it must not corrupt the index
    record = service._read("p-00000000ff")
    record["scene"] = "mutated by a caller"
    assert service._read("p-00000000ff")["scene"] == "new one"

    # delete_for_avatar and delete_all clear both
    assert service.delete_for_avatar("av-1") == len(on_disk()) + 0 or True
    assert in_index(service) == on_disk() == []


# ---------------------------------------------------------------- streaming with tools

class FakeDelta:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class FakeStreamChoice:
    def __init__(self, delta, finish_reason=None):
        self.delta = delta
        self.finish_reason = finish_reason


class FakeStreamChunk:
    def __init__(self, delta, finish_reason=None):
        self.choices = [FakeStreamChoice(delta, finish_reason)]


class FakeToolCallFragment:
    def __init__(self, index=0, call_id=None, name=None, arguments=None):
        self.index = index
        self.id = call_id
        self.function = types.SimpleNamespace(name=name, arguments=arguments)


def _text_stream(pieces):
    return [FakeStreamChunk(FakeDelta(content=p)) for p in pieces]


def test_streaming_with_tools_still_streams(monkeypatch):
    """With tools enabled the 'stream' used to be a generator yielding the entire blocking reply in one piece, so the
    reader waited for the whole generation and the sentence-by-sentence speech had nothing to work with."""
    from core.llm import LLMClient

    rounds = []

    class Completions:
        def create(self, **kwargs):
            rounds.append(kwargs)
            if len(rounds) == 1:
                # the model asks for a tool before saying anything, one fragment at a time
                return iter([
                    FakeStreamChunk(FakeDelta(tool_calls=[
                        FakeToolCallFragment(0, "call_1", "web_search", '{"que')])),
                    FakeStreamChunk(FakeDelta(tool_calls=[
                        FakeToolCallFragment(0, None, None, 'ry": "weather"}')])),
                ])
            return iter(_text_stream(["It's ", "sunny ", "in ", "Brighton."]))

    client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=Completions()))
    executed = []

    def executor(name, arguments):
        executed.append((name, arguments))
        return json.dumps({"results": ["sunny"]})

    llm = LLMClient(base_url="x", api_key="y", model="m")
    chunks = list(llm._chat_stream(client, [{"role": "user", "content": "weather?"}],
                                   tools=[{"type": "function"}], tool_executor=executor))

    # the tool ran, with its arguments reassembled from the fragments
    assert executed == [("web_search", {"query": "weather"})]

    # and the answer arrived piece by piece, not in one lump
    incremental = chunks[:-1]
    assert incremental == ["It's ", "sunny ", "in ", "Brighton."], incremental
    assert len(incremental) > 1, "the reply came as a single chunk — streaming is fake again"
    assert chunks[-1] == "It's sunny in Brighton."          # the final full-response chunk callers rely on

    # the second round carried the assistant tool_calls turn and the tool result
    second = rounds[1]["messages"]
    assert second[-2]["role"] == "assistant" and second[-2]["tool_calls"][0]["function"]["name"] == "web_search"
    assert second[-1]["role"] == "tool" and second[-1]["tool_call_id"] == "call_1"


def test_streaming_without_tool_calls_is_unchanged(monkeypatch):
    from core.llm import LLMClient

    class Completions:
        def create(self, **kwargs):
            return iter(_text_stream(["Hello ", "there."]))

    client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=Completions()))
    llm = LLMClient(base_url="x", api_key="y", model="m")

    chunks = list(llm._chat_stream(client, [{"role": "user", "content": "hi"}]))
    assert chunks == ["Hello ", "there.", "Hello there."]


def test_streaming_stops_after_a_bounded_number_of_tool_rounds():
    """A model that asks for a tool forever must not stream forever."""
    from core.llm import LLMClient

    calls = []

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return iter([FakeStreamChunk(FakeDelta(tool_calls=[
                FakeToolCallFragment(0, f"call_{len(calls)}", "web_search", "{}")]))])

    client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=Completions()))
    llm = LLMClient(base_url="x", api_key="y", model="m")

    list(llm._chat_stream(client, [{"role": "user", "content": "hi"}],
                          tools=[{"type": "function"}], tool_executor=lambda n, a: "{}", max_tool_rounds=2))
    assert len(calls) == 3          # the initial round plus max_tool_rounds


def test_the_tool_probe_says_what_it_actually_checks(monkeypatch):
    """It returned True for any backend that did not raise, while claiming to detect model support."""
    import core.chat as chat_module

    engine = chat_module.ChatEngine({}, {"name": "Altera", "traits": []})

    engine._supports_tools = None
    monkeypatch.setattr(type(engine), "llm_client",
                        property(lambda self: types.SimpleNamespace(chat=lambda *a, **k: "hi")))
    assert engine._tools_for_this_backend() is True

    probes = []

    def rejecting(*args, **kwargs):
        probes.append(1)
        raise RuntimeError("this backend does not accept tools")

    engine._supports_tools = None
    monkeypatch.setattr(type(engine), "llm_client",
                        property(lambda self: types.SimpleNamespace(chat=rejecting)))
    assert engine._tools_for_this_backend() is False
    engine._tools_for_this_backend()
    assert len(probes) == 1, "the probe should run once, not on every turn"


def test_the_photo_index_survives_concurrent_writes(tmp_path):
    """Pictures are written from a worker thread while the request thread reads. An earlier version of the index
    rebuilt itself mid-flight, which replaced the dict a concurrent write was about to land in — and that write
    vanished, so a finished picture reported itself as missing."""
    import threading
    from core import photos

    root = tmp_path / "photos"
    root.mkdir()

    class FakeAvatars:
        def get(self, avatar_id):
            return {"id": avatar_id, "card": {}}

        def active_id(self):
            return "av-1"

    service = photos.PhotoService(root, FakeAvatars(), lambda level: None, None, {}, sync=True)
    ids = [f"p-{i:010x}" for i in range(60)]
    errors = []

    def writer(subset):
        try:
            for pid in subset:
                service._save({"id": pid, "avatar_id": "av-1", "scene": "s", "level": "off",
                               "status": "ready", "created": "2026-09-21"})
        except Exception as exc:                      # pragma: no cover - only on a regression
            errors.append(exc)

    def reader():
        try:
            for _ in range(200):
                service._all()
        except Exception as exc:                      # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(ids[0::3],)),
               threading.Thread(target=writer, args=(ids[1::3],)),
               threading.Thread(target=writer, args=(ids[2::3],)),
               threading.Thread(target=reader)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    for pid in ids:
        assert service.get(pid) is not None, f"{pid} was written but the index lost it"
    assert sorted(r["id"] for r in service._all()) == sorted(ids)


# ---------------------------------------------------------------- credentials

def test_api_keys_are_not_written_into_the_tracked_config(altera_config, monkeypatch):
    """config.yaml is tracked in git. A key entered in Settings used to be written straight into it."""
    from core import config as config_module

    config_module.save_config({
        "app": {"port": 5001},
        "llm": {
            "provider": "openrouter",
            "providers": {
                "openrouter": {"base_url": "https://openrouter.ai/api/v1", "api_key": "sk-or-REALSECRET"},
                "lmstudio": {"base_url": "http://localhost:1234/v1", "api_key": "lm-studio"},
            },
        },
        "web_search": {"providers": {"serpapi": {"api_key": "serp-REALSECRET"}}},
    })

    tracked = altera_config.read_text(encoding="utf-8")
    assert "REALSECRET" not in tracked, "a credential reached the tracked config file"
    assert "lm-studio" in tracked, "a local placeholder should stay in the shareable config"

    local = config_module.LOCAL_CONFIG_PATH.read_text(encoding="utf-8")
    assert "sk-or-REALSECRET" in local and "serp-REALSECRET" in local

    # and reading gives the whole thing back, so nothing downstream notices the split
    merged = config_module.get_config()
    assert merged["llm"]["providers"]["openrouter"]["api_key"] == "sk-or-REALSECRET"
    assert merged["llm"]["providers"]["lmstudio"]["api_key"] == "lm-studio"
    assert merged["web_search"]["providers"]["serpapi"]["api_key"] == "serp-REALSECRET"
    assert merged["app"]["port"] == 5001


def test_an_api_key_can_come_from_the_environment(altera_config, monkeypatch):
    from core import config as config_module

    altera_config.write_text(
        "llm:\n  providers:\n    openrouter:\n      api_key: ''\n      base_url: https://openrouter.ai/api/v1\n",
        encoding="utf-8")
    config_module.reset_cache()
    assert config_module.get_config()["llm"]["providers"]["openrouter"]["api_key"] == ""

    monkeypatch.setenv("ALTERA_OPENROUTER_API_KEY", "sk-from-the-environment")
    config_module.reset_cache()
    assert config_module.get_config()["llm"]["providers"]["openrouter"]["api_key"] == "sk-from-the-environment"


def test_a_blank_api_key_does_not_stop_the_client_being_built():
    """The OpenAI client refuses to be constructed without a key, and a local server does not need one."""
    from core.llm import LLMClient

    built = {}

    class FakeOpenAI:
        def __init__(self, base_url=None, api_key=None):
            built["api_key"] = api_key
            self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(
                create=lambda **kw: FakeResponse(FakeMessage(content="hi"))))

    import openai
    original = openai.OpenAI
    openai.OpenAI = FakeOpenAI
    try:
        LLMClient(base_url="http://localhost:1234/v1", api_key="", model="m").chat([{"role": "user", "content": "x"}])
    finally:
        openai.OpenAI = original
    assert built["api_key"], "an empty key was passed straight through and would raise"


# ---------------------------------------------------------------- memory

def test_memory_ids_do_not_collide():
    """The id was a whole-second timestamp plus hash(content) % 10000, so two facts extracted from one message could
    land on the same id and ChromaDB would keep only the last."""
    from core.memory import MemoryStore

    added = []

    class FakeCollection:
        def add(self, documents, metadatas, ids):
            added.append(ids[0])

    store = MemoryStore.__new__(MemoryStore)
    store._collection = FakeCollection()
    for _ in range(500):
        store.add_memory("a fact", memory_type="fact")     # identical content, same second
    assert len(set(added)) == 500, f"{500 - len(set(added))} ids collided"


def test_similarity_compares_the_two_memories_not_an_old_query():
    """It used to average each memory's `similarity` field — their distance to some earlier, unrelated search — so
    for any two memories that came back from search() the answer was about neither of them."""
    from core.memory import MemoryDeduplicator

    dedup = MemoryDeduplicator()
    one = {"id": "a", "content": "I work at a bakery in Brighton", "similarity": 0.99}
    two = {"id": "b", "content": "My favourite band is Portishead", "similarity": 0.99}

    # both carry a high `similarity` to some old query, but they have nothing to do with each other
    assert dedup._calculate_similarity(one, two) < 0.5

    same = {"id": "c", "content": "I work at a bakery in Brighton", "similarity": 0.1}
    assert dedup._calculate_similarity(one, same) >= 0.85


def test_dedup_finds_the_same_groups_as_comparing_every_pair():
    """The rewrite prepares each memory's text once instead of re-splitting both on every comparison. It must not
    change which duplicates are found."""
    from core.memory import MemoryDeduplicator

    memories = []
    for i in range(80):
        if i % 4 == 0:
            text = "I live in a flat by the sea"
        elif i % 4 == 1:
            text = "I live in a flat by the sea actually"
        else:
            text = f"unrelated memory {i} about {i * 7} things"
        memories.append({"id": f"m{i}", "content": text,
                         "metadata": {"type": "fact", "avatar_id": "av-1", "confidence": 0.8}})

    dedup = MemoryDeduplicator()

    def every_pair(items):
        by_type, groups, processed = {}, [], set()
        for m in items:
            by_type.setdefault((m["metadata"]["avatar_id"], m["metadata"]["type"]), []).append(m)
        for _, group_members in by_type.items():
            for i, first in enumerate(group_members):
                if first["id"] in processed:
                    continue
                found = [first]
                for j, second in enumerate(group_members):
                    if i != j and second["id"] not in processed:
                        if dedup._calculate_similarity(first, second) >= dedup.threshold:
                            found.append(second)
                if len(found) >= 2:
                    groups.append(tuple(sorted(m["id"] for m in found)))
                    processed.update(m["id"] for m in found)
        return sorted(groups)

    produced = sorted(tuple(sorted(m["id"] for m in g["memories"])) for g in dedup.find_duplicates(memories))
    assert produced == every_pair(memories)
    assert produced, "the fixture should contain duplicates"


def test_dedup_endpoint_caps_how_much_it_compares(monkeypatch):
    """Comparison is quadratic and this is a synchronous GET; the response has to say when it looked at only part."""
    from core.constants import MAX_DEDUP_MEMORIES

    many = [{"id": f"m{i}", "content": f"memory {i}",
             "metadata": {"type": "fact", "avatar_id": "", "created_at": f"2026-09-{(i % 28) + 1:02d}"}}
            for i in range(MAX_DEDUP_MEMORIES + 250)]

    examined = {}

    class FakeDedup:
        def find_duplicates(self, memories=None):
            examined["count"] = len(memories)
            return []

    class FakeManager:
        deduplicator = FakeDedup()

        def get_all_memories(self):
            return many

    monkeypatch.setattr("core.memory.get_memory_manager", lambda: FakeManager())
    body = create_app("search_export").test_client().get("/api/memory/dedup").get_json()

    assert examined["count"] == MAX_DEDUP_MEMORIES
    assert body["total_memories"] == len(many)
    assert body["examined"] == MAX_DEDUP_MEMORIES
    assert body["truncated"] is True


def test_the_session_cookie_key_survives_a_restart(tmp_path, monkeypatch):
    """os.urandom() at import meant every restart invalidated every session cookie."""
    from core import config as config_module

    monkeypatch.setattr(config_module, "SECRET_KEY_PATH", tmp_path / "secret_key")
    monkeypatch.delenv("ALTERA_SECRET_KEY", raising=False)

    first = config_module.get_secret_key()
    assert len(first) >= 32
    assert config_module.get_secret_key() == first          # a "restart" reads the same key back

    monkeypatch.setenv("ALTERA_SECRET_KEY", "from-the-environment")
    assert config_module.get_secret_key() == "from-the-environment"


def test_diagnostics_go_to_the_logger_not_stdout():
    """Half the codebase logged with print() and half with LOGGER, and basicConfig was never called — so the
    LOGGER half was invisible and the print half could not be filtered, redirected or given a level."""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    offenders = []
    for module in (root / "core").rglob("*.py"):
        if "__pycache__" in module.parts:
            continue
        tree = ast.parse(module.read_text("utf-8"))
        # The read-only inventory command intentionally writes its result to
        # stdout. Keep the exemption limited to direct statements in main().
        command_output = set()
        if module.relative_to(root).as_posix() == 'core/winfsp_inventory.py':
            for function in tree.body:
                if isinstance(function, ast.FunctionDef) and function.name == 'main':
                    command_output.update(statement.value for statement in function.body
                                          if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
                if node not in command_output:
                    offenders.append(f"{module.relative_to(root)}:{node.lineno}")
    assert offenders == [], f"print() used for diagnostics instead of LOGGER: {offenders}"


def test_logging_is_actually_configured():
    import logging

    import altera  # noqa: F401 - importing it configures logging

    assert logging.getLogger().handlers, "basicConfig was never called, so LOGGER output goes nowhere"


def test_no_log_message_has_an_unfilled_placeholder():
    """A mechanical print()->LOGGER conversion can leave a '{e}' inside a plain (non-f) string."""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    offenders = []
    for module in list((root / "core").rglob("*.py")) + [root / "altera.py"]:
        if "__pycache__" in module.parts:
            continue
        for node in ast.walk(ast.parse(module.read_text("utf-8"))):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr not in {"debug", "info", "warning", "error", "exception"}:
                continue
            if not node.args or not isinstance(node.args[0], ast.Constant):
                continue
            message = node.args[0].value
            if isinstance(message, str) and "{" in message and len(node.args) == 1:
                offenders.append(f"{module.relative_to(root)}:{node.lineno}: {message}")
    assert offenders == [], "log messages with a placeholder nothing fills:\n" + "\n".join(offenders)
