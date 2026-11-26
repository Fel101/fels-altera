"""Starter characters: the cards in content/starters, offered on Home and added only when chosen."""

import json

import pytest

from core import characters, starter_characters as starters
from core.routes.lock import KIDS_ALLOWED
from test_blueprints import create_app
from test_characters import png
from test_companion_api import tmp_store, world  # noqa: F401
from test_suite_cards import card


@pytest.fixture
def shelf(tmp_path, monkeypatch):
    root = tmp_path / "starters"
    root.mkdir()
    (root / "captain.charx").write_bytes(card())
    (root / "starters.json").write_text(json.dumps({"starters": [
        {"key": "captain-vale", "name": "Captain Vale", "file": "captain.charx", "line": "A cartographer at sea."}]}),
        encoding="utf-8")
    monkeypatch.setattr(starters, "ROOT", root)
    return root


def test_a_starter_is_offered_until_added_and_adding_it_twice_adds_it_once(world, shelf):
    client = create_app("companions", "companion").test_client()
    assert client.get("/api/characters/starters").json == {"starters": [
        {"key": "captain-vale", "name": "Captain Vale", "line": "A cartographer at sea."}]}
    assert client.get("/api/characters/starters/captain-vale/portrait").data == png()
    assert not world.avatars.list()

    made = client.post("/api/characters/starters/captain-vale")
    assert made.status_code == 201, made.json
    avatar_id = made.json["card_id"]
    assert world.avatars.get(avatar_id)["source"]["starter"] == "captain-vale"
    assert world.checker.calls >= 1                                    # the picture age check ran
    assert client.get("/api/characters/starters").json == {"starters": []}

    again = client.post("/api/characters/starters/captain-vale")
    assert again.status_code == 201 and again.json["card_id"] == avatar_id
    assert len(world.avatars.list()) == 1

    assert client.delete(f"/api/companions/{avatar_id}").status_code == 200
    assert [row["key"] for row in client.get("/api/characters/starters").json["starters"]] == ["captain-vale"]


def test_a_starter_whose_picture_fails_the_age_check_is_not_added(world, shelf):
    world.checker.result.update(ok=False, apparent_age=15)
    client = create_app("companions", "companion").test_client()
    refused = client.post("/api/characters/starters/captain-vale")
    assert refused.status_code == 400 and "cannot be imported" in refused.json["error"]
    assert not world.avatars.list()


def test_unknown_starters_and_bad_list_entries_are_refused(world, shelf):
    (shelf / "other.json").write_bytes(card())
    (shelf / "starters.json").write_text(json.dumps({"starters": [
        {"key": "../captain", "name": "Bad key", "file": "captain.charx"},
        {"key": "up-a-folder", "name": "Bad file", "file": "../captain.charx"},
        {"key": "not-a-card", "name": "Not a CHARX", "file": "other.json"},
        {"key": "missing", "name": "Missing", "file": "missing.charx"},
        {"key": "no-name", "file": "captain.charx"},
        "not an entry"]}), encoding="utf-8")
    client = create_app("companions", "companion").test_client()
    assert client.get("/api/characters/starters").json == {"starters": []}
    for key in ("captain-vale", "missing", "up-a-folder"):
        assert client.post(f"/api/characters/starters/{key}").status_code == 404
        assert client.get(f"/api/characters/starters/{key}/portrait").status_code == 404
    (shelf / "starters.json").write_text("not json", encoding="utf-8")
    assert client.get("/api/characters/starters").json == {"starters": []}
    assert not world.avatars.list()


def test_adding_a_starter_is_refused_from_another_site_and_in_the_kids_view(world, shelf):
    client = create_app("companions", "companion").test_client()
    refused = client.post("/api/characters/starters/captain-vale", headers={"Origin": "http://elsewhere.example"})
    assert refused.status_code == 403 and not world.avatars.list()
    assert not {"companions.starter_characters", "companions.starter_portrait", "companions.add_starter"} & KIDS_ALLOWED


def test_the_starters_that_come_with_altera_are_adults_with_designed_voices_and_no_flirting():
    for entry in starters.catalogue():
        package = characters.read_package(entry["path"].name, entry["path"].read_bytes())
        assert package.card["name"] == entry["name"] and entry["line"]
        assert package.portrait, entry["key"]
        fields = characters.sheet_fields(package)
        assert int(fields["age"]) >= 21, entry["key"]
        fels = package.card.get("fels") or {}
        assert fels.get("voice", {}).get("cloned_from_person") is False, entry["key"]
        assert fels.get("licence", {}).get("line"), entry["key"]
        assert "flirty" not in json.dumps(package.card).lower(), entry["key"]
        assert not {"flirty", "seductive"} & {*package.expressions, *package.frames}, entry["key"]
