# Pals

A **pal** is a light persona: easygoing company for a chat, made in a few seconds from a **name**, a **vibe** and a
**voice**. Pals use the same chat page, speech and controls as companions; what they keep beyond the chat itself is up to
you.

## Making a pal

On **Home** (`/home`), choose **Make a pal** (on the Pals tile's empty note, or beside the Pals gallery). Give it a name
(up to 40 characters), pick a vibe and a voice, and choose **Make pal**. The chat opens and the pal says hello.

- **Vibes** are YAML presets shipped in `content/pals/` (Sunny, Chill, Witty, Thoughtful, Geeky). Each has the same shape
  as `personality.yaml` (`traits`, `speech_style`, `interests`, `quirks`) plus `label`, `description` and `order` for the
  picker. Add a file there to add a vibe; a file that does not parse is skipped.
- **Voices** come from the speech engine in Settings. With Voice Lab selected, the picker lists ready Cast voices first,
  then ready presets. Each voice shows its licence rating and **Play a sample** lets you hear it before saving.
  **Default** uses the configured voice. A saved choice is kept if its engine or voice is unavailable.

## What a pal keeps

| Switch | When on | Off (the default) |
|---|---|---|
| Memory | facts from your messages are remembered and recalled | nothing is extracted or recalled |
| Notes | notes about you and your chats are kept and read | no notes are written or read |
| Story | saved moments and reflection are used | no episodes in the prompt; reflection refuses to run |
| Check-ins | presence may send messages (it must also be switched on in the chat) | presence refuses to enable or run |

With memory and notes off, the pal is told it only knows what is in the current chat, so it says so instead of
pretending to remember. Change the switches, the name or the voice with **Settings** under the pal on Home.

## Changing who a pal is

The vibe is only where a pal starts. On Home, **Settings** under the pal → **Who they are** has everything the vibe
gave it, to change as you like:

- **About**: a sentence or two about them (up to 400 characters).
- **Traits**, **Interests** and **Quirks**, one per line (up to 8 traits, 8 interests and 6 quirks; a line may hold a
  comma).
- **Tone**, **Formality** and **Humour**: how they talk, in a few words each. **Uses emojis** is a switch.

The same hard content rules apply as to every avatar card, and a pal in [kids mode](KIDS_MODE.md) also keeps its words
to the topics kids mode allows. Something too long or too many is refused with what to fix; nothing is cut short
without saying so. The change is saved to the pal's `pal.yaml` and the next reply uses it. The vibe's own file in
`content/pals/` is not changed, so another pal made from it starts as before.

A pal can also have its own temperature and reply length: see [Reply settings](REPLY_SETTINGS.md).

## Where a pal lives

`data/avatars/<id>/pal.yaml` is the pal: the chosen name, vibe, voice, the persona copied from the vibe, and the
`features` switches. Its settings on Home change this file; you can also edit it by hand, and the next time the pal is
opened the change is picked up. A file that no
longer parses, or breaks the same hard content rules as every avatar card, is ignored and the pal stays as it was.
`avatar.json` beside it is the copy the rest of Altera reads (`"kind": "pal"`).

Because pals live under `data/`, backups, restore and **Remove** cover them exactly as they cover companions. (The plan
called for `content/pals/`; that folder holds the shipped vibes instead, because backups only carry a fixed list of
preference files outside `data/`, and a pal kept there would not survive a restore with its chat.)

A pal starts with no portrait: the picture shows its initial on a colour chosen from its id. NPC Forge can paint it one
from a description, and then bring it to life, from the pal's settings on Home: see [Portraits](PORTRAITS.md). **Play a
sample** beside the voice list says a short line in that voice.

A pal can be put in [kids mode](KIDS_MODE.md), a guardrail for a pal a child talks to, once kids mode has been set
up on Home.
