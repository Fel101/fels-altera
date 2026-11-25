"""Startup registration for locally installed Altera add-ons.

Registration is staged in a separate host for each add-on. Only the host's
validated contributions are published. Installed Python is trusted local code.
"""

import importlib.util
import logging
import re
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from flask import abort, current_app, has_app_context, make_response, request
from markupsafe import Markup
from werkzeug.routing import Map, Rule

from core.paths import root

LOGGER = logging.getLogger(__name__)
NAME = re.compile(r"[a-z][a-z0-9_-]{0,47}")
KIDS_BLOCK = "Remove the installed add-on and restart Altera before setting up Kids mode."
ADDON_BLOCK = "This add-on can load after Kids mode is turned off and Altera is restarted."


def _regular(path, kind):
    try:
        info = path.lstat()
        return kind(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400
    except OSError:
        return False


def _folders(directory):
    if not _regular(directory, stat.S_ISDIR):
        return []
    try:
        return [p for p in sorted(directory.iterdir()) if NAME.fullmatch(p.name)
                and _regular(p, stat.S_ISDIR) and _regular(p / "__init__.py", stat.S_ISREG)]
    except OSError:
        LOGGER.warning("Altera could not read its add-ons folder.")
        return []


@dataclass
class Addon:
    name: str
    directory: Path
    data: Path
    levels: dict = field(default_factory=dict)
    engines: dict = field(default_factory=dict)
    panel: dict = field(default_factory=dict)
    studio: object = None
    routes: dict = field(default_factory=dict)
    rules: list = field(default_factory=list)
    map: Map | None = None

    def add_level(self, name, *, label, summary="", prompt, photo_rule, mood, scenes=(), terms=(), checked=True,
                  full_length=False, sensitive=False, framing=""):
        if not isinstance(name, str) or not NAME.fullmatch(name) or name == "off" or name in self.levels:
            raise ValueError("An add-on can only add new content levels.")
        texts = (label, summary, prompt, photo_rule, mood, framing)
        if not all(isinstance(text, str) and len(text) <= 12000 for text in texts):
            raise ValueError("An add-on level needs bounded text.")
        if type(checked) is not bool or type(full_length) is not bool or type(sensitive) is not bool:
            raise ValueError("An add-on level needs boolean picture options.")
        if any(not isinstance(items, (list, tuple)) or len(items) > 200
               or any(not isinstance(text, str) or not text.strip() or len(text) > 500 for text in items)
               for items in (scenes, terms)):
            raise ValueError("An add-on level needs bounded scene and term lists.")
        self.levels[name] = {"label": label, "summary": summary, "prompt": prompt, "rule": photo_rule, "mood": mood,
                             "scenes": tuple(scenes), "terms": tuple(terms), "checked": checked or sensitive,
                             "full_length": full_length, "sensitive": sensitive, "framing": framing}

    def picture_engine(self, level, render):
        if level not in self.levels or level in self.engines or not callable(render):
            raise ValueError("A picture engine must belong to a level added by this add-on.")
        self.engines[level] = render
        self.levels[level]["checked"] = True

    def picture_source(self, source):
        if self.studio is not None or not all(callable(getattr(source, key, None))
                                              for key in ("items", "file", "body", "picture_details")):
            raise ValueError("An add-on picture source needs readers for items, files and picture details.")
        self.studio = source

    def route(self, method, path, handler):
        method = method.upper()
        if (method not in {"GET", "POST", "PUT", "DELETE"} or not callable(handler)
                or not isinstance(path, str) or not path.startswith("/") or path.startswith("//")
                or ".." in path or "\\" in path or (method, path) in self.routes):
            raise ValueError("An add-on can only publish unique routes below its own prefix.")
        self.routes[method, path] = handler
        self.rules.append(Rule(path, methods=[method], endpoint=(method, path)))

    def settings_panel(self, *, html, trigger="", badge="", script=None, stylesheet=None):
        if self.panel or not all(isinstance(text, str) and len(text) <= 64000 for text in (html, trigger, badge)):
            raise ValueError("An add-on may publish one settings panel.")
        self.panel = {"html": Markup(html), "trigger": Markup(trigger), "badge": Markup(badge)}
        for key, file, suffix in (("script", script, ".js"), ("stylesheet", stylesheet, ".css")):
            if file is None:
                continue
            path = self.directory / file
            if path.parent != self.directory or path.suffix != suffix or not _regular(path, stat.S_ISREG):
                raise ValueError("The settings panel must use files from its add-on directory.")
            content = path.read_text(encoding="utf-8")
            if len(content) > 256000:
                raise ValueError("The settings panel asset is too large.")
            mime = "application/javascript" if suffix == ".js" else "text/css"
            self.route("GET", "/" + path.name, lambda content=content, mime=mime: (content, 200, {"Content-Type": mime}))
            self.panel[key] = f"/addons/{self.name}/{path.name}"


@dataclass
class Registry:
    directory: Path
    loaded: dict = field(default_factory=dict)
    levels: dict = field(default_factory=dict)


_runtime = None


def registry():
    if has_app_context():
        return current_app.extensions.get("altera_addons", _runtime)
    return _runtime


def installed():
    value = registry()
    return bool(_folders(value.directory if value else root() / "addons"))


def active(store=None):
    from core import kids
    value = registry()
    if not value or not value.loaded:
        return None
    try:
        return None if kids.get_state(store)["enabled"] else value
    except Exception:
        return None


def panels():
    value = active()
    return [addon.panel for addon in value.loaded.values() if addon.panel] if value else []


def picture_source():
    value = active()
    return next((addon.studio for addon in value.loaded.values() if addon.studio), None) if value else None


def picture_engine(level):
    value = active()
    if value and level in value.levels:
        return next((addon.engines[level] for addon in value.loaded.values() if level in addon.engines), None)
    return None


def _load(folder, data, value):
    name = f"_altera_addon_{uuid4().hex}"
    host = Addon(folder.name, folder, data)
    try:
        spec = importlib.util.spec_from_file_location(name, folder / "__init__.py", submodule_search_locations=[str(folder)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        module.register(SimpleNamespace(
            name=host.name, data=host.data, add_level=host.add_level, picture_engine=host.picture_engine,
            picture_source=host.picture_source, route=host.route, settings_panel=host.settings_panel,
        ))
        if set(host.levels) & set(value.levels):
            raise ValueError("An add-on cannot replace another add-on's content level.")
        host.map = Map(host.rules)
        host.data.mkdir(parents=True, exist_ok=True)
    except Exception:
        for key in tuple(sys.modules):
            if key == name or key.startswith(name + "."):
                sys.modules.pop(key, None)
        LOGGER.exception("Altera skipped an add-on that could not register (%s).", folder.name)
        return
    value.loaded[folder.name] = host
    value.levels.update(host.levels)


def mount(app, value):
    app.extensions["altera_addons"] = value
    app.add_url_rule("/addons/<name>/<path:resource>", "addons.dispatch", dispatch,
                     methods=("GET", "POST", "PUT", "DELETE"))


def install(app, directory=None, *, load=True):
    from core import kids
    directory = Path(directory) if directory is not None else root() / "addons"
    value = Registry(directory)
    try:
        kids_enabled = kids.get_state()["enabled"]
    except Exception:
        kids_enabled = True
    if load and not kids_enabled:
        for folder in _folders(directory):
            _load(folder, root() / "data" / "addons" / folder.name, value)
    mount(app, value)
    if load:
        global _runtime
        _runtime = value


def dispatch(name, resource):
    value = active()
    addon = value.loaded.get(name) if value else None
    if addon is None:
        abort(404)
    method = "GET" if request.method == "HEAD" else request.method
    endpoint, values = addon.map.bind("").match("/" + resource, method=method)
    response = make_response(addon.routes[endpoint](**values))
    if response.mimetype not in {"application/json", "application/javascript", "text/css", "text/html", "text/plain"}:
        abort(415, "Add-on pictures must pass through Altera's picture service.")
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
