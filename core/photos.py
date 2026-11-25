"""
Altera — The pictures she sends.

A picture is an edit of the active avatar's reference images (its NPC Forge portrait, plus the full-body view when it has
one) driven by a one-sentence scene. Every request goes through the same gates, in this order:

  1. hard rules (core/safety.py): no minors or childlike detail, no real people, nothing non-consensual  -> blocked
  2. the adult-mode level: words that need a higher level than the current one                             -> blocked
  3. generation (a ComfyUI backend)
  4. the image age check (core/age_check.py). A picture that reads as under 18 is discarded, and at the suggestive level
     and above a picture is delivered only if the check ran and passed.

A blocked or discarded picture is never written to disk. Pictures live in data/photos/ (private, git-ignored) and are
served only through no-store routes.
"""
import datetime
import hashlib
import json
import logging
import os
import queue
import random
import re
import tempfile
import threading
import time
import uuid
from pathlib import Path

from core import addons, adult, kids, safety
from core.imagegen import FULL_LENGTH, QUALITY, ImageGenError

LOGGER = logging.getLogger(__name__)

PHOTO_ID_RE = re.compile(r"^p-[0-9a-f]{10}$")
SHARED_REQUEST_RE = re.compile(r'^shared_[0-9a-f]{32}$')
MAX_SCENE = 300
KIDS_PICTURES_OFF = 'Chat pictures are turned off for this pal in kids mode.'


def _retryable_replace_error(error):
    return os.name == 'nt' and getattr(error, 'winerror', None) in {5, 32, 33}


def _replace_photo_record(source, target):
    """Tolerate brief Windows sharing conflicts without losing the old record."""
    for delay in (0.02, 0.05, 0.1, 0.2, None):
        try:
            os.replace(source, target)
            return
        except PermissionError as error:
            if delay is None or not _retryable_replace_error(error):
                raise
            time.sleep(delay)

# NPC Forge art style id -> how a picture should look in that style.
STYLES = {
    "photoreal": {"edit_name": "photorealistic",
                  "look": "A candid, realistic smartphone photograph with natural light, sharp focus and natural skin texture."},
    "semi_real": {"edit_name": "soft painterly digital painting", "look": "A soft painterly digital painting."},
    "cartoon": {"edit_name": "flat vector cartoon", "look": "A flat vector cartoon illustration."},
    "pixel": {"edit_name": "16-bit pixel", "look": "A 16-bit pixel art illustration."},
}
BASE_NEGATIVE = ("blurry, low quality, deformed face, extra fingers, extra limbs, deformed hands, watermark, text, logo, "
                 "signature, cropped, multiple people, collage")


class PhotoError(ValueError):
    """Something the user can fix: the message is safe to show."""


class PhotoBlocked(PhotoError):
    """The request breaks a rule (hard rule or adult-mode level). required_level is set when only the level is in the way."""

    def __init__(self, message, required_level=None):
        super().__init__(message)
        self.required_level = required_level


class PhotoUnchecked(PhotoBlocked):
    """A picture that needs the age check while no vision model answered it. That is not the picture's fault, so a sync tries
    it again later instead of remembering it as refused."""

    def __init__(self, message, error=None):
        super().__init__(message)
        self.error = error


_STOPWORDS = {"with", "that", "this", "from", "your", "have", "some", "wearing", "looking", "holding", "photo", "picture",
              "selfie", "sitting", "standing", "taking", "into", "over", "while", "there", "their", "them", "then", "and", "the",
              "for", "her", "his", "she", "out", "all", "one", "you", "are", "was", "just", "very", "little", "nice", "cute",
              # what a request says around the scene ("send me another pic please")
              "send", "more", "another", "please", "again", "snap", "want", "would", "could", "take", "show", "share", "give",
              "need", "can"}


def _words(text) -> set:
    """Meaningful lowercase words of a scene, for matching a request against ready-made pictures."""
    return {w for w in re.findall(r"[a-z]{4,}", (text or "").lower()) if w not in _STOPWORDS}


_SHORT_OK = {"bar", "spa", "gym", "bed", "tub", "sun", "sea", "car", "hat", "bus", "pub", "zoo", "cat", "dog"}


def _stem(word: str) -> str:
    """A crude stem, enough to see that 'walking', 'walks' and 'walk' are the same thing ('cafes' and 'cafe' too)."""
    if len(word) <= 3:
        return word
    stem = None
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("ing") and len(word) > 5:
        stem = word[:-3]
    elif word.endswith("ed") and len(word) > 4:
        stem = word[:-2]
    if stem is not None:
        return stem[:-1] if len(stem) > 2 and stem[-1] == stem[-2] and stem[-1] not in "lsf" else stem       # 'running' -> 'run'
    if word.endswith(("ches", "shes", "sses", "xes", "zes")) and len(word) > 4:
        return word[:-2]                                                                                   # 'beaches' -> 'beach'
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]                                                                                   # 'cafes' -> 'cafe'
    return word


def _keys(text) -> set:
    """Stems of the meaningful words of a scene or a request (three letters and up, so 'bar', 'spa' and 'gym' count)."""
    words = re.findall(r"[a-z]{3,}", (text or "").lower())
    return {_stem(w) for w in words if w not in _STOPWORDS and (len(w) >= 4 or w in _SHORT_OK)}


def match_score(wanted: set, candidate: set) -> float:
    """How well a picture's description covers what was asked for: the share of the (at most five) key words it has, 0..1."""
    if not wanted:
        return 0.0
    return len(wanted & candidate) / min(len(wanted), 5)


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def valid_id(value) -> bool:
    return isinstance(value, str) and bool(PHOTO_ID_RE.match(value))


def normalize_scene(text) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip().strip("\"'`")
    return text[:MAX_SCENE].strip(" .")


def _who(card: dict) -> str:
    return {"female": "woman", "male": "man"}.get((card.get("gender") or "").lower(), "person")


FRAME_FULL = ("Frame it as a full-length photograph taken from a few metres back, so the whole body is visible from head to knees "
              "or further, with plenty of space around the figure.")
_CLOSE_SHOT = re.compile(r"\b(close-up|closeup|headshot|portrait)\b", re.I)


def frame_of(scene: str, level: str):
    """Request the registered framing when the scene does not ask for a close shot."""
    return "full" if adult.options(level)["full_length"] and not _CLOSE_SHOT.search(scene or "") else None


def build_prompt(card: dict, scene: str, level: str, refs: int = 1) -> dict:
    """The Mage-Flow-Edit prompt and negative prompt for one picture.

    The clothing sentence is measured, not decorative: with a plain "the clothing comes from the description" the reference's
    apron turned up on a beach walk and in a mirror selfie in jeans. Telling the model that nothing the person wears in the
    reference may appear removed it in every test scene (naming the garments to drop made it worse: it primed them).
    """
    who = _who(card)
    style = STYLES.get(card.get("style"), {"edit_name": card.get("style") or "same", "look": ""})
    if refs > 1:
        reference = (f"the reference images (the first shows the {who}'s face and hair, the second shows the whole body)")
    else:
        reference = "the reference image"
    style_rule = adult.options(level)["framing"]
    dress = style_rule.replace("{who}", who) if style_rule else (
        f"Dress the {who} only as the description says, and in ordinary everyday clothes for anything it does not mention.")
    parts = [
        f"Show the same {who} from {reference} {scene}.",
        FRAME_FULL if frame_of(scene, level) else "",
        f"Keep the exact same face, hair, skin tone and body build as in the reference. "
        f"The {who} is an adult, {card.get('age')} years old"
        + (f", with {card['look']}." if card.get("look") else "."),
        f"Change the clothing completely: nothing the {who} wears in the reference may appear. " + dress,
        style["look"],
        f"Keep the exact same {style['edit_name']} art style.",
    ]
    source = addons.picture_source() if level != "off" else None
    body = source.picture_details(card, level) if source else ""
    if body:
        parts.append(f"Body: {body}.")
    mood = adult.photo_mood(level)
    if mood:
        parts.append(f"The {who} has {mood}.")
    return {"positive": " ".join(p for p in parts if p), "negative": f"{BASE_NEGATIVE}, {safety.YOUTH_NEGATIVE}"}


class PhotoService:
    def __init__(self, root, avatars, backend_for, checker, config=None, sync=False, rng=None):
        """avatars: an AvatarStore. backend_for(level) -> backend. checker: an AgeCheck (or None). sync=True runs jobs inline."""
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.avatars = avatars
        self.backend_for = backend_for
        self.checker = checker
        cfg = (config or {}).get("imagegen") or {}
        self.quality = cfg.get("quality") if cfg.get("quality") in QUALITY else "standard"
        # Off by default. Measured with real pictures: adding the full-body view made the reference outfit bleed into the scene
        # (an apron on a beach walk) and doubled the time per picture; the portrait alone kept the face just as well.
        self.use_body = cfg.get("use_body_reference", False) is True
        # On by default: a loaded chat model can hold 30-40+ GB of RAM/VRAM that a picture needs to even start, and LM
        # Studio does not release it between chat turns on its own. The next chat reply is slower -- it loads the model
        # again -- but only that one.
        self.unload_llm = cfg.get("unload_llm_before_generate", True) is not False
        self.sync = sync
        self._rng = rng or random.Random()
        self._queue = queue.Queue()
        self._thread = None
        self._lock = threading.Lock()
        self._shared_lock = threading.Lock()
        self._shared_pending = 0
        self._shared_bindings = {}
        self._syncing = set()                            # avatars whose studio pictures are being imported right now
        self._sync_lock = threading.Lock()
        self._records = None                             # id -> record; built on first use, see _index()
        self._index_lock = threading.Lock()
        self._recover()

    # ---- storage --------------------------------------------------------------
    def _json(self, pid) -> Path:
        return self.root / f"{pid}.json"

    def _png(self, pid) -> Path:
        return self.root / f"{pid}.png"

    # Every record, by id, read from disk once and kept in step by _save() and delete(). Before this, _all() reread and
    # re-parsed every p-*.json on the disk, and build_context() calls list_premade() on EVERY chat turn -- so a companion
    # with a few hundred pictures did a few hundred file reads and JSON parses before she could answer a message.
    def _index(self) -> dict:
        """The index, built from the folder the first time it is needed and owned by this service from then on.

        It is built exactly once and never swapped out, because a picture is written from the worker thread while the
        request thread is reading: rebuilding it would replace the dict a concurrent _save() is about to write into,
        and that write would be lost. Records this service has not seen are picked up one at a time by _read().
        """
        with self._index_lock:
            if self._records is None:
                records = {}
                for path in self.root.glob("p-*.json"):
                    rec = self._read_file(path.stem)
                    if rec and rec.get("id"):
                        records[rec["id"]] = rec
                self._records = records
            return self._records

    def _read_file(self, pid) -> dict | None:
        """One record, straight from disk."""
        if not valid_id(pid):
            return None
        try:
            return json.loads(self._json(pid).read_text("utf-8"))
        except (OSError, ValueError):
            return None

    def _read(self, pid) -> dict | None:
        """One record. A copy, because callers mutate what they get back and then hand it to _save()."""
        if not valid_id(pid):
            return None
        cached = self._index().get(pid)
        if cached is not None:
            return dict(cached)
        # Not in the index: written by something other than this service (another PhotoService over the same folder,
        # or a file dropped in by hand). One file read, and it joins the index from then on.
        fresh = self._read_file(pid)
        if not fresh or not fresh.get("id"):
            return None
        index = self._index()
        with self._index_lock:
            index[fresh["id"]] = dict(fresh)
        return dict(fresh)

    def _save(self, rec: dict) -> None:
        path = self._json(rec["id"])
        fd, tmp = tempfile.mkstemp(dir=self.root, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(rec, f, indent=2, ensure_ascii=False)
            _replace_photo_record(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        index = self._index()
        with self._index_lock:
            index[rec["id"]] = dict(rec)

    def _recover(self) -> None:
        """Jobs left running by a previous run of the app can never finish."""
        for rec in self._all():
            if rec.get("status") in ("pending", "running"):
                rec.update(status="failed", reason="Interrupted when the app was restarted.", finished=_now())
                self._save(rec)

    @staticmethod
    def public(rec: dict) -> dict:
        result = {k: rec.get(k) for k in ("id", "avatar_id", "session_id", "status", "reason", "scene", "level", "created",
                                           "finished", "age_checked", "source", "superseded_by", "required_level", "premade",
                                           "label")}
        if result["required_level"] and not adult.visible(result["required_level"]):
            result.update(required_level=None, reason="The picture did not pass the content rules.")
        # Altera only creates or imports generated companion images. This public
        # label also covers legacy records without rewriting their metadata.
        result['provenance'] = 'imagined_scene'
        return result

    # ---- public API -----------------------------------------------------------
    def create(self, scene=None, level="off", avatar_id=None, session_id=None, source="user", record_blocked=False) -> dict:
        """Start a picture. A request that breaks a rule raises PhotoBlocked, or, with record_blocked=True (a picture she
        chose to send herself), is kept as a 'blocked' record so the chat can show why nothing arrived."""
        avatar_id = avatar_id or self.avatars.active_id()
        avatar = self.avatars.get(avatar_id) if avatar_id else None
        if not avatar:
            raise PhotoError("Choose an avatar first: import one from NPC Forge in Settings.")
        if kids.is_on(avatar):
            raise PhotoBlocked(KIDS_PICTURES_OFF)
        if not self.avatars.image_path(avatar_id, "portrait"):
            raise PhotoError("This avatar has no portrait image.")
        level = level if adult.visible(level) else "off"
        scene = normalize_scene(scene) or adult.pick_scene(level, self._rng)

        reason, required = safety.hard_problem(scene), None
        if not reason:
            tier = adult.tier_problem(scene, level)
            if tier:
                reason, required = tier["message"], tier["required"]

        rec = {"id": "p-" + uuid.uuid4().hex[:10], "avatar_id": avatar_id, "session_id": session_id, "scene": scene,
               "level": level, "status": "pending", "reason": None, "created": _now(), "finished": None,
               "seed": self._rng.randrange(2 ** 31), "age_checked": False, "source": source, "required_level": required}
        if reason:
            if not record_blocked:
                raise PhotoBlocked(reason, required)
            rec.update(status="blocked", reason=reason, finished=_now())
            self._save(rec)
            return self.public(rec)

        self._save(rec)
        if self.sync:
            self._process(rec["id"])
        else:
            try:
                self._enqueue(rec["id"])
            except Exception:
                self._finish(rec, 'failed', 'The picture could not be queued. Retry when Altera is ready.')
                raise
        return self.public(self._read(rec["id"]))

    def create_shared(self, scene, reference_png, *, avatar_id, session_id, dataset_epoch, request_id, self_attested,
                      on_accept=None, before_new=None, vision_target=None, image_backend=None, approval_id=''):
        """Queue an explicitly attested user reference for one imagined scene.

        Reference bytes are held only by this process and are not added to the
        photo record. The shared-scene route binds the session, epoch and
        identity before calling; this method also supports trusted test callers.
        """
        from core.shared_reference import ReferenceError, normalize_png
        if (self_attested is not True or not isinstance(avatar_id, str) or not avatar_id
                or type(session_id) is not int or not 0 < session_id < 2 ** 63 or not isinstance(dataset_epoch, str)
                or not 0 < len(dataset_epoch) <= 128 or not isinstance(request_id, str) or not SHARED_REQUEST_RE.fullmatch(request_id)
                or (on_accept is not None and not callable(on_accept)) or (before_new is not None and not callable(before_new))
                or not isinstance(approval_id, str) or len(approval_id) > 128):
            raise PhotoError('Review the companion, conversation and your reference confirmation.')
        if (not isinstance(scene, str) or not scene.strip() or len(scene) > MAX_SCENE
                or any((ord(c) < 32 and c not in '\n\r\t') or ord(c) == 127 for c in scene)):
            raise PhotoError('Describe the imagined scene in 300 characters or fewer.')
        try:
            scene.encode('utf-8')
        except UnicodeError:
            raise PhotoError('Describe the imagined scene using valid text.') from None
        scene = normalize_scene(scene)
        reason = safety.hard_problem(scene)
        tier = adult.tier_problem(scene, 'off') if not reason else None
        if reason or tier:
            raise PhotoBlocked(reason or tier['message'], tier['required'] if tier else None)
        avatar = self.avatars.get(avatar_id)
        if kids.is_on(avatar):
            raise PhotoBlocked(KIDS_PICTURES_OFF)
        if not avatar or safety.card_problem(avatar.get('card') or {}) or not self.avatars.image_path(avatar_id, 'portrait'):
            raise PhotoError('The selected companion or her adult portrait is unavailable.')
        if not self.checker or not callable(getattr(self.checker, 'check_subjects', None)):
            raise PhotoError('A subject-counting vision check is required for shared images.')
        try:
            reference = normalize_png(reference_png)
        except ReferenceError as error:
            raise PhotoError(str(error)) from None
        fingerprint = hashlib.sha256(json.dumps([avatar_id, session_id, dataset_epoch, scene, approval_id,
            hashlib.sha256(reference).hexdigest()], ensure_ascii=True, separators=(',', ':')).encode('ascii')).hexdigest()
        with self._shared_lock:
            existing = next((row for row in self._all() if row.get('shared_request') == request_id), None)
            if existing:
                if existing.get('shared_fingerprint') != fingerprint:
                    raise PhotoError('This request belongs to a different shared image. Start a new request.')
                if on_accept is not None:
                    on_accept(existing['id'])
                return self.public(existing)
            if before_new is not None:
                before_new()
            if self._shared_pending >= 2:
                raise PhotoError('Wait for the current shared images to finish before trying again.')
            pid = None
            for _ in range(16):
                candidate = 'p-' + uuid.uuid4().hex[:10]
                if (candidate not in self._index() and not os.path.lexists(self._json(candidate))
                        and not os.path.lexists(self._png(candidate))):
                    pid = candidate
                    break
            if pid is None:
                raise PhotoError('A unique shared picture could not be reserved. Retry later.')
            rec = {'id': pid, 'avatar_id': avatar_id, 'session_id': session_id,
                   'scene': scene, 'level': 'off', 'status': 'pending', 'reason': None, 'created': _now(),
                   'finished': None, 'seed': self._rng.randrange(2 ** 31), 'age_checked': False,
                   'source': 'shared-scene', 'required_level': None, 'shared_request': request_id,
                   'shared_fingerprint': fingerprint, 'dataset_epoch': dataset_epoch}
            self._save(rec)
            try:
                if on_accept is not None:
                    on_accept(pid)
            except Exception:
                self._finish(rec, 'failed', 'The shared picture could not be attached to the conversation.')
                raise
            self._shared_bindings[pid] = (vision_target, image_backend)
            try:
                if self.sync:
                    try:
                        self._process_shared(rec['id'], reference)
                    finally:
                        self._shared_bindings.pop(pid, None)
                else:
                    self._shared_pending += 1
                    try:
                        self._enqueue(rec['id'], reference)
                    except Exception:
                        self._shared_pending -= 1
                        raise
            except Exception:
                self._shared_bindings.pop(pid, None)
                if not self.sync:
                    self._finish(rec, 'failed', 'The shared picture could not be queued. Start a new request to retry.')
                raise
        return self.public(self._read(rec['id']))

    def get(self, pid) -> dict | None:
        rec = self._read(pid)
        return self.public(rec) if rec and adult.visible(rec.get("level")) else None

    def scene_for(self, pid) -> str | None:
        rec = self._read(pid)
        return rec.get("scene") if rec and adult.visible(rec.get("level")) else None

    def image_path(self, pid) -> Path | None:
        rec = self._read(pid)
        if rec and rec.get("status") == "ready" and adult.visible(rec.get("level")) and self._png(pid).is_file():
            return self._png(pid)
        return None

    def list(self, avatar_id=None, limit=60) -> list:
        """Newest first. Pictures that were redone are left out: their replacement is listed instead."""
        recs = [r for r in self._all() if not r.get("superseded_by") and adult.visible(r.get("level"))]
        if avatar_id:
            recs = [r for r in recs if r.get("avatar_id") == avatar_id]
        recs.sort(key=lambda r: r.get("created", ""), reverse=True)
        return [self.public(r) for r in recs[:limit]]

    def delete(self, pid) -> bool:
        if not valid_id(pid):
            return False
        with self._shared_lock:
            rec = self._read(pid)
            if rec and rec.get('source') == 'shared-scene' and rec.get('status') in ('pending', 'running'):
                raise PhotoError('Wait for this shared picture to finish before deleting it.')
            # Keep the record/index until the image is removed. A denied unlink
            # must remain visible and retryable, particularly during privacy clear.
            removed = False
            for path in (self._png(pid), self._json(pid)):
                try:
                    path.unlink()
                    removed = True
                except FileNotFoundError:
                    pass
            index = self._index()
            with self._index_lock:
                index.pop(pid, None)
            return removed

    def delete_all(self) -> int:
        """Every picture ('Delete All Conversations' removes the pictures made in them). Avatars are not touched."""
        removed = 0
        # Include orphaned images and malformed records left by an interrupted
        # or older deletion; these still contain private content on disk.
        ids = {r["id"] for r in self._all()}
        ids.update(path.stem for pattern in ('p-*.png', 'p-*.json') for path in self.root.glob(pattern)
                   if valid_id(path.stem))
        for pid in sorted(ids):
            if self.delete(pid):
                removed += 1
        return removed

    def delete_for_avatar(self, avatar_id) -> int:
        removed = 0
        for pid in [r["id"] for r in self._all() if r.get("avatar_id") == avatar_id]:
            if self.delete(pid):
                removed += 1
        return removed

    def reroll(self, pid, level=None) -> dict:
        """Same scene, new seed. The level can only be the one passed in (the current adult-mode level). The old picture
        points at its replacement, so a chat message that shows the old one shows the new one."""
        rec = self._read(pid)
        if not rec or not adult.visible(rec.get("level")):
            raise PhotoError("That picture was not found.")
        if rec.get('source') == 'shared-scene':
            raise PhotoError('Choose your reference again to create another shared picture.')
        new = self.create(scene=rec["scene"], level=level if level is not None else rec["level"],
                          avatar_id=rec["avatar_id"], session_id=rec.get("session_id"), source="reroll")
        if not rec.get("premade"):                   # a ready-made picture stays as it is; "another" only adds a new one
            rec = self._read(pid) or rec
            rec["superseded_by"] = new["id"]
            self._save(rec)
        return new

    # ---- ready-made pictures (made in NPC Forge's studio, imported with the avatar) ---------------------------------
    def import_premade(self, avatar_id, png, scene, level, key, label=None, sig=None, check=None) -> dict:
        """Add one ready-made picture. It goes through the same gates as a new picture: the hard rules, the words-need-a-level
        check (its description may not need a higher level than the one it is filed under) and the age check (Suggestive and
        above only with a passing check). Importing the same `key` again replaces the earlier copy, keeping its id.
        `check` is an age-check result to use instead of asking again: a sync passes one once the vision model has not answered."""
        avatar = self.avatars.get(avatar_id)
        if not avatar:
            raise PhotoError("That avatar was not found.")
        if kids.is_on(avatar):
            raise PhotoBlocked(KIDS_PICTURES_OFF)
        if problem := safety.card_problem(avatar.get("card") or {}):
            raise PhotoBlocked(problem)
        if not adult.visible(level):
            raise PhotoError(f"Unknown level: {level!r}.")
        scene = normalize_scene(scene)
        if not scene:
            raise PhotoError("This picture has no description.")
        reason = safety.hard_problem(scene)
        if reason:
            raise PhotoBlocked(reason)
        tier = adult.tier_problem(scene, level)
        if tier:
            raise PhotoBlocked(f"Its description needs {adult.label(tier['required'])}, but it is filed under "
                               f"{adult.label(level)}. Fix the level in NPC Forge.", tier["required"])
        if check is None:
            check = self.checker.check(png) if self.checker else {"ok": None, "error": "No age check is configured."}
        if check.get("ok") is False:
            raise PhotoBlocked(f"The age check rejected this picture (it reads as about {check.get('apparent_age')}, "
                               f"under {safety.MIN_APPARENT_AGE}).")
        if check.get("ok") is not True and adult.needs_age_check(level):
            raise PhotoUnchecked("Pictures at this level need the age check, and it could not run: "
                                 f"{check.get('error') or 'no answer'}", check.get("error"))

        existing = next((r for r in self._all() if r.get("premade_key") == key and r.get("avatar_id") == avatar_id), None)
        pid = existing["id"] if existing else "p-" + uuid.uuid4().hex[:10]
        rec = {"id": pid, "avatar_id": avatar_id, "session_id": None, "scene": scene, "label": (label or "").strip()[:80] or None,
               "level": level, "status": "ready", "reason": None, "premade": True, "premade_key": key,
               "created": (existing or {}).get("created") or _now(), "finished": _now(),
               "age_checked": check.get("ok") is True, "source": "npc-forge", "required_level": None, "premade_sig": sig}
        self._png(pid).write_bytes(png)
        self._save(rec)
        return self.public(rec)

    def _all(self) -> list:
        """Every record, from the in-memory index. Copies: callers filter and sort these, and some mutate them."""
        index = self._index()
        with self._index_lock:
            return [dict(r) for r in index.values()]

    def premade_keys(self, avatar_id) -> dict:
        """{'<npc id>:<studio item id>': photo id} for what has already been imported."""
        return {r["premade_key"]: r["id"] for r in self._all()
                if r.get("premade") and r.get("avatar_id") == avatar_id and r.get("premade_key")}

    def list_premade(self, avatar_id, max_level=None) -> list:
        """Ready-made pictures of this avatar that the current level allows, newest first."""
        recs = [r for r in self._all() if r.get("premade") and r.get("status") == "ready" and r.get("avatar_id") == avatar_id
                and adult.visible(r.get("level"), max_level)]
        recs.sort(key=lambda r: r.get("created", ""), reverse=True)
        return [self.public(r) for r in recs]

    def get_premade(self, pid, avatar_id, max_level) -> dict | None:
        rec = self._read(pid)
        if (rec and rec.get("premade") and rec.get("status") == "ready" and rec.get("avatar_id") == avatar_id
                and adult.visible(rec.get("level"), max_level) and self._png(pid).is_file()):
            return self.public(rec)
        return None

    # ---- keeping her gallery in step with NPC Forge's studio ---------------------------------------------------------
    def _sync_path(self, avatar_id) -> Path:
        return self.root / f"sync-{avatar_id}.json"

    def _sync_state(self, avatar_id) -> dict:
        try:
            data = json.loads(self._sync_path(avatar_id).read_text("utf-8"))
        except (OSError, ValueError):
            data = {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _file_sig(path) -> str:
        if isinstance(path, bytes):
            from hashlib import sha256
            return sha256(path).hexdigest()
        try:
            stat = Path(path).stat()
            return f"{stat.st_mtime_ns}-{stat.st_size}"
        except OSError:
            return ""

    def sync_premade(self, avatar_id) -> dict:
        """Make her gallery match what is approved ('kept') in NPC Forge's studio: import what is new (each picture goes through
        the same gates as any other, the age check included), replace what was redone there, drop what was un-kept or deleted.
        A picture that was refused is not offered to the age check again until its file changes. One the age check could not
        look at (no vision model answered) is 'waiting': it is tried again on the next sync. Returns the counts."""
        record = self.avatars.get(avatar_id)
        result = {"imported": 0, "updated": 0, "removed": 0, "refused": 0, "waiting": 0, "problems": []}
        if not record or not addons.picture_source():
            return result
        npc_id = (record.get("source") or {}).get("id")      # a pal has no NPC Forge character, so no studio pictures
        if not npc_id:
            return result
        items, listed = self.avatars.forge_premade_read(npc_id)
        kept_keys = {f"{npc_id}:{i['item_id']}" for i in items}
        state = self._sync_state(avatar_id)
        refused = state.get("refused") if isinstance(state.get("refused"), dict) else {}
        have = {r["premade_key"]: r for r in self._all()
                if r.get("premade") and r.get("avatar_id") == avatar_id and r.get("premade_key")}
        no_answer = None            # once the age check has not answered, the rest of this pass does not wait on it again

        for item in items:
            key = f"{npc_id}:{item['item_id']}"
            path = self.avatars.forge_premade_file(npc_id, item["item_id"])
            if not path:
                continue
            sig = self._file_sig(path)
            existing = have.get(key)
            if existing and existing.get("premade_sig") == sig:
                continue
            if not existing and (refused.get(key) or {}).get("sig") == sig:
                result["refused"] += 1
                continue
            try:
                png = path if isinstance(path, bytes) else path.read_bytes()
                done = self.import_premade(avatar_id, png, item["scene"], item["level"], key=key, label=item["label"],
                                           sig=sig, check=no_answer)
                refused.pop(key, None)
                result["updated" if existing else "imported"] += 1
                if not done.get("age_checked") and no_answer is None:      # an everyday picture that came in unchecked
                    no_answer = {"ok": None, "error": "The vision model did not answer."}
            except PhotoUnchecked as e:                          # not the picture's fault: no verdict is remembered, it is tried again
                no_answer = {"ok": None, "error": e.error or "The vision model did not answer."}
                result["waiting"] += 1
                result["problems"].append(f"{item.get('label') or item['scene'][:40]}: {e}")
            except PhotoError as e:                              # a rule, or the age check said no
                refused[key] = {"sig": sig, "reason": str(e)[:300]}
                result["refused"] += 1
                result["problems"].append(f"{item.get('label') or item['scene'][:40]}: {e}")

        for key, rec in have.items():                            # un-kept or deleted in the studio: no longer hers to send
            if listed and key not in kept_keys:                  # (only when the list could be read: it may be being written)
                self.delete(rec["id"])
                result["removed"] += 1
        state.update(refused=refused, synced_at=_now(), last=result)
        try:
            self._sync_path(avatar_id).write_text(json.dumps(state, indent=2), "utf-8")
        except OSError:
            pass
        return result

    def sync_premade_async(self, avatar_id) -> bool:
        """Start sync_premade in the background (one at a time per avatar). False if it is already running. In sync mode: inline."""
        if not addons.picture_source():
            return False
        with self._sync_lock:
            if avatar_id in self._syncing:
                return False
            self._syncing.add(avatar_id)

        def job():
            try:
                self.sync_premade(avatar_id)
            except Exception:
                LOGGER.exception("Could not sync the studio pictures")
            finally:
                with self._sync_lock:
                    self._syncing.discard(avatar_id)
        if self.sync:
            job()
        else:
            from core.state_gate import start_job
            try:
                start_job(job, name="altera-photo-sync")
            except Exception:
                with self._sync_lock:
                    self._syncing.discard(avatar_id)
                raise
        return True

    def sync_status(self, avatar_id) -> dict:
        with self._sync_lock:
            running = avatar_id in self._syncing
        state = self._sync_state(avatar_id)
        return {"running": running, "synced_at": state.get("synced_at"), "last": state.get("last") or {}}

    # ---- her gallery: what she can send without making anything ---------------------------------------------------------
    def gallery(self, avatar_id, level) -> list:
        """Every picture of hers that can be sent again at this level: the studio's approved ones, then the ones she made for
        the person earlier (never one that was blocked, failed or redone). Newest first within each group."""
        made, ready = [], []
        for rec in self._all():
            if (rec.get("avatar_id") != avatar_id or rec.get("status") != "ready" or rec.get("superseded_by")
                    or rec.get('source') == 'shared-scene'
                    or not adult.visible(rec.get("level"), level) or not self._png(rec["id"]).is_file()):
                continue
            (ready if rec.get("premade") else made).append(rec)
        for group in (ready, made):
            group.sort(key=lambda r: r.get("created", ""), reverse=True)
        return [self.public(r) for r in ready + made]

    def rank_premade(self, pool: list, request: str, limit: int = 12) -> list:
        """The pictures to tell her about this turn: the ones that fit what was just said first, then a random handful."""
        wanted = _keys(request)
        scored = [(match_score(wanted, _keys(f"{p.get('scene')} {p.get('label') or ''}")), self._rng.random(), p) for p in pool]
        scored.sort(key=lambda t: (-t[0], t[1]))
        return [p for _, _, p in scored[:limit]]

    def find_match(self, avatar_id, level, wanted, recent=()) -> dict | None:
        """A picture she already has that shows what was asked for, or None (then a new one is made). `wanted` is her own
        description or the person's request; `recent` are ids that were sent lately, which are only used when nothing else fits.
        A studio picture wins a tie against one she made earlier."""
        keys = _keys(wanted)
        if not keys:
            return None
        best, fallback = [], None
        for pic in self.gallery(avatar_id, level):
            have = _keys(f"{pic.get('scene')} {pic.get('label') or ''}")
            overlap = len(keys & have)
            score = match_score(keys, have)
            if overlap < min(2, len(keys)) or score < 0.5:
                continue
            score += 0.05 if pic.get("premade") else 0.0
            if pic["id"] in set(recent):
                fallback = fallback or (pic, score)
                continue
            best.append((score, pic))
        if best:
            top = max(score for score, _ in best)
            return {**self._rng.choice([p for score, p in best if score >= top - 1e-9]), "reused": True}
        return {**fallback[0], "reused": True} if fallback else None

    def pick_premade(self, avatar_id, level, hint=None, recent=()) -> dict | None:
        """A ready-made picture for a request. With no hint any allowed one will do (one that was not sent lately, if there is
        one); with a hint it must share a meaningful word with the picture's description or label, otherwise None (the caller
        then makes a new picture)."""
        pool = self.list_premade(avatar_id, level)
        if not pool:
            return None
        if not hint:
            fresh = [p for p in pool if p["id"] not in set(recent)]
            return self._rng.choice(fresh or pool)
        wanted = _words(hint)
        scored = [(len(wanted & _words(f"{p.get('scene')} {p.get('label') or ''}")), p) for p in pool]
        best = max(score for score, _ in scored)
        return self._rng.choice([p for score, p in scored if score == best]) if best >= 1 else None

    # ---- the job ----------------------------------------------------------------
    def _enqueue(self, pid, shared_reference=None) -> None:
        from core.state_gate import STATE, start_job
        lease = STATE.enter("altera-photo")
        try:
            with self._lock:
                if self._thread is None:
                    self._thread = start_job(self._worker, name="altera-photos")
                self._queue.put((pid, lease, shared_reference))
        except BaseException:
            lease.release()
            raise

    def _worker(self) -> None:
        while True:
            with self._lock:
                try:
                    pid, lease, shared_reference = self._queue.get_nowait()
                except queue.Empty:
                    self._thread = None
                    return
            try:
                if shared_reference is None:
                    self._process(pid)
                else:
                    self._process_shared(pid, shared_reference)
            except Exception:
                if shared_reference is not None:
                    LOGGER.warning('Shared image job failed (%s)', pid)
                    try:
                        rec = self._read(pid)
                        if rec and rec.get('status') in ('pending', 'running'):
                            self._finish(rec, 'failed', 'The shared picture could not be made. Start a new request to retry.')
                    except Exception:
                        LOGGER.warning('Shared image failure could not be saved (%s)', pid)
                else:
                    LOGGER.exception("Picture job crashed: %s", pid)
            finally:
                if shared_reference is not None:
                    shared_reference = None
                    with self._shared_lock:
                        self._shared_bindings.pop(pid, None)
                        self._shared_pending -= 1
                lease.release()
                self._queue.task_done()

    def _finish(self, rec: dict, status: str, reason: str | None = None) -> None:
        rec.update(status=status, reason=reason, finished=_now())
        self._save(rec)

    def _process_shared(self, pid, reference) -> None:
        from core.shared_reference import ReferenceError, normalize_png
        rec = self._read(pid)
        if not rec or rec.get('status') != 'pending' or rec.get('source') != 'shared-scene':
            return
        rec['status'] = 'running'
        self._save(rec)
        try:
            avatar = self.avatars.get(rec['avatar_id'])
            if kids.is_on(avatar):
                return self._finish(rec, 'blocked', KIDS_PICTURES_OFF)
            refs = self.avatars.refs(rec['avatar_id'], False)
            if not avatar or safety.card_problem(avatar.get('card') or {}) or not refs:
                raise PhotoError('The companion portrait is unavailable.')
            vision_target, image_backend = self._shared_bindings.get(pid, (None, None))
            def check(image, count):
                if vision_target is not None:
                    return self.checker.check_subjects(image, count, target=vision_target)
                return self.checker.check_subjects(image, count)
            if check(reference, 1).get('ok') is not True:
                return self._finish(rec, 'blocked', 'The reference could not be checked as one apparent adult. No image was made.')
            prompt = (f"Create an imagined scene of the two adults from the separate references: the first is the companion, "
                      f"the second is the user. Keep each person's identity distinct. Scene: {rec['scene']}. "
                      "Both people are adults and wear ordinary everyday clothing. Do not add other people.")
            negative = f"blurry, extra people, merged faces, nudity, sexual content, {safety.YOUTH_NEGATIVE}"
            width, height, steps = QUALITY[self.quality]
            png = (image_backend or self.backend_for('off')).render([refs[0], reference], prompt, negative, rec['seed'], (width, height), steps)
            png = normalize_png(png)
            if check(png, 2).get('ok') is not True:
                return self._finish(rec, 'blocked', 'The generated scene could not be checked as two apparent adults. It was discarded.')
            self._png(pid).write_bytes(png)
            rec['age_checked'] = True
            self._finish(rec, 'ready')
        except (ImageGenError, PhotoError, ReferenceError):
            self._finish(rec, 'failed', 'The shared picture could not be made. Check the image services and retry.')
        except Exception:
            LOGGER.warning('Shared image processing failed (%s)', pid)
            self._finish(rec, 'failed', 'The shared picture could not be made. Check the image services and retry.')

    def _process(self, pid) -> None:
        rec = self._read(pid)
        if not rec or rec.get("status") != "pending":
            return
        rec["status"] = "running"
        self._save(rec)

        try:
            avatar = self.avatars.get(rec["avatar_id"])
            if kids.is_on(avatar):
                return self._finish(rec, 'blocked', KIDS_PICTURES_OFF)
            if not adult.visible(rec["level"]):
                return self._finish(rec, "blocked", "The saved picture settings are unavailable.")
            if safety.hard_problem(rec["scene"]) or adult.tier_problem(rec["scene"], rec["level"]):
                return self._finish(rec, "blocked", "The requested scene does not pass the picture rules.")
            refs = self.avatars.refs(rec["avatar_id"], self.use_body)
            if not refs or not avatar:
                raise PhotoError("The avatar's reference images are missing. Import it again from NPC Forge.")
            if safety.card_problem(avatar["card"]):
                return self._finish(rec, "blocked", "The character does not pass the picture rules.")
            prompt = build_prompt(avatar["card"], rec["scene"], rec["level"], refs=len(refs))
            if rec["level"] != "off" and safety.hard_problem(prompt["positive"]):
                return self._finish(rec, "blocked", "The add-on's picture prompt does not pass the picture rules.")
            width, height, steps = (FULL_LENGTH if frame_of(rec["scene"], rec["level"]) else QUALITY)[self.quality]
            if self.unload_llm:
                from core.llm import unload_models
                unload_models()
            render = self.backend_for(rec["level"]).render
            hook = addons.picture_engine(rec["level"])
            if hook is None:
                png = render(refs, prompt["positive"], prompt["negative"], rec["seed"], (width, height), steps)
            else:
                def checked_render(positive, negative):
                    if not all(isinstance(text, str) and len(text) <= 24000 for text in (positive, negative)):
                        raise PhotoError("The add-on did not supply a usable picture prompt.")
                    if safety.hard_problem(positive) or adult.tier_problem(positive, rec["level"]):
                        raise PhotoBlocked("The add-on's picture prompt does not pass the picture rules.")
                    return render(refs, positive, f"{negative}, {safety.YOUTH_NEGATIVE}", rec["seed"], (width, height), steps)
                png = hook(prompt=prompt.copy(), render=checked_render)
                from core.charcard import CardError, png_size
                try:
                    if not isinstance(png, bytes):
                        raise CardError("Expected PNG bytes.")
                    png_size(png)
                except CardError:
                    raise PhotoError("The add-on did not return a readable PNG picture.") from None
        except (ImageGenError, PhotoError) as e:
            return self._finish(rec, "failed", str(e))
        except Exception:
            LOGGER.exception("Picture generation failed")
            return self._finish(rec, "failed", "Something went wrong while making the picture.")

        check = self.checker.check(png) if self.checker else {"ok": None, "error": "No age check is configured."}
        if check.get("ok") is False:
            return self._finish(rec, "blocked", f"The age check rejected this picture (it reads as about "
                                                f"{check.get('apparent_age')}, under {safety.MIN_APPARENT_AGE}). It was discarded.")
        if check.get("ok") is not True and adult.needs_age_check(rec["level"]):
            return self._finish(rec, "blocked", "Pictures at this level need the age check, and it could not run: "
                                                f"{check.get('error') or 'no answer'} It was discarded.")
        self._png(pid).write_bytes(png)
        rec["age_checked"] = check.get("ok") is True
        self._finish(rec, "ready")
