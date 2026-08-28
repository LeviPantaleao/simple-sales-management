# Third-Party Licenses

SSM (Simple Sales Management) is distributed under the MIT License (see
[`LICENSE`](LICENSE)). It bundles and builds on third-party software that
remains under its own license. This document inventories those components.

The lists below are derived from [`requirements.txt`](requirements.txt),
the installed Python environment, [`package.json`](package.json), and
[`package-lock.json`](package-lock.json) at the time of writing. Version
numbers reflect the versions resolved in the development environment used to
produce the current build; `requirements.txt` itself pins no versions, so a
fresh `pip install` may resolve newer releases.

Full, verbatim license texts for the copyleft dependencies are kept in the
[`LICENSES/`](LICENSES/) directory. Permissive licenses (MIT, BSD, ISC,
Apache-2.0, PSF, HPND) are identified per component below; their short texts are
carried in each upstream project's own distribution.

This file is informational and records how the project handles the licenses of
its dependencies. It is not legal advice.

---

## Python dependencies

### Declared in `requirements.txt`

| Package      | Version (resolved) | License                                            | Shipped in the installed app? |
|--------------|--------------------|----------------------------------------------------|-------------------------------|
| Flask        | 3.1.3              | BSD-3-Clause                                        | Yes (frozen into the executable) |
| Flask-Babel  | 4.0.0              | BSD-3-Clause                                        | Yes |
| Babel        | 2.18.0             | BSD-3-Clause                                        | Yes |
| Pillow       | 12.3.0             | MIT-CMU (HPND / historical PIL license)             | Yes |
| **fpdf2**    | **2.8.7**          | **LGPL-3.0-only**                                   | **Yes — see the dedicated section below** |
| PyInstaller  | 6.21.0             | GPL-2.0-or-later *with* the PyInstaller bootloader exception | Build tool; only its bootloader stub is embedded in the executable (the exception explicitly permits this) |

### Transitive dependencies frozen into the executable

Pulled in automatically by the packages above and bundled by PyInstaller into
the distributed executable:

| Package      | Version (resolved) | License        | Brought in by |
|--------------|--------------------|----------------|---------------|
| Werkzeug     | 3.1.8              | BSD-3-Clause   | Flask |
| Jinja2       | 3.1.6              | BSD-3-Clause   | Flask |
| MarkupSafe   | 3.0.3              | BSD-3-Clause   | Flask / Jinja2 |
| ItsDangerous | 2.2.0              | BSD-3-Clause   | Flask |
| Click        | 8.4.2              | BSD-3-Clause   | Flask |
| Blinker      | 1.9.0              | MIT            | Flask |
| defusedxml   | 0.7.1              | PSF-2.0        | fpdf2 |
| fonttools    | 4.63.0             | MIT            | fpdf2 |

### Build-time-only Python packages (not shipped)

Used by PyInstaller while packaging; not present in the installed application:

| Package                    | Version (resolved) | License |
|----------------------------|--------------------|---------|
| pyinstaller-hooks-contrib  | 2026.6             | Apache-2.0 OR GPL-2.0-or-later |
| altgraph                   | 0.17.5             | MIT |
| pefile                     | 2024.8.26          | MIT |
| pywin32-ctypes             | 0.2.3              | BSD-3-Clause |
| setuptools                 | 83.0.0             | MIT |

---

## Node / Electron dependencies

### Declared in `package.json`

| Package  | Version | License | Notes |
|----------|---------|---------|-------|
| electron | 43.1.0  | MIT     | The `electron` npm package is MIT. The Electron runtime binary it downloads bundles Chromium, V8 and Node.js, each under its own license; Electron ships the combined license text as `LICENSE`, `LICENSES.chromium.html` and related files inside its `dist` directory. In an installed SSM, those files live under `_internal\node_modules\electron\dist\`. |

### Transitive dev dependencies (from `package-lock.json`)

These are used only at build time (to download and unpack the Electron binary).
Their source is **not** redistributed; the Electron *binary* they fetch is
bundled by PyInstaller as application data.

| Package                              | Version   | License      |
|--------------------------------------|-----------|--------------|
| @electron/get                        | 5.0.0     | MIT          |
| @electron-internal/extract-zip       | 1.0.4     | BSD-2-Clause |
| @types/node                          | 24.13.3   | MIT          |
| debug                                | 4.4.3     | MIT          |
| env-paths                            | 3.0.0     | MIT          |
| graceful-fs                          | 4.2.11    | ISC          |
| ms                                   | 2.1.3     | MIT          |
| progress                             | 2.0.3     | MIT          |
| semver                               | 7.8.5     | ISC          |
| sumchecker                           | 3.0.1     | Apache-2.0   |
| undici                               | 7.28.0    | MIT          |
| undici-types                         | 7.18.2    | MIT          |

---

## fpdf2 and the LGPL-3.0-only obligation

**Component:** [fpdf2](https://github.com/py-pdf/fpdf2) 2.8.7
**License:** GNU Lesser General Public License v3.0 only (LGPL-3.0-only)
**Role in SSM:** fpdf2 renders the thermal-roll (58 mm) PDF receipts
(`server.py`, `_render_one_receipt_pdf`). It is imported and used unmodified;
SSM contains no patched copy of fpdf2.

### Why this needs attention

The distributed SSM is packaged with PyInstaller (a `--onedir` build: `ssm.exe`
plus its `_internal` folder), which freezes fpdf2 and the rest of the Python
dependency tree into that bundle — fpdf2's Python code is embedded in the
frozen archive inside `ssm.exe` — and the whole bundle is placed inside the
Windows installer. Embedding the library into the frozen executable this way is
the situation the LGPL treats as **static linking**, which brings the
obligations of section 4 of the LGPL-3.0 into play — in particular, the
recipient must be able to relink or rebuild the combined work against a
modified or different version of the LGPL library.

### How this project complies

1. **Notice.** This document states that fpdf2 is present in the distributed
   executable and that it is licensed under LGPL-3.0-only. The full text of the
   license, and of the GNU GPL-3.0 that it builds on, is included verbatim in
   this repository at [`LICENSES/LGPL-3.0-only.txt`](LICENSES/LGPL-3.0-only.txt)
   and [`LICENSES/GPL-3.0-only.txt`](LICENSES/GPL-3.0-only.txt).

2. **Upstream source.** The complete, corresponding source code of fpdf2 is
   publicly available from the upstream project:
   - Repository: <https://github.com/py-pdf/fpdf2>
   - License text: <https://github.com/py-pdf/fpdf2/blob/master/LICENSE>
   - Release used here: <https://pypi.org/project/fpdf2/2.8.7/>

3. **Build files and rebuild instructions.** The files needed to reproduce the
   combined executable are part of this repository:
   - [`requirements.txt`](requirements.txt) — the Python dependency set
   - [`ssm.spec`](ssm.spec) — the PyInstaller specification
   - [`build_ssm.ps1`](build_ssm.ps1) — the end-to-end packaging script
   - [`ssm_setup.iss`](ssm_setup.iss) — the Inno Setup installer script

   The build steps are documented in [`README.md`](README.md#build-from-source).
   A user who wants to run SSM against a different or modified fpdf2 can install
   that version into the virtual environment (for example
   `pip install fpdf2==<other-version>`, or `pip install -e <local checkout>`)
   and re-run `build_ssm.ps1` to produce a new executable linked against their
   chosen version, then repackage with `ssm_setup.iss`. Running the app
   directly from source (`python viewer.py`) also uses whatever fpdf2 is
   installed in the environment, with no freezing step at all.

4. **No modification.** SSM uses fpdf2 as an unmodified dependency. Any
   modifications a user makes would live in their own environment or fork.

---

## rcedit (not committed to this repository)

**Component:** [rcedit](https://github.com/electron/rcedit) (`rcedit-x64.exe`)
**License:** MIT (the Electron project)
**Status in the repo:** **not committed.** `tools/*.exe` is listed in
[`.gitignore`](.gitignore); the binary must be downloaded separately and placed
at `tools\rcedit-x64.exe` before building the installer (see
[`README.md`](README.md#build-from-source)).

**Role:** The Inno Setup installer embeds `rcedit-x64.exe` and runs it once,
during installation, to write the localized program name into the metadata
(`ProductName`, `FileDescription`) of the installed `SSM.exe`, so that Windows
surfaces (Task Manager, file properties, taskbar) show the program name in the
language chosen in the setup wizard. rcedit is not installed onto the target
machine and is not part of the running application.

Download: <https://github.com/electron/rcedit/releases>
License text: <https://github.com/electron/rcedit/blob/master/LICENSE>
