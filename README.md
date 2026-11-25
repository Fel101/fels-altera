# Altera

Altera is a companion app that runs on your own PC. You chat with pals, characters and companions you make yourself,
and the conversations, memories and notes stay in a folder on your machine.

It talks to a chat model you run yourself, in LM Studio or Ollama. If you prefer, it can use a cloud service instead.
Speech, pictures and a phone connection are optional extras.

> **Part of Fel's Studio.** Four free apps that run on your own PC and work together.
> [Voice Slate](https://github.com/Fel101/fels-voice-slate) records voices with consent,
> [Voice Lab](https://github.com/Fel101/fels-voice-lab) designs and clones voices,
> [NPC Forge](https://github.com/Fel101/fels-npc-forge) makes characters for games, and
> [Altera](https://github.com/Fel101/Altera) lets you talk with them. Each one also works on its own.

![Home, with three pals](docs/images/home.png)

![A chat with a pal](docs/images/chat.png)

<img src="docs/images/phone.png" alt="The same chat on a phone" width="300">

*The screenshots show made-up people, with scripted replies standing in for a model.*

## What you need

- **Windows 10 or 11**, where Altera is developed and tested. A `setup.sh` for Linux and macOS is included, but it is
  less tested.
- **Python 3.12** from [python.org](https://www.python.org/downloads/). 3.11 and 3.13 work too, but the optional local
  voice needs 3.12 or older, so 3.12 covers everything. Setup checks the version and won't use any other.
- **A chat model server.** [LM Studio](https://lmstudio.ai) or [Ollama](https://ollama.com) on the same PC is the usual
  choice. vLLM, OpenAI, OpenRouter and any OpenAI-compatible server also work.

Altera itself is light: it runs on the CPU and needs no graphics card. Your hardware matters for the chat model. How
large a model you can run depends on your GPU's memory, or on your RAM if your graphics share system memory. LM Studio
shows which models fit. Small models are quicker but follow instructions less well.

**Optional pieces** add something, but chat needs none of them:

| Piece | What it adds | What it costs |
| --- | --- | --- |
| Memory and microphone models | Companions keep memories; you can speak instead of type | About 230 MB, downloaded once by `setup.bat --models` |
| [A local voice](docs/VOICE.md#a-local-voice) | Replies read aloud by Kokoro, on your PC | 1 to 2 GB of packages plus about 350 MB of model and voices. On the CPU of the PC it was measured on, a 4-second sentence took about 10 seconds to make. A GPU is opt-in |
| [Pictures](docs/AVATARS_AND_ADULT_MODE.md) | Companions send pictures of themselves in the chat | Needs [ComfyUI](https://github.com/Comfy-Org/ComfyUI) and a strong GPU. On a 32 GB GPU, a picture took about 50 s with Mage-Flow-Edit and 35 s with Qwen-Image-Edit. The heaviest engines need about 29 GB of free video memory |
| [Phone access](docs/ANDROID_TAILSCALE.md) | The same chats on your phone | A free [Tailscale](https://tailscale.com) account |

[NPC Forge](https://github.com/Fel101/fels-npc-forge) paints portraits from descriptions and creates characters with
personalities. You can also upload a picture, import a character card that has one, or use the pal's initial.

Start Voice Lab and choose **Use Voice Lab for speech** in Setup or Settings. Each pal can use a ready Cast or preset
voice from [Voice Lab](https://github.com/Fel101/fels-voice-lab).

## Start in five minutes

### With LM Studio

1. Install [LM Studio](https://lmstudio.ai), download a chat model in it, and load the model.
2. Turn on its local server (in the Developer view, or run `lms server start`). It listens on port 1234.
3. Download this repository and open a terminal in its folder. Then run:

   ```powershell
   .\setup.bat --models
   .\start.bat
   ```

4. A fresh install opens in your browser at **http://127.0.0.1:8767**. It asks for your name and lists the models LM Studio
   serves. Pick one, and it sends one short test message to check the model answers. Then choose **Make a pal**.
   Altera keeps the port in an existing `config.yaml`, including `5001`.

### With Ollama

1. Install [Ollama](https://ollama.com), then get a model: `ollama pull llama3.2`. On Windows and macOS, Ollama runs in
   the background once installed. On Linux, run `ollama serve`.
2. Run `.\setup.bat --models` and `.\start.bat` as above. On the first page choose **Ollama** as the provider, then pick
   the model.

`--models` downloads the memory and microphone models. Leave it off to set up without them: setup will say they're
missing, and you can run it again later with `--models`. If the model doesn't answer, the first page says what to
start. The same check is in Settings as **Setup check**. [Setup](docs/SETUP.md) covers the other options: offline
installs, exact versions, and another folder for your data (`ALTERA_HOME`). On Linux or macOS, use `sh setup.sh` instead
of `setup.bat` and run `python altera.py --open` with the `.venv` Python instead of `start.bat`.

## What stays on your PC and what leaves it

With LM Studio or Ollama, your chats never leave your PC. Your conversations, memories, notes, pictures and settings are
kept in Altera's folder (`data/` and a few files beside it).

These are the only things that send anything out, and each one is a choice you make:

- **A cloud model** (OpenAI, OpenRouter or another remote server) receives what you write and the conversation it needs
  to answer.
- **Microsoft's online voices** (edge-tts) receive the text of each reply they read aloud. Speech is off at first, and
  the starter setting is the local Kokoro voice. The cloud voice is never used while adult mode is on.
- **Speech on a configured server** receives the text it speaks if you explicitly point Voice Lab or Kokoro at another
  computer. Automatically discovered Voice Lab addresses use this PC. Voice Lab speech is opt-in.
- **Character cards** sent from NPC Forge wait in the local Fel's Studio inbox. When you preview or import an embedded
  voice file, Altera sends that file to Voice Lab on this PC. The speaker's stated use terms appear before a cloned voice is imported.
- **Web search and reading web pages** are off at first. When switched on, the search terms and addresses go to those
  sites.
- **Pictures** go to the ComfyUI and vision-model servers you set up. Those are usually local, but not necessarily.
- **Setup** downloads Python packages from PyPI. With `--models`, it also downloads the memory and speech models from
  Chroma's model store and Hugging Face.

Altera has no account, no telemetry and no automatic fallback to another provider. The files on disk are not encrypted:
anyone who can open Altera's folder can read them, so use full-disk encryption if that matters to you
([Private storage](docs/PRIVATE_STORAGE.md)). **Settings → Backup and restore** makes encrypted, password-protected
backups. See [Backup and restore](docs/BACKUP_AND_RESTORE.md) and
[Remember, correct and forget](docs/MEMORY_CONTROLS.md).

## Content rules

Conversation and pictures stay non-sexual. An optional adult mode, for adults only, is off and out of sight by default;
[Avatars and adult mode](docs/AVATARS_AND_ADULT_MODE.md#adult-mode) explains it.

Some rules hold whatever is switched on. They are written in the code (`core/safety.py`), so editing the settings or
the prompt cannot loosen them:

- No minors. Every companion must be 18 or older. A request or description that points at a child, a teenager or an age
  under 18 is refused, and a sexual message that points at a minor gets a fixed refusal without reaching the model.
- No real people. Requests for a celebrity, a look-alike or a deepfake are refused.
- Nothing non-consensual.
- **Every picture is age-checked.** A vision model estimates the age of the person in each picture, and a picture that
  reads as under 18 is discarded. A more revealing picture is shown only if the check ran and passed.

The age check is a model's estimate, not proof: it can be off by several years either way. The word lists catch the
obvious phrasings, not every phrasing. Both are guardrails behind each other, not a guarantee. The same page gives the
details and the measured results.

## Kids mode

Kids mode makes a pal a better fit for a child: simpler words, child-friendly topics, and a fixed reply when a message
touches a topic such as self-harm, sex, drugs, violence or personal details. A parent PIN locks it, and a **kids view** lets a child's
tablet reach only kids pals. The server refuses every other page in that view, so typing a web address doesn't get
around it.

**Kids mode is a guardrail, not parental-control software.** It has not been tested or approved as one, its topic
lists will miss things, and the model can still get things wrong. An adult needs to keep an eye on things. Anyone who
knows the PIN, or can open the files on the PC, can undo it. See [Kids mode](docs/KIDS_MODE.md).

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
