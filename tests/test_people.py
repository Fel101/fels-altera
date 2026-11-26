"""Person kinds (pal, character, companion) and the start screen that lists them. NPC Forge is a fake; nothing here touches
the real data folder."""
import json

import pytest

from core import avatars, companions
from test_blueprints import create_app
from test_companion_api import active_avatar, tmp_store, world  # noqa: F401
from test_companions import second_avatar


def set_kind(world, avatar_id, kind):
    path = world.avatars.root / avatar_id / "avatar.json"
    record = json.loads(path.read_text("utf-8"))
    if kind is None:
        record.pop("kind", None)
    else:
        record["kind"] = kind
    path.write_text(json.dumps(record), "utf-8")
    return record


# ---------------------------------------------------------------- the kind on a record

def test_a_record_without_a_valid_kind_is_a_companion():
    assert avatars.kind_of({}) == avatars.kind_of(None) == avatars.kind_of({"kind": "robot"}) == "companion"
    assert [avatars.kind_of({"kind": k}) for k in avatars.KINDS] == ["pal", "character", "companion"]


# ---------------------------------------------------------------- pronouns for a person, not "she" for everyone

def test_pronouns_are_taken_from_the_records_gender_with_they_as_the_default():
    assert avatars.pronouns("female") == {"they": "she", "them": "her", "their": "her", "they're": "she's"}
    assert avatars.pronouns("male") == {"they": "he", "them": "him", "their": "his", "they're": "he's"}
    for unknown in ("", None, "robot", "nonbinary"):
        assert avatars.pronouns(unknown) == {"they": "they", "them": "them", "their": "their", "they're": "they're"}


def test_fill_pronouns_still_fills_npc_forges_placeholders_from_the_gender():
    assert avatars.fill_pronouns("{Subj} likes {poss} own jokes and tells {obj}", "male") == \
        "He likes his own jokes and tells him"
    assert avatars.fill_pronouns("{subj} likes {poss} own jokes", "female") == "she likes her own jokes"
    assert avatars.fill_pronouns("{subj} likes {poss} own jokes", "") == "they likes their own jokes"


def test_the_saved_context_report_no_longer_says_she_about_everyone():
    from core.knowledge_report import render_markdown
    report = {"scope": {"id": "x", "name": "Wren"}, "generated_at": "2026-11-18T00:00:00+00:00", "schema_version": 1,
              "recall": {}, "summary": {}, "limitations": [], "reason_descriptions": {}, "memories": [], "notebook": [],
              "reflection_notebook": [], "legacy_context": [], "conversations": []}
    text = render_markdown(report)
    assert text.startswith("# What is known about me")
    assert "What she knows" not in text


def test_an_import_from_forge_is_a_companion_and_says_so(world):
    mara = active_avatar(world)
    assert mara["kind"] == "companion"
    assert world.avatars.get(mara["id"])["kind"] == "companion"


def test_reimporting_keeps_the_kind_someone_already_has(world):
    mara = active_avatar(world)
    set_kind(world, mara["id"], "pal")
    again = world.avatars.import_from_forge(mara["id"])
    assert again["kind"] == "pal" and world.avatars.get(mara["id"])["kind"] == "pal"


def test_people_saved_before_kinds_become_companions_once_and_nothing_else_changes(world):
    mara, ana = active_avatar(world), second_avatar(world)
    before = set_kind(world, mara["id"], None)
    set_kind(world, ana["id"], "character")
    assert world.avatars.public(world.avatars.get(mara["id"]))["kind"] == "companion"     # read as a companion even before
    assert companions.migrate_kinds() == 1
    after = world.avatars.get(mara["id"])
    assert after == {**before, "kind": "companion"}
    assert world.avatars.get(ana["id"])["kind"] == "character"                           # a valid kind is left alone
    assert companions.migrate_kinds() == 0


def test_a_card_refresh_from_forge_keeps_the_kind(world):
    mara = active_avatar(world)
    set_kind(world, mara["id"], "pal")
    world.avatars.refresh_card(mara["id"])
    assert world.avatars.get(mara["id"])["kind"] == "pal"


# ---------------------------------------------------------------- the start screen's data

def test_people_counts_each_kind_and_continues_with_the_one_you_talked_to_last(world):
    mara, ana = active_avatar(world), second_avatar(world)
    set_kind(world, ana["id"], "pal")
    a = companions.open_companion(mara["id"])["session_id"]
    world.store.save_message(a, "user", "hi")
    companions.open_companion(ana["id"])
    data = companions.people()
    assert data["counts"] == {"pal": 1, "character": 0, "companion": 1}
    assert data["kinds"] == ["pal", "character", "companion"]
    assert data["last"]["id"] == ana["id"]                                              # the active one
    assert [p["id"] for p in data["people"]] == [mara["id"], ana["id"]]
    pals = companions.people("pal")
    assert [p["id"] for p in pals["people"]] == [ana["id"]] and pals["counts"] == data["counts"]
    with pytest.raises(companions.CompanionError) as error:
        companions.people("robot")
    assert error.value.status == 400


def test_people_never_opens_or_activates_anyone(world):
    mara, ana = active_avatar(world), second_avatar(world)
    world.avatars.activate(mara["id"])
    companions.people()
    assert world.avatars.active_id() == mara["id"]
    assert world.store.get_avatar_session_id(ana["id"]) is None


def test_with_nobody_there_is_no_one_to_continue_with(world):
    data = companions.people()
    assert data["people"] == [] and data["last"] is None and data["counts"] == {"pal": 0, "character": 0, "companion": 0}


# ---------------------------------------------------------------- the routes

@pytest.fixture
def client(world):
    return create_app("pages", "companions", "companion").test_client()


def test_the_people_route_lists_filters_and_rejects_unknown_kinds(client, world):
    mara, ana = active_avatar(world), second_avatar(world)
    set_kind(world, ana["id"], None)
    response = client.get("/api/people")
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    body = response.get_json()
    assert {p["id"] for p in body["people"]} == {mara["id"], ana["id"]} and body["counts"]["companion"] == 2
    assert world.avatars.get(ana["id"])["kind"] == "companion"                         # the route migrated the old record
    assert client.get("/api/people?kind=pal").get_json()["people"] == []
    assert client.get("/api/people?kind=robot").status_code == 400


def test_the_home_page_has_the_three_tiles_and_the_gallery(client, world):
    from core.profile import complete_onboarding
    complete_onboarding()  # a first run is sent to /setup instead (test_setup.py)
    response = client.get("/home")
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    page = response.get_data(as_text=True)
    for kind in ("pal", "character", "companion"):
        assert f'data-kind="{kind}"' in page and f'data-count="{kind}"' in page
    for element in ("homeContinue", "homeGallery", "homeStatus", "js/home.js", "css/home.css"):
        assert element in page
    assert "<title>Fel's Altera — Home</title>" in page
