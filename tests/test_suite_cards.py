"""Synthetic suite cards and fake Forge and Voice Lab responses."""

import base64
import io
import json
import os
import shutil
import zipfile
from hashlib import sha256

import pytest

from core import card_inbox, characters, charcard, companion, fels_suite, forge, pals, portraits, voicelab
from test_blueprints import create_app
from test_characters import V2, png, text_chunk
from test_companion_api import active_avatar, tmp_store, world  # noqa: F401

NPC = "captain-vale"
ID = "a" * 32
REF = "b" * 32
ARCHIVE = b"stand-in for a checked Voice Lab persona file"
LAB = "http://127.0.0.1:17665"


def card(*, bad_hash=False, extra=None, age=36, voice=None, undeclared=False):
    portrait, joy, talking = png(), png(width=3), png(width=4)
    digest = sha256(portrait).hexdigest()
    paths, assets = {}, []

    def add(path, data, kind="other", name=""):
        paths[path] = data
        uri = "embeded://" + path
        assets.append({"uri": uri, "ext": path.rsplit(".", 1)[-1], "type": kind, "name": name})
        return uri

    add("assets/icon/main.png", portrait, "icon", "main")
    add("assets/emotion/joy.png", joy, "emotion", "joy")
    add("assets/other/sprite.png", portrait, "other", "sprite")
    frame = "assets/other/living/talking.png"
    add(frame, talking)
    if undeclared:
        assets.pop()
    manifest = add("assets/other/living/manifest.json",
                   json.dumps({"portrait": {"sha256": digest}, "frames": {"talking": {"file": frame}}}))
    sheet = add("assets/other/npc.json", json.dumps({"id": NPC, "setting": "modern",
                "profile": {"first_name": "Captain", "last_name": "Vale", "age": age,
                            "occupation": "Cartographer", "personality": ["patient"], "hobbies": ["sketching"]}}))
    if voice and voice.get("file"):
        voice = {**voice, "file": add("assets/other/voice.zip", ARCHIVE)}
    fields = {**V2["data"], "assets": assets, "extensions": {"fels": {
        "contract": 1, "sheet": sheet, "voice": voice, "ai_generated": True, "licence": {"rating": "commercial_conditions"},
        "living_portrait": {"manifest": manifest, "portrait_sha256": "wrong" if bad_hash else digest}}}}
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as packed:
        packed.writestr("card.json", json.dumps({"spec": "chara_card_v3", "spec_version": "3.0", "data": fields}))
        for name, data in {**paths, **(extra or {})}.items():
            packed.writestr(name, data)
    return output.getvalue()


def test_imports_card_sheet_licence_sprite_and_frames_through_inbox(world, monkeypatch):
    raw = card()
    fels_suite.inbox_dir("altera", make=True)
    path = fels_suite.inbox_write("altera", "npc-forge", "captain.charx", raw)
    monkeypatch.setattr(fels_suite, "_recycle", os.unlink)
    client = create_app("companions", "companion").test_client()
    row, = client.get("/api/characters/inbox").json["cards"]
    assert row["sender"] == "npc-forge" and "path" not in row and not world.avatars.list()
    response = client.post("/api/characters/inbox/" + row["id"])
    assert response.status_code == 201, response.json
    avatar_id = response.json["card_id"]
    detail = client.get(f"/api/characters/{avatar_id}").json
    assert detail["ai_generated"] and detail["licence"] == "Commercial use with conditions"
    assert world.avatars.get(avatar_id)["card"]["occupation"] == "Cartographer"
    assert world.avatars.get(avatar_id)["card"]["traits"] == ["patient"]
    assert world.avatars.image_path(avatar_id, "body").read_bytes() == png()
    assert client.get(f"/api/avatars/{avatar_id}/portrait").data == png()
    for frame, width in (("happy", 3), ("talking", 4)):
        assert client.get(f"/api/avatars/{avatar_id}/living/{frame}").data == png(width=width)
    assert not path.exists() and (characters.originals_dir(world.avatars) / f"{avatar_id}.charx").read_bytes() == raw


@pytest.mark.parametrize(("options", "problem"), [
    ({"bad_hash": True}, "SHA-256 mismatch"),
    ({"undeclared": True}, "undeclared asset"),
    ({"age": 16}, "18 or older"),
    ({"extra": {"assets/../elsewhere.png": b"unread"}}, "unsafe"),
    ({"extra": {f"assets/unused/{i}.png": b"" for i in range(500)}}, "more than 500"),
    ({"extra": {"assets/unused/large.png": b"\0" * (charcard.MAX_FILE_BYTES + 1)}}, "larger than 20 MB"),
])
def test_refuses_broken_unsafe_and_oversized_cards(options, problem):
    with pytest.raises(characters.CharacterError, match=problem):
        characters.read_package("file.charx", card(**options))


def test_inbox_oversize_and_bad_card_report_reason_and_can_be_dismissed(world, monkeypatch):
    directory = fels_suite.inbox_dir("altera", make=True)
    broken = fels_suite.inbox_write("altera", "npc-forge", "broken.charx", b"broken")
    png_file = directory / "too-big.png"
    with png_file.open("wb") as handle:
        handle.truncate(charcard.MAX_FILE_BYTES + 1)
    (directory / ".partial.charx").write_bytes(b"hidden")
    monkeypatch.setattr(fels_suite, "_recycle", os.unlink)
    rows = {row["name"]: row for row in card_inbox.waiting()}
    assert set(rows) == {broken.name, png_file.name} and "20 MB" in rows[png_file.name]["problem"]
    service = companion.get_services()
    with pytest.raises(characters.CharacterError, match="ZIP is damaged"):
        card_inbox.accept(service, rows[broken.name]["id"])
    assert broken.exists()
    card_inbox.dismiss(rows[broken.name]["id"])
    assert not broken.exists() and not world.avatars.list()


def test_card_routes_avoid_a_current_forge_library_and_old_portraits_survive(world, monkeypatch):
    old = active_avatar(world)
    portrait = world.avatars.image_path(old["id"]).read_bytes()
    pal = pals.create(world.avatars, "Rowan", "sunny", "af_bella", engine="kokoro")
    portraits.from_library(world.avatars, pal["id"], old["id"])
    shutil.rmtree(world.avatars.forge)
    client = create_app("companions", "companion").test_client()
    assert client.get(f"/api/avatars/{old['id']}/portrait").data == portrait
    assert client.get(f"/api/avatars/{pal['id']}/portrait").data == portrait
    assert world.avatars.get(pal["id"])["voice"] == "af_bella"
    opened = client.post(f"/api/companions/{old['id']}/open")
    assert opened.status_code == 200 and opened.json["id"] == old["id"]

    def request(self, url, method, path, body=None, timeout=None, *, binary_limit=None):
        if path.startswith("/api/health"):
            return {"fels": fels_suite.health_block("npc-forge", "1.1.0", 48)}
        if path == "/api/npcs":
            return [{"id": NPC, "age": 36, "has": {"portrait": True}, "setting": "modern"}]
        if path == f"/api/npcs/{NPC}/card.charx?voice=0":
            return card()
        if path == f"/api/npcs/{NPC}/card.png?voice=0":
            return png(text_chunk("ccv3", {**V2, "spec": "chara_card_v3"}))
        pytest.fail(path)

    monkeypatch.setattr(forge.ForgeClient, "_request_at", request)
    world.avatars.forge_client = lambda: forge.ForgeClient()
    assert not world.avatars.forge_available()
    assert client.get("/api/avatars/forge").json["characters"][0]["id"] == NPC
    assert client.get(f"/api/forge/{NPC}/thumb").data == png()
    made = client.post("/api/avatars/import", json={"npc_id": NPC})
    assert made.status_code == 200 and world.avatars.get(NPC)["source"]["type"] == "npc-forge-card"
    assert world.avatars.image_path(NPC).read_bytes() == png()

def test_portable_recording_review_requires_permission_and_uses_the_imported_cast_identity(world, monkeypatch):
    calls = []
    approved = False

    def read(url, endpoint, *, data=None, reference_metadata=None, **_):
        nonlocal approved
        calls.append(endpoint)
        if endpoint == "/api/health":
            result = {"app": "Fel's Voice Lab", "lab_id": "test-library"}
        elif endpoint == "/api/portable/preview":
            assert data == ARCHIVE
            result = {"archive": {"title": "Synthetic speaker", "kind": "persona", "archive_sha256": sha256(data).hexdigest(),
                "voices": [{"engine_id": "turbo", "model_installed": True, "sample": {
                    "source_type": "recording", "origin": "Fictional test recording", "allowed_use": "Private rehearsal"}}]}}
        elif endpoint == "/api/portable/import":
            assert data == ARCHIVE
            assert json.loads(base64.urlsafe_b64decode(reference_metadata + "=" * (-len(reference_metadata) % 4))) == {
                "permission": True, "archive_sha256": sha256(data).hexdigest()}
            result = {"imported": {"kind": "persona", "identity": {"id": ID, "references": [{"id": REF}]}}}
        elif endpoint == f"/api/identities/{ID}/update":
            assert json.loads(data) == {"status": "approved", "reference_version_id": REF}
            approved = True
            result = {}
        elif endpoint == "/api/voices":
            result = {"voices": [{"name": "Actual Cast name", "kind": "cast", "model": "turbo", "identity_id": ID, "ready": approved}]}
        else:
            pytest.fail(endpoint)
        return json.dumps(result).encode()

    monkeypatch.setenv("VOICE_LAB_URL", LAB)
    monkeypatch.setattr(voicelab.voice_http, "read", read)
    client = create_app("companions").test_client()
    data = card(voice={"file": True, "name": "Synthetic speaker", "model": "turbo", "cloned_from_person": False})
    made = client.post("/api/characters/import", data={"file": (io.BytesIO(data), "voice.charx")}).json
    avatar_id = made["card_id"]
    url = f"/api/characters/{avatar_id}/card-voice"
    assert not calls
    review = client.post(url, json={"action": "preview"}).json
    assert review["cloned"] and review["origin"] == "Fictional test recording" and review["allowed_use"] == "Private rehearsal"
    assert client.post(url, json={"action": "use", "review": review["review"]}).status_code == 409
    assert not approved and "/api/portable/import" not in calls
    payload = {"action": "use", "review": review["review"], "permission": True}
    assert client.post(url, json=payload).json["name"] == "Actual Cast name"
    assert client.post(url, json=payload).json["name"] == "Actual Cast name"
    assert calls.count("/api/portable/import") == 1 and world.avatars.get(avatar_id)["voice"] == "Actual Cast name"
