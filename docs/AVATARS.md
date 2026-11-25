# Avatars and pictures

Reviewed 2026-10-26. Hardware timings below are historical observations, not current performance guarantees. The portable starter disables image generation; existing instance settings may differ.

[NPC Forge](https://github.com/Fel101/fels-npc-forge) is an optional app that paints portraits from descriptions and creates characters with personalities. You can make a pal, import a character card, or write a companion yourself and upload a picture right in Altera. Pictures in the chat also need ComfyUI.

Each avatar has a conversation and its own settings. See [Companions](COMPANIONS.md).

Altera can have a face and send pictures of herself in the chat. Avatars are made in **NPC Forge** (the ComfyUI character
studio) and imported here; pictures are edits of the avatar's portrait, made by ComfyUI. Core conversation and pictures stay non-sexual.

Avatars and pictures are stored locally. Your selected model and image services receive prompts; use local services for local inference.

Altera labels generated pictures **Imagined image** in chat and returns `imagined_scene` in the photo API. Notebook and
chat context treat the scene as creative input and track whether the image was delivered.

**Picture of us** is available in the ordinary app for an explicitly selected photo of yourself and an everyday scene. Review the displayed vision endpoint, vision model and image endpoint, then confirm that the photo is yours, you are an adult, and those services may receive it. A changed provider requires fresh review; a queued job retains the reviewed targets. The original stays on the device. A bounded, metadata-stripped PNG copy is held in Altera's process memory while queued and sent to both configured services, which may be remote and retain it; custom service workflows may have their own storage or network behavior. Altera stores the generated image, request fingerprint and conversation marker, not the reference bytes. Removing a conversation does not itself remove its generated picture; delete that picture separately if wanted. Shared images are excluded from automatic gallery reuse, and a new reference is required to reroll. The request does not unload the chat model.

Choose **Saved pictures** in the chat header to view the open companion's latest 60 saved pictures. Generated pictures are labelled as imagined.

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
- **Blocked or failed pictures** show a short reason in the chat. A blocked image is discarded and its status is kept for inspection.

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
  (😂 laughing, 😮 surprised, 🥺 sad, 😳 embarrassed, 😊 happy...). Blink and talking frames use the resting face, so she blinks and talks with it
  and shows the expression afterwards. Her mouth follows the loudness of her voice once you have clicked or typed on the page
  (the browser's rule for listening to audio); before that it moves in a steady rhythm.

`GET/POST /api/living` (the setting), `GET /api/avatars/<id>/living` (which frames, how many checked, why none),
`POST /api/avatars/<id>/living/sync`, `GET /api/avatars/<id>/living/<frame>` (no-store).

## Content rules

- Characters must be fictional adults. The rules refuse requests involving minors, childlike characters, real people's likenesses, or non-consensual acts.
- Altera checks images for apparent age. The check is an estimate, and shared scenes need a passing result for every subject.
- Separate add-ons are available. Altera applies the same content rules to them.

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
- "Delete All Conversations" removes every picture. Imported avatars stay; "Remove" in the avatar
  picker deletes the companion's owned chats, notes, memories, avatar and pictures through a guarded operation; active work returns 409 before deletion. NPC Forge's own copy is never touched.

## Endpoints

| Route | Purpose |
|-------|---------|
| `GET /api/avatars`, `GET /api/avatars/forge` | Imported avatars; characters in NPC Forge's library and whether each can be imported. |
| `POST /api/avatars/import` | `{npc_id, name?, age?}`. `age` is needed for a freeform character with none. |
| `POST /api/avatars/<id>/activate`, `DELETE /api/avatars/<id>` | Switch or remove. |
| `GET /api/avatars/<id>/portrait`, `GET /api/forge/<id>/thumb` | Images (`no-store`). |
| `POST /api/photos` | `{scene, session_id?}`: the camera button; an explicit positive existing session selects that conversation's companion, independent of the globally active picker. 422 with the reason, `blocked`, and `required_level` (null for a hard rule) if a rule blocks it. |
| `GET /api/photos`, `GET /api/photos/<id>`, `GET /api/photos/<id>/image` | List, status (`pending`/`running`/`ready`/`blocked`/`failed`), picture. |
| `POST /api/photos/<id>/reroll`, `DELETE /api/photos/<id>` | Another one; delete. |
| `GET /api/imagegen/status` | ComfyUI reachable? Which model does the age check use? |

`POST /api/chat/stream` also sends `{"photo": {...}}` events after the text, and `POST /api/chat` returns a `photos` list.

## Files

`core/safety.py` hard rules, `core/adult.py` content policy, `core/photo_tags.py` the tag and the request fallback,
`core/avatars.py` NPC Forge import, `core/photos.py` the picture service, `core/imagegen.py` ComfyUI client and backends,
`core/age_check.py`, `core/companion.py` shared wiring, `core/routes/companion.py`,
`static/js/companion.js`, `static/css/companion.css`. Tests: `test_companion_rules.py`, `test_companion_images.py`,
`test_companion_api.py` (no GPU, model or network needed; they never touch the real database or memory store).
