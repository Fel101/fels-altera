"""
Altera — Living portrait.

NPC Forge can make a "living portrait" of a character: frames of the portrait in which only the face changes (a blink, the
mouth open mid-word, happy, laughing, flirty...), lined up so that everything else is pixel-identical. It writes them to
<character>/living/ with a living.json that names the portrait they were made from (npc-generator/npcgen/living.py).

Altera copies the frames next to the avatar and the page animates her picture with them (static/js/living.js): she blinks, her
mouth moves while her voice plays, and her face follows what she says. The frames are only used while they were made from
the very portrait Altera has (same sha256): after a new portrait in NPC Forge the avatar has to be updated first.

Every frame goes through the age check, like every picture: one that reads as under 18 is refused and never shown. One the
check could not look at (no vision model answered) is used, marked unchecked, and checked again on the next sync -- the same
rule as the portrait itself at import. Nothing here writes to NPC Forge.

Layout: data/avatars/<id>/living/<frame>.png and living.json
    {"portrait_sha256", "frames": {id: {"sig", "age_checked", "apparent_age"}}, "refused": {id: {"sig", "reason"}},
     "synced_at", "reason"}
"""
import datetime
import hashlib
import json
import logging
import re
import shutil
import threading
from pathlib import Path

from core import safety

LOGGER = logging.getLogger(__name__)
FRAME_RE = re.compile(r"^[a-z][a-z_]{1,23}$")
# The frames the page knows what to do with. Anything else NPC Forge adds later is ignored until the page learns it.
FRAMES = ("blink", "talking", "happy", "laughing", "sad", "surprised", "smug", "embarrassed", "thinking", "flirty",
          "neutral", "angry", "scared", "disgusted", "tired")
KIDS_KEPT_BACK = frozenset({"flirty"})
EMOTIONS = {"joy": "happy", "sadness": "sad", "anger": "angry", "surprise": "surprised", "fear": "scared",
            "disgust": "disgusted", "amusement": "laughing", "pride": "smug", "embarrassment": "embarrassed",
            "curiosity": "thinking"}


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class LivingStore:
    def __init__(self, avatars, checker=None, sync=False):
        self.avatars = avatars
        self.checker = checker
        self.sync_inline = sync                      # tests: run the sync in the calling thread
        self._lock = threading.Lock()
        self._syncing = set()

    # ---- NPC Forge (read-only) ----------------------------------------------------------------------------------------
    def forge_manifest(self, npc_id) -> dict | None:
        """NPC Forge's living.json for this character, or None."""
        d = self.avatars._forge_dir(npc_id)
        data = _read_json(d / "living" / "living.json") if d else None
        if not data or not isinstance(data.get("frames"), dict) or not isinstance(data.get("portrait"), dict):
            return None
        return data

    def _forge_frame(self, npc_id, frame) -> Path | None:
        d = self.avatars._forge_dir(npc_id)
        if not d or frame not in FRAMES:
            return None
        path = d / "living" / f"{frame}.png"
        return path if path.is_file() else None

    # ---- what Altera has --------------------------------------------------------------------------------------------------
    def _dir(self, avatar_id) -> Path | None:
        d = self.avatars._dir(avatar_id)
        return d / "living" if d else None

    def _state(self, avatar_id) -> dict:
        d = self._dir(avatar_id)
        return (_read_json(d / "living.json") if d else None) or {}

    def _save(self, avatar_id, state) -> None:
        d = self._dir(avatar_id)
        d.mkdir(parents=True, exist_ok=True)
        (d / "living.json").write_text(json.dumps(state, indent=2), "utf-8")

    def _portrait_sha(self, avatar_id) -> str | None:
        path = self.avatars.image_path(avatar_id, "portrait")
        return _sha(path.read_bytes()) if path else None

    def _kept_back(self, avatar_id) -> frozenset:
        """Frames never shown for this person: a pal in kids mode has no flirty face (core/kids.py)."""
        from core import kids
        return KIDS_KEPT_BACK if kids.is_on(self.avatars.get(avatar_id)) else frozenset()

    def frame_path(self, avatar_id, frame) -> Path | None:
        """A frame's file, only while it belongs to the portrait she has now and was not refused."""
        if frame not in FRAMES or frame in self._kept_back(avatar_id):
            return None
        state = self._state(avatar_id)
        if frame not in (state.get("frames") or {}) or state.get("portrait_sha256") != self._portrait_sha(avatar_id):
            return None
        path = self._dir(avatar_id) / f"{frame}.png"
        return path if path.is_file() else None

    def status(self, avatar_id) -> dict:
        """What the page needs: which frames can be shown, how many are age-checked, and why there are none if there are none."""
        with self._lock:
            running = avatar_id in self._syncing
        state = self._state(avatar_id)
        current = state.get("portrait_sha256") and state.get("portrait_sha256") == self._portrait_sha(avatar_id)
        frames = sorted(f for f in (state.get("frames") or {}) if current and self.frame_path(avatar_id, f))
        checked = sum(1 for f in frames if state["frames"][f].get("age_checked"))
        reason = state.get("reason") or ""
        if not frames and not reason:
            reason = "She has no living portrait yet. Make one in NPC Forge (the Living portrait card on her page)."
        return {"available": bool(frames), "frames": frames, "checked": checked, "refused": sorted(state.get("refused") or {}),
                "reason": "" if frames else reason, "synced_at": state.get("synced_at"), "running": running}

    def import_frames(self, avatar_id, package, checks=None):
        """Copy expressions and the verified living frames from a card into Altera's own portrait store."""
        pictures = {EMOTIONS.get(name, name): data for name, data in package.expressions.items()}
        pictures.update(package.frames)
        portrait_sha = self._portrait_sha(avatar_id)
        if not pictures or not portrait_sha:
            return
        checks = {} if checks is None else checks
        d = self._dir(avatar_id)
        d.mkdir(parents=True, exist_ok=True)
        previous = self._state(avatar_id)
        frames, refused = {}, {}
        unavailable = None
        for frame in FRAMES:
            if frame not in pictures:
                continue
            png = pictures[frame]
            sig = _sha(png)
            path = d / f"{frame}.png"
            old = previous.get("frames", {}).get(frame) or {}
            refused_before = previous.get("refused", {}).get(frame) or {}
            if old.get("sig") == sig and old.get("age_checked") and path.is_file():
                frames[frame] = old
                continue
            if refused_before.get("sig") == sig:
                refused[frame] = refused_before
                continue
            check = checks.get(sig)
            if check is None:
                check = unavailable or (self.checker.check(png) if self.checker else {})
                checks[sig] = check
            if check.get("ok") is False:
                refused[frame] = {"sig": sig, "reason": "The frame did not pass the portrait age check."}
                path.unlink(missing_ok=True)
                continue
            if check.get("ok") is None:
                unavailable = check
            path.write_bytes(png)
            frames[frame] = {"sig": sig, "age_checked": check.get("ok") is True, "apparent_age": check.get("apparent_age")}
        self._save(avatar_id, {"portrait_sha256": portrait_sha, "frames": frames, "refused": refused, "synced_at": _now()})

    # ---- keeping in step with NPC Forge ----------------------------------------------------------------------------------
    def sync(self, avatar_id) -> dict:
        """Copy NPC Forge's living portrait of this avatar: new or redone frames are age-checked and copied, frames that are
        gone there are removed here. A refused frame is not offered to the age check again until it changes."""
        record = self.avatars.get(avatar_id)
        result = {"imported": 0, "kept": 0, "removed": 0, "refused": 0, "unchecked": 0}
        if not record:
            return result
        from core.avatars import forge_id, kind_of
        npc_id = forge_id(record)
        if npc_id and self.avatars.forge_mode() == "cards":
            from core.characters import read_package
            package = read_package(npc_id + ".charx", self.avatars.forge_client().card(npc_id), include_voice=False)
            if package.portrait is None or _sha(package.portrait) != self._portrait_sha(avatar_id):
                return result
            self.import_frames(avatar_id, package)
            result["imported"] = len(self.status(avatar_id)["frames"])
            return result
        source = record.get("source") or {}
        if not npc_id and source.get("type") == "character":
            if source.get("file") == avatar_id + ".charx":
                from core import charcard, characters
                path = characters.originals_dir(self.avatars) / source["file"]
                with path.open("rb") as saved:
                    package = charcard.parse_charx(saved.read(charcard.MAX_CHARX_BYTES + 1), include_voice=False)
                if package.portrait and _sha(package.portrait) == self._portrait_sha(avatar_id):
                    self.import_frames(avatar_id, package)
                    result["imported"] = len(self.status(avatar_id)["frames"])
            return result
        state = self._state(avatar_id)
        have = state.get("frames") if isinstance(state.get("frames"), dict) else {}
        refused = state.get("refused") if isinstance(state.get("refused"), dict) else {}
        manifest = self.forge_manifest(npc_id) if npc_id else None
        portrait_sha = self._portrait_sha(avatar_id)

        if not manifest and kind_of(record) != "companion":
            state.update(reason="No living portrait yet. Make one with Bring it to life in their settings on Home (it needs "
                                "a portrait from NPC Forge first).")
        elif not manifest:
            state.update(reason="She has no living portrait yet. Make one in NPC Forge (the Living portrait card on her page).")
        elif manifest["portrait"].get("sha256") != portrait_sha:
            state.update(reason="Her living portrait in NPC Forge was made from a different portrait than the one Altera has. "
                                "Import her again (Manage companions) or remake the living portrait in NPC Forge.")
        else:
            state["reason"] = ""
            d = self._dir(avatar_id)
            d.mkdir(parents=True, exist_ok=True)
            no_answer = None                         # once the vision model has not answered, this pass does not wait on it again
            wanted = [f for f in manifest["frames"] if f in FRAMES and FRAME_RE.match(f)]
            for frame in wanted:
                path = self._forge_frame(npc_id, frame)
                if not path:
                    continue
                png = path.read_bytes()
                sig = _sha(png)
                old = have.get(frame) or {}
                if old.get("sig") == sig and old.get("age_checked") and (d / f"{frame}.png").is_file():
                    result["kept"] += 1
                    continue
                if (refused.get(frame) or {}).get("sig") == sig:
                    result["refused"] += 1
                    continue
                check = no_answer or (self.checker.check(png) if self.checker else {"ok": None, "error": "No age check is configured."})
                if check.get("ok") is False:
                    refused[frame] = {"sig": sig, "reason": f"The age check read this frame as about {check.get('apparent_age')}, "
                                                            f"under {safety.MIN_APPARENT_AGE}."}
                    have.pop(frame, None)
                    (d / f"{frame}.png").unlink(missing_ok=True)
                    result["refused"] += 1
                    continue
                if check.get("ok") is None:
                    no_answer = check
                    result["unchecked"] += 1
                refused.pop(frame, None)
                (d / f"{frame}.png").write_bytes(png)
                have[frame] = {"sig": sig, "age_checked": check.get("ok") is True, "apparent_age": check.get("apparent_age")}
                result["imported"] += 1
            for frame in [f for f in have if f not in wanted]:
                have.pop(frame)
                (d / f"{frame}.png").unlink(missing_ok=True)
                result["removed"] += 1
            state["portrait_sha256"] = portrait_sha
        state.update(frames=have, refused=refused, synced_at=_now(), last=result)
        if self.avatars._dir(avatar_id) and self.avatars._dir(avatar_id).is_dir():
            self._save(avatar_id, state)
        return result

    def sync_async(self, avatar_id) -> bool:
        """Start sync() in the background, one at a time per avatar. False if one is already running."""
        with self._lock:
            if avatar_id in self._syncing:
                return False
            self._syncing.add(avatar_id)

        def job():
            try:
                self.sync(avatar_id)
            except Exception:
                LOGGER.exception("Could not sync the living portrait")
            finally:
                with self._lock:
                    self._syncing.discard(avatar_id)
        if self.sync_inline:
            job()
        else:
            from core.state_gate import start_job
            try:
                start_job(job, name="altera-living-sync")
            except Exception:
                with self._lock:
                    self._syncing.discard(avatar_id)
                raise
        return True

    def forget(self, avatar_id) -> None:
        """Remove her copy (the avatar was imported again with another portrait, or removed)."""
        d = self._dir(avatar_id)
        if d and d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
