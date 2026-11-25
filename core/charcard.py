"""
Altera — Reading character cards (Character Card V2, and the older V1 and newer V3 shapes), without any image library.

A PNG card is an ordinary PNG with the card's JSON, base64-encoded, in a text chunk. The usual keyword is "chara"; V3 cards
add "ccv3" and some tools write "tavernCardV2". tEXt, zTXt (zlib) and iTXt chunks are all read. A .json card is the same
JSON on its own.

parse_png(data) -> (card, clean_png)   the card, and the image with every text chunk removed (that is what is shown)
parse_json(data) -> card
normalize(raw) -> card                 V1/V2/V3 -> one dict of V2 data fields, every value a string or list of strings,
                                       plus "book": the character book (lorebook) or None, see read_book

Everything is bounded: file size, chunk count, decompressed size and JSON size. A malformed file raises CardError with a
message that is safe to show.
"""
import base64
import binascii
import hashlib
import io
import json
import re
import stat
import struct
import zipfile
import zlib
from dataclasses import dataclass, field

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_CHARX_BYTES = 100 * 1024 * 1024
MAX_CHARX_MEMBERS = 500
MAX_CHUNKS = 4096
MAX_CARD_BYTES = 2 * 1024 * 1024           # the decoded JSON of one card
MAX_IMAGE_SIDE = 8192
CARD_KEYWORDS = ("ccv3", "chara", "taverncardv2")   # preferred first; compared case-insensitively
TEXT_CHUNKS = {b"tEXt", b"zTXt", b"iTXt"}

# The V2 data fields Altera reads. extensions are kept only as an opaque original (the saved file).
STRING_FIELDS = ("name", "description", "personality", "scenario", "first_mes", "mes_example", "creator_notes",
                 "system_prompt", "post_history_instructions", "creator", "character_version")
LIST_FIELDS = ("alternate_greetings", "tags")
FIELD_LIMIT = 60_000                          # characters per text field
BOOK_ENTRIES = 512                            # entries read from a character book
BOOK_KEYS = 32                                # keys per entry
KEY_LIMIT = 100                               # characters per key
ENTRY_LIMIT = 8000                            # characters per entry
POSITIONS = ("before_char", "after_char")
_REGEX_KEY = re.compile(r"^/.+/[a-z]*$")       # a key written as a regular expression (/pattern/flags)


class CardError(ValueError):
    """The file is not a card Altera can read. The message is safe to show."""


@dataclass
class CardPackage:
    card: dict
    extension: str
    portrait: bytes | None = None
    body: bytes | None = None
    expressions: dict = field(default_factory=dict)
    frames: dict = field(default_factory=dict)
    sheet: dict | None = None
    voice_file: bytes | None = None


def file_limit(name):
    return MAX_CHARX_BYTES if name.lower().endswith(".charx") else MAX_FILE_BYTES


def _chunks(data: bytes):
    if len(data) > MAX_FILE_BYTES:
        raise CardError("The card is larger than 20 MB.")
    if not data.startswith(PNG_SIGNATURE):
        raise CardError("That file is not a PNG image.")
    pos, count = len(PNG_SIGNATURE), 0
    while pos < len(data):
        if pos + 8 > len(data):
            raise CardError("The PNG file is cut short.")
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        end = pos + 12 + length
        if end > len(data):
            raise CardError("The PNG file is cut short.")
        body = data[pos + 8:pos + 8 + length]
        crc = struct.unpack(">I", data[pos + 8 + length:end])[0]
        if zlib.crc32(kind + body) & 0xFFFFFFFF != crc:
            raise CardError("The PNG file is damaged (a checksum does not match).")
        yield kind, body, data[pos:end]
        count += 1
        if count > MAX_CHUNKS:
            raise CardError("The PNG file has too many parts to be a card.")
        pos = end
        if kind == b"IEND":
            return
    raise CardError("The PNG file has no end marker.")


def _inflate(body: bytes) -> bytes:
    stream = zlib.decompressobj()
    out = stream.decompress(body, MAX_CARD_BYTES * 2 + 1)
    if len(out) > MAX_CARD_BYTES * 2 or stream.unconsumed_tail:
        raise CardError("The card text is too large.")
    return out


def _text_chunk(kind: bytes, body: bytes):
    """(keyword, text bytes) of a tEXt / zTXt / iTXt chunk, or None if it is malformed."""
    keyword, sep, rest = body.partition(b"\0")
    if not sep or not 1 <= len(keyword) <= 79:
        return None
    try:
        if kind == b"tEXt":
            return keyword.decode("latin-1"), rest
        if kind == b"zTXt":
            return (keyword.decode("latin-1"), _inflate(rest[1:])) if rest[:1] == b"\0" else None
        compressed, method = rest[:1], rest[1:2]
        _language, sep1, rest = rest[2:].partition(b"\0")
        _translated, sep2, text = rest.partition(b"\0")
        if not (sep1 and sep2):
            return None
        if compressed == b"\1":
            if method != b"\0":
                return None
            text = _inflate(text)
        return keyword.decode("latin-1"), text
    except (zlib.error, UnicodeDecodeError):
        return None


def _decode_card_text(text: bytes):
    raw = text.strip()
    try:
        decoded = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        decoded = raw                                         # a few tools store the JSON itself
    if len(decoded) > MAX_CARD_BYTES:
        raise CardError("The card text is too large.")
    try:
        return json.loads(decoded.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None


def png_size(data: bytes):
    """(width, height) of a PNG Altera can show, or CardError. Every chunk's checksum and the end marker are checked, so a
    truncated or damaged upload is refused before it is written."""
    return _png_image(data)[0]


def _png_image(data):
    width = height = 0
    picture, kept = False, [PNG_SIGNATURE]
    for kind, body, whole in _chunks(data):
        if kind == b"IHDR":
            if len(body) != 13:
                raise CardError("The PNG header is damaged.")
            width, height = struct.unpack(">II", body[:8])
        elif kind == b"IDAT":
            picture = True
        if kind not in TEXT_CHUNKS:
            kept.append(whole)
    if not (0 < width <= MAX_IMAGE_SIDE and 0 < height <= MAX_IMAGE_SIDE):
        raise CardError(f"The image must be at most {MAX_IMAGE_SIDE} pixels on each side.")
    if not picture:
        raise CardError("The PNG image has no picture data.")
    return (width, height), b"".join(kept)


def parse_png(data: bytes):
    """(card, clean_png): the normalized card and the same image without its text chunks."""
    found, kept, width, height = {}, [PNG_SIGNATURE], 0, 0
    for kind, body, whole in _chunks(data):
        if kind == b"IHDR":
            if len(body) != 13:
                raise CardError("The PNG header is damaged.")
            width, height = struct.unpack(">II", body[:8])
        if kind in TEXT_CHUNKS:
            parsed = _text_chunk(kind, body)
            if parsed and parsed[0].lower() in CARD_KEYWORDS and parsed[0].lower() not in found:
                found[parsed[0].lower()] = parsed[1]
            continue                                          # text chunks are never kept in the shown image
        kept.append(whole)
    if not (0 < width <= MAX_IMAGE_SIDE and 0 < height <= MAX_IMAGE_SIDE):
        raise CardError(f"The card image must be at most {MAX_IMAGE_SIDE} pixels on each side.")
    for keyword in CARD_KEYWORDS:
        if keyword in found:
            raw = _decode_card_text(found[keyword])
            if raw is not None:
                return normalize(raw), b"".join(kept)
    raise CardError("This PNG has no character card in it (no chara or ccv3 text).")


def _json(data):
    if len(data) > MAX_CARD_BYTES:
        raise CardError("The card is larger than 2 MB.")
    try:
        return json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise CardError("That file is not valid JSON.") from None


def parse_json(data: bytes):
    return normalize(_json(data))


_EMBED = "embeded://"
_ASSET_PART = re.compile(r"[A-Za-z0-9_. -]+")
_FRAME = re.compile(r"[a-z0-9_-]{1,40}")


def _check_directory(data):
    # Bound the actual number of records before ZipFile builds a ZipInfo for each one.
    tail = len(data) - min(len(data), 65535 + 22)
    end = data.rfind(b"PK\x05\x06", tail)
    if end < 0 or end + 22 > len(data):
        raise CardError("The CHARX ZIP is damaged.")
    disk, directory_disk, count_disk, count, size, start, comment = struct.unpack_from("<4H2IH", data, end + 4)
    if (disk or directory_disk or count_disk != count or start + size != end
            or end + 22 + comment != len(data)):
        raise CardError("The CHARX ZIP directory is damaged or uses an unsupported format.")
    if count > MAX_CHARX_MEMBERS:
        raise CardError("The CHARX card has more than 500 ZIP members.")
    pos = start
    for _ in range(count):
        if pos + 46 > end or data[pos:pos + 4] != b"PK\x01\x02":
            raise CardError("The CHARX ZIP directory is damaged.")
        pos += 46 + sum(struct.unpack_from("<3H", data, pos + 28))
    if pos != end:
        raise CardError("The CHARX ZIP directory has an invalid number of members.")


def _members(archive):
    entries = archive.infolist()
    if len(entries) > MAX_CHARX_MEMBERS:
        raise CardError("The CHARX card has more than 500 ZIP members.")
    members, names, total = {}, set(), 0
    for entry in entries:
        name = entry.orig_filename
        parts = name.removesuffix("/").split("/")
        mode = stat.S_IFMT(entry.external_attr >> 16)
        if (not all(_ASSET_PART.fullmatch(part) and part not in (".", "..") for part in parts)
                or name.removesuffix("/").lower() in names or mode not in (0, stat.S_IFREG, stat.S_IFDIR)
                or entry.external_attr & 0x400 or entry.flag_bits & 1):
            raise CardError("The CHARX contains an unsafe, duplicate or encrypted ZIP member.")
        if entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise CardError("The CHARX ZIP uses an unsupported compression format. Export a new copy.")
        if entry.file_size > MAX_FILE_BYTES:
            raise CardError("A member of the CHARX card is larger than 20 MB.")
        total += entry.file_size
        if total > MAX_CHARX_BYTES:
            raise CardError("The contents of the CHARX card exceed 100 MB.")
        names.add(name.removesuffix("/").lower())
        if not entry.is_dir():
            members[name] = entry
    return members


def _assets(archive, members, raw):
    """Resolve only declared, local assets. Other URI schemes never leave the card reader."""
    items = raw["data"].get("assets")
    assets, declared = [], set()
    for item in items if isinstance(items, list) else ():
        if not isinstance(item, dict):
            continue
        uri = item.get("uri")
        if isinstance(uri, str) and uri.startswith(_EMBED):
            path = uri.removeprefix(_EMBED)
            if not path.startswith("assets/") or path not in members:
                raise CardError("A declared CHARX asset is missing or outside the assets folder.")
            declared.add(uri)
            assets.append(item)

    def read(uri, *, limit=MAX_FILE_BYTES):
        if not isinstance(uri, str) or uri not in declared:
            raise CardError("The CHARX card references an undeclared asset.")
        member = members[uri.removeprefix(_EMBED)]
        if member.file_size > limit:
            raise CardError("A CHARX text asset is larger than 2 MB.")
        return archive.read(member)

    return assets, read


def _asset_object(read, uri):
    value = _json(read(uri, limit=MAX_CARD_BYTES))
    if not isinstance(value, dict):
        raise CardError("A CHARX text asset must contain a JSON object.")
    return value


def _living_frames(read, picture, info, original_portrait):
    manifest = _asset_object(read, info["manifest"])
    portrait, frames = manifest.get("portrait"), manifest.get("frames")
    if original_portrait is None or not isinstance(portrait, dict) or not isinstance(frames, dict):
        raise CardError("The CHARX living portrait manifest is incomplete.")
    digest = hashlib.sha256(original_portrait).hexdigest()
    if digest != portrait.get("sha256") or digest != info.get("portrait_sha256"):
        raise CardError("The CHARX living portrait was made from a different portrait (SHA-256 mismatch).")
    result = {}
    for name, entry in frames.items():
        path = entry.get("file") if isinstance(entry, dict) else None
        if not _FRAME.fullmatch(name) or not isinstance(path, str):
            raise CardError("The CHARX living portrait names an invalid frame.")
        uri = path if path.startswith(_EMBED) else _EMBED + path
        result[name] = picture(uri)
    return result


def parse_charx(data, *, include_voice=True):
    if len(data) > MAX_CHARX_BYTES:
        raise CardError("The CHARX card is larger than 100 MB.")
    _check_directory(data)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = _members(archive)
            root = members.get("card.json")
            if root is None:
                raise CardError("The CHARX card needs card.json at the root of the ZIP.")
            if root.file_size > MAX_CARD_BYTES:
                raise CardError("The card is larger than 2 MB.")
            raw = _json(archive.read(root))
            if not isinstance(raw, dict) or raw.get("spec") != "chara_card_v3":
                raise CardError("A CHARX file needs a Character Card V3 in card.json.")
            package = CardPackage(normalize(raw), ".charx")
            assets, read = _assets(archive, members, raw)
            images = {}

            def picture(uri):
                if uri not in images:
                    images[uri] = _png_image(read(uri))[1]
                return images[uri]

            pngs = [item for item in assets if item.get("ext") == "png"]
            icons = [item for item in pngs if item.get("type") == "icon"]
            icon = next((item for item in icons if item.get("name") == "main"), icons[0] if icons else None)
            original = read(icon["uri"]) if icon else None
            if original is not None:
                package.portrait = images[icon["uri"]] = _png_image(original)[1]
            for item in pngs:
                name = item.get("name")
                if item.get("type") == "emotion" and isinstance(name, str) and _FRAME.fullmatch(name):
                    package.expressions[name] = picture(item["uri"])
                elif item.get("type") == "other" and name == "sprite":
                    package.body = picture(item["uri"])
            fels = package.card.get("fels", {})
            if fels.get("sheet"):
                package.sheet = _asset_object(read, fels["sheet"])
            if (living := fels.get("living_portrait")) and living.get("manifest"):
                package.frames = _living_frames(read, picture, living, original)
            if (voice := fels.get("voice")) and voice.get("file"):
                if include_voice:
                    package.voice_file = read(voice["file"])
                elif not isinstance(voice["file"], str) or not any(item["uri"] == voice["file"] for item in assets):
                    raise CardError("The CHARX card references an undeclared voice file.")
            return package
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, OSError, EOFError, zlib.error):
        raise CardError("The CHARX ZIP is damaged or uses an unsupported compression format.") from None


def read_card(filename, data, *, include_voice=True):
    name = filename.lower()
    if name.endswith(".charx"):
        return parse_charx(data, include_voice=include_voice)
    if name.endswith(".png") or data.startswith(PNG_SIGNATURE):
        card, png = parse_png(data)
        return CardPackage(card, ".png", png)
    if name.endswith(".json"):
        return CardPackage(parse_json(data), ".json")
    raise CardError("Choose a character card in .charx, .png or .json format.")


def _text(value) -> str:
    if value is None:
        return ""
    if not isinstance(value, (str, int, float)):
        return ""
    return str(value).replace("\r\n", "\n").strip()[:FIELD_LIMIT]


def _flag(value, default=False) -> bool:
    return value if isinstance(value, bool) else default


def _number(value, default, low, high) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    try:
        return max(low, min(high, int(value)))
    except (ValueError, OverflowError):
        return default


def _keys(value) -> list:
    values = value if isinstance(value, list) else []
    keys = []
    for item in values[:BOOK_KEYS]:
        text = " ".join(_text(item).split())[:KEY_LIMIT]
        if text and text not in keys:
            keys.append(text)
    return keys


def read_book(raw):
    """A character_book (V2/V3) as {name, scan_depth, token_budget, entries: [...]}, or None when there is none. Each entry is
    {name, keys, secondary_keys, content, enabled, constant, selective, case_sensitive, regex, order, position}: unknown or
    broken values fall back to the spec's meaning, and an entry with no content is dropped."""
    if not isinstance(raw, dict) or not isinstance(raw.get("entries"), list):
        return None
    entries = []
    for item in raw["entries"][:BOOK_ENTRIES]:
        if not isinstance(item, dict):
            continue
        content = _text(item.get("content"))[:ENTRY_LIMIT]
        if not content:
            continue
        extensions = item.get("extensions") if isinstance(item.get("extensions"), dict) else {}
        position = item.get("position") if item.get("position") in POSITIONS else "before_char"
        keys, secondary = _keys(item.get("keys")), _keys(item.get("secondary_keys"))
        entries.append({
            "name": " ".join(_text(item.get("name") or item.get("comment")).split())[:120],
            "keys": keys, "secondary_keys": secondary, "content": content,
            "enabled": _flag(item.get("enabled"), True), "constant": _flag(item.get("constant")),
            "selective": _flag(item.get("selective")), "case_sensitive": _flag(item.get("case_sensitive")),
            # V3 use_regex, or SillyTavern's /pattern/ keys: Altera does not run a card's regular expressions
            "regex": (_flag(item.get("use_regex")) or _flag(extensions.get("use_regex"))
                      or any(_REGEX_KEY.match(key) for key in keys + secondary)),
            "order": _number(item.get("insertion_order"), 100, -100000, 100000), "position": position,
        })
    if not entries:
        return None
    return {"name": " ".join(_text(raw.get("name")).split())[:120],
            "scan_depth": _number(raw.get("scan_depth"), 0, 0, 100),
            "token_budget": _number(raw.get("token_budget"), 0, 0, 100000), "entries": entries}


def normalize(raw) -> dict:
    """One flat dict of V2 data fields from a V1, V2 or V3 card. Raises CardError when there is no usable card."""
    if not isinstance(raw, dict):
        raise CardError("The card is not a JSON object.")
    spec = raw.get("spec")
    if spec in ("chara_card_v2", "chara_card_v3") or isinstance(raw.get("data"), dict):
        data = raw.get("data")
        if not isinstance(data, dict):
            raise CardError("The card has no data section.")
        version = "v3" if spec == "chara_card_v3" else "v2"
    else:
        data = {**raw, "first_mes": raw.get("first_mes", raw.get("first_message"))}   # V1: the fields sit at the top
        version = "v1"
    card = {field: _text(data.get(field)) for field in STRING_FIELDS}
    for key in LIST_FIELDS:
        values = data.get(key) if isinstance(data.get(key), list) else []
        card[key] = [text for text in (_text(v) for v in values[:64]) if text]
    card["spec"] = version
    card["book"] = read_book(data.get("character_book"))
    extensions = data.get("extensions")
    fels = extensions.get("fels") if isinstance(extensions, dict) else None
    if version == "v3" and isinstance(fels, dict) and type(fels.get("contract")) is int and fels["contract"] == 1:
        card["fels"] = {key: fels[key] for key in ("made_by", "licence", "voice", "living_portrait")
                        if isinstance(fels.get(key), dict)}
        card["fels"].update(ai_generated=fels.get("ai_generated") is True, sheet=fels.get("sheet"))
    card["name"] = " ".join(card["name"].split())[:80]
    if not card["name"]:
        raise CardError("The card has no name.")
    if not (card["description"] or card["personality"] or card["first_mes"]):
        raise CardError("The card has no description, personality or first message.")
    return card
