# Memory sources and controls

Open **Memory** from chat and choose a companion. The page shows only that companion’s memories. **Default companion / legacy shared memories** is a separate scope. Switching this selector does not switch the companion you are chatting with.

- **Remember** on a chat message opens a fact you can edit before saving. You can also add a fact directly on Memory.
- **Sources** shows the original message and says whether it changed or is missing.
- Automatic memories come from your saved messages, usually after four new messages. Replies, imagined scenes and
  roleplay are left out. If the chat model is unavailable, entries made with a simpler check are marked **phrase fallback**.
- **Correct** lets you replace a fact. Altera marks the new version as your correction.
- **Forget** removes the chosen memory. **Forget all memories for this companion** affects only the selected companion.


Older memories with no tracked source remain visible and are marked for review. They are not supplied as recalled facts until you confirm them through Correct or rebuild automatic memories from the current eligible chat. The app does not invent source links for old entries.

## What is known about you

On **Memory**, select a companion. Under **What is known about you**, choose **Show current report**. The report reads
saved memories, notes and messages directly. It shows where the saved facts came from and which chat messages Altera
can still use in a reply. You can download the same report as JSON or Markdown.

**Eligible** means Altera could use an item in a reply. Which items it uses depends on the message and the amount
of text that fits. **Current** means the source message still matches what Altera recorded. Check the source if
a fact looks wrong. Notes may mix your edits with older summaries, and their sources describe the latest update.

If a report says Altera is busy, wait for the reply, notes or picture to finish, then try again. Downloads contain
private text anyone with the file can read. [How Altera chooses context](ARCHITECTURE.md#context-and-knowledge)

## What correction and forgetting change

Correction and forgetting clear the companion’s notebook, including manual note text and note history. They also set a fresh recall boundary across this companion’s conversations: earlier chat stays visible in the app but is no longer sent to the model. The confirmation explains this before you apply it. Other saved memories remain; still-valid memories retained before a boundary also survive later repairs.


Correction needs the local memory model. If Altera says it is missing, install it and try again.
[Install the memory model](SETUP.md#the-memory-and-microphone-models)

This prevents a forgotten fact from being immediately read back from the old conversation or an older notebook. The source conversation, pictures, other memories, downloaded exports and saved backups are not erased by forgetting one memory. Other copies of the same information can still exist in those places. Default-persona legacy callbacks are cleared or excluded after a recall reset because they have no source links.

Altera can remember new messages normally after the change.

## When conversation text changes

Editing, deleting or replacing a message pauses derived memories and notes for its companion. The chat links to **Review memories**. Choose **Rebuild automatic memories** to replace automatic entries from the current eligible text and clear stale notebook copies. Manually added/corrected memories remain. A manually saved memory whose source changed stays marked for review and excluded from recall until you correct or forget it.

Rebuilding asks the chat model to read the current eligible chat again, in batches, and write the automatic memories afresh. It runs in the background: chatting carries on, and derived memories and notes stay paused until it finishes. If the chat is edited while the model is reading, they stay paused and Memory asks for another rebuild. With the model unreachable it falls back to the local phrase matcher, and those memories are marked as such. Rebuilding never asks the model to rewrite your own added or corrected facts. The notebook is subsequently written afresh through the normal notes workflow. Notebook source details distinguish conversation ranges, user edits and earlier notes whose sources were not recorded.

Wait for any reply or notes update to finish before changing a memory. If a correction or removal fails, review
what remains before retrying. Automatic memories may stay paused for repair.
[Notebook removal checks](SAFETY.md#local-access-and-data)

Conversations belonging to different companions cannot be merged. Splits retain ownership, message IDs and timestamps. Split/merge operations retire old request replay copies and pause derived knowledge for repair.

Proactive check-ins honor the same owner recall boundary. They skip generation when no eligible conversation remains and exclude stale shared mood/follow-up context from scoped companion prompts.

## Priority, episodes and reflection

**Retrieval priority** changes a memory's pin and importance (1–5) separately from its content, source eligibility and confidence. Age lowers ordering with a 30-day half-life and a 20% floor; pins suppress age decay, and no item is deleted by this score. Unknown legacy timestamps without an offset stay neutral. Context inclusion counts reflect completed saved reply inputs, not measured model attention or a guarantee the model mentioned a memory. Failed turns and report reads do not strengthen recency.

**Our story** keeps reviewed, source-tracked episodes with separate labels for textual exchanges, imagined scenes and derived summaries. Optional reflection has separate per-companion consent and adds source-tracked derived notebook entries without overwriting the manual notebooks. The knowledge report includes those current entries and exact original user-message excerpts. New ordinary notebook evidence is user-only; earlier derived or manually edited text retains its provenance limits.

Correction/Forget and source edits invalidate affected episodes, check-ins and reflection work. Explicit Forget clears current reflection notebook copies and reports too; pending job tombstones retain no copied conversation text. A restore retains your story curation and pins as stale material and requires explicit matching-source review before recall. See [Our story and reflection](OUR_STORY.md) for the complete behavior and limits.

## API

All mutation routes use the app’s origin and dataset-epoch checks. Scope IDs are companion IDs; `_default` is reserved for the default persona and cannot collide with a valid avatar ID.

| Method and path | Behavior |
| --- | --- |
| `GET /api/knowledge/scopes` | Companion choices and the active companion. |
| `GET /api/knowledge/messages/<id>` | Current source message and its companion scope. |
| `GET /api/knowledge/scopes/<scope>/memories` | Memories, source statuses, review flags, recall boundary and notebook provenance. |
| `POST …/memories` | Remember `content`, optional `type`, `confidence`, `source_message_id` and `session_id`. |
| `PUT …/memories/<id>` | Correct `content`, with optional `confidence`. |
| `PATCH …/memories/<id>/priority` | Change `pinned` and/or integer `salience` 1–5 with frozen `dataset_epoch`, priority `revision` and `content_sha256`; no recall reset or confidence change. |
| `DELETE …/memories/<id>` | Forget; JSON `confirm: "forget"`. |
| `DELETE …/memories` | Forget the scope; JSON `confirm: "forget-all"`. |
| `POST …/repair` | Rebuild; JSON `confirm: "rebuild"`. Returns `status: "started"` and runs in the background; the inventory's `rebuild` reports `running`, `done`, `changed` (edited meanwhile: rebuild again) or `error`. |
| `GET …/duplicates` | Review this scope’s current, usable duplicate candidates. |
| `POST …/duplicates` | Consolidate; JSON `confirm: "merge"`. User corrections and stale source entries are protected. |

The existing `/api/memory` CRUD routes use the same correction/forgetting behavior. GET defaults to the active companion; `avatar_id=` selects the default scope. Explicit owner reassignment through PUT clears foreign source metadata and resets recall for both affected companions. The older global clear endpoint still requires `confirm: "yes"`; the new page offers scoped clearing instead.
