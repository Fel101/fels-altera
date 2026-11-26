# Altera storage and schema reference

Reviewed against `core/datastore.py`, `memory.py`, `knowledge.py`, `notes.py`, `episodes.py`, `reflection.py`, `backups.py`, `backup_crypto.py` and browser code on 2026-10-28. Paths below are relative to the instance directory: the checkout by default, or `ALTERA_HOME` set before imports. Live database/vector/files remain plaintext; new backup API requests default to authenticated encrypted archives. Use [Backup and restore](BACKUP_AND_RESTORE.md) before manual repair.

## SQLite: `data/conversations.db`

`DataStore` owns ordinary persistence. `init_db()` selects persistent SQLite WAL mode before creating/publishing the current schema and adding missing legacy columns; shared runtime startup prepares FTS and reply recovery. Ordinary query connections inherit that mode instead of attempting a journal transition during another reader/writer's transaction. [SQLite's WAL documentation](https://sqlite.org/wal.html#persistence_of_wal_mode) explains its persistence. This does not eliminate all SQLite busy/lock conditions or authorize external writers. Declared references document relationships; application validation and explicit deletion/mutation routines maintain them.

### `sessions`

| Column | Type/default | Purpose |
| --- | --- | --- |
| `id` | INTEGER primary key, autoincrement | Stable conversation ID |
| `name` | TEXT, `New Conversation` | Display name |
| `tags` | TEXT, empty | JSON list when set through the API |
| `archived` | BOOLEAN, 0 | Hidden from the normal active list |
| `avatar_id` | TEXT, empty | Owning companion; empty means default/legacy |
| `created_at`, `updated_at` | TIMESTAMP, current time | Lifecycle timestamps |

`idx_sessions_avatar` indexes companion ownership. Companions normally open one continuous primary chat; the session APIs also retain legacy and split conversations.

### `messages`

| Column | Type/default | Purpose |
| --- | --- | --- |
| `id` | INTEGER primary key, autoincrement | Source identity retained through split/merge |
| `session_id` | INTEGER, reference to sessions | Conversation owner |
| `role` | TEXT, required | `user`, `assistant` or `system` |
| `content` | TEXT, required | Visible message text |
| `mood` | TEXT, nullable | Serialized mood data when analyzed |
| `timestamp` | TIMESTAMP, current time | Message time |

`idx_messages_session(session_id, id)` supports ordered history. API edits/deletes and answer replacement retire replay copies and invalidate derived companion knowledge. Partial failed/stopped answers are request records rather than confirmed assistant rows.

### `message_versions` and `message_version_current`

Regenerating an answer keeps the text it replaces. The message row keeps its id (memories, voice notes, episodes and the reply ledger point at it) and always holds the text the person is looking at; every other answer it has had is a row in `message_versions` (`message_id`, `version_id`, `content`, `created_at`; primary key `(message_id, version_id)`, so version ids belong to their message and one message's versions can never overwrite another's). `message_version_current(message_id PRIMARY KEY, version_id)` names the version the row is showing, and is NULL when the message's own text is showing (the usual case, and after a hand edit). Showing a version moves the pointer and marks companion knowledge for review/rebuild. Editing or deleting a message by hand drops its earlier answers: a correction is not another answer.

### Durable reply tables

`voice_notes` is an additive application-owned table with `note_id` primary key and unique immutable `request_id`. It stores session/companion/dataset identity, source revision/recall floor, upload hash/bytes/format/duration, transcript and reviewed revisions, job state/process identity, linked user/assistant IDs, retained reply-audio metadata, errors and timestamps. States are `upload`, `transcribing`, `review`, `ready`, `sending`, `sent`, `failed`, `stale`. Session and owner indexes support retrieval. Deletion leaves a redacted tombstone; source invalidation never converts it back into a visible note. Session/global deletion removes those rows explicitly.

Input and retained reply audio live under `data/voice_notes/vn_<32 hex>/`; interrupted deletion uses a contained discoverable staging directory for retry. Browser originals use the separate `her-voice-notes` IndexedDB database, bounded to ten notes/100 MiB. Voice activity frames are never persisted. See [Voice](VOICE.md) for plaintext retention, source boundaries and backup scope.

`chat_turns` stores one row per stable `request_id`:

| Column | Type | Purpose |
| --- | --- | --- |
| `request_id` | TEXT primary key | Idempotency/reconnect identity |
| `session_id` | INTEGER, required | Conversation |
| `fingerprint` | TEXT, required | Input identity; not sent to browser |
| `process_id` | TEXT, required | Worker ownership; not sent to browser |
| `status` | TEXT, required | `sending`, `streaming`, `saved`, `failed` or `interrupted` |
| `response`, `error` | TEXT, default empty | Current visible response and failure notice |
| `user_message_id` | INTEGER, nullable | Original user row |
| `assistant_message_id` | INTEGER, nullable | Confirmed saved assistant row |
| `target_message_id` | INTEGER, nullable | Replacement target for regenerate |
| `first_text_ms`, `elapsed_ms` | INTEGER, nullable | Wall-clock timing |
| `diagnostics` | TEXT, default `{}` | JSON per-call usage, timings/context estimates |
| `expression_pending` | TEXT, nullable | Internal source hashes, owner, boundary and save time for recovering a saved expression; omitted from request-status responses |
| `memory_pending` | TEXT, nullable | Internal selected memory IDs and source hashes for retrying saved-reply recency; omitted from request-status responses |
| `created_at`, `updated_at` | TIMESTAMP, current time | Request timestamps |

The partial unique index `idx_one_active_turn` prevents two rows in `sending`/`streaming` for the same session. Startup reconciles unfinished rows owned by an old process, recognizes a matching saved answer, and otherwise marks the reply interrupted. Eligible saved-expression and ordinary-mode memory intents are consumed only after source and context checks. Restore clears those intents.

`chat_turn_events(id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL, event TEXT NOT NULL)` stores replayable event JSON; `idx_turn_events(request_id,id)` provides event order.

`chat_request_tombstones(request_id TEXT PRIMARY KEY, deleted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)` remembers retired IDs without keeping deleted prompt/reply copies. Stale retries cannot recreate deleted or moved data.

Shared-picture conversation markers use a `photo:shared_...` tombstone in this existing table. The marker and tombstone are inserted together after checking the original session, companion and dataset epoch. A retry cannot recreate a deleted or edited marker; no reference bytes or provider credentials are stored in this table.

### `life_events`

Columns are `id` (autoincrement primary key), `session_id`, `event_type` (required TEXT), `detail` (required TEXT), `confidence` (REAL, 0.7), `timestamp` (current time) and `raw_text`. Types include birthday, achievement, trip, work, relationship, health, holiday and general milestone. These are extracted summaries, not a structured calendar.

### `user_profile` key/value store

Columns are `key TEXT PRIMARY KEY`, `value TEXT` and `updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP`. Most values are serialized JSON. Legacy feature flags may use JSON booleans. This table is distinct from `user_profile.yaml`.

| Key/family | Content |
| --- | --- |
| `relationship_graph` | Legacy entity graph and relationship scores |
| `communication_style` | Analyzed tone/formality/preferences |
| `user_opinions`, `humor_style`, `jokes_and_references` | Legacy conversation callbacks |
| `follow_ups` | Pending/completed follow-up items |
| `custom_personality_templates` | User-created template definitions |
| `onboarding_complete`, `last_active_session` | Onboarding/navigation state |
| `dataset_epoch` | `{value, required}` to reject stale tabs after restore or successful global clear |
| `knowledge_recall:<owner>` | `through` message-ID boundary and reset metadata |
| `knowledge_dirty_owner:<owner>` | Companion-wide derived-knowledge pause |
| `knowledge_dirty:<session_id>` | Session invalidation state |

The default knowledge owner in internal keys is `@default`; API paths use reserved `_default`. Other service-specific preferences/state also use this key/value store; the table is not limited to this inventory.

### FTS5

`messages_fts` has content, session ID, role, mood and timestamp columns, with `content='messages'` and `content_rowid='id'`. Insert/update/delete triggers mirror message mutations into its external-content index. `fts_state(name TEXT PRIMARY KEY, built_at TIMESTAMP)` records completed backfill. Startup and restore can rebuild the index from canonical messages.

Search quotes literal input words instead of accepting arbitrary FTS query syntax. It is lexical conversation search. ChromaDB supplies semantic memory recall; it does not currently supply semantic conversation search. A legacy `memories_fts` table is not part of the current schema.

## ChromaDB: `data/chroma_db/`

`MemoryStore` opens a persistent client lazily with anonymized telemetry disabled and serializes initial client and collection creation across callers. The `altera_memories` collection uses cosine distance and Altera's local-only adapter for Chroma's default embedding identity. `memory.embedding_model_path` can select existing compatible model assets; Altera does not download or repair them during embedding. The vector directory is `data/chroma_db`, not a configurable `memory.chroma_path`. Existing collection compatibility matters when restoring saved embeddings. See [cached local embeddings](LOCAL_EMBEDDINGS.md) for asset requirements and native acceptance limits.

A document contains memory text and has ID `mem_<uuid hex>`. Metadata uses Chroma-compatible scalar values:

| Field | Meaning |
| --- | --- |
| `id`, `type`, `confidence` | Identity, category and confidence |
| `avatar_id` | Companion ownership, empty for default/legacy |
| `session_id` | Source conversation when known |
| `user_context` | For example automatic extraction, manual assertion or `manual_corrected` |
| `source_refs` | JSON string containing `{message_id, session_id, sha256}` references |
| `created_at`, `updated_at` | ISO timestamps |
| `salience`, `pinned` | Retrieval priority (integer 1–5, default 3) and explicit pin (boolean, default false), independent of extraction confidence |
| `priority_revision`, `priority_updated_at` | Optimistic priority-edit revision and latest explicit priority-edit timestamp |
| `salience_anchor_at` | Aware timestamp used as a known age anchor |
| `last_recalled_at`, `last_recall_message_id`, `recall_count` | Recency/count receipt from a successfully saved completed exchange, deduplicated by assistant message ID |

Higher-level knowledge controls validate the source hash/current owner before recall. Legacy untracked, changed or missing-source entries can remain visible for review without being recalled. Malformed references are excluded. Manual corrections do not retain false attribution to the old source. Correction/forgetting sets a recall boundary and clears derived notebooks/history. See [Memory controls](MEMORY_CONTROLS.md).

Priority decay changes retrieval ordering only: 30-day half-life, minimum factor 0.2, and no age decay for pins. Unknown/naive legacy timestamps remain neutral until a known aware anchor is available. Pins never repair provenance or raise extraction confidence, and no priority calculation deletes a memory. The bounded ranking receipt is exposed in the scoped inventory/report.

Backups use a logical snapshot of IDs, documents, metadata and embeddings plus the embedding-function identity. Restoring that snapshot does not run an embedding model; it validates dimensions/values and requires a compatible function. Raw Chroma files are excluded from the archive.

## Instance files

| Path | Content |
| --- | --- |
| `config.yaml` | Shared application settings and provider overrides |
| `config.local.yaml`, `.env` | Machine settings/credentials; keep private |
| `personality.yaml` | Default persona definition |
| `user_profile.yaml` | Name, hobbies, preferences, facts, recent moods and saved UI/service preferences |
| `data/addons/<name>/` | Data directory for an installed add-on |
| `data/secret_key` | Persistent session signing key |
| `data/avatars/<id>/` | Avatar card, portrait/body, living frames, companion state |
| `data/avatars/<id>/notes/` | `about_you.md`, `our_story.md`, state/provenance and up to eight older versions per note |
| `data/photos/` | Generated/imported pictures, metadata and synchronization receipts |
| `data/companions.json` | Legacy-companion migration receipt |
| `data/conversations.before-companions.db` | Pre-companion-migration database copy when migration ran |
| `data/offline_queue.json` | Legacy server-side request queue |
| `data/cache/tts/` | Generated WAV/MP3 speech cache; old files cleaned after seven days on speech use |
| `data/tmp/` | Confined multipart upload spooling, STT recordings and TTS intermediate files; owned files are cleaned on ordinary exit, excluded from backups |
| `data/altera.log`, `.1`–`.3` | Rotating private logs, 5 MiB per file |
| `workspace/` | Text files written by the optional workspace tools |
| `backups/` | `.alterabak` authenticated encrypted archives or explicitly requested plaintext ZIPs, private restore staging/results and any recovery journal |

Speech keeps the route `/static/audio/tts/<filename>` for compatibility, but files are stored under the instance's `data/cache/tts/` and served with `Cache-Control: no-store`. Older source-tree speech caches are not automatically migrated into backups.

Configuration reads merge the main and local files, then supported API-key environment overrides. Writes are atomic UTF-8 replacements. The portable [config.example.yaml](../config.example.yaml) is the settings reference; live configuration can differ. Missing-key code fallbacks are not necessarily the same as starter values.

## Browser storage

The browser's IndexedDB database is `her-offline-queue`, version 3, with `pending-messages`. Entries include local ID, `session_id`, `request_id`, `dataset_epoch`, message text, timestamp, status and retry count. `pending`, `sent` and `needs_review` states support safe retry and explicit draft review. This is private browser data and is not shared between devices.

The UI also uses localStorage for appearance/settings and sessionStorage for pending durable request identities. Browser backup capture includes an allowlisted set of preferences/drafts from that browser only. Restore does not automatically send imported drafts.

The service worker caches static application assets. Private page HTML, APIs, pictures and generated audio are excluded; browser drafts are not a complete offline application or backup.

## Schema evolution and recovery

Current startup adds legacy session fields (`archived`, `tags`, `avatar_id`) and reply fields (`target_message_id`, `diagnostics`, `expression_pending`, `memory_pending`) if absent, creates tables/indexes/triggers and prepares FTS. There is no general user-facing database migration CLI. Do not edit the SQLite file or raw Chroma files while Altera is running.

The inner cross-version data artifact is `altera-backup`, version 1, with validated restore and recovery. The optional authenticated outer envelope is `altera-encrypted-backup`, version 1 (backups from when the app was called Her use `her-backup` and `her-encrypted-backup` and still restore); new backup API calls choose it by default. Readable JSON/Markdown/PDF exports remain separate plaintext reports; full JSON report import is not implemented. Snapshot coordination assumes a single server process and no external writers. See [Backup and restore](BACKUP_AND_RESTORE.md) for limits and fail-closed recovery.

## Presence and simulated expression (October 28 update)

All five tables are additive application-owned SQLite schema; ordinary snapshots include them. Restore disables preferences with `needs_review`, invalidates jobs/inbox and clears expression. Source changes invalidate jobs and items; acceptance linkage remains so moved stale messages can be filtered from prompts. The service is single-process and assumes no external database writers.

| Table | Stored state |
| --- | --- |
| `presence_preferences` | Companion `avatar_id` primary key, bound `session_id`/`dataset_epoch`, optimistic `revision`, enabled flag, settings JSON, review flag, next due/updated UTC epoch seconds. |
| `presence_rituals` | Stable ID, owner/session/epoch, revision, greeting/goodnight/weekly kind, minute/weekday, enabled/deleted flags and creation UTC seconds. |
| `presence_jobs` | Deterministic occurrence ID, owner/session/epoch, kind/ritual/occurrence, due/expiry UTC seconds, pending/running/published/failed/stale/expired state, process/claim token, attempts, captured source/context snapshot JSON, safe error and timestamps. |
| `presence_inbox` | Stable item ID and unique job ID, owner/session/epoch, generated text, kind/creation/expiry, source snapshot, invalidated flag/error, separate read/dismiss/accept times, unique accepted message ID. |
| `companion_expression` | Owner primary key, source session/dataset, simulated state/intensity/time, user and assistant IDs/hashes, captured recall floor and knowledge revision. No copied message text. |

Job snapshots contain source IDs/hashes, ownership/dataset identity, revisions, recall floor and fingerprints. They contain no API keys. Network approval is a fingerprint of the exact selected connection, including credential identity. Conversation deletion scrubs generated text and snapshots while preserving minimal stale-acceptance tombstones. Companion removal also clears its preference/ritual activation. Global data clear removes all rows. Browser presence polling retains no durable message queue; it only retrieves the server inbox. Existing browser voice-note/offline-message retention remains separate.

Expression intensity halves every six hours on reads. Malformed, future, dirty, forgotten or changed-source state returns calm. The living portrait consumes a validated read-only cue and gives speech/temporary expressions priority. See [Presence](PRESENCE.md).

## Episodes, recall and reflection (phase 5)

These seven additive SQLite tables are prepared by `DataStore.init_db()` and included in logical backups. Service schema helpers preserve a caller's existing transaction. Their JSON source references use whole-message UTF-8 `sha256` plus zero-based Unicode `start`/`end` offsets (inclusive/exclusive). IDs, hashes and ranges establish attribution and integrity, not claim truth. Derived text is never an independent source.

### `companion_episodes`

| Column(s) | Type/default | Purpose |
| --- | --- | --- |
| `id` | TEXT primary key | `ep_` plus 32 hex characters |
| `owner`, `session_id`, `dataset_epoch` | TEXT, INTEGER, TEXT; required | Companion, source conversation and dataset identity |
| `revision` | INTEGER, 1 | Optimistic curation revision |
| `kind` | TEXT, required | `actual_exchange`, `imagined_scene`, `derived_summary` |
| `title`, `summary`, `uncertainty` | TEXT; title required, others empty | Reviewed/generated curation and uncertainty |
| `confidence` | REAL, nullable | Reflection extraction confidence; distinct from truth and priority |
| `salience`, `pinned`, `in_story` | INTEGER, defaults 3/0/0 | Priority, explicit pin and explicit Our story inclusion |
| `curation` | TEXT, required | `manual_saved`, `manual_curated`, `manual_corrected` or `reflection` |
| `status`, `reason` | TEXT, defaults `current`/empty | Stored `current`/`stale`/`forgotten` state and invalidation reason |
| `source_refs` | TEXT, required | JSON array of one/two `{message_id,session_id,role,sha256,start,end}` original references; excerpts are resolved on reads |
| `recall_floor`, `knowledge_revision` | INTEGER, required | Captured recall boundary and source revision |
| `capture_key`, `capture_fingerprint` | TEXT, required | Idempotent capture/evidence identity and original input fingerprint |
| `created_at`, `updated_at` | TEXT, required | Aware ISO timestamps |

`UNIQUE(owner,dataset_epoch,capture_key)` preserves retry identity; `idx_episodes_owner(owner,created_at)` supports bounded owner inventory. Automatic reflection candidate keys follow their cited original evidence across runs, preserving the first generated candidate, subsequent user edits and Forget tombstones. A current `reflection` candidate is source-valid but excluded from episodic recall until deliberate correction/curation; pin/salience changes alone do not approve it. A source-checked response can report stale even when the stored status remains current.

The read-only episode source picker adds no table or copied text. It examines the newest 60 user/assistant rows in the selected conversation after the recall floor, applies the current eligibility/safety filter, and returns IDs/roles/saved timestamps with at most 120 Unicode code points of preview. Paused knowledge supplies no choices. Full excerpts and whole-message hashes are checked again for deliberate capture.

### Episodic recall coordination

| Table | Columns and constraints |
| --- | --- |
| `episode_recall_reservations` | `request_id TEXT PRIMARY KEY`; required `owner TEXT`, `session_id INTEGER`, `dataset_epoch TEXT`, `process_id TEXT`, `episode_ids TEXT` (JSON) and `snapshot_sha256 TEXT`; `UNIQUE(owner,dataset_epoch)`. Reserves one companion slot for a durable current-process turn without spending budget. |
| `episode_recall_turns` | `user_message_id INTEGER PRIMARY KEY`, `assistant_message_id INTEGER NOT NULL UNIQUE`, required `owner TEXT`, `dataset_epoch TEXT`, `episode_ids TEXT` (JSON, including `[]`) and `used_at TEXT` (aware ISO). Records completed saved exchanges and actual budget use. |

`idx_episode_recall_owner(owner,dataset_epoch,used_at)` supports the budget. Recall uses at most two episodes/1,800 characters, at most twice per UTC day, with four other completed exchanges between uses. A retry reconstructs exactly the reserved IDs and order after snapshot validation, without reranking newer captures. A pending reservation is released after failed/interrupted work; startup reconciles it with the durable turn's process/session/owner/dataset/status. Preview reads do not reserve or spend. These rows coordinate the supported single server's threads; they are not distributed locks.

### `reflection_preferences`

`avatar_id TEXT PRIMARY KEY`; required `session_id INTEGER`, `dataset_epoch TEXT`, `revision INTEGER`, `enabled INTEGER DEFAULT 0`, `settings TEXT` (JSON), `needs_review INTEGER DEFAULT 0`, `next_due_at REAL` and `updated_at REAL`. Times are UTC epoch seconds. Reflection is a separate off-by-default opt-in. Settings store IANA quiet hours, interval/cooldown, expiry/cap and a reflection-purpose fingerprint of the exact approved external provider/model/endpoint/credential identity. Credentials themselves are never stored here.

### `reflection_jobs`

`id TEXT PRIMARY KEY`; required `avatar_id TEXT`, `session_id INTEGER`, `dataset_epoch TEXT`, `occurrence TEXT`, `due_at REAL`, `expires_at REAL`, `state TEXT`, `snapshot TEXT`, `created_at REAL`, `updated_at REAL`; `process_id TEXT DEFAULT ''`, `claim_token TEXT DEFAULT ''`, `attempts INTEGER DEFAULT 0`, `error TEXT DEFAULT ''`, and nullable `started_at REAL`. `idx_reflection_jobs_due(state,due_at)` supports claims. States are `pending`, `running`, `published`, `failed`, `stale`, `expired`. The deterministic occurrence/request identity prevents duplicate work; a recovered uncertain running claim fails instead of automatically retrying inference.

Snapshots contain owner/session/epoch, preference/knowledge revisions, recall floor, source IDs/hashes/ranges and context/provider fingerprints. They contain no provider credentials or copied original conversation text. Failed attempts consume cooldown/daily caps. Idle scheduling holds no lease; planning/claimed generation coordinate with maintenance and share the presence inference slot. Catch-up plans only the newest unexpired occurrence.

### `reflection_reports`

`id TEXT PRIMARY KEY`, `job_id TEXT NOT NULL UNIQUE`, required `avatar_id TEXT`, `session_id INTEGER`, `dataset_epoch TEXT`, `summary TEXT`, `snapshot TEXT`, `episode_ids TEXT` (JSON), `notebook_ids TEXT` (JSON), `created_at REAL`, and `invalidated INTEGER DEFAULT 0`. A successful report, its derived episode candidates and its derived notebook addenda publish atomically in one SQLite transaction after current source/consent/expiry checks. Reports expose safe scope/status/provenance and result IDs, not private process or provider details. Notebook links resolve their current edited/dismissed rows; this table does not preserve immutable original provider-output text.

### `reflection_notebook`

`id TEXT PRIMARY KEY`; required `avatar_id TEXT`, `session_id INTEGER`, `dataset_epoch TEXT`, `job_id TEXT`, `note TEXT` (`about_you`/`our_story`), `text TEXT`, `uncertainty TEXT`, `confidence REAL`, `sources TEXT` (JSON original user references), `snapshot TEXT`, `created_at REAL`, `updated_at REAL`; `revision INTEGER DEFAULT 1`, `user_edited INTEGER DEFAULT 0`, `state TEXT DEFAULT 'current'`. `idx_reflection_notebook_owner(avatar_id,session_id,state)` supports current-layer reads. State is `current`, `stale` or `dismissed`.

This is an automatically updated current derived layer, separate from `NotesStore`'s editable Markdown files. Generated upserts can replace only their own unedited current addenda; explicit edits and dismissals survive later proposals using the same originals. Corrections use the knowledge mutation guard and reject active replies before writing. Source validation suppresses stale content before prompt/report use. The helper validates the newest 40 current addenda and caps each note's prompt contribution at 4,000 characters; older stored rows are retained. A run selects at most eight original user messages from the latest 60 conversation rows, each an exact first-1,000-character range, and writes at most two addenda. Neither earlier generated output, assistant text, accepted presence nor existing notebook text becomes independent evidence.

### Invalidation, Forget and restored curation

Source edits/deletion, companion/session removal, knowledge repair state and explicit Forget cover episode/reflection stores and outstanding jobs. An episode Forget uses the shared owner recall boundary, clears derived notebook copies and retains only its redacted episode tombstone. Existing chat remains visible, but pre-boundary sources cannot be captured again by reflection. Global clear removes the new rows with the dataset reset.

Restore preserves retained curation/source references as stale, clears recall reservations/accounting, invalidates reflection reports/current addenda, and disables reflection with `needs_review` and a new preference revision. The restored dataset receives a fresh epoch. A read-only episode restore-review validates retained labels, text bounds/content safety, priority/confidence and original hashes/ranges/owner/floor/revision; explicit revalidation repeats those checks and compares the exact reviewed references before updating the episode epoch/status. A current-epoch collision on the unique capture key returns 409 without overwriting newer or older curation. Revalidation cannot bypass Forget or repair changed/missing evidence. Old reflection text is never automatically reactivated; fresh enabled work must derive from currently eligible originals.

Run only one server process per instance and stop its predecessor before replacement. Random incarnation IDs let startup reconcile interrupted jobs, but cannot establish exclusive ownership against another live server or an external database writer.

## Backup envelope and work files (phase 6)

This milestone adds file formats and filesystem lifecycle state, **not new SQLite tables**. The existing logical SQLite snapshot and logical vector snapshot remain inside the validated ZIP payload. Source IDs, episode/reflection provenance, preferences and job ledgers retain their existing schema and restore invalidation rules.

### Authenticated `.alterabak` archives

The outer `altera-encrypted-backup` version-1 header identifies the fixed secretstream XChaCha20-Poly1305 cipher, 1 MiB chunks, plaintext byte count, stream header, password wrapper and optional recovery wrapper. Argon2id uses a fixed 64 MiB/three-operation profile; XChaCha20-Poly1305 wraps a fresh random 32-byte archive key independently for password/recovery access. Each frame authenticates the entire exact envelope prefix, with a mandatory authenticated empty final frame and EOF check. Strict canonical JSON, field/type/version/size validation and complete framing preflight precede the password KDF. See the authoritative [encrypted format specification](ENCRYPTED_BACKUP_FORMAT.md), including exact bytes and test-vector expectations.

The header contains salts, nonces and encrypted key wrappers, but no raw password, raw recovery key, unwrapped archive key, member names or private conversation content. Public metadata includes file size/time, encryption profile, plaintext byte count and recovery-slot presence. New passwords are at least 12 Unicode characters and at most 1,024 UTF-8 bytes, with spaces/Unicode spelling preserved. The optional printable recovery secret is generated only on explicit request and returned once, never persisted in backup receipts or ordinary configuration. Losing both applicable credentials has no server-held recovery bypass.

The inner ZIP cap is 2 GiB; encrypted overhead is at most 51,249 bytes. Expanded data remains limited to 8 GiB and 100,000 files. The codec writes an unpredictable owner-private `.altera-crypto-*.part` on the destination filesystem, then publishes to a previously unused destination only after completion/authentication. Unsafe sources or existing destinations are refused. Normal failure removes its own partial output; filesystem refusal or process termination can leave residue. Decrypted content is temporary plaintext and still requires ordinary manifest/path/schema validation before application data changes. These mechanisms do not encrypt live SQLite, vectors or media.

### Preview, staging and recovery files

| Path | Stored state and retention |
| --- | --- |
| `backups/.staging.lock` | Known regular-file marker plus OS byte/flock lock; per-directory in-process locking also serializes archive operations/cleanup. No password or recovery secret. |
| `backups/.staging/<token>/upload.zip` | Original uploaded/saved archive bytes. The filename is internal; an encrypted `.alterabak` input remains ciphertext in this file. |
| `backups/.staging/<token>/.preview-owner.json` | Noncredential marker: `format: her-preview-stage`, `version: 1`, matching `token`, numeric creation time. Identifies recognized abandoned uploads for bounded expiry. |
| `backups/.staging/<token>/preview.json` | Token, expiry, upload SHA-256, restore-connection flag, validated counts, source creation/version and encrypted flag. Contains no unlock credential. Counts/policy remain private instance metadata. |
| `backups/.work/` | Private snapshot/ZIP, decrypted validation/extraction and restore work. Ordinary temporary work is removed on exit; interrupted restore work is retained for recovery. |
| `backups/.work/restore-<id>/protected-settings.yaml` | Mandatory private mapping preserving this PC's API credentials/access settings during recovery. Sensitive plaintext in the current implementation, outside portable archive contents. |
| `backups/restore-in-progress.json` | Restore work-directory name, recovery archive ID, token and, in every newly written journal, `protected_settings_sha256`. No raw archive credential. |
| `backups/.results/<token>.json` | Completed application receipt for retry-safe restoration; can contain private restored browser preferences/drafts, but never the archive password/key. Retained outside preview TTL cleanup. |

Preview tokens expire after 15 minutes and become eligible for operation-triggered cleanup. Cleanup examines at most 100 staging entries per invocation and advances a per-service scan cursor around preserved unknown entries. Restart resets the cursor; new entries may wait until the next cycle, and no deletion deadline/background timer is promised. It removes only recognized expired token directories with approved regular files and preserves active/recovery tokens. Linked paths, unknown files, malformed owner/receipt metadata, unsafe permissions or uncertain recovery journals are left intact. It does not recursively sweep `.work/` recovery data or completed receipts. Staging/work/results, preview folders and restore subdirectories use owner-only 0700 permissions on POSIX; unsafe existing paths fail instead of being changed automatically. Windows confinement/reparse checks do not claim equivalent verified filesystem encryption or ACL policy.
