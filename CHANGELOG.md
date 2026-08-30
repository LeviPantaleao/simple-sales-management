# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `CHANGELOG.md` (this file).
- `.editorconfig`, aligned with the line-ending rules already declared in
  `.gitattributes` (LF for `.py`/`.js`/`.html`/`.json`/`.md`/`.txt`/`.spec`,
  CRLF for `.ps1`/`.iss`).
- `viewables/money-mask.js`: the pure "ATM-style" currency arithmetic
  (`clampCents`, `sanitizeDigits`, `pushDigits`, `popDigit`) extracted into
  one file that works both as a browser global (`window.MoneyMask`) and as a
  Node module (`module.exports`). Served at `/assets/money-mask.js` and loaded
  by `base.html` before `app-common.js`.
- `tests/test_server.py`: pytest coverage for `_parse_money_any`,
  `_verify_bundle` (valid / tampered payload / tampered MAC / wrong key id /
  malformed / non-dict), `_save_json` (atomic write, no leftover `.tmp`,
  failure leaves the original intact), `_slugify_short` and
  `_receipt_filename_for_sale`.
- `tests/conftest.py` and `pytest.ini`: hermetic setup that redirects
  `SSM_CONFIG_DIR` / `APP_DATA_DIR` to a temp directory before importing
  `server`, so the suite never reads or writes real business data.
- `requirements-dev.txt` pinning `pytest==9.1.1` and `ruff==0.16.5`.
- `ruff.toml`: a deliberately narrow lint config (`E9` + pyflakes `F`
  only; style rules left out for now).

### CI

- `.github/workflows/ci.yml` now also sets up Python 3.13, installs
  `requirements.txt` + `requirements-dev.txt`, byte-compiles every `.py`,
  and runs `pytest` — the workflow previously ran only the Node test, so
  the Python side was entirely unchecked.
- Actions are pinned to explicit released versions
  (`checkout@v4.2.2`, `setup-node@v4.2.0`, `setup-python@v5.4.0`), and pip
  and npm caching is enabled.
- A `ruff` step is added, non-blocking (`continue-on-error`) for now; it
  currently reports 6 findings (3 unused imports, 1 unused variable, and 2
  `FPDF` "undefined name" hits that are annotation-only and harmless under
  `from __future__ import annotations`).

### Changed

- `viewables/app-common.js` no longer carries its own copy of the money
  arithmetic; `enforceMoneyMask` / `renderMoneyMask` now delegate to
  `window.MoneyMask` from `money-mask.js`.
- `tests/money_mask.test.js` now imports the real functions from
  `viewables/money-mask.js` instead of re-implementing them, so a change to
  the algorithm actually breaks the test. The existing cases are kept and a
  few (`sanitizeDigits` edge cases, `clampCents` rounding/clamping) were
  added.
- `requirements.txt` now pins every direct dependency to an exact version
  (`flask==3.1.3`, `flask-babel==4.0.0`, `babel==2.18.0`, `pillow==12.3.0`,
  `fpdf2==2.8.7`, `pyinstaller==6.21.0`) so a build is reproducible.
- `package.json`: `version` aligned to `0.0.1` to match the installer and the
  Git release; the unused `"main": "index.js"` field was removed (the packaged
  Electron entry point is `main.js`, generated at build time by `viewer.py`
  into `resources/app`, and is never loaded from the repository root).
- `THIRD_PARTY_LICENSES.md`: the note that `requirements.txt` pinned no
  versions was updated to describe the new pinning.

### Fixed

- `server.py` was stored with a UTF-8 BOM and contained double-encoded
  (mojibake) text in comments, docstrings and error strings. The BOM was
  removed and the affected characters restored; no code paths changed.
- Comments and docstrings across `server.py`, `languages.py`,
  `tests/money_mask.test.js`, `viewables/app-common.js`, `viewables/base.html`
  and `viewables/management.html` referenced templates that no longer exist
  (`sale.html`, `sales.html`, `clients.html`, `searchbar.html`). They now
  point at the current files (`management.html`, `app-common.js`, `base.html`).

## [0.0.1] - 2026-08-28

First public release.

### Added

- Offline Windows desktop app for a single small business: a Flask backend
  (`server.py`) rendering server-side templates from `viewables/`, wrapped in
  an Electron window, packaged into a single executable with PyInstaller and
  an Inno Setup installer.
- Clients: six-digit code, name, address, contact and free-text description;
  the client list is kept in sync with the sales history.
- Sales: registered against a new or existing client, with product, amount and
  an optional discounted final amount (a final amount larger than the original
  is rejected); filter and search by code, product, description and month/year.
- Receipts: PDF in the 58 mm thermal-roll layout (58 × 210 mm) with optional
  business logo, business header, client block, sale block with struck-through
  original price, and a dated footer; currency and date formatting follow the
  active language.
- Import / export of client and sales data as JSON: a legacy `data.json` shape
  and an HMAC-signed bundle format (`SSM_DATA_V1`) are both accepted on import.
- Local JSON storage with atomic writes; no database, no user accounts, no
  cloud component.
- Security model: the backend binds to `127.0.0.1` only, guards its endpoints
  with a per-run session token compared using `hmac.compare_digest`, and runs
  the Electron renderer with `contextIsolation` on and `nodeIntegration` off.
- Trilingual UI (Portuguese, English, Spanish) via a project translation table
  in `languages.py` together with Flask-Babel.
- Licensing layer: MIT `LICENSE`, `THIRD_PARTY_LICENSES.md` inventory, and
  verbatim LGPL-3.0 / GPL texts under `LICENSES/` for fpdf2 compliance.

[Unreleased]: https://github.com/LeviPantaleao/simple-sales-management/compare/0.0.1...HEAD
[0.0.1]: https://github.com/LeviPantaleao/simple-sales-management/releases/tag/0.0.1
