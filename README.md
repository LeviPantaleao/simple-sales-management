# SSM — Simple Sales Management

[![CI](https://github.com/LeviPantaleao/simple-sales-management/actions/workflows/ci.yml/badge.svg)](https://github.com/LeviPantaleao/simple-sales-management/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A small offline Windows desktop app for a single small business to record
clients, register sales with an optional discount, and print a receipt as a
58 mm thermal-roll PDF.

---

## What it does

- **Clients.** Register clients with a six-digit code, name, address, contact
  and a free-text description. The client list is kept in sync with the sales
  history, so a client seen in a sale always appears in the client list.
- **Sales.** Register a sale against a new or existing client: product, amount,
  and an optional discounted final amount (the app rejects a final amount
  larger than the original). A "no discount" flag keeps the sale at full price.
  Sales can be filtered and searched by code, product or description, and by
  month/year.
- **Receipts.** Generate a PDF receipt for a sale in the classic SSM layout:
  a 58 mm-wide thermal-roll format (page size 58 × 210 mm) with an optional
  business logo at the top, the business header (name, phone, address, tax/zip
  line), the client block, the sale block with original price struck through
  and the discounted price shown when a discount applies, and a dated footer.
  Currency and date formatting follow the active language.
- **Import / export.** Client and sales data can be exported to and imported
  from a JSON bundle (a legacy `data.json` shape and an HMAC-signed bundle
  format are both accepted on import).

There are no user accounts and no cloud component.

---

## Architecture

- **Backend:** a Flask application (`server.py`) that renders the UI from
  server-side templates in `viewables/` and exposes the JSON/form endpoints the
  UI calls. PDF receipts are produced with fpdf2.
- **Desktop shell:** the backend is frozen with PyInstaller (`viewer.py` is the
  entry point; see `ssm.spec` / `build_ssm.ps1`) and launched inside an
  Electron 43.1.0 window. `viewer.py` starts Flask on a loopback port in a
  background thread, waits for it to come up, then spawns Electron pointed at
  that URL. Electron is configured as a plain app window — no menu bar, DevTools
  shortcuts suppressed, external links opened in the system browser, and native
  Save/Open dialogs wired through to the renderer.
- **Storage:** local-first, plain JSON files, no database. Data lives in a
  per-user data directory (by default
  `%LOCALAPPDATA%\SimpleSalesManagement\data\`, relocatable from Settings):
  `clients.json`, `sales.json`, `business.json`, `settings.json`,
  `filters.json`. Every write goes through a small atomic-write helper that
  writes a sibling `.tmp` file and then atomically replaces the target
  (`os.replace` semantics), so a crash mid-write cannot truncate the live file.
- **Packaging:** a Windows installer built with Inno Setup 6 (`ssm_setup.iss`).
  The installer is localized (English / Portuguese (BR) / Spanish); the chosen
  language sets the installed program name and seeds the app's initial
  language. It also adds Windows Firewall rules that block inbound connections
  to the SSM executables as defence in depth, and — once, during installation —
  uses an embedded `rcedit` to write the localized program name into the
  `SSM.exe` metadata.

---

## Internationalization

The UI is available in **Brazilian Portuguese, English and Spanish**.

- UI strings come from `languages.py`, a hand-maintained EN/PT/ES lookup table.
  English is the source text; a missing key falls back to English. Optional
  external override packs can be dropped in as
  `data/fallback_locales/pt.json` / `es.json`.
- **Flask-Babel** performs locale negotiation. The active language is chosen in
  this order: `?lang=` query parameter → `lang` cookie → saved
  `preferred_locale` setting → OS locale → `Accept-Language` header.
- **Babel** provides localized month names and date formatting.

---

## Security model

SSM is meant to be reachable only by its own Electron window on the machine it
runs on.

- **Loopback only.** The backend binds `127.0.0.1` (this is fixed in
  `viewer.py` and not overridable by environment variable). The listening port
  is a free port scanned at launch (a specific port can be forced with the
  `APP_PORT` environment variable, still on loopback).
- **Per-run session token.** On each launch, `viewer.py` generates a fresh
  token with `secrets.token_urlsafe(24)` and passes it to the backend via the
  `APP_SESSION_TOKEN` environment variable.
- **Token required on every request.** A Flask `before_request` hook requires
  every request to present the token — as the `ssm_auth` cookie, an
  `X-SSM-Token` header, or a `?k=` query parameter — and compares it with
  `secrets.compare_digest` (constant-time). A request without the valid token
  gets `403 Forbidden`. On the first authenticated request the
  token is set as an `HttpOnly`, `SameSite=Strict` cookie, so later requests
  carry it automatically. The Electron main process also injects the
  `X-SSM-Token` header on every outgoing request. Flask's `secret_key` is
  derived from the same token.
- **Consequence.** Another user or process on the same machine opening
  `http://127.0.0.1:<port>` in a browser has no token and receives `403`.
- **Always on.** The check is registered unconditionally. If
  `APP_SESSION_TOKEN` is not supplied by the environment (for example running
  `server.py` on its own during development), the backend generates its own
  token at startup and logs it, so the lock is still enforced — copy that token
  and pass it once as `?k=<token>`.
- **Installer hardening.** The Inno Setup installer adds inbound-block Windows
  Firewall rules for both SSM executables; since the backend only binds
  loopback this is a safety net, and it also suppresses any Windows "allow
  network access?" prompt.

---

## Install (end users)

Download `SSM-Setup.exe` from the project's
[GitHub Releases](https://github.com/LeviPantaleao/simple-sales-management/releases)
and run it. The installer is not stored in the Git repository — it is a
release asset (roughly 190 MB, because it contains the Electron runtime).

The setup wizard asks for a language (English / Portuguese (BR) / Spanish),
which determines the installed program name and the language the app starts in.
Administrator rights are required (the installer writes firewall rules and
program metadata).

---

## Build from source

### Prerequisites

- Windows x64
- Python 3.13 (developed with 3.13.7)
- Node.js ≥ 22.12 (developed with 24.x)
- Inno Setup 6+ — only for the installer step

### Steps

```powershell
# 1. Python environment
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 2. Electron (downloads the 43.1.0 runtime into node_modules)
npm install

# 3. rcedit — NOT in the repo, download it manually
#    Get rcedit-x64.exe from https://github.com/electron/rcedit/releases
#    and place it at:  tools\rcedit-x64.exe

# 4. Package the backend + Electron into dist\ssm\
powershell -ExecutionPolicy Bypass -File .\build_ssm.ps1

# 5. Build the installer -> installer_output\SSM-Setup.exe
#    Open ssm_setup.iss in the Inno Setup Compiler, or:
iscc ssm_setup.iss
```

To run without packaging, install the Python and npm dependencies (steps 1–2)
and start the app directly:

```powershell
.\.venv\Scripts\python.exe viewer.py
```

This launches Flask and opens the Electron window using the Electron binary in
`node_modules`.

### Tests

One test exists, covering the cash-register-style money input mask used in the
sale form:

```powershell
npm test        # runs: node --test tests/money_mask.test.js
```

---

## License

SSM is released under the MIT License — see [`LICENSE`](LICENSE).

The application bundles third-party software that remains under its own
license, including **fpdf2 (LGPL-3.0-only)**, which is frozen into the
distributed executable. See [`THIRD_PARTY_LICENSES.md`](THIRD_PARTY_LICENSES.md)
for the full inventory and for how the project meets the LGPL obligations
(notice, upstream source links, and the build files plus rebuild instructions
that let you relink against a different version of the library). Verbatim
copyleft license texts are in [`LICENSES/`](LICENSES/).

---

## AI assistance disclosure

This project was built with the help of Claude and Claude Code, under my
direction. Commits made with that assistance carry a
`Co-Authored-By: Claude` trailer, kept deliberately as a transparent record of
how the work was done.
