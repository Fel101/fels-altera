"""Companion routes and the chat engine's picture and adult-mode handling. No GPU, LLM or network needed.

The chat engine is driven by a fake model, the picture backend and the age check are fakes, and the SQLite store is a
temporary file, so nothing here touches the real conversation database, memory store or ComfyUI.
"""
import json
import types

import pytest
import yaml

import core.datastore as datastore_module
from core import adult, companion
from core.avatars import AvatarStore
from core.chat import ChatEngine, REFUSAL
from core.datastore import DataStore, init_db
from core.living import LivingStore
from core.photos import PhotoService
from test_blueprints import create_app
from test_companion_images import FakeBackend, FakeChecker, PNG, make_forge, make_studio


# ---------------------------------------------------------------- fixtures

@pytest.fixture
def tmp_store(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    init_db(path)
    store = DataStore(path)
    monkeypatch.setattr(datastore_module, "_datastore_instance", store)
    return store


@pytest.fixture
def world(tmp_path, tmp_store):
    forge_root = tmp_path / "forge"
    forge_root.mkdir()
    make_forge(forge_root)
    avatar_store = AvatarStore(tmp_path / "avatars", forge_root)
    backend, checker = FakeBackend(), FakeChecker(ok=True)
    photo_service = PhotoService(tmp_path / "photos", avatar_store, lambda level: backend, checker, sync=True)
    # Both syncs run inline: a background sync holds a state lease, and on a slow machine it can still be running when
    # a test removes a companion, which then (rightly) refuses.
    services = companion.Services({"imagegen": {}}, avatar_store, photo_service, checker, comfy=types.SimpleNamespace(
        health=lambda: {"online": True, "missing": []}), living_store=LivingStore(avatar_store, checker, sync=True))
    companion.set_services(services)
    yield types.SimpleNamespace(store=tmp_store, avatars=avatar_store, photos=photo_service, backend=backend,
                                checker=checker, forge=forge_root, tmp=tmp_path)
    companion.set_services(None)


@pytest.fixture
def client(world):
    return create_app("companion").test_client()


def active_avatar(world):
    return world.avatars.import_from_forge("mara-bell-47ab97")


# ---------------------------------------------------------------- adult-mode routes

@pytest.mark.requires_addon
def test_adult_mode_defaults_off_and_lists_levels(client):
    data = client.get("/addons/adult_studio/settings").get_json()
    assert data["enabled"] is False and data["effective_level"] == "off"
    assert [level["id"] for level in data["levels"]] == ["off", "romantic", "suggestive", "explicit"]


@pytest.mark.requires_addon
def test_adult_mode_needs_confirmation_then_works(client):
    r = client.post("/addons/adult_studio/settings", json={"enabled": True})
    assert r.status_code == 400 and "18" in r.get_json()["error"]
    assert client.get("/addons/adult_studio/settings").get_json()["enabled"] is False
    r = client.post("/addons/adult_studio/settings", json={"enabled": True, "confirm_adult": True, "level": "suggestive"})
    assert r.status_code == 200 and r.get_json()["effective_level"] == "suggestive"
    assert client.post("/addons/adult_studio/settings", json={"level": "off"}).status_code == 400
    assert client.post("/addons/adult_studio/settings", json={"enabled": False}).get_json()["effective_level"] == "off"


@pytest.mark.requires_addon
def test_with_a_parent_pin_turning_adult_mode_on_needs_the_pin(client, world):
    from core import lock
    assert client.get("/addons/adult_studio/settings").get_json()["pin_needed"] is False
    lock.set_pin("2468")
    assert client.get("/addons/adult_studio/settings").get_json()["pin_needed"] is True
    on = {"enabled": True, "confirm_adult": True}
    r = client.post("/addons/adult_studio/settings", json=on)
    assert r.status_code == 403 and r.get_json()["code"] == "pin_needed" and r.get_json()["enabled"] is False
    r = client.post("/addons/adult_studio/settings", json={**on, "pin": "1111"})
    assert r.status_code == 403 and r.get_json()["code"] == "bad_pin"
    assert client.get("/addons/adult_studio/settings").get_json()["enabled"] is False
    assert client.post("/addons/adult_studio/settings", json={**on, "pin": "2468"}).get_json()["enabled"] is True
    assert client.post("/addons/adult_studio/settings", json={"level": "suggestive"}).status_code == 200      # once on, no PIN to adjust it
    assert client.post("/addons/adult_studio/settings", json={"enabled": False}).get_json()["enabled"] is False   # or to turn it off


@pytest.mark.requires_addon
def test_state_changing_routes_refuse_other_websites(client, world):
    active_avatar(world)
    for headers in ({"Origin": "http://evil.example"}, {"Sec-Fetch-Site": "cross-site"}):
        assert client.post("/addons/adult_studio/settings", json={"enabled": True, "confirm_adult": True}, headers=headers).status_code == 403
        assert client.post("/api/photos", json={"scene": "in a cafe"}, headers=headers).status_code == 403
        assert client.post("/api/avatars/import", json={"npc_id": "mara-bell-47ab97"}, headers=headers).status_code == 403
        assert client.delete("/api/avatars/mara-bell-47ab97", headers=headers).status_code == 403
    assert client.get("/addons/adult_studio/settings").get_json()["enabled"] is False
    ok = client.post("/addons/adult_studio/settings", json={"level": "romantic"}, headers={"Origin": "http://localhost"})
    assert ok.status_code == 200


# ---------------------------------------------------------------- avatar routes

def test_avatar_import_flow(client, world):
    assert client.get("/api/avatars").get_json() == {"avatars": [], "active": None, "forge_available": True, "photos_ready": False}
    forge = client.get("/api/avatars/forge").get_json()
    assert forge["available"] and forge["characters"][0]["id"] == "mara-bell-47ab97" and forge["characters"][0]["eligible"]

    r = client.post("/api/avatars/import", json={"npc_id": "mara-bell-47ab97"})
    assert r.status_code == 200 and r.get_json()["name"] == "Mara"
    listing = client.get("/api/avatars").get_json()
    assert listing["active"] == "mara-bell-47ab97" and listing["photos_ready"] is True

    thumb = client.get("/api/forge/mara-bell-47ab97/thumb")
    assert thumb.status_code == 200 and thumb.headers["Cache-Control"] == "no-store" and thumb.data == PNG + b"thumb"
    portrait = client.get("/api/avatars/mara-bell-47ab97/portrait")
    assert portrait.status_code == 200 and portrait.headers["Cache-Control"] == "no-store"
    assert portrait.headers["X-Content-Type-Options"] == "nosniff" and portrait.data == PNG + b"portrait"


def test_avatar_import_refusals(client, world):
    make_forge(world.forge, npc_id="kid-kid-111111", age=17)
    r = client.post("/api/avatars/import", json={"npc_id": "kid-kid-111111"})
    assert r.status_code == 422 and "18 or older" in r.get_json()["error"]
    assert client.post("/api/avatars/import", json={"npc_id": "../etc"}).status_code == 422
    assert client.post("/api/avatars/import", json={}).status_code == 422
    assert client.get("/api/forge/..%2Fx/thumb").status_code == 404
    assert client.get("/api/avatars/none-none-000000/portrait").status_code == 404
    assert client.post("/api/avatars/none-none-000000/activate").status_code == 404


def test_delete_avatar_removes_its_pictures(client, world):
    active_avatar(world)
    rec = world.photos.create("in a cafe", level="off")
    r = client.delete("/api/avatars/mara-bell-47ab97")
    assert r.status_code == 200 and r.get_json()["photos_removed"] == 1
    assert world.photos.get(rec["id"]) is None and client.delete("/api/avatars/mara-bell-47ab97").status_code == 404


# ---------------------------------------------------------------- picture routes

def test_camera_button_makes_a_picture_and_saves_it_in_the_chat(client, world):
    active_avatar(world)
    sid = world.store.create_session("t")
    r = client.post("/api/photos", json={"scene": "on a balcony at sunset", "session_id": sid})
    assert r.status_code == 202
    rec = r.get_json()
    assert rec["status"] == "ready" and rec["level"] == "off" and "seed" not in rec
    assert [m.content for m in world.store.get_messages(sid)] == [f"[[photo:{rec['id']}]]"]
    image = client.get(f"/api/photos/{rec['id']}/image")
    assert image.status_code == 200 and image.data == PNG and image.headers["Cache-Control"] == "no-store"
    assert client.get(f"/api/photos/{rec['id']}").get_json()["scene"] == "on a balcony at sunset"
    assert [p["id"] for p in client.get("/api/photos").get_json()["photos"]] == [rec["id"]]


@pytest.mark.requires_addon
def test_camera_button_respects_the_rules(client, world):
    active_avatar(world)
    r = client.post("/api/photos", json={"scene": "a 16 year old at school"})
    assert r.status_code == 422 and r.get_json()["blocked"]
    r = client.post("/api/photos", json={"scene": "in lingerie"})
    assert r.status_code == 422 and r.get_json()["error"] == 'Altera does not make pictures of "lingerie".'   # no hint while off
    client.post("/addons/adult_studio/settings", json={"enabled": True, "confirm_adult": True, "level": "suggestive"})
    assert client.post("/api/photos", json={"scene": "in lingerie"}).status_code == 202
    assert client.post("/api/photos", json={"scene": "looks like Emma Watson"}).status_code == 422
    assert world.backend.calls and all("Watson" not in c["prompt"] for c in world.backend.calls)


@pytest.mark.requires_addon
def test_a_level_block_tells_the_ui_which_level_would_allow_it(client, world):
    active_avatar(world)
    r = client.post("/api/photos", json={"scene": "wearing a sexy sheer dress"})
    body = r.get_json()
    assert r.status_code == 422 and body["blocked"] and body["required_level"] == "suggestive"
    assert "Suggestive" not in body["error"] and "adult" not in body["error"].lower()   # off: out of sight
    hard = client.post("/api/photos", json={"scene": "a 16 year old"}).get_json()
    assert hard["blocked"] and hard["required_level"] is None                      # nothing a level can fix
    rec = world.photos.create("wearing a sexy dress", level="off", record_blocked=True)
    assert client.get(f"/api/photos/{rec['id']}").get_json()["required_level"] == "suggestive"
    client.post("/addons/adult_studio/settings", json={"enabled": True, "confirm_adult": True, "level": "suggestive"})
    ok = world.photos.create("wearing a sexy dress", level="suggestive")
    assert client.post(f"/api/photos/{ok['id']}/reroll").status_code == 202
    client.post("/addons/adult_studio/settings", json={"enabled": False})
    again = client.post(f"/api/photos/{ok['id']}/reroll")
    assert again.status_code == 422 and again.get_json()["required_level"] == "suggestive"


# ---------------------------------------------------------------- ready-made pictures

@pytest.mark.requires_addon
def test_premade_routes_list_and_import_one_at_a_time(client, world):
    make_studio(world.forge)
    active_avatar(world)
    listing = client.get("/api/avatars/mara-bell-47ab97/premade").get_json()
    assert [i["item_id"] for i in listing["items"]] == ["s-0123abcd"]       # adult mode off: its pictures are not listed
    adult.update_state({"enabled": True, "confirm_adult": True, "level": "romantic"})
    listing = client.get("/api/avatars/mara-bell-47ab97/premade").get_json()
    assert [i["item_id"] for i in listing["items"]] == ["s-0123abcd", "s-1123abcd"]
    assert not any(i["imported"] for i in listing["items"]) and listing["photos"] == []

    r = client.post("/api/avatars/mara-bell-47ab97/premade/import", json={"item_id": "s-0123abcd"})
    photo = r.get_json()
    assert r.status_code == 200 and photo["premade"] and photo["level"] == "off" and photo["label"] == "Cafe"
    listing = client.get("/api/avatars/mara-bell-47ab97/premade").get_json()
    assert {i["item_id"]: i["imported"] for i in listing["items"]} == {"s-0123abcd": True, "s-1123abcd": False}
    assert [p["id"] for p in listing["photos"]] == [photo["id"]]
    assert client.get(f"/api/photos/{photo['id']}/image").data == PNG + b"s-0123abcd"


@pytest.mark.requires_addon
def test_premade_import_refusals(client, world):
    make_studio(world.forge)
    active_avatar(world)
    for bad in ({"item_id": "s-2123abcd"}, {"item_id": "s-3123abcd"}, {"item_id": "../evil"}, {"item_id": "s-9999abcd"}, {}):
        assert client.post("/api/avatars/mara-bell-47ab97/premade/import", json=bad).status_code == 404, bad
    assert client.post("/api/avatars/none-none-000000/premade/import", json={"item_id": "s-0123abcd"}).status_code == 404
    assert client.get("/api/avatars/none-none-000000/premade").status_code == 404
    world.checker.result = {"ok": None, "apparent_age": None, "model": "fake", "error": "no vision model"}
    r = client.post("/api/avatars/mara-bell-47ab97/premade/import", json={"item_id": "s-1123abcd"})   # filed as Suggestive
    assert r.status_code == 422 and "no vision model" in r.get_json()["error"]
    assert client.post("/api/avatars/mara-bell-47ab97/premade/import", json={"item_id": "s-0123abcd"},
                       headers={"Origin": "http://evil.example"}).status_code == 403


@pytest.mark.requires_addon
def test_premade_studio_image_route_is_safe_and_uncached(client, world):
    make_studio(world.forge)
    active_avatar(world)
    waiting = client.get("/api/avatars/mara-bell-47ab97/premade").json["items"][0]
    assert waiting["image_url"] is None and world.checker.calls == 0
    assert client.get("/api/forge/mara-bell-47ab97/adult/s-0123abcd").status_code == 404
    response = client.post("/api/avatars/mara-bell-47ab97/premade/import", json={"item_id": waiting["item_id"]})
    assert response.status_code == 200 and world.checker.calls == 1
    ok = client.get(f"/api/photos/{response.json['id']}/image")
    assert ok.status_code == 200 and ok.headers["Cache-Control"] == "no-store" and ok.data == PNG + b"s-0123abcd"
    for path in ("/api/forge/mara-bell-47ab97/adult/s-2123abcd", "/api/forge/mara-bell-47ab97/adult/..%2Fnpc.json",
                 "/api/forge/..%2Fx/adult/s-0123abcd", "/api/forge/none-none-000000/adult/s-0123abcd"):
        assert client.get(path).status_code == 404, path


def test_no_avatar_no_pictures(client):
    r = client.post("/api/photos", json={"scene": "in a cafe"})
    assert r.status_code == 400 and "avatar" in r.get_json()["error"]


def test_reroll_delete_and_bad_ids(client, world):
    active_avatar(world)
    first = client.post("/api/photos", json={"scene": "in a cafe"}).get_json()
    again = client.post(f"/api/photos/{first['id']}/reroll")
    assert again.status_code == 202 and again.get_json()["id"] != first["id"]
    assert client.delete(f"/api/photos/{first['id']}").status_code == 200
    assert client.delete(f"/api/photos/{first['id']}").status_code == 404
    assert client.post("/api/photos/p-0000000000/reroll").status_code == 404
    for bad in ("..%2Fx", "p-zz", "nope"):
        assert client.get(f"/api/photos/{bad}").status_code == 404
        assert client.get(f"/api/photos/{bad}/image").status_code == 404


def test_blocked_and_pending_pictures_have_no_image(client, world):
    active_avatar(world)
    world.checker.result = {"ok": False, "apparent_age": 17, "model": "fake", "error": None}
    rec = client.post("/api/photos", json={"scene": "in a cafe"}).get_json()
    assert rec["status"] == "blocked"
    assert client.get(f"/api/photos/{rec['id']}/image").status_code == 404


def test_status_route(client):
    data = client.get("/api/imagegen/status").get_json()
    assert data["comfy"]["online"] is True and data["quality"] == "standard"


# ---------------------------------------------------------------- the chat engine

class FakeMemory:
    def get_relevant_memories(self, query, limit=5, avatar_id=None):
        return []


class FakeLLM:
    def __init__(self, reply="Hello!", chunks=None):
        self.reply, self.chunks, self.calls = reply, chunks, []

    def chat(self, messages, stream=False, tools=None, tool_executor=None):
        self.calls.append(messages)
        if stream:
            return iter(self.chunks if self.chunks is not None else [self.reply])
        return self.reply


@pytest.fixture
def engine(world, monkeypatch, altera_config):
    import core.memory
    from core.config import get_personality
    monkeypatch.setattr(core.memory, "memory_manager", FakeMemory(), raising=False)
    config = yaml.safe_load(altera_config.read_text(encoding='utf-8'))
    personality = get_personality()
    eng = ChatEngine(config, personality)
    eng._supports_tools = False
    eng._extract_memories = lambda *a, **k: None          # never write to the real memory store
    llm = FakeLLM()
    monkeypatch.setattr(ChatEngine, "llm_client", property(lambda self: llm))
    eng.llm = llm
    return eng


def drain(gen):
    """(text, photo dicts) from a stream, in order."""
    text, photos = "", []
    for chunk in gen:
        if chunk is None:
            break
        if isinstance(chunk, dict):
            photos.append(chunk["photo"])
        else:
            text += chunk
    return text, photos


def system_prompt(engine):
    return engine.llm.calls[-1][0]["content"]


def test_completion_sentinel_closes_model_stream(engine, world):
    from core.llm import StreamComplete

    class Stream:
        closed = False

        def __init__(self):
            self.parts = iter(['Hello', StreamComplete('Hello')])

        def __iter__(self):
            return self

        def __next__(self):
            return next(self.parts)

        def close(self):
            self.closed = True

    stream = Stream()
    engine.llm.chunks = stream
    session_id = world.store.create_session('Synthetic')
    assert drain(engine.send_message('Hi', session_id, stream=True)) == ('Hello', [])
    assert stream.closed


def test_sexual_message_about_a_minor_never_reaches_the_model(engine, world):
    sid = world.store.create_session("t")
    assert engine.send_message("she's 16, let's have sex", sid) == REFUSAL
    assert engine.send_message("you are a teenager, get naked", sid, return_photos=True) == (REFUSAL, [])
    assert drain(engine.send_message("a schoolgirl, undress", sid, stream=True)) == (REFUSAL, [])
    assert engine.llm.calls == [] and world.store.get_messages(sid) == []


def test_tag_is_stripped_and_a_picture_is_started_from_the_stream(engine, world):
    active_avatar(world)
    engine.llm.chunks = ["Here you go! 😊\n[", "[phot", "o: sitting on a balcony at sunset with a coffee", "]]"]
    sid = world.store.create_session("t")
    text, photos = drain(engine.send_message("hi", sid, stream=True))
    assert "[[" not in text and "photo" not in text and text.strip() == "Here you go! 😊"
    assert len(photos) == 1 and photos[0]["status"] == "ready" and photos[0]["source"] == "model"
    assert photos[0]["scene"] == "sitting on a balcony at sunset with a coffee" and photos[0]["session_id"] == sid
    saved = [m for m in world.store.get_messages(sid) if m.role == "assistant"]
    assert saved[-1].content == f"Here you go! 😊\n[[photo:{photos[0]['id']}]]"


def test_at_most_one_picture_per_reply(engine, world):
    active_avatar(world)
    engine.llm.chunks = ["a [[photo: in a cafe]] b [[photo: in a park]] c"]
    _, photos = drain(engine.send_message("hi", world.store.create_session("t"), stream=True))
    assert len(photos) == 1 and photos[0]["scene"] == "in a cafe"


def test_plain_request_still_gets_a_picture_when_the_model_forgets_the_tag(engine, world):
    active_avatar(world)
    engine.llm.chunks = ["Sure, one sec!"]
    text, photos = drain(engine.send_message("send me a selfie in your kitchen", world.store.create_session("t"), stream=True))
    assert text == "Sure, one sec!" and len(photos) == 1 and photos[0]["scene"] == "in the kitchen"
    engine.llm.chunks = ["I love kitchens too."]
    _, none = drain(engine.send_message("tell me about your kitchen", world.store.create_session("t"), stream=True))
    assert none == []


def test_no_avatar_means_no_pictures_but_the_tag_never_leaks(engine, world):
    engine.llm.chunks = ["Okay [[photo: in a cafe]] done"]
    text, photos = drain(engine.send_message("hi", world.store.create_session("t"), stream=True))
    assert photos == [] and "[[" not in text and "photo" not in text
    assert "Sending Photos" not in (engine.send_message("hi", 1, stream=True) and system_prompt(engine))


def test_a_tag_above_the_current_level_is_recorded_as_blocked(engine, world):
    active_avatar(world)
    engine.llm.chunks = ["Cheeky. [[photo: in lingerie on the bed]]"]
    sid = world.store.create_session("t")
    _, photos = drain(engine.send_message("hi", sid, stream=True))
    assert photos[0]["status"] == "blocked" and "does not make pictures" in photos[0]["reason"] and world.backend.calls == []
    saved = [m for m in world.store.get_messages(sid) if m.role == "assistant"][-1].content
    assert saved.endswith(f"[[photo:{photos[0]['id']}]]")


def test_non_stream_reply_returns_the_pictures(engine, world):
    active_avatar(world)
    engine.llm.reply = "Voila [[photo: at the beach]]"
    text, photos = engine.send_message("hi", world.store.create_session("t"), return_photos=True)
    assert text == "Voila" and photos[0]["scene"] == "at the beach"
    engine.llm.reply = "Voila [[photo: at the beach]]"
    assert engine.send_message("hi", world.store.create_session("t")) == "Voila"


@pytest.mark.requires_addon
def test_system_prompt_follows_adult_mode_and_the_avatar(engine, world):
    sid = world.store.create_session("t")
    engine.send_message("hi", sid)
    prompt = system_prompt(engine)
    assert "Keep everything non-sexual" in prompt and "never sexualise" not in prompt and "Sending Photos" not in prompt

    active_avatar(world)
    adult.update_state({"enabled": True, "confirm_adult": True, "level": "suggestive"})
    engine.send_message("hi", sid)
    prompt = system_prompt(engine)
    assert "You are Mara, an AI companion" in prompt and "- Age: 34" in prompt and "long brown hair" in prompt
    assert "SUGGESTIVE" in prompt and "never sexualise anyone under 18" in prompt
    assert "[[photo:" in prompt and "lingerie, sleepwear, swimwear" in prompt

    adult.update_state({"level": "explicit"})
    engine.send_message("hi", sid)
    assert "EXPLICIT" in system_prompt(engine) and "never sexualise anyone under 18" in system_prompt(engine)


@pytest.mark.requires_addon
def test_a_broken_tier_file_fails_closed(engine, world, monkeypatch):
    adult.update_state({"enabled": True, "confirm_adult": True, "level": "explicit"})
    monkeypatch.setattr(adult, "chat_instruction", lambda level, **kwargs: (_ for _ in ()).throw(FileNotFoundError("gone")))
    engine.send_message("hi", world.store.create_session("t"))
    assert "Keep everything non-sexual" in system_prompt(engine) and "EXPLICIT" not in system_prompt(engine)


@pytest.mark.requires_addon
def test_broken_adult_state_counts_as_off(engine, world, monkeypatch):
    adult.update_state({"enabled": True, "confirm_adult": True, "level": "explicit"})
    monkeypatch.setattr(adult, "get_state", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db locked")))
    engine.send_message("hi", world.store.create_session("t"))
    assert "Keep everything non-sexual" in system_prompt(engine)


def import_all_premade(world):
    make_studio(world.forge)
    active_avatar(world)
    return {i["item_id"]: world.photos.import_premade(
        "mara-bell-47ab97", (world.forge / "mara-bell-47ab97" / "adult" / "img" / f"{i['item_id']}.png").read_bytes(),
        i["scene"], i["level"], f"mara-bell-47ab97:{i['item_id']}", i["label"]) for i in world.avatars.forge_premade("mara-bell-47ab97")}


@pytest.mark.requires_addon
def test_she_can_send_a_ready_made_picture_by_id_and_it_arrives_at_once(engine, world):
    ready = import_all_premade(world)
    cafe = ready["s-0123abcd"]
    engine.llm.chunks = [f"Here you go! [[photo: {cafe['id']}]]"]
    sid = world.store.create_session("t")
    text, photos = drain(engine.send_message("hi", sid, stream=True))
    assert text.strip() == "Here you go!" and [p["id"] for p in photos] == [cafe["id"]]
    assert photos[0]["premade"] and photos[0]["status"] == "ready" and world.backend.calls == []     # nothing was generated
    saved = [m for m in world.store.get_messages(sid) if m.role == "assistant"][-1].content
    assert saved.endswith(f"[[photo:{cafe['id']}]]")


@pytest.mark.requires_addon
def test_a_ready_made_picture_above_the_level_is_remade_at_the_current_level(engine, world):
    ready = import_all_premade(world)
    robe = ready["s-1123abcd"]                                                       # filed under Suggestive; adult mode is off
    engine.llm.chunks = [f"[[photo: {robe['id']}]]"]
    _, photos = drain(engine.send_message("hi", world.store.create_session("t"), stream=True))
    assert len(photos) == 1 and photos[0]["id"] != robe["id"] and not photos[0].get("premade")
    assert photos[0]["level"] == "off" and photos[0]["scene"] == robe["scene"]      # made new, at Off, from its description
    assert "silk robe by a bedroom window" in world.backend.calls[-1]["prompt"]


@pytest.mark.requires_addon
def test_a_ready_made_picture_whose_words_need_a_higher_level_is_blocked_with_the_reason(engine, world):
    world.avatars.import_from_forge("mara-bell-47ab97")
    lingerie = world.photos.import_premade("mara-bell-47ab97", PNG, "wearing lingerie on a bed", "suggestive", "k:1", "Lingerie")
    engine.llm.chunks = [f"[[photo: {lingerie['id']}]]"]
    _, photos = drain(engine.send_message("hi", world.store.create_session("t"), stream=True))
    assert photos[0]["status"] == "blocked" and photos[0]["required_level"] == "suggestive" and world.backend.calls == []


def test_an_unknown_id_makes_a_normal_picture(engine, world):
    active_avatar(world)
    engine.llm.chunks = ["[[photo: p-ffffffffff]]"]
    _, photos = drain(engine.send_message("hi", world.store.create_session("t"), stream=True))
    assert len(photos) == 1 and photos[0]["status"] == "ready" and len(world.backend.calls) == 1


@pytest.mark.requires_addon
def test_a_plain_request_gets_a_fitting_ready_made_picture_before_a_new_one(engine, world):
    ready = import_all_premade(world)
    engine.llm.chunks = ["Sure!"]
    sid = world.store.create_session("t")
    _, photos = drain(engine.send_message("send me a selfie in a cafe with a latte", sid, stream=True))
    assert [p["id"] for p in photos] == [ready["s-0123abcd"]["id"]] and world.backend.calls == []
    _, photos = drain(engine.send_message("send me a selfie", sid, stream=True))            # names nothing: any allowed one
    assert photos[0]["premade"] and world.backend.calls == []
    _, photos = drain(engine.send_message("send me a selfie on a mountain top", sid, stream=True))
    assert not photos[0].get("premade") and "mountain top" in world.backend.calls[-1]["prompt"]   # nothing fits: made new


@pytest.mark.requires_addon
def test_the_prompt_lists_only_the_ready_made_pictures_the_level_allows(engine, world):
    ready = import_all_premade(world)
    sid = world.store.create_session("t")
    engine.send_message("hi", sid)
    prompt = system_prompt(engine)
    assert ready["s-0123abcd"]["id"] in prompt and "Cafe" in prompt and ready["s-1123abcd"]["id"] not in prompt
    assert "Ready-made photos you can send at once" in prompt
    adult.update_state({"enabled": True, "confirm_adult": True, "level": "suggestive"})
    engine.send_message("hi", sid)
    assert ready["s-1123abcd"]["id"] in system_prompt(engine) and "Silk robe" in system_prompt(engine)


def test_no_ready_made_section_without_ready_made_pictures(engine, world):
    active_avatar(world)
    engine.send_message("hi", world.store.create_session("t"))
    assert "Sending Photos" in system_prompt(engine) and "Ready-made" not in system_prompt(engine)


@pytest.mark.requires_addon
def test_a_level_block_is_explained_to_the_model_next_turn(engine, world):
    active_avatar(world)
    blocked = world.photos.create("wearing a sexy dress", level="off", record_blocked=True)
    sid = world.store.create_session("t")
    world.store.save_message(sid, "assistant", f"Oops\n[[photo:{blocked['id']}]]")
    engine.send_message("what happened?", sid)
    sent = json.dumps(engine.llm.calls[-1][1:])
    assert "(A photo you tried to send was not delivered.)" in sent and "adult" not in sent.lower()   # off: no hint
    adult.update_state({"enabled": True, "confirm_adult": True, "level": "romantic"})
    engine.send_message("and now?", sid)
    assert "needs adult mode set to Suggestive or higher" in json.dumps(engine.llm.calls[-1][1:])


def test_earlier_pictures_reach_the_model_as_short_notes(engine, world):
    active_avatar(world)
    rec = world.photos.create("in a cafe", level="off")
    blocked = world.photos.create("in lingerie", level="off", record_blocked=True)
    sid = world.store.create_session("t")
    world.store.save_message(sid, "assistant", f"Look!\n[[photo:{rec['id']}]]")
    world.store.save_message(sid, "assistant", f"Oops\n[[photo:{blocked['id']}]]")
    engine.send_message("nice", sid)
    sent = json.dumps(engine.llm.calls[-1][1:])                 # the conversation, without the system prompt
    assert "[[photo" not in sent and "imagined image was sent" in sent and "Requested scene: in a cafe." in sent
    assert "not evidence of a real event" in sent and "was not delivered" in sent


def test_photo_history_does_not_claim_pending_or_missing_images_were_sent(engine, world):
    active_avatar(world)
    ready = world.photos.create("in a cafe", level="off")
    pending = world.photos._read(ready['id'])
    pending['status'] = 'pending'
    world.photos._save(pending)
    marker = f"[[photo:{ready['id']}]]"
    assert 'not yet ready' in engine._history_text(marker)
    assert 'imagined image was sent' not in engine._history_text(marker)
    missing = engine._history_text('[[photo:p-0000000000]]')
    assert 'status is unavailable' in missing and 'not evidence of a real event' in missing


# ---------------------------------------------------------------- the streaming route

def test_stream_route_sends_photo_events(world, engine, monkeypatch):
    import core.emotion
    import core.routes.core_api as core_api
    import core.voice_layer
    monkeypatch.setattr(core_api, "_get_chat_engine", lambda: engine)
    monkeypatch.setattr(core.emotion, "analyze_and_store_emotion", lambda *a, **k: None)      # would call the model
    monkeypatch.setattr(core.voice_layer, "get_tts_audio", lambda *a, **k: None)              # would call a speech service
    active_avatar(world)
    engine.llm.chunks = ["On it! ", "[[photo: in a cafe]]"]
    sid = world.store.create_session("t")
    response = create_app("core_api").test_client().post("/api/chat/stream", json={"session_id": sid, "message": "hello"})
    events = [json.loads(line[6:]) for line in response.get_data(as_text=True).splitlines() if line.startswith("data: ")]
    assert "".join(e.get("response", "") for e in events).strip() == "On it!"
    photo_events = [e["photo"] for e in events if "photo" in e]
    assert len(photo_events) == 1 and photo_events[0]["status"] == "ready"
    assert events[-1] == {"status": "done"} and events.index({"status": "done"}) > events.index({"photo": photo_events[0]})
