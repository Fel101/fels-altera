"""Guards for the whole test suite: no test may reach a real model or touch the real config, and each test starts with
a fresh notes service."""
import os
import shutil
import tempfile
from pathlib import Path

import pytest
os.environ['ALTERA_DISABLE_AUTOSTART'] = '1'

# Isolate collection as well as fixtures: imports must never inherit live instance paths
# or depend on the developer's enabled services/credentials. Start from portable defaults.
_collection_data = tempfile.TemporaryDirectory(prefix="altera-pytest-collection-", ignore_cleanup_errors=True)
os.environ['ALTERA_HOME'] = _collection_data.name
_test_source = Path(__file__).resolve().parent.parent
shutil.copy(_test_source / 'config.example.yaml', Path(_collection_data.name) / 'config.yaml')
shutil.copy(_test_source / 'personality.yaml', Path(_collection_data.name) / 'personality.yaml')
from core import datastore as _datastore_module  # noqa: E402 - ALTERA_HOME must be set before core is imported
_datastore_module._datastore_instance = _datastore_module.DataStore(os.path.join(_collection_data.name, "conversations.db"))

from core import notes  # noqa: E402


@pytest.fixture(autouse=True)
def altera_config(tmp_path, monkeypatch):
    """Every test gets its own copy of config.yaml and personality.yaml, and yields the path to the copy.

    Nothing a test does can reach the files Altera actually runs with. This matters because the provider switcher
    *writes* config.yaml: while config.yaml was opened by the bare relative name, a test that had chdir'd into a
    temporary directory wrote to a throwaway file and the real one was safe by accident. core/config.py resolves the
    path properly now, so that accident is gone and the protection has to be deliberate — a test that switches
    provider would otherwise rewrite the developer's own model, temperature and comments.

    A test that needs particular settings writes them to this path:

        altera_config.write_text("voice:\\n  tts_enabled: true\\n", encoding="utf-8")
    """
    from core import config as config_module

    sandbox = tmp_path / "_altera_config"
    sandbox.mkdir()
    monkeypatch.setenv("FELS_SUITE_DIR", str(tmp_path / "fels"))
    config_copy = sandbox / "config.yaml"
    local_copy = sandbox / "config.local.yaml"
    personality_copy = sandbox / "personality.yaml"

    for source, destination in ((config_module.CONFIG_PATH, config_copy),
                                (config_module.LOCAL_CONFIG_PATH, local_copy),
                                (config_module.PERSONALITY_PATH, personality_copy)):
        if source.exists():
            shutil.copy(source, destination)

    monkeypatch.setattr(config_module, "CONFIG_PATH", config_copy)
    monkeypatch.setattr(config_module, "LOCAL_CONFIG_PATH", local_copy)   # save_config writes credentials here
    monkeypatch.setattr(config_module, "PERSONALITY_PATH", personality_copy)
    monkeypatch.setattr(config_module, 'ROOT', sandbox)
    monkeypatch.setattr(config_module, 'SECRET_KEY_PATH', sandbox / 'data/secret_key')
    from core import datastore, memory, profile, companion, tts, adult
    from core.routes import admin, agent_tools
    monkeypatch.setattr(datastore, '_datastore_instance', datastore.DataStore(str(sandbox / 'data/conversations.db')))
    monkeypatch.setattr(memory, 'memory_manager', memory.MemoryManager())
    monkeypatch.setattr(profile, 'PROFILE_PATH', sandbox / 'user_profile.yaml')
    monkeypatch.setattr(companion, 'ROOT', sandbox)
    monkeypatch.setattr(tts, 'AUDIO_CACHE_DIR', str(sandbox / 'data/cache/tts'))
    monkeypatch.setattr(admin, 'OFFLINE_QUEUE_FILE', str(sandbox / 'data/offline_queue.json'))
    monkeypatch.setattr(agent_tools, 'PROJECT_ROOT', sandbox)
    tiers = sandbox / 'content/adult_tiers.yaml'
    tiers.parent.mkdir()
    from core.paths import CODE_ROOT
    shutil.copy(CODE_ROOT / 'content/adult_tiers.yaml', tiers)
    monkeypatch.setattr(adult, 'TIERS_PATH', tiers)
    monkeypatch.setattr(adult, '_cache', {'mtime': None, 'data': {}, 'needs': {}})
    # An api_key in the developer's real environment must not leak into a test's expectations.
    for name in list(os.environ):
        if name.startswith("ALTERA_") and name.endswith("_API_KEY"):
            monkeypatch.delenv(name, raising=False)
    config_module.reset_cache()
    yield config_copy
    config_module.reset_cache()


@pytest.fixture(autouse=True)
def _no_real_model_and_a_fresh_notes_service(monkeypatch):
    notes.set_notes_service(None)
    real_call = notes.NotesService._call

    def guarded(self, messages):
        if self._llm is None:
            raise RuntimeError("a test tried to make her notes call a real model; give the NotesService an llm=")
        return real_call(self, messages)

    monkeypatch.setattr(notes.NotesService, "_call", guarded)

    # Memory is written in the background too. By default a test gets a writer that does nothing, so no test reaches a
    # model or writes memories by accident; a test about memories installs its own with memory.set_memory_writer().
    from core import memory as memory_module

    class WriterOff:
        sync = True

        def schedule(self, owner):
            return None

        def run(self, owner, force=False):
            return {"ok": True, "stored": 0}

        def rebuild(self, owner):
            return {"ok": True, "stored": 0}

    memory_module.set_memory_writer(WriterOff())
    real_memory_call = memory_module.MemoryWriter._call

    def guarded_memory(self, messages):
        if self._llm is None:
            raise RuntimeError("a test tried to make memory extraction call a real model; give the MemoryWriter an llm=")
        return real_memory_call(self, messages)

    monkeypatch.setattr(memory_module.MemoryWriter, "_call", guarded_memory)

    from core import age_check

    def no_client(self, base_url, api_key):
        raise RuntimeError("a test tried to reach a real vision model; give the AgeCheck a client_factory=")

    monkeypatch.setattr(age_check.AgeCheck, "_openai_client", no_client)
    monkeypatch.setattr(age_check.AgeCheck, "_direct_client", no_client)

    # Any other way to a model (LLMClient.chat, core.llm.ask, the helpers) goes through openai.OpenAI: a test that needs one
    # replaces it with a fake (test_model_helpers.py), and one that forgot to fails here instead of calling LM Studio.
    import openai

    def no_openai(*args, **kwargs):
        raise RuntimeError("a test tried to reach a real model through openai.OpenAI; patch it with a fake")

    monkeypatch.setattr(openai, "OpenAI", no_openai)

    # Picture tests must not unload a developer's real LM Studio model. SDK
    # guards do not intercept CLI control. Tests of CLI behavior provide their
    # own discovery and subprocess fakes explicitly.
    from core import llm
    real_which = llm.shutil.which
    def without_model_cli(command, *args, **kwargs):
        if Path(os.fsdecode(command)).name.lower() in {'lms', 'lms.exe'}:
            return None
        return real_which(command, *args, **kwargs)
    monkeypatch.setattr(llm.shutil, 'which', without_model_cli)
    monkeypatch.setattr(llm, '_default_lms_path', lambda: None)

    # The mood is analysed in the background after a reply; in a test it runs before the reply is done, so it can be looked at.
    from core import emotion
    monkeypatch.setattr(emotion, "RUN_INLINE", True)
    # The later age check of portraits likewise (a test's AgeCheck that cannot reach a model answers "no answer" at once).
    from core import portraits
    monkeypatch.setattr(portraits, "RECHECK_INLINE", True)
    monkeypatch.setattr(portraits, "_no_answer", {})
    monkeypatch.setattr(portraits, "_rechecking", set())

    # One shared chat engine, built from whatever config the test has in place.
    from core import chat
    chat.reset_chat_engine()
    yield
    chat.reset_chat_engine()
    notes.set_notes_service(None)
    memory_module.set_memory_writer(None)
