"""Hard rules, adult-mode state and the [[photo:]] tag. No GPU, LLM or network needed."""
import random

import pytest

from core import adult, photo_tags, safety
from core.datastore import DataStore, init_db


@pytest.fixture
def store(tmp_path):
    path = str(tmp_path / "t.db")
    init_db(path)
    return DataStore(path)


# ---------------------------------------------------------------- safety

MINOR_TEXT = [
    "a 16 year old girl", "sixteen-year-old", "she's 15", "17yo", "under 18", "under the age of 18", "school uniform",
    "a teenager", "young girl", "loli", "aged 12", "a 12 y/o", "high school student", "barely legal", "an underage girl",
    "she is 14", "not yet 18", "nearly eighteen",
]
ADULT_TEXT = [
    "a 27-year-old woman", "she's 25", "she's 5 feet 6", "she's 10 minutes late", "18 years old", "hey baby",
    "sitting on a balcony at sunset with a coffee", "a warm childhood memory", "at the beach in a red dress",
    "she's 30", "he's 40 and tired", "a 21 year old", "wearing a school-teacher style blouse", "in a photo booth",
]


@pytest.mark.parametrize("text", MINOR_TEXT)
def test_minor_signals_are_caught(text):
    assert safety.has_minor_signal(text, strong_only=True), text
    assert safety.hard_problem(text), text


@pytest.mark.parametrize("text", ADULT_TEXT)
def test_adult_text_passes(text):
    assert safety.hard_problem(text) is None, text


def test_family_words_block_scenes_but_not_chat():
    assert safety.hard_problem("playing with a kid in the park")
    assert safety.hard_problem("a child on her lap")
    assert safety.blocks_message("the kids are asleep, let's have sex") is None     # no strong minor signal


def test_chat_tripwire_needs_minor_plus_sexual():
    assert safety.blocks_message("let's have sex, you're a teenager") == "minor_sexual"
    assert safety.blocks_message("she's 16, come here, i want you naked") == "minor_sexual"
    assert safety.blocks_message("i was a teenager in the nineties") is None
    assert safety.blocks_message("i love sex") is None
    assert safety.blocks_message("") is None


def test_real_person_and_non_consent_are_blocked():
    assert safety.hard_problem("a celebrity in a red dress")
    assert safety.hard_problem("looks like Emma Watson")
    assert safety.hard_problem("resembling Zendaya on a beach")
    assert safety.hard_problem("a deepfake of my neighbour")
    assert safety.hard_problem("she is drugged and unconscious")
    assert safety.hard_problem("against her will")
    assert safety.hard_problem("it looks like rain today") is None
    assert safety.hard_problem("grape juice and therapy") is None


def test_age_problem_and_card_problem():
    assert safety.age_problem(27) is None
    assert safety.age_problem(21) is None
    assert safety.age_problem(18) is None                                                    # the legal floor
    assert "18 or older" in safety.age_problem(17) and "18 or older" in safety.age_problem(12)
    assert "stated age" in safety.age_problem(None)
    assert safety.age_problem("abc")
    assert safety.card_problem({"age": 30, "look": "long dark hair", "bio": "A baker."}) is None
    assert safety.card_problem({"age": 18, "look": "long dark hair"}) is None
    assert safety.card_problem({"age": 17, "look": "long dark hair"})
    assert "not allowed" in safety.card_problem({"age": 30, "bio": "She is a schoolgirl at heart"})
    assert safety.card_problem({"age": 30, "hobbies": ["playing with her kids"]})


# ---------------------------------------------------------------- adult mode

def test_default_state_is_off(store):
    state = adult.get_state(store)
    assert state["enabled"] is False and state["effective_level"] == "off" and state["privacy_blur"] is True


@pytest.mark.requires_addon
def test_enabling_needs_the_18_confirmation(store):
    state, error = adult.update_state({"enabled": True}, store)
    assert error and "18" in error and state["enabled"] is False
    state, error = adult.update_state({"enabled": True, "confirm_adult": True, "level": "suggestive"}, store)
    assert error is None and state["enabled"] is True and state["effective_level"] == "suggestive"
    assert state["confirmed_at"]


@pytest.mark.requires_addon
def test_confirmation_is_remembered_and_disabling_always_works(store):
    adult.update_state({"enabled": True, "confirm_adult": True}, store)
    state, _ = adult.update_state({"enabled": False}, store)
    assert state["enabled"] is False and state["effective_level"] == "off" and state["confirmed_at"]
    state, error = adult.update_state({"enabled": True}, store)                  # no second confirmation needed
    assert error is None and state["enabled"] is True


@pytest.mark.requires_addon
def test_level_is_validated_and_off_is_not_a_level(store):
    _, error = adult.update_state({"level": "off"}, store)
    assert error
    _, error = adult.update_state({"level": "bogus"}, store)
    assert error
    state, error = adult.update_state({"level": "explicit"}, store)
    assert error is None and state["level"] == "explicit" and state["effective_level"] == "off"   # not enabled yet


@pytest.mark.requires_addon
def test_confirmation_cannot_be_forged_through_the_store(store):
    store.set_key(adult.STATE_KEY, {"enabled": True, "level": "explicit"})       # no confirmed_at
    assert adult.get_state(store)["enabled"] is False


@pytest.mark.requires_addon
def test_delete_all_conversations_forgets_the_opt_in(store):
    adult.update_state({"enabled": True, "confirm_adult": True, "level": "explicit"}, store)
    store.reset_all_user_data()                                     # what "Delete All Conversations" runs
    state = adult.get_state(store)
    assert state["enabled"] is False and state["confirmed_at"] is None and state["effective_level"] == "off"
    _, error = adult.update_state({"enabled": True}, store)
    assert error and "18" in error                                  # and it asks for the confirmation again


@pytest.mark.requires_addon
def test_privacy_blur_toggle(store):
    state, _ = adult.update_state({"privacy_blur": False}, store)
    assert state["privacy_blur"] is False


@pytest.mark.requires_addon
def test_chat_instruction_carries_hard_rules_above_off():
    assert "Keep everything non-sexual" in adult.chat_instruction("off") and "never sexualise" not in adult.chat_instruction("off")
    for level in adult.levels()[1:]:
        assert "never sexualise anyone under 18" in adult.chat_instruction(level)


@pytest.mark.requires_addon
def test_tier_problem_reports_the_highest_level_needed():
    assert adult.tier_problem("in a bikini by the pool", "off")["required"] == "romantic"
    assert adult.tier_problem("in a bikini by the pool", "romantic") is None
    assert adult.tier_problem("wearing lingerie", "romantic")["required"] == "suggestive"
    assert adult.tier_problem("wearing lingerie", "suggestive") is None
    assert adult.tier_problem("nude on a bed", "suggestive")["required"] == "explicit"
    assert adult.tier_problem("lingerie and nude", "off")["required"] == "explicit"
    assert adult.tier_problem("nude on a bed", "explicit") is None
    assert adult.tier_problem("a sexy dress", "romantic")["required"] == "suggestive"
    assert adult.tier_problem("a coffee on a balcony", "off") is None
    assert adult.tier_problem("a bramble hedge", "off") is None                  # 'bra' inside a word is not a match


@pytest.mark.requires_addon
def test_default_scenes_have_no_pronouns_and_the_explicit_ones_are_their_own():
    assert adult.default_scenes("off")
    explicit = adult.default_scenes("explicit")
    assert len(explicit) >= 10 and explicit != adult.default_scenes("suggestive")     # the explicit list is filled in
    for scene in explicit:
        assert adult.tier_problem(scene, "suggestive")["required"] == "explicit", scene   # each one really is an explicit scene
    for level in adult.levels():
        for scene in adult.default_scenes(level):
            assert not any(w in scene.lower().split() for w in ("her", "his", "she", "he")), scene
            assert safety.hard_problem(scene) is None
            assert adult.tier_problem(scene, level) is None, (level, scene)   # a default scene never trips its own level
    assert adult.pick_scene("off", random.Random(1)) in adult.default_scenes("off")


@pytest.mark.requires_addon
def test_an_empty_scene_list_falls_back_to_the_level_below(monkeypatch):
    scenes = {"off": ["a"], "romantic": ["b"], "suggestive": ["c"], "explicit": []}
    for level in ("romantic", "suggestive", "explicit"):
        monkeypatch.setitem(adult.options(level), "scenes", scenes[level])
    assert adult.default_scenes("explicit") == ["c"] and adult.default_scenes("romantic") == ["b"]
    scenes["suggestive"].clear()
    assert adult.default_scenes("explicit") == ["b"]


@pytest.mark.requires_addon
def test_describe_levels_lists_all_four():
    assert [level["id"] for level in adult.describe_levels()] == list(adult.levels())


# ---------------------------------------------------------------- photo tags

def _stream(chunks):
    f = photo_tags.TagFilter()
    out = "".join(f.feed(c) for c in chunks) + f.flush()
    return out, f.tags


def test_tag_is_removed_and_collected():
    text, tags = photo_tags.extract("Here you go!\n[[photo: sitting on a balcony at sunset with a coffee]]")
    assert text == "Here you go!"
    assert tags == ["sitting on a balcony at sunset with a coffee"]


def test_tag_split_across_chunks():
    chunks = ["Sure! [", "[pho", "to: on a ", "beach at dawn", "]", "] Enjoy"]
    out, tags = _stream(chunks)
    assert "[[" not in out and "photo" not in out
    assert out.strip().startswith("Sure!") and out.strip().endswith("Enjoy")
    assert tags == ["on a beach at dawn"]


def test_every_split_position_gives_the_same_result():
    text = "Hi there. [[photo: at the beach in a red dress]] See you."
    for i in range(1, len(text)):
        out, tags = _stream([text[:i], text[i:]])
        assert tags == ["at the beach in a red dress"], i
        assert "[[" not in out and "]]" not in out and "photo:" not in out, (i, out)


def test_bare_and_empty_tags():
    assert photo_tags.extract("ok [[photo]]")[1] == [""]
    assert photo_tags.extract("ok [[photo:]]")[1] == [""]
    assert photo_tags.extract("ok [[ PHOTO : a cafe ]]")[1] == ["a cafe"]


def test_other_brackets_are_left_alone():
    text = "Use [[wiki links]] and [single] and [[ note ]] here"
    out, tags = _stream([text])
    assert out == text and tags == []
    out, tags = _stream(["[[photograph of a thing]] and [", "x]"])
    assert tags == [] and "photograph" in out and out.endswith("[x]")


def test_lone_bracket_at_the_end_is_released():
    out, tags = _stream(["array[", "0] is fine"])
    assert out == "array[0] is fine" and tags == []
    out, _ = _stream(["ends with ["])
    assert out == "ends with ["


def test_unterminated_tag_is_dropped():
    out, tags = _stream(["Here it is [[photo: on a bal"])
    assert out == "Here it is " and tags == []


def test_overlong_tag_is_treated_as_text():
    body = "x" * (photo_tags.MAX_TAG_LEN + 50)
    out, tags = _stream(["[[photo: " + body])
    assert tags == [] and "photo:" in out


def test_tidy_removes_gaps():
    assert photo_tags.tidy("Line one.\n\n\n[[x]]  \n\nEnd  ") .count("\n\n\n") == 0
    assert photo_tags.extract("Hi!\n\n[[photo: a cafe]]\n\nBye")[0] == "Hi!\n\nBye"


@pytest.mark.parametrize("text", [
    "send me a selfie", "Can you send me a pic?", "could you take a selfie for me", "show me a picture of you",
    "I wanna see you", "can you share a photo of yourself", "take a selfie in the kitchen", "hey, send me a cute photo please",
    # follow-ups after she has already sent one -- no "send/show/take" verb, just a plain ask for another
    "how about another pic?", "got any other pics?", "any more photos?", "one more picture please",
    "can I see another one of you?",
])
def test_photo_intent_positive(text):
    assert photo_tags.photo_intent(text), text


@pytest.mark.parametrize("text", [
    "I took a photo of my dog", "let's take a picture together", "I love selfies", "what's the picture in the museum like",
    "nice to see you", "how was your day", "the photo you sent was great",
    "can I see you tonight?", "any other plans this weekend?", "how about one more?",  # no noun at all: too vague to catch
])
def test_photo_intent_negative(text):
    assert not photo_tags.photo_intent(text), text


def test_scene_from_request():
    assert photo_tags.scene_from_request("send me a selfie") is None
    assert photo_tags.scene_from_request("Can you send me a selfie in your kitchen?") == "in the kitchen"
    assert photo_tags.scene_from_request("hey, send me a cute pic of you at the beach please") == "at the beach"
    assert photo_tags.scene_from_request("take a selfie wearing a red dress") == "wearing a red dress"
    assert photo_tags.scene_from_request("send me a picture in the kitchen") == "in the kitchen"  # "pic" must not eat "picture"


def test_scene_from_a_bare_follow_up_is_none_so_a_default_scene_is_picked():
    # a bare follow-up has no scene of its own -- it must come back empty, not as literal (nonsense) generation text
    for text in ("how about another pic?", "got any other pics?", "any more photos?", "one more picture please"):
        assert photo_tags.scene_from_request(text) is None, text
    # "can I see ..." only strips the verb clause -- what is left ("another one of you") is pure filler with no keys of
    # its own (core.photos._keys treats "another", "one" and "you" as stopwords), so it still cannot drive generation
    from core.photos import _keys
    assert not _keys(photo_tags.scene_from_request("can I see another one of you?") or "")
    # ... but a follow-up that does name a scene still keeps it
    assert photo_tags.scene_from_request("got any other beach pics?") == "beach pics"
