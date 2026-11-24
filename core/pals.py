"""
Altera — Pals: light personas made in a few seconds from a name, a vibe and a voice.

Vibes are YAML presets shipped in content/pals/, shaped like personality.yaml (traits, speech_style, interests, quirks) plus
a label and a description for the picker. Making a pal copies a vibe into data/avatars/<id>/pal.yaml with the chosen name
and voice. From then on that file *is* the pal: change who it is in its settings on Home (update, persona=...), or edit the
file by hand, and the next time the pal is opened the change is picked up (AvatarStore.refresh_card). Keeping it under data/ means backups, restore and removal cover a pal exactly as
they cover a companion. avatar.json beside it is the copy the rest of Altera reads (kind "pal").

A pal keeps nothing beyond the chat itself unless you switch it on: memory, notes, story and presence all start off
(features in pal.yaml). Chats, speech and the chat page are the same ones companions use. A pal starts with no portrait; the
portrait route draws its initial instead (portrait_svg) until NPC Forge paints it one (core/portraits.py).
"""
import hashlib
import html
import os
import re
import secrets
import tempfile
from pathlib import Path

import yaml

from core import safety
from core.avatars import FEATURES, _now, _write_json, valid_id
from core.paths import CODE_ROOT

VIBES_DIR = CODE_ROOT / "content" / "pals"
PAL_FILE = "pal.yaml"
NAME_MAX = 40
_NAME_RE = re.compile(r"^[^\x00-\x1f\x7f<>{}\[\]\\]{1,40}$")
_VIBE_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,31}$")

# Voices a pal can have. Kokoro voice ids are the local voice service's; edge ids are Microsoft's neural voices. An empty
# voice means the voice set in Settings.
KOKORO_VOICES = (
    ("af_heart", "Heart (warm, US)"), ("af_bella", "Bella (bright, US)"), ("af_nicole", "Nicole (soft, US)"),
    ("af_sky", "Sky (light, US)"), ("am_adam", "Adam (deep, US)"), ("am_michael", "Michael (friendly, US)"),
    ("bf_emma", "Emma (UK)"), ("bm_george", "George (UK)"),
)
EDGE_VOICES = (
    ("en-US-AriaNeural", "Aria (US)"), ("en-US-JennyNeural", "Jenny (US)"), ("en-US-GuyNeural", "Guy (US)"),
    ("en-US-DavisNeural", "Davis (US)"), ("en-GB-SoniaNeural", "Sonia (UK)"), ("en-GB-RyanNeural", "Ryan (UK)"),
)
_KOKORO_ID = re.compile(r"^[a-z]{2}_[a-z]{2,20}$")
_EDGE_ID = re.compile(r"^[a-z]{2}-[A-Z]{2}-[A-Za-z]{2,30}Neural$")


class PalError(ValueError):
    """Something the user can fix: the message is safe to show."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


# ---- vibes -----------------------------------------------------------------------------------------------------------
def _load_yaml(path: Path) -> dict | None:
    try:
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError):
        return None
    return data if isinstance(data, dict) else None


def _strings(value) -> list:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [s for item in value for s in _strings(item)]
    return []


def _persona(data: dict) -> dict:
    """The personality.yaml-shaped part of a vibe or a pal, with the shapes checked and the text trimmed."""
    style = data.get("speech_style") if isinstance(data.get("speech_style"), dict) else {}
    return {
        "traits": _strings(data.get("traits"))[:8],
        "speech_style": {
            "tone": str(style.get("tone") or "").strip()[:120],
            "formality": str(style.get("formality") or "").strip()[:120],
            "humor": str(style.get("humor") or "").strip()[:120],
            "emojis": style.get("emojis") is True,
        },
        "interests": _strings(data.get("interests"))[:8],
        "quirks": _strings(data.get("quirks"))[:6],
    }


def vibes(directory: Path | None = None) -> list:
    """Every vibe in content/pals/, in picker order: [{id, label, description, ...persona}]."""
    directory = Path(directory or VIBES_DIR)
    found = []
    if directory.is_dir():
        for path in sorted(directory.glob("*.yaml")):
            data = _load_yaml(path)
            if not data or not _VIBE_ID_RE.match(path.stem):
                continue
            order = data.get("order")
            found.append({"id": path.stem, "label": str(data.get("label") or path.stem.title())[:40],
                          "description": str(data.get("description") or "").strip()[:200],
                          "order": order if isinstance(order, int) else 999, **_persona(data)})
    found.sort(key=lambda v: (v["order"], v["id"]))
    return found


def vibe(vibe_id, directory: Path | None = None) -> dict | None:
    return next((v for v in vibes(directory) if v["id"] == vibe_id), None)


# ---- voices ----------------------------------------------------------------------------------------------------------
def valid_voice(voice, engine) -> bool:
    if not voice:
        return True
    if engine == 'voicelab':
        from core import voicelab
        return voicelab.valid_name(voice)
    pattern = _KOKORO_ID if engine == "kokoro" else _EDGE_ID
    return isinstance(voice, str) and bool(pattern.match(voice))


def voice_name(voice) -> str:
    """A voice's short name for a sentence: 'Michael' for am_michael, or the id itself for a voice not listed here."""
    label = dict(KOKORO_VOICES + EDGE_VOICES).get(voice)
    return label.split(" (")[0] if label else str(voice)


def voices(cfg=None) -> dict:
    """The voices to offer for the engine chosen in Settings: {engine, default, options: [{id, label}]}."""
    from core import tts
    cfg = cfg or tts.settings()
    engine = cfg["engine"]
    if engine == 'voicelab':
        from core import voicelab
        lab = cfg['voicelab']
        try:
            found = voicelab.catalogue(lab['url'])
        except voicelab.LabError as error:
            return {'engine': engine, 'default': tts.voice_label(cfg), 'options': [], 'message': str(error)}
        default = lab['voice'] if any(row['id'] == lab['voice'] for row in found.voices) else voicelab.default_voice(found.voices)
        return {'engine': engine, 'default': default or tts.voice_label(cfg), 'options': found.voices,
                'message': found.notice or ('' if found.voices else voicelab.NO_VOICES)}
    options = KOKORO_VOICES if engine == "kokoro" else EDGE_VOICES
    return {"engine": engine, "default": tts.voice_label(cfg),
            "options": [{"id": voice_id, "label": label} for voice_id, label in options]}


def _check_voice(voice, engine):
    if not valid_voice(voice, engine) or (voice and engine == 'voicelab'
                                         and voice not in {item['id'] for item in voices()['options']}):
        raise PalError("Choose one of the listed voices, or the default voice.")


# ---- a pal on disk ---------------------------------------------------------------------------------------------------
def _check_name(name) -> str:
    name = " ".join(str(name or "").split())
    if not name:
        raise PalError("Give your pal a name.")
    if len(name) > NAME_MAX or not _NAME_RE.match(name):
        raise PalError(f"Use up to {NAME_MAX} letters, numbers, spaces or simple punctuation for the name.")
    return name


def _problem(data: dict) -> str | None:
    """Why this pal's text cannot be used (the same hard rules as every avatar card), or None."""
    for text in [data.get("name") or "", data.get("about") or "", *_strings(data.get("traits")), *_strings(data.get("interests")),
                 *_strings(data.get("quirks")), *[str(v) for v in (data.get("speech_style") or {}).values() if isinstance(v, str)]]:
        found = safety.hard_problem(text)
        if found:
            return f"This pal cannot be used: {found[0].lower()}{found[1:]}"
    return None


def _new_id(store, name) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:24].strip("-") or "pal"
    for _ in range(20):
        avatar_id = f"pal-{slug}-{secrets.token_hex(3)}"
        if valid_id(avatar_id) and not (store.root / avatar_id).exists():
            return avatar_id
    raise PalError("Could not choose a name for the pal's folder. Try again.", 500)


def card_from(data: dict) -> dict:
    """The avatar card (what the prompt and the page read) for a pal's YAML."""
    persona = _persona(data)
    style = persona["speech_style"]
    speech = "; ".join(part for part in (style["tone"], style["formality"]) if part)
    card = {"name": data["name"], "kind": "pal", "personality": persona["traits"], "speech": speech,
            "humor": style["humor"], "emojis": style["emojis"], "hobbies": persona["interests"],
            "quirks": persona["quirks"], "quirk": "; ".join(persona["quirks"]),
            "vibe": str(data.get("vibe_label") or data.get("vibe") or ""), "bio": str(data.get("about") or "").strip()[:400]}
    return card


# What a person can write about a pal in its settings: (how many, how long each) for the lists, and the longest text.
PERSONA_LISTS = {"traits": (8, 60, "traits"), "interests": (8, 100, "interests"), "quirks": (6, 140, "quirks")}
STYLE_FIELDS = {"tone": "tone", "formality": "formality", "humor": "humour"}
STYLE_MAX = 120
ABOUT_MAX = 400


def _persona_changes(persona, current: dict) -> dict:
    """The pal's persona with these changes laid over it, checked. Raises PalError with what to fix; nothing is cut short."""
    if not isinstance(persona, dict) or not set(persona) <= {"about", *PERSONA_LISTS, "speech_style"}:
        raise PalError("persona may hold about, traits, speech_style, interests and quirks only.")
    fresh = {}
    if "about" in persona:
        about = persona["about"]
        if not isinstance(about, str):
            raise PalError("About must be text.")
        about = " ".join(about.split())
        if len(about) > ABOUT_MAX:
            raise PalError(f"Keep About under {ABOUT_MAX} characters.")
        fresh["about"] = about
    for key, (count, length, label) in PERSONA_LISTS.items():
        if key not in persona:
            continue
        items = persona[key]
        if not isinstance(items, list) or any(not isinstance(item, str) for item in items):
            raise PalError(f"Send the {label} as a list of short phrases.")
        items = [" ".join(item.split()) for item in items if item.strip()]
        if len(items) > count:
            raise PalError(f"Give up to {count} {label}.")
        if any(len(item) > length for item in items):
            raise PalError(f"Keep each of the {label} under {length} characters.")
        fresh[key] = items
    if "speech_style" in persona:
        style = persona["speech_style"]
        if not isinstance(style, dict) or not set(style) <= {*STYLE_FIELDS, "emojis"}:
            raise PalError("speech_style may hold tone, formality, humor and emojis only.")
        merged = dict(current.get("speech_style") or {})
        for key, label in STYLE_FIELDS.items():
            if key in style:
                if not isinstance(style[key], str):
                    raise PalError(f"The {label} must be text.")
                value = " ".join(style[key].split())
                if len(value) > STYLE_MAX:
                    raise PalError(f"Keep the {label} under {STYLE_MAX} characters.")
                merged[key] = value
        if "emojis" in style:
            if type(style["emojis"]) is not bool:
                raise PalError("emojis must be true or false.")
            merged["emojis"] = style["emojis"]
        fresh["speech_style"] = merged
    return fresh


def _persona_texts(data: dict) -> list:
    return [data.get("about") or "", *_strings(data.get("traits")), *_strings(data.get("interests")),
            *_strings(data.get("quirks")), *[str(v) for v in (data.get("speech_style") or {}).values() if isinstance(v, str)]]


def _kids_problem(data: dict) -> bool:
    """True when a pal in kids mode has words on a topic kids mode keeps out (core/kids.py)."""
    return data.get("kids") is True and any(safety.kids_topic(text) for text in [data.get("name") or "", *_persona_texts(data)])


def _features(data: dict) -> dict:
    chosen = data.get("features") if isinstance(data.get("features"), dict) else {}
    keeps = {name: chosen.get(name) is True for name in FEATURES}
    if data.get("kids") is True:                        # kids mode: no check-ins (core/kids.py)
        keeps["presence"] = False
    return keeps


def _kids_allowed():
    from core import kids
    if not kids.enabled():
        raise PalError("Kids mode is locked. Enable it first: it is a guardrail, not a guarantee.", 409)


# Kept on avatar.json across a rebuild from pal.yaml: a portrait from NPC Forge, its job, and the portrait's age check.
PORTRAIT_KEYS = ("forge", "portrait_from", "forge_job", "age_check", "portrait_removed")


def _record(avatar_id: str, data: dict, imported: str) -> dict:
    return {
        "id": avatar_id, "name": data["name"], "kind": "pal", "card": card_from(data),
        "source": {"type": "pal", "file": PAL_FILE, "vibe": data.get("vibe") or ""},
        "has_body": False, "age_check": {"ok": None, "apparent_age": None, "model": None},
        "voice": data.get("voice") or "", "features": _features(data), "kids": data.get("kids") is True, "imported": imported,
    }


def _write_yaml(path: Path, data: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("# A pal. Edit freely: the next time the pal is opened, Altera picks the changes up.\n")
            handle.write("# features: memory, notes, story and presence are off unless set to true.\n")
            handle.write("# kids: true is kids mode (a guardrail, not a guarantee); presence stays off while it is on.\n")
            yaml.safe_dump(data, handle, default_flow_style=False, sort_keys=False, allow_unicode=True)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read(store, avatar_id) -> dict | None:
    """The pal's pal.yaml, or None (not a pal, or the file is missing or broken)."""
    if not valid_id(avatar_id):
        return None
    return _load_yaml(store.root / avatar_id / PAL_FILE)


def create(store, name, vibe_id, voice="", engine=None, vibes_dir=None, kids=False) -> dict:
    """Make a pal from a vibe. Returns its public record. Raises PalError for anything the person can fix."""
    name = _check_name(name)
    chosen = vibe(vibe_id, vibes_dir)
    if not chosen:
        raise PalError("Choose one of the vibes.")
    if engine is None:
        from core import tts
        engine = tts.settings()["engine"]
    voice = (voice or "").strip()
    _check_voice(voice, engine)
    if type(kids) is not bool:
        raise PalError("kids must be true or false.")
    if kids:
        _kids_allowed()
    data = {"name": name, "vibe": chosen["id"], "vibe_label": chosen["label"], "voice": voice, "about": chosen["description"],
            **_persona(chosen), "features": dict.fromkeys(FEATURES, False), "kids": kids}
    problem = _problem(data)
    if problem:
        raise PalError(problem)
    avatar_id = _new_id(store, name)
    folder = store.root / avatar_id
    folder.mkdir(parents=True)
    _write_yaml(folder / PAL_FILE, data)
    record = _record(avatar_id, data, _now())
    _write_json(folder / "avatar.json", record)
    if store.active_id() is None:
        store.activate(avatar_id)
    return store.public(record)


def refresh(store, avatar_id) -> bool:
    """Take up hand edits to pal.yaml. A file that is missing, broken or breaks a rule changes nothing. True when it changed."""
    record = store.get(avatar_id)
    data = read(store, avatar_id)
    if not record or not data:
        return False
    try:
        data["name"] = _check_name(data.get("name"))
    except PalError:
        return False
    if _problem(data) or _kids_problem(data):
        return False
    try:
        from core import tts
        engine = tts.settings()["engine"]
    except Exception:
        engine = None
    if engine and not valid_voice(data.get("voice") or "", engine):
        data["voice"] = ""
    fresh = _record(avatar_id, data, record.get("imported") or _now())
    for key in PORTRAIT_KEYS:                          # what pal.yaml does not hold: the portrait (core/portraits.py)
        if key in record:
            fresh[key] = record[key]
    if fresh == record:
        return False
    _write_json(store.root / avatar_id / "avatar.json", fresh)
    return True


def update(store, avatar_id, *, name=None, voice=None, features=None, kids=None, persona=None) -> dict:
    """Rename a pal, change its voice, who it is (persona: about, traits, speech_style, interests, quirks), what it keeps,
    or switch kids mode. Returns its public record. The same rules as making one apply, and a pal in kids mode keeps its
    words to the topics kids mode allows."""
    record = store.get(avatar_id)
    data = read(store, avatar_id)
    if not record or (record.get("kind") != "pal") or data is None:
        raise PalError("That pal was not found.", 404)
    if name is not None:
        data["name"] = _check_name(name)
    if voice is not None:
        from core import tts
        voice = str(voice).strip()
        if voice != data.get('voice', ''):
            _check_voice(voice, tts.settings()['engine'])
        data["voice"] = voice
    if persona is not None:
        data.update(_persona_changes(persona, data))
    if features is not None:
        if not isinstance(features, dict) or not set(features) <= set(FEATURES) or any(type(v) is not bool for v in features.values()):
            raise PalError("features may only switch memory, notes, story and presence on or off.")
        data["features"] = {**_features(data), **features}
    if kids is not None:
        if type(kids) is not bool:
            raise PalError("kids must be true or false.")
        if kids != (data.get("kids") is True):
            _kids_allowed()                             # on or off, the switch only moves while kids mode is enabled
        data["kids"] = kids
    if data.get("kids") is True:
        if (features or {}).get("presence") is True:
            raise PalError("Check-ins stay off in kids mode.")
        data["features"] = {**_features(data), "presence": False}
    problem = _problem(data)
    if problem:
        raise PalError(problem)
    if _kids_problem(data):
        raise PalError("This pal is in kids mode: keep who it is to child-friendly topics.")
    _write_yaml(store.root / avatar_id / PAL_FILE, data)
    refresh(store, avatar_id)
    return store.public(store.get(avatar_id))


def settings(store, avatar_id) -> dict:
    """What the pal settings window shows."""
    record = store.get(avatar_id)
    data = read(store, avatar_id)
    if not record or record.get("kind") != "pal" or data is None:
        raise PalError("That pal was not found.", 404)
    persona = _persona(data)
    return {"id": avatar_id, "name": record.get("name"), "vibe": data.get("vibe") or "", "vibe_label": data.get("vibe_label") or "",
            "voice": record.get("voice") or "", "features": _features(data), "kids": data.get("kids") is True,
            "about": str(data.get("about") or "").strip()[:ABOUT_MAX], "traits": persona["traits"],
            "speech_style": persona["speech_style"], "interests": persona["interests"], "quirks": persona["quirks"]}


# ---- the picture a pal has until it has a portrait -------------------------------------------------------------------
def portrait_svg(record: dict) -> bytes:
    """A round-friendly square with the pal's initial on a colour chosen from its id. Static SVG: no script, no links."""
    name = (record.get("name") or "?").strip() or "?"
    initial = html.escape(name[0].upper())
    hue = int(hashlib.sha256(record.get("id", "").encode("utf-8")).hexdigest()[:4], 16) % 360
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 500" width="400" height="500">'
            f'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
            f'<stop offset="0" stop-color="hsl({hue},55%,42%)"/><stop offset="1" stop-color="hsl({(hue + 40) % 360},60%,26%)"/>'
            f'</linearGradient></defs><rect width="400" height="500" fill="url(#g)"/>'
            f'<text x="200" y="250" dy=".35em" text-anchor="middle" font-family="system-ui,sans-serif" font-size="190" '
            f'font-weight="600" fill="rgba(255,255,255,.92)">{initial}</text></svg>').encode()
