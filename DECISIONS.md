# Decisions

Why SSM is built the way it is. One entry per architectural decision, in the
author's own words from a Q&A pass over the codebase. Each entry states the
problem, what was considered and rejected, and the known limit of the choice:
when it stops being the right one. See [`ARCHITECTURE.md`](ARCHITECTURE.md)
for the resulting system, described without justification.

Provenance note: for two entries (D1, D2) the author's answer was "apply your
own recommendation," not a first-hand account of the reasoning. Those two say
so plainly instead of being written up as if they were the author's original
deliberation.

---

## D1 — No database; data in plain JSON files

**Decision:** Client, sales, business and settings data live in plain JSON
files (`clients.json`, `sales.json`, `business.json`, `settings.json`,
`filters.json`) in a per-user directory, loaded into memory and rewritten on
every change. No database engine, no ORM.

**Context:** The author's stated reason was simplicity. The recommendation
below fills in the rest — the author asked for it to be applied as-is rather
than walking through the alternative in more detail.

**Alternatives considered:** SQLite was not evaluated in depth before
choosing JSON files; simplicity was the deciding factor on its own. (SQLite
would have kept the "no server to run" property SSM was after, while adding
transactions and indexed lookup — it was the closest fit not taken.)

**Known limit:** Fine for one user, one machine, and a client/sales volume a
small business produces in a few years — the whole dataset is loaded into
memory on every request. It stops being the right choice if SSM ever needs
concurrent writers, multi-machine access, or a dataset large enough that
loading it whole becomes slow. At that point the natural next step is SQLite,
which needs no server either.

---

## D2 — Atomic writes (`.tmp` + `os.replace`), no cross-process lock

**Decision:** Every write to a JSON data file writes a sibling
`<name>.json.tmp` and then atomically replaces the target
(`Path.replace`/`os.replace` semantics), so a crash or power loss mid-write
cannot leave a truncated file. There is no file lock between processes; a
single running instance is assumed and enforced at the Electron layer
(`requestSingleInstanceLock`), not in `server.py`.

**Context:** The author's stated reason ("para não pesar") reads as a
performance concern, but the mechanism itself is not about performance — a
single `os.replace` is not meaningfully cheaper or more expensive than a
plain `write_text`. What it actually buys is protection against a corrupted
or half-written file if the app is killed mid-save. The entry below reflects
that correction, applied at the author's request ("apply your own
recommendation").

**Alternatives considered:** A file lock (e.g. `filelock`) to serialize
writes across processes was not adopted — SSM's concurrency model is "one
window, one user," so the single-instance guarantee at the Electron layer was
judged sufficient without adding a locking dependency.

**Known limit:** The atomicity is per file, not across files. An operation
that touches more than one data file (e.g. deleting a client updates both
`sales.json` and `clients.json`; importing a bundle can touch
`clients.json`, `sales.json` and `business.json`) is not transactional — if
the process dies between two of those writes, the files can disagree until
the next reconciliation pass. The endpoints now report a failed write instead
of claiming success (see `CHANGELOG.md`), but they do not roll the whole
operation back. This also assumes a single local instance and a local
filesystem: two copies of the app pointed at the same data folder, or that
folder living on a network share / synced cloud folder (OneDrive, etc.), are
outside what this was designed for.

---

## D3 — Loopback bind, per-run token, firewall rule

**Decision:** The Flask backend binds `127.0.0.1` only, hardcoded and not
overridable by environment variable. A session token is generated fresh on
every launch and required on every request (`secrets.compare_digest`
comparison). The Inno Setup installer additionally adds Windows Firewall
rules blocking inbound connections to both SSM executables.

**Context (in the author's words):** the goal was that the backend must
**not vazar para a rede da loja de jeito nenhum**: it must never be reachable
from the store's network, period — not merely unreachable by default.

**Alternatives considered:** Not raised as alternatives by the author in
those terms; the loopback bind and the firewall rule were treated as two
layers of the same guarantee rather than separately weighed options. The
per-run token and the firewall rule were not separately deliberated as
distinct decisions — they were adopted together as defense in depth once the
"never reachable from the network" requirement was set.

**Known limit:** This protects against the network — it does not protect
against another process running as the *same Windows user* on the *same
machine*, which can read the session token (from the environment, the log
line printed when no token is supplied, or the cookie) and talk to the
backend like the app itself does. There is no HTTPS (loopback-only, so this
was judged unnecessary) and no protection against a second admin account on
a shared machine.

---

## D4 — Electron + PyInstaller, not pywebview or a plain browser tab

**Decision:** The UI runs inside an Electron window; the Python backend is
frozen with PyInstaller into `ssm.exe`.

**Context (in the author's words):** **pywebview é mais leve, mas menos
refinado e conflitava com alguns recursos visuais** — pywebview was
considered and rejected: lighter, but less polished, and it conflicted with
some of the visual features the UI needed. The resulting installer size
(roughly 150–250 MB, because Electron bundles Chromium) was accepted
consciously, in the author's words, **em prol de seu desempenho**: in
exchange for the rendering performance Electron gives the UI.

**Alternatives considered:** pywebview (rejected — see above). Opening the
UI in the user's default browser instead of a dedicated window was not
evaluated by the author as a distinct option.

**Known limit:** The installer is an order of magnitude larger than a
pywebview-based build would be, and every install carries a full Chromium
runtime for a single-window, single-user app. This is Windows-only — the
whole packaging pipeline (PyInstaller spec, Inno Setup script, the
`electron.exe` → `SSM.exe` rename, DPAPI for the export key) assumes Windows.

---

## D5 — `main.js` / `preload.js` / `package.json` generated at runtime

**Decision:** `viewer.py` generates the Electron shell's `main.js`,
`preload.js` and `package.json` as strings at launch time (one function,
roughly 940 lines) instead of shipping them as static files, and additionally
"bakes" a static copy into `resources/app` at build time for the
bare-launch fallback.

**Context:** Applied at the author's request ("aplique o recomendado na sua
concepção" — apply what you'd recommend). The generation lets the session
token, start URL, window title, theme and language be embedded as literal
values in the generated source for that one run, instead of being hardcoded
into a versioned file or passed in some other way at runtime.

**Alternatives considered:** Not raised by the author as a distinct choice;
documented here as the most direct explanation available from the code
itself.

**Known limit:** This is the technical debt named explicitly in the
project's own limitations list (see `README.md`): a single ~940-line function
that emits JavaScript as an f-string template has no syntax checking, no
linting, and no test coverage of the generated code — a typo inside the
template is a runtime failure in the Electron process, not a Python-side
error caught earlier.

---

## D6 — i18n: a hand-maintained table in `languages.py`, plus Flask-Babel

**Decision:** All UI message text (server-side templates and `_t(...)` calls
in `server.py`) is looked up in `languages.py`, a hand-maintained EN/PT/ES
dictionary; `server.py` overrides Flask's default Jinja `_` to point at it
instead of Flask-Babel's gettext. Flask-Babel is still used, but only for
locale negotiation and date/number formatting.

**Context / alternatives considered:** The standard approach for Flask is
Flask-Babel's own gettext machinery with compiled `.po`/`.mo` catalogs. A
migration to that was scoped in detail during this audit — extracting ~130
keys × 3 languages into `.po` files, adding a `babel.cfg` and a
`pybabel compile` build/CI step, removing the `_` override, and reworking
`viewer.py`'s use of `languages.py` outside any Flask request (it needs the
window title and native dialog strings before Flask even starts, which plain
gettext isn't set up for). The author decided **not** to do this now,
explicitly setting it aside as out of scope for this pass.

**Known limit:** No extraction tooling and no dedicated translation
workflow — every new string is added by hand to three dictionaries in one
Python file, with no automated check that a key exists in all three
languages. `gettext`/`.po` remains the standard alternative if the project
ever needs a real translation workflow (translator-friendly file format,
extraction tooling, plural forms).

---

## D7 — Export/import bundle signed with HMAC-SHA256

**Decision:** `/data/export` and `/data/import` exchange an `SSM_DATA_V1`
bundle: the data payload plus an HMAC-SHA256 signature over its canonical
JSON encoding. The key is generated on first use and persisted in
`CONFIG_DIR/export_key.json`, DPAPI-protected at rest on Windows.

**Context:** The stated purpose is integrity: make an exported file
"recognizable only by the program" (per the code's own comment), and reject
one that was hand-edited or came from a different export/import format. The
payload stays plain, readable JSON — the goal was never to keep its contents
confidential.

**Alternatives considered:** Encryption (e.g. password-based, Fernet/AES-GCM)
would additionally keep the contents confidential, but was not adopted —
confidentiality of the export file was never the goal, only tamper
detection. Applied during this audit: the key was originally stored in
plaintext; it is now DPAPI-protected at rest (same key value, so bundles
signed before the change still verify) — see `CHANGELOG.md`.

**Known limit:** This defends against a casual user editing the exported
file by hand and re-importing something malformed or inconsistent — not
against an attacker with access to the machine, who can still read the
protected key (DPAPI unprotects transparently for that same Windows user)
and forge a valid bundle. That was an explicit, accepted trade-off: the
threat model is "accidental/casual tampering," not "hostile local attacker."

---

## D8 — Localized Inno Setup installer, with `rcedit` renaming the shipped Electron binary

**Decision:** The Windows installer is built with Inno Setup 6
(`ssm_setup.iss`), localized into English / Portuguese (BR) / Spanish. The
bundled `electron.exe` is renamed to `SSM.exe`, and an embedded `rcedit` is
run once, at install time, to rewrite `SSM.exe`'s `ProductName` /
`FileDescription` metadata to the localized program name.

**Context (in the author's words):** **Electron Builder é bem menos
refinado do que Inno Setup** — `electron-builder` (the more common packaging
tool in the Electron ecosystem) was considered and rejected as less polished
than Inno Setup. The `rcedit` rewrite exists so the installed app
**fique com a cara de Simple Sales Management, como um programa por
completo** — presents itself as a complete, standalone program, not as
"Electron" in the taskbar, Task Manager, or file properties.

**Alternatives considered:** `electron-builder` (rejected — see above).
Leaving the shipped binary identified as "Electron" (skipping the `rcedit`
step) was implicitly rejected in favor of the polished, dedicated-program
identity described above.

**Known limit:** `rcedit-x64.exe` is not committed to the repository (build
instructions say to download it manually into `tools/rcedit-x64.exe`), so a
from-source build has a manual, undocumented-in-code prerequisite the CI does
not verify. The metadata rewrite happens once, at install time, tied to the
installer's own chosen language — the installed program's *file* metadata
does not change later if the in-app language is switched.

---

## D9 — The release binaries are not code-signed

**Decision:** The installer (`SSM-Setup.exe`) and the executables it ships
(`ssm.exe`, `SSM.exe`) go out without an Authenticode signature. The goal for
end users is "download the installer, install, use it" with no manual steps.

**Context (in the author's words):** the target user is non-technical and
should not have to work around security prompts. Code-signing is the accepted
way to get there, but it needs a paid certificate that also has to build
reputation, which has not been done for this project yet.

**Consequences today:**
- On a typical machine, running the unsigned installer shows a SmartScreen
  warning ("Windows protected your PC" → *More info* → *Run anyway*). It is
  clickable but looks alarming to a layperson.
- On a machine with **Smart App Control** enforced (default on some clean
  Windows 11 installs), the installer is **blocked outright** — Inno Setup
  reports *"Unable to execute file in the temporary directory. Setup aborted.
  Error 4551: An Application Control policy has blocked this file."* — and so
  is the portable `ssm.exe`. There is no user-side override; the machine's
  owner must disable Smart App Control (a one-way switch until a Windows
  reinstall) or run the app from source (`python viewer.py`).

**What is already wired for when a certificate exists:**
`build_ssm.ps1` takes `-SignSubject` / `-SignPfx` (+ `-SignPfxPassword`) and,
when given, signs `ssm.exe` and the renamed Electron binary with `signtool`;
`ssm_setup.iss` signs the installer and the uninstaller when compiled with
`/DSIGN_ENABLED` and a registered `ssmsign` tool. Without those parameters the
build is unchanged and unsigned.

**Alternatives considered:** shipping a portable ZIP instead of an installer
(rejected — Smart App Control blocks the unsigned `.exe` just the same);
turning the whole thing into a from-source run (rejected — defeats the
non-technical-user goal). A self-signed certificate was not pursued because
the user would still have to install and trust it manually.

**Known limit:** even with signing wired in, `ssm_setup.iss` runs `rcedit`
against `SSM.exe` **at install time** (D8), which invalidates any signature on
that specific file. Full Smart App Control compatibility for the *installed*
app would require moving the `rcedit` step into the build and signing
afterwards — not done. The recommended path forward is a reputable OV
certificate or a signing service such as Azure Trusted Signing.
