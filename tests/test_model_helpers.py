"""The background helpers (jokes, opinions, humor, life events, people and places, communication style) ask the model that is
answering chat, through core.llm.ask, and never name a model of their own: LM Studio loads whatever model a request names, and a
helper that asked for a hard-coded model once swapped the chat model out. The model is a fake OpenAI client (see conftest.py:
nothing here can reach LM Studio)."""
import types
from pathlib import Path

import openai
import pytest

from core import llm
from core.events import EventTimeline, LifeEventExtractor
from core.jokes import JokeTracker
from core.opinions import HumorMatcher, OpinionTracker
from core.relationships import PersonalityEvolution, RelationshipGraph
from test_companion_api import engine, tmp_store, world  # noqa: F401

CHAT_MODEL = "the-chat-model"


class FakeModel:
    """Stands in for openai.OpenAI: records every request and answers from a queue."""

    def __init__(self):
        self.requests, self.replies, self.fail = [], [], False

    def reply(self, text):
        self.replies.append(text)

    def models(self):
        return sorted({r["model"] for r in self.requests})

    def __call__(self, base_url=None, api_key=None, **kwargs):
        def create(**request):
            self.requests.append(request)
            if self.fail:
                raise ConnectionError("no model here")
            text = self.replies.pop(0) if self.replies else "{}"
            message = types.SimpleNamespace(content=text, reasoning_content=None, tool_calls=None)
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])
        return types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create)))


@pytest.fixture
def model(monkeypatch):
    fake = FakeModel()
    monkeypatch.setattr(openai, "OpenAI", fake)
    monkeypatch.setattr(llm.time, "sleep", lambda seconds: None)             # no waiting between the retries of a failing call
    monkeypatch.setattr(llm.LLMClient, "_instance", llm.LLMClient(base_url="http://chat.example/v1", api_key="k", model=CHAT_MODEL))
    return fake


# ---------------------------------------------------------------- ask()

def test_ask_names_the_chat_model_and_leaves_room_for_a_reasoning_model(model):
    model.reply('<think>let me see...</think>  {"ok": true}  ')
    assert llm.ask("Be brief.", "Hello?", temperature=0.1, max_tokens=300) == '{"ok": true}'
    request = model.requests[0]
    assert request["model"] == CHAT_MODEL and request["temperature"] == 0.1
    assert request["max_tokens"] == 300 + llm.ANSWER_ROOM
    assert [m["role"] for m in request["messages"]] == ["system", "user"] and request["messages"][1]["content"] == "Hello?"


def test_ask_raises_when_the_model_cannot_be_reached(model):
    model.fail = True
    with pytest.raises(RuntimeError):
        llm.ask("Be brief.", "Hello?")
    assert len(model.requests) == llm.MAX_RETRIES and model.models() == [CHAT_MODEL]


# ---------------------------------------------------------------- unload_models() (freeing RAM/VRAM before a picture)

def test_unload_models_uses_the_suite_helper_for_lm_studio_and_ollama(monkeypatch, caplog):
    from core import fels_suite
    calls = []
    results = [{"program": "LM Studio", "done": False, "problem": "LM Studio could not unload."},
               {"program": "Ollama", "done": True, "problem": None}]
    monkeypatch.setattr(fels_suite, "unload_outside", lambda *, ask_json, timeout: calls.append(timeout) or results)
    assert llm.unload_models(timeout=7) is True
    assert calls == [7]
    assert "LM Studio could not unload" in caplog.text
    results.pop()
    assert llm.unload_models() is False


# ---------------------------------------------------------------- the helpers really reach the model

def test_inside_jokes_are_read_by_the_chat_model_and_kept(model, tmp_store):
    sid = tmp_store.create_session("t")
    model.reply('```json\n{"jokes": ["the cat did what"], "recurring_topics": ["cats"], "shared_references": []}\n```')
    result = JokeTracker().extract_jokes_and_references("the cat did what, again", sid)
    assert result == {"jokes": ["the cat did what"], "recurring_topics": ["cats"], "shared_references": []}
    assert model.models() == [CHAT_MODEL] and "the cat did what" in tmp_store.get_jokes_and_references()["jokes"]


def test_opinions_and_humor_are_read_by_the_chat_model_and_kept(model, tmp_store):
    sid = tmp_store.create_session("t")
    model.reply('{"topics_with_opinions": [{"topic": "Jazz", "opinion": "loves it", "strength": 0.9}]}')
    found = OpinionTracker().analyze_opinions("I love jazz", sid)
    assert found[0]["topic"] == "Jazz" and "jazz" in tmp_store.get_user_opinions()

    model.reply('{"style": "dry", "level": "high", "is_joking": true}')
    assert HumorMatcher().analyze_humor_style(text_samples=["a very dry joke"], session_id=sid) == \
        {"style": "dry", "level": "high", "is_joking": True}
    assert tmp_store.get_humor_style()["style"] == "dry"

    tmp_store.save_message(sid, "user", "that was hilarious")                # no samples given: it reads the chats
    model.reply('{"style": "witty", "level": "medium", "is_joking": false}')
    assert HumorMatcher().analyze_humor_style()["style"] == "witty"
    assert "that was hilarious" in model.requests[-1]["messages"][1]["content"] and model.models() == [CHAT_MODEL]


def test_life_events_are_read_by_the_chat_model_only_when_the_caller_asks(model, tmp_store):
    sid = tmp_store.create_session("t")
    text = "Guess what, I finished my degree"
    extractor = LifeEventExtractor()
    assert extractor.extract_from_text(text, session_id=sid) and model.requests == []          # patterns only: nothing is asked

    model.reply('[{"type": "achievement", "detail": "finished a degree", "confidence": 0.9}]')
    rich = extractor.extract_from_text(text, session_id=sid, use_llm=True)
    assert any(e["detail"] == "finished a degree" for e in rich) and len(model.requests) == 1 and model.models() == [CHAT_MODEL]

    timeline = EventTimeline()
    assert timeline.add_events_from_conversation(text, session_id=sid) and len(model.requests) == 1       # the default asks nothing
    model.reply('[{"type": "achievement", "detail": "finished a degree", "confidence": 0.9}]')
    timeline.add_events_from_conversation(text, session_id=sid, use_llm=True)
    assert len(model.requests) == 2 and any(e["detail"] == "finished a degree" for e in timeline.get_timeline(limit=50))


def test_people_and_places_are_read_by_the_chat_model_and_kept(model, tmp_store):
    sid = tmp_store.create_session("t")
    model.reply('{"people": ["Maya"], "places": ["Lisbon"], "organizations": []}')
    found = RelationshipGraph().extract_entities("Maya and I flew to Lisbon", sid)
    assert found == {"people": ["Maya"], "places": ["Lisbon"], "organizations": []}
    graph = tmp_store.get_relationship_graph()
    assert graph["maya"]["type"] == "person" and graph["lisbon"]["type"] == "place" and model.models() == [CHAT_MODEL]


def test_the_communication_style_is_read_from_the_chat_that_was_open_last(model, tmp_store):
    evolution = PersonalityEvolution()
    assert evolution.analyze_communication_style() is None and model.requests == []           # nothing said anywhere yet
    sid = tmp_store.create_session("t")
    for text in ("hey", "so tired today", "lol ok"):
        tmp_store.save_message(sid, "user", text)
    tmp_store.set_last_active_session(sid)
    model.reply('{"formality": "casual", "tone": "warm", "length_preference": "brief", "humor_level": "medium"}')
    assert evolution.analyze_communication_style()["formality"] == "casual"                    # no chat named: the open one
    prompt = model.requests[-1]["messages"][1]["content"]
    assert prompt.index("hey") < prompt.index("so tired today") < prompt.index("lol ok")       # oldest first
    other = tmp_store.create_session("empty")
    assert evolution.analyze_communication_style(session_id=other) is None and len(model.requests) == 1


def test_when_the_model_cannot_be_reached_each_helper_uses_its_fallback(model, tmp_store):
    sid = tmp_store.create_session("t")
    tmp_store.save_message(sid, "user", "hello there")
    tmp_store.set_last_active_session(sid)
    model.fail = True
    assert JokeTracker().extract_jokes_and_references("x", sid) == {"jokes": [], "recurring_topics": [], "shared_references": []}
    assert OpinionTracker().analyze_opinions("x", sid) == []
    assert HumorMatcher().analyze_humor_style(text_samples=["x"]) == {"style": "playful", "level": "medium", "is_joking": False}
    assert RelationshipGraph().extract_entities("x", sid) == {"people": [], "places": [], "organizations": []}
    assert LifeEventExtractor()._extract_with_llm("x") == []
    assert PersonalityEvolution().analyze_communication_style() is None
    assert model.requests and model.models() == [CHAT_MODEL]                                   # it did try, and only ever named the chat model


# ---------------------------------------------------------------- nothing here runs by itself

def test_a_chat_turn_asks_the_model_nothing_beyond_the_reply(engine, world, monkeypatch):
    asked = []
    monkeypatch.setattr(llm, "ask", lambda *args, **kwargs: asked.append(args) or "{}")
    engine.llm.reply = "How lovely!"
    engine.send_message("I just got a promotion and we are going to Paris, my cat is hilarious", world.store.create_session("t"))
    assert asked == [] and len(engine.llm.calls) == 1


def test_no_module_can_name_a_model_of_its_own():
    import core.config
    assert not hasattr(core.config, "MODEL")
    for name in ("jokes", "opinions", "events", "relationships", "emotion"):
        source = (Path(__file__).resolve().parent.parent / "core" / f"{name}.py").read_text("utf-8")
        assert "completions.create" not in source and "import MODEL" not in source, name
