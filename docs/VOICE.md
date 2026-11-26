# Voice conversations and saved voice notes

The chat microphone transcribes and sends a message in the current conversation. **Voice notes** saves a recording first; upload, transcription, transcript review and Send are separate actions.

Voice settings show whether your speech service and microphone model are ready. If the microphone model is
missing, use [Setup](SETUP.md#the-memory-and-microphone-models) to install it. You can still use the microphone
button to interrupt speech. [How speech connects to its service](VOICE_TRANSPORT.md)

## A local voice

Altera can speak with Voice Lab, its own Kokoro service, or Microsoft's online voice. For Voice Lab, start the Lab with
a ready voice and choose **Use Voice Lab for speech** in Setup or Settings. In a pal's settings, pick a Cast or preset
voice, check its licence badge, and use **Play a sample** to hear it. Altera keeps that pal's voice choice when the Lab
is closed. If the Lab is unavailable, Altera says so and uses a local Kokoro service if it is ready. If a previously
enabled Kokoro service needs to start, a later reply can use it once it is ready.

Altera's own Kokoro is a small service in
`voice/`, running [Kokoro](https://huggingface.co/hexgrad/Kokoro-82M) (Apache-2.0). It is optional and installed
separately, because it needs PyTorch, which Altera's own environment does not have.

1. Have Python 3.10, 3.11 or 3.12 (Kokoro does not support newer versions yet). The Python 3.12 the
   [README](../README.md#what-you-need) recommends for Altera is the one `voice\setup.bat` uses.
2. Run `voice\setup.bat` on Windows or `sh voice/setup.sh` elsewhere. It makes `voice/.venv`, installs Kokoro and PyTorch
   from PyPI (about 1 to 2 GB), downloads the model and eight voices from Hugging Face (about 350 MB), and installs
   spaCy's small English model (about 13 MB, from GitHub), which Kokoro's pronunciation needs. That is the only time
   it goes online.
3. In Settings, switch **Voice** on. The first time, that also turns speech on in your instance's `config.yaml`
   (`voice.tts_enabled` and `voice.kokoro.autostart`) and starts the service. From then on Altera starts it when it
   starts and stops it when it exits. Switching Voice off again changes only your own preference.

Microsoft's online voice sends spoken text to Microsoft and is selected in `config.yaml` with
`voice.tts_engine: edge` and `voice.tts_enabled: true`. Microsoft speech is available under the core content setting.

Kokoro runs on the CPU by default. Speech can start a few seconds after the text. A GPU is optional. Set
`ALTERA_VOICE_DEVICE` to `cuda` before starting Altera if you have a working GPU build of PyTorch. If it crashes,
switch back to the CPU. To install more voices, run
`voice\.venv\Scripts\python.exe voice\serve.py --download --voices af_aoede,bf_lily` and choose one in Settings.

If the voice status says **not installed**, follow the setup steps above. For another compatible service, see
[Voice startup and transport](VOICE_TRANSPORT.md).

## Interrupting a reply

Click the chat microphone while Altera speaks to silence that reply and begin recording immediately. Late clips stay silent. The new transcript waits for the previous reply to settle; uncertain delivery, changed context or existing composer text leaves it for review. Stop preserves text already saved, including when speech is still being synthesized. A native synthesizer may finish its current file, under its existing writer lease; queued synthesis is cancelled and cancelled clips are not emitted.

Speech workers retain owned thread lifetime through final cleanup. Interrupting a reply returns promptly; a snapshot can still wait for the native writer to finish safely.

Settings → **Interrupt when I speak** enables automatic interruption for this tab only. The microphone indicator remains visible in chat when Settings closes. Monitoring opens the microphone only while Altera speaks and sends short PCM frames to Altera's server. Its installed Silero detector runs on the CPU without transcription, audio retention, cloud use or model downloads.

Two consecutive positive frames trigger interruption. The same microphone stream continues recording, with pre-roll preserving the beginning of your speech. Press Stop when finished; recordings are capped at five minutes. Leaving the page or changing companion ends automatic recording for review and turns monitoring off. A transcript already being transcribed or waiting for cancellation also becomes review-only when you switch away.

Use headphones to reduce speaker echo. Automatic interruption requires AudioWorklet, microphone permission and functioning echo cancellation. Unsupported browsers, device/detector failures, unavailable connection or stale context disable it visibly; use the microphone button instead. The option does not survive reload or automatically re-enable on return. Phone microphone access needs a secure origin; see [Android and Tailscale](ANDROID_TAILSCALE.md).

If microphone recording cannot start, Altera distinguishes blocked permission, a missing or busy device, and an unsupported recording format when the browser reports those conditions. For other setup errors, check the microphone and browser before trying again.

The chat microphone normally transcribes immediately without saving its audio as a voice note. If transcription fails or returns no clear speech, Altera offers to save the original in this browser for review. Cancel discards it. Open **Voice notes** to inspect or download the original. **Upload** stays disabled for the original. Use **Copy recording to current context** and confirm the conversation to enable upload on the new copy. Transcribing, reviewing and sending remain separate actions. If browser saving fails, the recording is available for download or another save attempt while the page stays open. If the voice-note interface itself is unavailable, record again.

## Saving and sending a voice note

Choose **Open voice notes** beside the chat microphone. Record, then **Stop and save recording**. The original is committed to this browser's IndexedDB before Altera says it is saved. It survives reload, including offline. Limits are ten local notes and 100 MiB per browser, and 20 MiB/five minutes per note. Recording formats depend on browser support.

1. **Upload recording to Altera** copies it to Altera's PC. The browser checks the returned hash and byte count and keeps its original. Uncertain retries reuse the same note ID.
2. **Transcribe locally** explicitly starts transcription with the configured `voice.stt_model` and its cached local Whisper weights and tokenizer. The model defaults to `base` if the setting is absent. Models do not download automatically: `setup.bat --models` (or `sh setup.sh --models`) downloads the configured size once. An incomplete local model without `tokenizer.json` is refused before the library can try its online tokenizer fallback.
3. Edit the transcript if needed and **Save transcript edit**. Each edit creates a revision and invalidates earlier review.
4. **Confirm reviewed revision**, then **Send reviewed note**. One immutable durable chat request owns that note. Repeated/retried sends retrieve that request rather than creating another turn. Delivery errors remain visible.

Altera checks an uploaded recording against its saved size and hash before playback, transcription and acknowledging a matching upload retry. If the server copy is missing or damaged, its warning appears in the note, transcription is disabled, and a browser original remains available to play or download. Keep that original and explicitly copy it to the current context or make a new note; the damaged server file is not silently replaced. Downloads use the recorded format's extension, including `.wav` for interrupted WAV audio; an unknown format uses `.bin`.

Reply text appears in chat. Completed speech clips are copied into note storage before the reply worker releases ownership, even without an open browser. Use the note's controls to play them later; voice-note replies do not autoplay. Missing/failed retention is labelled while saved text remains available. Existing voice settings determine whether speech is produced.

Retained reply clips are checked against their saved content hash before listing or playback. A missing or damaged clip is omitted and a warning is shown; the saved reply text remains available. A damaged file is preserved for inspection, and a matching cached copy can restore a missing file without changing the saved audio's identity. This checks against the original retained bytes, not whether the first source was the intended speech; external changes while a file is being read remain outside Altera's single-process coordination.

Closing a dialog stops recording/polling. Switching away stops and saves a note without uploading or sending. Page unload can interrupt an asynchronous browser write: wait for the saved acknowledgment before closing. Storage failure leaves the in-memory original downloadable and warns before closing. Stopping recording alone is not a persistence acknowledgment.

## Ownership, correction and recovery

Recordings capture their session, companion, dataset identifier, knowledge revision and recall floor. Upload and subsequent actions recheck them. Forget, correction, message edits, restored data or reassigned conversations cannot silently send a stale recording into fresh context. Offline recordings without approved context need explicit review after reconnecting.

**Copy recording to current context** requires confirmation and a new note ID. The original stays unchanged; the copy must be uploaded, transcribed and reviewed again. This is an explicit user assertion, never an automatic retry across a recall boundary.

Recordings, transcripts and reply clips use unencrypted PC/browser storage. Delete notes explicitly to remove copies; failed removal can require retry. Deleting a linked message removes its server attachment too. Session deletion, companion removal and Clear data clear the relevant server recordings. Corrections and Forget invalidate queued use; original visible recordings, downloads and backups remain until managed separately. Other browsers/devices retain their own originals.

Server backups include uploaded note rows and files. Restored notes become stale and never resume transcription or sending automatically. Browser-only recordings are not included in server backups; download originals before clearing browser storage or moving devices.

[User guide](README.md) · [API](API_REFERENCE.md) · [Memory controls](MEMORY_CONTROLS.md) · [Backup and restore](BACKUP_AND_RESTORE.md)
