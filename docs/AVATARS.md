# Avatars and pictures

A portrait gives a pal, character or companion a face. You can upload one on Home, use a character card's picture,
or have NPC Forge paint one. [Portraits](PORTRAITS.md)

For pictures inside a chat, open a companion with a portrait and set up ComfyUI. Altera turns off picture generation
in a fresh install. The configuration below explains how to switch it on.

## Make a picture in chat

1. Start ComfyUI and open the companion's chat.
2. Ask them for a picture or use the camera button and describe an everyday scene.
3. Wait for the image card. If a picture fails or a check blocks it, the card gives a reason.

Altera labels generated pictures **Imagined image**. Choose **Another** to make a new version of the same scene.
**Saved pictures** in the chat header shows the latest 60 pictures for that companion.

## Picture of us

Use **Picture of us** to imagine an everyday scene with your companion using a photo of yourself. Review the
vision and image services shown before you confirm. Use only your own photo and confirm you're an adult and
agree to send it to those services. The original stays on your device. The services receive a copy and may keep it.

Altera keeps the generated picture. Delete it separately to remove it from Altera's gallery. A new version requires
you to select a reference photo again. [How Altera checks pictures](SAFETY.md#image-checks)

## Living portrait

In **Settings → People & photos**, **Living portrait** lets a portrait blink, move while the companion speaks,
and show expressions that follow their replies. You can use frames from a character card or have NPC Forge make
them. If you replace the portrait in Forge, import the new portrait before using its frames. Use **Check again**
to look for the matching frames.

**Portrait room** puts a scene behind the portrait. Choose still daylight or evening, or follow your device's local
time and seasons. It starts off and is saved in this browser. Portrait animation follows the reduced-motion setting.

## Content rules

Altera's core makes non-sexual pictures of fictional adults. The age check uses a model that can look at images
to estimate how old someone looks.
[Content and image checks](SAFETY.md#image-checks)

## Configuration (`config.yaml > imagegen`)

| Key | Default | Meaning |
|-----|---------|---------|
| `enabled` | false in the starter configuration | Set this to `true` to turn on picture generation. |
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

Altera keeps portraits and pictures in its data folder. Image services receive what they need to make and check the
picture and may keep a copy. [How Altera keeps things safe](SAFETY.md#image-checks)

**Delete all conversations** removes chat pictures and keeps the imported portraits. **Remove** deletes the companion
and their chats, memories, notes and pictures in Altera. If a reply or a picture is in progress, wait for it or stop
it before retrying. The original in NPC Forge stays there.

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
