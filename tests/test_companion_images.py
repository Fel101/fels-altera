"""Image backends, the age check, avatars imported from NPC Forge, and the picture service. No GPU, LLM or network needed."""
import json
import threading
import time
import types

import pytest

from core import age_check, avatars, imagegen, photos
from core.imagegen import ImageGenError

PNG = b"\x89PNG\r\n\x1a\n" + b"fake-image-bytes"


# ---------------------------------------------------------------- imagegen

def test_edit_graph_with_two_references():
    g = imagegen.edit_graph(imagegen.DEFAULT_MODELS, ["a.png [temp]", "b.png [temp]"], "P", 7, steps=25, cfg=4.5,
                            size=(768, 1024), negative="N")
    enc = g["4"]["inputs"]
    assert enc["images.image_1"] == ["21", 0] and enc["images.image_2"] == ["23", 0]
    assert g["20"]["inputs"]["image"] == "a.png [temp]" and g["22"]["inputs"]["image"] == "b.png [temp]"
    assert (enc["width"], enc["height"], enc["prompt"], enc["negative_prompt"]) == (768, 1024, "P", "N")
    assert g["5"]["inputs"]["seed"] == 7 and g["5"]["inputs"]["steps"] == 25 and g["5"]["inputs"]["cfg"] == 4.5
    assert imagegen.edit_graph(imagegen.DEFAULT_MODELS, "only.png", "P", 1)["4"]["inputs"]["images.image_1"] == ["21", 0]


def test_fill_workflow_keeps_types_and_does_not_mutate():
    template = {"1": {"inputs": {"seed": "{{seed}}", "text": "{{prompt}}", "note": "size {{width}}x{{height}}",
                                 "image": "{{reference}}", "list": ["{{steps}}", "plain", 3]}}}
    out = imagegen.fill_workflow(template, {"seed": 5, "prompt": "hello", "width": 768, "height": 1024, "reference": "r.png",
                                            "steps": 30})
    inputs = out["1"]["inputs"]
    assert inputs["seed"] == 5 and isinstance(inputs["seed"], int)
    assert inputs["text"] == "hello" and inputs["note"] == "size 768x1024" and inputs["list"] == [30, "plain", 3]
    assert template["1"]["inputs"]["seed"] == "{{seed}}"


def test_fill_workflow_rejects_unknown_placeholders():
    with pytest.raises(ImageGenError, match=r"\{\{bogus\}\}"):
        imagegen.fill_workflow({"1": {"inputs": {"x": "{{bogus}}"}}}, {"seed": 1})


class FakeComfy:
    def __init__(self, result=PNG):
        self.uploads, self.graphs, self.result = [], [], result
        self.models = imagegen.DEFAULT_MODELS

    def upload_image(self, png, name, **kw):
        self.uploads.append((name, png))
        return f"altera/{name} [temp]"

    def run(self, graph, timeout=900):
        self.graphs.append(graph)
        return self.result

    # what WorkflowBackend asks of ComfyUI: it watches the picture (a workflow that is not a big model is never flushed)
    def get_json(self, path, timeout=30):
        if path.startswith("/models/"):
            return []
        if path == "/system_stats":
            return {"devices": [{"vram_total": 32 << 30, "vram_free": 20 << 30}], "system": {"ram_free": 45 << 30}}
        if path.startswith("/history/"):
            return {path.split("/")[2]: {"status": {"completed": True, "status_str": "success"},
                                         "outputs": {"9": {"images": [{"filename": "a.png"}]}}}}
        raise AssertionError(path)

    def submit(self, graph):
        self.graphs.append(graph)
        return "job-1"

    def fetch_image(self, filename, subfolder="", image_type="temp"):
        return self.result


def test_mage_flow_backend_uploads_every_reference():
    comfy = FakeComfy()
    out = imagegen.MageFlowBackend(comfy, cfg=3.0).render([b"p", b"b"], "prompt", "neg", 9, (512, 704), 20)
    assert out == PNG and len(comfy.uploads) == 2
    g = comfy.graphs[0]
    assert g["4"]["inputs"]["prompt"] == "prompt" and g["5"]["inputs"]["cfg"] == 3.0 and g["4"]["inputs"]["width"] == 512


def test_workflow_backend_fills_placeholders(tmp_path, monkeypatch):
    monkeypatch.setattr(imagegen.time, "sleep", lambda seconds: None)          # the watch polls every 2 s; the fake answers at once
    wf = tmp_path / "wf.json"
    wf.write_text(json.dumps({"3": {"inputs": {"seed": "{{seed}}", "steps": "{{steps}}"}},
                              "6": {"inputs": {"text": "{{prompt}}", "neg": "{{negative}}"}},
                              "10": {"inputs": {"image": "{{reference}}", "image2": "{{reference_2}}",
                                                "w": "{{width}}", "h": "{{height}}"}}}), "utf-8")
    comfy = FakeComfy()
    backend = imagegen.WorkflowBackend(comfy, wf)
    backend.render([b"only-one"], "P", "N", 11, (640, 960), 18)
    g = comfy.graphs[0]
    assert g["3"]["inputs"] == {"seed": 11, "steps": 18} and g["6"]["inputs"] == {"text": "P", "neg": "N"}
    assert g["10"]["inputs"]["image"] == g["10"]["inputs"]["image2"]            # one reference fills both slots
    assert g["10"]["inputs"]["w"] == 640 and g["10"]["inputs"]["h"] == 960
    with pytest.raises(ImageGenError, match="Cannot read the workflow"):
        imagegen.WorkflowBackend(comfy, tmp_path / "missing.json")


def test_backend_factory_uses_the_workflow_only_for_levels_that_name_one(tmp_path):
    (tmp_path / "wf.json").write_text("{}", "utf-8")
    cfg = {"imagegen": {"comfy_url": "http://127.0.0.1:1", "workflow_fallback": False, "workflows": {"explicit": "wf.json", "suggestive": ""}}}
    pick = imagegen.backend_factory(cfg, tmp_path, comfy=FakeComfy())
    assert isinstance(pick("off"), imagegen.MageFlowBackend)
    assert isinstance(pick("suggestive"), imagegen.MageFlowBackend)
    assert isinstance(pick("explicit"), imagegen.WorkflowBackend) and pick("explicit") is pick("explicit")


# ---------------------------------------------------------------- age check

@pytest.mark.parametrize("reply, expected", [
    ('{"apparent_age": 28, "adult": true}', (28, True)),
    ('```json\n{"apparent_age": 24.6, "adult": true}\n```', (24, True)),
    ('{"apparent_age": 17.9, "adult": true}', (17, True)),
    ('{"apparent_age": 1e999, "adult": true}', (None, True)),
    ('{"apparent_age": true, "adult": true}', (None, True)),
    ('{"apparent_age": 1000, "adult": true}', (None, True)),
    ('<think>She might be {"apparent_age": 15}</think>{"apparent_age": 30, "adult": true}', (30, True)),
    ('Sure. Here: {"apparent_age": 19, "adult": false} Hope that helps', (19, False)),
    ('{"adult": false}', (None, False)),
    ("I cannot help with that.", (None, None)),
    ('{"apparent_age": "unknown"}', (None, None)),
])
def test_parse_reply(reply, expected):
    assert age_check.parse_reply(reply) == expected


def test_judge():
    assert age_check.judge(27, True) is True
    assert age_check.judge(21, True) is True
    assert age_check.judge(18, True) is True                                                 # the legal floor
    assert age_check.judge(17, True) is False
    assert age_check.judge(30, False) is False
    assert age_check.judge(None, False) is False
    assert age_check.judge(None, None) is None
    assert age_check.judge(None, True) is None                     # "adult" alone, with no age, proves nothing
    assert age_check.judge(*age_check.parse_reply('{"apparent_age": 17.9, "adult": true}')) is False


class FakeChatClient:
    def __init__(self, content=None, reasoning=None, error=None):
        self.calls = []
        self.content, self.reasoning, self.error = content, reasoning, error
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        message = types.SimpleNamespace(content=self.content, reasoning_content=self.reasoning)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])


@pytest.fixture
def llm_config():
    from core.llm import LLMClient, reset_llm_instance
    cfg = {"llm": {"base_url": "http://lm/v1", "api_key": "k", "model": "chat-model"}}
    LLMClient.refresh_from_config(cfg)
    yield cfg
    reset_llm_instance()


def _checker(cfg, client):
    seen = {}

    def factory(base_url, api_key):
        seen.update(base_url=base_url, api_key=api_key)
        return client

    return age_check.AgeCheck(cfg, client_factory=factory), seen


def test_age_check_sends_the_image_to_the_chat_model(llm_config):
    client = FakeChatClient('{"apparent_age": 29, "adult": true}')
    checker, seen = _checker(llm_config, client)
    result = checker.check(PNG)
    assert result == {"ok": True, "apparent_age": 29, "model": "chat-model", "error": None}
    call = client.calls[0]
    assert call["model"] == "chat-model" and call["temperature"] < 0.5 and call["max_tokens"] >= 4096
    assert seen["base_url"] == "http://lm/v1"
    parts = call["messages"][0]["content"]
    assert parts[1]["type"] == "image_url" and parts[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_age_check_model_can_be_overridden(llm_config):
    llm_config["imagegen"] = {"age_check": {"model": "vision-model"}}
    checker, _ = _checker(llm_config, FakeChatClient('{"apparent_age": 30, "adult": true}'))
    assert checker.check(PNG)["model"] == "vision-model"


def test_age_check_fails_closed_on_errors_and_garbage(llm_config):
    checker, _ = _checker(llm_config, FakeChatClient(error=RuntimeError("model has no vision")))
    result = checker.check(PNG)
    assert result["ok"] is None and "vision" in result["error"]
    checker, _ = _checker(llm_config, FakeChatClient("I'd rather not say."))
    assert checker.check(PNG)["ok"] is None
    checker, _ = _checker(llm_config, FakeChatClient('{"apparent_age": 17, "adult": false}'))
    result = checker.check(PNG)
    assert result["ok"] is False and result["apparent_age"] == 17


def test_age_check_reads_reasoning_content_when_content_is_empty(llm_config):
    checker, _ = _checker(llm_config, FakeChatClient(content="", reasoning='{"apparent_age": 26, "adult": true}'))
    assert checker.check(PNG)["ok"] is True


@pytest.mark.parametrize('reply, count, expected', [
    ('{"person_count":1,"youngest_age":25,"all_adults":true,"certain":true}', 1, True),
    ('{"person_count":2,"youngest_age":18,"all_adults":true,"certain":true}', 2, True),
    ('{"person_count":2,"youngest_age":17,"all_adults":true,"certain":true}', 2, False),
    ('{"person_count":2,"youngest_age":30,"all_adults":false,"certain":true}', 2, False),
    ('{"person_count":3,"youngest_age":30,"all_adults":true,"certain":true}', 2, False),
    ('{"person_count":1,"youngest_age":30,"all_adults":true,"certain":false}', 1, None),
    ('{"person_count":1,"youngest_age":30.0,"all_adults":true,"certain":true}', 1, None),
    ('{"person_count":true,"youngest_age":30,"all_adults":true,"certain":true}', 1, None),
    ('{"person_count":1,"youngest_age":30,"all_adults":true}', 1, None),
    ('{"person_count":1,"youngest_age":30,"all_adults":true,"certain":true,"certain":true}', 1, None),
    ('{"person_count":1,"youngest_age":30,"all_adults":true,"certain":true} {"person_count":1}', 1, None),
])
def test_multi_subject_age_check_requires_exact_certain_unambiguous_verdict(llm_config, reply, count, expected):
    client = FakeChatClient(reply)
    checker, _ = _checker(llm_config, client)
    assert checker.check_subjects(PNG, count)['ok'] is expected
    assert 'Count all visible people' in client.calls[0]['messages'][0]['content'][0]['text']


# ---------------------------------------------------------------- avatars (imported from an NPC Forge library)

def make_forge(root, npc_id="mara-bell-47ab97", age=34, portrait=True, body=True, bio="A florist.", first="Mara", freeform=False):
    d = root / npc_id
    (d / "sheet").mkdir(parents=True)
    profile = {"first_name": first, "last_name": "Bell", "gender": "female", "heritage": "European", "occupation": "florist",
               "bio": bio, "personality": ["warm"], "hobbies": ["gardening"]}
    if age is not None:
        profile["age"] = age
    npc = {"id": npc_id, "style": "photoreal", "freeform": freeform, "profile": profile, "created": "2026-09-19T10:00:00",
           "appearance": {"build": "sturdy", "skin_tone": "fair", "hair": "long brown hair", "eyes": "hazel",
                          "outfit": "an apron", "accessory": "a knife", "distinguishing": "freckles"}}
    (d / "npc.json").write_text(json.dumps(npc), "utf-8")
    if portrait:
        (d / "portrait.png").write_bytes(PNG + b"portrait")
    if body:
        (d / "sheet" / "front.png").write_bytes(PNG + b"body")
    (d / "thumb.png").write_bytes(PNG + b"thumb")
    return d


STUDIO_ITEMS = [
    {"id": "s-0123abcd", "scene": "in a cafe with a latte", "label": "Cafe", "level": "off", "status": "ready", "keep": True},
    {"id": "s-1123abcd", "scene": "wearing a silk robe by a bedroom window", "label": "Silk robe", "level": "suggestive",
     "status": "ready", "keep": True},
    {"id": "s-2123abcd", "scene": "on a beach at sunset", "label": "Beach", "level": "romantic", "status": "ready", "keep": False},
    {"id": "s-3123abcd", "scene": "walking in a park", "label": "Park", "level": "off", "status": "failed", "keep": True},
    {"id": "../evil", "scene": "hostile id", "label": "x", "level": "off", "status": "ready", "keep": True},
]


def make_studio(forge_root, npc_id="mara-bell-47ab97", items=None, notes=None, body=None):
    """What NPC Forge's studio leaves next to a character: adult/items.json, adult/studio.json, adult/img/<item>.png."""
    d = forge_root / npc_id / "adult"
    (d / "img").mkdir(parents=True, exist_ok=True)
    items = STUDIO_ITEMS if items is None else items
    for it in items:
        if it["status"] == "ready" and it["id"].startswith("s-"):
            (d / "img" / f"{it['id']}.png").write_bytes(PNG + it["id"].encode())
    (d / "items.json").write_text(json.dumps({"items": items}), "utf-8")
    if notes is not None or body:
        (d / "studio.json").write_text(json.dumps({"notes": notes or "", **(body or {})}), "utf-8")
    return d


def snapshot(root):
    return sorted((str(p.relative_to(root)), p.stat().st_size, p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file())


@pytest.fixture
def forge(tmp_path):
    root = tmp_path / "forge"
    root.mkdir()
    return root


@pytest.fixture
def store(tmp_path, forge):
    return avatars.AvatarStore(tmp_path / "avatars", forge)


class FakeChecker:
    def __init__(self, ok=True, age=28, error=None):
        self.result = {"ok": ok, "apparent_age": age, "model": "fake", "error": error}
        self.calls = 0

    def check(self, png):
        self.calls += 1
        return dict(self.result)


def test_import_copies_images_builds_the_card_and_never_writes_to_forge(store, forge):
    make_forge(forge)
    before = snapshot(forge)
    result = store.import_from_forge("mara-bell-47ab97", checker=FakeChecker())
    assert snapshot(forge) == before
    assert result["name"] == "Mara" and result["age"] == 34 and result["active"] and result["has_body"] and result["age_checked"]
    record = store.get("mara-bell-47ab97")
    card = record["card"]
    assert card["look"] == "sturdy build, fair skin, long brown hair, hazel eyes, freckles"
    assert "apron" not in json.dumps(card) and "knife" not in json.dumps(card)          # outfit and accessory stay behind
    assert store.refs("mara-bell-47ab97") == [PNG + b"portrait", PNG + b"body"]
    assert store.refs("mara-bell-47ab97", use_body=False) == [PNG + b"portrait"]
    assert store.active_id() == "mara-bell-47ab97"


def test_forge_rolled_age_stays_labelled_until_the_user_overrides_it(store, forge):
    directory = make_forge(forge, freeform=True)
    npc_id = directory.name
    npc_path = directory / "npc.json"
    npc = json.loads(npc_path.read_bytes())
    npc["profile"]["age_source"] = "rolled"
    npc_path.write_text(json.dumps(npc), encoding="utf-8")
    assert store.list_forge()[0]["age_source"] == "rolled"
    assert store.import_from_forge(npc_id)["age_source"] == "rolled"
    assert "age_source" not in store.import_from_forge(npc_id, age=35)
    store.refresh_card(npc_id)
    assert "age_source" not in store.public(store.get(npc_id))


def test_import_without_a_body_view_and_reimport_updates(store, forge):
    d = make_forge(forge, body=False)
    store.import_from_forge("mara-bell-47ab97")
    assert store.get("mara-bell-47ab97")["has_body"] is False and len(store.refs("mara-bell-47ab97")) == 1
    (d / "sheet" / "front.png").write_bytes(PNG + b"new-body")
    store.import_from_forge("mara-bell-47ab97")
    assert store.refs("mara-bell-47ab97")[1] == PNG + b"new-body"
    (d / "sheet" / "front.png").unlink()
    store.import_from_forge("mara-bell-47ab97")
    assert store.image_path("mara-bell-47ab97", "body") is None


def test_under_18_is_refused_and_18_is_accepted(store, forge):
    make_forge(forge, age=17)
    with pytest.raises(avatars.AvatarError, match="18 or older"):
        store.import_from_forge("mara-bell-47ab97")
    assert store.list() == []
    make_forge(forge, npc_id="adult-eighteen-333333", age=18)
    store.import_from_forge("adult-eighteen-333333")
    assert len(store.list()) == 1


def test_age_can_be_supplied_but_only_if_adult(store, forge):
    make_forge(forge, age=None, freeform=True)
    with pytest.raises(avatars.AvatarError, match="stated age"):
        store.import_from_forge("mara-bell-47ab97")
    with pytest.raises(avatars.AvatarError, match="18 or older"):
        store.import_from_forge("mara-bell-47ab97", age=17)
    with pytest.raises(avatars.AvatarError, match="must be a number"):
        store.import_from_forge("mara-bell-47ab97", age="old")
    assert store.import_from_forge("mara-bell-47ab97", age="30")["age"] == 30


def test_name_can_be_overridden_and_is_required(store, forge):
    make_forge(forge, first="")
    with pytest.raises(avatars.AvatarError, match="no name"):
        store.import_from_forge("mara-bell-47ab97")
    assert store.import_from_forge("mara-bell-47ab97", name="Vera")["name"] == "Vera"


def test_youth_or_real_person_text_in_the_card_is_refused(store, forge):
    make_forge(forge, bio="She loves being a schoolgirl at heart.")
    with pytest.raises(avatars.AvatarError, match="not allowed"):
        store.import_from_forge("mara-bell-47ab97")
    make_forge(forge, npc_id="vera-look-111111", bio="Looks like Emma Watson.")
    with pytest.raises(avatars.AvatarError, match="real person"):
        store.import_from_forge("vera-look-111111")


def test_missing_portrait_unknown_and_hostile_ids(store, forge):
    make_forge(forge, portrait=False)
    with pytest.raises(avatars.AvatarError, match="no portrait"):
        store.import_from_forge("mara-bell-47ab97")
    for bad in ("nope-nope-000000", "../forge", "..", "a/b", "", None, "UPPER-case", "x" * 90):
        with pytest.raises(avatars.AvatarError, match="not found"):
            store.import_from_forge(bad)
    assert store.forge_file("../forge", "thumb") is None and store.image_path("../x") is None and store.get("../x") is None


def test_portrait_that_reads_as_young_is_refused_and_unavailable_check_is_recorded(store, forge):
    make_forge(forge)
    with pytest.raises(avatars.AvatarError, match="looks younger"):
        store.import_from_forge("mara-bell-47ab97", checker=FakeChecker(ok=False, age=17))
    assert store.list() == []
    result = store.import_from_forge("mara-bell-47ab97", checker=FakeChecker(ok=None, age=None, error="no vision"))
    assert result["age_checked"] is False and result["active"]


def test_list_forge_marks_eligibility(store, forge):
    make_forge(forge, npc_id="ok-one-111111", age=40)
    make_forge(forge, npc_id="young-one-222222", age=17)
    make_forge(forge, npc_id="no-portrait-333333", portrait=False)
    make_forge(forge, npc_id="no-age-444444", age=None, freeform=True)
    (forge / "not-a-character").mkdir()
    rows = {r["id"]: r for r in store.list_forge()}
    assert set(rows) == {"ok-one-111111", "young-one-222222", "no-portrait-333333", "no-age-444444"}
    assert rows["ok-one-111111"]["eligible"] and not rows["ok-one-111111"]["imported"]
    assert not rows["young-one-222222"]["eligible"] and "18" in rows["young-one-222222"]["reason"]
    assert not rows["no-portrait-333333"]["eligible"]
    assert rows["no-age-444444"]["eligible"] and rows["no-age-444444"]["needs_age"]
    store.import_from_forge("ok-one-111111")
    assert {r["id"]: r for r in store.list_forge()}["ok-one-111111"]["imported"]


@pytest.mark.requires_addon
def test_forge_premade_lists_only_approved_ready_items_with_safe_ids(store, forge):
    make_forge(forge)
    assert store.forge_premade("mara-bell-47ab97") == []                     # no studio folder yet
    make_studio(forge)
    items = store.forge_premade("mara-bell-47ab97")
    assert [i["item_id"] for i in items] == ["s-0123abcd", "s-1123abcd"]     # not kept / failed / hostile ids are left out
    assert items[1] == {"item_id": "s-1123abcd", "scene": "wearing a silk robe by a bedroom window", "label": "Silk robe",
                        "level": "suggestive"}
    assert store.forge_premade_file("mara-bell-47ab97", "s-0123abcd").read_bytes() == PNG + b"s-0123abcd"
    for bad in ("../evil", "s-2123abcd", "s-zz", "", None):
        assert store.forge_premade_file("mara-bell-47ab97", bad) is None, bad
    assert store.forge_premade_file("../x", "s-0123abcd") is None
    assert {r["id"]: r for r in store.list_forge()}["mara-bell-47ab97"]["premade"] == 2


@pytest.mark.requires_addon
def test_studio_notes_travel_with_the_import_and_reach_the_prompt(store, forge):
    make_forge(forge)
    make_studio(forge, notes="athletic build, defined waist")
    store.import_from_forge("mara-bell-47ab97")
    card = store.get("mara-bell-47ab97")["card"]
    assert card["body_notes"] == "athletic build, defined waist"
    prompt = photos.build_prompt(card, "on a beach", "romantic")["positive"]
    assert "Body: athletic build, defined waist." in prompt
    assert "Body:" not in photos.build_prompt({**card, "body_notes": ""}, "on a beach", "romantic")["positive"]


@pytest.mark.requires_addon
def test_the_studios_body_options_travel_and_the_areolas_only_reach_explicit_prompts(store, forge):
    make_forge(forge)
    make_studio(forge, notes="defined waist", body={"body_text": "a full, rounded bust, teardrop-shaped; defined waist",
                                                    "body_text_explicit": "medium-sized rose areolas"})
    store.import_from_forge("mara-bell-47ab97")
    card = store.get("mara-bell-47ab97")["card"]
    assert card["body_notes"] == "a full, rounded bust, teardrop-shaped; defined waist"
    assert card["body_notes_explicit"] == "medium-sized rose areolas"
    assert "Body:" not in photos.build_prompt(card, "on a beach", "off")["positive"]
    for level in ("romantic", "suggestive"):
        prompt = photos.build_prompt(card, "on a beach", level)["positive"]
        assert "Body: a full, rounded bust, teardrop-shaped; defined waist." in prompt and "areolas" not in prompt, level
    explicit = photos.build_prompt(card, "on a beach", "explicit")["positive"]
    assert "Body: a full, rounded bust, teardrop-shaped; defined waist; medium-sized rose areolas." in explicit
    assert store.forge_studio_notes("mara-bell-47ab97") == card["body_notes"]
    # a studio that only wrote free notes still works, and a hostile body text is refused like any other card text
    make_studio(forge, notes="athletic build")
    assert store.forge_studio_body("mara-bell-47ab97") == {"text": "athletic build", "explicit": ""}
    make_studio(forge, notes="", body={"body_text": "a petite schoolgirl figure"})
    with pytest.raises(avatars.AvatarError, match="not allowed"):
        store.import_from_forge("mara-bell-47ab97")


@pytest.mark.requires_addon
def test_studio_notes_are_held_to_the_same_rules(store, forge):
    make_forge(forge)
    make_studio(forge, notes="a petite schoolgirl figure")
    with pytest.raises(avatars.AvatarError, match="not allowed"):
        store.import_from_forge("mara-bell-47ab97")


def test_activate_delete_and_missing_forge(store, forge, tmp_path):
    make_forge(forge, npc_id="one-one-111111")
    make_forge(forge, npc_id="two-two-222222")
    store.import_from_forge("one-one-111111")
    store.import_from_forge("two-two-222222")
    assert store.active_id() == "one-one-111111"                                  # the first import becomes active
    store.activate("two-two-222222")
    assert store.active()["id"] == "two-two-222222"
    with pytest.raises(avatars.AvatarError):
        store.activate("ghost-ghost-000000")
    assert store.delete("two-two-222222") is True and store.active_id() is None and store.delete("two-two-222222") is False
    assert avatars.AvatarStore(tmp_path / "x", tmp_path / "missing").list_forge() == []


# ---------------------------------------------------------------- pictures

class FakeBackend:
    name = "fake"

    def __init__(self, error=None):
        self.calls, self.error = [], error

    def render(self, refs, prompt, negative, seed, size, steps):
        self.calls.append({"refs": refs, "prompt": prompt, "negative": negative, "seed": seed, "size": size, "steps": steps})
        if self.error:
            raise self.error
        return PNG


@pytest.fixture
def world(tmp_path, forge, store):
    make_forge(forge)
    store.import_from_forge("mara-bell-47ab97")
    backend, checker = FakeBackend(), FakeChecker(ok=True, age=28)
    svc = photos.PhotoService(tmp_path / "photos", store, lambda level: backend, checker, sync=True)
    return types.SimpleNamespace(svc=svc, backend=backend, checker=checker, store=store, tmp=tmp_path)


@pytest.mark.requires_addon
def test_prompt_names_the_adult_and_the_youth_negative_is_always_present():
    card = {"age": 34, "gender": "female", "look": "long brown hair", "style": "photoreal"}
    p = photos.build_prompt(card, "on a balcony at sunset", "romantic", refs=2)
    assert p["positive"].startswith("Show the same woman from the reference images")
    assert "on a balcony at sunset" in p["positive"] and "an adult, 34 years old" in p["positive"]
    assert "long brown hair" in p["positive"] and "photorealistic" in p["positive"] and "flirty" in p["positive"]
    assert "Change the clothing completely: nothing the woman wears in the reference may appear" in p["positive"]
    assert "underage" in p["negative"] and "schoolgirl" in p["negative"]
    for level in ("off", "romantic", "suggestive"):
        assert "in ordinary everyday clothes for anything it does not mention" in photos.build_prompt(card, "x", level)["positive"]
    explicit = photos.build_prompt(card, "x", "explicit")["positive"]                 # nothing is worn unless the scene says so
    assert "whatever it does not say the woman wears, the woman is not wearing" in explicit and "everyday clothes" not in explicit
    one = photos.build_prompt({**card, "gender": ""}, "x", "off", refs=1)
    assert "the same person from the reference image " in one["positive"]


@pytest.mark.requires_addon
def test_suggestive_and_explicit_pictures_ask_for_the_whole_body(world):
    card = {"age": 34, "gender": "female", "look": "long brown hair", "style": "photoreal"}
    for level in ("off", "romantic"):
        assert "full-length" not in photos.build_prompt(card, "on a balcony", level)["positive"], level
    for level in ("suggestive", "explicit"):
        assert "full-length photograph" in photos.build_prompt(card, "in a robe", level)["positive"], level
        assert "full-length" not in photos.build_prompt(card, "a close-up portrait in a robe", level)["positive"], level
    world.svc.create("sitting in a cafe with a latte", level="off")
    world.svc.create("in a silk robe by a bedroom window", level="suggestive")
    world.svc.create("a close-up portrait in a silk robe", level="suggestive")
    assert [c["size"] for c in world.backend.calls] == [(768, 1024), (832, 1216), (768, 1024)]


def test_a_picture_is_generated_checked_and_stored(world):
    rec = world.svc.create("sitting in a cafe with a latte", level="off")
    assert rec["status"] == "ready" and rec["age_checked"] is True and rec["level"] == "off"
    assert rec["provenance"] == "imagined_scene" and world.svc.get(rec["id"])["provenance"] == "imagined_scene"
    assert "seed" not in rec and "prompt" not in rec
    call = world.backend.calls[0]
    assert len(call["refs"]) == 1 and call["size"] == (768, 1024) and call["steps"] == 30      # portrait only by default
    assert "sitting in a cafe with a latte" in call["prompt"]
    assert world.svc.image_path(rec["id"]).read_bytes() == PNG
    assert world.svc.get(rec["id"])["status"] == "ready"


def test_body_reference_is_opt_in_and_quality_is_configurable(world):
    svc = photos.PhotoService(world.tmp / "p2", world.store, lambda level: world.backend, world.checker, sync=True,
                              config={"imagegen": {"use_body_reference": True, "quality": "draft"}})
    svc.create("a scene", level="off")
    call = world.backend.calls[-1]
    assert len(call["refs"]) == 2 and call["size"] == (512, 704) and call["steps"] == 20
    assert "the first shows the woman's face and hair" in call["prompt"]
    for setting in ({}, {"imagegen": {"use_body_reference": False}}, {"imagegen": {"use_body_reference": "yes"}}):
        svc = photos.PhotoService(world.tmp / "p3", world.store, lambda level: world.backend, world.checker, sync=True, config=setting)
        svc.create("a scene", level="off")
        assert len(world.backend.calls[-1]["refs"]) == 1, setting                     # anything but a real true stays portrait-only


def test_the_lm_studio_model_is_freed_before_a_picture_is_generated(world, monkeypatch):
    from core import llm
    order = []
    monkeypatch.setattr(llm, "unload_models", lambda: order.append("unload") or True)
    real_render = world.backend.render
    world.backend.render = lambda *a, **kw: order.append("render") or real_render(*a, **kw)
    world.svc.create("sitting in a cafe with a latte", level="off")
    assert order == ["unload", "render"]                                   # freed before the backend is asked, not after


def test_unload_before_generating_can_be_turned_off(world, monkeypatch):
    from core import llm
    calls = []
    monkeypatch.setattr(llm, "unload_models", lambda: calls.append(1) or True)
    svc = photos.PhotoService(world.tmp / "p4", world.store, lambda level: world.backend, world.checker, sync=True,
                              config={"imagegen": {"unload_llm_before_generate": False}})
    svc.create("a scene", level="off")
    assert calls == []


def test_hard_rules_block_before_anything_is_generated(world):
    for scene in ("a 16 year old girl at school", "looks like Emma Watson", "she is unconscious"):
        with pytest.raises(photos.PhotoBlocked):
            world.svc.create(scene, level="explicit")
    assert world.backend.calls == [] and world.svc.list() == []


@pytest.mark.requires_addon
def test_level_gates_the_words_in_a_request(world):
    with pytest.raises(photos.PhotoBlocked, match='does not make pictures of "bikini"'):   # off: no hint
        world.svc.create("in a bikini", level="off")
    assert world.svc.create("in a bikini", level="romantic")["status"] == "ready"
    with pytest.raises(photos.PhotoBlocked, match="Suggestive"):
        world.svc.create("in lingerie", level="romantic")
    with pytest.raises(photos.PhotoBlocked, match="Explicit"):
        world.svc.create("completely nude", level="suggestive")


def test_a_blocked_picture_she_chose_is_recorded_not_raised(world):
    rec = world.svc.create("in lingerie", level="off", record_blocked=True, source="model")
    assert rec["status"] == "blocked" and "does not make pictures" in rec["reason"] and world.backend.calls == []
    assert world.svc.image_path(rec["id"]) is None


def test_an_empty_scene_picks_a_default_for_the_level(world):
    rec = world.svc.create("", level="off")
    assert rec["status"] == "ready" and rec["scene"]


def test_age_check_rejection_discards_the_picture(world):
    world.checker.result = {"ok": False, "apparent_age": 17, "model": "fake", "error": None}
    rec = world.svc.create("in a cafe", level="off")
    assert rec["status"] == "blocked" and "17" in rec["reason"]
    assert world.svc.image_path(rec["id"]) is None and not list(world.svc.root.glob("*.png"))


@pytest.mark.requires_addon
def test_age_check_unavailable_blocks_suggestive_and_up_but_not_below(world):
    world.checker.result = {"ok": None, "apparent_age": None, "model": "fake", "error": "no vision model"}
    assert world.svc.create("in a cafe", level="off")["age_checked"] is False
    assert world.svc.create("in a cafe", level="romantic")["status"] == "ready"
    for level in ("suggestive", "explicit"):
        rec = world.svc.create("in a cafe", level=level)
        assert rec["status"] == "blocked" and "no vision model" in rec["reason"], level
        assert world.svc.image_path(rec["id"]) is None
    world.svc.checker = None
    assert world.svc.create("in a cafe", level="suggestive")["status"] == "blocked"


def test_backend_errors_become_failed_pictures(world):
    world.backend.error = ImageGenError("Cannot reach ComfyUI at http://127.0.0.1:8188. Is it running?")
    rec = world.svc.create("in a cafe", level="off")
    assert rec["status"] == "failed" and "ComfyUI" in rec["reason"]
    world.backend.error = RuntimeError("boom with /secret/path")
    rec = world.svc.create("in a cafe", level="off")
    assert rec["status"] == "failed" and "secret" not in rec["reason"]


def test_needs_an_avatar(tmp_path, store):
    svc = photos.PhotoService(tmp_path / "p", store, lambda level: FakeBackend(), FakeChecker(), sync=True)
    with pytest.raises(photos.PhotoError, match="Choose an avatar"):
        svc.create("in a cafe")


@pytest.mark.requires_addon
def test_reroll_keeps_the_scene_but_respects_the_current_level(world):
    first = world.svc.create("in lingerie", level="suggestive")
    again = world.svc.reroll(first["id"], level="suggestive")
    assert again["id"] != first["id"] and again["scene"] == first["scene"] and again["source"] == "reroll"
    assert world.svc.get(first["id"])["superseded_by"] == again["id"]                # the old one points at its replacement
    assert [r["id"] for r in world.svc.list()] == [again["id"]]                      # and is left out of the list
    with pytest.raises(photos.PhotoBlocked):
        world.svc.reroll(first["id"], level="off")                                 # she can't reroll above the current level
    with pytest.raises(photos.PhotoError, match="not found"):
        world.svc.reroll("p-0000000000")


def test_list_delete_and_ids(world):
    a = world.svc.create("one", level="off")
    b = world.svc.create("two", level="off")
    ids = [r["id"] for r in world.svc.list()]
    assert set(ids) == {a["id"], b["id"]} and world.svc.list("someone-else-000000") == []
    assert world.svc.delete(a["id"]) and not world.svc.delete(a["id"])
    assert world.svc.image_path(a["id"]) is None and world.svc.delete_for_avatar("mara-bell-47ab97") == 1
    for bad in ("../x", "p-zz", "", None, "p-0123456789/../x"):
        assert world.svc.get(bad) is None and world.svc.image_path(bad) is None and world.svc.delete(bad) is False


@pytest.mark.requires_addon
def test_a_blocked_picture_remembers_which_level_it_needed(world):
    rec = world.svc.create("wearing a sexy dress", level="off", record_blocked=True)
    assert rec["status"] == "blocked" and rec["required_level"] == "suggestive"
    with pytest.raises(photos.PhotoBlocked) as raised:
        world.svc.create("wearing lingerie", level="romantic")
    assert raised.value.required_level == "suggestive"
    with pytest.raises(photos.PhotoBlocked) as raised:
        world.svc.create("a 16 year old", level="explicit")
    assert raised.value.required_level is None                                    # a hard rule, not something a level fixes
    assert world.svc.create("in a cafe", level="off")["required_level"] is None


def premade(world, scene="in a cafe with a latte", level="off", key="mara-bell-47ab97:s-0123abcd", label="Cafe"):
    return world.svc.import_premade("mara-bell-47ab97", PNG + key.encode(), scene, level, key, label)


def test_a_premade_picture_is_imported_checked_and_stored(world):
    rec = premade(world)
    assert rec["status"] == "ready" and rec["premade"] is True and rec["age_checked"] is True and rec["label"] == "Cafe"
    assert world.svc.image_path(rec["id"]).read_bytes() == PNG + b"mara-bell-47ab97:s-0123abcd"
    assert world.backend.calls == []                                              # nothing was generated


def test_importing_the_same_premade_picture_again_replaces_it_and_keeps_its_id(world):
    first = premade(world)
    second = world.svc.import_premade("mara-bell-47ab97", PNG + b"new", "in a cafe with a latte", "off",
                                      "mara-bell-47ab97:s-0123abcd", "Cafe")
    assert second["id"] == first["id"] and world.svc.image_path(first["id"]).read_bytes() == PNG + b"new"
    assert len(world.svc.list_premade("mara-bell-47ab97")) == 1
    assert world.svc.premade_keys("mara-bell-47ab97") == {"mara-bell-47ab97:s-0123abcd": first["id"]}


@pytest.mark.requires_addon
def test_premade_pictures_go_through_the_same_gates(world):
    with pytest.raises(photos.PhotoBlocked, match="never allowed"):
        premade(world, scene="a 16 year old at school")
    with pytest.raises(photos.PhotoBlocked, match="filed under Romantic") as raised:
        premade(world, scene="wearing lingerie", level="romantic")
    assert raised.value.required_level == "suggestive"
    world.checker.result = {"ok": False, "apparent_age": 17, "model": "fake", "error": None}
    with pytest.raises(photos.PhotoBlocked, match="age check rejected"):
        premade(world)
    world.checker.result = {"ok": None, "apparent_age": None, "model": "fake", "error": "no vision model"}
    with pytest.raises(photos.PhotoBlocked, match="no vision model"):
        premade(world, scene="wearing a silk robe", level="suggestive", key="k:2")
    assert premade(world, key="k:3")["age_checked"] is False                      # Off and Romantic: unchecked is allowed
    with pytest.raises(photos.PhotoError, match="no description"):
        premade(world, scene="", key="k:4")
    with pytest.raises(photos.PhotoError, match="Unknown level"):
        premade(world, level="wild", key="k:5")
    assert len(world.svc.list_premade("mara-bell-47ab97")) == 1                   # the failures above stored nothing


@pytest.mark.requires_addon
def test_premade_pictures_respect_the_current_level(world):
    everyday = premade(world)
    robe = premade(world, scene="wearing a silk robe by a bedroom window", level="suggestive", key="mara-bell-47ab97:s-1123abcd",
                    label="Silk robe")
    assert {r["id"] for r in world.svc.list_premade("mara-bell-47ab97", "off")} == {everyday["id"]}
    assert {r["id"] for r in world.svc.list_premade("mara-bell-47ab97", "suggestive")} == {everyday["id"], robe["id"]}
    assert world.svc.get_premade(robe["id"], "mara-bell-47ab97", "romantic") is None
    assert world.svc.get_premade(robe["id"], "mara-bell-47ab97", "suggestive")["id"] == robe["id"]
    assert world.svc.get_premade(robe["id"], "someone-else-000000", "explicit") is None
    assert world.svc.get_premade("p-0000000000", "mara-bell-47ab97", "explicit") is None
    generated = world.svc.create("in a park", level="off")
    assert world.svc.get_premade(generated["id"], "mara-bell-47ab97", "explicit") is None      # not a ready-made one


@pytest.mark.requires_addon
def test_picking_a_premade_picture_for_a_request(world):
    cafe = premade(world)
    robe = premade(world, scene="wearing a silk robe by a bedroom window", level="suggestive", key="k:robe", label="Silk robe")
    assert world.svc.pick_premade("mara-bell-47ab97", "off")["id"] == cafe["id"]              # the only one allowed at Off
    assert world.svc.pick_premade("mara-bell-47ab97", "suggestive", "a latte in the cafe")["id"] == cafe["id"]
    assert world.svc.pick_premade("mara-bell-47ab97", "suggestive", "in a bedroom robe")["id"] == robe["id"]
    assert world.svc.pick_premade("mara-bell-47ab97", "suggestive", "on a mountain top") is None   # names nothing we have
    assert world.svc.pick_premade("mara-bell-47ab97", "off", "wearing a robe in the bedroom") is None   # robe is above Off
    assert world.svc.pick_premade("someone-else-000000", "explicit") is None


def test_redoing_a_premade_picture_adds_a_new_one_and_leaves_the_premade_alone(world):
    ready = premade(world)
    again = world.svc.reroll(ready["id"], level="off")
    assert again["id"] != ready["id"] and again.get("premade") is None
    assert world.svc.get(ready["id"])["superseded_by"] is None
    assert [r["id"] for r in world.svc.list_premade("mara-bell-47ab97")] == [ready["id"]]


def test_delete_all_removes_every_picture_but_keeps_avatars(world):
    world.svc.create("one", level="off")
    world.svc.create("in lingerie", level="off", record_blocked=True)
    assert world.svc.delete_all() == 2 and world.svc.list() == [] and not list(world.svc.root.iterdir())
    assert world.store.active_id() == "mara-bell-47ab97"


def test_jobs_left_running_by_a_restart_are_marked_failed(world):
    rec = {"id": "p-abcdef0123", "avatar_id": "x", "status": "running", "scene": "s", "level": "off", "created": "2026-01-01"}
    (world.svc.root / "p-abcdef0123.json").write_text(json.dumps(rec), "utf-8")
    photos.PhotoService(world.svc.root, world.store, lambda level: world.backend, world.checker, sync=True)
    assert world.svc.get("p-abcdef0123")["status"] == "failed"


def test_background_worker_finishes_jobs(tmp_path, forge, store):
    make_forge(forge)
    store.import_from_forge("mara-bell-47ab97")
    svc = photos.PhotoService(tmp_path / "bg", store, lambda level: FakeBackend(), FakeChecker(ok=True))
    rec = svc.create("in a cafe", level="off")
    assert rec["status"] in ("pending", "running", "ready")
    for _ in range(100):
        if svc.get(rec["id"])["status"] == "ready":
            break
        time.sleep(0.05)
    assert svc.get(rec["id"])["status"] == "ready"


def test_photo_worker_start_failure_is_saved_as_failed_and_can_be_retried(world, monkeypatch):
    from core import state_gate
    gate = state_gate.StateGate()
    monkeypatch.setattr(state_gate, 'STATE', gate)
    world.svc.sync = False
    def refuse(*args, **kwargs):
        raise RuntimeError('Synthetic photo startup failure')
    with monkeypatch.context() as failure:
        failure.setattr(threading.Thread, 'start', refuse)
        with pytest.raises(RuntimeError, match='Synthetic photo startup failure'):
            world.svc.create('in a synthetic cafe', level='off')
    failed, = world.svc.list()
    assert failed['status'] == 'failed' and 'could not be queued' in failed['reason']
    assert failed['finished'] and not world.backend.calls and not gate.active and gate.owned_jobs == 0
    retried = world.svc.create('reviewed synthetic retry in a cafe', level='off')
    assert gate.join_jobs(timeout=3)
    assert world.svc.get(retried['id'])['status'] == 'ready' and len(world.backend.calls) == 1
