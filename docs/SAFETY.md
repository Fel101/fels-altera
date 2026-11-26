# How Altera keeps things safe

This page describes the checks Altera enforces and the limits of those checks. To report a security problem, use
the private reporting instructions in [SECURITY.md](../SECURITY.md).

## Content checks

The core asks the chat model to keep conversation non-sexual and blocks requests for sexual or revealing pictures.
Altera applies fixed checks in `core/safety.py` independently of model instructions, user settings and add-ons.

- Companion descriptions must describe fictional adults. The checks reject clear references to minors or childlike
  characters, real people's likenesses and non-consensual content.
- A sexual message that triggers the minor check receives a fixed refusal before it reaches the chat model.
- Imported character descriptions, greetings and lorebook entries are checked. A card's instructions remain subject
  to the same rules.
- These text checks recognise known names, words and phrasings. The model can still miss instructions or produce
  inappropriate text.

## Image checks

Altera asks the configured vision model to estimate apparent age. That service receives the image and may keep it.
Estimates can be wrong by several years. The parser rejects implausible or non-finite ages and rounds fractional ages
down. Altera discards images that read as under 18. Where an add-on allows more revealing images, a passing check
is required before Altera saves or serves the image.

An everyday picture, imported portrait or imported living-portrait frame can be marked unchecked if the vision model
is unavailable. Everyday gallery pictures can be checked again during reuse, and living frames during the next sync.
For unchecked pal and character portraits, opening Home, chat or settings can queue a background recheck. One adult
reading passes an unchecked portrait. Removing a portrait already in use requires two under-18 readings, with a
third look to settle a split. If a portrait is removed, Altera shows a previously passed card portrait or the
companion's initial. With no verdict, the next portrait recheck can run after ten minutes.

**Picture of us** is a narrow exception for a self-attested adult photo. Before starting, Altera checks the
confirmation of ownership, age and permission for the displayed recipients. A change of provider requires
another review. Once accepted, the job keeps those reviewed destinations. The vision model must give a passing
result for one apparent adult in the reference and two in the result. An uncertain check blocks delivery.
This checks apparent age and subject count by model estimate. The person supplying the photo attests to identity
and consent.

Altera keeps the original on the device and holds a PNG copy stripped of metadata in process memory while the job
waits. This input is capped at 8 MiB and 4,194,304 pixels. The reviewed vision and image services receive the
copy and may keep it. Altera saves the result, request fingerprint and conversation marker. The reference bytes
are excluded from Altera's saved records.
Private image routes send `Cache-Control: no-store`, and the service worker caches application assets only.
[Picture controls and deletion](AVATARS.md)

## Connections to NPC Forge and ComfyUI

Meet uses a local HTTP or HTTPS NPC Forge address, with an optional base path. Altera checks service identity during
discovery, including on the old port that Voice Lab now uses. Use an explicit loopback IP if you need to avoid hostname
resolution. Invalid ports, embedded credentials, queries and fragments in the base address are rejected. Forge JSON
responses are limited to 4 MiB. Dedicated binary downloads have their own limits. Invalid, truncated or oversized
replies are rejected. Altera leaves Forge HTTP error bodies out of the interface and bounds cancellation identifiers.

ComfyUI can also be on a remote server you configure. Metadata is limited to 16 MiB and image responses to 64 MiB.
The client rejects malformed or truncated replies and omits HTTP error bodies. Both clients connect directly, ignoring
ambient proxy settings and refusing redirects. ComfyUI also rejects embedded credentials, queries and fragments in
the base address. A transport failure ends the operation. A workflow rejected with HTTP 400 can use the configured
fallback. Interruption and memory-release requests are best effort and apply to the configured server. Socket timeouts
apply to the connection and reads. They do not set a deadline for the whole job.

ComfyUI reference uploads use fresh random names and ask the server to avoid overwriting a file. Altera requires the
requested filename, folder and storage type in the response before starting the workflow. A service that controls its
own storage can still change, retain or forward the files. The checks cover Altera's requests and the replies it uses.

## Character cards

Altera reads only named, supported assets from a CHARX archive. It checks archive paths and sizes and reads members
without extracting them to disk paths. Both the archive and its total expanded size are capped at 100 MiB,
with at most 500 members and 20 MiB per member. PNG card files are limited to 20 MiB, decoded card JSON to 2 MiB
and images to 8192 pixels per side. Living-portrait frames must match the portrait's SHA-256 fingerprint.

PNG card imports recognise `chara`, `ccv3` and `tavernCardV2` in `tEXt`, `zTXt` and `iTXt` chunks. A V3 card takes
priority when present. The saved portrait omits the card text.

The card's creator notes are shown as plain text in its settings and are kept out of model requests. Lorebook
entries using regular expressions are skipped. When you preview or import an embedded voice, Altera contacts
Voice Lab on this PC. A cloned recording requires permission confirmation before import.
[Using character cards](CHARACTERS.md)

## Kids mode

The server limits a Kids browser session to pals in Kids mode and the pages and actions that view needs.
The session keeps its own open pal. A parent PIN is stored as a salted scrypt hash and omitted from API responses
and logs. Five incorrect entries start a shared wait, which increases up to five minutes. Browsers without a verified
PIN remain locked when state cannot be read. The Kids view leaves out pals whose status it cannot confirm.

A restricted message receives a fixed answer and stays out of model requests for chat or mood analysis.
It remains in the chat, and the saved text is excluded from automatic memories. Altera buffers the pal's complete
reply and checks it before displaying or speaking it. A rejected reply is replaced, and the blocked text is never
saved as the answer. Web tools, check-ins, new chat pictures and flirty portrait expressions are unavailable
in Kids mode. Existing pictures stay in history until removed. Kids mode and the separate 18+ add-on prevent
each other's activation.

The topic checks use word lists, and the model can get things wrong. Anyone who knows the PIN or can edit Altera's
files can unlock the app. Backups carry the lock settings too. Use adult supervision.
[Set up and use Kids mode](KIDS_MODE.md)

## Local access and data

Keep Altera on the PC and use its private access settings for a phone. The [security policy](../SECURITY.md)
describes loopback, browser request checks and Tailscale access. The [README](../README.md#what-stays-on-your-pc-and-what-leaves-it)
lists the choices that send information to other services. For other technical limits, see
[voice transport](VOICE_TRANSPORT.md), [encrypted backup format](ENCRYPTED_BACKUP_FORMAT.md),
[private storage](PRIVATE_STORAGE.md) and [architecture](ARCHITECTURE.md).

Correction and Forget check the companion and notebook directories before removing files. Symlinks, reparse points,
hard links, unexpected file types, excessive nesting or changed entries stop the operation. A filesystem error
after cleanup starts can leave a partially removed notebook and keep recalled knowledge paused. Keep the affected
files and review the paused state before retrying. The checks cannot protect against all changes by a process
running as the same user. Older backups and disk blocks may retain copies.
