"""Starter characters: character cards made with NPC Forge that come with Altera (content/starters), offered on Home.

Nothing is added until someone chooses Add. Adding imports the card the way an imported file is imported: the safety
rules, the picture age check and the card's voice, which is offered for review and never sent to Voice Lab on its own. A
starter that has been added is not offered again unless its character is removed.
"""

import json
import re
import threading
from pathlib import Path

from core import characters

ROOT = Path(__file__).resolve().parents[1] / "content" / "starters"
_KEY = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?")
_LOCK = threading.Lock()
_PORTRAITS = {}                                 # key -> ((path, size, mtime), the card's portrait), read once


def catalogue(root=None) -> list:
    """The starters listed in starters.json whose card file is there, in the listed order."""
    root = Path(root or ROOT)
    try:
        listed = json.loads((root / "starters.json").read_text(encoding="utf-8")).get("starters")
    except (OSError, ValueError, AttributeError):
        return []
    rows = []
    for item in listed if isinstance(listed, list) else []:
        if not isinstance(item, dict):
            continue
        key, name, file = item.get("key"), item.get("name"), item.get("file")
        if (isinstance(key, str) and _KEY.fullmatch(key) and isinstance(name, str) and name.strip()
                and isinstance(file, str) and Path(file).name == file and file.endswith(".charx") and (root / file).is_file()):
            rows.append({"key": key, "name": name.strip(), "line": str(item.get("line") or "").strip(), "path": root / file})
    return rows


def _entry(key, root=None):
    entry = next((row for row in catalogue(root) if row["key"] == key), None)
    if entry is None:
        raise characters.CharacterError("That starter character is not part of this Altera.", 404)
    return entry


def _added(store) -> dict:
    found = {}
    for row in store.list():
        key = (row.get("source") or {}).get("starter")
        if isinstance(key, str):
            found.setdefault(key, row)
    return found


def offered(store, root=None) -> list:
    """The starters not added yet: key, name and a line about them. Paths stay on the server."""
    added = _added(store)
    return [{"key": row["key"], "name": row["name"], "line": row["line"]} for row in catalogue(root) if row["key"] not in added]


def portrait(key, root=None) -> bytes:
    path = _entry(key, root)["path"]
    stat = path.stat()
    stamp = (str(path), stat.st_size, stat.st_mtime_ns)
    cached = _PORTRAITS.get(key)
    if not cached or cached[0] != stamp:
        package = characters.read_package(path.name, path.read_bytes(), include_voice=False)
        if not package.portrait:
            raise characters.CharacterError("This starter character has no picture.", 404)
        cached = _PORTRAITS[key] = (stamp, package.portrait)
    return cached[1]


def add(services, key, root=None) -> dict:
    """Import the starter's card once; choosing Add again opens the character already added."""
    entry = _entry(key, root)
    store = services.avatars
    with _LOCK:
        existing = _added(store).get(key)
        if existing:
            return store.public(existing)
        path = entry["path"]
        return characters.import_card(store, path.name, path.read_bytes(), checker=services.checker,
                                      living=services.living, starter=key)
