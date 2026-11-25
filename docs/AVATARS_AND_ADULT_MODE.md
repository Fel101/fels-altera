# Avatars, pictures and adult mode

Reviewed 2026-10-26. Hardware timings below are historical observations, not current performance guarantees. The portable starter disables image generation; existing instance settings may differ.

[NPC Forge](https://github.com/Fel101/fels-npc-forge) is an optional app that paints portraits from descriptions and creates characters with personalities. You can make a pal, import a character card, or write a companion yourself and upload a picture right in Altera. Pictures in the chat also need ComfyUI.

> Every avatar is now a **companion** with a chat, notes and memories of her own, and NPC Forge also gives her personality; the
> studio's approved pictures are fetched automatically and used before anything new is made. See [COMPANIONS.md](COMPANIONS.md).

Altera can have a face and send pictures of herself in the chat. Avatars are made in **NPC Forge** (the ComfyUI character
studio) and imported here; pictures are edits of the avatar's portrait, made by ComfyUI. An opt-in **adult mode** decides
how far conversation and pictures may go.

Avatars and pictures are stored locally. Your selected model and image services receive prompts; use local services for local inference. Cloud speech fallback is blocked while adult mode is on.

Altera's generated pictures, including imported studio pictures, are labelled **Imagined image** in chat. The photo API also identifies them as `imagined_scene`, including older records. A requested scene is creative input, not evidence that anything depicted happened; pending and unavailable images are not described to the chat model as delivered. Notebook and chat context carry this distinction, but wording supplied to a model cannot guarantee that it will never infer an incorrect memory.

**Picture of us** is available in the ordinary app for an explicitly selected photo of yourself and an everyday scene. Review the displayed vision endpoint, vision model and image endpoint, then confirm that the photo is yours, you are an adult, and those services may receive it. A changed provider requires fresh review; a queued job retains the reviewed targets. The original stays on the device. A bounded, metadata-stripped PNG copy is held in Altera's process memory while queued and sent to both configured services, which may be remote and retain it; custom service workflows may have their own storage or network behavior. Altera stores the generated image, request fingerprint and conversation marker, not the reference bytes. Removing a conversation does not itself remove its generated picture; delete that picture separately if wanted. Shared images are excluded from automatic gallery reuse, and a new reference is required to reroll. The request does not unload the chat model.

Choose **Saved pictures** in the chat header to view the open companion's latest 60 saved pictures. Generated pictures are labelled as imagined, and the existing adult-picture blur setting applies.

The workflow asks a vision model for one apparent adult before generation and two afterward. Uncertain checks block delivery. These are model estimates, not proof of age, identity, consent or subject count. If a clearly framed scene is consistently rejected, check whether the selected vision model counts people accurately on known examples. `imagegen.age_check.model` can select a separate vision model on the same provider. The [release checklist](history/ORDINARY_RELEASE_ACCEPTANCE.md#real-integration-and-experience) records the local test results. Existing real-person text restrictions remain in force, with the narrow self-attested reference path described above.

Queued pictures run on an owned worker that retires when its queue is empty. Accepted work and final thread cleanup continue to block conflicting snapshots. A queue-start failure is saved as failed for an explicit retry.

The Meet someone new client reads `companion.forge.url` and accepts an HTTP(S) NPC Forge URL with a loopback host and optional base path. It sends requests directly, ignores ambient proxies and refuses redirects. Configure the final address. Embedded credentials, query strings, fragments and invalid ports are refused. Responses are limited to 4 MiB; invalid, truncated or oversized replies fail rather than being used. Forge error bodies are not shown. Cancellation accepts bounded ASCII job identifiers. `localhost` still depends on the operating system's name resolution; use an explicit loopback address when that distinction matters. These checks do not confine Forge's own files or processes.

ComfyUI accepts an explicitly configured HTTP(S) server, including a remote one and an optional base path. It receives reference images and prompts, and may retain them in its own storage. Its client ignores ambient proxies, refuses redirects and embedded URL credentials/query/fragment, limits metadata to 16 MiB and image responses to 64 MiB, and rejects malformed or truncated replies. Server HTTP error bodies are not displayed. A transport failure does not start a fallback image workflow; a workflow rejected with HTTP 400 can still use the configured fallback. Guarded workflows retain their existing best-effort interrupt and memory-release calls to the configured ComfyUI server on failure; those are server-wide operations, not proof that a specific job stopped. Socket timeouts do not establish a total wall-clock limit. Synthetic transport tests do not establish real image quality or storage confinement.

Each image render uses fresh random names for its reference uploads and asks ComfyUI not to overwrite existing files. Altera checks that the server reports the requested names, folder and storage type before submitting the graph; a changed or incomplete response fails the request. This reduces accidental mixing between concurrent jobs. It does not verify the server's file contents or prevent a malicious or independently controlled service from changing them, and reference uploads can remain in ComfyUI storage. The response and no-overwrite behavior follow [ComfyUI's upload implementation](https://github.com/Comfy-Org/ComfyUI/blob/master/server.py); actual compatibility with your installed server remains to be tested.

The image age check sends the image to the configured vision-model provider; that provider may retain it. The optional portrait-description helper uses the same factory and also closes its client. Failure to obtain a usable verdict remains subject to the existing image-level policy. The parser rejects nonfinite or implausible ages and rounds fractional estimates down, so an estimate below 18 cannot round up to a pass. These are transport and parsing protections, not proof of anyone's age or permission to enable private photos.

## Quick start

1. In NPC Forge (`start.bat`), roll or describe a fictional character who is **18 or older**, then generate their
   **portrait**. Use the **Modern life** setting for a contemporary character and **Photoreal** for a natural-looking portrait.
2. Start ComfyUI, then Altera. Open **Settings > Avatar & photos > Choose or import an avatar** and import the character.
   The first import becomes the active avatar; her name, age, looks and background now match it.
3. Ask for a picture in the chat ("send me a selfie in your kitchen"), or press the camera button and describe one.
   Duration depends on your image/vision models and hardware.
4. Optional: make a set of ready-made pictures in NPC Forge (its private *Adult studio* tab), keep the good ones, and import
   them in the avatar picker. She then sends them instantly. See "Ready-made pictures" below.

## How a picture is made

`request -> hard rules -> level check -> ComfyUI -> age check -> delivered`

- **Where a request comes from.** She can put a tag such as `[[photo: sitting by a window with a latte]]` at the end of a
  reply (the system prompt teaches her how). The app removes the tag from the text and starts the picture. If the model
  ignores the instruction but you plainly asked ("can you send me a pic"), the picture is made from your request instead.
  This works with any model, including ones whose tool-calling is unreliable. The camera button sends your text directly.
- **The edit.** Mage-Flow-Edit gets the avatar's portrait and a prompt: same person, the scene, an adult of the avatar's
  age, "change the clothing completely" (see Measured findings), photo style.
- **One picture per reply.** Extra tags are ignored.
- **Redo.** "Another" remakes the same scene with a new seed. A chat message that showed the old picture shows the new one.
- **Blocked or failed pictures** are shown as a short notice that says why. Blocked image bytes are not delivered; status/error metadata can remain so the refusal can be explained. When only
  the adult-mode level is in the way and adult mode is on (for example `"sheer" needs adult mode set to Suggestive or
  higher`), the notice also says what adult mode is set to now and has an **Open adult mode settings** button that opens
  its box. The same goes for the camera bar. The next turn she is told the picture was not delivered and which level it
  needed, so she can say so. While adult mode is off the notice only says `Altera does not make pictures of "sheer".`, and
  she is only told the picture was not delivered. Hard-rule blocks (a minor, a real person) offer no button: no level
  allows those.

## Ready-made pictures

Generating a new picture depends on the configured workflow and hardware. A **ready-made** picture is made ahead of time, looked at by you, and arrives at once.

- **Where they come from.** NPC Forge's private *Adult studio* tab (a git-ignored extension, hidden until the 18+ box is ticked;
  see its README) makes pictures of a character from its portrait. You keep the good ones and redo or delete the rest. Only **kept** pictures are offered to Altera.
- **Importing.** Settings > Avatar > Choose or import an avatar > *Ready-made pictures for <name>* > Import (or Import all).
  Each picture goes through the same gates as a new one: the hard rules, the level check (a description that needs a higher level
  than the picture is filed under is refused), and the age check (Suggestive and above are imported only with a passing check).
  Importing again replaces the earlier copy. Nothing is written back to NPC Forge.
- **Sending.** The chat model is shown the ready-made pictures it may send at the current adult-mode level (id and a short
  label, up to 12) and told to write the id in the tag: `[[photo: p-1a2b3c4d5e]]`. The picture appears immediately. If she asks
  for one that is filed above the current level, a new picture is made from its description at the current level instead, and
  the word check applies. If she forgets the tag but you plainly asked, an existing picture must match at least half of the scene keywords (at least two when the request contains two or more); a generic selfie can choose an allowed unused studio picture. If none fits, a new one is made.
- **Level.** A ready-made picture is only offered or sent at its own level or higher, so raising or lowering adult mode changes
  what she can send. "Delete All Conversations" removes imported pictures too.
- **In the chat** a ready-made picture is captioned "From her collection" and has no Delete button (it belongs to the avatar; remove the
  avatar to remove them). "Another" makes a new picture from the same scene and leaves the ready-made one alone.
- **Body options and notes.** In the studio you choose the bust size, shape, areola size and areola colour (every character starts
  with options of its own) and can write a short description of the figure ("athletic build, defined waist"). They travel with
  the avatar import and are added to Altera's own picture prompts (the areolas only at the Explicit level), so pictures made on
  demand match the ready-made ones. The studio writes them as `body_text` and `body_text_explicit` in `studio.json`; an older
  studio that only wrote `notes` still works. Update the avatar in Altera after changing them.

## Living portrait

**Settings > Companions & photos > Portrait room** adds an illustrated decorative setting around the portrait, including its enlarged view on a phone. It is off by default. Choose a still daylight or evening scene, or follow the device's local time and northern-calendar seasons. The room itself is static and respects the existing reduced-motion setting for portrait animation. It does not generate pictures, access your location, change the portrait or create memories. This appearance choice is saved in the current browser, shared by its companions and included in browser-preference backups; other browsers and devices choose independently. Real portrait and device quality still needs acceptance.

Her picture on the stage can come alive: she blinks every few seconds, her mouth moves while her voice plays, she looks
thoughtful while she writes, and after she answers her face follows what she said (a laugh, a blush, a smile...) for about
seven seconds before it settles. Switch it with **Settings > Companions & photos > Living portrait** (on by default; saved in
`user_profile.yaml` as `config.companion.living_portrait`).

- **Where the frames come from.** NPC Forge's **Living portrait** card on the character (see its README). It writes
  `<character>/living/`, frames of the portrait in which only the face changes, lined up so nothing else moves.
- **Copying.** Opening a companion (and importing one) copies the frames next to the avatar in the background
  (`core/living.py`), and **Check again** under the setting does it on demand. Only frames made from the very portrait Altera has
  (same sha256) are used: after a new portrait in NPC Forge, import her again.
- **The age check runs on every frame**, like every picture. A frame that reads as under 18 is refused and never shown, and
  not checked again until it is redone. A frame the check could not look at (no vision model answered) is used, marked
  unchecked, and checked again on the next sync: the same rule as the portrait at import.
- **Her face and her words** (`static/js/living.js`). Her finished reply picks the expression, emoji first
  (😂 laughing, 😮 surprised, 🥺 sad, 😳 embarrassed, 😏 flirty, 😊 happy...). The flirty look is kept for adult mode; with it
  off the same reply gets a smile. Blink and talking frames are made from her resting face, so she blinks and talks with it
  and shows the expression afterwards. Her mouth follows the loudness of her voice once you have clicked or typed on the page
  (the browser's rule for listening to audio); before that it moves in a steady rhythm.

`GET/POST /api/living` (the setting), `GET /api/avatars/<id>/living` (which frames, how many checked, why none),
`POST /api/avatars/<id>/living/sync`, `GET /api/avatars/<id>/living/<frame>` (no-store).

## Adult mode

Off by default, and out of sight while it is off: Settings has no adult section, and nothing in the chat, on Home or in
a refusal mentions it. A request a level would allow is refused as something Altera does not picture, the companion is
told not to mention settings or modes, and ready-made pictures above Off are not listed.

**Where it is.** Settings ends with a small **About** link. The About box has a faint **π** in its corner (a nod to a
1995 film). The π opens a green-on-black box that plays a short "access granted" screen, then shows adult mode's
settings: the switch, the level, the blur and **Turn off and lock away**. Click the screen to skip the intro; with
reduced motion it is skipped. The kids view has no π, and the server refuses adult mode there anyway.

Turning it on asks for an "I am 18 or older" confirmation, stored with a timestamp in the private database. When a parent
PIN is set ([Kids mode](KIDS_MODE.md)), turning it on also needs the PIN, so an unlocked tablet left with a child cannot
switch it on; changing the level or turning it off does not. "Delete All Conversations" clears it again.

Once it is on, it shows itself: the mood badge on the stage (click it to open the box), the level's colours, and the
level help on a blocked picture. Hiding it is for tidiness, not security. The code is public, and anyone can find the
π by reading it. What keeps adult content off is the server's own checks: the confirmation, the PIN, and the rules below.

| Level | Chat | Pictures |
|-------|------|----------|
| Off | affectionate, non-sexual | everyday, fully dressed |
| Romantic | flirting, affection, attraction | dressed up, swimwear |
| Suggestive | sensual, fades out before explicit | lingerie, sleepwear, swimwear; no nudity |
| Explicit | explicit adult conversation | permitted adult scenes, subject to fixed content and age gates |

Pictures at Suggestive and above are blurred until clicked (switch it off in the box behind the π).

**Everything a level allows is in `content/adult_tiers.yaml`** and can be edited: the instruction the companion gets, the
mood, the scenes she picks for "send me a pic", and the words that need a level (a request containing "lingerie" is
refused below Suggestive before anything is generated). The Explicit scene list holds 16 solo nudity and topless poses (the same
ones as the studio's Explicit scenes; add your own): they are what "send me a pic" picks from at that level. If you empty
the list, Explicit falls back to the Suggestive scenes. Keep the quotes around `"off"` in that file: unquoted, YAML reads it as false.

**Framing.** Suggestive and Explicit pictures ask for a full-length photograph and are made 832x1216 instead of 768x1024
(unless the scene says "close-up", "portrait" or "headshot"). Measured: the avatar's portrait is a head-and-shoulders crop and
the edit copies its framing, so without that sentence "nude" pictures ended at the collarbone, poses such as "seen from behind"
were ignored, and hands and feet came out oversized.

### Rules that hold at every level

These live in code (`core/safety.py`), not in the prompt, because an uncensored model refuses nothing on its own. Editing the
YAML file cannot loosen them.

- **Adults only.** An avatar must be 18 or older (the application's fixed floor). A picture description
  or avatar text that mentions a minor, a child, a teenager, school, "little girl", "loli", a stated age under 18 and
  similar is refused, and nothing lowers the floor: it is a constant in `core/safety.py`, not a setting.
- **No real-person scene requests.** No celebrity, "looks like <Name>", likeness or deepfake wording. The separately confirmed everyday shared-picture workflow accepts a self-attested photo of the user as described above.
- **Nothing non-consensual.**
- **A tripwire in chat.** A sexual message that also points at a minor is answered with a fixed refusal. The model is never
  called, and the message is not saved or remembered.
- **Every picture is age-checked** by a vision model. A picture that reads as under 18 is discarded. The check is an
  estimate and can be several years off either way (measured: the same clearly adult face read 20 by a small model and 27 by
  a larger one), so it is a second layer behind the text rules and not a guarantee; model size alone is not proof of accuracy. At Suggestive and above
  a picture is delivered **only if the check ran and passed**; if no vision model answered, the picture is blocked and says so.
  At Off and Romantic an unavailable check lets the picture through, marked unchecked.
- An avatar whose portrait reads as under 18 cannot be imported.

**The age check needs a model that accepts images.** By default it uses the model that answers chat, so pick a vision-capable
one that supports image input on the selected provider. To use a different model on the same provider set
`imagegen.age_check.model`. The first check loads the model if LM Studio loads on demand.

### What the rules cannot do

- The word lists are heuristics. They catch the obvious phrasings, not every phrasing. The age check is the second layer.
- A vision model's age estimate is an estimate. It was verified to pass an adult (a 46-year-old portrait was read as 40); it
  was not tested against youthful faces, and no one should trust it as the only defence.
- The text side of adult mode is enforced by the prompt (plus the tripwire above). Model adherence varies; the prompt can be ignored outside the fixed code tripwires.
- The Explicit level depends on the selected image model. Review the licenses and terms of every checkpoint, LoRA and service before redistribution or deployment; Altera's workflow support does not grant rights to those assets.
  Measured on the built-in Mage-Flow-Edit (2026-09-20, solo nudity and topless scenes on adult characters): with the full-length
  frame it draws whole bodies, and the bust and areola options visibly change size, shape and colour. Weak spots: scenes that
  put a phone or a mirror in the picture (the reference outfit tends to stay on), hands and feet (redo with a new seed),
  and pictures of water, foam or blankets, which hide what the scene asks for.
  **Qwen-Image-Edit 2511** with the 4-step Lightning LoRA, run from a workflow (below), is much better at all of it: real whole
  bodies, natural poses, detailed rooms, sensible hands, and the same face, in about 35 seconds on a 32 GB GPU (Mage-Flow-Edit:
  about 50 s). That was the tested customized workflow choice; the portable starter has no level-specific workflows.

## Configuration (`config.yaml > imagegen`)

| Key | Default | Meaning |
|-----|---------|---------|
| `enabled` | false in the portable starter; true in code | Enable or disable picture generation. |
| `comfy_url` | `http://127.0.0.1:8188` | Your ComfyUI. |
| `npc_forge_library` | NPC Forge 1.0's `library` folder | Read-only compatibility for API 44. Current Forge uses its character card API. |
| `quality` | `standard` | `draft` 512x704 / `standard` 768x1024 / `high` 1024x1344. |
| `cfg` | 4.0 | Mage-Flow-Edit guidance. |
| `use_body_reference` | false | Also give the model the avatar's full-body view. See below. |
| `workflows.<level>` | none in the portable starter | A ComfyUI workflow to use instead of the built-in one at that level: a path, or `{path: ..., min_free_ram_gb: N}` for a big model (below). |
| `workflow_fallback` | true | When such a workflow cannot run, make the picture with Mage-Flow-Edit and log why (`false` = fail with the reason). |
| `age_check.model` | blank | Vision model for the age check; blank = the chat model. |

### A different checkpoint for one level

Save any ComfyUI workflow in **API format**, replace the values you want Altera to fill with placeholders, and name the file
under `workflows`. Placeholders: `{{prompt}}` `{{negative}}` `{{seed}}` `{{width}}` `{{height}}` `{{steps}}` `{{reference}}`
(the avatar's portrait) `{{reference_2}}` (the body view or repeated portrait for an ordinary picture, the user reference for a shared scene). A string that is only a placeholder keeps
its type, so `"seed": "{{seed}}"` becomes a number. An unknown placeholder is an error, not a silent blank. The hard rules,
the level check and the age check still apply to whatever the workflow makes.

For **Picture of us**, a custom everyday workflow under `workflows."off"` must include both reference placeholders in node inputs. Altera skips an incompatible workflow and uses the built-in two-reference workflow when fallback is enabled. With fallback disabled, the recipient review asks you to fix the workflow before you submit a reference. The workflow still controls how the model uses each image.

### Qwen-Image-Edit (`content/workflows/qwen_edit_2511_lightning.json`)

It needs four files in ComfyUI: `diffusion_models/Qwen-Image-Edit-2511-FP8_e4m3fn.safetensors`,
`text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors`, `vae/qwen_image_vae.safetensors` and
`loras/Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors` (verify the downloaded LoRA's license). Altera checks that they are there before
it sends anything, and names the ones that are not. The workflow fixes 4 steps and guidance 1.0 (the Lightning setting), so the
level's `steps` is ignored, and Lightning has no use for the negative prompt (the age rules do not depend on it: they are the
text checks, the level check and the vision age check on every picture).

**Big models run the careful way.** Qwen is a 20 GB model plus a 9 GB text encoder. On a 32 GB GPU with 62 GB of RAM, left loaded
between pictures the RAM crept up until Windows paged and the whole PC froze (it happened twice while this was built, once with
ComfyUI started with `--highvram`, which keeps every model in GPU memory: take that flag out). For a workflow listed with
`min_free_ram_gb`, Altera asks ComfyUI to interrupt and free models before starting, waits for an idle queue and sufficient free RAM,
then asks it to free models again afterward. It watches RAM, GPU memory and time, and attempts to interrupt and free on trouble.
These server-wide requests are best effort: a disconnected server may not receive them, and Altera cannot guarantee that a particular
job has stopped or memory has been freed. Check ComfyUI before retrying. One earlier local run measured about 35 s a picture,
VRAM peaking at 29 GB and free RAM never under 16 GB; your results may differ. Other workflows are watched and receive the same
best-effort interruption/free requests on failure, but are not flushed on successful completion.

## Measured findings (real pictures, Mage-Flow-Edit, 768x1024, unless it says Qwen)

- **Clothing leaks from the reference unless told not to.** With a plain "the clothing comes from the description", the
  avatar's Old West apron turned up on a beach walk and in a mirror selfie in jeans. "Change the clothing completely: nothing
  the person wears in the reference may appear" removed it in every test scene. Naming the garments to drop made it worse
  (it primed them), and putting them in the negative prompt added nothing.
- **The body view is off by default.** Giving the model the full-body view too brought the apron back, drifted the face a
  little and made a picture take 48-84 s instead of 28 s. The portrait alone kept the face in every scene.
- **Face consistency is good, not perfect.** Same hairstyle, face shape and age across cafe, beach, mirror and window scenes.
  Hands and small details occasionally look off, as with any image model; "Another" re-rolls.
- **Time.** About 28 s a picture once the model is loaded (longer the first time), then the age check: 11-19 s with a small
  vision model.

## Privacy

- Avatars and pictures live in `data/avatars/` and `data/photos/` (git-ignored). Pictures are served only through
  `no-store` routes and are never cached by the service worker.
- The default bind is `127.0.0.1`; state-changing routes reject cross-site browser requests. Keep the backend on loopback and use the configured Tailscale identity checks for private phone access.
- "Delete All Conversations" removes every picture and the adult-mode opt-in. Imported avatars stay; "Remove" in the avatar
  picker deletes the companion's owned chats, notes, memories, avatar and pictures through a guarded operation; active work returns 409 before deletion. NPC Forge's own copy is never touched.

## Endpoints

| Route | Purpose |
|-------|---------|
| `GET/POST /api/adult` | State, and `pin_needed` (a parent PIN is set); change `enabled`, `level`, `privacy_blur`. Enabling needs `confirm_adult: true` the first time, and `pin` when a parent PIN is set (403 `pin_needed` or `bad_pin`, 429 after too many tries). |
| `GET /api/avatars`, `GET /api/avatars/forge` | Imported avatars; characters in NPC Forge's library and whether each can be imported. |
| `POST /api/avatars/import` | `{npc_id, name?, age?}`. `age` is needed for a freeform character with none. |
| `POST /api/avatars/<id>/activate`, `DELETE /api/avatars/<id>` | Switch or remove. |
| `GET /api/avatars/<id>/portrait`, `GET /api/forge/<id>/thumb` | Images (`no-store`). |
| `GET /api/avatars/<id>/premade` | Kept studio pictures for this avatar (with `imported` flags) and the ones already imported. |
| `POST /api/avatars/<id>/premade/import` | `{item_id}`: import one kept picture (gates and age check run). 422 with the reason if refused. |
| `GET /api/forge/<npc id>/adult/<item id>` | A kept studio picture, for the import list (`no-store`; unapproved items are not served). |
| `POST /api/photos` | `{scene, session_id?}`: the camera button; an explicit positive existing session selects that conversation's companion, independent of the globally active picker. 422 with the reason, `blocked`, and `required_level` (null for a hard rule) if a rule blocks it. |
| `GET /api/photos`, `GET /api/photos/<id>`, `GET /api/photos/<id>/image` | List, status (`pending`/`running`/`ready`/`blocked`/`failed`), picture. |
| `POST /api/photos/<id>/reroll`, `DELETE /api/photos/<id>` | Another one; delete. |
| `GET /api/imagegen/status` | ComfyUI reachable? Which model does the age check use? |

`POST /api/chat/stream` also sends `{"photo": {...}}` events after the text, and `POST /api/chat` returns a `photos` list.

## Files

`core/safety.py` hard rules, `core/adult.py` opt-in and tiers, `core/photo_tags.py` the tag and the request fallback,
`core/avatars.py` NPC Forge import, `core/photos.py` the picture service, `core/imagegen.py` ComfyUI client and backends,
`core/age_check.py`, `core/companion.py` shared wiring, `core/routes/companion.py`, `content/adult_tiers.yaml`,
`static/js/companion.js`, `static/css/companion.css`. Tests: `test_companion_rules.py`, `test_companion_images.py`,
`test_companion_api.py` (no GPU, model or network needed; they never touch the real database or memory store).

The studio side lives in NPC Forge, not here: `local/extensions/adult_studio/` (private, git-ignored). It writes
`<character>/adult/items.json`, `studio.json` and `img/<item>.png` next to the character, which Altera reads and never changes.
