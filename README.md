# Fel's Altera

Altera is a companion app that runs on your own PC. You chat with pals, characters and companions you make yourself,
and the conversations, memories and notes stay in a folder on your machine.

It talks to a chat model you run yourself, in LM Studio or Ollama. If you prefer, it can use a cloud service instead.
Speech, pictures and a phone connection are optional extras.

> **Part of Fel's Studio.** Four free apps that run on your own PC and work together.
> [Voice Slate](https://github.com/Fel101/fels-voice-slate) records voices with consent,
> [Voice Lab](https://github.com/Fel101/fels-voice-lab) designs and clones voices,
> [NPC Forge](https://github.com/Fel101/fels-npc-forge) makes characters for games, and
> [Altera](https://github.com/Fel101/fels-altera) lets you talk with them. Each one also works on its own.

[Download for Windows](https://github.com/Fel101/fels-altera/releases/latest)

![Home, with three pals](docs/images/home.png)

![A chat with a pal](docs/images/chat.png)

<img src="docs/images/phone.png" alt="The same chat on a phone" width="300">

*The screenshots show made-up people, with scripted replies standing in for a model.*

## What you need

- **Windows 10 or 11**, where Altera is developed and tested. A `setup.sh` for Linux and macOS is included, but it is
  less tested.
- **Python 3.12** comes with the Windows installer. For a source checkout, get it from
  [python.org](https://www.python.org/downloads/). Versions 3.11 and 3.13 also run Altera. Use 3.12 for the optional
  local voice.
- **A chat model server.** [LM Studio](https://lmstudio.ai) or [Ollama](https://ollama.com) on the same PC is the usual
  choice. vLLM, OpenAI, OpenRouter and any OpenAI-compatible server also work.

Altera itself runs on the CPU. The chat model needs room in graphics memory or RAM. First-run setup suggests a
starting model based on this PC's memory. Small models are quicker but follow instructions less well.
[Choosing a chat model](docs/CHOOSING_A_MODEL.md) shows how seven local models did on Altera's own test.

**Optional pieces** add something, but chat needs none of them:

| Piece | What it adds | What it costs |
| --- | --- | --- |
| Memory and microphone models | Companions keep memories; you can speak instead of type | About 230 MB, downloaded once by `setup.bat --models` |
| [A local voice](docs/VOICE.md#a-local-voice) | Replies read aloud by Kokoro, on your PC | 1 to 2 GB of packages plus about 350 MB of model and voices. On the CPU of the PC it was measured on, a 4-second sentence took about 10 seconds to make. A GPU is opt-in |
| [Pictures](docs/AVATARS.md) | Companions send pictures of themselves in the chat | Needs [ComfyUI](https://github.com/Comfy-Org/ComfyUI) and a strong GPU. On a 32 GB GPU, a picture took about 50 s with Mage-Flow-Edit and 35 s with Qwen-Image-Edit. The heaviest engines need about 29 GB of free video memory |
| [Phone access](docs/ANDROID_TAILSCALE.md) | The same chats on your phone | A free [Tailscale](https://tailscale.com) account |

[NPC Forge](https://github.com/Fel101/fels-npc-forge) paints portraits from descriptions and creates characters with
personalities. You can also upload a picture, import a character card that has one, or use the pal's initial.

Start Voice Lab and choose **Use Voice Lab for speech** in Setup or Settings. Each pal can use a ready Cast or preset
voice from [Voice Lab](https://github.com/Fel101/fels-voice-lab).

## Start in five minutes

For a Windows release, run **Setup.exe** and open **Altera** from the Start menu. Setup lets you choose the
memory and microphone models and the optional Kokoro voice. The browser guides you through choosing a chat model.
[Windows setup and updates](docs/SETUP.md#windows-installer)

If you are running Altera from source, follow these steps.

1. Install [LM Studio](https://lmstudio.ai/download) or [Ollama](https://ollama.com/download).
2. Download this repository and open a terminal in its folder. Run:

   ```powershell
   .\setup.bat --models
   .\start.bat
   ```

3. Open Setup in the browser and choose your provider. If its server is off, choose **Start it for me** when offered.
   With Ollama, you can approve a suggested download there. With LM Studio, open the suggested model's page and
   use LM Studio to download it. Choose **Check again** when it appears.
4. Use a model that answers the short test message, then choose **Finish and make a pal**.
   A new install uses **http://127.0.0.1:8767**, and existing instances keep the port in `config.yaml`.

`--models` downloads the memory and microphone models. Leave it off to set up without them: setup will say they're
missing, and you can run it again later with `--models`. If the model doesn't answer, the first page says what to
start. The same check is in Settings as **Setup check**. [Setup](docs/SETUP.md) covers the other options: offline
installs, exact versions, and another folder for your data (`ALTERA_HOME`). On Linux or macOS, use `sh setup.sh` instead
of `setup.bat` and run `python altera.py --open` with the `.venv` Python instead of `start.bat`.

## What stays on your PC and what leaves it

With LM Studio or Ollama running on this PC, chat replies are generated here. Altera saves your conversations,
memories, notes, pictures and settings in its own folder (`data/` and a few files beside it).

These features can send data to other services when you choose to use them:

- **A cloud model** (OpenAI, OpenRouter or another remote server) receives what you write and the conversation it needs
  to answer.
- **Microsoft's online voices** (edge-tts) receive the text of each reply they read aloud. Speech is off at first, and
  the starter setting is the local Kokoro voice.
- **Speech on a configured server** receives the text it speaks if you explicitly point Voice Lab or Kokoro at another
  computer. Automatically discovered Voice Lab addresses use this PC. Voice Lab speech is opt-in.
- **Character cards** sent from NPC Forge wait in the local Fel's Studio inbox. When you preview or import an embedded
  voice file, Altera sends that file to Voice Lab on this PC. The speaker's stated use terms appear before a cloned voice is imported.
- **Web search and reading web pages** are off at first. When switched on, the search terms and addresses go to those
  sites.
- **Pictures** go to the ComfyUI and vision-model servers you set up. **Picture of us** sends a copy of the photo you
  select to the services shown after you confirm. [Picture of us](docs/AVATARS.md#picture-of-us)
- **Phone access** sends chats, pictures and other pages you open to your phone over your private Tailscale connection.
  [Phone setup](docs/ANDROID_TAILSCALE.md)
- **Setup** downloads Python packages from PyPI. With `--models`, it also downloads the memory and speech models from
  Chroma's model store and Hugging Face.
- **An approved Ollama download** asks Ollama on this PC to fetch the selected model from its registry. The request
  contains the model name. Chats stay on this PC. The LM Studio model link opens its catalog in your browser.
- **Check for updates** asks GitHub for the latest Altera release and identifies the version you're running.
  **Update now** downloads the installer and its checksum. GitHub receives the network address of the request.
  This check runs when you ask for it.

Altera has no account, no telemetry and no automatic fallback to another provider. The files on disk are not encrypted:
anyone who can open Altera's folder can read them, so use full-disk encryption if that matters to you
([Private storage](docs/PRIVATE_STORAGE.md)). **Settings → Backup and restore** makes encrypted, password-protected
backups. See [Backup and restore](docs/BACKUP_AND_RESTORE.md) and
[Remember, correct and forget](docs/MEMORY_CONTROLS.md).

## Content rules

Make fictional adult companions. Altera's core keeps conversation and pictures non-sexual and applies rules against
real-person likenesses, childlike characters and non-consensual content. Image age checks are estimates.
[Responsible use](docs/responsible-use.md) covers voice consent, licences and sharing.
[How Altera keeps things safe](docs/SAFETY.md) explains the checks and their limits.

## Kids mode

Kids mode gives a pal simpler words and child-friendly topics. A parent PIN locks the adult settings, and **Kids**
opens a view with only kids-mode pals. Kids mode adds guardrails. Use parental controls and adult supervision
alongside it. [Set up Kids mode](docs/KIDS_MODE.md)

## Models, voices and checkpoints

None of them is included. Each is downloaded from its own source and keeps its own licence and terms: the chat model
you choose, the memory and speech models, the Kokoro voice, and any image checkpoints. Check them before you share
anything made with them. [Licensing](docs/README.md#licensing) lists what each piece is under.

## Known limits

- **One household, one PC.** Altera runs as a single process for the people on one machine. It is not built to be
  reached from the internet; keep it on `127.0.0.1` and use Tailscale for your phone.
- **Linux and macOS** have a setup script, but most testing has been on Windows.
- **Replies are only as good as your model.** A small model forgets instructions sooner, and a reasoning model takes
  longer to start answering.
- **The local voice is slow on a CPU.** A GPU helps, but has to be switched on, and one GPU build crashed during testing
  ([Voice](docs/VOICE.md#a-local-voice)).
- **Pictures need a strong GPU**, and painting a portrait from a description uses [NPC Forge](https://github.com/Fel101/fels-npc-forge).
- **After an update**, close Altera's other tabs and reload, so the browser picks up the new version.

## Problems and questions

[The user guide](docs/README.md) covers everything in more depth, and its troubleshooting table lists the usual fixes.
To report a bug, open an issue in this repository's **Issues** tab; the form asks for the version (at the bottom of
Settings) and the error from `data/altera.log` with anything personal removed. Never paste your conversations.

Report a security problem privately instead, as [SECURITY.md](SECURITY.md) describes. That includes a way past the age
checks or out of the kids view.

## Development

There is no build step: Altera runs from its Python source, and the interface is plain HTML, CSS and JavaScript.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
node scripts/check_frontend.cjs
```

The tests use a temporary data folder and fake model calls, so they never touch your own data or start a model.
[CONTRIBUTING.md](CONTRIBUTING.md) has the house rules, and [Architecture](docs/ARCHITECTURE.md) explains how the
pieces fit together.

## Licence

MIT. See [LICENSE](LICENSE).
