# Companions

Each companion has their own background, chat, notes, memories and pictures. On **Home** you can open anyone
you've made, or choose **Continue** to return to the last person you spoke with. A [pal](PALS.md) is quicker to make,
and a [character](CHARACTERS.md) comes from a character card.

## Write a companion

1. On Home, choose **Write a companion**. In chat, use **Add someone → Write a companion**.
2. Give them a name and an age of 18 or older. You can add a background, hobbies, a way of speaking or a goal.
3. Open the chat. If they have a portrait, their first hello may follow a brief look at it.

To make changes, open their **Settings** on Home and choose **Edit who they are…**. You can upload a PNG or
have NPC Forge paint a portrait. Their initial appears until they have a picture. [Portraits](PORTRAITS.md)

## Meet someone new

With NPC Forge and ComfyUI running, choose **Add someone → Meet someone new**. **Make someone new** rolls a
fictional adult from Forge's **Modern life** setting. Choose Woman, Man, Non-binary or Any. You can leave heritage,
hair colour or age group on **Surprise me**. NPC Forge paints a portrait, and Altera opens the new chat when it
is ready. You can stop while it paints.

**From my NPC Forge library** imports someone you've already made. If you edit them in NPC Forge, Altera updates
their card the next time you open them and keeps the name, age and gender already imported.

When NPC Forge is stopped, the Meet window may offer **Start NPC Forge**. Forge and ComfyUI need a capable PC
to paint a portrait. The current Forge core supports Meet. [Portraits and GPU help](PORTRAITS.md#the-gpu)

## Get to know them

A new companion starts knowing their name, appearance and personality. They begin to learn about you from what
you tell them. If the vision model can describe their portrait, they use that description when introducing themselves.

On a wide screen, the panel beside chat shows their portrait, background and traits. On a phone, tap the small
portrait in the chat header to see it full size.

## Read or correct their notes and memories

Use the **Notes** button in chat to open **What [companion name] knows**. There are notes about you and notes about
your time together. Read them, fix anything wrong and save. Choose **Update now** to ask the model to write an update.

Open **Memory** to see individual facts, where they came from and whether the original message changed. Automatic
memories are based on your saved messages. If the model is unavailable, entries marked **phrase fallback** came from
a simpler check. [Remember, correct and forget](MEMORY_CONTROLS.md)

When a chat gets too long for the model, Altera updates the notes and sends those with a shorter stretch of recent
messages. Your complete conversation stays visible. If the notes update fails, Altera retries after a few more messages.
[How notebook updates work](ARCHITECTURE.md)

## Saved pictures and reuse

Altera can reuse an everyday picture from your companion's gallery when it fits the scene. Pictures made with
**Picture of us** stay with that conversation and are left out of automatic reuse. [Avatars and pictures](AVATARS.md)

## Remove a companion

In **Settings → People & photos**, choose **Manage companions and pictures**. Use **Remove** to delete that
companion's chats, memories, notes and pictures. Wait for any current reply or picture to finish, or use Stop when
available, before retrying. Their original character in NPC Forge stays there.

**Delete all conversations** clears conversations and saved memories for everyone in Altera while keeping
the imported avatars. [Make a backup first](BACKUP_AND_RESTORE.md)

## Configuration (`config.yaml > companion`)

| Key | Default | Meaning |
|---|---|---|
| `newborn_messages` | 16 | messages before the companion settles into ordinary chat |
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
| `forge.python` | (none) | the Python that runs NPC Forge when Altera starts it. The default uses the first compatible one it finds. |

[Companions and NPC Forge API](API_REFERENCE.md#companions-and-npc-forge) · [Storage schema](Database_Schema.md)
