"""Portraits from NPC Forge for pals and characters: painted from a description or redrawn from the card's picture, age-checked
before use, the card's picture kept to go back to, and the living portrait reached through the `forge` link. NPC Forge and the
model are fakes; nothing here touches the real data folder or a running NPC Forge."""
import base64
import json

import pytest

from core import avatars, characters, companion, forge, pals, portraits
from test_blueprints import create_app
from test_characters import V2, png, text_chunk
from test_companion_api import tmp_store, world  # noqa: F401
from test_companion_images import FakeChecker, make_forge
from test_companions import fake_opener
from test_living import make_living

NPC = "pip-moss-1a2b3c"


class FakeForge:
    """What core/portraits.py asks of forge.ForgeClient. The test makes the library folder when the job is 'done'."""

    def __init__(self, state="running", fail=None):
        self.state, self.fail, self.painted, self.lived, self.cancelled = state, fail, [], [], []

    def paint(self, **kw):
        if self.fail:
            raise forge.ForgeError(self.fail)
        self.painted.append(kw)
        return {"npc_id": NPC, "job_id": "job1"}

    def living(self, npc_id, edit_model=""):
        self.lived.append((npc_id, edit_model))
        return "job2"

    def job(self, job_id):
        return {"state": self.state, "note": "Portrait"}

    def cancel(self, job_id):
        self.cancelled.append(job_id)


def a_pal(world, name="Pip"):
    return pals.create(world.avatars, name, "sunny", "", engine="kokoro")


def a_character(world, card=None):
    data = png(text_chunk("chara", card or V2))
    return characters.import_card(world.avatars, "vale.png", data, checker=FakeChecker(ok=True))


def painted(world, avatar_id, fake=None, living=None, checker=None, **kw):
    """Start a portrait and finish it: NPC Forge 'paints' NPC with a portrait in its library."""
    fake = fake or FakeForge()
    portraits.start(world.avatars, avatar_id, fake, **{"source": "description", "description": "Short red hair.", **kw})
    if not (world.forge / NPC).exists():
        make_forge(world.forge, npc_id=NPC, first="Pip")
    fake.state = "done"
    return portraits.check(world.avatars, avatar_id, fake, checker or FakeChecker(ok=True), living)


# ---------------------------------------------------------------- the Forge client

FORGE_CONFIG = {"api_version": 43, "settings": {"modern": {"label": "Modern"}, "fantasy": {"label": "Fantasy"},
                                                "models": {"label": "Models", "origins": [], "hair_colors": [], "age_groups": []}},
                "styles": {"semi_real": {"label": "Semi-realistic"}, "anime": {"label": "Anime"}},
                "engines": {"image": [{"id": "mage", "label": "Mage-Flow", "note": "Light."},
                                      {"id": "qwen2512", "label": "Qwen-Image 2512", "note": "Needs about 29 GB."}],
                            "edit": [{"id": "mage", "label": "Mage-Flow-Edit", "note": "Fast."},
                                     {"id": "qwen2511", "label": "Qwen-Image-Edit 2511", "note": "Needs about 29 GB."}],
                            "three_d": [{"id": "hunyuan21", "label": "Hunyuan"}]},
                "defaults": {"options": {"image_model": "qwen2512", "edit_model": "qwen2511"}}}


def forge_routes(**extra):
    return {"GET /api/config": FORGE_CONFIG, "GET /api/health": {"online": True},
            "POST /api/roll": {"freeform": True, "profile": {"first_name": "Pip"}, "appearance": {"extra": "x"}},
            "POST /api/npcs": {"id": NPC}, f"POST /api/npcs/{NPC}/photo": {"id": NPC},
            "POST /api/generate": {"npc_ids": [NPC], "jobs": [{"id": "job1"}]}, **extra}


def test_forge_offers_every_style_and_every_setting_but_the_adult_one():
    status = forge.ForgeClient({}, opener=fake_opener(forge_routes())).status()
    assert status["styles"] == [{"id": "semi_real", "label": "Semi-realistic"}, {"id": "anime", "label": "Anime"}]
    assert [s["id"] for s in status["settings"]] == ["modern", "fantasy"]


def test_painting_from_words_makes_a_freeform_character_and_asks_for_its_portrait_only():
    log = []
    client = forge.ForgeClient({}, opener=fake_opener(forge_routes(), log))
    made = client.paint(name="Pip", description="Short red hair, freckles.", gender="female", style="anime", setting="modern")
    assert made == {"npc_id": NPC, "job_id": "job1"}
    sent = {key: json.loads(data) for key, data in log if data and key.startswith("POST")}
    assert sent["POST /api/roll"] == {"setting": "modern", "style": "anime", "freeform": "a grown adult, Short red hair, freckles.",
                                      "locked": {"profile.first_name": "Pip", "profile.gender": "female"}}
    assert sent["POST /api/npcs"]["freeform"] is True and f"POST /api/npcs/{NPC}/photo" not in sent
    assert sent["POST /api/generate"] == {"npc_id": NPC, "style": "anime", "options": {"quality": "standard"},
                                          "assets": {"portrait": True, "sprite": False, "expressions": []}}


def test_painting_from_a_picture_sends_it_as_the_photo_first():
    log = []
    client = forge.ForgeClient({}, opener=fake_opener(forge_routes(), log))
    client.paint(name="Vale", description="", style="semi_real", setting="fantasy", photo=b"PNGBYTES")
    keys = [key for key, _ in log if key.startswith("POST")]
    assert keys == ["POST /api/roll", "POST /api/npcs", f"POST /api/npcs/{NPC}/photo", "POST /api/generate"]
    roll = json.loads(next(data for key, data in log if key == "POST /api/roll"))
    assert roll["freeform"] == "a grown adult" and "profile.gender" not in roll["locked"]   # nothing rolled for the picture
    photo = json.loads(next(data for key, data in log if key.endswith("/photo")))["image"]
    assert base64.b64decode(photo.split(",", 1)[1]) == b"PNGBYTES"


# ---------------------------------------------------------------- NPC Forge 44: the GPU, an adult roll, a full name

LM_STUDIO = [{"name": "llama-server.exe", "mb": 25190}]
READY = {"ready": True, "code": None, "message": "Ready: ComfyUI draws on the R9700.", "holders": []}


def modern_client(gpu, log, unload=None, config=None, **routes):
    """A client for NPC Forge 44 whose GET /api/gpu answers `gpu` in turn (the last answer repeats)."""
    answers = list(gpu)
    table = forge_routes(**{"GET /api/config": {**FORGE_CONFIG, "api_version": 44}, **routes})
    base = fake_opener(table, log)

    def opener(request, timeout=None):
        if request.full_url.endswith("/api/gpu"):
            table["GET /api/gpu"] = answers.pop(0) if len(answers) > 1 else answers[0]
        return base(request, timeout)
    unloaded = []
    client = forge.ForgeClient(config or {}, opener=opener, sleep=lambda s: None,
                               unload=unload or (lambda: unloaded.append(True) or True))
    return client, unloaded


def test_from_api_44_painting_asks_for_an_adult_with_the_whole_name_and_no_prefix():
    log = []
    client, unloaded = modern_client([READY], log)
    client.paint(name="Wren Ashby", description="She has short grey hair.", style="anime", setting="modern")
    roll = json.loads(next(data for key, data in log if key == "POST /api/roll"))
    assert roll["adult"] is True and roll["freeform"] == "She has short grey hair."     # NPC Forge leads with the adult
    assert roll["locked"] == {"profile.first_name": "Wren Ashby", "profile.last_name": ""}   # no gender unless chosen
    assert [key for key, _ in log].index("GET /api/gpu") < [key for key, _ in log].index("POST /api/roll")
    assert unloaded == []


def test_lm_studio_holding_the_gpu_is_unloaded_before_a_picture_is_queued():
    log = []
    busy = {"ready": False, "code": "gpu_busy", "message": "Another program is using the GPU.", "holders": LM_STUDIO}
    client, unloaded = modern_client([busy, busy, READY], log)
    assert client.living(NPC, edit_model="mage") == "job1"
    assert unloaded == [True] and [key for key, _ in log].count("GET /api/gpu") == 3
    assert [key for key, _ in log][-1] == "POST /api/generate"


def test_api_44_card_photo_only_gets_a_nonempty_adult_roll_with_age_provenance():
    log = []
    roll_result = {"profile": {"age": 34, "age_source": "rolled"}}
    client, _ = modern_client([READY], log, **{"POST /api/roll": roll_result})
    made = client.paint(name="Wren Ashby", photo=b"png", style="anime", setting="modern")
    roll = json.loads(next(data for key, data in log if key == "POST /api/roll"))
    assert roll["adult"] is True and roll["freeform"] == "a character"
    assert made["age"] == 34 and made["age_source"] == "rolled"


def test_busy_comfy_is_checked_again_before_queueing():
    log = []
    client, unloaded = modern_client([{"ready": False, "code": "comfy_busy"}, READY], log)
    assert client.living(NPC) == "job1"
    assert [path for path, _ in log][-3:] == ["GET /api/gpu", "GET /api/gpu", "POST /api/generate"]
    assert unloaded == []


def test_retry_requires_confirmed_gpu_readiness_after_unload():
    log = []
    busy = {"ready": False, "code": "gpu_busy", "holders": LM_STUDIO}
    job = {"id": "abc42", "status": "error", "code": "gpu_busy"}
    client, unloaded = modern_client([busy, None], log, **{"GET /api/jobs": [job], "POST /api/jobs/abc42/retry": {}})
    assert client.job("abc42")["code"] == "gpu_busy"
    with pytest.raises(forge.ForgeError, match="Unload its model"):
        client.retry_gpu_job("abc42")
    assert unloaded == [True] and not any(path.startswith("POST") for path, _ in log)

    log.clear()
    client, unloaded = modern_client([busy, None, READY], log, **{"GET /api/jobs": [job], "POST /api/jobs/abc42/retry": {}})
    assert client.job("abc42")["code"] == "gpu_busy"
    client.retry_gpu_job("abc42")
    assert unloaded == [True] and log[-1][0] == "POST /api/jobs/abc42/retry"


@pytest.mark.parametrize("gpu, says", [
    ({"ready": False, "code": "comfy_down", "message": "ComfyUI is not running."}, "Start ComfyUI"),
    ({"ready": False, "code": "comfy_busy", "message": "ComfyUI is busy."}, "Try again when it is done"),
    ({"ready": False, "code": "gpu_comfy_held", "message": "ComfyUI holds 6 GB."}, "Restart ComfyUI"),
    ({"ready": False, "code": "gpu_busy", "message": "Another program is using the GPU: Blender (9 GB).",
      "holders": [{"name": "blender.exe", "mb": 9000}]}, "Blender"),
    ({"ready": False, "code": "low_memory", "message": "Not enough free memory."}, "Not enough free memory"),
])
def test_a_gpu_that_is_not_free_is_said_plainly_and_nothing_is_made(gpu, says):
    log = []
    client, unloaded = modern_client([gpu], log)
    with pytest.raises(forge.ForgeError, match=says):
        client.paint(name="Pip", description="Red hair.", style="anime", setting="modern")
    assert not any(key.startswith("POST") for key, _ in log) and unloaded == []       # only LM Studio is unloaded


def test_lm_studio_is_left_alone_when_unloading_is_switched_off_or_does_not_free_the_gpu():
    busy = {"ready": False, "code": "gpu_busy", "message": "Another program is using the GPU.", "holders": LM_STUDIO}
    log = []
    client, unloaded = modern_client([busy], log, config={"companion": {"forge": {"unload_llm": False}}})
    with pytest.raises(forge.ForgeError, match="Unload its model in LM Studio"):
        client.living(NPC)
    assert unloaded == []
    client, unloaded = modern_client([busy], [])
    with pytest.raises(forge.ForgeError, match="Unload its model in LM Studio"):
        client.living(NPC)                                                          # still held after waiting
    assert unloaded == [True]


def test_suite_forge_queues_pictures_and_makes_room_for_them_itself():
    busy = {"ready": False, "code": "gpu_busy", "holders": LM_STUDIO, "one_model_at_a_time": True,
            "suite_lock": {"holders": [], "alone": None}}
    health = forge.fels_suite.health_block("npc-forge", "1.1.0", 44)
    log = []
    client, unloaded = modern_client([busy], log, **{"GET /api/health": {"online": True, "fels": health}})
    client.paint(name="Pip", description="Red hair.", style="anime", setting="modern")
    assert any(key == "POST /api/roll" for key, _ in log)
    assert unloaded == []


def test_an_older_npc_forge_is_not_asked_about_the_gpu_and_an_unreadable_answer_does_not_block():
    log = []
    forge.ForgeClient({}, opener=fake_opener(forge_routes(), log)).living(NPC)
    assert "GET /api/gpu" not in [key for key, _ in log]
    log = []
    client, _ = modern_client([READY], log)
    client.gpu = lambda: None                                                       # /api/gpu did not answer
    assert client.living(NPC) == "job1"


@pytest.mark.parametrize("kw, message", [
    ({"style": "oil", "setting": "modern"}, "art styles"),
    ({"style": "anime", "setting": "models"}, "settings"),                       # never the adult setting
    ({"style": "anime", "setting": "modern", "description": ""}, "Describe"),
    ({"style": "anime", "setting": "modern", "photo": b"x" * (forge.MAX_PHOTO_BYTES + 1)}, "too big"),
])
def test_painting_refuses_what_nobody_offered(kw, message):
    client = forge.ForgeClient({}, opener=fake_opener(forge_routes()))
    with pytest.raises(forge.ForgeError, match=message):
        client.paint(**{"name": "Pip", "description": "Red hair.", **kw})


def test_forge_lists_its_picture_and_redraw_engines_and_its_own_defaults():
    status = forge.ForgeClient({}, opener=fake_opener(forge_routes())).status()
    assert [e["id"] for e in status["engines"]["image"]] == ["mage", "qwen2512"]
    assert status["engines"]["edit"][1] == {"id": "qwen2511", "label": "Qwen-Image-Edit 2511", "note": "Needs about 29 GB."}
    assert status["default_engines"] == {"image": "qwen2512", "edit": "qwen2511"}
    assert "three_d" not in status["engines"]


def test_a_chosen_engine_is_sent_and_one_nobody_offered_is_refused():
    log = []
    client = forge.ForgeClient({}, opener=fake_opener(forge_routes(), log))
    client.paint(name="Pip", description="Red hair.", style="anime", setting="modern", image_model="mage", edit_model="qwen2511")
    client.paint(name="Vale", style="anime", setting="modern", photo=b"PNG", image_model="qwen2512", edit_model="mage")
    client.paint(name="Pip", description="Red hair.", style="anime", setting="modern")
    options = [json.loads(data)["options"] for key, data in log if key == "POST /api/generate"]
    assert options == [{"quality": "standard", "image_model": "mage"},          # words: the picture engine only
                       {"quality": "standard", "edit_model": "mage"},           # a photo: the redraw engine only
                       {"quality": "standard"}]                                 # none chosen: NPC Forge's own default
    assert client.living(NPC, edit_model="mage") == "job1"
    assert json.loads(log[-1][1])["options"] == {"quality": "standard", "edit_model": "mage"}
    with pytest.raises(forge.ForgeError, match="engines"):
        client.paint(name="Pip", description="Red hair.", style="anime", setting="modern", image_model="hunyuan21")
    with pytest.raises(forge.ForgeError, match="engines"):
        client.living(NPC, edit_model="qwen2512")                               # an image engine is not a redraw engine


def test_bringing_it_to_life_asks_for_the_living_portrait_of_that_character():
    log = []
    client = forge.ForgeClient({}, opener=fake_opener(forge_routes(), log))
    assert client.living(NPC) == "job1"
    body = json.loads(next(data for key, data in log if key == "POST /api/generate"))
    assert body == {"npc_id": NPC, "assets": {"living": True}, "options": {"quality": "standard"}}
    with pytest.raises(forge.ForgeError):
        client.living("../evil")


# ---------------------------------------------------------------- a pal's portrait

def test_a_pal_gets_a_portrait_from_words_and_can_go_back_to_its_initial(world):
    pal = a_pal(world)
    assert portraits.summary(world.avatars, world.avatars.get(pal["id"]))["from"] == "none"
    fake = FakeForge()
    started = portraits.start(world.avatars, pal["id"], fake, source="description", description="Short red hair.",
                              style="anime", setting="modern", gender="female")
    assert started["job"]["kind"] == "portrait" and fake.painted[0]["name"] == "Pip" and fake.painted[0]["photo"] is None
    assert portraits.check(world.avatars, pal["id"], fake)["state"] == "running"              # still painting
    with pytest.raises(portraits.PortraitError, match="already"):
        portraits.start(world.avatars, pal["id"], fake, source="description", description="Again.")
    make_forge(world.forge, npc_id=NPC, first="Pip")
    fake.state = "done"
    done = portraits.check(world.avatars, pal["id"], fake, FakeChecker(ok=True))
    assert done["state"] == "ready" and done["portrait"]["from"] == "forge" and done["portrait"]["can_bring_to_life"]
    record = world.avatars.get(pal["id"])
    assert world.avatars.image_path(pal["id"]).read_bytes() == (world.forge / NPC / "portrait.png").read_bytes()
    assert record["forge"]["id"] == NPC and record["forge"]["description"] == "Short red hair." and "forge_job" not in record
    assert avatars.forge_id(record) == NPC and record["source"]["type"] == "pal"          # the pal file stays the source
    assert world.avatars.public(record)["portrait_at"] == record["forge"]["made"]
    again = portraits.summary(world.avatars, record)
    assert again["description"] == "Short red hair." and again["style"] == "anime" and again["gender"] == "female"

    back = portraits.stop_using(world.avatars, pal["id"])
    assert back["from"] == "none" and world.avatars.image_path(pal["id"]) is None
    assert avatars.forge_id(world.avatars.get(pal["id"])) is None
    with pytest.raises(portraits.PortraitError, match="not using"):
        portraits.stop_using(world.avatars, pal["id"])


def test_a_pals_portrait_survives_saving_its_settings_and_rereading_pal_yaml(world):
    pal = a_pal(world)
    painted(world, pal["id"], image_model="mage")
    before = world.avatars.get(pal["id"])
    pals.update(world.avatars, pal["id"], name="Pippa", features={"memory": True})        # rebuilt from pal.yaml
    path = world.avatars.root / pal["id"] / pals.PAL_FILE
    path.write_text(path.read_text("utf-8").replace("Pippa", "Pips"), "utf-8")
    assert pals.refresh(world.avatars, pal["id"]) is True                                 # a hand edit is taken up
    after = world.avatars.get(pal["id"])
    assert after["name"] == "Pips"
    for key in ("forge", "portrait_from", "age_check"):
        assert after[key] == before[key], key
    assert portraits.summary(world.avatars, after)["from"] == "forge" and avatars.forge_id(after) == NPC


def test_a_portrait_that_looks_underage_is_not_used(world):
    pal = a_pal(world)
    answer = painted(world, pal["id"], checker=FakeChecker(ok=False, age=15))
    assert answer["state"] == "rejected" and "younger than 18" in answer["note"]
    assert world.avatars.image_path(pal["id"]) is None and answer["portrait"]["from"] == "none"
    assert "forge_job" not in world.avatars.get(pal["id"])                                   # it can be tried again


@pytest.mark.parametrize("kw, message", [
    ({"source": "photo"}, "Choose whether"),
    ({"description": ""}, "Describe how they look"),
    ({"description": "x" * 1501}, "under 1500"),
    ({"gender": "robot"}, "listed genders"),
    ({"description": "a 14 year old girl"}, "cannot be used"),                               # the hard rules apply
    ({"source": "card"}, "no picture"),                                                      # a pal has no card
])
def test_what_cannot_be_painted_is_refused_before_npc_forge_is_asked(world, kw, message):
    pal = a_pal(world)
    fake = FakeForge()
    with pytest.raises(portraits.PortraitError, match=message):
        portraits.start(world.avatars, pal["id"], fake, **{"source": "description", "description": "Red hair.", **kw})
    assert fake.painted == []


def test_a_companion_is_not_painted_here_and_forge_trouble_is_said_plainly(world):
    mara = world.avatars.import_from_forge("mara-bell-47ab97")
    with pytest.raises(portraits.PortraitError) as refused:
        portraits.start(world.avatars, mara["id"], FakeForge(), source="description", description="Red hair.")
    assert refused.value.status == 404
    pal = a_pal(world)
    with pytest.raises(portraits.PortraitError, match="not running") as down:
        portraits.start(world.avatars, pal["id"], FakeForge(fail="NPC Forge is not running. Start it, then try again."),
                        source="description", description="Red hair.")
    assert down.value.status == 503 and "forge_job" not in world.avatars.get(pal["id"])


def test_a_job_npc_forge_lost_or_cancelled_ends_and_one_it_cannot_reach_is_kept(world):
    pal = a_pal(world)
    fake = FakeForge()
    portraits.start(world.avatars, pal["id"], fake, source="description", description="Red hair.")

    class Down(FakeForge):
        def job(self, job_id):
            raise forge.ForgeError("NPC Forge is not running. Start it, then try again.")
    assert portraits.check(world.avatars, pal["id"], Down())["state"] == "unreachable"
    assert world.avatars.get(pal["id"])["forge_job"]["job_id"] == "job1"                     # looked at again later
    stopped = portraits.cancel(world.avatars, pal["id"], fake)
    assert fake.cancelled == ["job1"] and stopped["job"] is None
    portraits.start(world.avatars, pal["id"], fake, source="description", description="Red hair.")
    fake.state = "error"
    assert portraits.check(world.avatars, pal["id"], fake) ["state"] == "error"
    assert "forge_job" not in world.avatars.get(pal["id"])


def test_a_living_portrait_the_gpu_is_refused_for_is_stopped_and_says_why(world):
    """Live, LM Studio held the GPU and NPC Forge refused each of ten expressions three times, for 55 minutes: the first
    refusal stops the job, and the note says what holds the GPU."""
    busy = "Another program is using the GPU: llama-server.exe (24.6 GB). (one model on the GPU at a time) Close it and try again."

    class Busy(FakeForge):
        def job(self, job_id):
            return {"state": "running", "note": f"Expression: sad. Expression: happy could not be made: {busy}",
                    "blocked": self.blocked}
    pal = a_pal(world)
    painted(world, pal["id"])
    fake = Busy()
    fake.blocked = ""
    portraits.bring_to_life(world.avatars, pal["id"], fake)
    still = portraits.check(world.avatars, pal["id"], fake)
    assert still["state"] == "running" and "could not be made" in still["note"] and fake.cancelled == []
    fake.blocked = busy
    stopped = portraits.check(world.avatars, pal["id"], fake)
    assert stopped["state"] == "error" and fake.cancelled == ["job2"]
    assert stopped["note"] == f"Stopped: NPC Forge needs the GPU to itself. {busy}"
    assert "forge_job" not in world.avatars.get(pal["id"]) and stopped["portrait"]["can_bring_to_life"]


# ---------------------------------------------------------------- a character's portrait

def test_a_character_is_redrawn_from_its_cards_picture_which_is_kept_to_go_back_to(world):
    vale = a_character(world)
    card_png = world.avatars.image_path(vale["id"]).read_bytes()
    first = portraits.summary(world.avatars, world.avatars.get(vale["id"]))
    assert first["from"] == "card" and first["card_picture"] is True
    fake = FakeForge()
    answer = painted(world, vale["id"], fake, source="card", description="", style="semi_real", setting="fantasy")
    assert fake.painted[0]["photo"] == card_png and answer["state"] == "ready"
    folder = world.avatars.root / vale["id"]
    assert (folder / portraits.CARD_PICTURE).read_bytes() == card_png
    assert world.avatars.image_path(vale["id"]).read_bytes() != card_png
    record = world.avatars.get(vale["id"])
    assert record["source"]["type"] == "character" and record["card"]["name"] == "Captain Vale"      # the card still speaks
    assert answer["portrait"]["card_picture"] is True and answer["portrait"]["from"] == "forge"

    painted_again = painted(world, vale["id"], FakeForge(), source="card")                   # from the card's picture again
    assert painted_again["state"] == "ready"
    assert (folder / portraits.CARD_PICTURE).read_bytes() == card_png                       # never the Forge one

    back = portraits.stop_using(world.avatars, vale["id"])
    assert back["from"] == "card" and world.avatars.image_path(vale["id"]).read_bytes() == card_png
    assert world.avatars.get(vale["id"])["age_check"]["ok"] is True


def test_a_json_card_is_painted_from_words_and_goes_back_to_its_initial(world):
    data = json.dumps(V2).encode("utf-8")
    vale = characters.import_card(world.avatars, "vale.json", data)
    assert portraits.summary(world.avatars, world.avatars.get(vale["id"]))["card_picture"] is False
    with pytest.raises(portraits.PortraitError, match="no picture"):
        portraits.start(world.avatars, vale["id"], FakeForge(), source="card")
    assert painted(world, vale["id"])["state"] == "ready"
    assert portraits.stop_using(world.avatars, vale["id"])["from"] == "none"
    assert world.avatars.image_path(vale["id"]) is None


def test_the_description_uses_the_cards_looks_without_locking_inferred_gender(world):
    card = {**V2["data"], "description": "{{char}} commands the airship Kestrel. She has cropped silver hair and grey eyes.\n"
                                         "She wears a long blue coat with brass buttons. She hates storms.",
            "personality": "Gruff. Her face is scarred."}
    record = {"id": "chr-x", "name": "Vale", "kind": "character", "card": {**card, "kind": "character"}}
    words = portraits.looks_from_card(record)
    assert words == ("She has cropped silver hair and grey eyes. She wears a long blue coat with brass buttons. "
                     "Her face is scarred.")
    assert portraits.summary(world.avatars, record)["gender"] == ""


def test_the_model_can_write_the_description_and_its_words_are_checked(world):
    class Model:
        def __init__(self, answer):
            self.answer, self.seen = answer, []

        def chat(self, messages, stream=False, **kw):
            self.seen.append(messages)
            return self.answer
    record = world.avatars.get(a_character(world)["id"])
    model = Model("<think>hmm</think>Cropped silver hair, grey eyes, a long blue coat.")
    assert portraits.describe_with_model(record, model) == "Cropped silver hair, grey eyes, a long blue coat."
    assert "Captain Vale commands the airship Kestrel" in model.seen[0][1]["content"]       # names filled in
    assert "SECRET" not in json.dumps(model.seen)                                              # creator notes never sent
    assert portraits.describe_with_model(record, Model("NONE")) == ""

    class Broken:
        def chat(self, *a, **k):
            raise ConnectionError("refused")
    with pytest.raises(portraits.PortraitError, match="did not answer"):
        portraits.describe_with_model(record, Broken())


# ---------------------------------------------------------------- the living portrait

def test_a_painted_pal_can_be_brought_to_life_and_its_frames_come_through_the_forge_link(world):
    pal = a_pal(world)
    services = companion.get_services()
    services.living.sync_inline = True
    with pytest.raises(portraits.PortraitError, match="first"):
        portraits.bring_to_life(world.avatars, pal["id"], FakeForge())
    painted(world, pal["id"], living=services.living)
    fake = FakeForge()
    started = portraits.bring_to_life(world.avatars, pal["id"], fake, services.living)
    assert fake.lived == [(NPC, "")] and started["job"]["kind"] == "living" and started["can_bring_to_life"] is False
    make_living(world.forge, npc_id=NPC)
    fake.state = "done"
    done = portraits.check(world.avatars, pal["id"], fake, FakeChecker(ok=True), services.living)
    assert done["state"] == "ready" and done["portrait"]["living"] is True and done["note"] == ""
    assert services.living.status(pal["id"])["frames"] == ["blink", "happy", "talking"]
    portraits.stop_using(world.avatars, pal["id"], services.living)                           # its frames go with it
    assert services.living.status(pal["id"])["available"] is False


def test_a_job_lm_studio_took_the_gpu_from_is_carried_on_once_after_unloading_it(world):
    """A chat reply or an age check can make LM Studio load its model in the middle of an 80-minute living portrait."""
    class Taken(FakeForge):
        retried = []

        def job(self, job_id):
            return {"state": "error", "note": "Another program is using the GPU.", "code": "gpu_busy", "holders": LM_STUDIO}

        def retry_gpu_job(self, job_id):
            self.retried.append(job_id)
    pal = a_pal(world)
    painted(world, pal["id"])
    fake = Taken()
    portraits.bring_to_life(world.avatars, pal["id"], fake)
    again = portraits.check(world.avatars, pal["id"], fake)
    assert again["state"] == "running" and again["note"] == portraits.RETRYING
    assert fake.retried == ["job2"]
    assert world.avatars.get(pal["id"])["forge_job"]["retried"] is True
    failed = portraits.check(world.avatars, pal["id"], fake)                       # taken again: said, not retried again
    assert failed["state"] == "error" and "GPU" in failed["note"] and fake.retried == ["job2"]
    assert "forge_job" not in world.avatars.get(pal["id"])


def test_a_job_another_program_took_the_gpu_from_is_not_retried(world):
    class Other(FakeForge):
        def job(self, job_id):
            return {"state": "error", "note": "Blender holds the GPU.", "code": "gpu_busy",
                    "holders": [{"name": "blender.exe", "mb": 9000}]}

        def retry_gpu_job(self, job_id):
            raise forge.ForgeError("Blender holds the GPU.")
    pal = a_pal(world)
    painted(world, pal["id"])
    fake = Other()
    portraits.bring_to_life(world.avatars, pal["id"], fake)
    assert portraits.check(world.avatars, pal["id"], fake)["state"] == "error" and fake.cancelled == []


def test_a_finished_living_portrait_says_its_frames_are_still_being_copied(world, monkeypatch):
    """Live, the frames took minutes to come in after NPC Forge finished (an age check per frame, and LM Studio loading the
    model first), while the dialog said "The new portrait is in."."""
    pal = a_pal(world)
    services = companion.get_services()
    painted(world, pal["id"], living=services.living)
    fake = FakeForge()
    portraits.bring_to_life(world.avatars, pal["id"], fake, services.living)
    started = []
    monkeypatch.setattr(services.living, "sync_async", lambda avatar_id: started.append(avatar_id) or True)
    fake.state = "done"
    done = portraits.check(world.avatars, pal["id"], fake, FakeChecker(ok=True), services.living)
    assert started == [pal["id"]] and done["state"] == "ready" and done["note"] == portraits.COPYING
    assert done["portrait"]["living"] is False


def test_core_withholds_the_flirty_face_in_kids_mode_and_ordinary_chat(world):
    from core import kids
    kids.enable(True)
    pal = pals.create(world.avatars, "Nova", "sunny", "", engine="kokoro", kids=True)
    services = companion.get_services()
    services.living.sync_inline = True
    painted(world, pal["id"], living=services.living)
    make_living(world.forge, npc_id=NPC, frames=("blink", "talking", "happy", "flirty"))
    services.living.sync(pal["id"])
    assert services.living.status(pal["id"])["frames"] == ["blink", "happy", "talking"]
    assert services.living.frame_path(pal["id"], "flirty") is None
    pals.update(world.avatars, pal["id"], kids=False)
    assert services.living.frame_path(pal["id"], "flirty") is None
    assert (world.avatars.root / pal["id"] / "living" / "flirty.png").is_file()


def test_the_engines_chosen_for_a_portrait_are_kept_and_the_living_portrait_uses_the_redraw_one(world):
    pal = a_pal(world)
    painted(world, pal["id"], image_model="mage", edit_model="qwen2511")
    shown = portraits.summary(world.avatars, world.avatars.get(pal["id"]))
    assert shown["image_model"] == "mage" and shown["edit_model"] == "qwen2511"          # the next portrait starts from them
    fake = FakeForge()
    portraits.bring_to_life(world.avatars, pal["id"], fake)
    assert fake.lived == [(NPC, "qwen2511")]
    portraits.cancel(world.avatars, pal["id"], fake)
    portraits.bring_to_life(world.avatars, pal["id"], fake, edit_model="mage")           # chosen again, and remembered
    assert fake.lived[-1] == (NPC, "mage") and world.avatars.get(pal["id"])["forge"]["edit_model"] == "mage"
    with pytest.raises(portraits.PortraitError, match="text"):
        portraits.start(world.avatars, a_pal(world, "Sky")["id"], FakeForge(), source="description",
                        description="Red hair.", image_model=3)


def test_a_pal_without_a_forge_portrait_says_where_its_living_portrait_comes_from(world):
    pal = a_pal(world)
    services = companion.get_services()
    services.living.sync(pal["id"])
    assert "Bring it to life" in services.living.status(pal["id"])["reason"]


# ---------------------------------------------------------------- the routes

@pytest.fixture
def client(world, monkeypatch):
    import core.routes.companions as routes
    fake = FakeForge()
    monkeypatch.setattr(routes, "_forge", lambda: fake)
    app = create_app("companions", "companion").test_client()
    app.fake = fake
    return app


def test_the_portrait_routes_paint_wait_bring_in_and_go_back(client, world):
    pal = a_pal(world)
    url = f"/api/portraits/{pal['id']}"
    assert client.get(url).get_json()["from"] == "none"
    assert client.post(url, json={"from": "description", "description": "Red hair.", "style": "anime",
                                  "setting": "modern", "junk": 1}).status_code == 400
    started = client.post(url, json={"from": "description", "description": "Red hair.", "style": "anime", "setting": "modern",
                                     "image_model": "mage", "edit_model": ""})
    assert started.status_code == 202 and started.get_json()["job"]["kind"] == "portrait"
    assert client.fake.painted[0]["image_model"] == "mage" and client.fake.painted[0]["edit_model"] == ""
    assert client.get(url + "/job").get_json()["state"] == "running"
    make_forge(world.forge, npc_id=NPC, first="Pip")
    client.fake.state = "done"
    assert client.get(url + "/job").get_json()["state"] == "ready"
    shown = client.get(f"/api/avatars/{pal['id']}/portrait")
    assert shown.mimetype == "image/png" and shown.data == (world.forge / NPC / "portrait.png").read_bytes()
    shown.close()                                                                       # Windows: let go of the file
    assert client.post(url + "/living", json={"edit_model": "mage", "junk": 1}).status_code == 400
    assert client.post(url + "/living", json={"edit_model": "mage"}).status_code == 202
    assert client.fake.lived == [(NPC, "mage")]
    assert client.delete(url + "/job").get_json()["job"] is None
    assert client.delete(url + "/forge").get_json()["from"] == "none"
    assert client.get(f"/api/avatars/{pal['id']}/portrait").mimetype == "image/svg+xml"         # its initial again
    assert client.get("/api/portraits/mara-bell-47ab97").status_code == 404


def test_the_describe_route_is_for_characters_and_uses_the_chat_model(client, world, monkeypatch):
    import core.routes.companions as routes
    monkeypatch.setattr(routes, "_portrait_llm", lambda: type("M", (), {"chat": lambda self, m, **k: "Silver hair."})())
    vale = a_character(world)
    assert client.post(f"/api/portraits/{vale['id']}/describe").get_json() == {"description": "Silver hair."}
    assert client.post(f"/api/portraits/{a_pal(world)['id']}/describe").status_code == 400


def test_the_portrait_routes_refuse_other_websites(client, world):
    pal = a_pal(world)
    cross = {"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"}
    url = f"/api/portraits/{pal['id']}"
    assert client.post(url, json={"from": "description", "description": "Red hair."}, headers=cross).status_code == 403
    for method, path in (("post", "/living"), ("delete", "/job"), ("delete", "/forge"), ("post", "/describe")):
        assert getattr(client, method)(url + path, headers=cross).status_code == 403
    assert client.fake.painted == []


# ---------------------------------------------------------------- a sample of a voice

def test_a_voice_sample_speaks_in_the_chosen_voice_and_only_a_listed_one(world, monkeypatch):
    import core.voice_layer
    spoken = []
    monkeypatch.setattr(core.voice_layer, "get_tts_audio",
                        lambda text, person_voice=None: spoken.append((text, person_voice)) or "/audio/x.wav")
    voice = pals.voices()["options"][0]["id"]
    app = create_app("engagement").test_client()
    answer = app.post("/api/voice/tts", json={"text": "Hi, I'm Pip.", "voice": voice})
    assert answer.get_json() == {"audio_url": "/audio/x.wav"} and spoken == [("Hi, I'm Pip.", voice)]
    assert app.post("/api/voice/tts", json={"text": "Hi.", "voice": "../../etc"}).status_code == 400
    assert app.post("/api/voice/tts", json={"text": "Hi."}).status_code == 200 and spoken[-1] == ("Hi.", None)


# ---------------------------------------------------------------- the age check, later, for a portrait that came in unchecked
SILENT = {"ok": None, "error": "The vision model could not be reached."}


def unchecked_pal(world):
    pal = a_pal(world)
    assert painted(world, pal["id"], checker=FakeChecker(ok=None, age=None))["state"] == "ready"   # the model was not running
    assert portraits.summary(world.avatars, world.avatars.get(pal["id"]))["age_checked"] == "unchecked"
    return pal


def test_an_unchecked_portrait_that_reads_as_under_age_is_taken_down_and_stays_down(world):
    pal = unchecked_pal(world)
    assert portraits.recheck(world.avatars, pal["id"], FakeChecker(ok=False, age=11)) == "removed"
    shown = portraits.summary(world.avatars, world.avatars.get(pal["id"]))
    assert world.avatars.image_path(pal["id"]) is None and shown["from"] == "none" and shown["age_checked"] == "none"
    assert shown["removed"]["apparent_age"] == 11 and shown["removed"]["from"] == "forge"
    pals.update(world.avatars, pal["id"], name="Pippa")                                     # rebuilt from pal.yaml
    assert portraits.summary(world.avatars, world.avatars.get(pal["id"]))["removed"]["apparent_age"] == 11
    assert portraits.recheck(world.avatars, pal["id"], FakeChecker(ok=False)) == "none"     # nothing left to check
    painted(world, pal["id"])                                                               # a new one, checked
    assert portraits.summary(world.avatars, world.avatars.get(pal["id"]))["removed"] is None


def test_an_unchecked_portrait_that_passes_is_marked_checked(world):
    pal = unchecked_pal(world)
    checker = FakeChecker(ok=True, age=31)
    assert portraits.recheck(world.avatars, pal["id"], checker) == "passed"
    assert world.avatars.get(pal["id"])["age_check"] == {"ok": True, "apparent_age": 31, "model": "fake"}
    assert portraits.summary(world.avatars, world.avatars.get(pal["id"]))["age_checked"] == "passed"
    assert portraits.recheck(world.avatars, pal["id"], checker) == "none" and checker.calls == 1


def test_a_characters_forge_portrait_gives_way_to_its_checked_card_picture(world):
    vale = a_character(world)
    card_png = world.avatars.image_path(vale["id"]).read_bytes()
    painted(world, vale["id"], source="card", checker=FakeChecker(ok=None, age=None))
    assert portraits.recheck(world.avatars, vale["id"], FakeChecker(ok=False, age=12)) == "removed"
    record = world.avatars.get(vale["id"])
    assert world.avatars.image_path(vale["id"]).read_bytes() == card_png and record["portrait_from"] == "card"
    assert record["age_check"]["ok"] is True and record["portrait_removed"]["from"] == "forge"


def test_an_unchecked_card_picture_that_reads_as_under_age_is_not_kept_to_go_back_to(world):
    vale = characters.import_card(world.avatars, "vale.png", png(text_chunk("chara", V2)))       # no check could run
    assert portraits.recheck(world.avatars, vale["id"], FakeChecker(ok=False, age=14)) == "removed"
    shown = portraits.summary(world.avatars, world.avatars.get(vale["id"]))
    assert shown["from"] == "none" and shown["card_picture"] is False and shown["removed"]["from"] == "card"
    with pytest.raises(portraits.PortraitError, match="no picture"):
        portraits.start(world.avatars, vale["id"], FakeForge(), source="card")


def test_no_answer_leaves_it_unchecked_and_it_is_tried_again_later_not_at_once(world):
    pal = unchecked_pal(world)
    silent = FakeChecker()
    silent.result = dict(SILENT)
    assert portraits.recheck_soon(world.avatars, silent) == 1 and silent.calls == 1
    assert portraits.summary(world.avatars, world.avatars.get(pal["id"]))["age_checked"] == "unchecked"
    assert portraits.recheck_soon(world.avatars, silent) == 0 and silent.calls == 1        # not straight away
    for key in portraits._no_answer:
        portraits._no_answer[key] -= portraits.RECHECK_AFTER
    assert portraits.recheck_soon(world.avatars, FakeChecker(ok=True)) == 1                 # ten minutes on
    assert portraits.summary(world.avatars, world.avatars.get(pal["id"]))["age_checked"] == "passed"


def test_when_the_model_gives_no_answer_the_rest_wait_too(world):
    first, second = unchecked_pal(world), unchecked_pal(world)
    silent = FakeChecker()
    silent.result = dict(SILENT)
    assert portraits.recheck_soon(world.avatars, silent) == 2 and silent.calls == 1
    assert portraits.recheck_soon(world.avatars, silent) == 0
    assert {portraits.summary(world.avatars, world.avatars.get(p["id"]))["age_checked"] for p in (first, second)} == {"unchecked"}


def test_a_portrait_changed_while_the_model_looked_is_left_for_the_next_check(world):
    pal = unchecked_pal(world)

    class Swapping(FakeChecker):
        def check(self, png):
            (world.avatars.root / pal["id"] / "portrait.png").write_bytes(b"\x89PNG another picture")
            return super().check(png)
    assert portraits.recheck(world.avatars, pal["id"], Swapping(ok=False, age=10)) == "none"
    assert world.avatars.image_path(pal["id"]) is not None


def test_home_opening_and_the_settings_start_the_later_check(client, world):
    pal = unchecked_pal(world)
    world.checker.result = {"ok": False, "apparent_age": 11, "model": "fake", "error": None}
    assert client.get("/api/people").status_code == 200
    assert world.avatars.image_path(pal["id"]) is None
    removed = client.get(f"/api/portraits/{pal['id']}").get_json()["removed"]
    assert removed["apparent_age"] == 11 and removed["from"] == "forge"

    other = unchecked_pal(world)
    world.checker.result = {"ok": True, "apparent_age": 30, "model": "fake", "error": None}
    assert client.post(f"/api/companions/{other['id']}/open").status_code == 200
    assert world.avatars.get(other["id"])["age_check"]["ok"] is True                        # opening them checked it

    third = unchecked_pal(world)
    assert client.get(f"/api/portraits/{third['id']}").get_json()["age_checked"] == "passed"   # the settings check too


class Readings(FakeChecker):
    """Answers with each of `oks` in turn (True adult, False under age, None no answer)."""

    def __init__(self, *oks):
        super().__init__()
        self.oks = list(oks)

    def check(self, png):
        self.calls += 1
        ok = self.oks.pop(0)
        return {"ok": ok, "apparent_age": None if ok is None else 30 if ok else 12, "model": "fake", "error": None}


@pytest.mark.parametrize("oks, state, calls", [
    ((True,), "passed", 1),                    # one adult reading passes it
    ((False, False), "removed", 2),            # two under-age readings take it down
    ((False, True, True), "passed", 3),        # a split is settled by a third look
    ((False, True, False), "removed", 3),
    ((False, None), "no_answer", 2),           # no second answer: it stays unchecked, to be tried later
])
def test_a_portrait_in_use_is_only_taken_down_on_two_under_age_readings(world, oks, state, calls):
    pal = unchecked_pal(world)
    checker = Readings(*oks)
    assert portraits.recheck(world.avatars, pal["id"], checker) == state and checker.calls == calls
    record = world.avatars.get(pal["id"])
    assert record["age_check"]["ok"] is {"passed": True, "removed": False, "no_answer": None}[state]
    assert (world.avatars.image_path(pal["id"]) is None) is (state == "removed")
