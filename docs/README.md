# Altera user guide

Altera runs on your PC. You can make a pal for everyday chat, import a fictional character, or write a companion
who keeps notes and memories. A chat model is all you need to start. Voices, pictures and phone access are optional.

## Start chatting

1. Install and start Altera using the [quick start](../README.md#start-in-five-minutes).
2. In **Setup**, choose LM Studio or Ollama if you want replies made on this PC. If the server is off, follow the
   app's instructions or choose **Start it for me** when offered. Setup can suggest a model that fits your PC.
   It asks before downloading one through Ollama. For LM Studio, open the suggested model page and download it there.
3. Choose a model and run the short test. **Listed** means Altera found the model. A test reply tells you it can
   answer. You can run the test again from **Settings → Setup check**.
4. Choose **Finish and make a pal**. On **Home**, give them a name and choose a vibe. Open their chat and say hello.

[First-run setup](SETUP.md#the-first-run) also explains how to skip ahead and set up a model later.

## Choose who to talk to

| What you want | Where to start |
| --- | --- |
| An easygoing chat | **Make a pal**. Choose how they sound and what they remember. [Pals](PALS.md) |
| A character from a file or NPC Forge | **Import a character card** or open the card waiting on Home. [Characters](CHARACTERS.md) |
| A companion with their own background, notes and memories | **Write a companion** or use **Add someone → Meet someone new** with NPC Forge. [Companions](COMPANIONS.md) |
| A chat for a child | Open a pal's **Settings** on Home and choose **Set up kids mode…**. [Kids mode](KIDS_MODE.md) |

## Use chat, memories and saved moments

- **Stop** ends a reply in progress. You can review any partial text. **Regenerate** keeps the old answer until a new
  one is ready. If the browser disconnects, reopen the chat to see how far the reply got.
- **Search** finds words in saved chats. Use date, conversation and speaker filters to narrow the results. Web search
  is a separate, optional feature. It sends search terms to the provider you choose.
- **Remember** saves a fact from a message. Use **Memory** to see where a fact came from, correct it or ask Altera
  to forget it. Corrections and forgetting can stop older chat from being used in future replies while keeping the
  visible conversation. [Memory controls](MEMORY_CONTROLS.md)
- **Our story** saves moments you choose and review. An imagined scene is marked separately from a real exchange.
  Optional reflection can suggest notes based on your messages. [Our story and reflection](OUR_STORY.md)
- **Check-ins** can leave a message from a companion while Altera and your model are running. Open the inbox to read
  or dismiss it, or add it to your chat. [Check-ins and rituals](PRESENCE.md)
- **Reply settings** change how long or varied your replies are. [Reply settings](REPLY_SETTINGS.md)

## Add a voice, a picture or your phone

| What you want | Where to start |
| --- | --- |
| Hear replies | Start Voice Lab and choose **Use Voice Lab for speech**, or install Altera's local Kokoro voice. [Voice](VOICE.md#a-local-voice) |
| Talk instead of type | Allow the microphone in your browser and install the local speech-recognition model. **Voice notes** lets you review a recording and its transcript before sending. [Microphone and voice notes](VOICE.md) |
| Give them a face | Open their settings on Home to add a picture or have NPC Forge paint one. [Portraits](PORTRAITS.md) |
| Ask a companion for a picture in chat | Set up ComfyUI, open a companion with a portrait, and use the camera button. [Avatars and pictures](AVATARS.md) |
| Chat from your phone | Follow the private Tailscale setup on your PC and phone. [Android and Tailscale](ANDROID_TAILSCALE.md) |

## Keep a backup

Open **Settings → Privacy & Data → Backup and restore** to make a password-protected backup. Download a copy to
another drive and keep the password or recovery key separately. Altera's working files and ordinary chat exports
are readable by anyone who can open them. [Backup and restore](BACKUP_AND_RESTORE.md) · [Private storage](PRIVATE_STORAGE.md)

Review drafts when reopening an older tab or after restoring a backup. Your browser can keep a draft offline,
and Altera and the selected model need to be reachable before you can chat again.

[What stays on your PC and what leaves it](../README.md#what-stays-on-your-pc-and-what-leaves-it) lists the features
that send information to another service. [Responsible use](responsible-use.md) covers voice consent, licences and
AI labels. [How Altera keeps things safe](SAFETY.md) explains content checks, Kids mode and their limits.

## Troubleshooting

| If this happens | Try this |
| --- | --- |
| Altera will not start | Run `start.bat` from a terminal to read the error. Check [setup](SETUP.md) for help. |
| The model is missing or gives no reply | Open **Settings → Setup check**. Start the server you chose and select one of its downloaded models. |
| You cannot hear speech or use the microphone | Check the Voice switch, the speech service and your browser's sound and microphone permissions. [Voice](VOICE.md) |
| A memory needs review | Choose **Sources** to see the original message, then correct or forget it, or rebuild automatic memories. [Memory controls](MEMORY_CONTROLS.md) |
| A reply is slow | Open **Reply diagnostics** to see where the wait is. [Reply diagnostics](DIAGNOSTICS.md) |
| You see Backup recovery | Keep your backup folder and follow the on-screen instructions for the marked recovery archive. [Interrupted restore](BACKUP_AND_RESTORE.md#interrupted-restore-and-recovery) |
| You see an older page after updating | Close Altera's other tabs and reopen it. |

When reporting a bug, include the version from the bottom of Settings and an error message from `data/altera.log`
with anything personal removed. Use the [private reporting instructions](../SECURITY.md) for a security problem.
See the [changelog](../CHANGELOG.md) for version changes.

## Technical references

- [Architecture](ARCHITECTURE.md), [API reference](API_REFERENCE.md) and [storage schema](Database_Schema.md)
- [Encrypted backup format](ENCRYPTED_BACKUP_FORMAT.md), [voice transport](VOICE_TRANSPORT.md) and
  [local memory embeddings](LOCAL_EMBEDDINGS.md)
- [Contributing](../CONTRIBUTING.md)

## Licensing

Altera is released under the MIT License: see [LICENSE](../LICENSE). This repository holds only Altera's own code and content. Everything else is installed or downloaded onto your machine from its original source and keeps its own licence and terms.

**Python packages.** What `requirements.txt` installs, including the packages those bring with them, is under permissive licences (MIT, BSD, Apache-2.0, ISC, PSF, ZPL), with three exceptions. edge-tts is LGPL-3.0, certifi is MPL-2.0, and tqdm and orjson contain MPL-2.0 parts. Altera uses all of them unmodified.

**The local voice** (`voice/`, optional). Kokoro and misaki are Apache-2.0 and PyTorch is BSD-3-Clause. For English pronunciation, misaki also installs phonemizer-fork (GPL-3.0-or-later), espeakng-loader (which bundles the espeak-ng library, GPL-3.0-or-later) and num2words (LGPL). These go into `voice/.venv` only and run in the voice service's own process. Altera does not ship them. If you redistribute a `voice/.venv`, the GPL's terms apply to it.

**Models.** None ships with Altera. MiniLM and Whisper are downloaded by `setup.bat --models`, Kokoro and spaCy's English model by `voice\setup.bat`, and Silero comes inside a package:

- Kokoro-82M and its voices: Apache-2.0.
- spaCy's en_core_web_sm, which misaki uses for English pronunciation: MIT.
- The faster-whisper speech-recognition models: MIT.
- all-MiniLM-L6-v2, used for memory search: Apache-2.0.
- Silero VAD, which ships inside the faster-whisper package: MIT.

Chat models and image checkpoints are your choice, so check each one's licence yourself. The workflow in `content/workflows/` names the models it expects but does not include them.

**Online services.** OpenRouter, OpenAI and Microsoft's online voices (edge-tts) are covered by those providers' terms, not by a licence. Using one sends your text to that provider.
