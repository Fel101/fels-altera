# Voice startup and HTTP transport

## Quiet backchannels

In **Settings > Voice Layer**, quiet backchannels are off by default. **Prepare a voice clip** explicitly sends the fixed phrase “Mm-hmm.” to the configured speech service; as with ordinary speech, that may be a configured remote server or Microsoft cloud voice. No conversation text is used for this clip. Preview it, then turn on the separate quiet-backchannel switch. The prepared clip uses Altera's ordinary speech cache, which is retained separately from chat messages and can expire or be cleared; prepare it again after expiry or a voice change. This browser's setting and cache reference are not included in portable app backups.

When voice output is enabled, at most one short, low-volume clip may play after three seconds waiting for the first reply text, with at least a minute between automatic clips in the same page. It is not added to a reply or remembered as conversation. Reply text or speech, Stop, a microphone or voice-note action, switching companions, muting, hiding the page and private-page closure stop the sound or cancel its timer. Browser autoplay rules can still block it. Tests use simulated audio and do not establish the quality of a real voice clip or microphone/echo behavior.

Kokoro uses the configured `voice.kokoro.url`, defaulting to `http://127.0.0.1:8770`. An explicitly configured remote endpoint receives the speech text; its label says **configured server** rather than **local**. The separate Microsoft voice and its opt-in cloud fallback retain their existing controls.

Voice Lab uses `voice.voicelab.url` or `VOICE_LAB_URL` if supplied, then the suite address file and local port 8765.
The health answer must identify Voice Lab before a voice list or speech request is sent. A remote address is used
only when it is explicitly configured. It receives the text that is spoken. Set `voice.voicelab.voice` to a ready name
to choose the default. Altera lists ready voices with `GET /api/voices` and requests WAV with `POST /v1/audio/speech`.
The response is cached by Altera. Voice Lab processes the speech without keeping a Cast take.

## Starting and observing

Ordinary app startup can start Kokoro when its configured autostart is enabled. An actual speech request can queue a bounded startup retry when the service is unavailable. Reading `/api/voice/status`, including HEAD, checks availability without launching or queueing a service; repeatedly opening a status panel cannot restart it. A service that is down can stay down until the next speech request or configured app startup.

Altera only starts a service for a configured loopback endpoint and existing interpreter/script. Existing independent speech servers can answer requests but are never adopted or stopped. Once Altera begins stopping its owned service, startup remains fenced until that Altera process restarts.

## Configured recipient and response limits

Both metadata and speech use a fresh direct HTTP(S) opener. Ambient HTTP/HTTPS/ALL proxy settings and a globally installed urllib opener are ignored. Redirects are refused, including redirects to the same host: configure the final base URL deliberately. HTTPS retains certificate verification. Base paths such as `https://example.invalid/speech` are supported; embedded credentials, query/fragment components, control characters, invalid ports and other URL schemes are refused.

Kokoro metadata is limited to 64 KiB. Voice Lab health is limited to 128 KiB and its voice list to 2 MiB. Each speech
response is limited to 16 MiB before any cache file is published. Invalid lengths and truncated responses are refused.
Voice Lab WAVs must also have a readable format and complete frames.

Provider errors report a fixed HTTP status message. Their response bodies, redirect locations, connection URLs and internal paths are not included in that error. A malformed, redirected, oversized or HTTP-error response is a speech failure and does not trigger Microsoft fallback. An unreachable service can use fallback only under the existing explicit cloud setting and content controls. Saved text remains available if speech fails.

## Verification and remaining work

Thirty-eight new synthetic loopback cases cover direct recipient selection despite ambient proxies/global openers, GET/POST refusal of five redirect codes without a second recipient, declared/chunked/undeclared oversize, malformed lengths, invalid URL components, error-body closure/redaction, truncated replies, no cache publication or cloud fallback after invalid output, remote labeling and GET/HEAD status without startup. Final voice/ownership checks passed **117 tests**; the broader affected run passed **204 tests** before the final two label cases, with one existing OpenTelemetry warning. Initial fixture assertions were corrected to inspect the dedicated cache directory and exercise an actual truncated peer response rather than an invalid request limit.

Complete Python regression: **2,080 passed, 11 platform skips, one existing OpenTelemetry warning in 424.90 seconds**, actual pytest exit 0. `data/verify-voice-transport-20261105.py` verified the receipt, exact result counts, current bindings and empty wrapper stderr; known launcher/wrapper/pytest PIDs were subsequently absent. Documentation checks passed **9 tests in 3.72 seconds** after final guide updates, configured Ruff and new-test lint passed, dependency checks found no broken requirements and whitespace checks passed. Frontend source is unchanged since the preceding 600-pass run; no new frontend/browser/native-storage acceptance is claimed.

External server storage, engine descendants, full native filesystem coverage, actual microphone/TTS quality and device acceptance remain unfinished. No installed speech engine, model or microphone is used in these tests. No dependency is installed or upgraded, live Altera restarted, personal data migrated or source published. The full companion roadmap remains active, with local Git only.

For a compatible service you start yourself, set `voice.kokoro.url`. To let Altera start it, configure
`voice.kokoro.python` and `voice.kokoro.script`. The service needs `GET /api/info` (`{"loaded": ["kokoro"]}` once
ready) and `POST /api/speak` (JSON `{"text", "voice", "speed"}` with a WAV response). An Altera-owned service also
accepts `--port`, `--idle-minutes`, `--preload` and `--parent-pid`.

[Voice guide](VOICE.md) · [API](API_REFERENCE.md)
