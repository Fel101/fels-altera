"""
Altera — NPC Forge, where every companion is made.

Altera discovers NPC Forge's local web server to make someone new: it asks for a "Models" character
(an adult woman with a personality, made from the shared tables), waits for the portrait, and then imports her like any other
avatar (core/avatars.py reads the character from Forge's library folder and the age check runs). Characters made by hand in NPC
Forge are imported the same way.

Pals and characters can have a portrait painted by NPC Forge too (core/portraits.py): from a description, or redrawn from a
card's picture. Those use NPC Forge's ordinary settings, never the adult "models" one, and need no 18+ box.

From NPC Forge's API 44 on, Altera checks GET /api/gpu before queuing a picture. Suite Forge coordinates exclusive use
itself. With the older GPU API, Altera can first unload local chat models (companion.forge.unload_llm, on by default).
When ComfyUI or another program needs attention, Altera reports the blocker. Older NPC Forge versions report GPU errors
in their job text.

Only loopback literals and the localhost name are accepted: NPC Forge is a local tool that spends GPU time and deletes files.
The operating system resolves localhost.
"""
import glob
import http.client
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from core import fels_suite

LOGGER = logging.getLogger(__name__)

DEFAULT_URL = f"http://127.0.0.1:{fels_suite.PORTS['npc-forge']}"
REQUIRED_API = 4                       # the NPC Forge API version that has personalities (speech, demeanor)
GPU_API = 44                           # GET /api/gpu, job and step `code`, gpu_busy `holders`, roll `adult`, an empty surname
SETTING = "models"                     # the setting made for Altera: adult women, ordinary clothes, photoreal
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
ALLOWED = {"origin": str, "hair_color": str, "age_group": str}     # what the person can ask for
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_PHOTO_BYTES = 1_400_000            # NPC Forge reads at most 2 MB of request, and a picture travels as base64
MAX_DESCRIPTION = 1500                 # NPC Forge keeps this much of a free description
GENDERS = ("female", "male", "nonbinary")
GPU_BUSY = "one model on the GPU at a time"   # before API 44: in NPC Forge's refusal when something else holds the GPU
GPU_SETTLE_S = 15                      # how long local models get to hand back the GPU after unloading
COMFY_BUSY_CHECKS = 2
_CHAT_PROCESS = re.compile(r"llama-server|lm ?studio|^lms(\.exe)?$|ollama", re.I)
GPU_ADVICE = {
    "comfy_down": "ComfyUI is not running, so NPC Forge cannot paint. Start ComfyUI, then try again.",
    "comfy_busy": "ComfyUI is busy with another job. Try again when it is done.",
    "gpu_comfy_held": "ComfyUI is holding on to GPU memory it was told to free. Restart ComfyUI, then try again.",
}
ENGINE_KINDS = ("image", "edit")
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,127}$")
_CHOICE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


def adult_lead(text) -> str:
    """The words an NPC Forge older than API 44 paints a portrait from, led by "a grown adult". A free description is the whole
    picture prompt there (the age it rolls is only for its data sheet), and a cartoon from a few words can otherwise come out
    looking like a child. From API 44 the roll asks for `adult` and NPC Forge leads with it itself. The age check still looks
    at every portrait."""
    text = str(text or "").strip()
    if not text or text == "a character":
        return "a grown adult"
    return "a grown adult, " + re.sub(r"^(A|An|The)(?=\s)", lambda m: m.group(1).lower(), text)


def _unload_llm() -> bool:
    from core.llm import unload_models
    return unload_models()


def _holder_names(holders):
    return list(dict.fromkeys(h["name"] for h in (holders or [])
                              if isinstance(h, dict) and isinstance(h.get("name"), str) and h["name"]))


class ForgeError(RuntimeError):
    """Something the person can act on; the message is safe to show."""


class ForgeNotListening(ForgeError):
    pass


class ForgeHTTPError(ForgeError):
    def __init__(self, status):
        self.status = status
        super().__init__(f"NPC Forge answered with an error (HTTP {status}).")


def settings(config=None) -> dict:
    cfg = ((config or {}).get("companion") or {}).get("forge") or {}
    return {"url": str(cfg.get("url") or "").rstrip("/"), "timeout": float(cfg.get("timeout_s") or 4)}


def is_loopback(url: str) -> bool:
    if (not isinstance(url, str) or not url
            or any(ord(char) <= 32 or ord(char) == 127 for char in url)
            or any(char in url for char in ('\\', '?', '#'))):
        return False
    try:
        parsed = urllib.parse.urlsplit(url)
        return (parsed.scheme in {"http", "https"} and parsed.hostname in LOOPBACK
                and parsed.username is None and parsed.password is None
                and parsed.port != 0)
    except ValueError:
        return False


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ForgeClient:
    def __init__(self, config=None, opener=None, unload=None, sleep=None):
        self.config = config or {}
        cfg = settings(self.config)
        self._given, self.timeout = cfg["url"], cfg["timeout"]
        self.url = self._given or DEFAULT_URL
        self._open = opener
        forge_cfg = ((self.config.get("companion") or {}).get("forge") or {})
        self.unload_llm = forge_cfg.get("unload_llm", True) is not False
        self._unload = unload or _unload_llm
        self._sleep = sleep or time.sleep
        self._api_version = None
        self._discovery = None

    # ---- plumbing -------------------------------------------------------------------------------------------------
    def _call(self, method: str, path: str, body: dict | None = None, timeout: float | None = None):
        self._discover()
        return self._request_at(self.url, method, path, body, timeout)

    def _discover(self):
        if self._discovery is not None:
            return self._discovery
        if self._given and not is_loopback(self._given):
            raise ForgeError("NPC Forge must be on this computer (127.0.0.1).")
        problems = []

        def ask(url):
            try:
                health = self._ask_health(url)
            except ForgeNotListening:
                return None
            except ForgeError as error:
                problems.append(str(error))
                return None
            found = fels_suite.identify(health)
            if found != "npc-forge":
                name = fels_suite.short_name(found) if found else "another program"
                problems.append(f"{name} answered at the NPC Forge address. Check the address in config.yaml.")
            return health

        found = fels_suite.find_app("npc-forge", ask=ask, given=self._given)
        if not found["url"]:
            if problems:
                raise ForgeError(problems[0])
            raise ForgeNotListening("NPC Forge is not running. Start it, then try again.")
        self.url = found["url"]
        self._discovery = found
        return found

    def _ask_health(self, url):
        timeout = min(self.timeout, 2.5)
        try:
            return self._request_at(url, "GET", "/api/health?identity=1", timeout=timeout)
        except ForgeHTTPError as error:
            if error.status not in {400, 404}:
                raise
        return self._request_at(url, "GET", "/api/health", timeout=timeout)

    def _request_at(self, url, method, path, body=None, timeout=None):
        if not is_loopback(url):
            raise ForgeError("NPC Forge must be on this computer (127.0.0.1).")
        data = json.dumps(body).encode("utf-8") if body is not None else (b"{}" if method != "GET" else None)
        request = urllib.request.Request(url + path, data=data, method=method,
                                         headers={"Content-Type": "application/json"} if method != "GET" else {})
        try:
            open_request = self._open or urllib.request.build_opener(
                urllib.request.ProxyHandler({}), _NoRedirect()).open
            with open_request(request, timeout=timeout or self.timeout) as response:
                length = response.headers.get("Content-Length")
                if length is not None:
                    size = int(length)
                    if size < 0 or size > MAX_RESPONSE_BYTES:
                        raise ForgeError("NPC Forge returned an invalid or oversized response.")
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES or (length is not None and len(raw) != size):
                    raise ForgeError("NPC Forge returned an invalid or oversized response.")
                return json.loads(raw or b"{}")
        except urllib.error.HTTPError as e:
            code = e.code
            e.close()
            raise ForgeHTTPError(code) from None
        except OSError as error:
            if isinstance(getattr(error, "reason", error), ConnectionRefusedError):
                raise ForgeNotListening("NPC Forge is not running. Start it, then try again.") from None
            raise ForgeError("Could not reach NPC Forge. Check its address and try again.") from None
        except (http.client.HTTPException, ValueError, UnicodeError):
            raise ForgeError("NPC Forge returned an invalid or oversized response.") from None

    # ---- what is available ----------------------------------------------------------------------------------------
    def status(self) -> dict:
        """{online, api_version, outdated, models, comfy_online, message, options}. Never raises."""
        out = {"online": False, "api_version": None, "outdated": False, "models": False, "comfy_online": None,
               "message": "", "not_running": False, "options": {}, "url": self.url, "styles": [], "settings": [],
               "engines": {"image": [], "edit": []}, "default_engines": {"image": "", "edit": ""}}
        try:
            found = self._discover()
            out.update(online=True, url=self.url, comfy_online=found["health"].get("online"),
                       notices=[note for note in found["notes"] if note.startswith("That address")])
            cfg = self._request_at(self.url, "GET", "/api/config", timeout=min(self.timeout, 2.5))
            if not isinstance(cfg, dict) or type(cfg.get("api_version")) is not int:
                raise ForgeError("NPC Forge returned an invalid configuration. Restart it and try again.")
        except ForgeError as e:
            out["online"] = False
            out["not_running"] = isinstance(e, ForgeNotListening)
            out["message"] = str(e)
            return out
        out["online"] = True
        out["api_version"] = cfg.get("api_version")
        self._api_version = out["api_version"] or 0
        out["outdated"] = (cfg.get("api_version") or 1) < REQUIRED_API
        setting = (cfg.get("settings") or {}).get(SETTING)
        out["models"] = bool(setting)
        # what a pal's or a character's portrait can use: every art style, and every setting but the adult one
        out["styles"] = [{"id": k, "label": str((v or {}).get("label") or k)} for k, v in (cfg.get("styles") or {}).items()
                         if isinstance(k, str) and _CHOICE.match(k)]
        out["settings"] = [{"id": k, "label": str((v or {}).get("label") or k)} for k, v in (cfg.get("settings") or {}).items()
                           if isinstance(k, str) and _CHOICE.match(k) and k != SETTING]
        # the image engines (a picture from words) and edit engines (a redrawn photo, the living portrait) NPC Forge has, and
        # the ones its own settings use when none is chosen
        engines = cfg.get("engines") if isinstance(cfg.get("engines"), dict) else {}
        defaults = ((cfg.get("defaults") or {}).get("options") or {}) if isinstance(cfg.get("defaults"), dict) else {}
        for kind in ENGINE_KINDS:
            rows = engines.get(kind) if isinstance(engines.get(kind), list) else []
            out["engines"][kind] = [{"id": e["id"], "label": str(e.get("label") or e["id"]), "note": str(e.get("note") or "")}
                                    for e in rows if isinstance(e, dict) and isinstance(e.get("id"), str) and _CHOICE.match(e["id"])]
            chosen = defaults.get(f"{kind}_model")
            out["default_engines"][kind] = chosen if chosen in {e["id"] for e in out["engines"][kind]} else ""
        if setting:
            out["options"] = {
                "origins": [{"id": o["id"], "label": o["label"]} for o in setting.get("origins") or []],
                "hair_colors": list(setting.get("hair_colors") or []),
                "age_groups": [{"id": a["id"], "label": a["label"]} for a in setting.get("age_groups") or []],
            }
        if out["comfy_online"] is None:
            try:
                out["comfy_online"] = self._request_at(self.url, "GET", "/api/health", timeout=2.5).get("online")
            except (ForgeError, AttributeError):
                pass
        if not setting:
            out["message"] = ("NPC Forge has no Models setting right now. Open NPC Forge, choose About, and tick the 18+ box "
                              "(the Models setting appears once it is ticked).")
        elif out["outdated"]:
            out["message"] = ("NPC Forge is an older version that does not give characters a personality of their own yet. "
                              "Restart NPC Forge to get the new one.")
        elif out["comfy_online"] is False:
            out["message"] = "ComfyUI is not reachable, so NPC Forge cannot paint a portrait. Start ComfyUI first."
        return out

    # ---- the GPU (API 44) ---------------------------------------------------------------------------------------------
    @staticmethod
    def modern(status) -> bool:
        return (status.get("api_version") or 0) >= GPU_API

    def gpu(self) -> dict | None:
        """NPC Forge's GET /api/gpu, or None when it cannot be read (then the picture is tried anyway)."""
        try:
            answer = self._call("GET", "/api/gpu", timeout=max(self.timeout, 5))
        except ForgeError:
            return None
        return answer if isinstance(answer, dict) else None

    def free_gpu(self, holders) -> bool:
        """For legacy Forge, unload the local chat models when they hold the GPU and the user permits it."""
        if (not self.unload_llm or (self._discovery and self._discovery["health"].get("fels"))
                or not any(_CHAT_PROCESS.search(name) for name in _holder_names(holders))):
            return False
        LOGGER.info("Local chat models hold the GPU NPC Forge needs: asking them to unload")
        return bool(self._unload())

    def ready_gpu(self, status, *, require_ready=False) -> None:
        """Check GPU readiness before queueing, with legacy model cleanup when needed."""
        if not self.modern(status):
            return
        gpu = self.gpu()
        for _ in range(COMFY_BUSY_CHECKS):
            if not gpu or gpu.get("ready") is not False or gpu.get("code") != "comfy_busy":
                break
            self._sleep(2)
            gpu = self.gpu() or gpu
        if not gpu or gpu.get("ready") is not False:
            if (require_ready or status.get("comfy_online") is False) and (not gpu or gpu.get("ready") is not True):
                raise ForgeError("Could not confirm that ComfyUI is ready. Check ComfyUI and try again.")
            return
        code = gpu.get("code")
        if code == "gpu_busy" and isinstance(gpu.get("suite_lock"), dict) and gpu.get("one_model_at_a_time"):
            return  # Suite Forge queues the work, then makes room for it with the shared lock protocol.
        if code == "gpu_busy" and self.free_gpu(gpu.get("holders")):
            for _ in range(GPU_SETTLE_S):
                self._sleep(1)
                checked = self.gpu()
                if checked and checked.get("ready") is True:
                    return
                if checked and checked.get("ready") is False:
                    gpu = checked
                    if gpu.get("code") != "gpu_busy":
                        break
            code = gpu.get("code")
        message = str(gpu.get("message") or "").strip()
        holders = _holder_names(gpu.get("holders"))
        names = ", ".join(holders)
        if code in GPU_ADVICE:
            message = GPU_ADVICE[code] + (f" (NPC Forge: {message})" if message else "")
        elif code == "gpu_busy" and names:
            message = (message + " " if message else "") + f"Current GPU use: {names}."
            tools = ["Ollama" if "ollama" in name.lower() else "LM Studio" for name in holders if _CHAT_PROCESS.search(name)]
            if tools:
                message += f" Unload its model in {' or '.join(dict.fromkeys(tools))}, then try again."
            else:
                message += " Wait for the work to finish, then try again."
            names = ""
        if names:
            message += f" Current GPU use: {names}."
        raise ForgeError(message or "The GPU is not free for NPC Forge right now. Try again in a moment.")

    def retry(self, job_id) -> None:
        """Run a failed job again, from the steps that did not finish (NPC Forge keeps its id)."""
        if not isinstance(job_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", job_id):
            raise ForgeError("NPC Forge lost track of that job.")
        self._call("POST", f"/api/jobs/{job_id}/retry", {}, timeout=30)

    def retry_gpu_job(self, job_id) -> None:
        """Confirm the GPU is ready before resuming a job refused by API 44 or later."""
        status = {"api_version": self._api_version}
        if not self.modern(status):
            raise ForgeError("This NPC Forge version cannot check GPU readiness before retrying.")
        self.ready_gpu(status, require_ready=True)
        self.retry(job_id)

    # ---- making someone -------------------------------------------------------------------------------------------
    @staticmethod
    def clean_constraints(constraints) -> dict:
        out = {}
        for key, kind in ALLOWED.items():
            value = (constraints or {}).get(key)
            if isinstance(value, kind) and value.strip() and value.strip().lower() not in ("any", "surprise"):
                out[key] = value.strip()[:60]
        return out

    def make_someone(self, constraints=None) -> dict:
        """Ask NPC Forge for one new Models character and her portrait. Returns {npc_id, job_id}."""
        status = self.status()
        if not status["online"] or not status["models"] or (status["comfy_online"] is False and not self.modern(status)):
            raise ForgeError(status["message"] or "NPC Forge cannot make someone new right now.")
        self.ready_gpu(status)
        answer = self._call("POST", "/api/generate", {
            "setting": SETTING, "style": "photoreal", "count": 1,
            "constraints": self.clean_constraints(constraints),
            "assets": {"portrait": True, "sprite": False, "expressions": []},
            "options": {"quality": "standard"},
        }, timeout=30)
        ids, jobs = answer.get("npc_ids") or [], answer.get("jobs") or []
        if not ids or not jobs:
            raise ForgeError("NPC Forge did not start a portrait.")
        return {"npc_id": ids[0], "job_id": jobs[0].get("id")}

    # ---- a portrait for a pal or a character ---------------------------------------------------------------------------
    @staticmethod
    def _engine(status, kind, chosen) -> dict:
        """{image_model|edit_model: id} for a chosen engine NPC Forge has; {} for '' (NPC Forge's own default)."""
        if not chosen:
            return {}
        if chosen not in {e["id"] for e in status["engines"][kind]}:
            raise ForgeError("Choose one of NPC Forge's engines, or its default.")
        return {f"{kind}_model": chosen}

    def paint(self, *, name, description="", gender="", style, setting, photo=None, image_model="", edit_model="") -> dict:
        """Have NPC Forge make a character from a free description (or redraw `photo`, PNG bytes, keeping its likeness) and
        paint its portrait. Returns {npc_id, job_id}. The description is what the picture is drawn from; a photo keeps the
        face and redraws the rest in the style. image_model draws from words, edit_model redraws a photo ('' for NPC Forge's
        own choice)."""
        status = self.status()
        if not status["online"] or (status["comfy_online"] is False and not self.modern(status)):
            raise ForgeError(status["message"] or "NPC Forge cannot paint a portrait right now.")
        if style not in {s["id"] for s in status["styles"]}:
            raise ForgeError("Choose one of NPC Forge's art styles.")
        if setting not in {s["id"] for s in status["settings"]}:
            raise ForgeError("Choose one of NPC Forge's settings.")
        engine = self._engine(status, "edit", edit_model) if photo is not None else self._engine(status, "image", image_model)
        description = str(description or "").strip()[:MAX_DESCRIPTION]
        if not description and photo is None:
            raise ForgeError("Describe how they look first.")
        if photo is not None and len(photo) > MAX_PHOTO_BYTES:
            raise ForgeError("The card's picture is too big to send to NPC Forge (over 1.4 MB). "
                             "Make the portrait from a description instead.")
        modern = self.modern(status)
        self.ready_gpu(status)                     # before the character is made, so a refusal leaves nothing behind
        locked = {"profile.first_name": str(name or "")[:40].strip() or "Someone"}
        if modern:
            locked["profile.last_name"] = ""       # the whole name is the first name: no rolled surname after it
        if gender in GENDERS:
            locked["profile.gender"] = gender      # only when chosen: otherwise NPC Forge reads it from the description
        # A free description, even with a photo: the photo is then what the face is kept from, and nothing is rolled
        # for the picture (no outfit or marks from the setting's tables).
        roll = {"setting": setting, "style": style, "locked": locked,
                "freeform": (description or "a character") if modern else adult_lead(description)}
        if modern:
            roll["adult"] = True                   # never under 18, and the picture is led by "a grown adult woman/man"
        draft = self._call("POST", "/api/roll", roll, timeout=30)
        made = self._call("POST", "/api/npcs", draft, timeout=30)
        npc_id = made.get("id") if isinstance(made, dict) else None
        if not isinstance(npc_id, str) or not _ID.match(npc_id):
            raise ForgeError("NPC Forge did not make the character.")
        if photo is not None:
            import base64
            self._call("POST", f"/api/npcs/{npc_id}/photo",
                       {"image": "data:image/png;base64," + base64.b64encode(photo).decode("ascii")}, timeout=60)
        result = {"npc_id": npc_id, "job_id": self._start(npc_id, style=style, options=engine,
                                                       assets={"portrait": True, "sprite": False, "expressions": []})}
        if modern:
            profile = made.get("profile") or draft.get("profile") or {}
            if type(profile.get("age")) is int and profile.get("age_source") in ("rolled", "chosen", "locked"):
                result.update(age=profile["age"], age_source=profile["age_source"])
        return result

    def living(self, npc_id, edit_model="") -> str:
        """Have NPC Forge make the living portrait (the expressions it needs, then the frames) of a character it painted,
        with `edit_model` ('' for NPC Forge's own choice). Returns the job id."""
        if not isinstance(npc_id, str) or not _ID.match(npc_id):
            raise ForgeError("That is not one of NPC Forge's characters.")
        status = self.status()
        if not status["online"] or (status["comfy_online"] is False and not self.modern(status)):
            raise ForgeError(status["message"] or "NPC Forge cannot make a living portrait right now.")
        self.ready_gpu(status)
        return self._start(npc_id, assets={"living": True}, options=self._engine(status, "edit", edit_model))

    def _start(self, npc_id, assets, style=None, options=None) -> str:
        body = {"npc_id": npc_id, "assets": assets, "options": {"quality": "standard", **(options or {})}}
        if style:
            body["style"] = style
        answer = self._call("POST", "/api/generate", body, timeout=30)
        jobs = (answer.get("jobs") or []) if isinstance(answer, dict) else []
        job_id = jobs[0].get("id") if jobs and isinstance(jobs[0], dict) else None
        if not isinstance(job_id, str) or not job_id:
            raise ForgeError("NPC Forge did not start the job.")
        return job_id

    def job(self, job_id, *, npc_id=None) -> dict:
        """{state: queued|running|done|error|cancelled, note, code, holders, blocked}. The note is the step running, and the
        first step that failed with its reason: that is the cause, as later steps often fail because of it. `code` is NPC
        Forge's reason code (API 44: gpu_busy, gpu_comfy_held, comfy_busy, comfy_down, no_model, low_memory, stalled, error,
        cancelled; '' before), and `holders` the programs on the GPU for gpu_busy. `blocked` is, for an NPC Forge older than
        API 44 only, its refusal when a step could not have the GPU (it then went on to the next step; newer ones stop)."""
        if self._api_version is None:
            cfg = self._call("GET", "/api/config", timeout=min(self.timeout, 2.5))
            self._api_version = cfg.get("api_version") or 0
        modern = self.modern({"api_version": self._api_version})
        jobs = self._call("GET", "/api/jobs")
        found = next((j for j in jobs if j.get("id") == job_id and (npc_id is None or j.get("npc_id") == npc_id)), None)
        if not found:
            return {"state": "error", "note": "NPC Forge lost track of that portrait (it may have been restarted).",
                    "code": "", "holders": [], "blocked": ""}
        running, failed, cancelled_reason = "", None, ""
        for step in found.get("steps") or []:
            if step.get("status") == "running":
                running = step.get("label") or ""
            if step.get("status") == "error" and step.get("error") and failed is None:
                failed = step
            if modern and step.get("status") == "cancelled" and not cancelled_reason:
                cancelled_reason = step.get("reason") or ""
        state = found.get("status") or "queued"
        reason = str(failed["error"]) if failed else ""
        if state == "error":
            note = reason or found.get("error") or "The portrait could not be painted."
        elif failed and state in ("queued", "running"):
            note = f"{running}. {failed.get('label') or 'A step'} could not be made: {reason}" if running else reason
        elif modern and state == "cancelled":
            note = found.get("error") or cancelled_reason
        else:
            note = running
        code = str(found.get("code") or (failed or {}).get("code") or "") if modern else ""
        holders = (failed or {}).get("holders") if code == "gpu_busy" else None
        return {"state": state, "note": note, "code": code,
                "holders": holders if isinstance(holders, list) else [],
                "blocked": reason if not modern and GPU_BUSY in reason else ""}

    def cancel(self, job_id) -> None:
        if not isinstance(job_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", job_id):
            return
        try:
            self._call("POST", f"/api/jobs/{job_id}/cancel", {})
        except ForgeError:
            pass


# ---- starting NPC Forge -----------------------------------------------------------------------------------------------
def _port_open(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    try:
        with socket.create_connection((parsed.hostname or "127.0.0.1", parsed.port or 80), timeout=0.6):
            return True
    except OSError:
        return False


def forge_folder(config=None) -> Path | None:
    """NPC Forge's folder: the parent of its library (imagegen.npc_forge_library)."""
    library = (((config or {}).get("imagegen") or {}).get("npc_forge_library"))
    folder = Path(library).parent if library else None
    return folder if folder and (folder / "server.py").is_file() else None


def _forge_python(config=None):
    """The command that runs NPC Forge: the first Python that has Pillow and numpy (Altera's own environment has neither, and when Altera
    was started from an activated environment `python` on the PATH is that one). companion.forge.python can name one."""
    candidates = []
    named = (((config or {}).get("companion") or {}).get("forge") or {}).get("python")
    if named:
        candidates.append([str(named)])
    if shutil.which("py"):
        candidates.append(["py", "-3"])
    if shutil.which("python"):
        candidates.append(["python"])
    if os.environ.get("LOCALAPPDATA"):
        candidates += [[p] for p in sorted(glob.glob(os.path.join(os.environ["LOCALAPPDATA"], "Programs", "Python", "Python3*", "python.exe")), reverse=True)]
    for command in candidates:
        try:
            done = subprocess.run(command + ["-c", "import PIL, numpy"], capture_output=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            continue
        if done.returncode == 0:
            return command
    return None


def start(config=None) -> dict:
    """Start NPC Forge on its configured or suite port if it is not already running."""
    client = ForgeClient(config)
    try:
        client._discover()
    except ForgeError as error:
        if not is_loopback(client.url):
            return {"started": False, "message": str(error)}
        if _port_open(client.url):
            return {"started": False, "message": f"The NPC Forge port is already in use. {error}"}
    else:
        return {"started": False, "message": "NPC Forge is already running."}
    folder = forge_folder(config)
    python = _forge_python(config) if folder else None
    if not folder or not python:
        return {"started": False, "message": "Could not find NPC Forge's folder, or a Python that has Pillow and numpy. "
                                             "Start NPC Forge yourself (start.bat)."}
    port = urllib.parse.urlparse(client.url).port or fels_suite.PORTS["npc-forge"]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    try:
        subprocess.Popen(python + ["server.py", "--port", str(port)], cwd=str(folder), creationflags=flags,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        LOGGER.warning("Could not start NPC Forge: %s", e)
        return {"started": False, "message": "NPC Forge could not be started. Start it yourself (start.bat)."}
    return {"started": True, "message": "Starting NPC Forge. This takes a few seconds."}
