# Android and Tailscale

Altera runs on the PC. Your phone opens the same companions, conversations, pictures and workspace tools over a private HTTPS connection. The PC must remain awake, with Altera and the selected model provider running. Local speech services also run on the PC.

## Open Altera on the Phone

1. Connect Tailscale on the PC and Android phone using the owner's account.
2. Start Altera on the PC with `start.bat`, or run `.\.venv\Scripts\python.exe altera.py` from the project folder. If Altera is already running, use that process.
3. Start the local API server in LM Studio when that is the selected provider, and make the configured chat model available.
4. Open the HTTPS address printed by `tailscale serve status` in Chrome on Android. Include Altera's port, normally `:8443`.
5. In Chrome's menu, choose **Add to Home screen** or **Install app** when offered.
6. Tap the microphone and allow microphone access. Tap again to finish recording. Audio is uploaded to Altera for transcription; a local voice provider plays replies through the phone.

The header checks Altera's reachability on opening, returning to the app and every 15 seconds while visible. It shows **Reconnecting...** when the phone still has internet but cannot reach Altera, and recovers when access returns. Messages interrupted while streaming are not automatically resent.

## One-Time Setup on the PC

Leave `app.host` set to `127.0.0.1` and debug mode off. New installs use port `8767`. If your `config.yaml` uses `5001` or another port, use that port as the Serve backend. Both devices must be connected to the same tailnet, and its access rules must permit the phone to reach the PC's HTTPS port.

Put the remote settings in `config.local.yaml` (git-ignored), replacing these example values with the PC's Tailscale hostname and the owner's Tailscale login:

```yaml
remote_access:
  enabled: true
  tailscale_host: your-pc.your-tailnet.ts.net:8443
  allowed_logins:
    - owner@example.com
```

`tailscale status --json` contains the PC's `Self.DNSName` and the owner's entry in `User`. The host above has no scheme or path; omit the DNS name's trailing dot. The login is the Tailscale account identity, not a new Altera password. These settings remain in the local file when Altera saves other settings.

Configure a dedicated HTTPS endpoint:

```powershell
tailscale serve --bg --https=8443 http://127.0.0.1:8767
tailscale serve status
```

Tailscale handles the HTTPS certificate. Use the full `https://...ts.net:8443/` address on the phone so microphone and home-screen browser features are available. This setup uses **Serve**, which is private to the tailnet. Existing endpoints on other ports are retained. No router port forwarding or public Funnel endpoint is needed.

Serve supplies the `Tailscale-User-Login` identity header. Altera accepts remote requests only for an allowed login, the configured host and HTTPS proxy connection. Identity headers must only be trusted with the backend listening on loopback. Tagged devices without a user identity are denied. Local `localhost`, `127.0.0.1` and `::1` browser access continues to work.

Remote access is refused when the remote settings are disabled or missing, even if the Serve endpoint is still configured.


## Stop or Restart

To disable only Altera's phone endpoint:

```powershell
tailscale serve --https=8443 off
```

To enable it again, rerun the setup command. Avoid `tailscale serve reset`, which removes other apps' endpoints too.

Altera itself runs separately from Tailscale. If launched in a terminal, stop it with Ctrl+C there and start it again with `start.bat`. Application and voice child logs rotate in `data/altera.log`. Any additional stdout/stderr redirect files depend on how you launch the process. A PC restart stops Altera unless you separately arrange startup. The `--bg` Serve configuration persists, but it cannot start Altera or the model provider.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Address does not load | Both Tailscale clients connected, correct hostname and port, PC awake, Altera running, tailnet access rules allow the port |
| Access denied (403) | Phone uses an allowed Tailscale login, hostname/port match `tailscale_host`, Serve HTTPS proxy is used |
| Page opens but chat fails | Selected provider is running; for local LM Studio, its API server is listening on `127.0.0.1:1234` and the configured model is available |
| Microphone unavailable | HTTPS address, Chrome microphone permission and Android microphone permission |
| No voice | Altera's voice toggle, phone media volume and PC voice-service status; Android may require a tap before playing audio |
| Reconnecting while internet works | Tailscale may be disconnected, the PC asleep or Altera stopped; the app probes Altera rather than relying only on internet status |
| Old interface after an update | Reopen the app and reload; Altera now registers `/sw.js` with root scope and removes its older `/static/` registration |

Workspace reads and writes still act on the PC, with writes confined to `workspace/`. Phone file/photo uploads, Android notifications and background voice conversations are separate capabilities; Tailscale alone does not add them.

References: [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve), [Serve CLI](https://tailscale.com/docs/reference/tailscale-cli/serve), [browser microphone requirements](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia).
