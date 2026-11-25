"""Her gallery: the studio's approved pictures kept in step with NPC Forge, and the rule that a picture she already has is used before a
new one is made. The picture backend and the age check are fakes; nothing here reaches ComfyUI or a model."""
import json

import pytest


from core.photos import _keys, match_score
from test_companion_api import active_avatar, drain, engine, system_prompt, tmp_store, world  # noqa: F401
from test_companion_images import PNG, make_studio

MARA = "mara-bell-47ab97"


def item(n, scene, label, level="off", keep=True, status="ready"):
    return {"id": f"s-{n:08x}", "scene": scene, "label": label, "level": level, "status": status, "keep": keep}


CAFE = item(1, "in a cozy cafe by a window with a latte, wearing a cream knit sweater", "Cafe")
PARK = item(2, "walking through a park in autumn in a cosy jacket, laughing", "Autumn walk")
BEACH = item(3, "on a beach at sunset with the waves around her ankles", "Beach", level="romantic")
ROBE = item(4, "wearing a silk robe by a bedroom window", "Silk robe", level="suggestive")


def studio(world, *items):
    return make_studio(world.forge, npc_id=MARA, items=list(items))


def gallery_ids(world, level="explicit"):
    return {p["scene"] for p in world.photos.gallery(MARA, level)}


# ---------------------------------------------------------------- matching

def test_scenes_are_compared_by_their_meaningful_words():
    assert _keys("walking through a park") >= {"walk", "park"} and _keys("Walks in the parks") >= {"walk", "park"}
    assert _keys("cafes") == _keys("a cafe") == {"cafe"}
    assert _keys("beaches") == _keys("a beach") and _keys("the waves") == _keys("a wave") and _keys("running") == {"run"}
    assert "bar" in _keys("at the bar") and "the" not in _keys("the cafe") and _keys("dress") == {"dress"}
    assert match_score({"cafe", "latte"}, {"cafe", "latte", "window"}) == 1.0
    assert match_score({"cafe", "latte", "sunset", "beach"}, {"cafe"}) == 0.25 and match_score(set(), {"cafe"}) == 0.0


@pytest.mark.requires_addon
def test_only_a_picture_that_really_shows_it_counts_as_a_match(world):
    active = active_avatar(world)
    studio(world, CAFE, PARK)
    world.photos.sync_premade(active["id"])
    match = world.photos.find_match(MARA, "off", "in a cafe with a latte")
    assert match["scene"] == CAFE["scene"] and match["reused"] is True and match["premade"] is True
    assert world.photos.find_match(MARA, "off", "walking in the park")["label"] == "Autumn walk"
    assert world.photos.find_match(MARA, "off", "having coffee somewhere quiet") is None        # one shared word is not enough
    assert world.photos.find_match(MARA, "off", "on a sailing boat at night") is None
    assert world.photos.find_match(MARA, "off", "") is None and world.photos.find_match(MARA, "off", "the a of") is None


@pytest.mark.requires_addon
def test_a_picture_that_was_just_sent_is_only_used_when_nothing_else_fits(world):
    active = active_avatar(world)
    twin = item(5, "in a cafe with a flat white, reading a book", "Cafe reading")
    studio(world, CAFE, twin)
    world.photos.sync_premade(active["id"])
    ids = {p["scene"]: p["id"] for p in world.photos.gallery(MARA, "off")}
    for _ in range(10):
        first = world.photos.find_match(MARA, "off", "in a cafe", recent=[ids[CAFE["scene"]]])
        assert first["id"] == ids[twin["scene"]]
    both = world.photos.find_match(MARA, "off", "in a cafe", recent=list(ids.values()))
    assert both["id"] in ids.values()                                                          # better repeated than made again


@pytest.mark.requires_addon
def test_a_picture_above_the_current_level_is_never_matched(world):
    active = active_avatar(world)
    studio(world, BEACH, ROBE)
    world.photos.sync_premade(active["id"])
    assert world.photos.find_match(MARA, "off", "on a beach at sunset") is None
    assert world.photos.find_match(MARA, "romantic", "on a beach at sunset")["label"] == "Beach"
    assert world.photos.find_match(MARA, "romantic", "a silk robe by a bedroom window") is None
    assert world.photos.find_match(MARA, "suggestive", "a silk robe by a bedroom window")["label"] == "Silk robe"


@pytest.mark.requires_addon
def test_what_she_made_for_the_person_earlier_can_be_sent_again_but_never_a_failure(world):
    active = active_avatar(world)
    studio(world, CAFE)
    world.photos.sync_premade(active["id"])
    made = world.photos.create("standing on a rooftop terrace at night with the city lights", level="off", source="model")
    blocked = world.photos.create("in lingerie on the bed", level="off", source="model", record_blocked=True)
    redone = world.photos.reroll(world.photos.create("sitting on a wooden pier at dawn with a thermos", level="off")["id"], level="off")
    scenes = [p["scene"] for p in world.photos.gallery(MARA, "off")]
    assert scenes[0] == CAFE["scene"] and made["scene"] in scenes and redone["scene"] in scenes      # the studio's pictures first
    assert blocked["scene"] not in scenes and scenes.count("sitting on a wooden pier at dawn with a thermos") == 1   # not the old copy
    again = world.photos.find_match(MARA, "off", "on a rooftop terrace at night")
    assert again["id"] == made["id"] and again["reused"] is True and again.get("premade") in (None, False)
    assert world.photos.find_match(MARA, "off", "in lingerie on the bed") is None


@pytest.mark.requires_addon
def test_the_pictures_that_fit_the_moment_are_listed_first(world):
    active = active_avatar(world)
    studio(world, CAFE, PARK, item(6, "on a hiking trail in the mountains", "Hike"), item(7, "cooking in a sunlit kitchen", "Kitchen"))
    world.photos.sync_premade(active["id"])
    pool = world.photos.list_premade(MARA, "off")
    for _ in range(5):
        assert world.photos.rank_premade(pool, "shall we go for a walk in the park?", 2)[0]["label"] == "Autumn walk"
    assert len(world.photos.rank_premade(pool, "anything", 3)) == 3 and world.photos.rank_premade([], "x") == []


# ---------------------------------------------------------------- keeping the gallery in step with NPC Forge

@pytest.mark.requires_addon
def test_the_studios_approved_pictures_become_hers_and_only_those(world):
    active = active_avatar(world)
    studio(world, CAFE, ROBE, item(8, "on a rainy street with an umbrella", "Rain", keep=False),
           item(9, "in a garden", "Garden", status="failed"))
    result = world.photos.sync_premade(active["id"])
    assert (result["imported"], result["updated"], result["removed"], result["refused"]) == (2, 0, 0, 0)
    assert gallery_ids(world) == {CAFE["scene"], ROBE["scene"]}
    assert all(p["age_checked"] for p in world.photos.gallery(MARA, "explicit"))
    assert world.photos.sync_premade(active["id"])["imported"] == 0                            # nothing new the second time
    assert world.checker.calls == 2                                                            # and no second age check


@pytest.mark.requires_addon
def test_a_redone_picture_replaces_the_old_copy_and_keeps_its_id(world):
    active = active_avatar(world)
    d = studio(world, CAFE)
    world.photos.sync_premade(active["id"])
    before = world.photos.gallery(MARA, "off")[0]
    (d / "img" / f"{CAFE['id']}.png").write_bytes(PNG + b"a brand new take, a little longer")
    result = world.photos.sync_premade(active["id"])
    assert result["updated"] == 1 and result["imported"] == 0
    after = world.photos.gallery(MARA, "off")
    assert len(after) == 1 and after[0]["id"] == before["id"]
    assert world.photos.image_path(before["id"]).read_bytes() == PNG + b"a brand new take, a little longer"


@pytest.mark.requires_addon
def test_a_picture_taken_out_of_the_studio_leaves_her_gallery(world):
    active = active_avatar(world)
    d = studio(world, CAFE, PARK)
    world.photos.sync_premade(active["id"])
    (d / "items.json").write_text(json.dumps({"items": [{**CAFE, "keep": False}, PARK]}), "utf-8")
    result = world.photos.sync_premade(active["id"])
    assert result["removed"] == 1 and gallery_ids(world) == {PARK["scene"]}
    (d / "items.json").write_text(json.dumps({"items": []}), "utf-8")
    assert world.photos.sync_premade(active["id"])["removed"] == 1 and gallery_ids(world) == set()


@pytest.mark.requires_addon
def test_a_studio_list_that_cannot_be_read_takes_nothing_away(world):
    active = active_avatar(world)
    d = studio(world, CAFE, PARK)
    world.photos.sync_premade(active["id"])
    (d / "items.json").write_text('{"items": [{"id": "s-000', "utf-8")                          # NPC Forge is half way through writing it
    result = world.photos.sync_premade(active["id"])
    assert result["removed"] == 0 and gallery_ids(world) == {CAFE["scene"], PARK["scene"]}
    (d / "items.json").write_text(json.dumps({"items": [PARK]}), "utf-8")                       # readable again: now it counts
    assert world.photos.sync_premade(active["id"])["removed"] == 1 and gallery_ids(world) == {PARK["scene"]}


@pytest.mark.requires_addon
def test_a_missing_forge_folder_takes_nothing_away(world):
    active = active_avatar(world)
    studio(world, CAFE)
    world.photos.sync_premade(active["id"])
    world.avatars.forge = world.tmp / "not-there"                                              # NPC Forge's folder is gone for now
    assert world.photos.sync_premade(active["id"]) == {"imported": 0, "updated": 0, "removed": 0, "refused": 0, "waiting": 0,
                                                       "problems": []}
    assert gallery_ids(world) == {CAFE["scene"]}


@pytest.mark.requires_addon
def test_a_refused_picture_is_not_shown_to_the_age_check_again_until_it_changes(world):
    active = active_avatar(world)
    d = studio(world, CAFE)
    world.checker.result.update(ok=False, apparent_age=16)
    calls = world.checker.calls
    first = world.photos.sync_premade(active["id"])
    assert first["refused"] == 1 and first["imported"] == 0 and "younger" in first["problems"][0].lower() or "age check" in first["problems"][0]
    assert world.checker.calls == calls + 1 and gallery_ids(world) == set()
    again = world.photos.sync_premade(active["id"])
    assert again["refused"] == 1 and world.checker.calls == calls + 1                          # remembered: no new check
    world.checker.result.update(ok=True, apparent_age=30)
    (d / "img" / f"{CAFE['id']}.png").write_bytes(PNG + b"redone")                             # the studio redid it: worth another look
    assert world.photos.sync_premade(active["id"])["imported"] == 1 and gallery_ids(world) == {CAFE["scene"]}


@pytest.mark.requires_addon
def test_a_picture_the_age_check_could_not_look_at_waits_and_is_not_remembered_as_refused(world):
    active = active_avatar(world)
    studio(world, CAFE, PARK, ROBE)                                                            # two everyday pictures and a suggestive one
    world.checker.result.update(ok=None, apparent_age=None, error="The vision model could not be reached or cannot read images.")
    result = world.photos.sync_premade(active["id"])
    assert (result["imported"], result["waiting"], result["refused"]) == (2, 1, 0)
    assert gallery_ids(world) == {CAFE["scene"], PARK["scene"]}                                # everyday pictures do not wait for it
    assert world.checker.calls == 1                                                            # and it was not asked again and again
    assert "could not run" in result["problems"][0] and world.photos.sync_status(MARA)["last"]["waiting"] == 1
    world.checker.result.update(ok=True, apparent_age=30, error=None)                          # the vision model is back
    again = world.photos.sync_premade(active["id"])
    assert (again["imported"], again["waiting"], again["refused"]) == (1, 0, 0)
    assert gallery_ids(world) == {CAFE["scene"], PARK["scene"], ROBE["scene"]}


@pytest.mark.requires_addon
def test_the_background_sync_runs_once_at_a_time_and_reports_what_it_did(world):
    active_avatar(world)
    studio(world, CAFE)
    assert world.photos.sync_status(MARA) == {"running": False, "synced_at": None, "last": {}}
    assert world.photos.sync_premade_async(MARA) is True                                       # inline in the test
    status = world.photos.sync_status(MARA)
    assert status["running"] is False and status["last"]["imported"] == 1 and status["synced_at"]
    world.photos._syncing.add(MARA)
    assert world.photos.sync_premade_async(MARA) is False and world.photos.sync_status(MARA)["running"] is True


# ---------------------------------------------------------------- the chat uses what she has before making anything

@pytest.mark.requires_addon
def test_a_picture_she_already_has_is_sent_at_once_and_nothing_is_made(engine, world):
    active = active_avatar(world)
    studio(world, CAFE, PARK)
    world.photos.sync_premade(active["id"])
    engine.llm.reply = "Here you go [[photo: sitting in a cafe with a latte]]"
    text, found = engine.send_message("hi", world.store.create_session("t"), return_photos=True)
    assert text == "Here you go" and len(found) == 1 and found[0]["label"] == "Cafe" and found[0]["reused"] is True
    assert world.backend.calls == []


@pytest.mark.requires_addon
def test_pictures_made_in_the_studio_after_she_was_opened_are_fetched_when_you_talk_to_her(engine, world):
    active_avatar(world)
    sid = world.store.create_session("t")
    engine.send_message("hi", sid)
    assert gallery_ids(world) == set()
    studio(world, CAFE)                                                                        # approved in the studio afterwards
    engine.llm.reply = "Sure!"
    found = engine.send_message("send me a selfie in a cafe with a latte", sid, return_photos=True)[1][0]
    assert found["label"] == "Cafe" and found["premade"] is True and world.backend.calls == []


@pytest.mark.requires_addon
def test_only_when_nothing_fits_is_a_new_picture_made(engine, world):
    active = active_avatar(world)
    studio(world, CAFE)
    world.photos.sync_premade(active["id"])
    engine.llm.reply = "Ooh [[photo: paddling a red kayak on a mountain lake]]"
    _, found = engine.send_message("hi", world.store.create_session("t"), return_photos=True)
    assert found[0]["scene"] == "paddling a red kayak on a mountain lake" and found[0]["source"] == "model" and len(world.backend.calls) == 1
    engine.llm.reply = "Here [[photo: paddling a red kayak on a mountain lake]]"                # the same request again: hers now
    _, again = engine.send_message("hi", world.store.create_session("t2"), return_photos=True)
    assert again[0]["id"] == found[0]["id"] and again[0]["reused"] is True and len(world.backend.calls) == 1


@pytest.mark.requires_addon
def test_a_plain_request_gets_a_studio_picture_that_was_not_sent_lately(engine, world):
    active = active_avatar(world)
    studio(world, CAFE, PARK)
    world.photos.sync_premade(active["id"])
    engine.llm.reply = "Sure!"
    sid = world.store.create_session("t")
    first = engine.send_message("send me a selfie", sid, return_photos=True)[1][0]
    second = engine.send_message("send me another pic", sid, return_photos=True)[1][0]
    assert {first["label"], second["label"]} == {"Cafe", "Autumn walk"}                        # not the same one twice in a row
    third = engine.send_message("send me one more selfie", sid, return_photos=True)[1][0]
    assert third["premade"] is True and world.backend.calls == []


@pytest.mark.requires_addon
def test_what_the_person_asks_for_is_matched_against_the_gallery_too(engine, world):
    active = active_avatar(world)
    studio(world, CAFE, PARK)
    world.photos.sync_premade(active["id"])
    engine.llm.reply = "On it"
    found = engine.send_message("send me a selfie in the park", world.store.create_session("t"), return_photos=True)[1][0]
    assert found["label"] == "Autumn walk" and world.backend.calls == []
    made = engine.send_message("send me a selfie on the roof", world.store.create_session("t2"), return_photos=True)[1][0]
    assert made["scene"] == "on the roof" and len(world.backend.calls) == 1


@pytest.mark.requires_addon
def test_she_is_told_about_the_pictures_that_fit_and_how_many_there_are(engine, world):
    active = active_avatar(world)
    many = [item(0x100 + i, f"pose number {i} in a quiet room", f"Room {i}") for i in range(14)]
    studio(world, CAFE, *many)
    world.photos.sync_premade(active["id"])
    sid = world.store.create_session("t")
    engine.send_message("do you have one from that cafe with the latte?", sid)
    prompt = system_prompt(engine)
    cafe_id = next(p["id"] for p in world.photos.gallery(MARA, "off") if p["label"] == "Cafe")
    assert cafe_id in prompt and "you have 15 in all" in prompt and "about half a minute" in prompt
    assert prompt.count("- p-") == 12


def test_an_avatar_with_nothing_ready_is_not_told_about_a_gallery(engine, world):
    active_avatar(world)
    engine.send_message("hi", world.store.create_session("t"))
    assert "Ready-made photos" not in system_prompt(engine) and "Sending Photos" in system_prompt(engine)
