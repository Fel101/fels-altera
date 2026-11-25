# Contributing

Thank you for helping. Bug reports, fixes and documentation corrections are all welcome. For a larger change, open an
issue first to describe it, so no one spends a weekend on something that doesn't fit.

Security problems go through private reporting instead: see [SECURITY.md](SECURITY.md).

## Getting set up

You need Python 3.11, 3.12 or 3.13 (CI runs all three) and, for the interface tests, Node.js (CI uses version 22).
From the repository folder:

```powershell
.\setup.bat
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

On Linux or macOS, use `sh setup.sh` and `.venv/bin/python` instead.

To try a change without touching your own chats, run Altera against an empty folder of its own. Altera copies the
starter settings into it, and `ALTERA_DISABLE_AUTOSTART=1` keeps it from starting the voice service or the background
schedulers:

```powershell
$env:ALTERA_HOME = "$env:TEMP\altera-try"
$env:ALTERA_DISABLE_AUTOSTART = "1"
.\.venv\Scripts\python.exe altera.py --open
```

## The checks

CI runs these on Windows and Linux for every push and pull request. Run them before you open one:

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest -q
node scripts/check_frontend.cjs
```

The full Python suite takes several minutes. While you work, run just the files you touched, for example
`python -m pytest -q tests/test_setup.py`.

The tests never reach a real model, the network or your own data. `tests/conftest.py` gives each test a temporary
`ALTERA_HOME` with the starter settings and fake model calls. Please keep it that way: a new test that needs a model,
a voice or a picture uses a fake one.

Tests for a separately installed add-on are marked `requires_addon`. Set `ALTERA_TEST_ADDON` to the package folder
and run `python -m pytest -q -m requires_addon` to include its integration checks.

## Fel's Studio ports

| Service | Port |
| --- | ---: |
| Voice Lab | 8765 |
| NPC Forge | 8766 |
| Altera | 8767 for new installs |
| Second copy of NPC Forge | 8768 |
| Voice Lab MCP over HTTP | 8769 |
| Altera voice service | 8770 |
| ComfyUI | 8188 |
| LM Studio | 1234 |
| Ollama | 11434 |

Use 8775 when an example needs an alternate port. Existing Altera instances keep the port in `config.yaml`.

## Suite commits

Use one local branch per suite step. Keep commits small, use a short imperative subject, and record the checks you ran
in the commit body. Fast-forward local `main` when the step's checks pass.

## Some house rules

- **Add a test with a fix**, ideally one that fails without it.
- **Keep the docs true.** If you change what something does, change the page in `docs/` that describes it. Several tests
  read the documentation and fail when it falls behind.
- **Bump the service worker when you change a script or style.** Browsers keep the old files from `static/` until
  `CACHE_VERSION` in `static/sw.js` changes, so raise it in any change there. Pages from `templates/` are not cached.
- **Line endings** follow `.gitattributes`: LF everywhere, CRLF for `.bat` files.
- **Nothing personal in the repository.** `config.yaml`, `.env`, `user_profile.yaml` and `data/` are ignored for a
  reason. Use made-up names and conversations in tests, screenshots and bug reports.
- **No downloads behind anyone's back.** Altera never downloads a model or sends anything out unless the person chose
  it. A new feature that talks to the network needs a setting that starts off, and a line in the README's privacy
  section.

[Architecture](docs/ARCHITECTURE.md) explains how the pieces fit together, and [the user guide](docs/README.md) lists
every page of documentation.

## Licence

Altera is MIT-licensed. By sending a contribution you agree that it is released under the same [licence](LICENSE).
