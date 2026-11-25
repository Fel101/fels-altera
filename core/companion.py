"""
Altera — Shared wiring for avatars and pictures.

One AvatarStore and one PhotoService for the whole app, built lazily from config.yaml. The chat engine and the routes both
get them from here, and tests replace them with set_services().
"""
import logging
import threading
from pathlib import Path

from core.paths import DEFAULT_ROOT

from core import age_check, avatars, forge, imagegen, living, photos

LOGGER = logging.getLogger(__name__)
ROOT = DEFAULT_ROOT


class Services:
    def __init__(self, config, avatar_store, photo_service, checker, comfy, living_store=None, *, photo_factory=None):
        self.config = config
        self.avatars = avatar_store
        self._photos = photo_service
        self._photo_factory = photo_factory
        self._photo_lock = threading.Lock()
        self.checker = checker
        self.comfy = comfy
        self.living = living_store or living.LivingStore(avatar_store, checker)

    @property
    def photos(self):
        """Construct and recover picture jobs only on first picture use."""
        with self._photo_lock:
            if self._photo_factory is not None:
                service = self._photo_factory()
                if service is None:
                    raise RuntimeError('The picture service could not be initialized.')
                self._photos = service
                self._photo_factory = None
            return self._photos

    @photos.setter
    def photos(self, value):
        with self._photo_lock:
            self._photos = value
            self._photo_factory = None

    @property
    def imagegen_config(self) -> dict:
        return self.config.get("imagegen") or {}


_services: Services | None = None
_lock = threading.Lock()


def _load_config() -> dict:
    from core.config import get_config
    return get_config()


def build_services(config: dict | None = None, root=None) -> Services:
    config = config if config is not None else _load_config()
    root = Path(root or ROOT)
    cfg = config.get("imagegen") or {}
    backend_for = imagegen.backend_factory(config, root)
    store = avatars.AvatarStore(root / "data" / "avatars", cfg.get("npc_forge_library") or None,
                                forge_client=lambda: forge.ForgeClient(config))
    checker = age_check.AgeCheck(config)
    def make_photos():
        return photos.PhotoService(root / "data" / "photos", store, backend_for, checker, config)
    return Services(config, store, None, checker, backend_for.comfy, photo_factory=make_photos)


def get_services() -> Services:
    global _services
    with _lock:
        if _services is None:
            _services = build_services()
        return _services


def set_services(services: Services | None) -> None:
    """Replace (or, with None, discard) the shared services. Used by tests."""
    global _services
    with _lock:
        _services = services


def photos_ready(avatar_id=None) -> bool:
    """True when pictures can be made: the feature is on and the avatar (the active one, unless a chat's own is given) has a
    portrait to work from."""
    try:
        s = get_services()
        if s.imagegen_config.get("enabled", True) is False:
            return False
        avatar = s.avatars.get(avatar_id) if avatar_id else s.avatars.active()
        from core.avatars import kind_of
        # Pictures are edits of an NPC Forge companion's own portrait; pals and characters do not send them.
        return bool(avatar and kind_of(avatar) == "companion" and s.avatars.image_path(avatar["id"], "portrait"))
    except Exception:
        LOGGER.exception("Could not check whether pictures are available")
        return False
