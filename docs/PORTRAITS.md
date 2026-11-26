# Portraits from NPC Forge

[NPC Forge](https://github.com/Fel101/fels-npc-forge) is an optional app that paints portraits from descriptions and creates characters with personalities. In Altera, you can make a pal, import a character card, or write a companion yourself and upload a picture for any of them.

A pal starts with no picture (Home shows their initial), a character has their card's picture or none for a `.json` card, and a
companion written in Altera has none until one is uploaded or painted. Each of them can get a portrait three ways, under
**Portrait** in their **Settings** on Home: an uploaded picture, a character already in NPC Forge's library, or a new one
NPC Forge paints. Companions imported from NPC Forge already have their portrait from their source character, so the
portrait choices are not offered for those.

## A picture uploaded instead

Choose **Upload a picture…** and pick a PNG. It goes through the same age check as a painted portrait — one that reads
as under 18 is refused, and the picture shown before stays — and until one succeeds Home shows the initial. A
character's card picture is kept aside, so **Use the card's picture again** goes back to it; for anyone else **Remove the
portrait** goes back to the initial. Once a companion has a portrait, pictures can be made from it; without one there is
nothing to make them from.

## A character from NPC Forge's library

**Choose from NPC Forge's library…** shows the characters you already made in NPC Forge that have a portrait. Click one
and its existing portrait is copied. A character under 18 is greyed out,
with the reason on hover, and the portrait goes through the age check before it is used. It counts as a portrait from
NPC Forge, so **Bring it to life** makes the living portrait from that character. Run the current NPC Forge to fetch
its cards through the API. With NPC Forge 1.0 (API 44), the picker uses
`imagegen.npc_forge_library` in `config.yaml` for compatibility.

## Making one

On **Home**, open **Settings** under the pal or character, and choose **Make a portrait in NPC Forge…** under **Portrait**.

- **Paint it from the card's picture** (characters with a picture only): the card's picture is sent to NPC Forge as the
  photo it redraws. The face is kept and the rest is redrawn in the art style you choose.
- **Paint it from a description** uses **How they look** to guide the portrait of a fictional adult. For a character
  the box starts with the card's description. **Ask the model to write it** asks your chat model to draft a shorter one.
  For a pal, write the description yourself.
- Choose an **Art style**, **Setting** and, if you want to specify it, **Gender**. The gender follows the
  description until you choose one.

NPC Forge has to be running, with ComfyUI. If it is not, the dialog says so and offers **Start NPC Forge**, which waits up
to a minute for it to answer. Painting takes a minute or two, and you can close the dialog and keep going: the portrait
is brought in the next time the settings (or the dialog) look at the job. **Stop painting** cancels it.

## Choosing an engine

NPC Forge draws with the engines chosen in its own settings unless you choose others in the dialog:

- **Picture engine** draws a portrait from a description.
- **Redraw engine** redraws the card's picture, and also makes the living portrait.

Each list starts with **NPC Forge's default** (named) and then every engine NPC Forge lists, with its own note on speed
and memory under the lists. The heavy defaults (Qwen-Image 2512, Qwen-Image-Edit 2511) need about 29 GB of free video
memory and around 35 GB of free RAM on a machine whose graphics share system memory. With less, NPC Forge refuses the
picture or waits for memory, and the dialog shows NPC Forge's own message. **Mage-Flow** (built in) is the light choice.

The engines chosen are kept with the portrait, so the next portrait starts from them. **Bring it to life** has its own
**Redraw engine for the living portrait** list, which starts from the one chosen for the portrait and is kept when changed.
Leaving an engine on NPC Forge's default follows whatever NPC Forge is set to, then and later.

## What happens to it

- NPC Forge makes a character of its own in its library and paints it, so you can keep working on that character in NPC
  Forge. Altera only reads NPC Forge's library; it never writes there.
- The vision model checks how old the person appears. If settings say **Not age-checked yet**, start your vision model
  and reopen Home or the person's settings. A portrait that reads as under 18 is refused or taken down. Altera shows a
  previously passed card portrait or the person's initial. [Image checks](SAFETY.md#image-checks)
- The card's own picture is kept beside it (`card_picture.png`). **Use the card's picture again** goes back to it, and
  for a pal or a `.json` card **Remove the portrait** goes back to the initial.
- The card's text stays what the character is: only the picture comes from NPC Forge.
- Pals and characters still do not send pictures in the chat. That is a companion feature.

## Bringing it to life

Once a pal or character has a portrait from NPC Forge, **Bring it to life** asks NPC Forge for its living portrait: the
expressions it needs, then the frames in which only the face moves (a blink, the mouth while it talks, happy, laughing…).
This takes a good while longer than the portrait: ten expressions, each about as long as a portrait or longer (on a
laptop's built-in graphics, about eight minutes each with Mage-Flow-Edit, so well over an hour). When it is done the
frames are copied and age-checked as they are for a companion (`core/living.py`), one model call per frame, so the
dialog says they are still coming in for a few minutes more. The chat animates the portrait once they are in, while the
living portrait setting is on.

A new portrait replaces the old frames, because frames only fit the portrait they were made from.

While NPC Forge works, the dialog shows the step it is on and the first step that could not be made, with NPC Forge's
reason.

## The GPU

Altera finds NPC Forge at the address in `companion.forge.url` if you set one, then through Fel's Studio, then on port
8766. It can also find NPC Forge 1.0 on port 8765, where it checks that the service really is NPC Forge.

The GPU light in the chat header shows which studio apps are sharing the graphics card or whether NPC Forge has it to
itself. Open the light to see what each app is running and since when. If **One model at a time** is on in NPC Forge, a
reply from LM Studio or Ollama on this PC waits for the card. You can stop the reply while it waits.

With NPC Forge 1.0 (API 44 or later), Altera checks whether the GPU is ready before queuing a picture. If LM Studio or
Ollama holds the card, Altera can ask the local model servers to unload and wait up to 15 seconds for the card to clear.
Set `companion.forge.unload_llm: false` to do that yourself. If ComfyUI or another program holds the card, the dialog
says which program needs attention. Altera makes one recovery attempt per portrait if the GPU becomes busy during a job.

When NPC Forge guesses an age for a character, Altera labels it as a guess. The picture has its own age check.

With an NPC Forge older than API 44 nothing is asked first. A step the GPU was refused for is read from NPC Forge's error
text, and Altera stops the job there, since every later step would be refused the same way.

## Where it is stored

In the person's `data/avatars/<id>/avatar.json`:

- `forge`: the NPC Forge character the portrait came from, with the description, style, setting, gender and engines
  (`image_model`, `edit_model`; empty for NPC Forge's default) used. The next portrait starts from them.
- `portrait_from`: `forge` (painted, or from the library: `forge.from` is then `library`), `card`, `upload` or `none`.
- `forge_job`: the job NPC Forge is working on, while there is one.
- `age_check`: the age check of the picture in use (`ok` is `null` while it is unchecked), and `card_age_check` the card
  picture's own.
- `portrait_removed`: `{at, from, apparent_age}` when the later check took the last portrait down. A new portrait clears it.

The picture itself is `portrait.png` in the same folder, and the living frames are in `living/`, as for companions.
`source` stays the card file or `pal.yaml`. Backups carry all of it.

The code is `core/portraits.py` (what to paint and bringing it in), `ForgeClient.paint` and `ForgeClient.living` in
`core/forge.py`, and the routes under `/api/portraits/` (see the [API reference](API_REFERENCE.md)).

## A sample of the voice

Beside the **Voice** list in **Make a pal** and in a pal's or character's settings, **Play a sample** says a short line in
the chosen voice (or the default one), so you can hear it before choosing it. When it stays silent it says why: the
local voice is not installed, not running yet or still loading, or the voice service does not have that voice (with the
command that adds it).
