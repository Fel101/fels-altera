# Backup and restore

Open **Settings > Privacy & Data > Backup and restore**. New backups default to an authenticated encrypted `.alterabak` archive. Download a copy to another drive; copies kept only in this instance's `backups/` directory cannot protect against losing that drive.

A backup contains conversations and reply records, server-saved voice notes/audio, vector memories with embeddings, companion cards/portraits, approved pictures, notebooks, episodes/reflection records, workspace text and application preferences. A backup made in chat includes this browser's appearance preferences and unsent text drafts. Browser-only voice recordings and other devices' browser storage are not included; download recordings separately or, for the optional private page, use its separately encrypted browser-data export and key recovery workflow. API credentials, remote-access policy, the signing key, private-worker runtime receipts, raw vector-database files, logs, caches and temporary uploads/recordings are excluded. Archives that try to replace a protected worker receipt are refused during preview.

If the interface asks you to restart Altera, its running server does not advertise the required backup API. Creation and restore stay disabled; saved backups can still be downloaded. The UI uses dedicated versioned mutation URLs so an older or rolled-back server cannot silently ignore the encryption fields. No automatic restart or legacy mutation fallback occurs.

## Create and keep an encrypted backup

1. Leave **Encrypt this backup** checked. Enter and confirm an independent password of at least 12 Unicode characters, at most 1,024 UTF-8 bytes. Spaces and Unicode spelling are preserved exactly.
2. Optionally select **Also generate a one-time recovery key**. Choose **Create backup**, then **Download** beside the saved archive.
3. If requested, explicitly download the recovery key before closing the dialog or making another backup. Its file identifies the associated backup. Keep the key/password separately from the archive, for example in your password manager. Altera stores neither secret for you.

The password or the applicable recovery key can open the archive. An archive without a recovery-key slot requires its password. Losing both credentials makes it unrecoverable; there is no reset or server-held bypass. A lost create response may leave a saved archive without displaying the optional key; the password still works. Review saved backups before creating another copy.

The encrypted file protects its manifest, filenames and payload. Its approximate size, timestamp, encryption profile and recovery-slot presence are public metadata. Saved-backup listings hide encrypted content counts until unlock. The envelope uses a fixed bounded Argon2id profile and libsodium authenticated streaming through pinned PyNaCl; see the [format specification](ENCRYPTED_BACKUP_FORMAT.md). Local tests/review do not constitute an independent cryptographic audit.

Unchecking encryption explicitly creates a **plaintext ZIP**; anyone with it can read its contents. Earlier ZIP backups still work and are not automatically converted. JSON/Markdown/PDF reports also remain plaintext exports.

## Restore

1. Enter the archive password or select **Recovery key**, then choose **Preview restore** beside the saved archive. For an uploaded file choose it first, then **Preview selected backup**. Select **Plaintext ZIP - no unlock secret** for an older ZIP.
2. Review validated counts and the replacement notice. Authentication and preview do not replace application data. Corrupt, truncated or wrong-key archives cannot publish a partial restore; successfully decrypted ZIPs still undergo ordinary path/schema/manifest validation.
3. Leave **Restore service connections and model/voice settings** off to keep this PC's connections. Enabling it can switch the model or speech to a cloud service; the preview identifies the selected provider, model host and speech fallback. Existing API keys, remote access and workspace-tool permissions are preserved.
4. Choose and confirm a **Recovery-backup password** for the copy of the data about to be replaced. Record this separately too. An encrypted source always requires an encrypted recovery backup. An explicitly selected plaintext source can also use an explicitly selected plaintext recovery ZIP.
5. Check the replacement acknowledgment and choose **Restore this backup**. Altera saves that recovery archive first, then replaces data and reloads. Optional content settings return off.

Unlock credentials remain only in the open dialog and are sent again when applying an unapplied restore, including after a server restart. Changing the file, unlock method/secret or connection choice invalidates the preview. Changing the recovery-copy password resets the acknowledgment. Closing clears all credential inputs and the displayed one-time key. Close/Escape is disabled during create/apply; closing a read-only preview does not cancel an already submitted server mutation.

A successfully applied token returns its saved application receipt on retry without decrypting or replacing data again. This is transport idempotence in an already open app, not a separate archive authentication mechanism. Credentials are never stored in preview metadata, receipts, URLs or logs. Browser preference/draft data in a receipt remains private instance data.

If Altera reports that backup cleanup could not be confirmed, it stops accepting new work in that process. An archive or restore may already have been published before the cleanup error, so review Saved backups and any recovery instructions before retrying. After a committed restore, its receipt remains available. Restart only after Altera has stopped.

Wait for active replies, pictures, notes and requests before snapshot/replacement. Altera coordinates one server process; do not run another server or external writer against the same instance. A busy crypto operation returns 409 rather than starting another concurrent memory-hard derivation.

After restore, older tabs cannot mutate the replacement dataset. Reload and use **Review drafts**. Restored, legacy and stale drafts remain unsent until explicitly placed in the composer and sent.

## Interrupted restore and recovery

A failed restore attempts to put the previous database, vectors, files and preferences back. Its recovery archive remains in Saved backups. If rollback fails or the process stops during replacement, ordinary requests pause and **Backup recovery** opens. Unlock, preview and restore the marked recovery archive first. Repair uses the existing recovery archive and does not create another copy or need another recovery-copy password.

Preserve `backups/restore-in-progress.json` and the referenced `backups/.work/` directory until repair succeeds. Recovery also needs the intact protected-settings file saved in that work directory; it preserves this PC's credentials and access policy. New journals verify its checksum. Missing, damaged or unreadable protection data keeps recovery paused for manual repair rather than silently dropping credentials. Preserve the whole `backups/` folder if its journal/protection data is damaged.

Before replacing data, restore also checks the current conversation database and copies its reply request IDs into the staged replacement. This prevents old requests from being replayed against the restored data. If the database is missing, damaged or lacks the required request-history tables, restore stops before publishing a new recovery journal or replacing application data. Keep the database, recovery archive and journal together; Altera does not yet offer an automatic repair for this case. The same check applies to ordinary restores; very old or incomplete live schemas must be resolved before restoring.

The inner logical format is `altera-backup`, version 1; the outer encrypted envelope is `altera-encrypted-backup`, version 1. Backups made while the app was called Her (`her-` files, `.herbak`, recovery keys starting `HER-RECOVERY-1-`) are still listed, previewed and restored. Restore imports rows into Altera's schema and rebuilds conversation search, without executing uploaded triggers or SQL schema. Vector restore uses saved embeddings and requires the same embedding function. It needs no model inference.

## Temporary files and coverage limits

Archive encryption does **not** encrypt the running SQLite/vector stores, notebooks, media, browser drafts or restore work. Snapshot creation and validation need temporary plaintext in private instance directories. Uploaded multipart files, STT recordings and TTS intermediate files now use `ALTERA_HOME/data/tmp`; normal completion/failure cleans owned files, while a crash or filesystem refusal can leave residue. The temp/cache directories are excluded from backups. Browser IndexedDB/session storage, downloads, external service files and OS paging/crash files are separate protection surfaces. See [private storage readiness](PRIVATE_STORAGE.md); full at-rest protection/unlock is a separate roadmap requirement, not silently activated by this checkbox.

Limits are 2 GiB for the inner compressed ZIP, 8 GiB expanded and 100,000 files. The encrypted envelope adds at most 51,249 bytes. Preview tokens expire after 15 minutes and then become eligible for operation-triggered cleanup. Each bounded pass examines at most 100 staging entries, advances through one scan cycle during the service lifetime, and removes only recognized expired uploads. A process restart begins a new scan; newly created entries may wait until the next cycle. There is no background cleanup timer or strict deletion deadline. Active or recovery work is preserved. After the completed restore receipt is published and the journal removed, cleanup failure cannot roll back that successful restore; the receipt remains usable for an idempotent retry. Saved archives remain until explicitly removed outside the app. Work needed for interrupted recovery and completed-token receipt files are retained; no general secure-erasure claim is made.

Presence/reflection preferences, jobs, inbox/derived notes and story curation are included in SQLite. Restore pauses unattended work, clears expression and marks derived sources stale. Retained story pins/curation require explicit matching-source review; no restored material can bypass a later Forget boundary. See [Our story](OUR_STORY.md) and [check-ins](PRESENCE.md).

## API

All backup responses are `no-store` and `nosniff`. Secrets belong in request bodies only. An API call without a format uses encrypted creation/recovery; plaintext requires the explicit `zip` choice. JSON requests use the app's normal request-size limit and reject duplicate fields and non-finite numbers; only multipart restore previews receive the larger archive-upload allowance. Unknown-length or terminated-stream JSON that reaches the configured limit is refused because its completeness cannot be established. Browser snapshots must fit the 8 MiB restore-preview limit when encoded as UTF-8.

Backup requests retain an application-state lease until their response closes, including downloads. Exclusive maintenance refuses new backup requests before backup-service initialization or staging cleanup; an open download can therefore delay maintenance. If a streamed response cannot be closed, the process fences new work rather than treating its files as released. A restore fault still permits recovery requests, while process shutdown refuses new ones. This coordination is local to one Altera process and does not lock external programs or another server.

| Method and path | Contract |
| --- | --- |
| `GET /api/backups` | Saved archives and recovery status; encrypted counts withheld. |
| `POST /api/backups` | `format: "encrypted"` (default), `password`, optional boolean `include_recovery_key`, optional `browser_state`. `format: "zip"` accepts no password/key request. Returns a raw recovery key only on its requested create response. |
| `GET /api/backups/<id>/download` | Encrypted binary or plaintext ZIP attachment. |
| `POST /api/backups/restore/preview` | JSON `backup_id`, or multipart `file`; optional `restore_connections`. Encrypted input needs exactly one `password` or `recovery_key`. Returns token, expiry, validated counts and `encrypted`. |
| `POST /api/backups/restore/apply` | `token`, `confirm: "restore"`, source unlock credential again, optional current `browser_state`; `recovery_format: "encrypted"` (default) and `recovery_password`, or explicit `zip` where permitted. Fault recovery uses the existing recovery archive. |
| `GET /api/backups/recovery` | Standalone recovery interface. |

Browser state contains the current `dataset_epoch`, whitelisted appearance `preferences` and `drafts`. Ordinary API mutations after replacement require the current dataset identifier through `X-Altera-Dataset` or JSON `dataset_epoch`; stale identifiers return 409 `dataset_changed`.

If this browser's saved queued or current-tab text drafts cannot be read, the backup or restore request is not started. The snapshot includes this tab's unfinished turns, saved composer and recovery-fallback drafts, including on the standalone recovery page. It checks tab text before and after reading the queue and refuses a dataset change during collection; these browser stores are not a single atomic snapshot. Other tabs can share the queue, but their tab-specific state and other devices are not included. After a successful restore, existing queued drafts and pending turns are kept; restored drafts are added for review. The changed dataset identity prevents older drafts and pending turns from being automatically sent into the restored data. Copies can appear more than once, so review them before sending or deleting anything.
