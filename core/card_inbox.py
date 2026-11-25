"""Receive cards through the suite inbox without exposing machine paths."""

import hashlib
import logging
import os
import re
import stat
import threading
from pathlib import Path

from core import charcard, characters, fels_suite

LOGGER = logging.getLogger(__name__)
KINDS = (".charx", ".png")
LOCK = threading.Lock()


def _plain(path, *, directory=False):
    info = path.lstat()
    wanted = stat.S_ISDIR if directory else stat.S_ISREG
    if not wanted(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise OSError("The inbox contains a link or a special file.")
    return info


def _inbox():
    folder = fels_suite.inbox_dir("altera")
    for path in (folder.parent.parent, folder.parent, folder):
        _plain(path, directory=True)
    return folder


def _signature(info):
    return info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size


def _entries():
    try:
        folder = _inbox()
    except OSError:
        return
    for item in fels_suite.inbox_list("altera", KINDS, charcard.MAX_CHARX_BYTES):
        name = item["name"]
        if Path(name).suffix.lower() not in KINDS:
            continue
        path = folder / name
        try:
            signature = _signature(_plain(path))
        except OSError:
            continue
        limit = charcard.file_limit(name)
        public = {key: item[key] for key in ("name", "sender", "sender_name", "sent", "problem")}
        stem = Path(name).stem
        if item["sent"] and item["sender"]:
            stem = stem.split("-", 1)[1].removeprefix(item["sender"] + "-")
            if item["sender"] == "npc-forge":
                stem = re.sub(r"-[0-9a-f]{6}(?:-[0-9]+)?$", "", stem).replace("-", " ").title()
        public["label"] = stem or name
        public["id"] = hashlib.sha256(repr((name, signature)).encode("utf-8", errors="surrogatepass")).hexdigest()
        if signature[-1] > limit:
            public["problem"] = f"This card is larger than {limit // 1048576} MB. Ask for a smaller card or dismiss it."
        yield public, path, signature


def waiting():
    return [entry for entry, _, _ in _entries()]


def _entry(key):
    match = next((row for row in _entries() if row[0]["id"] == key), None)
    if match is None:
        raise characters.CharacterError("This card is no longer in the inbox. Refresh Home and try again.", 404)
    return match


def _verify(path, signature):
    if path.parent != _inbox() or _signature(_plain(path)) != signature:
        raise OSError("The inbox file changed.")


def _read(path, signature):
    limit = charcard.file_limit(path.name)
    try:
        _verify(path, signature)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        with os.fdopen(os.open(path, flags), "rb") as stream:
            if _signature(os.fstat(stream.fileno())) != signature:
                raise OSError("The inbox file changed.")
            data = stream.read(limit + 1)
        _verify(path, signature)
        return data
    except OSError:
        raise characters.CharacterError("The card changed while Altera was reading it. Refresh Home and try again.", 409) from None


def _done(path, signature):
    _verify(path, signature)
    fels_suite.inbox_done(path)


def dismiss(key):
    with LOCK:
        _, path, signature = _entry(key)
        try:
            _done(path, signature)
        except (OSError, ValueError):
            raise characters.CharacterError("Altera could not dismiss this card. Close any app using the file and try again.", 409) from None


def accept(services, key):
    with LOCK:
        entry, path, signature = _entry(key)
        if entry["problem"]:
            raise characters.CharacterError(entry["problem"], 413)
        store = services.avatars
        # A successful local import can be retried if Windows could not recycle the file.
        existing = next((row for row in store.list() if (row.get("source") or {}).get("inbox_key") == key), None)
        if existing:
            record = store.public(existing)
        else:
            record = characters.import_card(store, path.name, _read(path, signature), checker=services.checker,
                                            living=services.living, inbox_key=key)
        try:
            _done(path, signature)
        except (OSError, ValueError):
            LOGGER.warning("An imported card could not be cleared from the suite inbox.")
            return record, "The character was imported. Dismiss its card later to clear the inbox."
        return record, ""
