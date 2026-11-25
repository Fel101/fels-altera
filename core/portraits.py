"""
Altera — Forge portraits for pals and characters.

A pal has no picture of its own (Home draws its initial), and a character has the card's picture or none. NPC Forge can paint
one, in two ways:

  from a description   the words are all NPC Forge draws from (a character's are picked out of its card, and can be edited)
  from the card        the card's picture is sent as the photo NPC Forge redraws: the face is kept, the rest is redrawn in the
                       chosen art style

Either way NPC Forge makes a character of its own in its library and paints it, and Altera waits on the job, then copies the
portrait in. A character already in NPC Forge's library can lend its portrait instead (from_library), and anyone here can
have a picture uploaded (core.avatars.set_uploaded_portrait). Every portrait goes through the age check first, like every picture; one that came in while the check could
not run (the model was not running) is checked later, in the background, and taken down if it reads as under age. The
card's text stays what the character says and does; only the picture comes from NPC Forge. The card's own picture is kept
(card_picture.png) so it can be used again.

Once a pal or character has a Forge portrait, NPC Forge can make its living portrait too (expressions, then the frames), and
core/living.py copies those frames as it does for companions: through the `forge` link, not `source`, which stays the card or
pal file.

On the record (avatar.json):
  forge         {id, made, from, description, style, setting, gender, image_model, edit_model}
                the NPC Forge character the portrait came from, and the engines chosen ('' for NPC Forge's own default)
  portrait_from "forge" | "card" | "upload" | "none"     (unset: the card's picture if there is one, else none)
  forge_job     {kind: portrait|living, npc_id, job_id, started, ...}  while NPC Forge is working on one
  card_age_check  the card picture's age check, put back with the card's picture
  portrait_removed  {at, from, apparent_age}: the last portrait was taken down because the later age check read it as under age
"""
import logging
import re
import shutil
import threading
import time
from pathlib import Path

from core import forge, safety
from core.avatars import _now, _write_json, kind_of, valid_id, written_here

LOGGER = logging.getLogger(__name__)

KINDS = ("pal", "character")
CARD_PICTURE = "card_picture.png"
SOURCES = ("description", "card")
DEFAULT_SETTING, DEFAULT_STYLE = "modern", "semi_real"

# Sentences of a card that say how someone looks. The words are kept as written; this only chooses which sentences to keep.
_LOOKS = re.compile(
    r"\b(hair(?:ed)?|eyes?|eyed|skin|complexion|freckle[sd]?|tall|short|height|petite|slender|slim|lanky|muscular|curvy|"
    r"stocky|build|figure|physique|wears?|wearing|dressed|dress|outfit|clothes|clothing|jacket|coat|shirt|blouse|skirt|"
    r"trousers|jeans|boots|hoodie|uniform|armou?r|robes?|glasses|spectacles|beard|moustache|mustache|scars?|tattoos?|"
    r"piercings?|horns?|tail|wings?|ears|fangs|face|lips|appearance|looks like|features)\b", re.IGNORECASE)
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")


class PortraitError(ValueError):
    """Something the person can act on: the message is safe to show."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _person(store, avatar_id) -> dict:
    """The person whose portrait this is. Pals, characters and companions written in Altera get one here; a companion
    imported from NPC Forge already has its portrait from its source character."""
    record = store.get(avatar_id) if valid_id(avatar_id) else None
    if not record or (kind_of(record) not in KINDS and not written_here(record)):
        raise PortraitError("Only pals, characters and companions written here get their portrait here.", 404)
    return record


def _save(store, record) -> None:
    _write_json(store.root / record["id"] / "avatar.json", record)


def card_picture(store, record) -> Path | None:
    """The card's own picture: kept aside once a Forge portrait replaced it, else the portrait itself (a .png card)."""
    if kind_of(record) != "character":
        return None
    kept = store.root / record["id"] / CARD_PICTURE
    if kept.is_file():
        return kept
    if record.get("portrait_from") not in ("forge", "upload"):
        return store.image_path(record["id"], "portrait")
    return None


def portrait_from(store, record) -> str:
    """Where the picture came from: "forge" (NPC Forge painted it, or it was chosen from NPC Forge's library), "card" (an
    imported character card's picture), "upload" (a picture the person uploaded) or "none" (the initial is shown)."""
    if record.get("portrait_from") == "forge" and store.image_path(record["id"], "portrait"):
        return "forge"
    if not store.image_path(record["id"], "portrait"):
        return "none"
    if record.get("portrait_from") == "upload":
        return "upload"
    if kind_of(record) == "character":
        return "card"
    if kind_of(record) == "companion":
        return "forge" if (record.get("source") or {}).get("type") == "npc-forge" else "upload"
    return "none"


def looks_from_card(record) -> str:
    """The sentences of a character's description (and prose personality) that say how they look, as written. A companion
    written here has no separate look field, so its background is read the same way."""
    from core.characters import render, user_name
    card = record.get("card") or {}
    kind = kind_of(record)
    if kind == "character":
        fields = ("description", "personality")
    elif written_here(record):
        fields = ("look", "bio")
    else:
        return ""
    name = card.get("name") or record.get("name") or ""
    text = "\n".join(str(card.get(field) or "") for field in fields if isinstance(card.get(field), str))
    kept, used = [], 0
    for sentence in _SENTENCE.split(render(text, name, user_name())):
        sentence = sentence.strip(" \t-*[](){}")
        if not sentence or not _LOOKS.search(sentence) or sentence in kept:
            continue
        if used + len(sentence) + 1 > forge.MAX_DESCRIPTION:
            break
        kept.append(sentence)
        used += len(sentence) + 1
    return " ".join(kept)


def summary(store, record, living=None) -> dict:
    """What the settings dialog shows: where the portrait came from, what can be done, and the words to start from."""
    made = record.get("forge") if isinstance(record.get("forge"), dict) else {}
    job = record.get("forge_job") if isinstance(record.get("forge_job"), dict) else None
    current = portrait_from(store, record)
    character = kind_of(record) == "character"
    written = written_here(record)
    card = record.get("card") or {}
    description = made.get("description") or (looks_from_card(record) if character or written else "")
    gender = made.get("gender") or (card.get("gender") if written else "")
    alive = False
    if living is not None and current == "forge":
        try:
            alive = bool(living.status(record["id"]).get("available"))
        except Exception:
            alive = False
    return {
        "id": record["id"], "kind": kind_of(record), "name": record.get("name") or "",
        "from": current, "card_picture": card_picture(store, record) is not None,
        "job": {"kind": job.get("kind"), "started": job.get("started")} if job else None,
        "living": alive, "can_bring_to_life": current == "forge" and not job,
        "can_upload": not job,
        "description": description, "gender": gender,
        **({key: made[key] for key in ("age", "age_source") if key in made} if current == "forge" else {}),
        "style": made.get("style") or DEFAULT_STYLE, "setting": made.get("setting") or DEFAULT_SETTING,
        "image_model": made.get("image_model") or "", "edit_model": made.get("edit_model") or "",
        "age_checked": _age_state(store, record), "removed": _removed(record),
    }


def _age_state(store, record) -> str:
    """'passed', 'unchecked' (it came in while the check could not run: it is checked later) or 'none' (no portrait)."""
    if not store.image_path(record["id"], "portrait"):
        return "none"
    return "passed" if (record.get("age_check") or {}).get("ok") is True else "unchecked"


def _removed(record) -> dict | None:
    gone = record.get("portrait_removed")
    if not isinstance(gone, dict):
        return None
    return {"at": gone.get("at"), "from": gone.get("from"), "apparent_age": gone.get("apparent_age")}


def describe_with_model(record, llm) -> str:
    """Ask the chat model to write how a character looks from its card, for the description box. '' when the card says nothing
    about looks. A companion written here has no separate look field, so its background is read the same way."""
    from core.characters import render, user_name
    card = record.get("card") or {}
    if kind_of(record) == "character":
        fields = ("description", "personality", "scenario")
    elif written_here(record):
        fields = ("bio", "look")
    else:
        raise PortraitError("Only a character or a companion written here has words to describe them from.")
    name = card.get("name") or record.get("name") or "the character"
    text = render("\n\n".join(str(card.get(f) or "") for f in fields if isinstance(card.get(f), str)),
                  name, user_name()).strip()[:6000]
    if not text:
        return ""
    prompt = (f"Below is a description of a character called {name}. Write how {name} looks, for a portrait painter: face, "
              f"hair, eyes, skin, build, clothes and anything distinctive, as one short paragraph of at most 80 words. Use "
              f"only what the text says; leave out personality, history and anything that is not seen. If the text says "
              f"nothing about how {name} looks, answer only NONE.\n\n---\n{text}")
    try:
        answer = llm.chat([{"role": "system", "content": "You describe how characters look. Answer with the description only."},
                           {"role": "user", "content": prompt}], stream=False)
    except Exception:
        raise PortraitError("The model did not answer. Check it is running, or write the description yourself.", 503) from None
    answer = re.sub(r"<think>.*?</think>", "", str(answer or ""), flags=re.DOTALL).strip().strip('"').strip()
    if not answer or answer.upper().startswith("NONE"):
        return ""
    if safety.hard_problem(answer):
        return ""
    return answer[:forge.MAX_DESCRIPTION]


# ---- painting -----------------------------------------------------------------------------------------------------------
def start(store, avatar_id, client, *, source, description="", style=DEFAULT_STYLE, setting=DEFAULT_SETTING, gender="",
          image_model="", edit_model="", living=None) -> dict:
    """Ask NPC Forge for a portrait. Returns the summary, with the job recorded on the person."""
    record = _person(store, avatar_id)
    if record.get("forge_job"):
        raise PortraitError("NPC Forge is already working on a picture for them. Wait for it, or cancel it.", 409)
    if source not in SOURCES:
        raise PortraitError("Choose whether to paint from a description or from the card's picture.")
    if not all(isinstance(v, str) for v in (description, style, setting, gender, image_model, edit_model)):
        raise PortraitError("description, style, setting, gender and the engines are text.")
    description = description.strip()
    if len(description) > forge.MAX_DESCRIPTION:
        raise PortraitError(f"Keep the description under {forge.MAX_DESCRIPTION} characters.")
    if gender and gender not in forge.GENDERS:
        raise PortraitError("Choose one of the listed genders, or leave it to the description.")
    if source == "description" and not description:
        raise PortraitError("Describe how they look first.")
    found = safety.hard_problem(description) if description else None
    if found:
        raise PortraitError(f"This description cannot be used: {found[0].lower()}{found[1:]}")
    photo = None
    if source == "card":
        picture = card_picture(store, record)
        if not picture:
            raise PortraitError("This character's card has no picture. Paint from a description instead.")
        photo = picture.read_bytes()
    if not store.forge_library_available():
        raise PortraitError("Start NPC Forge with character card support, then try again.", 503)
    try:
        made = client.paint(name=record.get("name") or "", description=description, gender=gender, style=style,
                            setting=setting, photo=photo, image_model=image_model, edit_model=edit_model)
    except forge.ForgeError as error:
        raise PortraitError(str(error), 503) from None
    record["forge_job"] = {"kind": "portrait", "npc_id": made["npc_id"], "job_id": made["job_id"], "started": _now(),
                           "from": source, "description": description, "style": style, "setting": setting, "gender": gender,
                           "image_model": image_model, "edit_model": edit_model,
                           **{key: made[key] for key in ("age", "age_source") if key in made}}
    _save(store, record)
    return summary(store, record, living)


def bring_to_life(store, avatar_id, client, living=None, edit_model=None) -> dict:
    """Ask NPC Forge for the living portrait of the character it painted for this person, with `edit_model` (None: the one
    chosen for the portrait; '': NPC Forge's own default). The choice is remembered for next time."""
    record = _person(store, avatar_id)
    if record.get("forge_job"):
        raise PortraitError("NPC Forge is already working on a picture for them. Wait for it, or cancel it.", 409)
    npc_id = (record.get("forge") or {}).get("id") if portrait_from(store, record) == "forge" else None
    if not npc_id:
        raise PortraitError("Make a portrait in NPC Forge first: the living portrait is made from it.", 409)
    if edit_model is None:
        edit_model = record["forge"].get("edit_model") or ""
    if not isinstance(edit_model, str):
        raise PortraitError("edit_model is text.")
    try:
        job_id = client.living(npc_id, edit_model=edit_model)
    except forge.ForgeError as error:
        raise PortraitError(str(error), 503) from None
    record["forge"]["edit_model"] = edit_model
    record["forge_job"] = {"kind": "living", "npc_id": npc_id, "job_id": job_id, "started": _now()}
    _save(store, record)
    return summary(store, record, living)


def check(store, avatar_id, client, checker=None, living=None) -> dict:
    """Where NPC Forge's job is: {state: none|queued|running|ready|rejected|error|cancelled|unreachable, note, portrait}.
    A finished portrait is age-checked and brought in; a finished living portrait is copied (core/living.py)."""
    record = _person(store, avatar_id)
    job = record.get("forge_job")
    if not isinstance(job, dict):
        return {"state": "none", "note": "", "portrait": summary(store, record, living)}
    try:
        found = client.job(job.get("job_id"))
    except forge.ForgeError as error:                     # not running just now: the job is kept, to look again later
        return {"state": "unreachable", "note": str(error), "portrait": summary(store, record, living)}
    state, note = found.get("state"), found.get("note") or ""
    if state in ("queued", "running") and found.get("blocked"):   # an NPC Forge before API 44 goes on to the next step
        client.cancel(job.get("job_id"))                  # every step after would wait for the GPU and be refused the same way
        state, note = "error", f"Stopped: NPC Forge needs the GPU to itself. {found['blocked']}"
    if state == "error" and found.get("code") == "gpu_busy" and not job.get("retried"):
        try:
            client.retry_gpu_job(job.get("job_id"))
        except forge.ForgeError as error:
            note = str(error)
        else:
            record["forge_job"] = {**job, "retried": True}
            _save(store, record)
            return {"state": "running", "note": RETRYING, "portrait": summary(store, record, living)}
    if state in ("queued", "running"):
        return {"state": state, "note": note, "portrait": summary(store, record, living)}
    record.pop("forge_job", None)
    if state == "done" and job.get("kind") == "portrait":
        state, note = _bring_in(store, record, job, checker, living)
    elif state == "done":
        state, note = "ready", ""
        if living is not None:
            living.sync_async(record["id"])               # the frames are age-checked as they are copied
            if not living.status(record["id"]).get("available"):
                note = COPYING                            # still copying: the age check is a model call per frame
    elif state not in ("error", "cancelled"):
        state, note = "error", note or "NPC Forge stopped without saying why."
    _save(store, record)
    return {"state": state, "note": note, "portrait": summary(store, record, living)}


def _bring_in(store, record, job, checker, living):
    package = None
    npc_id = job.get("npc_id")
    if store.forge_mode() == "cards":
        from core import characters
        try:
            package = characters.read_package(f"{npc_id}.charx", store.forge_client().card(npc_id), include_voice=False)
            if package.sheet is not None:
                fields = characters.sheet_fields(package)
                job = {**job, **{key: fields[key] for key in ("age", "age_source") if key in fields}}
        except (forge.ForgeError, characters.CharacterError) as error:
            return "error", str(error)
        png = package.portrait
    else:
        path = store.forge_file(npc_id, "portrait")
        png = path.read_bytes() if path else None
    if not png:
        return "error", "NPC Forge has no portrait for this character. Open it in Forge and try again."
    result = checker.check(png) if checker else {"ok": None, "apparent_age": None, "model": None}
    if result.get("ok") is False:
        return "rejected", (f"The portrait looks younger than {safety.MIN_APPARENT_AGE} (estimated "
                            f"{result.get('apparent_age')}), so it was not used. Try a different description.")
    folder = store.root / record["id"]
    portrait = folder / "portrait.png"
    if kind_of(record) == "character" and record.get("portrait_from") not in ("forge", "upload") and portrait.is_file() \
            and not (folder / CARD_PICTURE).is_file():
        shutil.copyfile(portrait, folder / CARD_PICTURE)    # the card's own picture, to go back to
        record["card_age_check"] = record.get("age_check")
    portrait.write_bytes(png)
    record["forge"] = {"id": job.get("npc_id"), "made": _now(), **{k: job.get(k) or "" for k in
                                                                   ("from", "description", "style", "setting", "gender",
                                                                    "image_model", "edit_model")},
                       **{key: job[key] for key in ("age", "age_source") if key in job}}
    record["portrait_from"] = "forge"
    record.pop("portrait_removed", None)
    record["age_check"] = {"ok": result.get("ok"), "apparent_age": result.get("apparent_age"), "model": result.get("model")}
    if living is not None:
        living.forget(record["id"])                       # frames of an earlier portrait do not fit this one
        if package is not None:
            from hashlib import sha256
            living.import_frames(record["id"], package, {sha256(png).hexdigest(): result})
    return "ready", ""


def cancel(store, avatar_id, client, living=None) -> dict:
    record = _person(store, avatar_id)
    job = record.pop("forge_job", None)
    if isinstance(job, dict):
        client.cancel(job.get("job_id"))
        _save(store, record)
    return summary(store, record, living)


def stop_using(store, avatar_id, living=None) -> dict:
    """Go back to the card's picture (a character) or the initial (a pal, or a card without a picture)."""
    record = _person(store, avatar_id)
    if record.get("forge_job"):
        raise PortraitError("NPC Forge is still working on a picture for them. Wait for it, or cancel it.", 409)
    if portrait_from(store, record) != "forge":
        raise PortraitError("They are not using a portrait from NPC Forge.", 409)
    folder = store.root / record["id"]
    kept = folder / CARD_PICTURE
    if kind_of(record) == "character" and kept.is_file():
        shutil.copyfile(kept, folder / "portrait.png")
        record["portrait_from"] = "card"
        record["age_check"] = record.get("card_age_check") or record.get("age_check")
    else:
        (folder / "portrait.png").unlink(missing_ok=True)
        record["portrait_from"] = "none"
        record["age_check"] = {"ok": None, "apparent_age": None, "model": None}
    _save(store, record)
    if living is not None:
        living.forget(record["id"])
    return summary(store, record, living)


def from_library(store, avatar_id, npc_id, checker=None, living=None) -> dict:
    """Use the portrait of a character already in NPC Forge's library. Nothing is painted: the portrait is age-checked and
    copied in exactly as a newly painted one is, and the living portrait can be made from that character afterwards."""
    record = _person(store, avatar_id)
    if record.get("forge_job"):
        raise PortraitError("NPC Forge is still working on a picture for them. Wait for it, or cancel it.", 409)
    if not store.forge_library_available():
        raise PortraitError("Start NPC Forge with character card support, then try again.", 503)
    try:
        row = next((r for r in store.forge_choices() if r["id"] == npc_id), None) if valid_id(npc_id) else None
    except forge.ForgeError as error:
        raise PortraitError(str(error), 503) from None
    if not row:
        raise PortraitError("That character was not found in NPC Forge's library.", 404)
    if not row["has_portrait"]:
        raise PortraitError("They have no portrait yet. Make one in NPC Forge first.", 409)
    if row["reason"]:
        raise PortraitError(row["reason"], 422)
    job = {"npc_id": npc_id, "from": "library",
           **({"age": row["age"], "age_source": row.get("age_source")} if row["age"] is not None else {})}
    state, note = _bring_in(store, record, {k: v for k, v in job.items() if v is not None}, checker, living)
    if state != "ready":
        raise PortraitError(note, 422)
    _save(store, record)
    return summary(store, record, living)


def remove_uploaded(store, avatar_id, living=None) -> dict:
    """Take away a picture the person uploaded: a character's card picture comes back, anyone else's initial. A Forge
    portrait is removed with stop_using instead."""
    record = _person(store, avatar_id)
    if record.get("forge_job"):
        raise PortraitError("NPC Forge is still working on a picture for them. Wait for it, or cancel it.", 409)
    if portrait_from(store, record) != "upload":
        raise PortraitError("There is no uploaded portrait to remove.", 409)
    folder = store.root / record["id"]
    kept = folder / CARD_PICTURE
    if kind_of(record) == "character" and kept.is_file():
        shutil.copyfile(kept, folder / "portrait.png")
        record["portrait_from"] = "card"
        record["age_check"] = record.get("card_age_check") or {"ok": None, "apparent_age": None, "model": None}
    else:
        (folder / "portrait.png").unlink(missing_ok=True)
        record["portrait_from"] = "none"
        record["age_check"] = {"ok": None, "apparent_age": None, "model": None}
    _save(store, record)
    if living is not None:
        living.forget(record["id"])
    return summary(store, record, living)


# ---- the age check, later, for a portrait that came in without one ------------------------------------------------------
# A portrait painted (or a card imported) while the model was not running came in unchecked. It is checked when the model can
# look at it: in the background, one at a time, started when Home lists everyone, someone is opened, or their settings open.

RETRYING = "The GPU is available again. NPC Forge is carrying on from where it stopped."
COPYING = ("NPC Forge made the living portrait. Altera is age-checking its frames and copying them in, which can take a "
           "few minutes; the chat shows it once that is done.")
RECHECK_AFTER = 600            # seconds before a portrait the model gave no answer about is tried again
RECHECK_INLINE = False         # tests: check in the calling thread
_recheck_lock = threading.Lock()
_rechecking: set = set()       # (store root, avatar id) being checked now
_no_answer: dict = {}          # (store root, avatar id) -> time.monotonic() of the last try without an answer


def unchecked(store, record) -> bool:
    """A pal's, character's or written companion's portrait the age check has not passed (and that is not waiting on NPC Forge)."""
    return (isinstance(record, dict) and (kind_of(record) in KINDS or written_here(record)) and not record.get("forge_job")
            and store.image_path(record["id"], "portrait") is not None
            and (record.get("age_check") or {}).get("ok") is None)


def recheck(store, avatar_id, checker, living=None) -> str:
    """Age-check a portrait that came in unchecked: 'passed', 'removed' (it read as under age, so it was taken down),
    'no_answer' (the model could not look at it: it stays unchecked) or 'none' (nothing to check)."""
    record = _person(store, avatar_id)
    if checker is None or not unchecked(store, record):
        return "none"
    png = store.image_path(record["id"], "portrait").read_bytes()
    # One adult reading passes it. A picture already in use is only taken down when two readings say under age: the model's
    # estimate can swing between looks, so a third look settles a split.
    readings = []
    while True:
        result = checker.check(png)
        if result.get("ok") not in (True, False):
            return "no_answer"
        readings.append(result)
        under = sum(1 for r in readings if r["ok"] is False)
        adult = len(readings) - under
        if under >= 2 or adult >= 2 or (adult and not under):
            break
    result = next(r for r in reversed(readings) if r["ok"] is (under < 2))
    record = _person(store, avatar_id)                       # it may have changed while the model looked
    path = store.image_path(record["id"], "portrait")
    if not unchecked(store, record) or path.read_bytes() != png:
        return "none"
    verdict = {"ok": result["ok"], "apparent_age": result.get("apparent_age"), "model": result.get("model")}
    if result["ok"] is True:
        record["age_check"] = verdict
        _save(store, record)
        return "passed"
    _take_down(store, record, verdict, living)
    return "removed"


def _take_down(store, record, verdict, living=None) -> None:
    """Stop using a portrait the age check read as under age. A Forge portrait gives way to the card's picture (checked in its
    turn); the card's own picture is not kept to be put back."""
    folder = store.root / record["id"]
    current = portrait_from(store, record)
    kept = folder / CARD_PICTURE
    (folder / "portrait.png").unlink(missing_ok=True)
    if current in ("forge", "upload") and kind_of(record) == "character" and kept.is_file():
        shutil.copyfile(kept, folder / "portrait.png")
        record["portrait_from"] = "card"
        record["age_check"] = record.get("card_age_check") or {"ok": None, "apparent_age": None, "model": None}
    else:
        if current == "card":
            kept.unlink(missing_ok=True)
            record["card_age_check"] = verdict
        record["portrait_from"] = "none"
        record["age_check"] = verdict
    record["portrait_removed"] = {"at": _now(), "from": current, "apparent_age": verdict.get("apparent_age")}
    _save(store, record)
    LOGGER.warning("A portrait was taken down: the age check read it as about %s (avatar %s)",
                   verdict.get("apparent_age"), record["id"])
    if living is not None:
        living.forget(record["id"])


def recheck_soon(store, checker, living=None, ids=None) -> int:
    """Check the unchecked portraits of pals and characters (everyone's, or those in `ids`) in the background, one at a time.
    When the model gives no answer, the rest wait too, and are tried again after RECHECK_AFTER seconds. Returns how many
    were started."""
    if checker is None:
        return 0
    root, now = str(store.root), time.monotonic()
    records = store.list() if ids is None else [store.get(i) for i in ids if valid_id(i)]
    with _recheck_lock:
        wanted = [r["id"] for r in records if unchecked(store, r) and (root, r["id"]) not in _rechecking
                  and now - _no_answer.get((root, r["id"]), now - RECHECK_AFTER) >= RECHECK_AFTER]
        _rechecking.update((root, i) for i in wanted)
    if not wanted:
        return 0

    def job():
        silent = False
        for avatar_id in wanted:
            try:
                if silent:
                    _no_answer[(root, avatar_id)] = time.monotonic()
                    continue
                state = recheck(store, avatar_id, checker, living)
                if state == "no_answer":
                    silent = True
                    _no_answer[(root, avatar_id)] = time.monotonic()
                else:
                    _no_answer.pop((root, avatar_id), None)
            except Exception:
                LOGGER.exception("Could not age-check a portrait later")
            finally:
                with _recheck_lock:
                    _rechecking.discard((root, avatar_id))

    if RECHECK_INLINE:
        job()
    else:
        from core.state_gate import start_job
        try:
            start_job(job, name="altera-portrait-recheck")
        except Exception:
            with _recheck_lock:
                _rechecking.difference_update((root, i) for i in wanted)
            raise
    return len(wanted)
