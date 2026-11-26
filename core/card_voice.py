"""Review and apply a character card's optional Voice Lab voice."""

import threading
from hashlib import sha256

from itsdangerous import BadSignature, URLSafeTimedSerializer

from core import charcard, characters, tts, voice_http, voicelab
from core.avatars import _write_json

LOCK = threading.Lock()


def _archive(store, avatar_id):
    path = characters.originals_dir(store) / (avatar_id + ".charx")
    try:
        with path.open("rb") as source:
            package = charcard.parse_charx(source.read(charcard.MAX_CHARX_BYTES + 1))
        if package.voice_file:
            return package.voice_file
    except (OSError, charcard.CardError):
        pass
    raise characters.CharacterError("The saved card has no readable voice file. Import a new copy.", 409)


def _preset(found, offer):
    for row in found.voices:
        if row["kind"] != "preset" or row["model"] != offer["model"]:
            continue
        if ((offer["preset"] and row.get("voice_id") == offer["preset"])
                or (not offer["preset"] and offer["name"] in (row["id"], row.get("voice_id")))):
            return row["id"]
    raise voicelab.LabError("This preset is not ready in Voice Lab. Get its model and voice ready there, then try again.")


def _in_lab(found, sample_info, model):
    """The ready Cast voice this Lab already holds for the file, when the file was exported from this Lab's Cast.

    A card made in NPC Forge carries the voice file of a Cast voice from the same Voice Lab. Importing it again would
    make a second Cast voice with the same name, and Voice Lab then refuses that name for everyone who uses it, so the
    voice already there is used instead. The file names its voice by the Cast identity it came from; that id is random,
    so only this Lab's own export can name one of its voices.
    """
    identity = sample_info.get("id")
    if not voicelab._valid_identity(identity):
        return None
    if identity in found.hidden:
        raise voicelab.LabError(_shared_name(found.hidden[identity]))
    name = next((row["id"] for row in found.voices
                 if row["kind"] == "cast" and row.get("identity_id") == identity and row.get("model") == model), None)
    if name is not None and found.shared(name):
        raise voicelab.LabError(_shared_name(name))
    return name


def _shared_name(name):
    return f"Voice Lab has more than one voice called {name}. Rename one in Voice Lab’s Cast, then try again."


def review(store, avatar_id, data, secret):
    action = data.get("action")
    with LOCK:
        record = characters._character(store, avatar_id)
        offer = record.get("card_voice") or {}
        if not offer:
            raise characters.CharacterError("This card has no Voice Lab voice.", 404)
        cfg = tts.settings()
        given = cfg["voicelab"]["url"]
        proof = URLSafeTimedSerializer(secret, salt="altera-card-voice-v1")
        if offer["archive"]:
            url, library, _ = voicelab.discover(given)
            archive = _archive(store, avatar_id)
            voice = sha256(archive).hexdigest()
        else:
            found = voicelab.catalogue(given)
            url, library = found.url, found.library
            voice = _preset(found, offer)
        binding = [avatar_id, sha256(f"{url}\n{library}".encode()).hexdigest(), voice]

        if action == "preview":
            info = {"name": offer["name"], "archive": offer["archive"], "remote": not voice_http.is_loopback(url)}
            if offer["archive"]:
                title, sample_info = voicelab.preview_persona(url, archive)
                if sample_info.get("engine_id") != offer["model"]:
                    raise voicelab.LabError("The file contains a different voice model from the card. Ask for a new card.")
                if sample_info.get("model_installed") is not True:
                    raise voicelab.LabError("Install this voice’s model in Voice Lab, then preview the file again.")
                sample = sample_info.get("sample")
                sample = sample if isinstance(sample, dict) else {}
                cloned = offer["cloned"] or sample.get("source_type") == "recording"
                origin = sample.get("origin")
                allowed = sample.get("allowed_use")
                if cloned and not all(isinstance(value, str) and value.strip() for value in (origin, allowed)):
                    raise voicelab.LabError("The recording has no stated origin or allowed use. Ask for a voice file with both.")
                info.update(name=title, cloned=cloned, origin=origin[:1000] if isinstance(origin, str) else "",
                            allowed_use=allowed[:1000] if isinstance(allowed, str) else "",
                            in_lab=_in_lab(voicelab.catalogue(url), sample_info, offer["model"]) is not None)
            return {**info, "review": proof.dumps(binding), "selected": cfg["engine"] == "voicelab"}

        token = data.get("review")
        if data.get("permission") is not True or not isinstance(token, str) or len(token) > 2048:
            raise characters.CharacterError("Review the voice and confirm that you want to use it first.", 409)
        try:
            if proof.loads(token, max_age=900) != binding:
                raise BadSignature("The card or Voice Lab changed.")
        except BadSignature:
            raise characters.CharacterError("The review expired or the voice changed. Preview it again.", 409) from None

        if offer["archive"]:
            _, sample_info = voicelab.preview_persona(url, archive)
            found = voicelab.catalogue(url)
            if found.url != url or found.library != library:
                raise voicelab.LabError("Voice Lab changed during the review. Preview the voice again.")
            voice = (_in_lab(found, sample_info, offer["model"])
                     or _apply_archive(store, avatar_id, url, binding[1:], archive))
        _save(store, avatar_id, voice=voice)
        return {"name": voice, "selected": cfg["engine"] == "voicelab"}


def _save(store, avatar_id, **changes):
    record = characters._character(store, avatar_id)
    _write_json(store.root / avatar_id / "avatar.json", {**record, **changes})


def _apply_archive(store, avatar_id, url, binding, archive):
    pending = characters._character(store, avatar_id).get("card_voice_import") or {}
    if pending.get("target") != binding:
        identity = voicelab.import_persona(url, archive)
        references = identity.get("references") or []
        reference = references[0].get("id") if references and isinstance(references[0], dict) else None
        pending = {"target": binding, "identity": identity["id"], "reference": reference}
        _save(store, avatar_id, card_voice_import=pending)
    found = voicelab.catalogue(url)
    if found.url != url or sha256(f"{found.url}\n{found.library}".encode()).hexdigest() != binding[0]:
        raise voicelab.LabError("Voice Lab changed during the import. Check the voice in Cast and try again.")
    voice = next((row["id"] for row in found.voices if row.get("identity_id") == pending["identity"]), None)
    if not pending.get("approved"):
        voicelab.approve_persona(url, pending["identity"], pending["reference"])
        pending["approved"] = True
        _save(store, avatar_id, card_voice_import=pending)
        if voice is None:
            found = voicelab.catalogue(url)
            voice = next((row["id"] for row in found.voices if row.get("identity_id") == pending["identity"]), None)
    if not voice and pending["identity"] in found.hidden:
        raise voicelab.LabError(_shared_name(found.hidden[pending["identity"]]))
    if not voice:
        raise voicelab.LabError("The imported voice is in Cast. Get it ready in Voice Lab and try again.")
    return voice
