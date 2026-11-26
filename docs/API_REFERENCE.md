# Altera API reference

The route inventory is checked against the registered application blueprints by `test_documentation.py`. Paths are relative to Altera's configured origin (default `http://127.0.0.1:8767`). The tables include every application route; payload examples below cover the main supported flows. For legacy analysis endpoints, the linked route module is the precise field/response reference.

## Access, requests and errors

Altera is a private single-owner application. Local access is loopback; optional remote access requires the configured Tailscale Serve host/port, HTTPS proxy identity and allowed login. There is no per-user public API-key authentication. Keep the backend on loopback. [Android setup](ANDROID_TAILSCALE.md) documents the supported proxy.


Send JSON with `Content-Type: application/json` unless a route accepts multipart audio/archive/Markdown. Browser mutations must pass the global Origin/fetch-site checks. After restore or a successful global clear, send the current dataset ID through `X-Altera-Dataset` or JSON `dataset_epoch`; stale/missing identifiers then return 409 with `code: "dataset_changed"`. The browser embeds its epoch in each page and sends it automatically. `GET /api/offline/status` reports the current identifier; reload and review stale drafts instead of automatically replaying them against a replacement or cleared dataset.

Errors generally contain `error` and sometimes `code`. Typical statuses are 400 invalid input, 403 access/origin blocked, 404 missing object, 409 conflicting/active work or stale dataset, 413 oversized request, 415 wrong media type, 422 content gate, and 503 unavailable/interrupted work or maintenance. Error text is not a stable machine contract. Ordinary uploads are capped at 25 MiB; validated backup uploads have a separate 2 GiB archive allowance.

A 200 response from a model-list or health route confirms metadata connectivity, not model inference. Some legacy GET endpoints can initialize companion state; GET must not be treated as a general dry-run mode. The proactive check-in GET/HEAD route also retains browser Origin/fetch-site protection; it returns a pointer without model work or saved-state changes.


## Chat and message controls

Prefer durable requests:

```json
{
  "session_id": 1,
  "request_id": "reply_20261026_001",
  "dataset_epoch": "current-identifier",
  "message": "Hello"
}
```

`POST /api/chat/stream` returns `text/event-stream`, with each JSON object in a `data:` record separated by a blank line. IDs must be 8–100 letters, digits, underscores or hyphens. Reuse exactly the same ID/input after a transport failure. Different input with the same ID, a retired ID or concurrent work for that session is rejected. Omitting `session_id` creates a session. A first meeting can use `greet: true` with an empty companion conversation instead of user text.

The durable stream begins with `{status: "accepted", request_id, session_id}`. Response chunks use `{response: "text"}`; optional records include `photo`, `status: "audio"` with `audio_url`/`seq`, notes state and typing/looking status. The terminal outcome is `saved`, `failed` or `interrupted`; only `saved` confirms the persisted assistant ID. A reply that comes back with no visible text is asked for once more before anything is shown; when it is empty again the turn fails with `error` saying so (the user's message stays saved). Do not infer success from partial text or socket closure. `GET /api/chat/requests/<request_id>` retrieves durable state and retained partial text without exposing the internal fingerprint/process identity.

A model completion with no visible reply fails instead of saving an empty assistant message. A provider's separate reasoning field is not treated as a visible answer. The user's message remains available for retry; a failed regeneration also retains the previous answer. A successfully created picture-only reply is valid.

A local LM Studio or Ollama reply waiting for the suite GPU lock emits `{status: "gpu_wait", message}` and emits `typing` when it can start. The same Stop endpoint cancels a waiting reply.

`POST /api/chat` with a request ID uses the same durable worker but waits for JSON; unsuccessful terminal work returns 503. Without an ID, `/api/chat` retains the older `{response, session_id, photos}` response and `/api/chat/stream` retains its legacy stream ending `final`/`done`. These compatibility modes do not provide durable retries or owned Stop.

| Control | Request |
| --- | --- |
| Stop | `POST /api/chat/requests/<request_id>/stop`; retains partial text |
| Regenerate/retry | `POST /api/chat/regenerate` with `session_id`, optional `message_id` and a fresh stable `request_id`; returns durable SSE |
| Read conversation | `GET /api/messages/<session_id>`; confirmed messages plus failed/stopped draft records, with `reply_state`, and `versions`/`other_versions` (how many answers the message has) |
| Read one message | `GET /api/messages/item/<message_id>` |
| Read the earlier answers | `GET /api/messages/<message_id>/versions`; `[{version_id, content, current}]`, oldest first |
| Show an earlier answer | `POST /api/messages/<message_id>/versions/<version_id>` with `session_id` (and optionally `expected_content`, `avatar_id`, `dataset_epoch`); the chosen text becomes the message's content and the one it held is kept. The message keeps its id, so memories, voice notes and the reply ledger stay attached. Marks companion knowledge for review/rebuild, like a regeneration |
| Edit | `PATCH /api/messages/<message_id>` with `session_id` and `content`; reviewed clients also send `expected_content`, `avatar_id` and `dataset_epoch` |
| Delete | `DELETE /api/messages/<message_id>` with JSON/query `session_id`; reviewed clients also send `expected_content`, `avatar_id`, `dataset_epoch` and `confirm: "delete-message"` |

Regenerate keeps every answer it replaces, so the person can move between them; the one showing is the one history, memories and notes use. A failed or stopped regeneration leaves the previous answer and adds no version. Editing or deleting a message by hand keeps no old text: that is a correction, not another answer. Edit/Delete validate ownership and reject active work. Reviewed changes compare the original text, companion and dataset; a mismatch returns 409. Successful edits include the sanitized saved `content`. The private host requires all three review fields, requires the explicit confirmation for deletion, enforces a 512 KiB JSON limit and refuses deletion while other requests or owned jobs are active; legacy ordinary clients may omit the review fields. Linked voice-note audio is removed before the message: a cleanup failure leaves the message for retry, although earlier linked audio may already be removed. These changes mark companion knowledge for review/rebuild. Copy is a browser action, not an API. Message text is nonempty, stripped of unsafe control characters and capped at 10,000 characters.

Stop also suppresses remaining speech work. If text has already been saved while speech is draining, Stop returns `saved` and preserves that answer. An already-running synthesizer can finish writing its clip under its existing writer lease, but cancelled clips are not emitted; queued synthesis is cancelled. Browser mic activation and playback Stop latch suppression for the reply so late audio events cannot restart it. Explicit Replay remains available after recording finishes.

## Sessions and reports

`POST /api/sessions` accepts `name` and optional `tags`; `GET /api/sessions?include_archived=true` includes active and archived sessions. Name/tags/archive mutations use `{name}`, `{tags: [...]}` and `{archived: true|false}`. Merge on the destination session uses `{target_session_id: source_id}`; different-companion conversations cannot merge. Split uses `{split_message_id}` and preserves source IDs/timestamps/ownership.

`GET /api/sessions/<id>/export` is a Markdown report. Import posts multipart `file` to `/api/sessions/<id>/import-markdown`; it requires an existing idle target and nonempty valid UTF-8 in Altera's `### You/Altera — time` layout (exports made before the rename use `### Her` and still import), imports the parsed batch atomically, preserves message-body rules/fences and does not restore original timestamps. `POST /api/export` accepts `format: "json"|"markdown"|"pdf"`; PDF can fall back to an HTML report. A JSON export whose memories could not be read has an empty `memories` list and a `warnings` list that says so. These reports are not full restore archives. Use backups for a complete recoverable snapshot.

`GET /api/search` accepts `q`, `session_id`, `role`, `mood`, `date_from`, `date_to` and `limit`. Search is lexical FTS5 literal-word matching. Blank queries return an empty list and stats. Semantic conversation search and full JSON report import are not implemented.

## Scoped memories, backups and diagnostics

Use the companion-scoped `/api/knowledge/scopes/<scope>/...` API for new integrations. `_default` is the default/legacy persona. Remember accepts `content`, optional `type`, finite `confidence` in 0–1, `source_message_id` and `session_id`. Correct/Forget also resets the owner's recall boundary and clears notebook copies. Confirmation values are `forget`, `forget-all`, `rebuild` and `merge` for the corresponding scoped mutations. [Memory controls](MEMORY_CONTROLS.md) describes provenance, failure handling and compatibility `/api/memory` routes.


`PATCH /api/knowledge/scopes/<scope>/memories/<memory_id>/priority` accepts the current `dataset_epoch`, priority `revision`, `content_sha256`, and one or both of boolean `pinned` and integer `salience` (1–5). The inventory supplies the reviewed revision/hash. The result contains `id`, `priority` and `content_sha256`. This changes retrieval priority without changing claim confidence, correcting content or advancing the Forget boundary. Priority receipts expose age/decay, pin status and successful recall counts; a pin never makes a stale source eligible.


`GET /api/knowledge/scopes/<scope>/report` returns a deterministic version-1 saved-context report without inference: scope, generation time, recall/repair state, counts, limitations, memories with eligibility/reason codes and source excerpts, aggregate notebook provenance, conversation-context candidates and applicable legacy default-persona context. Eligible information is not necessarily selected or sent to any reply. Current sources verify saved message integrity, not the truth of an extracted claim; notebook sources are aggregate, not claim-level citations. The report omits machine configuration and other companions' stores. Snapshot creation returns 409 while other writers/requests are active rather than mixing revisions. All report responses are `Cache-Control: no-store`.

`GET /api/knowledge/scopes/<scope>/report/export?format=json|markdown` downloads that report as an attachment; the default is JSON, unsupported formats return 400, and unknown scopes return 404. These private plaintext reports are readable exports, not complete backups. See [Memory controls](MEMORY_CONTROLS.md) for coverage and exclusions.

The current UI requires exact `backup_api_version: 2` from the listing and posts only to `/api/backups/v2`, `/api/backups/v2/restore/preview` and `/api/backups/v2/restore/apply`. Missing/unsupported capability disables creation and restore while leaving download links available. Those mutation URLs return 404 on the older plaintext-only implementation even if the process is rolled back after a capability response; the UI never falls back to its legacy mutation paths. Restarting the intended release is still a separate activation step. The unversioned routes remain API aliases.


Faulted recovery preview also returns `protected_settings`: `hash_matched` means the retained settings are a readable YAML mapping matching the journal's fingerprint; `legacy_unverified` means an older journal has no fingerprint; `unavailable` means the file cannot be verified. The response contains no settings, path or credential. This is a point-in-time inspection, not a repair capability or a guarantee that repair would succeed.

`POST /api/backups` defaults to an encrypted archive: supply `password`, optional boolean `include_recovery_key`, and optional current `browser_state`; explicit `format: "zip"` creates plaintext with no password/key request. Creation passwords require at least 12 Unicode characters and at most 1,024 UTF-8 bytes, preserved without trimming/normalization. An optional recovery key is returned once and never stored. Preview accepts saved `backup_id` as JSON or multipart `file`, explicit `restore_connections`, and exactly one `password` or `recovery_key` for encrypted input. Apply requires `token`, `confirm: "restore"`, the source credential again, and default encrypted recovery with `recovery_password`; explicit `recovery_format: "zip"` is allowed only for plaintext source. A fault repair reuses the existing recovery copy. A successful-token retry returns its saved application receipt without replacing/decrypting again. Expired owned preview uploads are reclaimed under a bounded operation lock; interrupted recovery work is retained. All backup responses are no-store/nosniff, and encrypted list counts remain hidden until unlock. [Backup and restore](BACKUP_AND_RESTORE.md) defines coverage, limits, recovery and browser-state fields. Downloads are private attachments, not public share links.

`GET /api/diagnostics?session_id=<id>` returns the latest 20 measured request summaries for that conversation plus current provider/notebook context, with no prompts, message text, notebook text, endpoint URLs or credentials. [Measurement coverage](DIAGNOSTICS.md) explains reported usage/cost versus estimates and excluded background calls.

## Speech, providers, pictures and tools

Voice status retains `stt_available` as the speech-package installation indicator and adds `stt_model_state`: `not_installed`, `unavailable`, `cached`, or `loaded`. Cache inspection is local-only and does not load a model; `cached` checks expected files, not their integrity or successful inference. `unavailable` includes missing, incomplete or unreadable assets. The route does not queue speech startup.

`POST /api/voice/activity?session_id=<id>` checks a local speech-activity frame. Send raw mono signed PCM16 little-endian with `Content-Type: audio/pcm`, `X-Audio-Sample-Rate: 16000` and current `X-Altera-Dataset`. Frames contain 512–16,000 samples (at most 32,000 bytes). Response includes `available`, `speech`, measured maximum `probability`, `sample_rate`, `samples`, `session_id` and exact `avatar_id`. Installed CPU Silero inference neither transcribes nor retains audio. Invalid input returns 400/413, missing session/owner 404, stale/busy state 409 and unavailable detector 503. Failed browser monitors disable visibly rather than polling indefinitely.

Voice-note IDs are `vn_` plus 32 lowercase hex characters. `GET /api/voice-notes/context?session_id=<id>` requires exactly one positive-integer `session_id` and no extra query keys. It reads the datastore without constructing/reconciling the voice service or starting jobs (usual missing dataset-epoch initialization still applies). The lookup returns `avatar_id` (empty for Default), `dataset_epoch`, `source_revision` and `recall_after`. Multipart `POST /api/voice-notes` requires those fields plus `note_id`, `session_id` and `audio`. Actual format/codec/duration are checked: 20 MiB/five-minute limits. Stale context returns 409 before publication; matching ID/content retries deduplicate, and deleted IDs cannot be resurrected. Public notes contain states, hashes/byte counts, transcript/review revisions, durable request/message IDs and replay URLs, never local paths.

Ordinary note responses include `audio_warning` and omit `audio_url` when a saved input recording is missing or fails its recorded byte-count/SHA-256 check. Corrupted playback, transcription and matching upload retries return a 409 rather than acknowledging or consuming changed audio; a missing playback file can return 404. A transcription worker rechecks the bytes it actually reads, and a damaged original remains on disk for inspection. Reply clips use their content-addressed filename for a separate integrity check. These checks do not protect against external writers racing a subsequent file read.


Explicit `POST /api/voice-notes/<note_id>/transcribe` uses `{dataset_epoch}` for cached-local transcription with the configured `voice.stt_model` (default `base`). Matching retries join an active job; interrupted jobs need an explicit retry. `PUT /api/voice-notes/<note_id>` uses `{dataset_epoch, transcript, revision}`; `POST …/review` uses `{dataset_epoch, revision}`. Send via `/api/chat/stream` or durable `/api/chat` with `{voice_note_id, session_id, dataset_epoch, reviewed_revision}`. The server supplies the reviewed text and immutable request ID, overriding client message/request values, and revalidates context under the submission lock. Upload/review never sends or transcribes automatically.

`GET /api/voice-notes?session_id=<id>` lists notes; `GET /api/voice-notes/<note_id>` reconciles without inference. Replay supports byte ranges and `no-store`. Delete uses `{confirm: "delete-voice-note", dataset_epoch}`. Failures remain retryable and redacted tombstones survive invalidation. See [Voice](VOICE.md) for browser retention, recovery and model availability.

- `POST /api/voice/toggle`: `{enabled: true|false}`; saved preference still obeys instance `voice.tts_enabled`.
- `POST /api/voice/tts`: `{text, voice?}` → `audio_url`, or `audio_url: null` with a `message` saying why it stayed silent
  (the local voice not installed, not running, still loading, or without that voice). `voice` speaks a sample in one of
  the voices `/api/pals/options` lists (400 for any other). `POST /api/voice/stt`: multipart `file` or raw audio bytes → `text`/`language`.
- `POST /api/llm/switch`: `{provider, model?}`. Provider configuration uses `PUT /api/llm/config` with `provider` and allowed connection fields. Credentials are saved in ignored local configuration. Test connection lists models; it does not generate text. `POST /api/setup/check` is the one place that does: the first-run page sends a single fixed `Reply with the single word OK.` prompt, clearly labelled as a test, and nothing a person typed is included.
- Companion opening/notes, gallery synchronization and NPC Forge creation are described in [Companions](COMPANIONS.md). Pictures and their content checks are described in [the image guide](AVATARS.md).
- Workspace routes require tools enabled. File writes use `{path, content}` with a `workspace/` path and UTF-8 text; reads use query `path`, and URL fetch uses `{url}`. File read/write and fetch payloads are capped at 512 KiB. Fetch connects directly to validated public addresses, rechecks redirects and ignores ambient `HTTP_PROXY`/`HTTPS_PROXY`; private/shared/Tailscale destinations and secret files are refused.
- `POST /api/clear-data` requires `{confirm: "delete-all"}` and idle writers, including unfinished speech. It clears conversations/derived knowledge, pictures, current and legacy speech cache, profile and server queue, and atomically changes the dataset identifier with the SQLite reset. Failures return 500 with `code: "clear_incomplete"` and can be partial; retry before using Altera. Imported avatars, backups/exports, logs and workspace files remain. Global legacy memory clearing requires `{confirm: "yes"}`. These confirmations are accidental-deletion guards, not authentication.

## Route inventory

The following inventory is derived from route decorators and blueprint prefixes. Flask also supplies automatic HEAD/OPTIONS behavior where applicable.


### Administration and legacy queue

Source: [`admin.py`](../core/routes/admin.py).

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/clear-data` | Delete all conversations, memories, profile state, and derived data. |
| `GET` | `/api/offline/status` | Check if the server is reachable (for offline detection). |
| `POST` | `/api/messages/queue` | Queue a message for later delivery when online. |
| `POST` | `/api/messages/queue/retry` | Retry all queued messages. |
| `GET` | `/api/pwa/install-prompt` | Return install prompt state (for frontend to decide whether to show banner). |


### Workspace tools

Source: [`agent_tools.py`](../core/routes/agent_tools.py).

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/tools/files` | List visible project files for the UI workspace browser. |
| `GET` | `/api/tools/file` | Read a text file inside the project workspace. |
| `POST` | `/api/tools/file` | Write a UTF-8 text file inside the project's writable subdirectory. |
| `POST` | `/api/tools/fetch` | Fetch a small HTTP(S) resource for quick research from the UI. |


### Backup and restore

Source: [`backups.py`](../core/routes/backups.py).

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/backups` | Saved archives, recovery status and `backup_api_version: 2` capability. |
| `POST` | `/api/backups` | Create a versioned archive. |
| `POST` | `/api/backups/v2` | Current UI creation contract; unknown on older plaintext-only servers. |
| `GET` | `/api/backups/<backup_id>/download` | Private encrypted binary or plaintext ZIP attachment. |
| `POST` | `/api/backups/restore/preview` | Validate and stage a restore preview. |
| `POST` | `/api/backups/v2/restore/preview` | Versioned alias used by the current backup UI. |
| `POST` | `/api/backups/restore/apply` | Confirmed replacement with recovery backup. |
| `POST` | `/api/backups/v2/restore/apply` | Versioned alias used by the current backup UI. |
| `GET` | `/api/backups/recovery` | Standalone recovery page. |


### Avatars, pictures and adult mode

Source: [`companion.py`](../core/routes/companion.py).

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/avatars` | Imported avatars. |
| `GET` | `/api/avatars/forge` | NPC Forge library candidates. |
| `GET` | `/api/forge/<npc_id>/thumb` | Source character thumbnail. |
| `POST` | `/api/avatars/import` | Import a library character. |
| `POST` | `/api/avatars/<avatar_id>/activate` | Set active avatar. |
| `DELETE` | `/api/avatars/<avatar_id>` | Remove avatar data. |
| `GET` | `/api/avatars/<avatar_id>/portrait` | Private portrait. A pal, a card without a picture, or a companion written here with no picture yet gets its initial (an SVG); an imported companion with no file is 404. |
| `POST` | `/api/avatars/<avatar_id>/portrait` | Multipart `file`: a PNG picture for a pal, a character or a companion written in Altera (a character's card picture is kept to go back to). It goes through the age check like a Forge portrait (422 when it reads as under 18, or is not a PNG Altera can show); 413 when it is too large. |
| `DELETE` | `/api/avatars/<avatar_id>/portrait` | Take away an uploaded portrait; a character's card picture or the initial comes back. 409 when there is no uploaded portrait. |
| `GET` | `/api/living` | Living portrait preference. |
| `POST` | `/api/living` | Update living portrait preference. |
| `GET` | `/api/avatars/<avatar_id>/living` | Living portrait frame metadata. |
| `POST` | `/api/avatars/<avatar_id>/living/sync` | Synchronize living portrait frames. |
| `GET` | `/api/avatars/<avatar_id>/living/<frame>` | Private living portrait frame. |
| `GET` | `/api/avatars/<avatar_id>/premade` | List picture candidates from an installed source. Only previously checked and imported pictures have image URLs. |
| `POST` | `/api/avatars/<avatar_id>/premade/import` | Import one candidate through the core picture checks. |
| `POST` | `/api/photos` | The camera button: ask for a picture directly. |
| `POST` | `/api/shared-scenes/context` | Review the current conversation's vision and image recipients without sending a photo. Returns 409 when no configured workflow accepts both reference inputs. |
| `POST` | `/api/shared-scenes` | Submit a confirmed, bounded user reference and everyday imagined scene to the reviewed recipients. |
| `GET` | `/api/photos` | Picture list. |
| `GET` | `/api/photos/<photo_id>` | Picture status/metadata. |
| `GET` | `/api/photos/<photo_id>/image` | Private picture file. |
| `POST` | `/api/photos/<photo_id>/reroll` | Generate a replacement picture. |
| `DELETE` | `/api/photos/<photo_id>` | Remove a picture. |
| `GET` | `/api/imagegen/status` | Is ComfyUI reachable, and which vision model does the age check use? (Makes a short network call.). |

Photo metadata returned by creation, lookup, listing and reuse includes `provenance: "imagined_scene"`. This also applies to existing imported and generated records without rewriting them. A photo request or image is not evidence that its depicted scene occurred.

The camera and both shared-scene endpoints return `422` with `blocked: true` for a pal in kids mode. An earlier recipient review does not override the current setting.

The shared-scene context body contains `session_id`, `avatar_id` and `dataset_epoch`; it returns vision endpoint/model, image endpoint and an opaque recipient fingerprint. Submit those scope fields plus `request_id` (`shared_` and 32 lowercase hex digits), `scene`, base64 `reference_png`, `provider_fingerprint`, and true `self_reference`, `adult_confirmation` and `provider_confirmation`. The fingerprint must still match before a provider is contacted; accepted work retains the reviewed targets. A matching retry attaches the same conversation marker and does not regenerate. A removed or edited marker cannot be recreated by retry. Pending work must finish before its image or session can be deleted. The original reference is not retained by Altera, but configured providers may retain the submitted copy. See [image checks](SAFETY.md#image-checks) for limits.


### Companions and NPC Forge

Source: [`companions.py`](../core/routes/companions.py).

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/companions` | Companion cards and counts. |
| `POST` | `/api/companions` | Write a companion in Altera, without NPC Forge: `{name, age, gender?, bio?, hobbies?, speech?, demeanor?, quirk?, goal?, peeve?, voice?}` -> the new companion, opened (201). The age must be 18 or older and the text passes `safety.card_problem`, exactly as an import; 400 with the reason otherwise. `hobbies` takes a list (or a comma-separated string). |
| `GET` | `/api/companions/<avatar_id>` | A companion written here: `written: true`, `name`, `age`, `gender`, `voice`, `features` (all four, always on), and the card fields `bio`, `hobbies`, `speech`, `demeanor`, `quirk`, `goal`, `peeve`. A companion from NPC Forge (changed in NPC Forge, not here) gets `{written: false, id, name, voice, features}`. 404 for anyone who is not a companion. |
| `PUT` | `/api/companions/<avatar_id>` | Change any of those fields (only what is sent) and return the settings. The same age and safety rules apply. |
| `GET` | `/api/companions/current` | Resolve/open current companion. |
| `GET` | `/api/people` | Start screen data: everyone with their `kind` (`pal`, `character`, `companion`), counts per kind and the person to continue with. Optional `?kind=` narrows the list (not the counts); an unknown kind is 400. Reads only: nobody is opened or activated. Records saved before kinds are written as `companion` once. |
| `GET` | `/api/people/<avatar_id>/replies` | This person's own reply settings, for anyone (pal, character or companion): `own` (`temperature` and `max_tokens`, `null` where the app-wide value is used), `app` (the app-wide values) and `limits`. 404 for an unknown person. See [Reply settings](REPLY_SETTINGS.md). |
| `PUT` | `/api/people/<avatar_id>/replies` | `{temperature?, max_tokens?}`: a number, or `null` for the app-wide value again. Kept in the person's `companion.json`. 400 for other fields or a value outside its limits; returns the same as `GET`. |
| `POST` | `/api/companions/<avatar_id>/open` | Open companion and owned chat. |
| `GET` | `/api/pals/options` | Pal vibes from `content/pals/` (`id`, `label`, `description`) and the voices for the configured speech engine (`engine`, `default`, `options`). |
| `POST` | `/api/pals` | `{name, vibe, voice?, kids?}` makes a pal (writes `data/avatars/<id>/pal.yaml` and its record) and opens it; 201 with the opened summary. Memory, notes, story and presence start off. 400 for a missing/long name, unknown vibe or a voice not offered; 409 for `kids: true` while kids mode is locked. |
| `GET` | `/api/pals/<avatar_id>` | A pal's settings: `name`, `vibe`, `vibe_label`, `voice`, `features` (`memory`, `notes`, `story`, `presence`), `kids`, and who it is: `about`, `traits`, `speech_style` (`tone`, `formality`, `humor`, `emojis`), `interests`, `quirks`. 404 for anyone who is not a pal. |
| `PUT` | `/api/pals/<avatar_id>` | `{name?, voice?, persona?, features?, kids?, pin?}` changes a pal and returns its settings. `persona` holds any of `about` (up to 400 characters), `traits` (up to 8), `interests` (up to 8), `quirks` (up to 6), each a list of short phrases, and `speech_style` (`tone`, `formality`, `humor` up to 120 characters, `emojis` true or false; what is not sent stays). Too long or too many is 400 with what to fix, as is text that breaks the card rules or, for a pal in kids mode, the kids-mode topics. Other fields are 400. Moving `kids` either way needs the parent PIN in `pin` (403 without it, or when it is wrong); moving it while kids mode is locked is 409, and so is `presence: true` for a pal in kids mode. |
| `GET` | `/api/kids` | Kids mode for this Altera: `enabled`, `enabled_at`, `pin_set`, and `pals`. When an add-on is installed, `blocked_reason` explains why Kids mode setup is unavailable. See [Kids mode](KIDS_MODE.md). |
| `POST` | `/api/kids` | `{enabled: true, understood: true, pin}` unlocks kids mode and sets the parent PIN (400 without `understood: true` or a 4–8 digit `pin`). `{enabled: false, pin}` locks it again (409 while a pal is in kids mode). Once a PIN exists both need it (403 when it is wrong). Returns the same as `GET`. |
| `POST` | `/api/characters/import` | Multipart `file` accepts CHARX, PNG and JSON character cards. Saves the original and local character, then returns 201 with the opened summary, `card_id` and any `card_voice` offer. Returns 400 for a damaged or refused card, or 413 for an oversized upload. See [Characters](CHARACTERS.md). |
| `GET` | `/api/characters/inbox` | Lists waiting suite handoffs as `cards` with opaque IDs, sender labels and size problems. |
| `POST`, `DELETE` | `/api/characters/inbox/<key>` | Explicitly import or dismiss a waiting card by its opaque inbox ID. A refused import returns its reason and leaves the file waiting. |
| `GET` | `/api/characters/starters` | Lists the starter characters that come with Altera and have not been added: `starters` with `key`, `name` and `line`. |
| `GET` | `/api/characters/starters/<key>/portrait` | The starter card's portrait as PNG. 404 for a key that is not a starter. |
| `POST` | `/api/characters/starters/<key>` | Adds a starter by importing its card with the same checks as `/api/characters/import`, and returns 201 like it. Adding one already added returns that character. |
| `POST` | `/api/characters/<avatar_id>/card-voice` | Send `{"action":"preview"}` to review a ready preset or voice file. Send `{"action":"use","review":"…","permission":true}` to choose the reviewed voice. Embedded files go to local Voice Lab. |
| `GET` | `/api/characters/<avatar_id>` | A character's settings: `name`, `voice`, `features`, `card_instructions`, `has_instructions`, `lorebook` (on or off), `book` (`{name, entries: [{name, keys, constant, enabled, regex}]}`, without what the entries say), `creator_notes`, `creator`, `spec`. 404 for anyone who is not a character. |
| `PUT` | `/api/characters/<avatar_id>` | `{voice?, features?, card_instructions?, lorebook?}` changes a character and returns its settings. Other fields (the name included) are 400. |
| `DELETE` | `/api/companions/<avatar_id>` | Remove companion and owned data. |
| `GET` | `/api/companions/<avatar_id>/notes` | Read notebook and provenance. |
| `PUT` | `/api/companions/<avatar_id>/notes` | Correct the companion's notes. |
| `POST` | `/api/companions/<avatar_id>/notes/update` | Ask the companion to write up the conversation so far. |
| `GET` | `/api/companions/<avatar_id>/status` | Notebook/gallery background status. |
| `POST` | `/api/companions/<avatar_id>/pictures/sync` | Fetch the pictures approved in NPC Forge's studio (in the background). |
| `GET` | `/api/forge/status` | Source reachability, `meet_supported`, Modern life `options` for Meet and `meet_message` when the Forge version or setting prevents rolling. Also lists portrait `styles`, `settings`, `engines` and `default_engines`. |
| `POST` | `/api/forge/start` | Start configured local NPC Forge. |
| `POST` | `/api/companions/make` | Roll a fictional adult in Modern life (Forge API 44+) and queue their portrait. Optional fields are `gender` (`female`, `male`, `nonbinary` or blank for Any), `origin`, `hair_color` and `age_group`. |
| `GET` | `/api/portraits/<avatar_id>` | A pal's, character's or written companion's portrait: `from` (`forge`, `card`, `upload` or `none`), `card_picture`, `job` (`{kind, started}` while NPC Forge works), `living`, `can_bring_to_life`, `can_upload` (no job running), `age_checked` (`passed`, `unchecked` or `none`), `removed` (`{at, from, apparent_age}` when the later age check took the last portrait down, else `null`), and the `description`, `gender`, `style` and `setting` to start from. An unchecked portrait is age-checked first when the model answers. 404 for a companion from NPC Forge. See [Portraits](PORTRAITS.md). |
| `POST` | `/api/portraits/<avatar_id>` | `{from: description\|card, description?, style, setting, gender?, image_model?, edit_model?}`: NPC Forge starts painting (202, the same as `GET`, which also returns the engines last chosen). It works for a pal, a character and a companion written here. An engine left out or `""` is NPC Forge's own default; `image_model` draws from words, `edit_model` redraws the card's picture. 400 for an empty or refused description, a card without a picture or an unknown field; 409 while a job is running; 503 when NPC Forge cannot paint. |
| `POST` | `/api/portraits/<avatar_id>/describe` | A character or a companion written here: the chat model writes how they look from the card or background (creator's notes are never sent): `{description}`, `""` when there is nothing about looks. 503 when the model does not answer. |
| `GET` | `/api/portraits/<avatar_id>/job` | `{state, note, portrait}`: `queued`, `running`, `ready` (the portrait was age-checked and brought in), `rejected` (it read as under 18), `error`, `cancelled`, `unreachable` (the job is kept) or `none`. |
| `DELETE` | `/api/portraits/<avatar_id>/job` | Cancel the job. |
| `POST` | `/api/portraits/<avatar_id>/living` | `{edit_model?}`: NPC Forge makes the living portrait from the portrait it painted (202), with that redraw engine (`""` for NPC Forge's default; left out, the one chosen for the portrait). The choice is kept. 409 without a portrait; 503 for an engine NPC Forge does not list. |
| `POST` | `/api/portraits/<avatar_id>/library` | `{npc_id}`: use an existing NPC Forge character's portrait (`GET /api/avatars/forge` lists available characters). The current Forge serves the card through its API. Altera age-checks the portrait and saves a local copy. |
| `DELETE` | `/api/portraits/<avatar_id>/forge` | Stop using the NPC Forge portrait: back to the card's picture, or the initial. |
| `GET` | `/api/companions/make/<npc_id>/<job_id>` | Where the portrait is. |
| `DELETE` | `/api/companions/make/<npc_id>/<job_id>` | Cancel character portrait job. |
| `POST` | `/api/companions/make/<npc_id>/<job_id>/retry` | Resume a portrait NPC Forge stopped because the GPU was taken, once the GPU is ready again. 409 when the job is not waiting for the GPU. |


### Chat, messages, legacy memory and profile

Source: [`core_api.py`](../core/routes/core_api.py).

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/messages/<int:session_id>` | Get all messages for a session. |
| `POST` | `/api/chat` | Handle a chat message -- returns full response. |
| `POST` | `/api/chat/stream` | Handle a chat message -- returns streaming response. |
| `POST` | `/api/chat/regenerate` | Durable regenerate/retry stream. |
| `POST` | `/api/chat/requests/<request_id>/stop` | Cancel owned request. |
| `GET` | `/api/messages/item/<int:message_id>` | Read a message by ID. |
| `GET` | `/api/messages/<int:message_id>/versions` | The earlier answers kept for a message. |
| `POST` | `/api/messages/<int:message_id>/versions/<int:version_id>` | Show an earlier answer again. |
| `PATCH, DELETE` | `/api/messages/<int:message_id>` | Edit/delete a message in its session. |
| `GET` | `/api/chat/requests/<request_id>` | Durable request status and partial text. |
| `POST` | `/api/memory` | Manually add a memory to ChromaDB. |
| `PUT` | `/api/memory/<memory_id>` | Update an existing memory. |
| `GET` | `/api/memory` | Selected-owner memory inventory. |
| `DELETE` | `/api/memory/<memory_id>` | Delete a memory. |
| `POST` | `/api/memory/clear` | Clear all memories from ChromaDB. |
| `GET` | `/api/profile` | Get user profile. |
| `GET` | `/api/onboarding/status` | Whether the first run is finished, and the saved name. |
| `POST` | `/api/onboarding/complete` | Finish the first run and save the name (blank keeps the old one). |
| `PUT` | `/api/profile/last-session` | Update the last active session ID. |


### Mood, voice and proactive engagement

Source: [`engagement.py`](../core/routes/engagement.py).

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/mood` | Detect mood from a message. |
| `GET` | `/api/mood/trend` | Get mood trend over the last N days (Phase 4). |
| `GET` | `/api/mood/history` | Get recent emotional history (Phase 4). |
| `GET` | `/api/voice/status` | Read voice availability/state, including pending shutdown, without queueing startup. Private voice processing remains gated. |
| `POST` | `/api/voice/toggle` | Toggle voice playback on/off for the current user. |
| `POST` | `/api/voice/tts` | Generate speech for given text. |
| `GET` | `/api/voice/lab` | Check Voice Lab's ready voice count, whether it is selected, and the message to show. |
| `POST` | `/api/voice/engine` | Select `{"engine":"voicelab"}` for speech, or `{"engine":"kokoro"}` to use Altera's local Kokoro. |
| `POST` | `/api/voice/activity` | Bounded local CPU speech detection without audio storage. |
| `GET, POST` | `/api/voice-notes` | List by session or upload a context-bound recording. |
| `GET` | `/api/voice-notes/context` | Capture session/owner/dataset/recall context. |
| `GET, PUT, DELETE` | `/api/voice-notes/<note_id>` | Read, edit or delete a saved voice note. |
| `POST` | `/api/voice-notes/<note_id>/transcribe` | Explicit cached-local transcription job. |
| `POST` | `/api/voice-notes/<note_id>/review` | Approve the current transcript revision. |
| `GET` | `/api/voice-notes/<note_id>/audio` | Replay the original recording. |
| `GET` | `/api/voice-notes/<note_id>/replies/<filename>` | Replay a retained reply clip. |
| `POST` | `/api/voice/stt` | Receive audio data and transcribe it. |
| `GET` | `/api/proactive/settings` | Read legacy compatibility settings; they do not enable scheduled presence. |
| `POST` | `/api/proactive/settings` | Save legacy compatibility settings; use presence preferences for new scheduling. |
| `GET` | `/api/proactive/checkin` | Legacy compatibility: no inference; points to the opted-in inbox. |


### Legacy jokes, opinions and humor

Source: [`jokes_opinions.py`](../core/routes/jokes_opinions.py).

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/jokes/callbacks` | Get inside joke callbacks and shared references. |
| `POST` | `/api/jokes/extract` | Extract jokes and references from a message. |
| `GET` | `/api/opinions/user` | Get stored user opinions. |
| `POST` | `/api/opinions/extract` | Extract opinions from a message. |
| `GET` | `/api/humor/style` | Get the user's humor style. |
| `POST` | `/api/humor/analyze` | Analyze the user's humor style from messages. |


### Scoped knowledge controls

Source: [`knowledge.py`](../core/routes/knowledge.py).

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/knowledge/scopes` | Companion/default knowledge scopes. |
| `GET` | `/api/knowledge/messages/<int:message_id>` | Current source and owner. |
| `GET` | `/api/knowledge/scopes/<scope>/report` | Deterministic scoped saved-context report. |
| `GET` | `/api/knowledge/scopes/<scope>/report/export` | Scoped JSON/Markdown report attachment. |
| `GET, POST, DELETE` | `/api/knowledge/scopes/<scope>/memories` | Scoped inventory/Remember/Forget all. |
| `PUT, DELETE` | `/api/knowledge/scopes/<scope>/memories/<memory_id>` | Scoped Correct/Forget. |
| `PATCH` | `/api/knowledge/scopes/<scope>/memories/<memory_id>/priority` | Set reviewed pin/salience priority without changing claim confidence. |
| `POST` | `/api/knowledge/scopes/<scope>/repair` | Rebuild automatic memories. |
| `GET, POST` | `/api/knowledge/scopes/<scope>/duplicates` | Review/consolidate scoped duplicates. |


### Provider/model management

Source: [`llm.py`](../core/routes/llm.py).

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/llm/providers` | List all configured providers with status. |
| `GET` | `/api/llm/providers/<provider_key>/models` | Fetch available models from a provider. |
| `GET` | `/api/llm/active` | Get current active provider + model. |
| `POST` | `/api/llm/switch` | Switch provider and/or model. |
| `POST` | `/api/llm/test-connection` | Test connection to a provider. |
| `PUT` | `/api/llm/config` | Update provider configuration (for custom provider). |
| `GET` | `/api/llm/sampling` | How replies are sampled: `temperature`, `max_tokens` (the reply length), `sampling` (only the optional settings that are set: `top_p`, `min_p`, `repetition_penalty`, `presence_penalty`, `frequency_penalty`), `provider`, `not_sent` (the optional settings this provider is never sent) and `limits` (`{min, max, whole, label}` for each). See [Reply settings](REPLY_SETTINGS.md). |
| `PUT` | `/api/llm/sampling` | Any of those settings. An optional one sent as `null` is cleared and is then not sent at all; `temperature` and `max_tokens` must be numbers. 400 for an unknown field or a value outside its limits, and then nothing changes. Saved to `config.yaml`; returns the same as `GET`. |


### First-run setup

Source: [`setup.py`](../core/routes/setup.py).

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/setup/check` | Send one tiny fixed prompt to the selected model and report whether it answered. A missing, down, misconfigured or silent provider is a result (`ok: false` with `reason`, `detail` and `hint`), never a server error. |
| `GET` | `/api/setup/local/<provider>` | For LM Studio and Ollama at their normal loopback ports, report server state, available models, a hardware-based suggestion when the library is empty, and the latest job. |
| `POST` | `/api/setup/local/<provider>/start` | With `{confirmed: true}`, start the installed local provider and return a job. |
| `POST` | `/api/setup/local/ollama/download` | With `{confirmed: true, model}`, ask local Ollama to download an offered model. |
| `GET`, `DELETE` | `/api/setup/local/<provider>/jobs/<job_id>` | Read an action's status or stop an active download. |


### App lock and kids view

Source: [`lock.py`](../core/routes/lock.py). The PIN is a salted scrypt hash in the database, rate-limited after five
wrong tries, and never returned or logged. A browser that has not entered it sees `/lock`; a browser in the kids view
may reach only kids-mode pals, and every other page and API call is refused by the server. See [Kids mode](KIDS_MODE.md).

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/lock` | The lock screen: the PIN (the whole app) or Kids (only kids pals). |
| `GET` | `/api/lock` | `{pin_set, kids_enabled, kids_view, unlocked, locked, kids_pals}`. Never the PIN, its hash or the try counter. |
| `POST` | `/api/lock/unlock` | `{pin}` opens the whole app for this browser. 403 for a wrong PIN, 429 after too many tries. |
| `POST` | `/api/lock/kids` | Enters the kids view without a PIN (a grown-up set one with kids mode) and opens a kids pal. 409 when kids mode is off, no PIN is set, or no pal is in kids mode. Optional `{avatar_id}` chooses which kids pal. |
| `POST` | `/api/lock/leave` | `{pin}` leaves the kids view and unlocks this browser. 403 for a wrong PIN. |
| `POST` | `/api/lock/lock` | Locks this browser again. Anyone holding the tablet can lock it. |
| `POST` | `/api/lock/pin` | `{new}` sets the first PIN, or `{current, new}` changes one. 403 when `current` is wrong, 409 from inside the kids view. |

### Pages and service worker

Source: [`pages.py`](../core/routes/pages.py).

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/sw.js` | A worker at the root can control the chat and the other app pages. |
| `GET` | `/` | Main chat interface. `?meet=1` opens Meet someone new once. |
| `GET` | `/setup` | First-run setup: a name, one tiny model check, and which optional pieces are running. `?check=1` is the same page opened from Settings. |
| `GET` | `/home` | Start screen: Pals, Characters and Companions tiles, continue with the last person, and a gallery of everyone. Launching with `--open` and the installed app open here. Until the first run is finished it redirects to `/setup`. |
| `GET` | `/memory` | Memory management page. |
| `GET` | `/timeline` | Timeline view of conversations with mood annotations. |
| `GET` | `/search` | Render the conversation search page. |
| `GET` | `/knowledge` | Render the knowledge summary / life data page. |
| `GET` | `/story` | Inspect source-backed episodes, curate Our story and control reflection. |
| `GET` | `/lock` | The lock screen, shown to a browser that has not entered the PIN (see above). |


### Legacy personality tools

Source: [`personality.py`](../core/routes/personality.py).

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/personality/style` | Get the user's communication style profile. |
| `POST` | `/api/personality/style/analyze` | Force re-analysis of communication style. |
| `GET` | `/api/personality/evolution` | Get personality evolution data. |
| `GET` | `/api/personality/templates` | Get available personality templates. |
| `GET` | `/api/personality/templates/<template_name>` | Get a specific personality template. |
| `POST` | `/api/personality/custom` | Save a custom personality template. |
| `GET` | `/api/personality/custom/templates` | Get all custom personality templates. |
| `POST` | `/api/personality/mood-adjustments` | Get personality adjustments based on detected mood. |


### Legacy relationship graph

Source: [`relationships.py`](../core/routes/relationships.py).

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/relationships` | Get the full relationship graph. |
| `GET` | `/api/relationships/search` | Search entities by name. |
| `GET` | `/api/relationships/people` | Get all tracked people. |
| `GET` | `/api/relationships/places` | Get all tracked places. |
| `GET` | `/api/relationships/depth` | Get relationship depth scores and insights. |


### Search, reports and legacy summaries

Source: [`search_export.py`](../core/routes/search_export.py).

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/search` | Search conversations with filters. |
| `GET` | `/api/search/stats` | Get search statistics. |
| `POST` | `/api/export` | Export all data in specified format. |
| `GET` | `/api/knowledge/summary` | Get a summary of Altera's knowledge about the user. |
| `GET` | `/api/memory/dedup` | Find duplicate memories for review. |
| `POST` | `/api/memory/dedup/consolidate` | Consolidate duplicate memories. |


### Session management

Source: [`sessions.py`](../core/routes/sessions.py).

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/sessions` | List all conversation sessions. |
| `POST` | `/api/sessions` | Create a new conversation session. |
| `POST` | `/api/sessions/<int:session_id>/archive` | Archive or unarchive a conversation session. |
| `PUT` | `/api/sessions/<int:session_id>/name` | Update the name of a conversation session. |
| `PUT` | `/api/sessions/<int:session_id>/tags` | Update the tags of a conversation session. |
| `GET` | `/api/sessions/archive/list` | List all archived conversation sessions. |
| `POST` | `/api/sessions/<int:session_id>/merge` | Merge another session into this session. |
| `POST` | `/api/sessions/<int:session_id>/split` | Split a session at a specific message ID. |
| `DELETE` | `/api/sessions/<int:session_id>` | Delete a conversation session. |
| `GET` | `/api/sessions/<int:session_id>/export` | Export a single conversation session as Markdown. |
| `POST` | `/api/sessions/<int:session_id>/import-markdown` | Import a markdown file into the current session. |
| `POST` | `/api/sessions/auto-name/<int:session_id>` | Auto-generate a descriptive name for the session based on content. |
| `GET` | `/api/sessions/last-active` | Get the most recent active session ID. |


### Health and diagnostics

Source: [`system.py`](../core/routes/system.py).

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Suite discovery. Returns `ok: true` and the `fels` block with `contract`, `app`, `version` and `api`. Available without the Altera PIN. |
| `GET` | `/api/health/details` | App dependency checks for the UI. Accepts an optional `session_id` and uses the app's normal access controls. |
| `GET` | `/api/gpu` | Suite GPU lock state (`free`, `shared` or `alone`), current holders and any pending exclusive request. Uses the app's normal access controls. |
| `POST` | `/api/unload` | Local app-to-app request with JSON. Frees Altera's own GPU voice and microphone models, returning `{ok: true}`. Returns 409 with `code: "busy"` and `doing` during active work. Available without the PIN to direct loopback clients. Browser and proxied requests are refused. |
| `GET` | `/api/diagnostics` | Private per-request diagnostics. |
| `GET` / `POST` | `/api/updates` | Direct local access only. GET reports installed and available versions and download progress. POST with `{action}` accepts `check`, `download`, `stop`, `install` or `undo`. Only the managed Windows launch can download, open Setup or undo. |
| `GET` | `/api/conversation-starters` | Get suggested conversation starters based on time of day and user interests. |


### Application assets

Source: [`core/app.py`](../core/app.py).

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/static/<path:filename>` | Flask static application assets |
| `GET` | `/static/audio/tts/<path:filename>` | Instance speech cache, always no-store |

Use `/sw.js` for root-scope service-worker registration. Private data routes remain subject to the same access policy as the chat.

### Companion presence

Source: [presence.py](../core/routes/presence.py). Every response is `no-store`. Reads never generate and accept exactly one `session_id` query argument, with no unknown/duplicate arguments. Writes require a positive integer `session_id`, the current `dataset_epoch` and frozen `avatar_id` (empty string for the default companion). The owner/dataset are rechecked inside the write transaction. JSON is bounded to 16 KiB; duplicate fields at any nesting level, nonfinite numbers, unknown fields, invalid types/zones, stale revisions and cross-site writes are rejected.

| Method | Path | Contract |
| --- | --- | --- |
| `GET` | `/api/presence/preferences` | Query `session_id`; returns `preferences` and selected `provider` identity/approval, never credentials. |
| `PATCH` | `/api/presence/preferences` | JSON scope + current `revision` + `settings`; returns saved preferences/provider. |
| `GET` | `/api/presence/inbox` | Query `session_id`; returns current `scope`, bounded `items`, recent `jobs`, and simulated `expression`. |
| `GET` | `/api/presence/notices` | For the browser's own notifications. Query `after` (seconds since 1970) lists the check-ins, across everyone, that arrived after it and are still unread, current and unexpired: `{id, avatar_id, session_id, kind, name, created_at}`, without their text unless `text=1`. Never a kids pal's. Without `after` it lists nothing and returns only `now`, so a browser starts watching from then. Reads only; other arguments are 400. Refused in the kids view. See [Notifications](PRESENCE.md#notifications). |
| `POST` | `/api/presence/inbox/<item_id>/<action>` | Scope + action `read`, `dismiss` or `accept`; returns `item`. Accept saves original text once and returns `accepted_message_id`. Accepting stale/expired items returns 410; they can still be dismissed. |
| `POST` | `/api/presence/check-now` | Scope + stable `request_id` (8-100 ASCII letters/digits/underscore/hyphen); returns `job`, 202. Queues only; scheduler owns generation. |
| `GET` | `/api/presence/rituals` | Query `session_id`; returns `rituals`. |
| `POST` | `/api/presence/rituals` | Scope + `kind`, `minute`, optional `weekday`, `enabled`; returns `ritual`, 201. |
| `PUT`, `DELETE` | `/api/presence/rituals/<ritual_id>` | Scope + current ritual `revision`; PUT accepts the editable ritual fields, DELETE retires it. |

Settings: `enabled`/`quiet_enabled` booleans, IANA `timezone`, `quiet_start`/`quiet_end` integer minutes 0-1439, `interval_minutes`/`cooldown_minutes`/`expiry_minutes` integers 15-1440, `daily_cap` integer 0-5, `provider_approval` null or the exact displayed fingerprint. External providers need that approval to enable or generate. Turning settings off invalidates outstanding work. Main opt-in is off by default and does not inherit legacy proactive settings.

Ritual kinds are `greeting`, `goodnight`, `weekly`; `minute` is 0-1439 and weekly `weekday` is Monday=0 through Sunday=6. Ritual edits invalidate previous work. Inbox state is `available`, `accepted`, `dismissed`, `expired` or `stale`; unavailable text is suppressed. See [Presence](PRESENCE.md) for scheduling, expiry, source checks, restore and expression limits.

### Episodes and Our story

Source: [episodes.py](../core/routes/episodes.py), [episode service](../core/episodes.py). Responses are private `no-store` JSON. Reads perform no model work and spend no recall budget. Writes use a frozen scope: positive integer `session_id`, current `dataset_epoch`, and exact `avatar_id` (an empty string for the default companion). A changed session owner, stale revision or unsupported field is rejected. JSON requests are limited to 16 KiB; duplicate fields and nonfinite JSON numbers are rejected.

| Method | Path | Contract |
| --- | --- | --- |
| `GET` | `/api/episodes` | Query one `session_id`; returns `scope`, newest 200 retained `episodes`, `recall_after`, `repair_pending` and `recall_budget`. Forgotten rows are omitted; retained older-dataset rows are stale. |
| `GET` | `/api/episodes/sources` | Query exactly one `session_id`; returns `scope`, bounded recent source choices, `recall_after` and `repair_pending`, without model work. Unknown/duplicate query fields are rejected. |
| `POST` | `/api/episodes` | Frozen scope plus stable `request_id`, `kind`, `title`, `sources`, optional `summary`, `uncertainty`, `salience`, `in_story`; returns `scope` and `episode`, 201. Matching request retries preserve edits/tombstones; different input with the same ID conflicts. |
| `PATCH` | `/api/episodes/<episode_id>` | Frozen scope plus current `revision` and supported changes: `title`, `summary`, `uncertainty`, `kind`, `salience`, boolean `pinned`/`in_story`; returns `scope` and `episode`. Current sources are required. |
| `DELETE` | `/api/episodes/<episode_id>` | Frozen scope plus reviewed `revision` and `confirm: "forget"`; clears the episode text/source copies, advances the owner's recall boundary, and clears derived notebook copies. Repeating a completed Forget does not advance the boundary again. |
| `GET` | `/api/episodes/recall` | Query `session_id` and optional `query` (at most 4,000 characters); returns read-only candidate `episodes`, `scope`, `budget` and evidence instructions. Preview never reserves or spends a turn's budget. |
| `GET` | `/api/episodes/<episode_id>/restore-review` | Query `session_id`, current `dataset_epoch`, `avatar_id` and retained `revision`; returns source-validated saved curation, bounded original excerpts and exact `reviewed_sources`. This does not reactivate the episode. |
| `POST` | `/api/episodes/<episode_id>/revalidate` | Frozen scope plus retained `revision`, the exact reviewed `reviewed_sources`, and `confirm: "restore-curation"`; returns current `scope`/`episode` only if original evidence, ownership, recall floor and knowledge revision still match. Matching retries are idempotent; a conflicting current-dataset capture returns 409 without replacing either curation. |

Episode IDs use `ep_` plus 32 lowercase hexadecimal characters. Capture `request_id` is 8–80 ASCII letters/digits/underscore/hyphen. Kinds are `actual_exchange`, `imagined_scene` and `derived_summary`; title is nonempty and at most 160 characters, summary at most 1,200, uncertainty at most 400, salience 1–5 (default 3), and `in_story` defaults false. `actual_exchange` means those words were exchanged, not that their contents describe a verified real-world event. Imagined scenes retain that label in recall. Derived summaries remain interpretations with explicit uncertainty.

The source picker examines the newest 60 current-conversation user/assistant messages after the recall floor, then excludes blocked or generated accepted-presence text. Dirty knowledge returns no choices. Each choice is `{message_id, session_id, role, preview, timestamp}`; `preview` is at most 120 Unicode code points and the timestamp is as saved. Choices are newest first. This endpoint supplies a picker, not reviewed evidence: inspect the selected current source text and save its exact hash/range when capturing. See [Our story](OUR_STORY.md) for the review flow.

Choose one or two distinct source messages with `sources: [{message_id, start?, end?, session_id?, role?, sha256?}]`. Ranges use zero-based Unicode character offsets, inclusive `start` and exclusive `end`, at most 2,000 characters each and 4,000 total. Missing ranges select the first bounded slice. `sha256` hashes the entire original UTF-8 message, not just its excerpt; supplying the reviewed hash/role/session detects changed inputs. Stored source objects include original IDs/hashes/ranges, current source status and a bounded `excerpt`. Current integrity proves attribution to saved text, never factual truth. Generated accepted presence text cannot be fresh episode evidence.

Public episode fields include `id`, `avatar_id`, `session_id`, `dataset_epoch`, `revision`, `kind`, `label`, `title`, `summary`, `uncertainty`, nullable extraction `confidence`, `salience`, `pinned`, `in_story`, `curation`, `status`, `reason`, `eligible`, `sources`, `ranking` and timestamps. `status` is `current`, `stale` or `forgotten`; stale content/excerpts are suppressed. Curation distinguishes manual saved/curated/corrected entries from automatic `reflection` candidates. Pin/salience changes alone never approve an automatic reflection candidate for recall; an explicit content correction or inclusion in Our story does. Confidence and retrieval priority are separate.

Automatic recall selects at most two relevant episodes within a shared 1,800-character contribution, at most twice per UTC day, with four completed exchanges between uses. A durable turn reserves its companion's slot while writing, then spends budget only after saving the completed user/assistant pair; failed or interrupted work releases its reservation. A retry reconstructs its exact reserved episode IDs/order, rather than reranking newer captures. An unavailable budget, dirty sources or changed evidence produces no episodic recall. Restore retains saved curation as stale; review/revalidation validates retained labels, sizes, priority/confidence and content safety as well as current sources. It cannot cross a later Forget boundary or repair changed/missing evidence.

### Scheduled reflection and derived notebooks

Source: [reflection.py](../core/routes/reflection.py), [reflection service](../core/reflection.py). Reflection has a separate off-by-default opt-in and external-provider approval. Presence or legacy proactive consent does not enable it. Responses are `no-store`; GETs never queue or run inference. Writes require the same frozen `session_id`, `avatar_id`, `dataset_epoch` scope as episodes and reject unknown fields; request JSON is limited to 16 KiB and rejects duplicate fields at any nesting level and nonfinite numbers.

| Method | Path | Contract |
| --- | --- | --- |
| `GET` | `/api/reflection/preferences` | Query exactly one `session_id`; returns `preferences` and selected `provider` identity/approval without credentials. |
| `PATCH` | `/api/reflection/preferences` | Frozen scope plus current preference `revision` and `settings`; returns saved preferences/provider. |
| `POST` | `/api/reflection/run` | Frozen scope plus stable `request_id` (8–100 ASCII letters/digits/underscore/hyphen); returns `job`, 202. Requires reflection enabled; queues only. Retry the same ID after a lost response. |
| `GET` | `/api/reflection/reports` | Query exactly one `session_id`; returns `scope`, newest 30 `jobs`/`reports` and validated `current_notebook`. No background polling is required to retain results. |
| `PATCH` | `/api/reflection/notebook/<identifier>` | Frozen scope plus current entry `revision` and `text`, `uncertainty` and/or boolean `dismiss`; returns `entry`. Nonempty text is at most 1,200 characters; uncertainty at most 400. Active replies/knowledge mutations must finish first. Stale entries can be dismissed but cannot be restored by editing. |

Settings are boolean `enabled`/`quiet_enabled`, IANA `timezone`, quiet minute bounds `quiet_start`/`quiet_end` (0–1439), interval/cooldown minutes (60–10,080), `daily_cap` (0–2), expiry minutes (15–1,440), and `provider_approval` (null or the displayed reflection fingerprint). Defaults are off, UTC, quiet 22:00–08:00, 1,440-minute interval/cooldown, one attempt/day and 120-minute expiry. External approval binds the exact selected provider, endpoint, model and credential identity specifically to reflection. Changed connection details require fresh approval. Disabling cancels outstanding work without rewriting saved manual notebooks.

The scheduler catches up only the newest unexpired occurrence. From the newest 60 conversation messages, it uses at most the latest eight eligible original user messages, each a precise first-1,000-character range with its whole-message SHA-256 and saved timestamp. Assistant replies, accepted presence, old reflection, episode summaries and manually edited notebooks are never independent reflection evidence. One global background inference slot is shared with presence: one 15-second, 1,200-token SDK attempt, no retries, tools, photos, TTS or fallback provider. Failed attempts count against cooldown/daily limits. Idle scheduling holds no writer lease; planning, generation and publication coordinate with backup/restore. One server process per instance is supported.

A successful run atomically saves a visible report, up to two derived episode candidates and up to two source-tracked addenda in a separate SQLite current-notebook layer. It updates the companion's derived current notes automatically; the existing `about_you.md`/`our_story.md` bytes and their user edits remain separate. The prompt labels this layer as derived, preserves uncertainty and excludes stale sources. Repeated source proposals retain user edits and dismissals. A report's compatibility field `notebook_proposals` resolves the current linked derived addenda, including subsequent edits/dismissals; it is not an immutable copy of the provider's original text or an unapplied rewrite of the manual notebook.

`current_notebook` contains `about_you`, `our_story`, `entries` and `derived: true`. Entries include `id`, `job_id`, frozen scope, `note`, `revision`, `text`, `uncertainty`, extraction `confidence`, `state`, `user_edited`, source ID/hash/range/status objects and timestamps. Reads validate the newest 40 current addenda and cap each note's prompt text at 4,000 characters; older records remain stored. Reports expose `id`, `job_id`, `state`, `summary`, `created_at`, `sources`, `episode_ids` and `notebook_proposals`. Job states are `pending`, `running`, `published`, `failed`, `stale`, `expired`; job responses omit private snapshots/process identities. Restores pause scheduling and invalidate generated layers; explicitly review retained episode curation or enable a fresh reflection run against eligible originals. No reactivation bypasses Forget, owner, epoch, hash or recall-floor checks.

## Local add-ons

| Method | Path | What it does |
| --- | --- | --- |
| `GET`, `POST`, `PUT`, `DELETE` | `/addons/<name>/<path:resource>` | Dispatch a route registered by an installed add-on at startup. Requests use the regular Altera access and write guards. Unregistered resources return 404. |
