"""
Altera — Chat Engine (Session 1 of Architecture Refactor)

Owns all conversation flow logic: context building, LLM calling, and response generation.
altera.py routes call chat_engine.send_message() instead of inline chat logic.

This is the deep interface for "how Altera talks."
"""
from core.prompts import PromptBuilder
from core.llm import LLMClient, StreamComplete
import json
import logging
import re
import threading
from contextlib import ExitStack
from dataclasses import dataclass, field

from core import adult, companions, photo_tags, safety, suite_gpu
from core import notes as notes_module
from core import photos as photos_module
from core.turns import CURRENT_TURN, EmptyReply
from core.owned_iterator import owned_iterator
from core.datastore import get_datastore
from core.web_search import create_web_searcher

LOGGER = logging.getLogger(__name__)

# How many times a reply that came back empty (a reasoning model's thinking alone, or a stop at once) is asked for again
EMPTY_REPLY_RETRIES = 1
# What she says when a sexual message points at a minor. The model is never called for these (see core/safety.py).
REFUSAL = "I won't go there. Let's talk about something else."
# The cue for her very first words (a new companion speaks first). It is sent to the model but never saved as a message.
GREETING_CUE = ("(You have just come into being. Nobody has spoken to you yet. Say your first words to the person who is here: "
                "a few natural sentences, in your own voice.)")
# A pal was just picked by the person; it says hello in its own style instead of waking up.
PAL_GREETING_CUE = ("(The person has just picked you as their pal and opened this chat. Say hello in your own style: one or two "
                    "short, friendly sentences, with at most one easy question.)")
_PHOTO_MARKER = re.compile(r"\n?\[\[photo:(p-[0-9a-f]{10})\]\]")


# ============================================================
# Context Data Classes (the seam between ChatEngine and core/)
# ============================================================

@dataclass
class ChatContext:
    """Composed context for a single chat turn. Built from multiple core modules."""
    system_prompt: str = ""
    conversation_history: list[dict] = field(default_factory=list)
    relevant_memories: list[dict] = field(default_factory=list)
    emotion_data: dict = field(default_factory=dict)
    expression: dict = field(default_factory=dict)
    episodes: list[dict] = field(default_factory=list)
    reflection_notes: dict = field(default_factory=dict)
    joke_material: list[dict] = field(default_factory=list)
    humor_style: dict = field(default_factory=dict)
    avatar: dict | None = None                                   # the active avatar record (imported from NPC Forge)
    adult: dict = field(default_factory=lambda: {"effective_level": "off"})
    photos_enabled: bool = False                                    # an avatar with a portrait is active
    premade: list = field(default_factory=list)                     # ready-made pictures she can send at the current level
    premade_total: int = 0                                          # how many ready-made pictures she has in all
    avatar_id: str = ""                                             # the companion this chat belongs to ('' = default persona)
    notes: dict = field(default_factory=dict)                       # her own notes: {"about_you": ..., "our_story": ...}
    newborn: bool = False                                           # she is still in her first days with this person
    self_view: str = ""                                             # how she describes her own face
    lore: str = ""                                                  # a character's lorebook entries the chat called up


# LLM Client imported from core/llm.py (Session 2)


# ============================================================
# Prompt Builder (Session 3 — extracted to core/prompts.py)
# ============================================================



# ============================================================
# Web Search Tool Definition
# ============================================================

WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the web for real-time information: weather, news, facts, current events.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to search for. Be specific and concise."
                },
                "provider": {
                    "type": "string",
                    "description": "Search provider to use",
                    "enum": ["duckduckgo", "wikipedia", "openweathermap"],
                    "default": "duckduckgo"
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return",
                    "minimum": 1,
                    "maximum": 10,
                    "default": 5
                }
            },
            "required": ["query"]
        }
    }
}


# ============================================================
# ChatEngine (the main deep interface — Candidate 1)
# ============================================================

class ChatEngine:
    """Owns all conversation flow logic. altera.py routes call send_message().

    Composes context from memory/emotion/jokes/opinions modules through their
    existing interfaces (no new dependencies needed).

    Usage in altera.py:
        engine = ChatEngine(config, personality)
        response = engine.send_message(user_text, session_id, db_conn)
    """

    def __init__(self, config: dict, personality: dict):
        self.config = config
        self.personality = personality
        # A refreshed engine must not mutate the prompt or persona of a reply
        # still running on the previous engine.
        self.prompt_builder = PromptBuilder(personality, config)
        self._llm_lock = threading.Lock()  # Serialize LLM calls (prevents LM Studio race conditions)
        self.web_searcher = create_web_searcher(config)
        self._supports_tools = None       # whether this backend accepts `tools`; probed once, see _server_accepts_tools
        self._tools_lock = threading.Lock()
        self._llm_client = None  # Lazy initialization to get singleton reference

    @property
    def llm_client(self) -> LLMClient:
        """Get the current LLMClient singleton instance (reflects runtime changes)."""
        if self._llm_client is None:
            self._llm_client = LLMClient.get_instance(self.config)
        return LLMClient.get_instance(self.config)  # Always return current singleton

    def _reply_client(self, avatar_id):
        """The client for this person's replies: the shared one, with their own temperature and reply length laid over it
        when they have chosen any (core/sampling.py)."""
        client = self.llm_client
        if not avatar_id or not isinstance(client, LLMClient):
            return client
        from core import companion, sampling
        try:
            overrides = sampling.for_reply(companion.get_services().avatars, avatar_id)
        except Exception:
            LOGGER.exception("Could not read this person's reply settings")
            overrides = {}
        return client.for_person(overrides)

    def send_message(self, user_text: str, session_id: int, stream: bool = False, return_photos: bool = False, greet: bool = False):
        """Main entry point: receive a message and return Altera's response.

        Args:
            user_text: The user's message text.
            session_id: Current conversation session ID.
            stream: If True, returns generator of text chunks. If False, returns full string.
            return_photos: Non-stream only: return (text, photos) instead of just the text.
            greet: She speaks first (a new companion's first words): user_text is ignored and nothing is saved for the person.

        Returns:
            Full response string (non-stream) or generator of chunk strings (stream). The stream can also yield
            {"photo": {...}} dicts, after the text, for each picture she started.
        """
        turn = CURRENT_TURN.get()
        if turn:
            turn.check_cancelled()
        # An owned chat must never fall back to another active companion if its
        # avatar was removed outside the companion controls.
        owner = companions.avatar_for_session(session_id)
        from core import kids
        kids_on = kids.is_on(owner)
        try:
            addon_level = not kids_on and adult.effective_level() != "off"
        except Exception:
            addon_level = False
        kids_reply = None
        regenerating = bool(turn and turn.regenerate)
        if regenerating:
            user_text = turn.regenerate["user_text"]
            greet = turn.regenerate.get("greet", False)
            turn.update(user_message_id=turn.regenerate.get("user_message_id"))
        if greet:
            user_text = ""
        else:
            # 0. Hard rule: a sexual message that points at a minor never reaches the model, is not saved and is not remembered.
            if safety.blocks_message(user_text) or (addon_level and safety.hard_problem(user_text)):
                LOGGER.warning("Blocked a message by the content safety rules")
                if stream:
                    return iter([REFUSAL, None])
                return (REFUSAL, []) if return_photos else REFUSAL

            # Kids mode: a restricted topic gets a fixed reply and never reaches the model (core/kids.py)
            if kids_on:
                kids_reply = kids.reply_for_message(user_text)

            # 1. Save user message
            if not regenerating:
                self._save_message(session_id, "user", user_text)


        if turn:
            turn.check_cancelled()
        # 3. Build context (her persona, notes, memories, mood)
        context = self.build_context(session_id)
        if context.avatar_id and not greet:
            companions.mark_met(context.avatar_id)

        # 4. Get conversation history for the LLM
        messages = self._history_for(session_id, context)

        # 5. Call LLM with tools
        llm_messages = [
            {"role": "system", "content": context.system_prompt}
        ] + messages
        from core.avatars import kind_of
        kind = kind_of(context.avatar) if context.avatar else ""
        fixed_reply = kids_reply
        if greet:
            if kind == "character":
                fixed_reply = self._character_greeting(context.avatar, regenerating) or None
            cue = PAL_GREETING_CUE if kind == "pal" else GREETING_CUE
            if fixed_reply is None:
                llm_messages.append({"role": "user", "content": cue})
        elif kind == "character" and llm_messages[-1]["role"] == "user":
            # The card's post-history instructions go after the conversation, with the newest message (never saved)
            from core import characters
            after = characters.post_history(context.avatar)
            if after:
                llm_messages[-1] = {**llm_messages[-1], "content": llm_messages[-1]["content"] + "\n\n[" + after + "]"}
        system_tokens = notes_module.estimate_tokens(context.system_prompt)

        def after_reply():
            if regenerating:
                return None
            action = self._notes_after_reply(session_id, context, system_tokens)
            self._memories_after_reply(context)
            return action


        # Kids mode: no web search, and the model's whole reply is checked before the child sees it
        guard = kids.check_reply if kids_on and fixed_reply is None else None
        if addon_level:
            def guard(text):
                return REFUSAL if safety.hard_problem(text) else None
        client = self._reply_client(context.avatar_id)
        if stream:
            return self._call_llm_stream(llm_messages, session_id, user_text, after_reply, fixed_reply=fixed_reply,
                                         allow_tools=not kids_on, guard=guard, client=client)
        reply = fixed_reply if fixed_reply is not None else self._call_llm(llm_messages, session_id, allow_tools=not kids_on,
                                                                           client=client)
        if guard:
            replacement = guard(reply)
            if replacement:
                self._persist_reply(session_id, replacement)
                after_reply()
                return (replacement, []) if return_photos else replacement
        text, tags = photo_tags.extract(reply)
        photos = self._start_photos(tags, user_text, session_id)
        self._persist_reply(session_id, photo_tags.tidy(text) + "".join(f"\n[[photo:{p['id']}]]" for p in photos))
        after_reply()
        return (text, photos) if return_photos else text

    @staticmethod
    def _character_greeting(avatar, regenerating=False) -> str:
        """A character's first message, as the card wrote it. Asking again picks one of its alternate greetings."""
        from core import characters
        first = characters.first_message(avatar)
        if not regenerating:
            return first
        card = (avatar or {}).get("card") or {}
        name = card.get("name") or (avatar or {}).get("name") or ""
        others = [characters.render(g, name, characters.user_name()).strip() for g in card.get("alternate_greetings") or []]
        choices = [g for g in [first, *others] if g]
        import random
        return random.choice(choices) if choices else first

    def build_context(self, session_id: int) -> ChatContext:
        """Compose everything she knows for one turn: who she is, her notes about the person, memories, mood, pictures.

        Each chat belongs to one companion, so only that companion's avatar, notes and memories are used: a new avatar knows
        nothing about the person until she has been told.
        """
        ctx = ChatContext()
        ds = get_datastore()
        services = None

        # Optional image/adult helpers must not change the personal-data owner.
        ctx.avatar = companions.avatar_for_session(session_id)
        ctx.avatar_id = (ctx.avatar or {}).get('id') or ''

        # Who she is, the adult-mode level and whether she can send pictures. If any of this fails, adult mode counts as off.
        try:
            from core import companion
            services = companion.get_services()
            ctx.adult = adult.get_state()
            ctx.photos_enabled = companion.photos_ready(ctx.avatar_id or None)
            if ctx.photos_enabled:
                level = ctx.adult.get("effective_level", "off")
                # Pictures approved in NPC Forge's studio since she was opened are fetched now, in the background (a pass over
                # nothing new is a few file reads)
                services.photos.sync_premade_async(ctx.avatar_id)
                pool = services.photos.list_premade(ctx.avatar_id, level)
                latest = (ds.get_recent_user_context(session_id, limit=1) or [""])[0]
                ctx.premade_total = len(pool)
                # the ones that fit what was just said first, then a random handful, so she does not repeat herself
                ctx.premade = services.photos.rank_premade(pool, latest, 12)
        except companions.CompanionError:
            raise
        except Exception:
            LOGGER.exception("Error loading avatar / adult-mode state")
            ctx.adult, ctx.photos_enabled, ctx.premade = {"effective_level": "off"}, False, []
        from core import kids
        if kids.is_on(ctx.avatar):                 # kids mode: adult mode counts as off and there are no pictures
            ctx.adult, ctx.photos_enabled, ctx.premade = {"effective_level": "off"}, False, []
        avatar_id = ctx.avatar_id or None
        # What this person keeps beyond the chat: everything for companions, only what was switched on for a pal
        from core.avatars import features_of, kind_of
        keeps = features_of(ctx.avatar) if ctx.avatar else features_of(None)
        if ctx.avatar and kind_of(ctx.avatar) == "character":
            # The card's lorebook: entries whose keys the last few messages (the new one included) mention
            try:
                from core import characters, companion
                ctx.lore = characters.lore(companion.get_services().avatars, ctx.avatar, self._recent_texts(session_id),
                                           self.config)
            except Exception:
                LOGGER.exception("Could not read the character's lorebook")
                ctx.lore = ""

        # Memories, only this companion's (semantic search over what was said to her)
        try:
            from core.memory import memory_manager
            recent_context = ds.get_recent_user_context(session_id, limit=5) if keeps["memory"] else []
            if recent_context:
                kwargs = {"avatar_id": avatar_id or ''}
                ctx.relevant_memories = memory_manager.get_relevant_memories(
                    recent_context[0], limit=self.config['memory'].get('top_k', 5), **kwargs)
        except Exception:
            LOGGER.exception("Error building memory context")

        if avatar_id:
            # Her own notes, her first days, how she sees herself, and the person's recent mood in THIS chat
            try:
                ctx.notes = notes_module.get_notes_service().store.read(avatar_id) if keeps["notes"] else {}
                # the first-days persona is a companion's; a pal or a character starts as itself
                ctx.newborn = kind_of(ctx.avatar) == "companion" and companions.is_newborn(session_id, self.config)
                ctx.self_view = (services.avatars.state(avatar_id).get("self_view") or "") if services else ""
                from core.knowledge import prompt_messages
                moods = [(m.mood or {}).get("primary_emotion") for m in prompt_messages(ds, session_id, ds.get_messages(session_id, limit=20)) if m.role == "user"]
                ctx.emotion_data['mood_history'] = [m for m in moods if m]
            except Exception:
                LOGGER.exception("Error loading her notes")
        else:
            # No avatar: the default persona, with what earlier versions of Altera tracked for everyone
            try:
                from core.profile import load_user_profile
                ctx.emotion_data['mood_history'] = load_user_profile().get('mood_history', [])
            except Exception:
                LOGGER.exception("Error loading mood history")
            try:
                from core.jokes import joke_tracker
                callbacks = joke_tracker.get_callback_material(limit=2)
                if callbacks:
                    ctx.joke_material = callbacks
            except Exception:
                LOGGER.exception("Error getting joke callbacks")
            try:
                from core.opinions import humor_matcher
                humor_style = humor_matcher.get_humor_style()
                if humor_style and 'style' in humor_style:
                    ctx.humor_style = humor_style
            except Exception:
                LOGGER.exception("Error getting humor style")

        # Edited history must not keep feeding stale derived notes or facts to the model.
        from core.knowledge import dirty
        if dirty(ds, ctx.avatar_id):
            ctx.notes = {}
            ctx.relevant_memories = []
        from core.knowledge import floor
        if not ctx.avatar_id and floor(ds, ''):
            # Legacy global callbacks have no source IDs. Do not reintroduce them after an explicit recall reset.
            ctx.joke_material, ctx.humor_style = [], {}
            ctx.emotion_data['mood_history'] = []
        # Build system prompt from composed sections
        from core.expression import get_expression
        ctx.expression = get_expression(ds, ctx.avatar_id)
        from core.reflection import current_notebook
        ctx.reflection_notes = current_notebook(ds, ctx.avatar_id) if keeps["story"] else {}
        turn = CURRENT_TURN.get()
        if turn and keeps["story"]:
            from core.episodes import get_episode_service
            source_id = (turn.service.get(turn.request_id) or {}).get('user_message_id')
            source = ds.get_message(source_id) if source_id else None
            epoch = (ds.get_key('dataset_epoch') or ds.dataset_epoch())['value']
            turn.recall_epoch = epoch
            if source and source.id > floor(ds, ctx.avatar_id):
                ctx.episodes = get_episode_service(ds).select_for_turn(session_id, epoch, turn.request_id, source.content[:4000])
            turn.episode_inputs = [item['id'] for item in ctx.episodes]
        ctx.system_prompt = self.prompt_builder.build(ctx)
        turn = CURRENT_TURN.get()
        if turn:
            from core.memory import MemoryManager
            turn.memory_inputs = MemoryManager.capture_recall(ds, ctx.avatar_id, ctx.relevant_memories)

        return ctx

    def _start_photos(self, tags: list, user_text: str, session_id: int) -> list:
        """Start a picture job for her [[photo: ...]] tag, or, if she ignored the instruction but the user plainly asked
        for a picture, for their request. One picture per reply. Returns the public photo records (possibly blocked)."""
        try:
            from core import companion
            avatar = companions.avatar_for_session(session_id)
            if not avatar or not companion.photos_ready(avatar["id"]):
                return []
            asked = photo_tags.photo_intent(user_text)
            if not tags and not asked:
                return []
            services = companion.get_services()
            level = adult.effective_level()
            scene = tags[0] if tags else ""

            recent = self._recent_photo_ids(session_id)

            # She named a ready-made picture by id: it is already made, so it arrives at once.
            if scene and photos_module.valid_id(scene):
                ready = services.photos.get_premade(scene, avatar["id"], level)
                if ready:
                    return [ready]
                # Not usable as it is (unknown, or filed above the current level): make a new picture from its description,
                # which still has to pass the word check at the current level. An unknown id has no description.
                known = services.photos.get(scene) or {}
                scene = known.get("scene") or "" if known.get("avatar_id") == avatar["id"] else ""

            # No scene given: what the person asked for is the scene.
            if not scene:
                scene = (photo_tags.scene_from_request(user_text) or "") if asked else ""

            # A picture she already has that shows it (a studio picture, or one she made earlier) arrives at once; with no
            # scene at all, any studio picture that was not sent lately will do. Only when nothing fits is a new one made.
            # A "scene" that is only the words of the request ("send me one more") asks for nothing in particular, but one
            # that the rules would refuse still goes to create(), which records why it was refused.
            if photos_module._keys(scene):
                match = services.photos.find_match(avatar["id"], level, scene, recent)
                if match:
                    return [match]
            elif not scene or not (safety.hard_problem(scene) or adult.tier_problem(scene, level)):
                scene = ""
                ready = services.photos.pick_premade(avatar["id"], level, None, recent)
                if ready:
                    return [ready]

            record = services.photos.create(scene, level=level, session_id=session_id, source="model", record_blocked=True)
            return [record]
        except Exception:
            LOGGER.exception("Could not start a picture")
            return []

    def _recent_photo_ids(self, session_id: int) -> list:
        """Ids of the pictures sent in the last stretch of this chat (so the same one is not sent again at once)."""
        ids = []
        try:
            for m in get_datastore().get_messages(session_id, limit=30):
                if m.role == "assistant":
                    ids += _PHOTO_MARKER.findall(m.content or "")
        except Exception:
            LOGGER.exception("Could not look up the pictures sent lately")
        return ids

    def _history_text(self, content: str) -> str:
        """How earlier pictures read to the model: a short note instead of the [[photo:id]] marker."""
        def note(match):
            try:
                from core import companion
                service = companion.get_services().photos
                record = service.get(match.group(1))
                scene = (record or {}).get("scene")
                status = (record or {}).get("status")
                if status in ("blocked", "failed"):
                    needed = (record or {}).get("required_level")
                    if needed and adult.get_state().get("enabled"):   # so she can explain, and tell the user how to allow it
                        # (while adult mode is off she says nothing about it: it stays out of sight)
                        return (f"\n(A photo you tried to send was not delivered: it needs adult mode set to "
                                f"{adult.label(needed)} or higher.)")
                    return "\n(A photo you tried to send was not delivered.)"
                if status in ("pending", "running"):
                    return "\n(An imagined image was requested but is not yet ready; it is not evidence of a real event.)"
                if status != "ready":
                    return "\n(An imagined image was requested; its status is unavailable. It is not evidence of a real event.)"
                description = f" Requested scene: {scene}." if scene else ""
                return f"\n(An imagined image was sent.{description} The scene is not evidence of a real event.)"
            except Exception:
                return "\n(An imagined image was requested; its status is unavailable. It is not evidence of a real event.)"
        return _PHOTO_MARKER.sub(note, content or "")

    def _call_llm(self, messages: list[dict], session_id: int, allow_tools: bool = True, client=None) -> str:
        """Call the LLM with serialized access and handle tool calls."""
        client = client or self.llm_client

        try:
            with ExitStack() as stack:
                for _event in suite_gpu.wait_for_reply(client, CURRENT_TURN.get(), stack):
                    pass  # synchronous callers receive the final reply
                tools = [WEB_SEARCH_TOOL] if allow_tools and self._tools_for_this_backend() else None
                stack.enter_context(self._llm_lock)
                for attempt in range(1 + EMPTY_REPLY_RETRIES):
                    reply = client.chat(
                        messages,
                        stream=False,
                        tools=tools,
                        tool_executor=self._execute_tool_call if tools else None,
                    )
                    if (isinstance(reply, str) and reply.strip()) or attempt == EMPTY_REPLY_RETRIES:
                        break
                    LOGGER.warning("The model returned no visible reply; asking once more: session_id=%r", session_id)
                return reply
        except (InterruptedError, suite_gpu.GpuWaitError):
            raise
        except Exception as e:
            error_msg = "Error connecting to LM Studio"
            LOGGER.exception(error_msg)
            raise RuntimeError("The configured model could not be reached.") from e

    def _call_llm_stream(self, messages: list[dict], session_id: int, user_text: str = "", after_reply=None, fixed_reply=None,
                         allow_tools: bool = True, guard=None, client=None):
        """Call the LLM with streaming response and handle tool calls.

        guard(text) -> replacement or None: when given, the reply is held back until it is complete and checked as a whole
        (kids mode), so nothing it would replace is ever shown or spoken."""
        if CURRENT_TURN.get():
            CURRENT_TURN.get().check_cancelled()
        client = client or self.llm_client
        # Save assistant message to DB inside generator (request context gone in Flask 3.x generators)
        def generate():
            LOGGER.debug("Inner stream generator started: session_id=%r", session_id)

            full_response = ""          # everything the model sent
            visible = ""                # what the user sees: the same text without [[photo: ...]] tags
            tags = photo_tags.TagFilter()
            chunk_count = 0
            try:
                with ExitStack() as stack:
                    if fixed_reply is None:
                        yield from suite_gpu.wait_for_reply(client, CURRENT_TURN.get(), stack)
                    tools = [WEB_SEARCH_TOOL] if allow_tools and fixed_reply is None and self._tools_for_this_backend() else None
                    # A reasoning model now and then answers with its thinking alone, or stops at once: nothing was shown yet,
                    # so it is asked once more before the turn counts as failed.
                    attempts = 1 if fixed_reply is not None else 1 + EMPTY_REPLY_RETRIES
                    for attempt in range(attempts):
                        LOGGER.debug("Calling LLM stream: tools=%s attempt=%s", bool(tools), attempt + 1)
                        # A character's first message is the card's own text: it streams like a reply, without a model call
                        source = iter([fixed_reply]) if fixed_reply is not None else client.chat(
                            messages,
                            stream=True,
                            tools=tools,
                            tool_executor=self._execute_tool_call if tools else None,
                        )
                        with owned_iterator(source) as chunks:
                            for chunk in chunks:
                                if CURRENT_TURN.get():
                                    CURRENT_TURN.get().check_cancelled()
                                if isinstance(chunk, str):
                                    # Aggregate completion has an explicit type: ordinary deltas can
                                    # legitimately repeat all the text seen so far ("ha", "ha", "!").
                                    if isinstance(chunk, StreamComplete):
                                        LOGGER.debug("Skipped final duplicate stream chunk: chars=%s", len(chunk))
                                        break  # Skip duplicate of final full response
                                    chunk_count += 1
                                    full_response += chunk
                                    shown = tags.feed(chunk)
                                    if shown:
                                        visible += shown
                                        if guard is None:
                                            yield shown  # Yield raw text only; caller wraps in SSE

                        tail = tags.flush()
                        if tail:
                            visible += tail
                            if guard is None:
                                yield tail
                        if visible.strip() or tags.tags or attempt + 1 == attempts:
                            break
                        LOGGER.warning("The model returned no visible reply; asking once more: session_id=%r", session_id)
                        if CURRENT_TURN.get():
                            CURRENT_TURN.get().check_cancelled()
                # The suite GPU lock is released before asking Forge to make a picture.
                instead = None
                if guard is not None:
                    instead = guard(full_response)
                    if instead:
                        LOGGER.info("Content checks replaced a reply: session_id=%r", session_id)
                        visible = instead
                    if visible:
                        yield visible

                resp_len = len(full_response)
                LOGGER.debug("Inner stream complete: chunks=%s chars=%s", chunk_count, resp_len)

                if CURRENT_TURN.get():
                    CURRENT_TURN.get().check_cancelled()
                # A tag (or a plain request for a picture) starts a picture job; the saved message carries a marker per picture
                photos = [] if instead else self._start_photos(tags.tags, user_text, session_id)
                saved = photo_tags.tidy(visible) + "".join(f"\n[[photo:{p['id']}]]" for p in photos)

                # Save with DataStore (creates its own connection — no Flask context needed)
                self._persist_reply(session_id, saved)
                LOGGER.debug("Saved streamed assistant message: session_id=%r", session_id)
                for p in photos:
                    yield {"photo": p}
                # Her notes may be due: that runs in the background, the page is only told so it can say so
                started = after_reply() if after_reply else None
                if started:
                    yield {"notes": started}

            except (InterruptedError, suite_gpu.GpuWaitError):
                raise
            except Exception:
                if CURRENT_TURN.get() and CURRENT_TURN.get().cancel.is_set():
                    raise InterruptedError("Reply stopped") from None
                LOGGER.exception("Stream LLM/DB error")
                raise

            LOGGER.debug("Inner stream yielding completion sentinel")
            yield None  # Sentinel: stream complete

        return generate()

    def _execute_tool_call(self, name: str, arguments: dict) -> str:
        """Execute model-requested tools and return JSON text for the next LLM round."""
        if name != "web_search":
            return json.dumps({"error": f"Unknown tool: {name}"})
        if not self.web_searcher.config.enabled:
            return json.dumps({"error": "Web search is turned off."})

        query = str(arguments.get("query", "")).strip()
        if not query:
            return json.dumps({"error": "query is required"})

        provider = arguments.get("provider")
        max_results = arguments.get("max_results")
        try:
            max_results = int(max_results) if max_results is not None else None
        except (TypeError, ValueError):
            max_results = None

        results = self.web_searcher.search(query=query, provider=provider, max_results=max_results)
        return json.dumps({
            "query": query,
            "provider": provider or self.web_searcher.config.default_provider,
            "results": [
                {
                    "title": result.title,
                    "url": result.url,
                    "snippet": result.snippet,
                    "source": result.source,
                }
                for result in results
            ],
        })

    def _tools_for_this_backend(self) -> bool:
        """Whether to send tool definitions on this backend. Probed once, under a lock so two concurrent first turns
        do not each fire a probe at the model."""
        if not self.web_searcher.config.enabled:
            return False
        if self._supports_tools is None:
            with self._tools_lock:
                if self._supports_tools is None:
                    self._supports_tools = self._server_accepts_tools()
        return self._supports_tools

    def _server_accepts_tools(self) -> bool:
        """Whether the endpoint accepts a request carrying `tools` at all.

        This is not a test of how well the model uses tools — it cannot be, from one probe — and it no longer claims
        to be one. It answers the only question the caller can act on: a backend that rejects the `tools` parameter
        outright would fail every chat turn, so on a rejection we stop sending it. A backend that accepts it gets
        tools, and a model that never calls one simply never calls one.
        """
        probe_messages = [
            {"role": "system", "content": "You are a test assistant."},
            {"role": "user", "content": "Say hello"},
        ]
        probe_tool = {
            "type": "function",
            "function": {
                "name": "test_function",
                "description": "A test function",
                "parameters": {"type": "object", "properties": {}},
            },
        }
        try:
            with self._llm_lock:
                self.llm_client.chat(probe_messages, stream=False, tools=[probe_tool])
            return True
        except Exception as e:
            LOGGER.info("This backend rejects tool definitions, so chat will run without them: %s", e)
            return False

    def _recent_texts(self, session_id: int, limit: int = 50) -> list[str]:
        """The newest messages of the chat as text, oldest first (before the cut-off when a reply is being redone)."""
        ds = get_datastore()
        turn = CURRENT_TURN.get()
        if turn and turn.regenerate:
            records = ds.get_messages_before(session_id, turn.regenerate["cutoff"], limit)
        else:
            records = ds.get_messages(session_id, limit=limit)
        return [self._history_text(r.content) for r in records]

    def _history_for(self, session_id: int, ctx: ChatContext) -> list[dict]:
        """The messages the model sees: the newest ones that fit next to the prompt and the reply, starting after whatever her
        notes have already taken in (see core/notes.py). Everything stays in the database either way."""
        cfg = notes_module.settings(self.config)
        ds = get_datastore()
        after = 0
        if ctx.avatar_id and companions.feature_on(ctx.avatar_id, "notes")                 and not (ds.get_key(f"knowledge_dirty:{session_id}") or {}).get("pending"):
            try:
                after = notes_module.get_notes_service().history_after(ctx.avatar_id, session_id)
            except Exception:
                LOGGER.exception("Could not read where her notes left off")
        turn = CURRENT_TURN.get()
        if turn and turn.regenerate:
            cutoff = turn.regenerate["cutoff"]
            records = ds.get_messages_before(session_id, cutoff, cfg["history_messages"])
        else:
            records = ds.get_messages_after(session_id, after) if after else ds.get_messages(session_id, limit=cfg["history_messages"])
        from core.knowledge import prompt_messages
        records = prompt_messages(ds, session_id, records)
        items = [{"role": r.role, "content": self._history_text(r.content)} for r in records[-cfg["history_messages"]:]]

        context, max_reply = self._context_size()
        max_reply = self._own_reply_length(ctx, max_reply)
        room = context - notes_module.estimate_tokens(ctx.system_prompt) - min(max_reply, int(context * 0.2))
        kept, used = [], 0
        for item in reversed(items):
            cost = notes_module.estimate_tokens(item["content"]) + 4
            if len(kept) >= 2 and used + cost > room:
                break
            kept.append(item)
            used += cost
        kept.reverse()
        if turn:
            turn.metrics['context'] = {'limit_tokens': context,
                'limit_source': 'configuration' if cfg['context_tokens'] > 0 else notes_module._ctx_cache.get('source', 'fallback assumption'),
                'system_tokens_estimate': notes_module.estimate_tokens(ctx.system_prompt), 'history_tokens_estimate': used,
                'reply_reserve_estimate': min(max_reply, int(context * 0.2)), 'max_reply_tokens': max_reply,
                'history_messages': len(kept), 'history_candidates': len(items),
                'history_after': after}
        return kept

    def _context_size(self) -> tuple:
        """(the model's context length in tokens, the longest reply it may write)."""
        client = getattr(self, "llm_client", None)
        context = notes_module.context_tokens(self.config, getattr(client, "base_url", None), getattr(client, "model", None))
        max_reply = getattr(client, "max_tokens", None) or (self.config.get("llm") or {}).get("max_tokens") or 1024
        return context, int(max_reply)

    def _own_reply_length(self, ctx: ChatContext, max_reply: int) -> int:
        """This person's own reply length when they have one (core/sampling.py), else max_reply."""
        client = self._reply_client(getattr(ctx, "avatar_id", ""))
        own = getattr(client, "max_tokens", None) if client is not self.llm_client else None
        return int(own) if own else max_reply

    def _notes_after_reply(self, session_id: int, ctx: ChatContext, system_tokens: int) -> str | None:
        """After a reply: start updating her notes if they are due ('pass' or 'compact'), in the background."""
        if not ctx.avatar_id or not companions.feature_on(ctx.avatar_id, "notes")                 or (get_datastore().get_key(f"knowledge_dirty:{session_id}") or {}).get("pending"):
            return None
        try:
            context, max_reply = self._context_size()
            max_reply = self._own_reply_length(ctx, max_reply)
            action = notes_module.get_notes_service().schedule(ctx.avatar_id, session_id, system_tokens, context, max_reply)
            if CURRENT_TURN.get() and action:
                CURRENT_TURN.get().metrics['notes_action'] = action
            return action
        except Exception:
            LOGGER.exception("Could not schedule the notes update")
            return None

    def _memories_after_reply(self, ctx: ChatContext) -> None:
        """After a reply: let the writer read any unread messages, in the background, once enough have gathered.

        Nothing is asked of the model per reply: the writer checks how many of the person's messages it has not read
        yet, and only then runs a batch. When the notebook pass runs, it does this at the end of the same job, so the
        GPU is never asked for two things for one person at once.
        """
        try:
            from core.memory import get_memory_writer
            get_memory_writer().schedule(ctx.avatar_id or '')
        except Exception:
            LOGGER.exception("Could not schedule a memory pass")

    def _save_message(self, session_id: int, role: str, content: str):
        """Save a message to the database."""
        ds = get_datastore()
        turn = CURRENT_TURN.get()
        if turn and role == 'user':
            if turn.service.store is not ds:
                raise RuntimeError('The reply datastore changed.')
            return ds.save_turn_message(turn.request_id, session_id, role, content)
        return ds.save_message(session_id, role, content)

    def _persist_reply(self, session_id, content):
        # A reasoning-only or otherwise empty completion is not a reply. Keep
        # the user's message (and any previous regenerated answer) intact.
        # A picture-only response remains valid because it carries a marker.
        if not isinstance(content, str) or not content.strip():
            raise EmptyReply()
        turn = CURRENT_TURN.get()
        ds = get_datastore()
        if not turn:
            return ds.save_message(session_id, "assistant", content)
        with turn.finish_lock:
            turn.check_cancelled()
            if turn.service.store is not ds:
                raise RuntimeError('The reply datastore changed.')
            target = (turn.regenerate or {}).get("assistant_message_id")
            return ds.save_turn_message(turn.request_id, session_id, 'assistant', content, target_id=target,
                                        captured_memories=getattr(turn, 'memory_inputs', None))


# ============================================================
# Module-level singleton (for easy import from altera.py)
# ============================================================

_chat_engine_instance = None
_engine_lock = threading.Lock()


def get_chat_engine(config: dict = None, personality: dict = None) -> ChatEngine:
    """The shared ChatEngine. This is the only one: altera.py and core/routes/core_api.py each used to keep their own.

    Switching provider refreshed two of the three, and the one actually serving /api/chat was not among them, so it
    kept its stale config (memory top_k, notes settings, web-search settings) and its stale function-calling verdict
    for the life of the process. Chat worked only because ChatEngine.llm_client reads the LLMClient singleton, which
    was being refreshed.

    config/personality are optional: they default to what core.config has, and are accepted so existing callers and
    tests can still pass their own.
    """
    global _chat_engine_instance
    if _chat_engine_instance is None:
        with _engine_lock:
            if _chat_engine_instance is None:
                from core import config as config_module
                _chat_engine_instance = ChatEngine(
                    config if config is not None else config_module.get_config(),
                    personality if personality is not None else config_module.get_personality(),
                )
    return _chat_engine_instance


def refresh_chat_engine(config: dict = None, personality: dict = None) -> ChatEngine:
    """Rebuild the ChatEngine from config (for provider switching). Everyone sees the new one, because there is one."""
    global _chat_engine_instance
    from core import config as config_module

    if config is None:
        config = config_module.reload_config()
    if personality is None:
        personality = config_module.get_personality()

    from core.llm import refresh_provider_registry
    refresh_provider_registry()             # the registry reads config.yaml for the newly selected provider
    LLMClient.refresh_from_config(config)   # then the client singleton the engine talks through

    with _engine_lock:
        _chat_engine_instance = ChatEngine(config, personality)
    return _chat_engine_instance


def reset_chat_engine() -> None:
    """Drop the singleton (tests)."""
    global _chat_engine_instance
    with _engine_lock:
        _chat_engine_instance = None
