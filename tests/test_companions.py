"""Companions: one avatar, one chat. Opening, listing, removing, the move from the old chats, the routes, meeting someone new through
NPC Forge, and how she sees herself. NPC Forge and the models are fakes; nothing here touches the real data folder."""
import json
import types

import pytest

from core import companions, forge, notes, selfview
from core.datastore import init_db
from test_blueprints import create_app
from test_companion_api import active_avatar, tmp_store, world  # noqa: F401
from test_companion_images import PNG, make_forge


def second_avatar(world, npc_id="ana-cruz-11aa22", first="Ana"):
    make_forge(world.forge, npc_id=npc_id, first=first)
    return world.avatars.import_from_forge(npc_id)


class FakeMemoryManager:
    def __init__(self, ids=()):
        self.deleted, rows = [], [{"id": i} for i in ids]
        outer = self

        class Store:
            collection = types.SimpleNamespace(delete=lambda ids: outer.deleted.extend(ids))

            @staticmethod
            def get_all(avatar_id=None):
                return rows

        self.store = Store()


# ---------------------------------------------------------------- opening and listing

def test_a_chat_belongs_to_its_avatar_and_an_older_chat_uses_the_active_one(world):
    mara = active_avatar(world)
    ana = second_avatar(world)
    world.avatars.activate(mara["id"])
    chat = world.store.ensure_avatar_session(ana["id"], "Ana")
    assert companions.avatar_for_session(chat)["id"] == ana["id"]
    plain = world.store.create_session("older chat")                               # no avatar of its own
    assert companions.avatar_for_session(plain)["id"] == mara["id"]
    assert companions.avatar_for_session(None)["id"] == mara["id"]
    assert world.store.get_session(chat).avatar_id == ana["id"] and world.store.get_avatar_session_id(mara["id"]) is None


def test_opening_a_companion_activates_her_and_makes_her_one_chat_once(world):
    mara, ana = active_avatar(world), second_avatar(world)
    first = companions.open_companion(ana["id"])
    assert first["id"] == ana["id"] and first["session_id"] and first["messages"] == 0 and first["met"] is False
    assert world.avatars.active_id() == ana["id"] and world.store.get_last_active_session() == first["session_id"]
    assert companions.open_companion(ana["id"])["session_id"] == first["session_id"]
    assert companions.open_companion(mara["id"])["session_id"] != first["session_id"]
    assert len(world.store.list_avatar_sessions(ana["id"])) == 1
    with pytest.raises(companions.CompanionError):
        companions.open_companion("nobody-here-1")


@pytest.mark.requires_addon
def test_opening_a_companion_starts_fetching_her_studio_pictures(world):
    mara = active_avatar(world)
    calls = []
    world.photos.sync_premade_async = lambda avatar_id: calls.append(avatar_id)
    companions.open_companion(mara["id"])
    assert calls == [mara["id"]]


def test_the_list_shows_the_one_you_talked_to_last_first_with_how_much_you_have_said(world):
    mara, ana = active_avatar(world), second_avatar(world)
    a = companions.open_companion(mara["id"])["session_id"]
    companions.open_companion(ana["id"])
    world.store.save_message(a, "user", "hi")
    world.store.save_message(a, "assistant", "hello")
    rows = companions.list_companions()
    assert [r["id"] for r in rows] == [mara["id"], ana["id"]]                           # Mara has the newest message
    assert rows[0]["messages"] == 2 and rows[0]["met"] is True and rows[1]["messages"] == 0 and rows[1]["met"] is False
    assert rows[0]["traits"] == ["warm"] and rows[0]["occupation"] == "florist"


def test_the_page_opens_on_the_companion_you_talked_to_last_or_on_nobody(world):
    assert companions.current() == {"avatar": None, "session_id": None}
    mara = active_avatar(world)
    now = companions.current()
    assert now["avatar"]["id"] == mara["id"] and now["session_id"] == now["avatar"]["session_id"]


def test_removing_a_companion_removes_her_chat_notes_memories_and_pictures(world):
    mara, ana = active_avatar(world), second_avatar(world)
    sid = companions.open_companion(mara["id"])["session_id"]
    world.store.save_message(sid, "user", "hi")
    notes.NotesStore(world.avatars).write(mara["id"], about_you="- x")
    world.photos.import_premade(mara["id"], PNG + b"p", "in a cafe", "off", key="k:1")
    keep = companions.open_companion(ana["id"])["session_id"]
    memory = FakeMemoryManager(["m1", "m2"])
    result = companions.remove(mara["id"], memory_manager=memory)
    assert result == {"photos_removed": 1, "chats_removed": 1, "memories_removed": 2} and memory.deleted == ["m1", "m2"]
    assert world.avatars.get(mara["id"]) is None and world.store.get_session(sid) is None
    assert world.store.get_session(keep) is not None and world.avatars.get(ana["id"]) is not None
    with pytest.raises(companions.CompanionError):
        companions.remove(mara["id"], memory_manager=memory)


# ---------------------------------------------------------------- the move from the old chats

def test_old_chats_and_memories_become_the_companion_you_have(world):
    old = world.store.create_session("first")
    newer = world.store.create_session("second")
    empty = world.store.create_session("Default Chat")
    world.store.save_message(old, "user", "one")
    world.store.save_message(newer, "user", "two")
    world.store.save_message(newer, "assistant", "three")
    world.store.archive_session(old)
    world.store.update_session(empty, name="Default Chat")
    mara = active_avatar(world)
    given = []
    result = companions.migrate_legacy(assign_memories=lambda owner: given.append(owner) or 3, data_dir=world.tmp)
    assert result["owner"] == mara["id"] and result["memories"] == 3 and given == [mara["id"]]
    main = world.store.get_avatar_session_id(mara["id"])
    assert [m.content for m in world.store.get_messages(main, limit=50)] == ["one", "two", "three"]     # in order, nothing lost
    assert all(s.avatar_id == mara["id"] for s in world.store.list_sessions(archived=None))
    assert [s.id for s in world.store.list_sessions(archived=False)] == [main]
    assert (world.tmp / "companions.json").is_file()
    assert companions.migrate_legacy(data_dir=world.tmp) == {"skipped": "already done"}                   # only once


def test_the_move_waits_for_an_avatar_and_copies_the_database_first(world, tmp_path):
    world.store.create_session("chat")
    assert companions.migrate_legacy(data_dir=world.tmp) == {"skipped": "no companion yet"}
    assert not (world.tmp / "companions.json").exists()
    active_avatar(world)
    companions.migrate_legacy(assign_memories=lambda owner: 0, data_dir=world.tmp)
    assert list(world.tmp.glob("*.before-companions.db"))


def test_a_database_from_before_companions_gets_the_new_column_without_losing_rows(tmp_path):
    import sqlite3
    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.executescript("CREATE TABLE sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, tags TEXT DEFAULT '', "
                       "archived BOOLEAN DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
                       "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP); INSERT INTO sessions (name) VALUES ('kept');")
    conn.commit()
    conn.close()
    init_db(path)
    init_db(path)                                                                            # twice is fine
    from core.datastore import DataStore
    store = DataStore(path)
    assert [(s.name, s.avatar_id) for s in store.list_sessions(archived=None)] == [("kept", "")]


def test_the_datastore_counts_and_moves_messages(tmp_store):
    a, b = tmp_store.create_session("a"), tmp_store.create_session("b")
    first = tmp_store.save_message(a, "user", "x")
    second = tmp_store.save_message(b, "user", "y")
    tmp_store.save_message(b, "assistant", "z")
    assert second == first + 1
    assert tmp_store.count_user_messages(b) == 1 and tmp_store.count_user_messages(b, after_id=second) == 0
    assert tmp_store.move_messages([b], a) == 2 and tmp_store.get_message_count(a) == 3 and tmp_store.get_message_count(b) == 0
    assert tmp_store.last_message_at(a) and tmp_store.last_message_at(b) == ""


# ---------------------------------------------------------------- the routes

@pytest.fixture
def client(world):
    return create_app("companions", "companion").test_client()


def test_the_companion_routes_list_open_and_remove(client, world, monkeypatch):
    mara, ana = active_avatar(world), second_avatar(world)
    listing = client.get("/api/companions").get_json()
    assert {c["id"] for c in listing["companions"]} == {mara["id"], ana["id"]} and listing["forge_library"] is True
    opened = client.post(f"/api/companions/{ana['id']}/open").get_json()
    assert opened["id"] == ana["id"] and opened["session_id"]
    assert client.get("/api/companions/current").get_json()["avatar"]["id"] == ana["id"]
    assert client.post("/api/companions/nobody-here-1/open").status_code == 404
    monkeypatch.setattr(companions, "remove", lambda avatar_id, memory_manager=None: {"photos_removed": 0, "chats_removed": 1, "memories_removed": 0})
    assert client.delete(f"/api/companions/{ana['id']}").get_json()["chats_removed"] == 1


def test_changing_routes_refuse_other_websites(client, world):
    mara = active_avatar(world)
    for headers in ({"Origin": "http://evil.example"}, {"Sec-Fetch-Site": "cross-site"}):
        assert client.post(f"/api/companions/{mara['id']}/open", headers=headers).status_code == 403
        assert client.delete(f"/api/companions/{mara['id']}", headers=headers).status_code == 403
        assert client.put(f"/api/companions/{mara['id']}/notes", json={"about_you": "x"}, headers=headers).status_code == 403
        assert client.post(f"/api/companions/{mara['id']}/notes/update", headers=headers).status_code == 403
        assert client.post(f"/api/companions/{mara['id']}/pictures/sync", headers=headers).status_code == 403
        assert client.post("/api/companions/make", json={}, headers=headers).status_code == 403
        assert client.post("/api/forge/start", headers=headers).status_code == 403
    assert client.post(f"/api/companions/{mara['id']}/open", headers={"Origin": "http://localhost"}).status_code == 200


def test_the_person_can_read_and_correct_her_notes(client, world):
    mara = active_avatar(world)
    data = client.get(f"/api/companions/{mara['id']}/notes").get_json()
    assert data["notes"] == {"about_you": "", "our_story": ""} and data["status"]["busy"] is False
    saved = client.put(f"/api/companions/{mara['id']}/notes", json={"about_you": "- Name is Sam"}).get_json()
    assert saved["notes"]["about_you"] == "- Name is Sam"
    assert client.put(f"/api/companions/{mara['id']}/notes", json={"our_story": "x" * 30000}).status_code == 400
    assert client.put(f"/api/companions/{mara['id']}/notes", json={"our_story": 5}).status_code == 400
    assert client.get("/api/companions/nobody-here-1/notes").status_code == 404
    assert client.put("/api/companions/nobody-here-1/notes", json={}).status_code == 404


def test_update_now_runs_in_the_background_and_says_when_she_is_busy(client, world):
    mara = active_avatar(world)
    assert client.post(f"/api/companions/{mara['id']}/notes/update").status_code == 400                  # nothing said yet
    sid = companions.open_companion(mara["id"])["session_id"]
    world.store.save_message(sid, "user", "hello there")
    svc = notes.NotesService(notes.NotesStore(world.avatars), lambda: world.store, {}, llm=lambda m: "<about_you>- A</about_you><our_story>- B</our_story>")
    notes.set_notes_service(svc)
    svc._busy.add(mara["id"])
    assert client.post(f"/api/companions/{mara['id']}/notes/update").status_code == 409
    svc._busy.clear()
    assert client.post(f"/api/companions/{mara['id']}/notes/update").status_code == 202
    import time
    for _ in range(100):
        if svc.store.read(mara["id"])["about_you"]:
            break
        time.sleep(0.05)
    assert svc.store.read(mara["id"])["about_you"] == "- A"
    status = client.get(f"/api/companions/{mara['id']}/status").get_json()
    assert status["notes"]["words"]["about_you"] == 2 and "running" in status["pictures"]


# ---------------------------------------------------------------- meeting someone new through NPC Forge

def fake_opener(routes, log=None):
    """A stand-in for urlopen: routes maps 'METHOD /path' to a dict, a list, or an exception to raise."""
    class Response:
        def __init__(self, body):
            self.body = body
            self.headers = {"Content-Length": str(len(body))}

        def read(self, size=-1):
            return self.body[:size] if size >= 0 else self.body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def wrapped(request, timeout=None):
        from urllib.parse import urlsplit
        key = f"{request.get_method()} {urlsplit(request.full_url).path}"
        if log is not None:
            log.append((key, request.data))
        if key == "GET /api/health":
            answer = routes.get(key, {"online": True})
            if isinstance(answer, dict):
                answer = {"gpu_rule": {}, **answer}
        else:
            answer = routes[key]
        if isinstance(answer, Exception):
            raise answer
        return Response(json.dumps(answer).encode())
    return wrapped


CONFIG = {"api_version": 48, "settings": {"modern": {
    "origins": [{"id": "japanese", "label": "Japanese"}], "hair_colors": ["blonde", "black"],
    "age_groups": [{"id": "child", "label": "Child (6-12)"}, {"id": "teen", "label": "Teen (13-17)"},
                   {"id": "young_adult", "label": "Ages 18-29"}]}}, "styles": {"photoreal": {"label": "Photoreal"}}}
MEET_DRAFT = {"setting": "modern", "adult": True, "style": "photoreal", "freeform": False,
              "profile": {"age": 34, "first_name": "Zoe", "personality": ["warm"], "speech": "Soft-spoken"}}


def test_forge_status_says_what_is_missing_in_plain_words():
    import urllib.error
    down = forge.ForgeClient({}, opener=fake_opener({"GET /api/health": urllib.error.URLError(ConnectionRefusedError())}))
    assert down.status()["online"] is False and "not running" in down.status()["message"]
    no_modern = forge.ForgeClient({}, opener=fake_opener({"GET /api/config": {**CONFIG, "settings": {"models": {"label": "Models"}}}}))
    assert no_modern.status()["meet_supported"] is False and "Modern life" in no_modern.status()["meet_message"]
    with pytest.raises(forge.ForgeError, match="Modern life"):
        no_modern.make_someone()
    old = forge.ForgeClient({}, opener=fake_opener({"GET /api/config": {**CONFIG, "api_version": 43}}))
    assert old.status()["outdated"] is True and "Update NPC Forge" in old.status()["meet_message"]
    with pytest.raises(forge.ForgeError, match="Update NPC Forge"):
        old.make_someone()
    nocomfy = forge.ForgeClient({}, opener=fake_opener({"GET /api/config": CONFIG, "GET /api/health": {"online": False}}))
    assert "ComfyUI" in nocomfy.status()["message"]
    ok = forge.ForgeClient({}, opener=fake_opener({"GET /api/config": CONFIG, "GET /api/health": {"online": True}}))
    status = ok.status()
    assert status["online"] and status["meet_supported"] and not status["outdated"] and status["message"] == ""
    assert status["options"]["origins"] == [{"id": "japanese", "label": "Japanese"}] and status["options"]["hair_colors"] == ["blonde", "black"]
    assert status["options"]["age_groups"] == [{"id": "young_adult", "label": "Ages 18-29"}]


def test_only_this_computer_is_ever_asked():
    client = forge.ForgeClient({"companion": {"forge": {"url": "http://example.com:8765"}}})
    with pytest.raises(forge.ForgeError, match="this computer"):
        client._call("GET", "/api/config")
    assert forge.start({"companion": {"forge": {"url": "http://example.com:8765"}}})["started"] is False


@pytest.mark.parametrize("gender", ["female", "male", "nonbinary", "", "any", "unknown"])
@pytest.mark.parametrize("api_version", [44, 48])
def test_meet_rolls_an_adult_in_modern_life_and_never_asks_for_models(gender, api_version):
    log = []
    routes = {"GET /api/config": {**CONFIG, "api_version": api_version}, "GET /api/gpu": {"ready": True}, "POST /api/roll": MEET_DRAFT,
              "POST /api/generate": {"npc_ids": ["zoe-park-9a8b7c"], "jobs": [{"id": "j1"}]}}
    client = forge.ForgeClient({}, opener=fake_opener(routes, log))
    made = client.make_someone({"gender": gender, "origin": "japanese", "hair_color": "Any", "age_group": "teen",
                                "occupation": "nurse", "adult": False, "setting": "models", "freeform": "someone famous"})
    assert made == {"npc_id": "zoe-park-9a8b7c", "job_id": "j1"}
    constraints = {"origin": "japanese"}
    if gender in ("female", "male", "nonbinary"):
        constraints["gender"] = gender
    assert [(key, json.loads(body)) for key, body in log if key.startswith("POST")] == [
        ("POST /api/roll", {"setting": "modern", "style": "photoreal", "adult": True, "constraints": constraints}),
        ("POST /api/generate", {"npc": MEET_DRAFT, "style": "photoreal",
                                "assets": {"portrait": True, "sprite": False, "expressions": []},
                                "options": {"quality": "standard"}}),
    ]


@pytest.mark.parametrize(("changes", "message"), [
    ({"adult": False}, "adult character"),
    ({"profile": {**MEET_DRAFT["profile"], "age": 17}}, "18 or older"),
    ({"appearance": {"extra": "celebrity lookalike"}}, "real person's likeness"),
])
def test_meet_does_not_queue_an_unsafe_rolled_draft(changes, message):
    routes = {"GET /api/config": CONFIG, "GET /api/gpu": {"ready": True}, "POST /api/roll": {**MEET_DRAFT, **changes}}
    client = forge.ForgeClient({}, opener=fake_opener(routes))
    with pytest.raises(forge.ForgeError, match=message):
        client.make_someone()


def test_a_job_is_reported_as_queued_running_done_or_failed():
    jobs = [{"id": "a", "status": "running", "steps": [{"label": "Portrait", "status": "running"}]},
            {"id": "b", "status": "error", "error": None, "steps": [{"label": "Portrait", "status": "error", "error": "ComfyUI is down"}]},
            {"id": "c", "status": "done", "steps": []}]
    client = forge.ForgeClient({}, opener=fake_opener({"GET /api/config": CONFIG, "GET /api/jobs": jobs}))
    assert client.job("a") == {"state": "running", "note": "Portrait", "code": "", "holders": [], "blocked": ""}
    assert client.job("b") == {"state": "error", "note": "ComfyUI is down", "code": "", "holders": [], "blocked": ""}
    assert client.job("c")["state"] == "done" and client.job("zzz")["state"] == "error"


BUSY = ("Another program is using the GPU: llama-server.exe (24.6 GB). Comfyui (mage_flow_edit_int8_convrot) was not started "
        "(one model on the GPU at a time). Close it or unload its model (LM Studio, a voice app, another NPC Forge...) and try again.")


def test_a_job_says_the_first_step_that_failed_and_whether_the_gpu_was_busy():
    """Live, a living portrait whose every expression was refused for the GPU ended on "None of the living-portrait
    expressions exist": the first failure is the cause, and it is said while the job still runs."""
    living = [{"label": "Expression: happy", "status": "error", "error": BUSY},
              {"label": "Expression: sad", "status": "running"},
              {"label": "Expression: blink", "status": "queued"}]
    ended = [{"label": "Expression: happy", "status": "error", "error": BUSY},
             {"label": "Expression: sad", "status": "error", "error": "ComfyUI still holds 6.4 GB of GPU memory (one model on the GPU at a time)."},
             {"label": "Living portrait", "status": "error", "error": "None of the living-portrait expressions exist for the current portrait yet"}]
    other = [{"label": "Expression: happy", "status": "error", "error": "The edit came back blank."},
             {"label": "Expression: sad", "status": "running"}]
    jobs = [{"id": "a", "status": "running", "steps": living}, {"id": "b", "status": "error", "error": BUSY, "steps": ended},
            {"id": "c", "status": "running", "steps": other},
            {"id": "d", "status": "done", "steps": [{"label": "Expression: happy", "status": "error", "error": "Blank."}]}]
    client = forge.ForgeClient({}, opener=fake_opener({"GET /api/config": {**CONFIG, "api_version": 43}, "GET /api/jobs": jobs}))
    assert client.job("a") == {"state": "running", "blocked": BUSY, "code": "", "holders": [],
                               "note": f"Expression: sad. Expression: happy could not be made: {BUSY}"}
    assert client.job("b") == {"state": "error", "note": BUSY, "blocked": BUSY, "code": "", "holders": []}
    assert client.job("c") == {"state": "running", "blocked": "", "code": "", "holders": [],
                               "note": "Expression: sad. Expression: happy could not be made: The edit came back blank."}
    assert client.job("d") == {"state": "done", "note": "", "blocked": "", "code": "", "holders": []}


def test_from_api_44_a_job_gives_its_code_and_who_holds_the_gpu_and_stops_by_itself():
    """NPC Forge 44 stops a job the GPU is refused for, so Altera reads `code` and no longer cancels it."""
    holders = [{"name": "llama-server.exe", "mb": 25190}]
    stopped = [{"label": "Expression: happy", "status": "error", "error": BUSY, "code": "gpu_busy", "holders": holders},
               {"label": "Expression: sad", "status": "cancelled", "reason": "not run: another program holds the GPU"},
               {"label": "Assemble the living portrait", "status": "cancelled", "reason": "no expression was made: ..."}]
    jobs = [{"id": "a", "status": "error", "code": "gpu_busy", "error": BUSY, "steps": stopped},
            {"id": "b", "status": "running", "code": None, "steps": [{"label": "Expression: happy", "status": "running"}]},
            {"id": "c", "status": "error", "code": "comfy_down", "error": "Cannot reach ComfyUI.",
             "steps": [{"label": "Portrait", "status": "error", "error": "Cannot reach ComfyUI.", "code": "comfy_down"}]}]
    client = forge.ForgeClient({}, opener=fake_opener({"GET /api/config": {**CONFIG, "api_version": 44}, "GET /api/jobs": jobs}))
    assert client.job("a") == {"state": "error", "note": BUSY, "code": "gpu_busy", "holders": holders, "blocked": ""}
    assert client.job("b") == {"state": "running", "note": "Expression: happy", "code": "", "holders": [], "blocked": ""}
    assert client.job("c")["code"] == "comfy_down" and client.job("c")["blocked"] == ""
    old = forge.ForgeClient({}, opener=fake_opener({"GET /api/config": {**CONFIG, "api_version": 43}, "GET /api/jobs": jobs}))
    assert old.job("a") == {"state": "error", "note": BUSY, "code": "", "holders": [], "blocked": BUSY}


def test_companion_retry_only_resumes_the_matching_gpu_refused_job(client, monkeypatch):
    from core.routes import companions as routes
    npc_id = "zoe-park-9a8b7c"
    job_id = "abc42"
    log = []
    table = {"GET /api/config": {**CONFIG, "api_version": 44},
             "GET /api/jobs": [{"id": job_id, "npc_id": npc_id, "status": "error", "code": "gpu_busy"}],
             "GET /api/gpu": {"ready": True, "code": None},
             f"POST /api/jobs/{job_id}/retry": {}}
    monkeypatch.setattr(routes, "_forge", lambda: forge.ForgeClient({}, opener=fake_opener(table, log)))
    assert client.post(f"/api/companions/make/other-npc/{job_id}/retry").status_code == 409
    assert all(path != "GET /api/gpu" for path, _ in log)
    assert client.post(f"/api/companions/make/{npc_id}/{job_id}/retry").status_code == 202
    assert [path for path, _ in log][-2:] == ["GET /api/gpu", f"POST /api/jobs/{job_id}/retry"]


class FakeForge:
    def __init__(self, **kw):
        self.state = kw.get("state", "done")
        self.calls = []

    def status(self):
        return {"online": True, "meet_supported": True, "message": "", "options": {}, "api_version": 48, "comfy_online": True}

    def make_someone(self, constraints=None):
        self.calls.append(constraints)
        return {"npc_id": "zoe-park-9a8b7c", "job_id": "j1"}

    def job(self, job_id):
        return {"state": self.state, "note": "Portrait"}

    def cancel(self, job_id):
        self.calls.append(("cancel", job_id))


def test_meeting_someone_new_makes_her_waits_for_the_portrait_then_imports_and_opens_her(client, world, monkeypatch):
    import core.routes.companions as routes
    fake = FakeForge(state="running")
    monkeypatch.setattr(routes, "_forge", lambda: fake)
    make_forge(world.forge, npc_id="zoe-park-9a8b7c", first="Zoe")
    choices = {"gender": "nonbinary", "origin": "japanese"}
    started = client.post("/api/companions/make", json=choices)
    assert started.status_code == 202 and started.get_json() == {"npc_id": "zoe-park-9a8b7c", "job_id": "j1"}
    assert fake.calls == [choices]
    url = "/api/companions/make/zoe-park-9a8b7c/j1"
    assert client.get(url).get_json() == {"state": "running", "note": "Portrait"}
    assert world.avatars.get("zoe-park-9a8b7c") is None
    fake.state = "done"
    ready = client.get(url).get_json()
    assert ready["state"] == "ready" and ready["companion"]["id"] == "zoe-park-9a8b7c" and ready["companion"]["session_id"]
    assert world.avatars.active_id() == "zoe-park-9a8b7c"
    assert client.get(url).get_json()["state"] == "ready"                                    # asking again just opens her
    assert client.delete(url).status_code == 200 and ("cancel", "j1") in fake.calls


def test_a_new_portrait_that_fails_the_age_check_is_not_imported(client, world, monkeypatch):
    import core.routes.companions as routes
    monkeypatch.setattr(routes, "_forge", lambda: FakeForge())
    make_forge(world.forge, npc_id="zoe-park-9a8b7c", first="Zoe")
    world.checker.result.update(ok=False, apparent_age=15)
    answer = client.get("/api/companions/make/zoe-park-9a8b7c/j1").get_json()
    assert answer["state"] == "rejected" and "younger" in answer["note"] and world.avatars.get("zoe-park-9a8b7c") is None
    assert client.get("/api/companions/make/../j1").status_code == 400 and client.get("/api/companions/make/BAD_ID/j1").status_code == 400


def test_the_page_is_told_when_forge_is_not_there(client, monkeypatch):
    import core.routes.companions as routes

    class Down(FakeForge):
        def make_someone(self, constraints=None):
            raise forge.ForgeError("NPC Forge is not running. Start it, then try again.")

        def status(self):
            return {"online": False, "message": "NPC Forge is not running. Start it, then try again."}
    monkeypatch.setattr(routes, "_forge", lambda: Down())
    assert client.post("/api/companions/make", json={}).status_code == 503
    assert "not running" in client.post("/api/companions/make", json={}).get_json()["error"]
    assert client.get("/api/forge/status").get_json()["online"] is False


def test_forge_is_only_started_when_nothing_is_listening_on_its_port(monkeypatch):
    import subprocess
    launched = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: launched.append((a, k)))
    monkeypatch.setattr(forge.ForgeClient, "_ask_health", lambda *args: {"online": True, "gpu_rule": {}})
    assert forge.start({})["started"] is False and "already running" in forge.start({})["message"] and launched == []
    def down(*args):
        raise forge.ForgeNotListening("NPC Forge is not running.")
    monkeypatch.setattr(forge.ForgeClient, "_ask_health", down)
    monkeypatch.setattr(forge, "_port_open", lambda url: True)
    assert forge.start({})["started"] is False and "port is already in use" in forge.start({})["message"] and launched == []
    monkeypatch.setattr(forge, "_port_open", lambda url: False)
    assert forge.start({})["started"] is False and launched == []                            # no library configured: no folder to start
    folder = "C:\\somewhere\\npc-generator"
    monkeypatch.setattr(forge, "forge_folder", lambda config=None: __import__("pathlib").Path(folder))
    monkeypatch.setattr(forge, "_forge_python", lambda config=None: None)
    assert forge.start({})["started"] is False and "Pillow" in forge.start({})["message"] and launched == []      # no Python that can run it
    monkeypatch.setattr(forge, "_forge_python", lambda config=None: ["py", "-3"])
    assert forge.start({})["started"] is True
    args, kwargs = launched[0]
    assert args[0] == ["py", "-3", "server.py", "--port", "8766"] and kwargs["cwd"] == folder


def test_forge_runs_with_the_first_python_that_has_pillow_and_numpy(monkeypatch):
    import shutil
    import subprocess
    tried = []

    def fake_run(command, capture_output=True, timeout=None):
        tried.append(command[0])
        return types.SimpleNamespace(returncode=0 if command[0] == "C:\\good\\python.exe" else 1)
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(shutil, "which", lambda name: name if name in ("py", "python") else None)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    config = {"companion": {"forge": {"python": "C:\\good\\python.exe"}}}
    assert forge._forge_python(config) == ["C:\\good\\python.exe"] and tried == ["C:\\good\\python.exe"]     # the named one, tried first
    tried.clear()
    assert forge._forge_python({}) is None and tried == ["py", "python"]                                        # none of the others can import PIL


# ---------------------------------------------------------------- her first words

@pytest.fixture
def stream(world, tmp_path, monkeypatch):
    import core.routes.core_api as core_api
    (tmp_path / "config.yaml").write_text("voice: {tts_enabled: false}\n", "utf-8")
    monkeypatch.chdir(tmp_path)

    class Engine:
        calls = []

        def send_message(self, text, session_id, stream=False, greet=False, **kw):
            Engine.calls.append((text, greet))

            def gen():
                yield "Hello?"
                yield {"notes": "pass"}
                yield None
            return gen()
    monkeypatch.setattr(core_api, "_get_chat_engine", lambda: Engine())
    seen = []
    monkeypatch.setattr(selfview, "ensure", lambda avatars_, avatar_id, config, client_factory=None: seen.append(avatar_id) or "")
    return types.SimpleNamespace(client=create_app("core_api").test_client(), calls=Engine.calls, looked=seen)


def events(response):
    return [json.loads(line[6:]) for line in response.get_data(True).split("\n\n") if line.startswith("data: ")]


def test_a_new_companion_speaks_first_after_looking_at_her_own_face(stream, world):
    mara = active_avatar(world)
    sid = companions.open_companion(mara["id"])["session_id"]
    got = events(stream.client.post("/api/chat/stream", json={"session_id": sid, "greet": True}))
    statuses = [e.get("status") for e in got]
    assert statuses[:2] == ["looking", "typing"] and {"status": "notes", "state": "pass"} in got
    assert {"response": "Hello?"} in got and statuses[-2:] == ["final", "done"]
    assert stream.calls == [("", True)] and stream.looked == [mara["id"]]


def test_she_only_says_hello_once_and_a_greeting_needs_no_message(stream, world):
    mara = active_avatar(world)
    sid = companions.open_companion(mara["id"])["session_id"]
    world.store.save_message(sid, "assistant", "Hello?")
    assert stream.client.post("/api/chat/stream", json={"session_id": sid, "greet": True}).status_code == 409
    assert stream.client.post("/api/chat/stream", json={"session_id": sid, "message": "  "}).status_code == 400
    assert stream.client.post("/api/chat/stream", json={"session_id": sid, "greet": "yes"}).status_code == 400   # only a real true counts


# ---------------------------------------------------------------- how she sees herself

class VisionClient:
    def __init__(self, text):
        self.text, self.sent = text, []
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.sent.append(kw)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=self.text, reasoning_content=None))])


def test_her_view_of_herself_is_cleaned_and_kept_once(world):
    mara = active_avatar(world)
    client = VisionClient('<think>hm</think>  "I see warm hazel eyes\nand a kind smile."  ')
    text = selfview.ensure(world.avatars, mara["id"], {}, client_factory=lambda base, key: client)
    assert text == "I see warm hazel eyes and a kind smile."
    sent = client.sent[0]["messages"][0]["content"]
    assert "YOU" in sent[0]["text"] and "Mara" in sent[0]["text"] and sent[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert world.avatars.state(mara["id"])["self_view"] == text
    assert selfview.ensure(world.avatars, mara["id"], {}, client_factory=lambda base, key: 1 / 0) == text        # never asks again


def test_a_view_that_breaks_a_rule_or_never_arrives_is_left_out(world):
    mara = active_avatar(world)
    assert selfview.describe(PNG, "Mara", {}, client_factory=lambda base, key: VisionClient("I look about 15 years old, a schoolgirl.")) == ""
    assert selfview.describe(PNG, "Mara", {}, client_factory=lambda base, key: 1 / 0) == ""
    assert selfview.ensure(world.avatars, mara["id"], {}, client_factory=lambda base, key: VisionClient("")) == ""
    assert selfview.clean("x " * 800).count("x") < 400


# ---------------------------------------------------------------- proactive check-ins belong to one chat

def quiet_for(world, session_id, hours):
    """Make the newest message of a chat this many hours old."""
    conn = world.store._connect()
    try:
        conn.execute("UPDATE messages SET timestamp = datetime('now', ?) WHERE session_id = ?", (f"-{hours} hours", session_id))
        conn.commit()
    finally:
        conn.close()


def own_profile_file(monkeypatch, tmp_path):
    """The check-in settings are saved in user_profile.yaml: use a temporary one, never the real file."""
    from core import profile
    monkeypatch.setattr(profile, "PROFILE_PATH", str(tmp_path / "user_profile.yaml"))
    return profile


def test_a_check_in_is_about_her_own_chat_and_waits_until_she_has_met_the_person(world, monkeypatch, tmp_path, altera_config):
    from core import proactive
    altera_config.write_text('phase4:\n  proactive_enabled: true\n  proactive_interval_hours: 6\n', encoding='utf-8')
    own_profile_file(monkeypatch, tmp_path)
    mara = active_avatar(world)
    sid = companions.open_companion(mara["id"])["session_id"]
    manager = proactive.ProactiveManager()
    manager.datastore = world.store
    assert manager.check_and_generate(session_id=sid)["message"] is None
    world.store.save_message(sid, "user", "I had a long day")
    quiet_for(world, sid, 8)
    asked = []
    monkeypatch.setattr(proactive, "get_client", lambda: types.SimpleNamespace(chat=lambda messages: asked.append(messages) or "How are you now?"))
    manager.followup_tracker.get_pending_followups = lambda limit=5: [{"priority": "high", "item": "someone else's dentist", "context": ""}]
    result = manager.check_and_generate(session_id=sid)
    assert result["message"] == "How are you now?"
    prompt = asked[0][1]["content"]
    assert "I had a long day" in prompt and "dentist" not in prompt


def test_a_check_in_waits_until_the_chat_has_been_quiet_and_then_comes_once_per_interval(world, monkeypatch, tmp_path, altera_config):
    from core import proactive
    altera_config.write_text('phase4:\n  proactive_enabled: true\n  proactive_interval_hours: 6\n', encoding='utf-8')
    own_profile_file(monkeypatch, tmp_path)
    mara = active_avatar(world)
    sid = companions.open_companion(mara["id"])["session_id"]
    world.store.save_message(sid, "user", "hello")
    asked = []
    monkeypatch.setattr(proactive, "get_client", lambda: types.SimpleNamespace(chat=lambda messages: asked.append(messages) or "Thinking of you"))
    manager = proactive.ProactiveManager()
    manager.datastore = world.store
    assert manager.check_and_generate(session_id=sid)["message"] is None and asked == []            # they just talked
    quiet_for(world, sid, 5)
    assert manager.check_and_generate(session_id=sid)["message"] is None and asked == []            # 5 h is under the 6 h default
    quiet_for(world, sid, 7)
    assert manager.check_and_generate(session_id=sid)["message"] == "Thinking of you" and len(asked) == 1
    assert manager.check_and_generate(session_id=sid)["message"] is None and len(asked) == 1        # she just did: not again for 6 h


def test_the_check_in_switch_in_settings_is_the_one_that_is_read(world, monkeypatch, tmp_path, altera_config):
    from core import proactive
    altera_config.write_text('phase4:\n  proactive_enabled: true\n  proactive_interval_hours: 6\n', encoding='utf-8')
    profile = own_profile_file(monkeypatch, tmp_path)
    assert profile.proactive_settings() == {"enabled": True, "interval_hours": 6}                  # the test's explicit defaults
    client = create_app("engagement").test_client()
    assert client.post("/api/proactive/settings", json={"enabled": False, "interval_hours": 3}).get_json() == {"status": "ok"}
    assert client.get("/api/proactive/settings").get_json() == {"enabled": False, "interval_hours": 3}     # it reads back
    assert profile.proactive_settings() == {"enabled": False, "interval_hours": 3}
    profile.save_user_profile({**profile.load_user_profile(), "name": "Sam"})                     # another save keeps it
    assert profile.load_user_profile()["name"] == "Sam" and profile.proactive_settings()["enabled"] is False

    mara = active_avatar(world)
    sid = companions.open_companion(mara["id"])["session_id"]
    world.store.save_message(sid, "user", "hello")
    quiet_for(world, sid, 30)
    asked = []
    monkeypatch.setattr(proactive, "get_client", lambda: types.SimpleNamespace(chat=lambda messages: asked.append(messages) or "hi"))
    manager = proactive.ProactiveManager()
    manager.datastore = world.store
    result = manager.check_and_generate(session_id=sid)
    assert result["message"] is None and "disabled" in result["reason"] and asked == []           # switched off: the model is not asked
    assert client.post("/api/proactive/settings", json={"enabled": True}).status_code == 200
    assert manager.check_and_generate(session_id=sid)["message"] == "hi" and len(asked) == 1


def test_background_helpers_ask_for_the_model_that_is_answering_chat(monkeypatch):
    """LM Studio loads whatever model a request names: a helper asking for its own default swapped the chat model out."""
    from core import llm
    from core.age_check import AgeCheck
    chat_model = llm.LLMClient(base_url="http://chat.example/v1", api_key="k", model="the-chat-model")
    monkeypatch.setattr(llm.LLMClient, "_instance", chat_model)
    helper = llm.get_client()
    assert (helper.base_url, helper.api_key, helper.model) == ("http://chat.example/v1", "k", "the-chat-model") and helper is not chat_model
    assert AgeCheck({})._target()[2] == helper.model                                              # the picture check names it too
    monkeypatch.setattr(llm.LLMClient, "_instance", None)                                         # not made yet: the chosen provider
    provider = types.SimpleNamespace(base_url="http://reg.example/v1", api_key="r", default_model="registry-default")
    monkeypatch.setattr(llm, "_provider_registry", types.SimpleNamespace(
        get_active_provider=lambda: provider, get_active_model=lambda: "registry-choice"))
    assert llm.get_client().model == "registry-choice" and llm.get_client().base_url == "http://reg.example/v1"
    monkeypatch.setattr(llm, "_provider_registry", None)                                          # no chat yet: the saved choice
    monkeypatch.setattr('core.config.get_config', lambda: {'llm': {'model': 'saved-before-chat'}})
    assert llm.get_client().model == 'saved-before-chat'


def test_a_file_briefly_locked_by_another_request_is_retried_not_taken_as_missing(tmp_path, monkeypatch):
    """Windows: two page loads at once made reading active.json fail for an instant, and "nobody is active" switched the
    person to another companion. Reads and writes now wait a moment and try again."""
    from core import avatars as avatars_module
    path = tmp_path / "active.json"
    avatars_module._write_json(path, {"id": "mara-bell-47ab97"})
    real_read, real_replace, fails = type(path).read_text, avatars_module.os.replace, {"read": 2, "replace": 2}

    def flaky_read(self, *a, **k):
        if fails["read"]:
            fails["read"] -= 1
            raise PermissionError("in use")
        return real_read(self, *a, **k)

    def flaky_replace(src, dst):
        if fails["replace"]:
            fails["replace"] -= 1
            raise PermissionError("in use")
        return real_replace(src, dst)
    monkeypatch.setattr(type(path), "read_text", flaky_read)
    monkeypatch.setattr(avatars_module.os, "replace", flaky_replace)
    assert avatars_module._read_json(path) == {"id": "mara-bell-47ab97"}
    avatars_module._write_json(path, {"id": "other-000000"})
    assert fails == {"read": 0, "replace": 0} and avatars_module._read_json(path) == {"id": "other-000000"}
