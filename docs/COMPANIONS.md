# Companions

Every avatar is a **companion**: one avatar, one life, one continuous chat. Meeting someone new means importing another avatar
from NPC Forge, or writing one in Altera itself; the old ones stay, each with the chat, the notes and the memories she made with
you.

Updated 2026-10-28. See [memory controls](MEMORY_CONTROLS.md) for source tracking, correction, forgetting and recall boundaries, and [backup/restore](BACKUP_AND_RESTORE.md) before recovery or data replacement.

## Kinds of people and the start screen

Everyone you can talk to has a **kind**: `companion` (the deep kind this guide describes), `pal` (a light persona) or
`character` (from an imported character card). Chats, voices and pictures work the same for all three. Everyone imported
before kinds existed is a companion: the first time the chat, the start screen or `/api/people` loads, `"kind": "companion"`
is written into each older `avatar.json`, and nothing else in it changes. [Pals](PALS.md) are made on Home, and
[characters](CHARACTERS.md) are imported there from character cards.

**Home** (`/home`, also the sidebar's first link) is where launching with `start.bat` and the installed app open. It shows a
tile for each kind with its count, **Continue** with the person you talked to last, and a gallery of everyone. A tile narrows
the gallery to that kind; pressing it again (or **Show everyone**) shows everyone. Choosing someone opens them in the chat at
`/`. With discreet mode on, the portraits are blurred here too.

## What you see

- **Sidebar:** "Your companions" (a picture, her name, how many messages, how long ago) and **Meet someone new**.
- **Her picture:** on a wide window (1100 px and up) a panel beside the chat shows her picture large, with her name, age, job,
  traits and background. The round picture in the header (and the picture in the panel) opens her full size. On a narrow window
  only the header picture is shown.
- **First meeting:** a companion nobody has spoken to yet wakes up: her picture fades in, she looks at herself once, and she
  speaks first.
- **What she knows:** the notes she keeps about you (see below), readable and editable.

## Meeting someone new

*Make someone new* rolls an adult from NPC Forge's **Modern life** setting. Choose Woman, Man, Non-binary or Any.
Heritage, hair colour and age group can each be left on **Surprise me**. Forge paints the portrait, then Altera imports the
companion. You can cancel while Forge paints. *From my NPC Forge library* imports someone you already made there.
Both paths check the character's stated age, screen for descriptions of minors and real people, and age-check the portrait.

NPC Forge supplies the companion's name, age, looks, background, traits, speech style, demeanor, quirk, dream, pet peeve
and hobbies. If you edit the character in NPC Forge, Altera updates the card the next time you open it and keeps the
imported name, age and gender. Older cards without a speech style or demeanor still use the character's traits.

If NPC Forge is stopped, the Meet window offers **Start NPC Forge**. Rolling a new companion needs NPC Forge API 44 or
later and ComfyUI. It works with Forge's core installation. Altera talks to NPC Forge on this computer.

## Writing a companion without NPC Forge

NPC Forge needs ComfyUI and a capable GPU. **Write a companion** on Home (or **Add someone > Write a companion** in the
sidebar) makes one without it: a name, an age (18 or older), a gender, and the same card fields an import fills — who they
are, hobbies, how they talk, how they come across, a quirk, something they want and a pet peeve. Only the name and age are
needed. The age rule and the safety checks are exactly the ones an import goes through.

The card is stored like any other companion's, so memory, notes, story, presence, voice and the chat work the same from the
first message. The portrait is the initial until you upload a picture (which goes through the age check) or make one in NPC
Forge from the companion's settings; until there is a portrait, there are no pictures to make. A written companion's settings
can change the card later (**Edit who they are…**). A companion imported from NPC Forge keeps its portrait tied to its source
character and does not get the upload or write controls.

### Her first days

A new companion is written as **an adult mind with no past**: she knows her name, her looks and her personality, and nothing about
the world or about you. She is curious, asks a question or two at a time, never guesses or invents facts about you, and gradually
settles into familiarity. For your first `companion.newborn_messages` messages (16) this is part of her prompt; after that the
notes carry the relationship. Source edits, corrections and forgetting can pause or reset recalled knowledge as described below. It is deliberately **not** "a child that knows nothing": Altera's hard rules forbid childlike text, and
the wording says so.

Before her first words the vision model looks at her portrait once and describes what it sees in her own words ("I have dark eyes
and a small, curious smile..."). That description becomes part of who she is (`companion.json > self_view`). If no vision model
answers, she simply goes without.

## What a companion knows

| Where | What | Shared? |
|---|---|---|
| Her card | who she is (from NPC Forge) | no, hers |
| `notes/about_you.md` | facts about you: name, life, people, likes, plans, how you like to be spoken to | no, only what you told **her** |
| `notes/our_story.md` | derived notes about exchanges, jokes, promises and imagined scenes; individual claims are not independently verified | no |
| Her chat | the newest messages that fit (see below) | no |
| ChromaDB memories | sentences she picked up that sounded like facts (each is tagged with her id and only she searches them) | no |

Companion prompts isolate notebook/history/vector recall by owner. Legacy instance-wide dashboards can still show older relationships/events/personality data; they are separate from the companion-scoped memory controls.

### The notes

She writes the two notes herself, with the selected chat model, and includes them in eligible replies, so nothing depends on a search finding
the right memory. **Notes button > What she knows** shows them; correct anything that is wrong and Save (she believes you over her
own notes). They live in `data/avatars/<id>/notes/`, the previous version of each is kept in `notes/history/` (the last 8), and a
bad rewrite by the model is refused: both notes must be present and neither may suddenly shrink to under half (a note that had grown
past its size may). Intimate matters are noted briefly and matter-of-factly.

She updates them
- every `every_messages` (12) messages from you, in the background after a reply ("Writing things down..." shows in the header), and
- when the chat no longer fits comfortably in the model's context (**compaction**).

### The memories

Automatic memories are written by the chat model, not picked out of your sentences. In the background, once
`memory.every_messages` (4) of your messages have not been read yet, it is shown those messages and asked for short statements
that stand on their own ("Is afraid of the sea since a storm on 2026-11-10"), each with a type (fact, preference or event) and a
confidence that reflects how plainly you said it. Relative dates become absolute ones. Only your own words are evidence: her
replies, imagined scenes and roleplay never become facts about you. Each memory keeps links to the messages it came from, so
Correct, Forget, Sources and "included in N reply contexts" keep working.

When the notebook is due, the same background pass writes the notes first and reads the memories at the end of that job, so the
GPU is never asked for two things for one companion at once. A pal's **memory** switch still governs this: notes can be off while
memories are on, and nothing is read for a pal whose memory is off.

If the chat model cannot be reached, or its answer cannot be read, the phrase matcher stands in. What it writes is marked
**phrase fallback** on the Memory page and in the report; it no longer treats "I'm a bit scared..." as a fact about who you are.
The automatic memories already saved stay where they are. After an upgrade the model starts with the messages sent from then
on; it does not go back over the whole history on its own. **Rebuild automatic memories** is the way to have the model read the
eligible chat again.

### Compaction: the chat that grows

She only ever sees the newest messages that fit next to her prompt and her reply (at most `history_messages`, 40). When the prompt
plus a reply would use more than `trigger_ratio` (60 %) of the model's context, she first folds everything into the notes, and then
everything but the last `keep_recent` (6) messages is **left out of her prompt from then on**. Nothing is deleted: the whole chat
stays in the database (Search, Timeline, Export, the chat window). The context length is asked from LM Studio when the model is
loaded (`/api/v0/models`); set `companion.notes.context_tokens` to the actual loaded context length so she tidies up as rarely
as possible. A failed update changes nothing and is retried after three more messages.

New notebook updates use original user messages as fresh evidence. Assistant guesses, generated check-ins and earlier reflection are not independent sources; older notebook text and user edits remain derived material with aggregate provenance limits. Optional [reflection and Our story](OUR_STORY.md) add a separate source-tracked notebook layer and curated episodes. Reflection starts off per companion, preserves manual notebook files, and participates in correction, Forget and restore review.

## Saved pictures and reuse

Altera can reuse an everyday picture from the companion's gallery when it fits the scene you asked for. It prefers a
match that hasn't appeared in the last 30 messages. Finished images in the shared-reference workflow stay with the
conversation and are excluded from automatic reuse. See [Avatars and pictures](AVATARS.md).

## Source changes, correction and forgetting

**Remember** saves a fact with its source when available. **Correct** and **Forget** on Memory set a companion-wide recall boundary and clear both notebooks and their history; earlier visible chat is retained but is not supplied to future prompts. Editing/deleting/replacing source messages pauses derived knowledge until **Rebuild automatic memories**. Manual assertions and valid retained facts are protected. [Memory controls](MEMORY_CONTROLS.md) explains exactly what remains in chats, pictures, exports and backups.

## The move from your old chats

The first time the new version starts, the chats and memories from before companions become the companion you had (the active
avatar): all old chats are merged into **one continuous chat** (the messages keep their order, nothing is deleted; the emptied chats
stay archived), the old memories are given to her, and the database is copied first to `data/conversations.before-companions.db`.
`data/companions.json` records that it happened. Her notes start empty and fill in after the first update ("Update now" does it at
once).

## Removing a companion

Companions and pictures (Settings) > **Remove** deletes her owned chats, notes, memories, pictures and avatar. Both avatar and companion removal routes use the same guarded operation and refuse with 409 while active requests/background work or replies are using the data. NPC Forge's copy is not
touched. "Delete all conversations" clears every chat, memory and note but keeps the companions (each meets you as if for the first
time).

## Configuration (`config.yaml > companion`)

| Key | Default | Meaning |
|---|---|---|
| `newborn_messages` | 16 | messages from you during which she is in her "first days" |
| `notes.enabled` | true | keep notes and compact |
| `notes.context_tokens` | 8192 in the portable starter; 0 in code | assumed context length; 0 asks supported LM Studio metadata (8192 if it cannot say) |
| `notes.trigger_ratio` | 0.6 | tidy up above this share of the context |
| `notes.keep_recent` | 6 | messages kept word for word after a tidy-up |
| `notes.every_messages` | 12 | update the notes after this many new messages from you |
| `notes.max_words` | 450 | size of each note |
| `notes.history_messages` | 40 | never send more than this many messages |
| `memory.every_messages` | 4 | read this many of your new messages for memories in one background pass |
| `memory.batch_messages` | 30 | at most this many messages in one memory call |
| `memory.max_memories` | 8 | at most this many memories from one batch |
| `forge.url` | Automatic discovery | Optional loopback address checked before the suite address file, port 8766, and legacy port 8765 |
| `forge.python` | (none) | the Python that runs NPC Forge when Altera starts it: by default the first compatible configured/candidate environment |

## Endpoints

| | |
|---|---|
| `GET /api/companions` | every companion (name, picture, message count, last message, traits...) |
| `POST /api/companions` | write a companion without NPC Forge: `{name, age, gender?, bio?, hobbies?, speech?, demeanor?, quirk?, goal?, peeve?, voice?}` -> the opened companion (201) |
| `GET` / `PUT /api/companions/<id>` | a written companion's card (name, age, gender, voice, `bio`, `hobbies`, `speech`, `demeanor`, `quirk`, `goal`, `peeve`); 404 for anyone else |
| `POST` / `DELETE /api/avatars/<id>/portrait` | upload an age-checked picture for a written companion / take it away again |
| `GET /api/people` | the start screen: everyone with their `kind`, `counts` per kind and `last` (who to continue with); `?kind=` narrows the list; reads only |
| `GET /api/companions/current` | the one to open the page on (opens her: activates, makes her chat, starts the picture sync) |
| `POST /api/companions/<id>/open` | open a companion |
| `DELETE /api/companions/<id>` | remove her for good |
| `GET` / `PUT /api/companions/<id>/notes` | read / correct her notes (`{about_you?, our_story?}`) |
| `POST /api/companions/<id>/notes/update` | write up the conversation now (background) |
| `GET /api/companions/<id>/status` | `{notes: {busy, updated_at, words, waiting, ...}, pictures: {running, last}, gallery}` |
| `GET /api/forge/status` | NPC Forge: online, Models available, ComfyUI, options for the Meet window |
| `POST /api/forge/start` | start NPC Forge if nothing listens on its port |
| `POST /api/companions/make` | `{origin?, hair_color?, age_group?}` -> `{npc_id, job_id}` |
| `GET` / `DELETE /api/companions/make/<npc_id>/<job_id>` | progress (`queued`, `running`, then imported and opened: `ready`) / cancel |
| `POST /api/chat/stream` with `{"session_id", "greet": true}` | her first words (only while the chat is empty); new SSE events: `looking`, `notes` |

## Files

```
data/avatars/<id>/avatar.json  portrait.png  body.png
                   companion.json          first_met, self_view
                   notes/about_you.md  our_story.md  state.json  history/
data/companions.json                       the move from the old chats happened (and what it did)
data/conversations.before-companions.db    the database as it was before that
```

Code: `core/companions.py` (opening, listing, removing, the move), `core/written.py` (companions written in Altera), `core/notes.py`, `core/forge.py`, `core/selfview.py`,
`core/routes/companions.py`, `static/js/companions.js`; the persona is `core/prompts.py` (`PersonaSection`, `NotesSection`,
`NewbornSection`); the gallery is `core/photos.py` (`sync_premade`, `gallery`, `find_match`, `rank_premade`).

## Integration validation limits

- Model-specific note quality, speed and the 6000-token output allowance need a real integration check with the selected model. The automated suite uses fake models. A rewrite that does not parse or loses too much is refused and retried later.
- How long a tidy-up takes while LM Studio and the voice service share the GPU.
- The 4:5 crop of the square portrait in the panel (`object-position: center 18%`) was judged by eye on three characters.
