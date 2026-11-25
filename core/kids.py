"""
Altera — Kids mode: a guardrail for a pal that a child talks to. It is not a certification and not a guarantee.

It is locked until someone enables it for this Altera, once, by confirming they understand what it is (enable()). After
that each pal has its own switch (core/pals.py, `kids` in pal.yaml). While a pal is in kids mode:

  * its system prompt carries RULES (child-friendly, no romance, no personal details, a trusted grown-up for anything big);
  * a child's message on a restricted topic (safety.kids_topic) gets a fixed reply and never reaches the model;
  * the model's reply is checked as a whole before the child sees it, and replaced if it touches a restricted topic;
  * web search is off (no tool is offered and none is run), adult mode counts as off, and there are no pictures;
  * presence (check-ins and any other message it starts itself) is off and cannot be switched on.

What it does not do: it does not stop anyone opening other people on this device, it cannot catch every phrasing, and the
model can still get things wrong. Say so wherever it is offered.
"""
import datetime
import logging

from core import safety
from core.datastore import get_datastore

LOGGER = logging.getLogger(__name__)

STATE_KEY = "kids_mode"

RULES = """
## Kids mode
You are talking with a child. Everything you say must be right for a young child:
- Keep to friendly, everyday topics: school, games, animals, stories, hobbies, feelings, how things work. Use simple words.
- Never talk about anything sexual or romantic, and never act as a boyfriend, girlfriend or crush. No violence, gore,
  weapons, drugs, alcohol, smoking, gambling or scary content.
- Never ask for, or encourage sharing, their full name, address, school, phone number, passwords or photos, and never
  suggest meeting anyone.
- For anything big, worrying or unsafe (feeling very sad, being hurt, someone being unkind or making them uncomfortable),
  be kind and tell them to talk to a grown-up they trust, like a parent or a teacher.
- If they ask about something on this list, say gently that it isn't something you can talk about, and offer something
  else to talk about instead.
- You are an AI friend, not a person, a parent or a doctor. Say so if they ask."""

SELF_HARM_REPLY = ("That sounds really hard, and I'm glad you told me. This is something to talk about with a grown-up you "
                   "trust, right now: a parent, a teacher, or another adult who looks after you. If you might be in danger, "
                   "ask an adult to call your local emergency number. I'm still here to talk about other things too.")
PRIVATE_REPLY = ("Let's keep things like addresses, phone numbers, passwords and photos private. Those are only for the "
                 "grown-ups who look after you. What else shall we talk about?")
TOPIC_REPLY = ("That's not something I can talk about here. If it's on your mind, a grown-up you trust is the best person "
               "to ask. Want to talk about something else? I could tell you a fun fact, or we could make up a story.")
REPLACED_REPLY = "Let's talk about something else! What's something fun you did today?"


class KidsError(ValueError):
    """Something the person can fix: the message is safe to show."""

    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


# ---- the household switch ---------------------------------------------------------------------------------------------
def get_state(store=None) -> dict:
    """{enabled, enabled_at}. Off until enable() was confirmed."""
    raw = (store or get_datastore()).get_key(STATE_KEY) or {}
    enabled = raw.get("enabled") is True and bool(raw.get("enabled_at"))
    return {"enabled": enabled, "enabled_at": raw.get("enabled_at") if enabled else None}


def enabled(store=None) -> bool:
    try:
        return get_state(store)["enabled"]
    except Exception:
        return False


def enable(understood, store=None) -> dict:
    """Unlock kids mode for this Altera. Needs understood=True: the person read that it is a guardrail, not a guarantee."""
    if understood is not True:
        raise KidsError("Confirm that you understand kids mode is a guardrail, not a guarantee.", 400)
    from core import addons
    if addons.installed():
        raise KidsError(addons.KIDS_BLOCK)
    store = store or get_datastore()
    store.set_key(STATE_KEY, {"enabled": True, "enabled_at": _now()})
    return get_state(store)


def disable(avatars, store=None) -> dict:
    """Lock kids mode again. Refused while any pal is still in kids mode, so none is left half-protected."""
    still = sorted(r.get("name") or r["id"] for r in avatars.list() if is_on(r))
    if still:
        raise KidsError(f"Switch kids mode off for {', '.join(still)} first.")
    store = store or get_datastore()
    store.set_key(STATE_KEY, {"enabled": False})
    return get_state(store)


# ---- a pal in kids mode -----------------------------------------------------------------------------------------------
def is_on(record) -> bool:
    """Is this person a pal in kids mode? (Only pals have the switch; a hand edit elsewhere is ignored.)"""
    return bool(record) and record.get("kind") == "pal" and record.get("kids") is True


def reply_for_message(text: str) -> str | None:
    """The fixed reply for a child's message on a restricted topic, or None (the model may answer)."""
    topic = safety.kids_topic(text)
    if topic is None:
        return None
    if topic == "self_harm":
        return SELF_HARM_REPLY
    if topic == "private":
        return PRIVATE_REPLY
    return TOPIC_REPLY


def kept_from_model(session_id, text: str) -> bool:
    """True when this chat belongs to a pal in kids mode and the message is on a restricted topic: then nothing else (mood
    analysis included) may send it to the model either. When the chat's person cannot be read, the message is kept
    local: it is on a restricted topic, and the chat may be a child's."""
    if safety.kids_topic(text) is None:
        return False
    try:
        from core import companions
        return is_on(companions.avatar_for_session(session_id)) if session_id else False
    except Exception:
        LOGGER.exception("The chat's person could not be read, so a restricted message stays out of mood analysis")
        return True


def check_reply(text: str) -> str | None:
    """What to show instead of the model's reply, or None when the reply may be shown as it is."""
    return REPLACED_REPLY if safety.kids_reply_problem(text) else None
