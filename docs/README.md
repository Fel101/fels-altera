# Altera user guide

Altera is a local-first companion app with streaming chat, separate companions, vector memories, editable notebooks, optional speech, and pictures from NPC Forge. Conversations are stored on your PC. Selected cloud model, speech and web services receive the content sent to them.

Only a chat model is required. Speech, NPC Forge and ComfyUI are optional. [NPC Forge](https://github.com/Fel101/fels-npc-forge) paints portraits from descriptions and creates characters with personalities. You can make a pal, import a character card, or write a companion yourself and upload a picture right in Altera.

## Documentation

### Using Altera

| Guide | Covers |
| --- | --- |
| [Quickstart](../README.md) | What Altera is, what you need, a five-minute start and what leaves your PC |
| [Setup](SETUP.md) | One setup entry point, offline prerequisites, preservation and metadata readiness |
| [Companions](COMPANIONS.md) | Meeting someone new, notebooks, compaction and galleries |
| [Pals](PALS.md) | Light personas from a name, a vibe and a voice, and changing who a pal is |
| [Characters](CHARACTERS.md) | Character cards (V1, V2 and V3) and what Altera reads from them |
| [Kids mode](KIDS_MODE.md) | A pal for a child, the parent PIN, the lock screen and the kids view |
| [Portraits](PORTRAITS.md) | Painting a portrait in NPC Forge, and the time and memory it takes |
| [Avatars, pictures and adult mode](AVATARS_AND_ADULT_MODE.md) | NPC Forge import, image workflows, content levels and their limits |
| [Reply settings](REPLY_SETTINGS.md) | Temperature, reply length, optional sampler settings, and a person's own |
| [Memory controls](MEMORY_CONTROLS.md) | Remember, Correct, Forget, provenance and rebuilding |
| [Our story and reflection](OUR_STORY.md) | Curated episodes, sources, pins, retrieval decay and optional reflection |
| [Check-ins and rituals](PRESENCE.md) | Optional unattended inbox, quiet hours, consent, notifications and simulated expression |
| [Voice conversations and saved notes](VOICE.md) | The microphone, voice notes, replay, and their limits |
| [Android and Tailscale](ANDROID_TAILSCALE.md) | Private phone access, installation and microphone troubleshooting |
| [Backup and restore](BACKUP_AND_RESTORE.md) | Encrypted snapshots, passwords/keys, validated restore and recovery |
| [Reply diagnostics](DIAGNOSTICS.md) | Timing, reported usage, estimates and compaction status |
| [Changelog](../CHANGELOG.md) | What changed in each version |

### How it works

| Reference | Covers |
| --- | --- |
| [Architecture](ARCHITECTURE.md) | Startup, services, persistence, security boundaries and source map |
| [API reference](API_REFERENCE.md) | Current route inventory and request contracts |
| [Database schema](Database_Schema.md) | SQLite tables, vector metadata, files and browser storage |
| [Encrypted backup format](ENCRYPTED_BACKUP_FORMAT.md) | The version-1 encrypted backup envelope |
| [Voice startup and transport](VOICE_TRANSPORT.md) | Status without startup, configured recipients, direct HTTP, redirect refusal and response limits |
| [Local memory embeddings](LOCAL_EMBEDDINGS.md) | Existing MiniLM assets, no runtime downloads and vector compatibility |
| [Storage readiness](PRIVATE_STORAGE.md) | Supplemental device checks and exact protection limits |
| [Security](../SECURITY.md) | How to report a problem privately, and what Altera does and does not protect against |
| [Contributing](../CONTRIBUTING.md) | Setting up, running the checks, and the house rules for a change |

### Project records

Dated reviews, verification evidence and plans from building Altera, kept in [`history/`](history/). They describe
the app as it was on their date, not as it is now.

[Code review, 2026-09-21](history/CODE_REVIEW_2026-09-21.md) ·
[Ordinary release acceptance](history/ORDINARY_RELEASE_ACCEPTANCE.md) ·
[Companion roadmap](history/COMPANION_ROADMAP.md) ·
[Polish record](history/POLISH_PLAN.md) ·
[Code review, 2026-10-26](history/CODE_REVIEW_2026-10-26.md) ·
[Companion memory verification, 2026-10-28](history/COMPANION_VERIFICATION_2026-10-28.md) ·
[Privacy and setup verification, 2026-10-28](history/PRIVACY_SETUP_VERIFICATION_2026-10-28.md) ·
[Terra/Sol handoff, 2026-11-05](history/HANDOFF_TERRA_SOL_2026-11-05.md) ·
[Historical roadmap](history/plan.md)

## Start and connect

Follow the [root quickstart](../README.md). After setup, `start.bat` starts Altera and opens the browser. New installs use **http://127.0.0.1:8767**. Existing installs keep the port in `config.yaml`. On a fresh instance the first run asks for a name, sends a tiny test message to the selected model and shows which optional pieces are running, then opens Home. If nothing answers it says what to start for LM Studio and Ollama; nothing there blocks finishing.

Start the model service you intend to use. In Settings, choose a provider and a model that service actually serves, or let the first run check it. The same check is available later as Settings → Setup check. Presets cover LM Studio, Ollama, vLLM, OpenAI, OpenRouter and a custom OpenAI-compatible endpoint. The starter example leaves the model blank; provider presets can supply a default name, but Altera does not install model assets for you. Select the exact identifier your server should run. Switching providers does not require restarting Altera. Altera does not automatically switch to a cloud model if a local provider fails.

The connection indicator means the browser can reach Altera. A model marked Listed passed a metadata check; inference still needs a working model. Reply diagnostics helps distinguish preparation time, first text, overall duration and missing usage.

## Chat and companions

Choose someone from the sidebar: make a pal, import a character card, write a companion of your own, or meet a new companion with **Add someone**. A companion can be written without NPC Forge — a name, an age and a few words — and can later be given a portrait or have one painted for them. Each person has their own chat ownership, notebook and recalled vector memories. Imported historical facts with unknown sources require review. Legacy relationship, event and personality dashboards can contain older instance-wide data; they are not a second source of person-scoped memory controls.

Kids mode is a guardrail for a pal a child talks to, with a parent PIN, a lock screen and a kids view the server enforces. See [Kids mode](KIDS_MODE.md).

Chat has Copy, Stop, Regenerate, Edit and Delete controls. A reply is confirmed saved only after it is persisted. A disconnected browser can reconnect to the same request without generating the reply twice. If Altera restarts after an answer has been saved, the request recovers as saved; unfinished replies remain interrupted for review. Stopped or failed replies retain partial text for review; regenerating replaces an answer only when the replacement succeeds. Editing, deleting or replacing source text pauses derived knowledge until it is reviewed or rebuilt. See [memory controls](MEMORY_CONTROLS.md).

Search uses SQLite FTS5 literal word matching, with session, role, mood and date filters. Semantic conversation search is not implemented. The legacy Timeline (`/timeline`) and knowledge (`/knowledge`) pages are still served at their addresses but are no longer in the navigation: they show instance-wide data, not one person’s. Exports in JSON, Markdown or PDF/HTML are readable reports, not full recoverable backups.

Web search is separate from conversation search. The portable starter keeps it off: to enable it, set `web_search.enabled` and the chosen entry under `web_search.providers` to `true` in your instance configuration, then restart Altera. Search providers receive model-generated queries and may retain them. If OpenWeatherMap has no API key, its weather fallback uses DuckDuckGo only when that provider is enabled. The existing configuration is not changed automatically. Altera's search messages avoid recording query text and provider exception details, but external providers and other software may keep their own logs.

**Our story** keeps moments you curate from reviewed message text, with distinct labels for actual exchanges, imagined scenes and derived interpretations. Pins and importance affect retrieval order without confirming a claim or deleting older memories. Optional reflection has its own per-companion consent and provider approval; it uses original user messages as evidence and publishes separate source-tracked derived notebook entries. [Our story and reflection](OUR_STORY.md) explains limits, correction, Forget and restore review.

## Voice

Text chat works with voice disabled. For a local voice, install Altera's own Kokoro voice service once with `voice\setup.bat` (Windows) or `sh voice/setup.sh`, then set `voice.tts_engine: kokoro` and `voice.kokoro.autostart: true`; [Voice](VOICE.md#a-local-voice) has the details. It listens on `http://127.0.0.1:8770`. To use another compatible service instead, set `voice.kokoro.python` and `voice.kokoro.script`, or `voice.kokoro.url` for one you start yourself.

`voice.tts_enabled` enables speech at the instance level; the chat voice switch is a saved user preference. The portable starter disables speech and autostart. Edge speech sends reply text to Microsoft's service. `voice.cloud_fallback` defaults off; when enabled with Kokoro, it permits Edge fallback outside adult mode. Cloud fallback is blocked while adult mode is active.

Click the microphone while Altera speaks to interrupt her and start recording. Late clips stay silent; uncertain delivery or a changed context leaves the transcript for review. Settings offers optional automatic interruption for this tab, with a visible microphone indicator and a local CPU detector. Voice notes saves recordings for explicit upload, cached-local transcription, review and sending; replies can be replayed later. See [Voice conversations and saved notes](VOICE.md) for limits, privacy, offline recovery and acceptance status.

Microphone recording uses browser permission and local faster-whisper transcription. The Whisper model is not downloaded by itself: `setup.bat --models` fetches it once (see [Setup](SETUP.md#the-memory-and-microphone-models)). On a phone, use the private HTTPS setup in [Android and Tailscale](ANDROID_TAILSCALE.md). The browser may require a tap before playing speech.

## Offline behavior and check-ins

The PWA can be installed and retains browser drafts/queued requests. Altera and its model still need to be reachable to chat. Private page HTML, API responses, pictures and generated speech are not available from a service-worker offline cache; only application assets are cached. New queued requests retain stable request IDs. Restored or stale drafts require explicit review and Send, and an interrupted active reply is not automatically submitted as a new request.

Optional [check-ins and rituals](PRESENCE.md) use an unattended server scheduler while Altera and the selected model service remain running. Each companion starts off and needs explicit consent; external providers require separate connection approval. Browser polling only retrieves the saved inbox. Read, Dismiss and Add to conversation are separate actions, with no autoplay. Opt-in browser notifications say who has a check-in while Altera is open in a tab or as the installed app; there is no push service, so nothing arrives while it is closed. Jobs honor source/recall boundaries, quiet hours, limits and expiry.

## Configuration and instance data

`config.example.yaml` is the portable starter reference. Existing `config.yaml` may be customized. Provider overrides belong under `llm.providers.<provider>`; the active selection is `llm.provider` and `llm.model`. `config.local.yaml` overlays the main file and holds credentials and remote-access identity. Supported `ALTERA_<PROVIDER>_API_KEY` variables override existing API-key fields. Never share the local file or `.env`.

`personality.yaml` defines the default persona. Imported companions get their identity from NPC Forge. `user_profile.yaml` stores user preferences and legacy profile/mood state; companion knowledge also lives in SQLite, ChromaDB and notebooks. Set `ALTERA_HOME` before Python imports to keep a separate instance. See the [schema and storage reference](Database_Schema.md).

The portable starter uses loopback port 8767, LM Studio, a blank model, 1024 maximum reply tokens, notes enabled with an 8192-token context assumption, and voice, image generation, workspace tools and proactive check-ins disabled. Code fallbacks for omitted settings can differ; keep explicit settings when privacy matters.

## Privacy and recovery

Use Settings → Backup and restore for an encrypted snapshot and validated restore preview. Keep its password or optional recovery key separately and download a copy onto another drive. Plaintext ZIP is an explicit choice. Running storage is not encrypted; use disk encryption to protect it (see [Private storage](PRIVATE_STORAGE.md)). Credentials, access policy, logs and caches are excluded from the archive. A restore preserves this PC's connections unless explicitly requested otherwise and resets adult mode off. [Backup coverage and recovery](BACKUP_AND_RESTORE.md) describes the limits.

Remember, Correct and Forget operate on a selected companion. Correction/forgetting also resets that companion's recall boundary and clears notebook copies, while the source chat and independent exports/backups remain. Review the confirmation before applying a change.

Delete all conversations clears application conversations, memories, pictures, notebooks, profile, queued messages and generated speech while retaining imported avatars. It refuses active replies/background writers, including speech still finishing after Stop. A storage failure returns `clear_incomplete`; cleanup can be partial, so retry before using Altera. Successful clearing changes the dataset identifier so stale tabs/drafts must be reloaded and reviewed. It is not secure disk erasure and does not remove backups/exports, logs, workspace files or other browsers' storage. Remove a companion to remove that companion's avatar data too. NPC Forge's source library is separate.

Workspace tools, when enabled, read allowed instance files, write only under `workspace/`, and fetch bounded public HTTP/HTTPS resources directly (ambient proxy environment variables are ignored). File and URL guards apply independently of the model. Tailscale access uses configured identities; Altera is not designed as a public multi-user service.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| Altera will not start | Run `start.bat` from a terminal to read the error; check Python dependencies, valid YAML, write permission and the configured port |
| Model unavailable or missing | Start the selected server; refresh its model list and select the exact model identifier |
| No voice | Check instance speech setting, saved voice preference, selected engine, service status and phone media permissions |
| Memory needs review | Inspect source status on Memory; use correction/forgetting or rebuild automatic memories as appropriate |
| Reply stopped/disconnected | Inspect saved conversation and request state before retrying; keep the same request ID for a transport retry |
| Data restored in another tab | Reload and review drafts; stale tabs cannot mutate the replacement dataset |
| Restore recovery screen | Preserve `backups/` including its journal/staging and restore the marked recovery archive |
| Old UI after update | Reload the app; a service-worker update may require closing older tabs and reopening |

Logs rotate in `data/altera.log` with three previous files. Keep the logs private and share a sanitized error, reproduction steps and the version shown at the bottom of Settings (also the first line Altera logs at start-up) when reporting a problem. [CHANGELOG.md](../CHANGELOG.md) lists what changed in each version.

## Licensing

Altera is released under the MIT License: see [LICENSE](../LICENSE). This repository holds only Altera's own code and content. Everything else is installed or downloaded onto your machine from its original source and keeps its own licence and terms.

**Python packages.** What `requirements.txt` installs, including the packages those bring with them, is under permissive licences (MIT, BSD, Apache-2.0, ISC, PSF, ZPL), with three exceptions. edge-tts is LGPL-3.0, certifi is MPL-2.0, and tqdm and orjson contain MPL-2.0 parts. Altera uses all of them unmodified.

**The local voice** (`voice/`, optional). Kokoro and misaki are Apache-2.0 and PyTorch is BSD-3-Clause. For English pronunciation, misaki also installs phonemizer-fork (GPL-3.0-or-later), espeakng-loader (which bundles the espeak-ng library, GPL-3.0-or-later) and num2words (LGPL). These go into `voice/.venv` only and run in the voice service's own process. Altera does not ship them. If you redistribute a `voice/.venv`, the GPL's terms apply to it.

**Models.** None ships with Altera. MiniLM and Whisper are downloaded by `setup.bat --models`, Kokoro and spaCy's English model by `voice\setup.bat`, and Silero comes inside a package:

- Kokoro-82M and its voices: Apache-2.0.
- spaCy's en_core_web_sm, which misaki uses for English pronunciation: MIT.
- The faster-whisper speech-recognition models: MIT.
- all-MiniLM-L6-v2, used for memory search: Apache-2.0.
- Silero VAD, which ships inside the faster-whisper package: MIT.

Chat models and image checkpoints are your choice, so check each one's licence yourself. The workflow in `content/workflows/` names the models it expects but does not include them.

**Online services.** OpenRouter, OpenAI and Microsoft's online voices (edge-tts) are covered by those providers' terms, not by a licence. Using one sends your text to that provider.
