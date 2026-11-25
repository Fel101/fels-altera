"""Core content policy and a compatibility boundary for saved level preferences."""

import datetime
import random
import re
from functools import lru_cache

from core import addons, safety
from core.datastore import get_datastore

STATE_KEY = "adult_mode"  # Keep the original key so older instances and backups can retain preferences.
OFF = {
    "label": "Off", "summary": "Friendly, everyday conversation and pictures.",
    "prompt": "Keep everything non-sexual. Be warm and friendly. If asked for sexual content, decline gently "
              "and change direction. Do not mention settings or other ways to allow it.",
    "rule": "Keep pictures everyday: fully dressed, natural, friendly.",
    "mood": "a natural, friendly expression",
    "scenes": (
        "taking a selfie in a sunlit kitchen, smiling at the camera and holding a coffee mug",
        "sitting by a window with a book, soft daylight, a relaxed smile",
        "out for a walk in a park in autumn in a cosy jacket, laughing",
        "taking a casual mirror selfie at home in everyday clothes",
        "curled up on a sofa with a blanket and a cup of tea, looking at the camera",
    ),
    "checked": False, "full_length": False, "sensitive": False, "framing": "",
}


def _optional(store=None):
    value = addons.active(store)
    return value.levels if value else {}


def levels():
    return ("off", *_optional())


def options(level):
    return _optional().get(level, OFF)


def level_index(level):
    order = levels()
    return order.index(level) if level in order else 0


def visible(level, ceiling=None):
    """Unknown saved levels stay on disk and are withheld from the core gallery."""
    level = level or "off"
    return level in levels() and (ceiling is None or level_index(level) <= level_index(ceiling))


def label(level):
    return options(level)["label"]


def describe_levels():
    return [{"id": level, "label": rule["label"], "summary": rule["summary"]}
            for level, rule in {"off": OFF, **_optional()}.items()]


def _saved(store):
    saved = store.get_key(STATE_KEY)
    return saved if isinstance(saved, dict) else {}


def get_state(store=None):
    store = store or get_datastore()
    optional = _optional(store)
    if not optional:
        return {"enabled": False, "level": "off", "confirmed_at": None, "privacy_blur": True, "effective_level": "off"}
    raw = _saved(store)
    chosen = raw.get("level")
    available = isinstance(chosen, str) and chosen in optional
    level = chosen if available else next(iter(optional))
    enabled = raw.get("enabled") is True and bool(raw.get("confirmed_at")) and (available or chosen is None)
    return {"enabled": enabled, "level": level, "confirmed_at": raw.get("confirmed_at"),
            "privacy_blur": raw.get("privacy_blur", True) is not False, "effective_level": level if enabled else "off"}


def effective_level(store=None):
    return get_state(store)["effective_level"]


def update_state(changes, store=None):
    store = store or get_datastore()
    optional = _optional(store)
    if not optional:
        return get_state(store), "These settings need a loaded add-on."
    raw = _saved(store).copy()
    if "level" in changes:
        level = changes["level"]
        if not isinstance(level, str) or level not in optional:
            return get_state(store), f"Unknown level. Choose one of: {', '.join(optional)}."
        raw["level"] = level
    if "privacy_blur" in changes:
        raw["privacy_blur"] = changes["privacy_blur"] is not False
    if "enabled" in changes:
        if changes["enabled"] is True and not raw.get("confirmed_at"):
            if changes.get("confirm_adult") is not True:
                return get_state(store), "Confirm that you are 18 or older to turn this on."
            raw["confirmed_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        saved_level = raw.get("level")
        if changes["enabled"] is True and (not isinstance(saved_level, str) or saved_level not in optional):
            raw["level"] = next(iter(optional))
        raw["enabled"] = changes["enabled"] is True
    store.set_key(STATE_KEY, raw)
    return get_state(store), None


def startup_notice():
    if addons.installed() and not addons.active():
        from core import kids
        if kids.enabled():
            return addons.ADDON_BLOCK
    if not _optional() and _saved(get_datastore()).get("confirmed_at"):
        return "A previous setting is now available as a separate add-on. Your saved data is still here."
    return ""


def chat_instruction(level, *, include_rules=True):
    text = options(level)["prompt"]
    return f"{text}\n{safety.ADDON_RULES}" if include_rules and level != "off" and visible(level) else text


def photo_rule(level):
    return options(level)["rule"]


def photo_mood(level):
    return options(level)["mood"]


def needs_age_check(level):
    return options(level)["checked"]


def default_scenes(level):
    for available in reversed(levels()[:level_index(level) + 1]):
        scenes = options(available)["scenes"]
        if scenes:
            return list(scenes)
    return []


def pick_scene(level, rng=None):
    return (rng or random).choice(default_scenes(level))


@lru_cache(maxsize=64)
def _terms(terms):
    if terms:
        words = [r"\s+".join(re.escape(word) for word in term.split()) for term in terms]
        return re.compile(r"\b(?:" + "|".join(words) + r")(?:s|es)?\b", re.I)
    return None


def tier_problem(text, level):
    optional = _optional()
    order = ("off", *optional)
    have = order.index(level) if level in order else 0
    for required in reversed(order[have + 1:]):
        pattern = _terms(optional[required]["terms"])
        match = pattern.search(text or "") if pattern else None
        if match:
            term = match.group(0)
            message = (f'"{term}" needs {label(required)} or higher.' if have
                       else f'Altera does not make pictures of "{term}".')
            return {"required": required, "term": term, "message": message}
    if not have and (term := safety.core_picture_term(text)):
        return {"required": None, "term": term, "message": f'Altera does not make pictures of "{term}".'}
    return None
