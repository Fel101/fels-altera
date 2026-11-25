# Characters

A **character** is someone from a CHARX, PNG or JSON character card that character-chat apps share, with the
character written inside. Altera reads Character Card V2 (`chara_card_v2`), and V1 and V3 cards too. A character uses the
same chat page, speech and controls as everyone else.

## Importing a card

On **Home** (`/home`), choose **Import a character card** (on the Characters tile's empty note, or **Import a card**
beside the Characters gallery) and pick the file. When NPC Forge sends a card, Home shows it waiting with **Import** and
**Dismiss**. A failed card stays in the inbox so you can read the reason or dismiss it. Imported and dismissed files go
to the Windows Recycle Bin or are deleted on other systems. The chat opens with the card's first message.

- **CHARX cards** contain a V3 card and its embedded assets. Altera copies supported PNG assets, including the main icon,
  the full-body sprite, expressions and any living portrait that matches the portrait's SHA-256. It uses NPC Forge's
  sheet for details such as age, occupation, traits and manner of speaking. Home shows the card's licence rating and
  AI-generated label when supplied.
- **PNG cards** carry the card in a text chunk (`chara` for V1/V2, `ccv3` for V3, `tavernCardV2` is also read; `tEXt`,
  `zTXt` and `iTXt` chunks all work). When a PNG has more than one, the V3 card wins. The picture becomes the
  character's portrait, with the card text stripped out of it.
- **JSON cards** are the card on its own. The portrait is the character's initial on a colour chosen from its id.
- A character can get a portrait from NPC Forge later, redrawn from the card's picture or painted from a description,
  in the character's settings on Home: see [Portraits](PORTRAITS.md). The card's own picture is kept to go back to.
- CHARX limits are 100 MB for the archive and for its expanded contents, 500 members, and 20 MB per member. PNG cards
  are limited to 20 MB, card JSON to 2 MB, and images to 8192 pixels on each side. Altera refuses a damaged card, a card
  with no name or character text, a card that breaks the hard content rules, or a portrait that the age check reads as under 18.

Altera saves the original in `data/characters/` and the local character record and pictures under `data/avatars/`.
Backups cover both locations. **Remove** deletes them.

## Card voices

Cards can name a preset voice or include a portable voice file. Check the preset in Voice Lab, then choose **Use this voice**
if it is ready. For a file, choose **Preview voice file** to check it with Voice Lab on this computer, then choose
**Import and use this voice**. A cloned recording shows its stated source and allowed use and asks you to confirm permission.
You can open the chat without the card voice and return to **Review card voice** in the character's settings. If Voice Lab speech
is off in Altera, turn it on to hear the new voice.

## How a card is used

| Card field | In Altera |
|---|---|
| `description`, `personality`, `scenario` | the system prompt: who they are, their personality and the situation |
| `first_mes` | the chat's first message, exactly as written (no model call). **Regenerate** on it picks from `alternate_greetings` |
| `mes_example` | example dialogue in the system prompt, cut to a token budget: whole `<START>` blocks first, then by line |
| `system_prompt` | put first in the system prompt (`{{original}}` is removed) while the card's instructions are on |
| `post_history_instructions` | added after your newest message while the card's instructions are on |
| `creator_notes` | shown in the character's settings on Home only, as plain text; never sent to a model |
| `character_book` | the lorebook: entries called up into the system prompt by the recent messages (see below) while the lorebook is on |

`{{char}}` / `<BOT>` become the character's name and `{{user}}` / `<USER>` your name from the profile (or "User").

The example budget is 600 tokens; set `characters.example_tokens` in `config.yaml` to change it (0 turns examples off,
4000 at most).

## The lorebook

A card's `character_book` (its lorebook, or world info) is a list of entries, each with keys and some text. Before each
reply Altera looks through the last few messages, yours and the character's, the new one included:

- An entry comes in when one of its **keys** is in them, as a whole word or phrase (`Kestrel` matches "the Kestrel's
  hull", not "Kestrels"). Keys ignore case unless the entry is `case_sensitive`.
- A **selective** entry needs one of its `secondary_keys` as well.
- An entry marked **constant** is always in.
- An entry switched off in the card (`enabled: false`) is never used.

The entries called up go into the system prompt in the card's `insertion_order`, in a section right after who the
character is, with `{{char}}` and `{{user}}` filled in. They are kept within a token budget: always-on entries are chosen
first, then the others in the card's order, and an entry that does not fit is skipped for a smaller one after it.

- **Budget**: 500 tokens, or the card's `token_budget` when that is smaller. Set `characters.book_tokens` in
  `config.yaml` to change it (0 turns lorebooks off, 4000 at most).
- **How far back**: 4 messages, or the card's `scan_depth` when that is smaller. Set `characters.book_scan_depth` to
  change it (50 at most).

Not used: keys written as regular expressions (`/pattern/` or V3's `use_regex`), because a badly written pattern can
hang the app; entries calling up other entries (`recursive_scanning`); and an entry's `position`, since every entry goes
in the same place. Separate lorebook files (SillyTavern World Info) cannot be imported yet.

Every entry, its keys and its name are checked against the same hard rules as the rest of the card at import, and a
book that breaks them stops the import. A character imported before Altera read lorebooks has its book read once from
the card file saved at import, the first time it is needed.

Altera's own rules and its safety checks on every message still apply whatever a card says. A card's instructions are
added to the prompt, they never replace Altera's.

## Settings

**Settings** under a character on Home changes:

- **Voice**, the same choices as for pals.
- **What it keeps**: memory, notes, story and check-ins, all on to start with (characters live in their story); switch
  any off and it works as described in [Pals](PALS.md#what-a-pal-keeps).
- **Use the card's own instructions**: on to start with. Off ignores the card's `system_prompt` and
  `post_history_instructions`. It is greyed out when the card has neither.
- **Use the card's lorebook**: on to start with, shown when the card has one, with its entries listed (names and keys,
  read only: what an entry says is not shown). Entries switched off in the card or using regular expressions are marked.

The name stays the card's. Characters have no photos.
