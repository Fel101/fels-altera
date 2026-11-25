"""
Altera — Characters: people from imported character cards (Character Card V2; V1 and V3 are read too, see core/charcard.py).

Importing a card saves the file exactly as it came under data/characters/<id>.png (or .json), keeps the card's image
(without its text) as the portrait, and writes data/avatars/<id>/avatar.json with kind "character". From then on the
character works like anyone else: one chat, the same chat page and speech.

How a card is used:
  description, personality, scenario   -> the system prompt (who they are, and the situation)
  first_mes                            -> the first message in the chat, as written (no model call)
  mes_example                          -> example dialogue in the system prompt, capped to a token budget
  system_prompt, post_history_instructions -> honoured while "use the card's own instructions" is on (it is, until you
                                          switch it off); post-history instructions go after the chat, with the newest message
  creator_notes                        -> shown on Home only, never sent to a model
  character_book                       -> the lorebook: an entry whose keys appear in the last few messages (or that is
                                          always on) goes into the system prompt, within a token budget, while the
                                          character's lorebook switch is on (it is, until you switch it off). Regex keys and
                                          entries that trigger other entries are not used.

{{char}} / <BOT> become the character's name and {{user}} / <USER> the person's name from the profile. Altera's own rules
(and its safety checks on every message) still apply whatever a card says.
"""
import re
import secrets
import shutil
from hashlib import sha256
from pathlib import Path

from core import charcard, safety
from core.avatars import FEATURES, AvatarError, _now, _write_json, card_from_npc, kind_of, valid_id
from core.notes import estimate_tokens

EXAMPLE_TOKENS = 600                       # default budget for example dialogue (config: characters.example_tokens)
BOOK_TOKENS = 500                          # default budget for lorebook entries (config: characters.book_tokens)
BOOK_DEPTH = 4                             # messages scanned for keys when the book does not say (characters.book_scan_depth)
_START = re.compile(r"^\s*<start>\s*$", re.IGNORECASE | re.MULTILINE)
_CHAR = re.compile(r"\{\{\s*char\s*\}\}|<bot>", re.IGNORECASE)
_USER = re.compile(r"\{\{\s*user\s*\}\}|<user>", re.IGNORECASE)
_ORIGINAL = re.compile(r"\{\{\s*original\s*\}\}", re.IGNORECASE)
CARD_FIELDS = ("description", "personality", "scenario", "first_mes", "mes_example", "system_prompt",
               "post_history_instructions")


class CharacterError(ValueError):
    """Something the user can fix: the message is safe to show."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def originals_dir(store) -> Path:
    return Path(store.root).parent / "characters"


def _problem(card: dict) -> str | None:
    """The same hard rules as every avatar card, over every text field (creator notes included: they are shown)."""
    book = card.get("book") or {}
    texts = [card.get("name") or "", card.get("creator_notes") or "", *[card.get(f) or "" for f in CARD_FIELDS],
             *(card.get("alternate_greetings") or []), *(card.get("tags") or []), book.get("name") or "",
             *[text for entry in book.get("entries") or []
               for text in (entry["name"], entry["content"], *entry["keys"], *entry["secondary_keys"])]]
    for text in texts:
        found = safety.hard_problem(text)
        if found:
            return f"This character cannot be imported: {found[0].lower()}{found[1:]}"
    return None


def _new_id(store, name) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:24].strip("-") or "character"
    for _ in range(20):
        avatar_id = f"chr-{slug}-{secrets.token_hex(3)}"
        if valid_id(avatar_id) and not (store.root / avatar_id).exists():
            return avatar_id
    raise CharacterError("Could not choose a folder for the character. Try again.", 500)


def read_package(filename, data, *, include_voice=True):
    if len(data) > charcard.file_limit(filename):
        raise CharacterError(f"The card is larger than {charcard.file_limit(filename) // 1048576} MB.", 413)
    try:
        package = charcard.read_card(filename, data, include_voice=include_voice)
    except charcard.CardError as error:
        raise CharacterError(str(error)) from None
    problem = _problem(package.card)
    if problem:
        raise CharacterError(problem)
    if package.sheet is not None:
        sheet_fields(package)
    return package


def sheet_fields(package, name=None, age=None):
    sheet = package.sheet
    if not sheet:
        raise CharacterError("This card has no character sheet. Export a CHARX card from NPC Forge.")
    if sheet.get("setting") == "models" or sheet.get("source_photo"):
        raise CharacterError("Choose a character from a core NPC Forge setting.")
    profile, appearance = sheet.get("profile"), sheet.get("appearance") or {}
    if (not isinstance(profile, dict) or not isinstance(appearance, dict)
            or any(type(sheet[key]) is not str for key in ("setting", "style") if key in sheet)
            or any(type(profile[key]) is not list or any(type(item) is not str for item in profile[key])
                   for key in ("personality", "hobbies") if key in profile)):
        raise CharacterError("The card has an invalid character sheet. Export a new copy.")
    try:
        original = card_from_npc(sheet, name=package.card["name"])
        problem = safety.card_problem(original)
        fields = card_from_npc(sheet, name=name or package.card["name"], age=age)
        problem = problem or safety.card_problem(fields)
    except (AvatarError, TypeError, ValueError, AttributeError, OverflowError):
        raise CharacterError("The card has an invalid character sheet. Export a new copy.") from None
    if problem:
        raise CharacterError(problem)
    return fields


_LICENCES = {"commercial_ok": "Commercial use OK", "commercial_conditions": "Commercial use with conditions",
             "not_cleared": "Commercial use unclear", "non_commercial": "Non-commercial use only"}


def suite_details(package):
    fels = package.card.get("fels")
    if fels is None:
        return {}
    rating = fels.get("licence", {}).get("rating")
    return {"ai_generated": fels["ai_generated"], "licence": _LICENCES.get(rating, "Commercial use unclear")
            if isinstance(rating, str) else "Commercial use unclear"}


def voice_offer(package):
    from core.voicelab import valid_name
    voice = package.card.get("fels", {}).get("voice")
    if not voice or not valid_name(voice.get("name")) or not valid_name(voice.get("model")):
        return {}
    return {"name": voice["name"], "model": voice["model"],
            "preset": voice["preset"] if valid_name(voice.get("preset")) else "",
            "archive": package.voice_file is not None, "cloned": voice.get("cloned_from_person") is True}


def import_card(store, filename: str, data: bytes, checker=None, *, living=None, package=None, inbox_key=None) -> dict:
    """Save an imported character and its original locally."""
    package = package or read_package(filename, data)
    card = package.card
    fields = sheet_fields(package) if package.sheet is not None else {}
    avatar_id = _new_id(store, card["name"])
    record = {
        "id": avatar_id, "name": card["name"], "kind": "character",
        "card": {**fields, "traits": fields.get("personality", []), "kind": "character", "name": card["name"],
                 **{f: card[f] for f in CARD_FIELDS}, "alternate_greetings": card["alternate_greetings"]},
        "creator_notes": card["creator_notes"], "creator": card["creator"], "tags": card["tags"],
        "source": {"type": "character", "file": avatar_id + package.extension, "spec": card["spec"],
                   "version": card["character_version"], **({"inbox_key": inbox_key} if inbox_key else {})},
        "voice": "", "features": dict.fromkeys(FEATURES, True), "card_instructions": True,
        "book": card["book"], "lorebook": True, "card_voice": voice_offer(package),
    }
    return _save_card(store, record, package, data, checker, living)


def import_forge_card(store, npc_id, data, *, checker=None, living=None, name=None, age=None):
    package = read_package(npc_id + ".charx", data, include_voice=False)
    if not package.portrait:
        raise CharacterError("Make a portrait for this character in NPC Forge and try again.")
    fields = sheet_fields(package, name=name, age=age)
    old = store.get(npc_id) or {}
    record = {**old, "id": npc_id, "name": fields["name"], "kind": kind_of(old), "card": fields,
              "source": {"type": "npc-forge-card", "id": npc_id, "file": npc_id + ".charx"}}
    return _save_card(store, record, package, data, checker, living)


def import_from_forge(services, npc_id, *, name=None, age=None):
    from core import forge
    store = services.avatars
    if not valid_id(npc_id):
        raise CharacterError("Choose a character from NPC Forge's library.")
    mode = store.forge_mode()
    if mode == "cards":
        return import_forge_card(store, npc_id, store.forge_client().card(npc_id), checker=services.checker,
                                 living=services.living, name=name, age=age)
    if mode != "legacy":
        raise forge.ForgeError("Start an up-to-date NPC Forge to fetch the character card.")
    record = store.import_from_forge(npc_id, name=name, age=age, checker=services.checker)
    services.living.sync_async(record["id"])
    return record


def _save_card(store, record, package, data, checker, living):
    avatar_id = record["id"]
    image = package.portrait
    check = {}
    if image is not None and checker is not None:
        check = checker.check(image)
        if check.get("ok") is False:
            raise CharacterError(f"The card's picture looks younger than {safety.MIN_APPARENT_AGE} "
                                 f"(estimated {check.get('apparent_age')}), so this character cannot be imported.")
    body = package.body
    if body is not None and checker is not None and check.get("ok") is not None and checker.check(body).get("ok") is False:
        body = None
    folder = store.root / avatar_id
    fresh = not folder.exists()
    folder.mkdir(parents=True, exist_ok=True)
    originals = originals_dir(store)
    original = originals / (avatar_id + package.extension)
    try:
        originals.mkdir(parents=True, exist_ok=True)
        if image is not None:
            (folder / "portrait.png").write_bytes(image)
        if body is not None:
            (folder / "body.png").write_bytes(body)
        else:
            (folder / "body.png").unlink(missing_ok=True)
        if living is not None and image is not None:
            living.forget(avatar_id)
            living.import_frames(avatar_id, package, {sha256(image).hexdigest(): check})
        original.write_bytes(data)
        record.update(imported=_now(), has_body=body is not None, **suite_details(package),
                      age_check={k: check.get(k) for k in ("ok", "apparent_age", "model")})
        _write_json(folder / "avatar.json", record)
    except Exception:
        if fresh:
            original.unlink(missing_ok=True)
            shutil.rmtree(folder)
        raise
    if store.active_id() is None:
        store.activate(avatar_id)
    return store.public(record)


def delete_original(store, avatar_id) -> None:
    """Remove the saved card file with the character (Remove means for good)."""
    if not valid_id(avatar_id):
        return
    for extension in (".png", ".json", ".charx"):
        try:
            (originals_dir(store) / (avatar_id + extension)).unlink()
        except FileNotFoundError:
            pass


# ---- settings ---------------------------------------------------------------------------------------------------------
def _character(store, avatar_id) -> dict:
    record = store.get(avatar_id)
    if not record or record.get("kind") != "character":
        raise CharacterError("That character was not found.", 404)
    return record


def settings(store, avatar_id) -> dict:
    record = _character(store, avatar_id)
    from core.avatars import features_of
    book = book_of(store, record) or {}
    entries = book.get("entries") or []
    return {"id": avatar_id, "name": record.get("name"), "voice": record.get("voice") or "", "features": features_of(record),
            "licence": record.get("licence"), "ai_generated": record.get("ai_generated") is True,
            "card_voice": record.get("card_voice") or {},
            "card_instructions": record.get("card_instructions") is not False,
            "lorebook": lorebook_on(record),
            "book": {"name": book.get("name") or "", "entries": [
                {"name": e["name"] or ", ".join(e["keys"][:3]) or "(always on)", "keys": e["keys"][:8],
                 "constant": e["constant"], "enabled": e["enabled"], "regex": e["regex"]} for e in entries]},
            "has_instructions": bool(record["card"].get("system_prompt") or record["card"].get("post_history_instructions")),
            "creator_notes": record.get("creator_notes") or "", "creator": record.get("creator") or "",
            "spec": (record.get("source") or {}).get("spec") or ""}


def update(store, avatar_id, *, voice=None, features=None, card_instructions=None, lorebook=None) -> dict:
    record = _character(store, avatar_id)
    if voice is not None:
        from core import pals, tts
        voice = str(voice).strip()
        if not pals.valid_voice(voice, tts.settings()["engine"]):
            raise CharacterError("Choose one of the listed voices, or the default voice.")
        record["voice"] = voice
    if features is not None:
        if not isinstance(features, dict) or not set(features) <= set(FEATURES) or any(type(v) is not bool for v in features.values()):
            raise CharacterError("features may only switch memory, notes, story and presence on or off.")
        from core.avatars import features_of
        record["features"] = {**features_of(record), **features}
    if card_instructions is not None:
        if type(card_instructions) is not bool:
            raise CharacterError("card_instructions must be true or false.")
        record["card_instructions"] = card_instructions
    if lorebook is not None:
        if type(lorebook) is not bool:
            raise CharacterError("lorebook must be true or false.")
        record["lorebook"] = lorebook
    _write_json(store.root / avatar_id / "avatar.json", record)
    return settings(store, avatar_id)


# ---- using a card ----------------------------------------------------------------------------------------------------
def user_name() -> str:
    try:
        from core.profile import load_user_profile
        name = str(load_user_profile().get("name") or "").strip()
    except Exception:
        name = ""
    return name[:80] or "User"


def render(text: str, char: str, user: str) -> str:
    """The card's text with {{char}}/<BOT> and {{user}}/<USER> filled in."""
    text = _CHAR.sub(lambda _: char, text or "")
    return _USER.sub(lambda _: user, text)


def instructions_on(record) -> bool:
    return (record or {}).get("card_instructions") is not False


def examples(card: dict, char: str, user: str, budget: int = EXAMPLE_TOKENS) -> str:
    """The card's example dialogue, whole <START> blocks first, cut to the token budget (a block that does not fit is cut
    at a line). '' when there is none or the budget is 0."""
    raw = card.get("mes_example") or ""
    if not raw.strip() or budget <= 0:
        return ""
    blocks = [render(block.strip(), char, user) for block in _START.split(raw) if block.strip()]
    kept, used = [], 0
    for block in blocks:
        cost = estimate_tokens(block)
        if used + cost <= budget:
            kept.append(block)
            used += cost
            continue
        lines = []
        for line in block.splitlines():
            cost = estimate_tokens(line)
            if used + cost > budget:
                break
            lines.append(line)
            used += cost
        if lines:
            kept.append("\n".join(lines))
        break
    return "\n\n".join(kept)


def example_budget(config) -> int:
    try:
        return max(0, min(4000, int(((config or {}).get("characters") or {}).get("example_tokens", EXAMPLE_TOKENS))))
    except (TypeError, ValueError):
        return EXAMPLE_TOKENS


def persona(card: dict, record: dict, config=None) -> str:
    """The persona part of the system prompt for a character. creator_notes are never read here."""
    name = card.get("name") or "the character"
    user = user_name()
    parts = []
    system_prompt = render(card.get("system_prompt") or "", name, user).strip()
    if system_prompt and instructions_on(record):
        parts.append(_ORIGINAL.sub("", system_prompt).strip())
    parts.append(f"You are {name}. Stay in character as {name} for this whole conversation: speak, think and react as "
                 f"{name} would, in {name}'s own voice. The person you are talking with is {user}.")
    for title, field in (("Who You Are", "description"), ("Personality", "personality"), ("Scenario", "scenario")):
        text = render(card.get(field) or "", name, user).strip()
        if text:
            parts.append(f"## {title}\n{text}")
    for label, value in (("Occupation", card.get("occupation")), ("Background", card.get("bio")),
                         ("Traits", ", ".join(card.get("traits") or ())), ("Speech", card.get("speech")),
                         ("Manner", card.get("demeanor")), ("Quirk", card.get("quirk")), ("Goal", card.get("goal")),
                         ("Pet peeve", card.get("peeve")), ("Hobbies", ", ".join(card.get("hobbies") or ()))):
        if value:
            parts.append(f"{label}: {render(value, name, user)}")
    shown = examples(card, name, user, example_budget(config))
    if shown:
        parts.append("## How You Talk (example dialogue, not part of this conversation)\n" + shown)
    return "\n\n".join(parts)


def first_message(record: dict) -> str:
    card = (record or {}).get("card") or {}
    return render(card.get("first_mes") or "", card.get("name") or record.get("name") or "", user_name()).strip()


def post_history(record: dict) -> str:
    """Instructions the card wants after the conversation, or '' (none, or switched off)."""
    card = (record or {}).get("card") or {}
    if not instructions_on(record):
        return ""
    return render(card.get("post_history_instructions") or "", card.get("name") or "", user_name()).strip()


# ---- the lorebook (character_book) ------------------------------------------------------------------------------------
def lorebook_on(record) -> bool:
    return (record or {}).get("lorebook") is not False


def book_of(store, record) -> dict | None:
    """The character's lorebook. A character imported before Altera read lorebooks has its book read once from the card
    file saved at import (the same hard rules apply: a book that breaks them is not used)."""
    if not record or record.get("kind") != "character":
        return None
    if "book" in record:
        return record["book"] if isinstance(record["book"], dict) else None
    book = None
    path = originals_dir(store) / ((record.get("source") or {}).get("file") or "")
    if valid_id(record.get("id")) and path.is_file() and path.parent == originals_dir(store):
        try:
            data = path.read_bytes()
            card = charcard.parse_png(data)[0] if data.startswith(charcard.PNG_SIGNATURE) else charcard.parse_json(data)
            book = card.get("book") if not _problem(card) else None
        except (charcard.CardError, OSError):
            book = None
    record["book"] = book
    _write_json(store.root / record["id"] / "avatar.json", record)
    return book


def book_settings(config, book) -> tuple:
    """(token budget, messages scanned): the card's own when it gives them, never more than Altera's settings allow."""
    cfg = (config or {}).get("characters") or {}

    def number(key, default, high):
        try:
            return max(0, min(high, int(cfg.get(key, default))))
        except (TypeError, ValueError):
            return default
    budget, depth = number("book_tokens", BOOK_TOKENS, 4000), number("book_scan_depth", BOOK_DEPTH, 50)
    if (book or {}).get("token_budget"):
        budget = min(budget, book["token_budget"])
    if (book or {}).get("scan_depth"):
        depth = min(depth, book["scan_depth"]) if depth else 0
    return budget, depth


def _found(key: str, text: str, case_sensitive: bool) -> bool:
    return re.search(r"(?<!\w)" + re.escape(key) + r"(?!\w)", text, 0 if case_sensitive else re.IGNORECASE) is not None


def triggered(book, texts, budget) -> list:
    """The entries the recent messages call up, as the card ordered them: always-on entries and those with a key in the
    text (and, for a selective entry, a secondary key too), chosen always-on first then by the card's order until the token
    budget is spent. Switched-off and regex entries are never used."""
    text = "\n".join(texts or [])
    called = []
    for entry in (book or {}).get("entries") or []:
        if not entry["enabled"] or entry["regex"]:
            continue
        if not entry["constant"]:
            if not any(_found(key, text, entry["case_sensitive"]) for key in entry["keys"]):
                continue
            if entry["selective"] and entry["secondary_keys"] and \
                    not any(_found(key, text, entry["case_sensitive"]) for key in entry["secondary_keys"]):
                continue
        called.append(entry)
    chosen, used = [], 0
    for entry in sorted(called, key=lambda e: (not e["constant"], e["order"])):
        cost = estimate_tokens(entry["content"])
        if used + cost > budget:
            continue                                   # a smaller one further down may still fit
        chosen.append(entry)
        used += cost
    return sorted(chosen, key=lambda e: e["order"])


def lore(store, record, texts, config=None) -> str:
    """The lorebook section of the system prompt for these recent messages, or '' (none called up, or switched off)."""
    if not record or record.get("kind") != "character" or not lorebook_on(record):
        return ""
    book = book_of(store, record)
    budget, depth = book_settings(config, book)
    if not book or budget <= 0 or depth <= 0:
        return ""
    entries = triggered(book, list(texts or [])[-depth:], budget)
    if not entries:
        return ""
    name = (record.get("card") or {}).get("name") or record.get("name") or "the character"
    user = user_name()
    body = "\n\n".join(render(entry["content"], name, user).strip() for entry in entries)
    return ("## What You Know (from the lorebook: facts of this story that the conversation just touched on; use them "
            "where they fit, never recite them)\n" + body)
