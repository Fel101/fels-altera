"""Smoke tests for blueprint imports and lightweight route behavior."""
import json
from pathlib import Path

from flask import Flask

ROOT = Path(__file__).resolve().parent.parent       # the app's templates/ and static/


BLUEPRINTS = [
    ("pages", "core.routes.pages"),
    ("sessions", "core.routes.sessions"),
    ("core_api", "core.routes.core_api"),
    ("engagement", "core.routes.engagement"),
    ("admin", "core.routes.admin"),
    ("relationships", "core.routes.relationships"),
    ("personality", "core.routes.personality"),
    ("search_export", "core.routes.search_export"),
    ("jokes_opinions", "core.routes.jokes_opinions"),
    ("llm", "core.routes.llm"),
    ("agent_tools", "core.routes.agent_tools"),
    ("companion", "core.routes.companion"),
    ("companions", "core.routes.companions"),
    ("backups", "core.routes.backups"),
    ("knowledge", "core.routes.knowledge"),
    ("system", "core.routes.system"),
    ("setup", "core.routes.setup"),
    ("lock", "core.routes.lock"),
]


def create_app(*blueprint_names):
    app = Flask(__name__, root_path=str(ROOT))
    app.secret_key = "test-secret-key"

    selected = set(blueprint_names) if blueprint_names else {name for name, _ in BLUEPRINTS}
    for name, module_name in BLUEPRINTS:
        if name not in selected:
            continue
        module = __import__(module_name, fromlist=["bp"])
        app.register_blueprint(module.bp)
    from core import addons
    if value := addons.registry():
        from core.request_security import install_request_security
        install_request_security(app)
        addons.mount(app, value)

    return app


def test_all_blueprints_import_and_register():
    app = create_app()

    for name, _ in BLUEPRINTS:
        assert name in app.blueprints


def test_chat_rejects_empty_message_without_llm_call():
    app = create_app("core_api")

    response = app.test_client().post("/api/chat", json={"message": "   "})

    assert response.status_code == 400
    assert response.get_json()["error"] == "Message cannot be empty"


def test_stream_rejects_invalid_session_without_llm_call():
    app = create_app("core_api")

    response = app.test_client().post(
        "/api/chat/stream",
        json={"session_id": "not-an-int", "message": "hello"},
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "Invalid session_id"


def test_a_data_export_says_when_it_has_no_memories_because_they_could_not_be_read(monkeypatch):
    import core.memory

    class Unreadable:
        def __init__(self):
            raise OSError("the memory store is locked")
    monkeypatch.setattr(core.memory, "MemoryManager", Unreadable)
    response = create_app("search_export").test_client().post("/api/export", json={"format": "json"})
    assert response.status_code == 200
    data = json.loads(response.get_data(as_text=True))
    assert data["memories"] == []
    assert data["warnings"] == ["Memories could not be read, so this export has none. Try again, or make a backup."]


def test_there_are_no_life_events_routes_and_no_page_calls_one():
    from pathlib import Path
    here = Path(__file__).resolve().parent.parent
    assert not (here / "core" / "routes" / "events.py").exists()
    assert not [rule.rule for rule in create_app().url_map.iter_rules() if rule.rule.startswith("/api/events")]
    for page in (here / "templates").glob("*.html"):
        assert "api/events" not in page.read_text("utf-8"), page.name
