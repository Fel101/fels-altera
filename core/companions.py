"""
Altera — Companions: one avatar, one life, one continuous chat.

Every avatar (a character made in NPC Forge) is a companion with a chat of her own, notes of her own (core/notes.py) and memories
of her own. Meeting someone new means importing another avatar; the old ones stay, and their chats are still there when you
go back to them.

The default persona from personality.yaml is only used when there is no avatar at all.
"""
import json
import logging
import shutil
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path

from core import companion
from core.avatars import forge_id
from core.datastore import get_datastore

LOGGER = logging.getLogger(__name__)

NEWBORN_MESSAGES = 16            # for this many messages from the person she is still finding her feet


class CompanionError(ValueError):
    """Something the user can fix: the message is safe to show."""

    def __init__(self, message, status=404):
        super().__init__(message)
        self.status = status


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def newborn_messages(config=None) -> int:
    try:
        return max(0, int(((config or {}).get("companion") or {}).get("newborn_messages", NEWBORN_MESSAGES)))
    except (TypeError, ValueError):
        return NEWBORN_MESSAGES


def avatar_for_session(session_id) -> dict | None:
    """The avatar a chat belongs to. A chat from before companions (or one with no avatar of its own) belongs to the active
    avatar, so an older chat still gets a persona."""
    services = companion.get_services()
    if session_id:
        info = get_datastore().get_session(session_id)
        if info and info.avatar_id:
            record = services.avatars.get(info.avatar_id)
            if record:
                return record
            raise CompanionError('This conversation’s companion is unavailable. Restore or reimport her before continuing.', 409)
    return services.avatars.active()


def is_newborn(session_id, config=None) -> bool:
    """True while she is still in her first days with this person."""
    return get_datastore().count_user_messages(session_id) < newborn_messages(config)


def message_count(session_id) -> int:
    return get_datastore().get_message_count(session_id)


def summary(record: dict, session_id: int | None = None) -> dict:
    """What the page needs to list or show a companion."""
    services = companion.get_services()
    ds = get_datastore()
    info = services.avatars.public(record)
    count = ds.get_message_count(session_id) if session_id else 0
    state = services.avatars.state(record["id"])
    info.update({
        "session_id": session_id, "messages": count, "met": bool(count),
        "last_at": ds.last_message_at(session_id) if session_id else "",
        "has_self_view": bool(state.get("self_view")),
    })
    return info


def open_companion(avatar_id, *, activate=True) -> dict:
    """Make an avatar the one you are talking to: activate it, take up any edits made in NPC Forge, make sure it has its chat.

    activate=False opens it without making it the household's current person: a browser in the kids view keeps its own
    current pal (core.lock), so a tablet in the kids view never changes who another browser opens with."""
    services = companion.get_services()
    record = services.avatars.get(avatar_id)
    if not record:
        raise CompanionError("That companion was not found.")
    if activate:
        services.avatars.activate(avatar_id)
    try:
        services.avatars.refresh_card(avatar_id)
        record = services.avatars.get(avatar_id) or record
    except Exception:
        LOGGER.exception("Could not refresh the card from NPC Forge")
    ds = get_datastore()
    session_id = ds.ensure_avatar_session(avatar_id, record.get("name") or avatar_id)
    if activate:
        ds.set_last_active_session(session_id)
    try:
        from core import addons
        if addons.picture_source():
            services.photos.sync_premade_async(avatar_id)
    except Exception:
        LOGGER.exception("Could not start the picture sync")
    try:
        if forge_id(record) and services.avatars.forge_available():
            services.living.sync_async(avatar_id)
    except Exception:
        LOGGER.exception("Could not start the living-portrait sync")
    return summary(record, session_id)


def current() -> dict:
    """Who the page opens with: the companion you talked to last (opened, so she has her chat), or nobody yet."""
    services = companion.get_services()
    avatar_id = services.avatars.active_id()
    if not avatar_id:
        listing = services.avatars.list()
        avatar_id = listing[0]["id"] if listing else None
    if not avatar_id:
        return {"avatar": None, "session_id": None}
    opened = open_companion(avatar_id)
    return {"avatar": opened, "session_id": opened["session_id"]}


def list_companions() -> list:
    """Every companion, the one you talked to most recently first."""
    services = companion.get_services()
    ds = get_datastore()
    rows = []
    for record in services.avatars.list():
        session_id = ds.get_avatar_session_id(record["id"])
        rows.append(summary(record, session_id))
    rows.sort(key=lambda r: (r["last_at"] or "", r.get("imported") or ""), reverse=True)
    return rows


def feature_on(avatar_id, name) -> bool:
    """Is memory / notes / story / presence used for this person? Always for companions, characters and the default
    persona ('' owner); for a pal only when switched on."""
    from core.avatars import features_of
    if not avatar_id:
        return True
    record = companion.get_services().avatars.get(avatar_id)
    return features_of(record)[name] if record else True


def migrate_kinds() -> int:
    """Everyone saved before there were kinds becomes a companion (written into avatar.json once)."""
    return companion.get_services().avatars.migrate_kinds()


def people(kind=None) -> dict:
    """The start screen: everyone (most recent first), how many of each kind, and who to continue with.

    Reads only; nobody is opened or activated. The person to continue with is the one you talked to last (the active one),
    else the most recently talked-to. kind narrows the list, not the counts.
    """
    from core.avatars import KINDS, kind_of
    if kind is not None and kind not in KINDS:
        raise CompanionError("Unknown kind. Use pal, character or companion.", 400)
    rows = list_companions()
    counts = dict.fromkeys(KINDS, 0)
    for row in rows:
        counts[kind_of(row)] += 1
    active = companion.get_services().avatars.active_id()
    last = next((row for row in rows if row["id"] == active), None) or next((row for row in rows if row["messages"]), None)
    shown = [row for row in rows if kind_of(row) == kind] if kind else rows
    return {"people": shown, "counts": counts, "kinds": list(KINDS), "last": last, "kind": kind}


def mark_met(avatar_id) -> None:
    """Note the moment she and the person first spoke."""
    services = companion.get_services()
    if not services.avatars.state(avatar_id).get("first_met"):
        services.avatars.update_state(avatar_id, first_met=_now())


@contextmanager
def _removing(ds, notes_service):
    """Reserve all writers before destructive work; a refusal must leave every file intact."""
    from flask import g, has_request_context
    from core.state_gate import STATE
    from core.turns import get_turn_service
    own = getattr(g, 'altera_state_lease', None) if has_request_context() else None
    with get_turn_service(ds).lock:
        with STATE.lock:
            if STATE.maintenance or STATE.fault or any(key != getattr(own, 'key', None) for key in STATE.active):
                raise CompanionError('Wait for replies, pictures and notes to finish before removing a companion.', 409)
            STATE.maintenance = True
        try:
            with notes_service._lock:
                if notes_service._busy:
                    raise CompanionError('Wait for the notes update to finish before removing a companion.', 409)
                yield
        finally:
            with STATE.lock:
                STATE.maintenance = False


def remove(avatar_id, memory_manager=None) -> dict:
    """Remove a companion for good: her chat, notes, memories, pictures and avatar. NPC Forge's copy is not touched."""
    services = companion.get_services()
    if not services.avatars.get(avatar_id):
        raise CompanionError("That companion was not found.")
    ds = get_datastore()
    from core.notes import get_notes_service
    with _removing(ds, get_notes_service()):
        # Check every chat before deleting pictures or notes, including ledger work
        # whose worker is no longer represented by a background lease.
        chats = ds.list_avatar_sessions(avatar_id)
        with closing(ds._connect()) as conn:
            if conn.execute("SELECT 1 FROM chat_turns t JOIN sessions s ON s.id=t.session_id WHERE s.avatar_id=? AND t.status IN ('sending','streaming')", (avatar_id,)).fetchone():
                raise CompanionError('Wait for the reply to finish before removing this companion.', 409)
        if memory_manager is None:
            from core.memory import memory_manager
        store = memory_manager.store
        # Read the memory index before destructive writes. An unavailable store
        # must not be reported as a successful complete removal.
        found = store.get_all(avatar_id=avatar_id)
        if found:
            store.collection.delete(ids=[m["id"] for m in found])
        memories = len(found)
        from core.presence import get_presence_service
        get_presence_service(ds).clear_owner(avatar_id)
        conn = ds._connect()
        try:
            with conn:
                # Removal already owns maintenance. Use the prepared schema and
                # this transaction; lazy service recovery would request a new lease.
                from core.reflection import ReflectionService
                ReflectionService._clear(conn, 'avatar_id', avatar_id)
                from core.expression import invalidate_owner
                invalidate_owner(conn, avatar_id)
                from core.episodes import delete_owner
                delete_owner(conn, avatar_id)
        finally:
            conn.close()
        for info in chats:
            ds.delete_session(info.id)
        photos = services.photos.delete_for_avatar(avatar_id)
        services.avatars.delete(avatar_id)                       # her folder, and with it her notes
        from core import characters
        characters.delete_original(services.avatars, avatar_id)  # an imported card's original file goes too
    return {"photos_removed": photos, "chats_removed": len(chats), "memories_removed": memories}


# ---- from chats before companions ------------------------------------------------------------------------------------
def migrate_legacy(datastore=None, avatars=None, assign_memories=None, data_dir=None) -> dict:
    """Once: the chats and memories from before companions become the companion you have now.

    All those chats are merged into one continuous chat (the messages keep their order; nothing is deleted; the emptied chats
    stay, archived) and the memories are given to her. The database is copied first. Runs again only if it could not run
    because there was no companion yet.
    """
    services = companion.get_services()
    ds = datastore or get_datastore()
    avatars = avatars or services.avatars
    data_dir = Path(data_dir or avatars.root.parent)
    flag = data_dir / "companions.json"
    if flag.exists():
        return {"skipped": "already done"}
    # The old chats and memories were with the default companion, so they go to a companion, never to a pal or character.
    from core.avatars import kind_of
    companions_only = [r["id"] for r in avatars.list() if kind_of(r) == "companion"]
    active = avatars.active_id()
    owner = active if active in companions_only else next(iter(companions_only), None)
    if not owner:
        return {"skipped": "no companion yet"}

    db = Path(ds.db_path)
    if db.is_file():
        backup = db.with_name(db.stem + ".before-companions.db")
        if not backup.exists():
            shutil.copy2(db, backup)

    result = {"owner": owner, "sessions": 0, "messages": 0, "memories": 0, "migrated_at": _now()}
    loose = [s for s in ds.list_sessions(archived=None) if not s.avatar_id]
    if loose:
        main = next((s for s in loose if not s.archived), loose[0])
        ds.set_session_avatar(main.id, owner)
        others = [s for s in loose if s.id != main.id]
        result["messages"] = ds.move_messages([s.id for s in others], main.id)
        for s in others:
            ds.set_session_avatar(s.id, owner)
            ds.archive_session(s.id)
        if main.archived:
            ds.unarchive_session(main.id)
        result["sessions"] = len(loose)
    try:
        if assign_memories is None:
            from core.memory import memory_manager
            assign_memories = memory_manager.store.assign_unowned
        result["memories"] = assign_memories(owner)
    except Exception:
        LOGGER.exception("Could not give the old memories to the companion")
    flag.write_text(json.dumps(result, indent=2), "utf-8")
    LOGGER.info("Chats from before companions now belong to %s: %s", owner, result)
    return result
