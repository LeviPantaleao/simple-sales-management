# SSM — Simple Sales Management

[![CI](https://github.com/LeviPantaleao/simple-sales-management/actions/workflows/ci.yml/badge.svg)](https://github.com/LeviPantaleao/simple-sales-management/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A small offline Windows desktop app for a single small business to record
clients, register sales with an optional discount, and print a receipt as a
58 mm thermal-roll PDF.

Before SSM, client and sale records were kept on paper — one slip per client,
filed by hand. Finding a specific client or a specific sale meant going
through that stack page by page. SSM replaces the paper trail: every client
and sale is searchable in seconds, and a receipt prints straight from the
matching record.

### Screenshots

**Sales list.** Every sale, searchable and filterable by month, year, code or
product, with per-row view / edit / delete / print receipt actions.
> <img width="1920" height="1032" alt="image" src="https://github.com/user-attachments/assets/da5bdd04-4f24-4d93-b25d-2b015009ed5b" />

**New sale.** Product, the cash-register-style amount entry (digits push in
from the cents place), and the optional discount with a final amount that
can't exceed the original.
> <img width="1920" height="1032" alt="image" src="https://github.com/user-attachments/assets/1785ef09-8f8a-404d-aa42-fbbbcdee267d" />

**Receipt.** The 58 mm thermal-roll PDF generated for a sale: business
header, client block, and — when a discount applies — the original price
struck through next to the discounted one.
> <img width="1920" height="1032" alt="image" src="https://github.com/user-attachments/assets/4150fd17-4707-412d-99d1-2a56fed40530" />

**Client list.** Every client, searchable and filterable, kept in sync with
the sales history — a client seen in a sale always appears here.
> <img width="1920" height="1032" alt="image" src="https://github.com/user-attachments/assets/7bed39ca-ee73-444d-a831-d3168e81192c" />

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

For a fuller reference see [`ARCHITECTURE.md`](ARCHITECTURE.md); for the
reasoning behind each choice below (problem, alternatives, known limits) see
[`DECISIONS.md`](DECISIONS.md).

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
  language sets the installed program name (shortcuts, Start Menu, Add/Remove
  Programs) and seeds the app's initial language. It also adds Windows Firewall
  rules that block inbound connections to the SSM executables as defence in
  depth. The `SSM.exe` icon and version-string metadata are written by
  `build_ssm.ps1` with `rcedit` at build time (fixed English name), before the
  optional code-signing step — see [`DECISIONS.md`](DECISIONS.md) D8/D9.

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

## Known limitations and technical debt

- **`server.py` is a single file of roughly 3,000 lines** — every route,
  every data-access helper, PDF generation, i18n plumbing, and the
  signed-bundle logic in one module. It is not split into packages.
- **`viewer.py` generates the Electron shell's `main.js`/`preload.js` from a
  single function over 1,000 lines**, emitting JavaScript as an f-string
  template. That generated code has no syntax checking, no linting, and no
  test coverage of its own — see decision D5 in
  [`DECISIONS.md`](DECISIONS.md).
- **JavaScript is embedded directly in the HTML templates** (`<script>`
  blocks inside `management.html` and the other `viewables/*.html` files)
  rather than living in separate `.js` files, except for the two files that
  were extracted for testability (`app-common.js`, `money-mask.js`).
- **Test coverage is narrow.** `tests/test_server.py` and
  `tests/money_mask.test.js` cover money parsing, the signed-bundle
  verifier, atomic writes, and the money-input mask — not the route handlers,
  the PDF rendering, the i18n table, or the Electron shell.
- **Single user, single machine, Windows only.** There are no user accounts,
  no concurrent-writer support beyond the single-instance guarantee at the
  Electron layer, and no macOS/Linux build — the packaging pipeline
  (PyInstaller spec, Inno Setup script, DPAPI-protected export key) assumes
  Windows throughout.
- **No code signing.** The installer and shipped executables are unsigned, so
  Windows SmartScreen warns on first run and Smart App Control blocks them
  outright. The build accepts a certificate when one is available, but none is
  in use — see [`DECISIONS.md`](DECISIONS.md) D9.

---

## Install (end users)

Download `SSM-Setup.exe` from the project's
[GitHub Releases](https://github.com/LeviPantaleao/simple-sales-management/releases)
and run it. The installer is not stored in the Git repository — it is a
release asset (roughly 190 MB, because it contains the Electron runtime).

The setup wizard asks for a language (English / Portuguese (BR) / Spanish),
which determines the installed program name and the language the app starts in.
Administrator rights are required (the installer writes Windows Firewall
rules).

**The installer is not code-signed.** On a typical machine Windows SmartScreen
shows a "Windows protected your PC" prompt — click *More info* then *Run
anyway*. On a machine with **Smart App Control** enabled, the installer is
blocked entirely (*"Error 4551: An Application Control policy has blocked this
file"*); there is no per-file override — the machine owner has to turn Smart
App Control off, or run the app from source (see *Build from source*). Signing
is wired into the build for when a certificate is available — see
[`DECISIONS.md`](DECISIONS.md) D9.

---

## Build from source

### Prerequisites

- Windows x64
- Python 3.13 (developed with 3.13.7)
- Node.js ≥ 22.12 (developed with 24.x)
- `rcedit-x64.exe` — not in the repo; `build_ssm.ps1` fails without it. Put it
  at `tools\rcedit-x64.exe` (from
  <https://github.com/electron/rcedit/releases>, or `npm i -g rcedit` and copy
  from `%APPDATA%\npm\node_modules\rcedit\bin\`).
- Inno Setup 6+ — only for the installer step
- A code-signing certificate — optional; without it the output is unsigned
  (SmartScreen warning / Smart App Control block). See D9 in
  [`DECISIONS.md`](DECISIONS.md).

### Steps

```powershell
# 1. Python environment
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 2. Electron (downloads the 43.1.0 runtime into node_modules)
npm install

# 3. Package the backend + Electron into dist\ssm\
#    (needs tools\rcedit-x64.exe — see Prerequisites)
#    add -SignPfx / -SignSubject to sign the executables
powershell -ExecutionPolicy Bypass -File .\build_ssm.ps1

# 4. Build the installer -> installer_output\SSM-Setup.exe
#    Open ssm_setup.iss in the Inno Setup Compiler, or:
iscc ssm_setup.iss
#    to sign the installer too:  iscc "/Sssmsign=..." /DSIGN_ENABLED ssm_setup.iss
```

To run without packaging, install the Python and npm dependencies (steps 1–2)
and start the app directly:

```powershell
.\.venv\Scripts\python.exe viewer.py
```

This launches Flask and opens the Electron window using the Electron binary in
`node_modules`.

### Tests

```powershell
npm test        # Node's test runner: viewables/money-mask.js (the money input mask)

.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest    # server.py: money parsing, signed-bundle
                                         # verification, atomic writes, export-key
                                         # protection, receipt filenames
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

**The application is mine.** I built SSM to help my mother run her small
business — centralizing client and sales data that used to be recorded by
hand on paper. The concept — Electron as a "browser" shell around a
lightweight pseudo-app, `viewer.py` packaged into a single `.exe`, and an
installer that seeds the machine's detected language so the app starts in
the right one — was mine, and so was the original implementation (the
`Commit inicial` in this repository's history predates any AI assistance).
The spreadsheet export and the PDF receipt printing were features I designed
and had built. SSM replaces an earlier version of the same idea I had also
built myself: an automated Excel workbook that did the same job by filtering
between worksheets — Clients and Sales — and produced the receipt by writing
the sale into a third, Print Receipt, worksheet. SSM has been in daily use
at the small business it was built for.

**What came later, with AI assistance.** The MIT license and third-party
license inventory, the version/build metadata cleanup, and the audit pass
that produced most of the fixes in `CHANGELOG.md` and this documentation
(`ARCHITECTURE.md`, `DECISIONS.md`, this section) were done with Claude and
Claude Code, under my direction. Commits made with that assistance carry a
`Co-Authored-By: Claude` trailer, kept deliberately as a transparent record
of how the work was done. The requirements, the architecture decisions
recorded in `DECISIONS.md`, and the review of everything shipped are mine.
