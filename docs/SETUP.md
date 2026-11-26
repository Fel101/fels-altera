# Set up Altera

## Windows installer

Run the release's **Setup.exe** and choose where to install Altera. It includes Python 3.12 and the packages it needs
for text chat. On the task page, choose whether to download the memory and microphone models or the local Kokoro
voice. The voice is unticked when Setup finds a Voice Lab installation. Open **Altera** from the Start menu.

The installed app keeps chats, settings and backups in the `instance` folder inside its installation folder.
If you use a separate source checkout, its data stays in its own folder.

Open **Settings → Check for updates** on the PC running Altera. **Update now** downloads the Windows installer and
checks its SHA-256 checksum. You can stop the download. When it's ready, choose **Open Setup and restart**. Altera
closes, Setup opens, and Altera reopens after you finish. In the same panel, **Undo** opens the previous installed
version using your existing chats and settings. Save a backup before moving between major versions.

The release supplies `SHA256SUMS` with the installer. To check a downloaded copy, run
`Get-FileHash -Algorithm SHA256 .\FelsAltera-Setup-1.0.0.exe` and compare the value for that file.
With GitHub CLI installed, use `gh attestation verify FelsAltera-Setup-1.0.0.exe --repo Fel101/fels-altera` to check its build
record.

## Set up from source

Use an installed **Python 3.11, 3.12 or 3.13**; the tests run on all three. Choose 3.12 if you can: the optional [local voice](VOICE.md#a-local-voice) runs on 3.12 or older, so one Python then covers both. Setup does not install Python, a model server or a speech service, and it downloads model weights only when asked to with `--models` (below). Obtain Python separately from [python.org](https://www.python.org/downloads/) or your operating system's package manager.

On Windows, run `setup.bat`. On Linux or macOS, run `sh setup.sh`. Both wrappers find the script beside themselves and can be called from another working directory. They prefer an existing project environment; otherwise they look for Python 3.12, then 3.13, then 3.11 (`py -3.12` on Windows, `python3.12` elsewhere), and finally plain `python` or `python3`, whose version setup checks itself. `ALTERA_SETUP_PYTHON` can name an explicit bootstrap interpreter. The equivalent Python command is:

```text
python3.12 -I -B /path/to/altera/scripts/setup_altera.py
```

An existing `.venv` keeps the Python it was made with. To move it to another version, delete the `.venv` folder and run setup again. Your settings and data are not inside `.venv`, so they are not touched.

Running setup explicitly creates a missing `.venv` **inside the source checkout**, installs declared Python dependencies from official PyPI, and copies missing portable starter files. It never starts Altera or performs inference. Python dependencies include the declared CPU inference libraries; this does not install an LM Studio/Ollama server, select a model, or download its weights.

## The memory and microphone models

Two models run inside Altera itself, and Altera never downloads them on its own:

- **MiniLM** (all-MiniLM-L6-v2), which companions need to keep memories.
- **Whisper**, the size set in `voice.stt_model` (`base` unless changed), which the microphone needs.

At the end of every run, setup says whether each one is there. To download the missing ones once (about 230 MB):

```powershell
.\setup.bat --models
```

```sh
sh /path/to/altera/setup.sh --models
```

MiniLM comes from Chroma's own model archive and is checked against the SHA-256 that Chroma pins. It goes to
`~/.cache/chroma/onnx_models/all-MiniLM-L6-v2/onnx`, where Altera reads it. Whisper comes from Hugging Face into its
usual cache. If `memory.embedding_model_path` or `voice.stt_model` names a folder of your own, setup does not download
into it and says what is missing instead. `--models` cannot be combined with `--offline` or `--check`. Text chat works
without either model.

Existing `config.yaml`, `config.local.yaml`, `.env`, personality, provider selection, data, backups, and model caches are preserved. Setup does not open the conversation/vector stores or unlock private storage. Setup and first startup create the starter config and copy the personality file when needed for a separate instance.

## Check before making changes

```powershell
.\setup.bat --check
.\setup.bat --dry-run
```

```sh
sh /path/to/altera/setup.sh --check
sh /path/to/altera/setup.sh --dry-run
```

`--check` inspects the environment's Python version, installed runtime requirements, `pip check`, and readable YAML mappings. It does not create an environment, install packages, copy files, contact providers, or launch services. Missing starter files are reported as files that setup/startup would create. A missing environment or unhealthy dependency state returns a nonzero exit status.

`--dry-run` describes the setup actions without changing files or packages, and performs no provider requests. These checks confirm dependency/configuration readiness only; they do not establish successful database unlock, microphone/speech quality, model inference, or safe production activation.


## Existing environments and exact versions

Normal setup keeps every already installed package version constrained, including transitive dependencies. It adds missing packages only if they can coexist with those versions. It uses no `--upgrade`, package-manager replacement, or automatic Python/runtime update.

If the environment has incompatible packages, review the reported package names first. Replacement requires a separate explicit invocation:

```powershell
.\setup.bat --allow-package-changes
```

This permits pip to replace conflicting Python packages within this project's `.venv`; it still does not upgrade everything or modify configuration/data. To request the repository's recorded exact environment instead of the runtime version floors:

```powershell
.\setup.bat --locked --dry-run
.\setup.bat --locked --allow-package-changes
```

`--locked` selects `requirements.lock.txt` explicitly. The recorded lock is a known environment, not a claim that wheels exist for every operating system or CPU. It was recorded with Python 3.11 on Windows; on Windows, its wheels also resolve for 3.12 and 3.13. Installs require binary wheels and do not invoke source builds or download a compiler. Failed setup leaves an existing environment in place; inspect a partial/incomplete `.venv` before repairing it. Setup never deletes and recreates one automatically.

## Offline installation

```powershell
.\setup.bat --offline --wheelhouse C:\prepared-wheels
```

```sh
sh /path/to/altera/setup.sh --offline --wheelhouse /prepared-wheels
```

Prepare all direct and transitive dependency wheels separately for the same Python version, operating system, and architecture. Offline setup uses `--no-index` with only that local directory; a pip download cache alone is not a wheelhouse. It does not download missing wheels, model weights, or a runtime. An already healthy environment can use `--check --offline` without a wheelhouse. Missing wheels produce a failure with settings and saved data preserved.

## Another instance directory

Set `ALTERA_HOME` to an absolute directory, or pass `--home` during setup:

```powershell
$env:ALTERA_HOME = 'D:\Altera-private-instance'
.\setup.bat
```

```sh
ALTERA_HOME=/absolute/private-instance sh /path/to/altera/setup.sh
```

The environment remains beside the source checkout. Keep the same absolute `ALTERA_HOME` when starting Altera later; `--home` does not change a persistent environment variable or migrate existing data. Existing files in that instance are kept byte-for-byte.

## The first run

The first time Altera opens on a fresh instance it asks for a name, sends a tiny test message to the selected model and shows which optional pieces are up (speech, NPC Forge, ComfyUI). It then ends on Home, where **Make a pal** needs nothing but the model.

A fresh instance has no model chosen. Choose your provider and a model it serves. The cloud providers offer a default model you can change.

- **If the local server is off**, Setup explains how to open LM Studio or Ollama. **Start it for me** asks first, then starts an installed provider at its usual local port.
- **If Ollama has no models**, Setup can suggest one based on your graphics memory and RAM. Its button shows the approximate download size. Confirm to download and use the model. **Stop download** closes Altera's download request. Ollama can reuse partial files when you try again.
- **If LM Studio has no models**, open the suggested model's page and use LM Studio to download it. Back in Altera, choose **Check again** and select the model.

The estimates leave some memory for Windows and the model's work. The [model catalog](../content/model_suggestions.yaml) lists the suggestions and their licences. [Qwen3 download](https://ollama.com/library/qwen3:4b-instruct-2507-q4_K_M) · [Granite download](https://ollama.com/library/granite4:micro)

The test message is the only reply Altera generates without a conversation: it is a fixed `Reply with the single word OK.` prompt, labelled as a test on the page, and nothing a person typed is included. If the provider is missing, down, misconfigured or silent, the page says what to start and how for LM Studio and Ollama. Nothing there blocks finishing: **Skip for now** is remembered, and the same check is in Settings as **Setup check**.

## Optional provider metadata readiness

After setup, you may explicitly ask for a bounded **GET of the selected provider’s configured model-list endpoint** (normally `/models`):

```powershell
.\setup.bat --check --probe-models
```

This uses the selected provider and existing credentials, follows no redirects, ignores proxy environment settings, reads at most 64 KiB, and has a bounded helper timeout. It sends no conversation, generates no reply/embedding, changes no model selection, and loads/unloads no model. Only loopback endpoints are permitted unless you additionally specify `--allow-network-provider`, which explicitly approves the configured external provider's metadata request. `--offline` refuses metadata requests.

A listed model is metadata evidence only. An unavailable provider means you should start your existing server separately and check its endpoint/credentials; setup never starts it or enables a fallback provider. A missing selected model means choose the model your server actually serves in Settings. Credentials and raw configuration are not printed by this check.

## Reasoning models

A model that thinks before it answers (Qwen3, DeepSeek-R1 and the like) works, with three things handled for it:

- Now and then such a model answers with its thinking alone, or stops at once. Altera asks once more before anything is
  shown; if the second answer is empty too, the chat says the model sent back an empty reply, and your message stays saved.
- Mood analysis after each message gives the model room to think (2,248 tokens rather than 200) and reads its JSON past
  the thinking. It runs in the background once the reply is saved, so the next message never waits for it; a model that
  does not answer leaves the mood to the keyword fallback.
- The picture age check, describing a character for a portrait and the other background helpers already leave room for
  thinking and remove it from the answer.

A reasoning model is slower to start replying than one that does not think. If the model you use serves both, pick its
non-thinking variant in LM Studio for quicker replies.

When dependency checks pass, start Altera deliberately using its `.venv` Python and the same instance directory. On Windows, the existing `start.bat` opens Altera; the Python entry point is `altera.py`. Keep real inference, speech/microphone acceptance, storage recovery, and production activation as separate checks.
