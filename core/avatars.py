"""
Altera — Avatars.

An avatar is a character made in NPC Forge (the ComfyUI character studio) and imported here. NPC Forge does the creating:
it rolls or takes a description, paints the portrait, the full-body view and the model sheet. Altera reads that library
(read-only) and copies what it needs: the portrait, the full-body front view when there is one, and the character's card
(name, age, looks, background). Every picture Altera sends is an edit of those reference images, so it keeps the avatar's face.

Nothing adult-related is stored in NPC Forge, and nothing here writes to it.

An avatar is also a companion: its own chat, its own notes about the person it talks to, its own first meeting. The
personality comes from NPC Forge too (traits, how the character talks and comes across, quirk, goal, pet peeve, hobbies,
background); Altera only copies it.

Layout under data/avatars/:  <id>/avatar.json, portrait.png, body.png (optional), companion.json (first meeting, how she sees
herself), notes/ (see core/notes.py)   and   active.json
"""
import datetime
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

from core import safety

# What kind of person someone is. Companions are the deep ones (memory, notes, story, presence); pals are light personas;
# characters come from imported character cards. Chats, voices and pictures work the same for all three.
KINDS = ("pal", "character", "companion")
DEFAULT_KIND = "companion"            # everyone imported before kinds existed came from NPC Forge as a companion

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")

# What a character's NPC Forge setting means for her world. A modern setting (or an unknown one) says nothing.
SETTING_WORLDS = {
    "western": "the late-1800s American Old West",
    "fantasy": "a fantasy world of magic and myth",
    "scifi": "a science-fiction future",
}


class AvatarError(ValueError):
    """Something the user can fix: the message is safe to show."""


def valid_id(value) -> bool:
    return isinstance(value, str) and bool(ID_RE.match(value))


def kind_of(record) -> str:
    """The record's kind; a record saved before kinds existed (or with an unknown one) is a companion."""
    kind = (record or {}).get("kind")
    return kind if kind in KINDS else DEFAULT_KIND


# What someone keeps beyond the chat itself. Companions always have all of it. A pal has none of it unless it is switched on
# for that pal (core/pals.py); a character has all of it unless it is switched off (core/characters.py).
FEATURES = ("memory", "notes", "story", "presence")


def features_of(record) -> dict:
    """{memory, notes, story, presence}: True where that part of Altera is used for this person."""
    kind = kind_of(record)
    if kind == "companion":
        return dict.fromkeys(FEATURES, True)
    chosen = (record or {}).get("features") or {}
    if kind == "pal":
        keeps = {name: chosen.get(name) is True for name in FEATURES}
        if (record or {}).get("kids") is True:          # kids mode: nothing it starts itself (core/kids.py)
            keeps["presence"] = False
        return keeps
    return {name: chosen.get(name) is not False for name in FEATURES}


def forge_id(record) -> str | None:
    """The NPC Forge character this person's pictures come from: a companion's own source, or the character NPC Forge
    painted a pal's or a character's portrait from (core/portraits.py). None when there is none."""
    record = record or {}
    if kind_of(record) == "companion":
        return (record.get("source") or {}).get("id") or None
    made = record.get("forge") if isinstance(record.get("forge"), dict) else {}
    return made.get("id") if record.get("portrait_from") == "forge" and valid_id(made.get("id")) else None


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


# On Windows a file cannot be replaced while another request has it open, and cannot be opened in the instant it is being
# replaced: both raise PermissionError. Two page loads at once hit that on active.json, and the reader then took "nobody is
# active" and switched to the first companion in the list. So both sides wait a moment and try again.
_RETRIES, _RETRY_WAIT = 8, 0.025


def _write_json(path: Path, data: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        for attempt in range(_RETRIES):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == _RETRIES - 1:
                    raise
                time.sleep(_RETRY_WAIT)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _read_json(path: Path) -> dict | None:
    for attempt in range(_RETRIES):
        try:
            return json.loads(path.read_text("utf-8"))
        except PermissionError:
            if attempt == _RETRIES - 1:
                return None
            time.sleep(_RETRY_WAIT)
        except (OSError, ValueError):
            return None


def _look(appearance: dict) -> str:
    """One sentence-fragment describing how the character looks, built from NPC Forge's appearance fields."""
    a = appearance or {}
    parts = []
    if a.get("build"):
        parts.append(f'{a["build"]} build')
    if a.get("skin_tone"):
        parts.append(f'{a["skin_tone"]} skin')
    if a.get("hair"):
        parts.append(a["hair"])
    if a.get("eyes"):
        parts.append(f'{a["eyes"]} eyes')
    for key in ("facial_hair", "glasses", "distinguishing", "extra"):
        if (a.get(key) or "").strip():
            parts.append(a[key].strip())
    return ", ".join(p for p in parts if p)


_PRONOUNS = {"female": ("she", "her", "her", "she's"), "male": ("he", "him", "his", "he's")}
_FALLBACK_PRONOUNS = ("they", "them", "their", "they're")
_PLACEHOLDER = re.compile(r"\{(subj|Subj|obj|poss)\}")


def pronouns(gender) -> dict:
    """The words for someone whose gender is known: {they, them, their, they're}. A missing or unknown gender is they/them,
    which is also what a pal with no gender chosen gets."""
    they, them, their, they_re = _PRONOUNS.get((gender or "").lower(), _FALLBACK_PRONOUNS)
    return {"they": they, "them": them, "their": their, "they're": they_re}


def fill_pronouns(text, gender) -> str:
    """NPC Forge's tables write "{poss} own jokes"; characters made before it filled those in have the raw text stored."""
    subj, obj, poss, _ = _PRONOUNS.get((gender or "").lower(), _FALLBACK_PRONOUNS)
    words = {"subj": subj, "Subj": subj.capitalize(), "obj": obj, "poss": poss}
    return _PLACEHOLDER.sub(lambda m: words[m.group(1)], str(text or ""))


def card_from_npc(npc: dict, name: str | None = None, age=None) -> dict:
    """Altera's avatar card from an NPC Forge npc.json. Outfit and accessory are left out: pictures choose their own clothes."""
    p, a = npc.get("profile") or {}, npc.get("appearance") or {}
    first, last = (p.get("first_name") or "").strip(), (p.get("last_name") or "").strip()
    display = (name or first).strip()
    if not display:
        raise AvatarError("This character has no name. Enter one to import it.")
    raw_age = age if age not in (None, "") else p.get("age")
    try:
        raw_age = int(raw_age) if raw_age not in (None, "") else None
    except (TypeError, ValueError):
        raise AvatarError("The age must be a number.") from None
    return {
        "name": display,
        "full_name": " ".join(x for x in (first, last) if x) or display,
        "age": raw_age,
        **(_forge_age_source(p) if age in (None, "") else {}),
        "gender": (p.get("gender") or "").strip().lower(),
        "style": npc.get("style") or "photoreal",
        "look": _look(a),
        "heritage": (p.get("heritage") or "").strip(),
        "occupation": (p.get("occupation") or "").strip(),
        "bio": fill_pronouns((p.get("bio") or "").strip(), p.get("gender")),
        "personality": [str(x) for x in (p.get("personality") or [])],
        "hobbies": [fill_pronouns(x, p.get("gender")) for x in (p.get("hobbies") or [])],
        # made in NPC Forge (older characters have none of these; the prompt copes)
        "speech": fill_pronouns((p.get("speech") or "").strip(), p.get("gender")),
        "demeanor": fill_pronouns((p.get("demeanor") or "").strip(), p.get("gender")),
        "quirk": fill_pronouns((p.get("quirk") or "").strip(), p.get("gender")),
        "goal": fill_pronouns((p.get("goal") or "").strip(), p.get("gender")),
        "peeve": fill_pronouns((p.get("peeve") or "").strip(), p.get("gender")),
        "world": SETTING_WORLDS.get(npc.get("setting") or "", ""),
    }


def _forge_age_source(profile):
    source = profile.get("age_source")
    if type(profile.get("age")) is int and source in ("rolled", "chosen", "locked"):
        return {"age_source": source}
    return {}


# ---- companions written in Altera, without NPC Forge ----------------------------------------------------------------
WRITTEN_SOURCE = "written"
WRITTEN_PREFIX = "cpt-"
WRITTEN_TEXT_LIMITS = {"bio": 1200, "speech": 400, "demeanor": 400, "quirk": 400, "goal": 400, "peeve": 400}
HOBBY_MAX = 100
HOBBIES_MAX = 12
MAX_PORTRAIT_BYTES = 12 * 1024 * 1024


def written_here(record) -> bool:
    """True for a companion written in Altera itself: kind "companion" with source type "written". Someone imported from
    NPC Forge is not, even though it is a companion too."""
    return kind_of(record) == "companion" and isinstance((record or {}).get("source"), dict) \
        and (record["source"] or {}).get("type") == WRITTEN_SOURCE


def card_from_written(*, name, age, gender="", bio="", hobbies=(), speech="", demeanor="", quirk="", goal="",
                      peeve="") -> dict:
    """Altera's avatar card from what a person typed: the same fields an NPC Forge import fills. The fields Altera has no
    words for (looks, occupation, world, traits) are left empty, exactly as they are for an older import."""
    return {
        "name": name, "full_name": name, "age": age, "age_source": "chosen",
        "gender": gender, "style": "", "look": "", "heritage": "", "occupation": "",
        "bio": bio, "personality": [], "hobbies": list(hobbies),
        "speech": speech, "demeanor": demeanor, "quirk": quirk, "goal": goal, "peeve": peeve,
        "world": "",
    }


class AvatarStore:
    def __init__(self, root, forge_library=None, *, forge_client=None):
        self.root = Path(root)
        self.forge = Path(forge_library) if forge_library else None
        self.forge_client = forge_client
        self._forge_mode = ("", 0)

    def forge_mode(self):
        """Remember the version briefly. An unavailable or newer Forge never enables direct library access."""
        if self.forge_client is None:
            return "legacy"
        mode, checked = self._forge_mode
        now = time.monotonic()
        if now - checked >= 5 or not checked:
            from core.forge import ForgeError
            try:
                mode = self.forge_client().library_mode()
            except ForgeError:
                mode = ""
            self._forge_mode = (mode, now)
        return mode

    # ---- NPC Forge (read-only) ----------------------------------------------
    def forge_available(self) -> bool:
        return bool(self.forge and self.forge_mode() == "legacy" and self.forge.is_dir())

    def forge_library_available(self):
        return self.forge_mode() == "cards" or self.forge_available()

    def forge_choices(self):
        if self.forge_mode() != "cards":
            return self.list_forge()
        return [{**row, "imported": self.get(row["id"]) is not None} for row in self.forge_client().card_library()]

    def _forge_dir(self, npc_id) -> Path | None:
        if not self.forge_available() or not valid_id(npc_id):
            return None
        d = self.forge / npc_id
        return d if (d / "npc.json").is_file() else None

    def forge_file(self, npc_id, kind) -> Path | None:
        d = self._forge_dir(npc_id)
        name = {"thumb": "thumb.png", "portrait": "portrait.png"}.get(kind)
        return d / name if d and name and (d / name).is_file() else None

    def _picture_source(self, npc_id):
        from core import addons
        source = addons.picture_source()
        if source is None or not valid_id(npc_id):
            return None, None, None
        mode = self.forge_mode()
        directory = self._forge_dir(npc_id) if mode == "legacy" else None
        client = self.forge_client() if mode == "cards" else None
        return source, directory, client

    def forge_premade(self, npc_id) -> list:
        """Ready-made pictures approved ('kept') in NPC Forge's studio: [{item_id, scene, label, level}]."""
        return self.forge_premade_read(npc_id)[0]

    def forge_premade_read(self, npc_id):
        """(the approved pictures, whether the studio's list could be read at all). A sync takes pictures away from her only when
        it could: NPC Forge may be writing the file at that moment, and that must not look like 'nothing is approved'."""
        source, directory, client = self._picture_source(npc_id)
        return source.items(npc_id, directory, client) if directory or client else ([], False)

    def forge_premade_file(self, npc_id, item_id):
        """The picture file of an approved studio item (an unapproved or failed one is not served or imported)."""
        source, directory, client = self._picture_source(npc_id)
        return source.file(npc_id, item_id, directory, client) if directory or client else None

    def forge_studio_body(self, npc_id) -> dict:
        source, directory, client = self._picture_source(npc_id)
        return source.body(npc_id, directory, client) if directory or client else {}

    def forge_studio_notes(self, npc_id) -> str:
        """The body words written in the studio ('' if none)."""
        return self.forge_studio_body(npc_id).get("text", "")

    def list_forge(self) -> list:
        """The characters in NPC Forge's library, newest first, each marked eligible or not (with the reason)."""
        if not self.forge_available():
            return []
        rows = []
        for d in self.forge.iterdir():
            npc = _read_json(d / "npc.json") if d.is_dir() else None
            if not npc or not valid_id(d.name):
                continue
            p = npc.get("profile") or {}
            has_portrait = (d / "portrait.png").is_file()
            age = p.get("age")
            reason, needs_age = "", False
            if not has_portrait:
                reason = "No portrait yet. Generate one in NPC Forge first."
            elif not isinstance(age, int):
                needs_age = True
            else:
                reason = safety.age_problem(age) or ""
            name = " ".join(x for x in ((p.get("first_name") or "").strip(), (p.get("last_name") or "").strip()) if x)
            rows.append({
                "id": d.name, "name": name or d.name, "age": age if isinstance(age, int) else None,
                **_forge_age_source(p),
                "gender": p.get("gender") or "", "style": npc.get("style") or "",
                "has_portrait": has_portrait, "has_body": (d / "sheet" / "front.png").is_file(),
                "has_thumb": (d / "thumb.png").is_file(), "needs_age": needs_age,
                "eligible": has_portrait and not reason, "reason": reason,
                "imported": (self.root / d.name / "avatar.json").is_file(),
                "premade": len(self.forge_premade(d.name)),
                "_sort": npc.get("updated") or npc.get("created") or "",
            })
        rows.sort(key=lambda r: r.pop("_sort"), reverse=True)
        return rows

    # ---- imported avatars ---------------------------------------------------
    def _dir(self, avatar_id) -> Path | None:
        return self.root / avatar_id if valid_id(avatar_id) else None

    def get(self, avatar_id) -> dict | None:
        d = self._dir(avatar_id)
        return _read_json(d / "avatar.json") if d else None

    def list(self) -> list:
        if not self.root.is_dir():
            return []
        found = [r for r in (self.get(d.name) for d in self.root.iterdir() if d.is_dir()) if r]
        found.sort(key=lambda r: r.get("imported", ""), reverse=True)
        return found

    def migrate_kinds(self) -> int:
        """Write kind=companion into every record saved before kinds existed. Nothing else in the record changes.
        Returns how many were updated; a record that already has a valid kind is left alone."""
        if not self.root.is_dir():
            return 0
        updated = 0
        for d in self.root.iterdir():
            record = self.get(d.name) if d.is_dir() else None
            if record and record.get("kind") not in KINDS:
                _write_json(d / "avatar.json", {**record, "kind": DEFAULT_KIND})
                updated += 1
        return updated

    def active_id(self) -> str | None:
        data = _read_json(self.root / "active.json") or {}
        avatar_id = data.get("id")
        return avatar_id if avatar_id and self.get(avatar_id) else None

    def active(self) -> dict | None:
        avatar_id = self.active_id()
        return self.get(avatar_id) if avatar_id else None

    def activate(self, avatar_id) -> dict:
        record = self.get(avatar_id)
        if not record:
            raise AvatarError("That avatar was not found.")
        self.root.mkdir(parents=True, exist_ok=True)
        _write_json(self.root / "active.json", {"id": avatar_id})
        return record

    def delete(self, avatar_id) -> bool:
        d = self._dir(avatar_id)
        if not d or not d.is_dir():
            return False
        was_active = self.active_id() == avatar_id
        shutil.rmtree(d)
        if was_active:
            try:
                (self.root / "active.json").unlink()
            except OSError:
                pass
        return True

    # ---- companion state (first meeting, how she sees herself) --------------------------------------------------
    def state(self, avatar_id) -> dict:
        d = self._dir(avatar_id)
        data = _read_json(d / "companion.json") if d else None
        return data if isinstance(data, dict) else {}

    def update_state(self, avatar_id, **changes) -> dict:
        d = self._dir(avatar_id)
        if not d or not d.is_dir():
            return {}
        data = {**self.state(avatar_id), **changes}
        _write_json(d / "companion.json", data)
        return data

    def refresh_card(self, avatar_id) -> bool:
        """Pick up what was edited in NPC Forge since the import (traits, how she talks, background, looks, body notes).
        Her name, age and gender stay as imported. A card that would no longer pass the safety rules is not taken. Returns
        True when the card changed."""
        record = self.get(avatar_id)
        source = (record or {}).get("source") or {}
        if source.get("type") == "pal":                    # a pal is refreshed from its own pal.yaml, never from NPC Forge
            from core import pals
            return pals.refresh(self, avatar_id)
        d = self._forge_dir(source.get("id")) if source.get("type") == "npc-forge" else None
        if not record or not d:
            return False
        npc = _read_json(d / "npc.json") or {}
        stamp = npc.get("updated") or npc.get("created")
        old = record.get("card") or {}
        try:
            card = card_from_npc(npc, name=old.get("name"), age=old.get("age"))
        except AvatarError:
            return False
        for key in ("gender", "full_name"):
            if old.get(key):
                card[key] = old[key]
        if old.get("age_source") in ("rolled", "chosen", "locked"):
            card["age_source"] = old["age_source"]
        if safety.card_problem(card):
            return False
        studio_body = self.forge_studio_body(source["id"])
        if studio_body.get("text"):
            card["body_notes"] = studio_body["text"]
        if studio_body.get("explicit"):
            card["body_notes_explicit"] = studio_body["explicit"]
        if card == old or safety.card_problem(card):
            if stamp and source.get("updated") != stamp and card == old:
                record["source"] = {**source, "updated": stamp}
                _write_json(self._dir(avatar_id) / "avatar.json", record)
            return False
        record["card"] = card
        record["name"] = card["name"]
        record["source"] = {**source, "updated": stamp}
        _write_json(self._dir(avatar_id) / "avatar.json", record)
        return True

    def image_path(self, avatar_id, kind="portrait") -> Path | None:
        d = self._dir(avatar_id)
        name = {"portrait": "portrait.png", "body": "body.png"}.get(kind)
        return d / name if d and name and (d / name).is_file() else None

    def refs(self, avatar_id, use_body=True) -> list:
        """Reference images for a picture: the portrait first, then the full-body view if the avatar has one."""
        images = []
        portrait = self.image_path(avatar_id, "portrait")
        if portrait:
            images.append(portrait.read_bytes())
        body = self.image_path(avatar_id, "body") if use_body else None
        if body and images:
            images.append(body.read_bytes())
        return images

    def import_from_forge(self, npc_id, name=None, age=None, checker=None) -> dict:
        """Copy a character out of NPC Forge's library. Refuses anyone under MIN_AVATAR_AGE, any card that describes a minor
        or a real person, and (when a checker is given) a portrait that reads as under 18."""
        d = self._forge_dir(npc_id)
        if not d:
            raise AvatarError("That character was not found in NPC Forge's library.")
        npc = _read_json(d / "npc.json") or {}
        card = card_from_npc(npc, name=name, age=age)
        if problem := safety.card_problem(card):
            raise AvatarError(problem)
        studio_body = self.forge_studio_body(npc_id)          # written in NPC Forge's studio while perfecting the character
        if studio_body.get("text"):
            card["body_notes"] = studio_body["text"]
        if studio_body.get("explicit"):
            card["body_notes_explicit"] = studio_body["explicit"]
        problem = safety.card_problem(card)
        if problem:
            raise AvatarError(problem)
        portrait = d / "portrait.png"
        if not portrait.is_file():
            raise AvatarError("This character has no portrait yet. Generate one in NPC Forge first.")
        body = d / "sheet" / "front.png"
        check = checker.check(portrait.read_bytes()) if checker else {"ok": None, "apparent_age": None, "model": None, "error": None}
        if check.get("ok") is False:
            raise AvatarError(f"The portrait looks younger than {safety.MIN_APPARENT_AGE} (estimated {check.get('apparent_age')}), "
                              "so this character cannot be used as an avatar.")

        target = self.root / npc_id
        target.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(portrait, target / "portrait.png")
        if body.is_file():
            shutil.copyfile(body, target / "body.png")
        elif (target / "body.png").exists():
            (target / "body.png").unlink()
        record = {
            "id": npc_id, "name": card["name"], "kind": kind_of(self.get(npc_id)), "card": card,
            "source": {"type": "npc-forge", "id": npc_id, "updated": npc.get("updated") or npc.get("created")},
            "has_body": body.is_file(),
            "age_check": {"ok": check.get("ok"), "apparent_age": check.get("apparent_age"), "model": check.get("model")},
            "imported": _now(),
        }
        _write_json(target / "avatar.json", record)
        if self.active_id() is None:
            self.activate(npc_id)
        return self.public(record)

    def set_uploaded_portrait(self, avatar_id, data, checker=None) -> dict:
        """Use a picture the person uploaded for a pal, a character or a companion written here. It is checked exactly like a
        Forge portrait: one that reads as under age is refused, and the picture shown before stays. A character's card
        picture is kept aside, to go back to."""
        record = self.get(avatar_id)
        if not record or (kind_of(record) not in ("pal", "character") and not written_here(record)):
            raise AvatarError("Only a pal, a character or a companion written in Altera can have a picture uploaded.")
        if len(data) > MAX_PORTRAIT_BYTES:
            raise AvatarError(f"The picture must be smaller than {MAX_PORTRAIT_BYTES // (1024 * 1024)} MB.")
        from core.charcard import CardError, png_size
        try:
            png_size(data)
        except CardError as error:
            raise AvatarError(f"That file is not a PNG image Altera can show. {error}") from None
        check = checker.check(data) if checker else {"ok": None, "apparent_age": None, "model": None}
        if check.get("ok") is False:
            raise AvatarError(f"The picture looks younger than {safety.MIN_APPARENT_AGE} "
                              f"(estimated {check.get('apparent_age')}), so it cannot be used.")
        folder = self._dir(avatar_id)
        from core.portraits import CARD_PICTURE
        portrait, kept = folder / "portrait.png", folder / CARD_PICTURE
        if (kind_of(record) == "character" and record.get("portrait_from") not in ("forge", "upload")
                and portrait.is_file() and not kept.is_file()):
            shutil.copyfile(portrait, kept)                     # the card's own picture, to go back to
            record["card_age_check"] = record.get("age_check")
        portrait.write_bytes(data)
        record["portrait_from"] = "upload"
        record["age_check"] = {"ok": check.get("ok"), "apparent_age": check.get("apparent_age"), "model": check.get("model")}
        record.pop("portrait_removed", None)
        _write_json(folder / "avatar.json", record)
        return self.public(record)

    def public(self, record: dict) -> dict:
        card = record.get("card") or {}
        return {
            "id": record["id"], "name": record.get("name") or card.get("name"), "kind": kind_of(record), "age": card.get("age"),
            **_forge_age_source(card),
            "gender": card.get("gender"), "style": card.get("style"), "look": card.get("look"),
            "has_body": bool(record.get("has_body")), "imported": record.get("imported"),
            "written": written_here(record),
            "age_checked": (record.get("age_check") or {}).get("ok") is True,
            "active": self.active_id() == record["id"], "features": features_of(record),
            "kids": kind_of(record) == "pal" and record.get("kids") is True,
            # changes with each new picture, so a page asks for the portrait again instead of showing the one it has
            "portrait_at": (record.get("forge") or {}).get("made") if record.get("portrait_from") == "forge" else None,
            # who she is, for the portrait panel
            "occupation": card.get("occupation") or "",
            # a list of traits; a character card's personality is prose and stays out of the chips
            "traits": card.get("personality") if isinstance(card.get("personality"), list) else card.get("traits") or [],
            **({"licence": record["licence"], "ai_generated": record.get("ai_generated") is True}
               if record.get("licence") else {}),
            "bio": card.get("bio") or "", "speech": card.get("speech") or "", "demeanor": card.get("demeanor") or "",
            "hobbies": card.get("hobbies") or [], "quirk": card.get("quirk") or "", "vibe": card.get("vibe") or "",
        }
