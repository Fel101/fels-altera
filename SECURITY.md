# Security

## Reporting a problem

Please report security problems privately, not in a public issue. On this repository's GitHub page, open the
**Security** tab and choose **Report a vulnerability**. Describe what you found and how to reproduce it, using
made-up data. Don't send real conversations, memories, pictures or logs that contain them.

This is a free project looked after in spare time, so there is no fixed response time, but security reports are read
first. Only the latest version is fixed; there are no maintained
older releases.

## What Altera is built for

Altera is a local app for one household on one PC. It is not built to be reached from the internet, and it has no user
accounts or passwords of its own. Its protection rests on keeping the people who can't be trusted from reaching it at
all:

- **It listens on `127.0.0.1`.** If the configured host is changed, it still refuses every request that doesn't come
  from this PC's loopback address.
- **Web pages can't use it.** It refuses a request whose Host header isn't the loopback address, so a page that reaches
  it through its own domain name gets nothing back. Any change it is asked to make (a message, a setting, a deletion)
  is refused when it comes from another site.
- **A phone reaches it through [Tailscale Serve](docs/ANDROID_TAILSCALE.md)**, and only for the Tailscale logins you
  list, at the host you configure, over HTTPS.
- **The local voice service** (`voice/serve.py`) listens on `127.0.0.1` only and refuses requests a web page could send
  it.

What it does not protect against:

- **Anyone or anything that can use this PC as you.** Another program running as your user, or a person at your
  keyboard, can open Altera and read its files. The data on disk is not encrypted; only backups are (see
  [Private storage](docs/PRIVATE_STORAGE.md) and [Backup and restore](docs/BACKUP_AND_RESTORE.md)).
- **The services you connect it to.** A cloud model, Microsoft's online voices, a web search provider, or a ComfyUI or
  vision server on another machine receives what Altera sends it, and keeps it under its own terms.
- **Exposing it yourself.** Forwarding its port, putting it behind a public reverse proxy or running it on a shared
  server is outside what it was designed for.

## What counts as a security problem

Please report these privately:

- A way to reach Altera or its voice service from another machine, from a web page, or past the Tailscale login check.
- A way to read or change a person's data without the access described above.
- A way past the fixed safety rules in `core/safety.py`: a picture or a companion that reads as under 18, a request
  about a real person, or the minor tripwire in chat.
- A way out of the kids view: reaching a pal that isn't in kids mode, or Settings, without the parent PIN.
- Secrets or personal data written somewhere they shouldn't be, such as API keys in a log.

These are not security problems, but ordinary issues are welcome:

- A model ignoring its instructions. The prompt guides the model's behavior. Altera applies the fixed rules above separately.
- The vision model getting someone's age wrong, when no fixed rule was bypassed. The age check is an estimate and is
  documented as one. [Avatars and pictures](docs/AVATARS.md#content-rules)
- Problems that need someone who can already use the PC as you, or who knows the parent PIN.
