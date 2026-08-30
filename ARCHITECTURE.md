# Architecture

This document describes **how SSM is built today** — the pieces, how they talk
to each other, and where things live on disk. It is factual, not a
justification; the reasoning behind each choice (what problem it solved, what
was rejected, and its known limits) is in [`DECISIONS.md`](DECISIONS.md).

## Overview

SSM is a single-user, offline, Windows-only desktop app. It has three moving
parts running as one Windows process tree:

```
ssm.exe (viewer.py, frozen by PyInstaller)
 ├─ Flask app (server.py), on a background thread, bound to 127.0.0.1
 └─ Electron window (main.js/preload.js, generated at launch), pointed at
    that Flask URL, acting as the UI shell
```

There is no server to administer, no database, and no network component
beyond the loopback HTTP link between Electron and Flask on the same machine.

## Components

### Backend — `server.py`

A single-file Flask application. It:

- Renders the UI from server-side Jinja templates in `viewables/`.
- Exposes the JSON/form endpoints the templates' JavaScript calls (sales,
  clients, business, settings, CSV/PDF export, import/export bundle).
- Reads and writes the on-disk JSON data files directly — there is no ORM or
  data-access layer; route handlers call the `_load_*` / `_save_*` helpers.
- Generates receipt PDFs with fpdf2.

### Desktop shell — `viewer.py` + generated Electron app

`viewer.py` is the process entry point (and the PyInstaller entry point via
`ssm.spec`). On launch it:

1. Picks a free loopback port and starts the Flask app (`server.py`) on a
   background thread.
2. Waits for the HTTP port to come up.
3. Generates a throwaway Electron app — `package.json`, `main.js`,
   `preload.js` — into a temp directory (`_make_temp_electron_app`), with the
   session token, start URL, window title, theme and language baked into the
   generated `main.js`/`preload.js` source as literal values (see
   [`DECISIONS.md`](DECISIONS.md) for why these files are generated rather
   than static).
4. Spawns Electron pointed at that generated app.

`main.js` opens a single `BrowserWindow` with `contextIsolation: true` and
`nodeIntegration: false`, no menu bar, DevTools shortcuts suppressed, and
external links redirected to the system browser. `preload.js` exposes a small
bridge (`window.electronAPI`) for the handful of things the renderer needs
from the OS: native Save/Open dialogs, opening a file/folder in Explorer, and
theme/title sync.

For a packaged install, `viewer.py --bake-resources-app` additionally "bakes"
a static copy of that same generated app into `resources/app` at build time
(see `build_ssm.ps1`), so the app also works if the Electron binary is
launched directly (bypassing `ssm.exe`) instead of through the normal launch
path — its `requestSingleInstanceLock` / stdout URL handshake logic still
finds a running backend or starts one itself.

### UI — `viewables/`

Server-rendered Jinja templates (`base.html`, `management.html`,
`business.html`, `settings.html`, `welcome.html`, plus the `*popup.html`
variants), sharing:

- `app-common.js` — desktop-bridge helpers, popup/lock mechanics, and the
  DOM-facing half of the money input mask.
- `money-mask.js` — the pure "ATM-style" currency arithmetic behind that
  mask, loaded before `app-common.js` and shared with the Node test suite.

### Internationalization — `languages.py` + Flask-Babel

Two separate jobs, split between two mechanisms:

- **Message text** (every `{{ _('...') }}` in a template and every `_t(...)`
  call in `server.py`) comes from `languages.py`, a hand-maintained EN/PT/ES
  lookup table. `server.py`'s Jinja `context_processor` overrides the
  default `_` global to point at this table's `t()` function instead of
  Flask-Babel's gettext.
- **Locale negotiation and formatting** (which language is active, and
  localized date/number formatting) is handled by Flask-Babel
  (`babel.init_app(...)`, `get_locale()`, `babel.dates.*`). Precedence:
  `?lang=` query parameter → `lang` cookie → saved `preferred_locale`
  setting → OS locale → `Accept-Language` header.

`viewer.py` also imports from `languages.py` directly (outside any Flask
request) to build the Electron window title and the native dialog strings
baked into the generated `main.js`.

### Storage — local JSON files

No database. Data lives in a per-user directory (default
`%LOCALAPPDATA%\SimpleSalesManagement\data\`, relocatable from Settings):
`clients.json`, `sales.json`, `business.json`, `settings.json`,
`filters.json`. The whole file is loaded into memory and rewritten on every
change; `clients.json` is partly derived from `sales.json`
(`_sync_clients_db_from_sales`).

Every write goes through `_save_json`: write a sibling `<name>.json.tmp`,
then `Path.replace` it onto the target (atomic on the same filesystem), so a
crash mid-write cannot leave a truncated file. `_save_json` returns `False`
on failure instead of raising; the mutating endpoints check that return value
and answer with an HTTP error instead of a false "ok" (see the `CHANGELOG.md`
Unreleased section for the endpoints this covers).

### Export/import bundle

`/data/export` and `/data/import` exchange an `SSM_DATA_V1` bundle: the
payload (clients + sales + business) plus a `sig` block —
`{"alg": "HS256", "kid": ..., "mac": ...}` — computed with HMAC-SHA256 over
the canonical JSON encoding of the payload. The key lives in
`CONFIG_DIR/export_key.json`, protected at rest with the Windows DPAPI
(`CryptProtectData`/`CryptUnprotectData`) when available. A legacy plaintext
key file, if found, is read once and migrated to the protected format in
place — the key value does not change, so bundles signed before that
migration still verify.

### Security model

- The backend binds `127.0.0.1` only (hardcoded in `viewer.py`, not
  overridable by environment variable).
- A session token, generated fresh per launch (`secrets.token_urlsafe(24)`),
  is required on every request (`ssm_auth` cookie, `X-SSM-Token` header, or
  `?k=` query parameter), compared with `secrets.compare_digest`. A request
  without it gets `403`.
- The Inno Setup installer additionally adds Windows Firewall rules blocking
  inbound connections to both SSM executables.

### Build & packaging

- `requirements.txt` / `requirements-dev.txt` — pinned Python dependencies
  (runtime and dev/CI, respectively).
- `ssm.spec` — PyInstaller spec that freezes `viewer.py` (and, transitively,
  `server.py`) into `ssm.exe`.
- `build_ssm.ps1` — end-to-end packaging script: runs PyInstaller, copies
  `viewables/`, renames the bundled `electron.exe` to `SSM.exe`, and bakes
  `resources/app` (see above).
- `ssm_setup.iss` — Inno Setup 6 installer script. Localized (English /
  Portuguese (BR) / Spanish); the installer language sets the installed
  program name and seeds the app's initial language. It embeds `rcedit` (not
  committed to the repo — downloaded per the README's build instructions) to
  rewrite `SSM.exe`'s `ProductName`/`FileDescription` metadata at install
  time, so the taskbar and file properties show "Simple Sales Management" in
  the installer's language instead of the generic Electron binary identity.

### Tests & CI

- `tests/money_mask.test.js` (Node's built-in test runner) exercises the
  money-mask arithmetic in `viewables/money-mask.js`.
- `tests/test_server.py` (pytest) exercises `server.py`'s money parsing,
  signed-bundle verification, atomic JSON writes, export-key protection, and
  receipt-filename helpers; `tests/conftest.py` redirects `SSM_CONFIG_DIR` /
  `APP_DATA_DIR` to a temp directory before import so the suite never touches
  real data.
- `.github/workflows/ci.yml` runs both suites on `windows-latest`, plus a
  non-blocking `ruff` lint pass (`ruff.toml`).

## Known limitations

See the "Known limitations and technical debt" section in
[`README.md`](README.md) for the practical consequences of some of these
choices (file size, generated-code size, test coverage, etc.), and
[`DECISIONS.md`](DECISIONS.md) for the limit of each individual decision.
