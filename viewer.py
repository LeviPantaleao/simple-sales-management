# viewer.py (Electron launcher)
from __future__ import annotations

import sys, os, atexit, tempfile, msvcrt, ctypes, logging, json, locale, secrets
import time, socket, threading, contextlib
import subprocess, shutil
from pathlib import Path

# --------------------------------------------------------------------
# App identity (mantém compatibilidade com seus paths antigos)
# --------------------------------------------------------------------
APP_SLUG = "SimpleSalesManagement"          # usado em lock / diretórios
APP_NAME = "Simple Sales Management"        # título base (msgid)
APP_USER_MODEL_ID = "LeviPantaleao.SSM.v2"  # trocado p/ descartar cache de icone/jumplist antigo no Windows

# --------------------------------------------------------------------
# --------------------------------------------------------------------
# Instância única: removida no nível Python. O Electron cuida disso
# (requestSingleInstanceLock + 'second-instance' abrindo uma nova janela
# na instância já em execução) -- ver main.js gerado. Um segundo
# ssm.exe pode iniciar normalmente; seu Electron filho vai detectar a
# instância já aberta, pedir pra ela abrir uma nova janela, e sair sozinho
# em seguida (sem popup, sem bloquear nada).
# --------------------------------------------------------------------

# --------------------------------------------------------------------
# CWD fix (evita “perder” caminhos relativos no executável)
# --------------------------------------------------------------------
def _fix_cwd():
    try:
        if getattr(sys, "frozen", False):
            base = os.path.dirname(sys.executable)
        else:
            base = os.path.dirname(os.path.abspath(__file__))
        if base:
            os.chdir(base)
    except Exception:
        pass

_fix_cwd()

try:
    sys.path.insert(0, str(Path(__file__).parent.resolve()))
except Exception:
    pass

# --------------------------------------------------------------------
# Locale helpers
# --------------------------------------------------------------------
# _canon() vem de languages.py -- fonte única compartilhada com server.py,
# pra detecção de locale do SO (aqui) e resolução de locale de request (lá)
# nunca divergirem.
try:
    from languages import canon_locale as _canon
except Exception:
    def _canon(tag: str | None) -> str:
        return "en"

# Detecção do idioma do SO: fonte única em languages.py (compartilhada com
# server.py). Fallback local só se o import falhar.
try:
    from languages import os_locale_tag as _detect_os_locale
except Exception:
    def _detect_os_locale() -> str:
        for getter in (lambda: locale.getlocale()[0], lambda: locale.getdefaultlocale()[0]):
            try:
                val = getter()
                if val:
                    return _canon(val)
            except Exception:
                pass
        return "en"

os.environ.setdefault("APP_IMPORT", "server:app")
# ✅ SEMPRE loopback: o backend nunca deve ser alcançável por outra máquina.
# (não é mais sobrescrevível por env — antes um APP_HOST=0.0.0.0 abria pra LAN.)
APP_HOST = "127.0.0.1"
APP_PORT = int(os.environ.get("APP_PORT", "0"))

# ✅ Token de sessão: sorteado por execução, exigido pelo server.py em toda
# requisição (ver _require_session_token lá). Impede que outro
# usuário/processo da mesma máquina use o app pela porta local.
SESSION_TOKEN = secrets.token_urlsafe(24)
os.environ["APP_SESSION_TOKEN"] = SESSION_TOKEN

def find_free_port(start: int = 5000, limit: int = 30) -> int:
    if APP_PORT:
        return APP_PORT
    for p in range(start, start + limit):
        with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
            # Sem SO_REUSEADDR: o bind falha de verdade se a porta já estiver
            # em uso (evita "roubar" a porta de outra instância/app).
            try:
                s.bind((APP_HOST, p))
                return p
            except OSError:
                continue
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind((APP_HOST, 0))
        return s.getsockname()[1]

def wait_http_up(host: str, port: int, timeout: float = 20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
            s.settimeout(0.25)
            try:
                s.connect((host, port))
                return True
            except OSError:
                time.sleep(0.15)
    return False

# --------------------------------------------------------------------
# Title (usa languages.py + fallback se a key não existir)
# --------------------------------------------------------------------
try:
    from languages import t as _lang_t
except Exception:
    def _lang_t(msgid: str, locale_tag: str | None = None) -> str:
        return msgid

def _title_fallback_for(tag: str | None) -> str:
    low = (tag or "en").replace("_", "-").lower()
    if low.startswith("pt"):
        return "Gerenciador Simples de Vendas"
    if low.startswith("es"):
        return "Gestión Simple de Ventas"
    return APP_NAME

def _localized_title(locale_tag: str | None) -> str:
    base = APP_NAME
    tag = (locale_tag or "en").replace("_", "-")
    out = _lang_t(base, tag)
    if not out or out == base:
        out = _title_fallback_for(tag)
    return out


def _dialog_strings_for(locale_str: str) -> dict:
    # Native OS dialogs (Save/Open/Select folder) should follow the app language.
    return {
        "saveFile": _lang_t("Save File", locale_str),
        "selectDirectory": _lang_t("Select Directory", locale_str),
        "openFile": _lang_t("Open File", locale_str),
        "images": _lang_t("Images", locale_str),
        "allFiles": _lang_t("All files", locale_str),
    }


def _title_map_from_languages() -> dict[str, str]:
    # Mapa para o Electron usar SEM depender de hardcode no main.js,
    # mas ainda com fallback caso languages.py não tenha a key.
    tags = ["en", "pt", "pt-BR", "es", "es-ES"]
    m: dict[str, str] = {}
    for t in tags:
        m[t] = _localized_title(t)
    # também aceita "_" por compat
    m["pt_BR"] = m.get("pt-BR", _title_fallback_for("pt-BR"))
    return m

# --------------------------------------------------------------------
# Settings helpers (mantém compatibilidade com server.py)
# --------------------------------------------------------------------
def _default_app_data_dir() -> Path:
    try:
        la = Path(os.getenv("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        d = la / APP_SLUG / "data"
        d.mkdir(parents=True, exist_ok=True)
        return d
    except Exception:
        p = Path(__file__).parent / "data"
        p.mkdir(parents=True, exist_ok=True)
        return p

def _data_dir() -> Path:
    return Path(os.environ.get("APP_DATA_DIR") or _default_app_data_dir())

def _settings_path() -> Path:
    return _data_dir() / "settings.json"

def _read_settings() -> dict:
    try:
        import importlib
        server_mod = importlib.import_module("server")
        s = server_mod._load_settings()
        return s if isinstance(s, dict) else {}
    except Exception:
        try:
            p = _settings_path()
            return json.loads(p.read_text("utf-8")) if p.exists() else {}
        except Exception:
            return {}

def _preferred_locale_from_settings() -> tuple[str | None, str]:
    s = _read_settings()
    pref = (s.get("preferred_locale") or "auto").strip()
    theme = (s.get("theme_mode") or "auto").strip().lower()
    if pref.lower() != "auto":
        return _canon(pref), theme
    return None, theme


# --------------------------------------------------------------------
# Installer language seed
#
# - Inno Setup writes: <install>\resources\ssm_install_lang.txt
# - When running only the win-unpacked folder, we also want to accept:
#     <win-unpacked-root>\ssm_install_lang.txt  (same folder as the main .exe)
#
# We only use this seed when preferred_locale is still "auto".
# If there is no seed, we force English (do NOT fallback to OS locale).
# --------------------------------------------------------------------
def _resources_root() -> Path:
    """Electron resources folder.

    In a packaged/win-unpacked app, viewer.py usually lives in:
      <root>\resources\app\viewer.py
    so resources root is the parent of 'app'.
    """
    try:
        p = Path(os.environ.get("SSM_APP_DIR") or Path(__file__).resolve().parent).resolve()
    except Exception:
        p = Path(__file__).resolve().parent
    return p.parent if p.name.lower() == "app" else p


def _exe_root() -> Path:
    """Best-effort folder where the main Electron .exe lives."""
    # 1) Explicit override
    try:
        ov = (os.environ.get("SSM_EXE_DIR") or "").strip()
        if ov:
            return Path(ov).expanduser().resolve()
    except Exception:
        pass

    # 2) If we are inside <root>\resources, exe is <root>
    try:
        r = _resources_root()
        if r.name.lower() == "resources":
            return r.parent
    except Exception:
        pass

    # 3) If running from the bundled python at <root>\resources\python\python.exe
    try:
        p = Path(sys.executable).resolve()
        if p.name.lower() == "python.exe" and p.parent.name.lower() == "python":
            # ...\resources\python\python.exe -> ...\resources
            return p.parent.parent
    except Exception:
        pass

    # Fallback: current working dir
    try:
        return Path.cwd().resolve()
    except Exception:
        return Path(".")


def _install_lang_seed_candidates() -> list[Path]:
    rr = _resources_root()
    er = _exe_root()

    cands = [
        rr / "ssm_install_lang.txt",              # <root>\resources\ssm_install_lang.txt
        er / "ssm_install_lang.txt",              # <root>\ssm_install_lang.txt
        er / "resources" / "ssm_install_lang.txt",# safety (some layouts)
    ]

    # remove duplicates while keeping order
    out: list[Path] = []
    seen: set[str] = set()
    for p in cands:
        try:
            key = str(p.resolve())
        except Exception:
            key = str(p)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _read_install_lang_seed() -> str | None:
    for p in _install_lang_seed_candidates():
        try:
            if not p.exists():
                continue
            raw = p.read_text(encoding="utf-8", errors="ignore").strip().splitlines()[0].strip()
            if not raw:
                continue
            return _canon(raw)
        except Exception:
            continue
    return None

# --------------------------------------------------------------------
# ICON: procura mais robusta + suporta override via env
# --------------------------------------------------------------------
def _find_app_icon() -> Path | None:
    try:
        ov = os.environ.get("APP_ICON", "").strip()
        if ov:
            p = Path(ov).expanduser()
            if not p.is_absolute():
                try:
                    p = (Path.cwd() / p).resolve()
                except Exception:
                    pass
            if p.exists() and p.is_file():
                return p
    except Exception:
        pass

    bases: list[Path] = []
    for cand in (
        lambda: Path(os.path.dirname(sys.executable)).resolve(),
        lambda: Path(__file__).parent.resolve(),
        lambda: Path(getattr(sys, "_MEIPASS", Path(__file__).parent)).resolve(),
        lambda: Path.cwd().resolve(),
        lambda: Path(__file__).parent.resolve().parent,
    ):
        try:
            bases.append(cand())
        except Exception:
            pass

    candidates: list[Path] = []
    for b in bases:
        candidates.extend([
            b / "app.ico",
            b / "assets" / "app.ico",
            b / "viewables" / "app.ico",
            b / "assets" / "app.png",
            b / "viewables" / "app.png",
        ])

    seen = set()
    for c in candidates:
        try:
            rc = c.resolve()
        except Exception:
            rc = c
        if str(rc) in seen:
            continue
        seen.add(str(rc))
        try:
            if c.exists() and c.is_file():
                return c
        except Exception:
            continue
    return None

# --------------------------------------------------------------------
# Electron runtime (gera app mínimo em temp e abre o Flask)
# --------------------------------------------------------------------
def _find_electron_executable() -> str | None:
    # 1) override
    try:
        ov = (os.environ.get("ELECTRON_PATH") or "").strip()
        if ov:
            p = Path(ov).expanduser()
            if p.exists():
                return str(p)
    except Exception:
        pass

    # 2) node_modules/.bin (dev)
    here = Path(__file__).parent.resolve()
    candidates = []
    if os.name == "nt":
        candidates += [
            here / "node_modules" / ".bin" / "electron.exe",
            here / "node_modules" / ".bin" / "electron.cmd",
        ]
    else:
        candidates += [here / "node_modules" / ".bin" / "electron"]
    for c in candidates:
        try:
            if c.exists():
                return str(c)
        except Exception:
            pass

    # 3) PATH
    exe = shutil.which("electron")
    if exe:
        return exe

    return None

def _explorer_dir_from_settings() -> str:
    """Reads Settings -> explorer_dir (fallback: ~/Documents)."""
    try:
        s = _read_settings()
        base = (s.get("explorer_dir") or s.get("pdf_dir") or str(Path.home() / "Documents")).strip()
        if not base:
            return str(Path.home() / "Documents")
        return str(Path(os.path.expanduser(base)).resolve())
    except Exception:
        return str(Path.home() / "Documents")


def _write_text(p: Path, s: str):
    p.write_text(s, encoding="utf-8", newline="\n")

def _escape_cmd_meta(s: str) -> str:
    # No cmd.exe, "&" separa comandos. Escapamos para evitar:
    # "'next' não é reconhecido..."
    return s.replace("&", "^&")

def _prefer_electron_exe(electron_path: str) -> str:
    """
    No Windows, 'electron' muitas vezes resolve para electron.cmd, e o cmd.exe interpreta '&' na URL.
    Tentamos trocar por electron.exe (evita parsing do cmd). Se não achar, mantém o original.
    Também preferimos um binário renomeado (SSM.exe) ao lado do electron.exe, para que a
    barra de tarefas do Windows mostre o nome do app em vez de "Electron".
    """
    if os.name != "nt":
        return electron_path

    def _renamed_variant(dist_dir: Path) -> Path | None:
        for name in ("SSM.exe", "ssm.exe"):
            cand = dist_dir / name
            try:
                if cand.exists():
                    return cand
            except Exception:
                pass
        return None

    try:
        p = Path(electron_path).expanduser()
        low = str(p).lower()

        if low.endswith(".exe"):
            # se já é electron.exe, ver se há um SSM.exe renomeado na mesma pasta
            rv = _renamed_variant(p.parent)
            return str(rv) if rv else str(p)

        if low.endswith((".cmd", ".bat")):
            same = p.with_suffix(".exe")
            if same.exists():
                rv = _renamed_variant(same.parent)
                return str(rv) if rv else str(same)

        for parent in [p.parent] + list(p.parents):
            try:
                dist = parent / "electron" / "dist"
                rv = _renamed_variant(dist)
                if rv:
                    return str(rv)
                cand = dist / "electron.exe"
                if cand.exists():
                    return str(cand)
            except Exception:
                pass

        return str(p)
    except Exception:
        return electron_path

def _make_temp_electron_app(
    start_url: str,
    title: str,
    icon_path: Path | None,
    theme_mode: str,
    lang_tag: str,
    title_map: dict[str, str],
    target_dir: Path | None = None,
) -> Path:
    if target_dir is not None:
        tmp = Path(target_dir)
        tmp.mkdir(parents=True, exist_ok=True)
    else:
        tmp = Path(tempfile.mkdtemp(prefix=f"{APP_SLUG}_electron_"))
        atexit.register(lambda: shutil.rmtree(tmp, ignore_errors=True))

    pkg = {"name": APP_SLUG.lower(), "version": "1.0.0", "private": True, "main": "main.js"}
    _write_text(tmp / "package.json", json.dumps(pkg, ensure_ascii=False, indent=2))

    # --- embed title map from Python (languages.py + override packs)
    title_map_json = json.dumps(title_map, ensure_ascii=False)

    dialog_str_json = json.dumps(_dialog_strings_for(lang_tag), ensure_ascii=False)

    main_js = rf"""
const {{ app, BrowserWindow, ipcMain, dialog, shell, nativeTheme, session }} = require('electron');
const fs = require('fs');
const path = require('path');
const http = require('http');
const https = require('https');

function argValue(prefix){{
  const a = process.argv.find(x => typeof x === 'string' && x.startsWith(prefix));
  return a ? a.slice(prefix.length) : '';
}}

const START_URL   = argValue('--url=')   || 'http://127.0.0.1:5000/';
const ICON_PATH   = argValue('--icon=')  || '';
// fallback: quando nao ha --icon (lancamento "cru"), usa um app.ico
// que pode estar salvo ao lado deste main.js (baked em resources\app).
function _resolveIconPath(){{
  if (ICON_PATH) return ICON_PATH;
  try{{
    const local = path.join(__dirname, 'app.ico');
    if (fs.existsSync(local)) return local;
  }}catch(e){{}}
  return '';
}}
const ICON_PATH_RESOLVED = _resolveIconPath();
const THEME_MODE  = (argValue('--theme=')|| 'auto').toLowerCase();
const APPID       = argValue('--appid=') || '';
const START_LANG  = (argValue('--lang=') || 'en').replace('_','-');
const START_TITLE = argValue('--title=') || '';

const EXPLORER_DIR = argValue('--explorer=') || '';

// Token de sessao (viewer.py). Fontes, em ordem: --token=, ?k= na START_URL,
// linha "SSM_TOKEN=" do backend no bare-launch. Injetado como header
// X-SSM-Token em TODA requisicao (janela, iframe, fetch do renderer via
// hook de sessao; downloads do processo main manualmente).
let SSM_TOKEN = argValue('--token=') || '';
try {{ if (!SSM_TOKEN) SSM_TOKEN = (new URL(START_URL)).searchParams.get('k') || ''; }} catch(e) {{}}

// ---------------------------------------------------------------------
// Bare-launch fallback: se o app foi aberto SEM --url (ex.: atalho/entrada
// da barra de tarefas apontando direto para este executável do Electron,
// sem passar pelo launcher Python), tentamos:
//   1) usar a URL padrão (porta 5000) se já houver um backend rodando;
//   2) senão, subir o backend nós mesmos (chamando o ssm.exe irmão em
//      modo "somente backend") e usar a URL que ele reportar.
// Quando --url É passado normalmente (fluxo padrão via ssm.exe), nada
// disto é usado: seguimos direto com a URL informada.
// ---------------------------------------------------------------------
const {{ spawn: _spawn }} = require('child_process');
const HAS_EXPLICIT_URL = !!argValue('--url=');

function _pingUrl(url, timeoutMs){{
  return new Promise((resolve) => {{
    try{{
      const mod = url.startsWith('https') ? https : http;
      const req = mod.get(url, (res) => {{ try{{ res.resume(); }}catch(e){{}} resolve(true); }});
      req.setTimeout(timeoutMs, () => {{ try{{ req.destroy(); }}catch(e){{}} resolve(false); }});
      req.on('error', () => resolve(false));
    }}catch(e){{ resolve(false); }}
  }});
}}

function _findSiblingLauncher(){{
  const guesses = [
    path.join(__dirname, '..', '..', '..', '..', '..', '..', 'ssm.exe'),
    path.join(__dirname, '..', '..', '..', '..', 'ssm.exe'),
  ];
  for (const g of guesses){{
    try{{ if (fs.existsSync(g)) return g; }}catch(e){{}}
  }}
  return null;
}}

function _bootBackendAndGetUrl(launcherPath, fallbackUrl){{
  return new Promise((resolve) => {{
    let done = false;
    const finish = (u) => {{ if(!done){{ done = true; resolve(u); }} }};
    try{{
      const child = _spawn(launcherPath, [], {{
        cwd: path.dirname(launcherPath),
        env: Object.assign({{}}, process.env, {{ SSM_NO_ELECTRON: '1' }}),
        stdio: ['ignore', 'pipe', 'ignore'],
        windowsHide: true,
      }});
      let buf = '';
      child.stdout.on('data', (chunk) => {{
        buf += chunk.toString('utf8');
        const mt = buf.match(/SSM_TOKEN=(\S+)/);
        if (mt) SSM_TOKEN = mt[1];
        const m = buf.match(/SSM_URL=(\S+)/);
        if (m) finish(m[1]);
      }});
      child.on('error', () => finish(fallbackUrl));
      setTimeout(() => finish(fallbackUrl), 15000);
    }}catch(e){{ finish(fallbackUrl); }}
  }});
}}

async function _resolveEffectiveUrl(){{
  if (HAS_EXPLICIT_URL) return START_URL;

  // Sem --url (provável abertura "crua" do executável do Electron):
  // tenta a porta padrao primeiro (outro backend pode ja estar de pe).
  const guess = 'http://127.0.0.1:5000/';
  const alive = await _pingUrl(guess, 800);
  if (alive) return guess;

  const launcher = _findSiblingLauncher();
  if (!launcher) return guess;

  return await _bootBackendAndGetUrl(launcher, guess);
}}

// Aplica o locale do Chromium o quanto antes (formato nativo de data segue o idioma)
try{{
  if (START_LANG) {{
    app.commandLine.appendSwitch('lang', START_LANG);
    try{{ app.commandLine.appendSwitch('accept-lang', START_LANG); }}catch(e){{}}
  }}
}}catch(e){{}}

function _expandPath(p){{
  try{{
    if(!p) return '';
    let s = String(p);

    // "~" -> home
    if (s.startsWith('~')){{
      try{{ s = path.join(app.getPath('home'), s.slice(1)); }}catch(e){{}}
    }}

    // %VAR% (Windows) and $VAR (POSIX)
    try{{
      s = s.replace(/%([^%]+)%/g, (m,k) => (process.env[k] || m));
    }}catch(e){{}}
    try{{
      s = s.replace(/\$([A-Za-z_][A-Za-z0-9_]*)/g, (m,k) => (process.env[k] || m));
    }}catch(e){{}}

    return s;
  }}catch(e){{}}
  return String(p||'');
}}

function _normDir(p){{
  try{{
    if(!p) return '';
    const s0 = _expandPath(p);
    // aceita caminhos com "/" mesmo no Windows
    const resolved = path.resolve(String(s0));
    if (fs.existsSync(resolved) && fs.statSync(resolved).isDirectory()) return resolved;
  }}catch(e){{}}
  try{{
    const s1 = String(_expandPath(p));
    if (s1 && fs.existsSync(s1) && fs.statSync(s1).isDirectory()) return s1;
  }}catch(e){{}}
  return '';
}}

let DEFAULT_SAVE_DIR = (() => {{
  const p = _normDir(EXPLORER_DIR);
  if (p) return p;
  try{{ return app.getPath('documents'); }}catch(e){{}}
  return '';
}})();


const TITLE_MAP = {title_map_json};
const STR = {dialog_str_json};

// ✅ Nome e AppUserModelID definidos ANTES do whenReady (afeta barra de tarefas)
try{{ app.setName('Simple Sales Management'); }}catch(e){{}}
try{{ app.name = 'Simple Sales Management'; }}catch(e){{}}
if (APPID){{
  try{{ app.setAppUserModelId(APPID); }}catch(e){{}}
}} else {{
  try{{ app.setAppUserModelId('LeviPantaleao.SSM.v2'); }}catch(e){{}}
}}

// ---------------------------------------------------------------------
// Instancia unica do Electron: se o app já está rodando (ex.: usuário
// clicou de novo no ícone/atalho fixado na barra de tarefas, ou no item
// de menu do botão direito), em vez de tentar abrir um processo do zero
// (que mostraria a tela padrão do Electron), pedimos pra INSTANCIA JÁ
// ABERTA criar uma NOVA JANELA — igual o Explorador de Arquivos faz.
// ---------------------------------------------------------------------
const extraWindows = [];

const _gotInstanceLock = app.requestSingleInstanceLock();
if (!_gotInstanceLock) {{
  // Já existe outra instância rodando: esta aqui não tem trabalho a fazer.
  app.quit();
}} else {{
  app.on('second-instance', () => {{
    try{{
      const activeUrl = (win && win.webContents && !win.webContents.isDestroyed())
        ? win.webContents.getURL()
        : (global.__SSM_RESOLVED_URL__ || START_URL);
      createAdditionalWindow(activeUrl);
    }}catch(e){{}}
  }});
}}

// ✅ Windows: limpa itens/jumplist (reduz coisas “estranhas” no clique direito)
if (process.platform === 'win32'){{
  try{{ app.setJumpList([]); }}catch(e){{}}
}}

function canon(tag){{
  if (!tag) return 'en';
  let t = String(tag).trim();
  t = t.split(':',1)[0].split('.',1)[0].replace('_','-');
  if (!t) return 'en';
  const low = t.toLowerCase();
  if (low === 'auto') return 'auto';
  const m = low.match(/^([a-z]{{2}})(?:-([a-z]{{2}}))?/i);
  if (!m) return 'en';
  const ll = m[1].toLowerCase();
  const rr = m[2] ? m[2].toUpperCase() : '';
  return rr ? `${{ll}}-${{rr}}` : ll;
}}

function titleFor(tag){{
  const t = canon(tag);
  if (t && TITLE_MAP[t]) return String(TITLE_MAP[t]);
  const base = (t || 'en').split('-',1)[0];
  if (base && TITLE_MAP[base]) return String(TITLE_MAP[base]);
  if (START_TITLE) return String(START_TITLE);
  return 'Simple Sales Management';
}}

function themeSource(mode){{
  const m = String(mode || 'auto').toLowerCase();
  if (m === 'dark') return 'dark';
  if (m === 'light') return 'light';
  return 'system';
}}

function downloadTo(url, destPath){{
  return new Promise((resolve, reject) => {{
    const u = String(url || '');
    const proto = u.startsWith('https:') ? https : http;
    const _hdrs = SSM_TOKEN ? {{ 'X-SSM-Token': SSM_TOKEN }} : {{}};
    const req = proto.get(u, {{ headers: _hdrs }}, (res) => {{
      const code = res.statusCode || 0;

      if (code >= 300 && code < 400 && res.headers && res.headers.location){{
        res.resume();
        return resolve(downloadTo(res.headers.location, destPath));
      }}

      if (code !== 200){{
        res.resume();
        return reject(new Error(`HTTP ${{code}}`));
      }}

      try{{ fs.mkdirSync(path.dirname(destPath), {{ recursive: true }}); }}catch(e){{}}
      const file = fs.createWriteStream(destPath);
      res.pipe(file);
      file.on('finish', () => file.close(() => resolve(true)));
      file.on('error', (err) => reject(err));
    }});
    req.on('error', reject);
  }});
}}

let win = null;

function _setExplorerDir(dir){{
  try{{
    const p = _normDir(dir);
    if (p) DEFAULT_SAVE_DIR = p;

    // atualiza a pasta default de download (se suportado)
    try{{
      if (win && win.webContents && win.webContents.session && typeof win.webContents.session.setDownloadPath === 'function' && DEFAULT_SAVE_DIR){{
        win.webContents.session.setDownloadPath(DEFAULT_SAVE_DIR);
      }}
    }}catch(e){{}}
    return DEFAULT_SAVE_DIR;
  }}catch(e){{}}
  return DEFAULT_SAVE_DIR;
}}

ipcMain.handle('ssm-set-explorer-dir', async (ev, dir) => {{
  try{{
    const out = _setExplorerDir(dir);
    return {{ ok:true, dir: out }};
  }}catch(e){{
    return {{ ok:false, dir: DEFAULT_SAVE_DIR, err: String(e && e.message ? e.message : e) }};
  }}
}});

let currentTitle = titleFor(START_LANG);

async function createWindow(startUrl, bounds, maximize){{
  try{{ nativeTheme.themeSource = themeSource(THEME_MODE); }}catch(e){{}}

  currentTitle = titleFor(START_LANG);

  win = new BrowserWindow({{
    width: (bounds && bounds.width) ? bounds.width : 1200,
    height: (bounds && bounds.height) ? bounds.height : 800,
    x: (bounds && typeof bounds.x === 'number') ? bounds.x : undefined,
    y: (bounds && typeof bounds.y === 'number') ? bounds.y : undefined,
    show: false,
    title: currentTitle,
    icon: ICON_PATH_RESOLVED ? ICON_PATH_RESOLVED : undefined,
    backgroundColor: '#201e17',
    autoHideMenuBar: true,
    webPreferences: {{
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false
    }}
  }});

  // ✅ impede que document.title das páginas altere o título nativo
  try{{
    win.on('page-title-updated', (event) => {{
      try{{ event.preventDefault(); }}catch(e){{}}
    }});
  }}catch(e){{}}

  // ✅ “cara de app final”: sem menu
  try{{ win.removeMenu(); }}catch(e){{}}
  try{{ win.setMenuBarVisibility(false); }}catch(e){{}}

  // ✅ bloqueia atalhos comuns de DevTools (F12 / Ctrl+Shift+I/J/C)
  try{{
    win.webContents.on('before-input-event', (event, input) => {{
      const key = (input.key || '').toLowerCase();
      const ctrl = !!input.control;
      const shift = !!input.shift;

      if (key === 'f12' || (ctrl && shift && (key === 'i' || key === 'j' || key === 'c'))){{
        event.preventDefault();
      }}
    }});
  }}catch(e){{}}

  win.once('ready-to-show', () => {{
    try{{ win.show(); }}catch(e){{}}
  }});

  // ✅ impede Ctrl+Click / _blank / window.open de criar nova janela:
  // - fora do app: abre no navegador
  // - dentro do app: navega na mesma janela
  try{{
    win.webContents.setWindowOpenHandler(({{
      url
    }}) => {{
      try{{
        const cur = new URL(win.webContents.getURL());
        const nxt = new URL(url);

        if (cur.origin !== nxt.origin){{
          shell.openExternal(url);
          return {{ action: 'deny' }};
        }}

        win.loadURL(url);
        return {{ action: 'deny' }};
      }}catch(e){{}}
      return {{ action: 'deny' }};
    }});
  }}catch(e){{}}

  // ✅ Downloads: usa Settings -> explorer_dir como pasta sugerida (em vez de "Downloads")
// e só abre o arquivo depois que o download termina (não abre se cancelar).
//
// Importante: aqui a gente *cancela* o download do Chromium e faz o download via Node (downloadTo),
// para evitar marcar o arquivo como "baixado da internet" no Windows (Excel/Office pode abrir bloqueado).
try{{
  const ses = win.webContents.session;

  // tenta setar a pasta default (se a API existir na versão do Electron)
  if (DEFAULT_SAVE_DIR){{
    try{{ if (ses && typeof ses.setDownloadPath === 'function') ses.setDownloadPath(DEFAULT_SAVE_DIR); }}catch(e){{}}
  }}

  ses.on('will-download', async (event, item) => {{
    try{{
      item.pause();

      const url = item.getURL ? item.getURL() : '';
      const filename = item.getFilename ? (item.getFilename() || 'download') : 'download';
      const defp = (DEFAULT_SAVE_DIR ? path.join(DEFAULT_SAVE_DIR, filename) : filename);

      const ext = (path.extname(filename) || '').replace('.','').toLowerCase();
      const filters = [];
      if (ext) filters.push({{ name: ext.toUpperCase(), extensions: [ext] }});
      filters.push({{ name: STR.allFiles, extensions: ['*'] }});

      const res = await dialog.showSaveDialog(win, {{
        title: STR.saveFile,
        defaultPath: defp,
        filters
      }});

      // sempre cancela o fluxo do Chromium (com ou sem cancelamento do usuário)
      try{{ item.cancel(); }}catch(e){{}}

      if (res.canceled || !res.filePath) return;
      if (!url) return;

      let out = String(res.filePath);
      const wantExt = (path.extname(filename) || '').toLowerCase();
      if (wantExt && !out.toLowerCase().endsWith(wantExt)) out += wantExt;

// Token downloads (/download/<token>) should not open a temporary preview.
// We ask the server to write bytes directly to the chosen path via /file/save_token.
    try{{
      const u = new URL(String(url));
      const m = u.pathname.match(/\/download\/([A-Za-z0-9\-_]+)/);
      if (m && m[1]){{
        const token = m[1];
        const saveUrl = u.origin + '/file/save_token';
        if (typeof fetch === 'function'){{
          const r = await fetch(saveUrl, {{
            method: 'POST',
            headers: {{'Content-Type':'application/json', 'X-SSM-Token': SSM_TOKEN}},
            body: JSON.stringify({{ token: token, path: out, open_after: false }})
          }});
          const j = await r.json().catch(()=>null);
          if (j && j.ok){{
            if (j.canceled) return {{ ok:false, canceled:true }};
            return {{ ok:true, path: out }};
          }}
        }}
      }}
    }}catch(_e){{}}

    await downloadTo(String(url), out);
    }}catch(e){{
      try{{ item.cancel(); }}catch(_){{}}
    }}
  }});
}}catch(e){{}}

await win.loadURL(startUrl || START_URL);

  // reforça título fixo após o primeiro load
  try{{ win.setTitle(currentTitle); }}catch(e){{}}
  try{{ if(maximize) win.maximize(); }}catch(e){{}}
}}

// ---------------------------------------------------------------------
// Abre uma NOVA janela do app já em execução (mesma URL/backend), sem
// substituir a janela principal (`win`). Usada quando o usuário tenta
// abrir o app de novo enquanto ele já está aberto (ex.: item do menu do
// botão direito na barra de tarefas) -- igual o Explorador de Arquivos.
// ---------------------------------------------------------------------
function createAdditionalWindow(url){{
  try{{
    const extraWin = new BrowserWindow({{
      width: 1200,
      height: 800,
      show: false,
      title: currentTitle,
      icon: ICON_PATH_RESOLVED ? ICON_PATH_RESOLVED : undefined,
      backgroundColor: '#201e17',
      autoHideMenuBar: true,
      webPreferences: {{
        preload: path.join(__dirname, 'preload.js'),
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: false
      }}
    }});

    try{{
      extraWin.on('page-title-updated', (event) => {{ try{{ event.preventDefault(); }}catch(e){{}} }});
    }}catch(e){{}}

    try{{
      extraWin.webContents.setWindowOpenHandler(({{ url: nurl }}) => {{
        try{{
          const cur = new URL(extraWin.webContents.getURL());
          const nxt = new URL(nurl);
          if (cur.origin !== nxt.origin){{ shell.openExternal(nurl); return {{ action: 'deny' }}; }}
          extraWin.loadURL(nurl);
        }}catch(e){{}}
        return {{ action: 'deny' }};
      }});
    }}catch(e){{}}

    extraWin.once('ready-to-show', () => {{ try{{ extraWin.show(); }}catch(e){{}} }});
    extraWin.on('closed', () => {{
      const i = extraWindows.indexOf(extraWin);
      if (i !== -1) extraWindows.splice(i, 1);
    }});

    extraWindows.push(extraWin);
    extraWin.loadURL(url || START_URL);
  }}catch(e){{}}
}}

app.whenReady().then(async () => {{
  // injeta o token em toda requisicao da sessao (janela + iframe + fetch do renderer)
  try {{
    session.defaultSession.webRequest.onBeforeSendHeaders((details, cb) => {{
      const h = details.requestHeaders || {{}};
      if (SSM_TOKEN) h['X-SSM-Token'] = SSM_TOKEN;
      cb({{ requestHeaders: h }});
    }});
  }} catch (e) {{}}

  const resolvedUrl = await _resolveEffectiveUrl();
  global.__SSM_RESOLVED_URL__ = resolvedUrl;
  await createWindow(resolvedUrl);
}});

app.on('window-all-closed', () => {{
  if (process.platform !== 'darwin') app.quit();
}});

// ✅ atualização de título baseada no lang REAL do HTML (mas só quando muda)
ipcMain.on('ssm-sync-title', (ev, lang) => {{
  try{{
    if (!win) return;
    const t = titleFor(lang);
    if (t && t !== currentTitle){{
      currentTitle = t;
      win.setTitle(t);
    }}
  }}catch(e){{}}
}});

ipcMain.handle('ssm-open-directory', async (ev, p) => {{
  try{{
    if (!p) return false;
    const s = String(p);
    if (fs.existsSync(s) && fs.statSync(s).isDirectory()){{
      await shell.openPath(s);
      return true;
    }}
    if (fs.existsSync(s) && fs.statSync(s).isFile()){{
      shell.showItemInFolder(s);
      return true;
    }}
  }}catch(e){{}}
  return false;
}});

ipcMain.handle('ssm-open-file', async (ev, p) => {{
  try{{
    if (!p) return false;
    const s = String(p);
    if (fs.existsSync(s)){{
      await shell.openPath(s);
      return true;
    }}
  }}catch(e){{}}
  return false;
}});

ipcMain.handle('ssm-choose-directory', async (ev, current) => {{
  try{{
    if (!win) return null;
    const res = await dialog.showOpenDialog(win, {{
      title: STR.selectDirectory,
      defaultPath: (current && String(current)) || undefined,
      properties: ['openDirectory','createDirectory']
    }});
    if (res.canceled || !res.filePaths || !res.filePaths[0]) return null;
    return res.filePaths[0];
  }}catch(e){{
    return null;
  }}
}});

ipcMain.handle('ssm-choose-save-path', async (ev, suggestedName) => {{
  try{{
    if (!win) return null;
    const name = (suggestedName && String(suggestedName)) || 'receipt.pdf';
    const ext  = (path.extname(name) || '').replace('.','').toLowerCase();

    const defPath = (DEFAULT_SAVE_DIR ? path.join(DEFAULT_SAVE_DIR, name) : name);

    const filters = [];
    if (ext) {{
      filters.push({{ name: ext.toUpperCase(), extensions: [ext] }});
    }}
    filters.push({{ name: STR.allFiles, extensions: ['*'] }});

    const res = await dialog.showSaveDialog(win, {{
      title: STR.saveFile,
      defaultPath: defPath,
      filters
    }});
    if (res.canceled || !res.filePath) return null;

    let out = String(res.filePath);
    const wantExt = (path.extname(name) || '').toLowerCase();
    if (wantExt && !out.toLowerCase().endsWith(wantExt)) out += wantExt;
    return out;
  }}catch(e){{
    return null;
  }}
}});


ipcMain.handle('ssm-download-to', async (ev, url, destPath) => {{
  try{{
    if (!url || !destPath) return {{ ok:false, err:'invalid_args' }};
    const out = String(destPath);
// Token downloads (/download/<token>) should not open a temporary preview.
// We ask the server to write bytes directly to the chosen path via /file/save_token.
    try{{
      const u = new URL(String(url));
      const m = u.pathname.match(/\/download\/([A-Za-z0-9\-_]+)/);
      if (m && m[1]){{
        const token = m[1];
        const saveUrl = u.origin + '/file/save_token';
        if (typeof fetch === 'function'){{
          const r = await fetch(saveUrl, {{
            method: 'POST',
            headers: {{'Content-Type':'application/json', 'X-SSM-Token': SSM_TOKEN}},
            body: JSON.stringify({{ token: token, path: out, open_after: false }})
          }});
          const j = await r.json().catch(()=>null);
          if (j && j.ok){{
            if (j.canceled) return {{ ok:false, canceled:true }};
            return {{ ok:true, path: out }};
          }}
        }}
      }}
    }}catch(_e){{}}

    await downloadTo(String(url), out);
let opened = false;
    return {{ ok:true, opened: !!opened }};
  }}catch(e){{
    return {{ ok:false, err: String(e && e.message ? e.message : e) }};
  }}
}});


// ✅ Save As (JSON) + download (para exportar data.json com confirmação real)
ipcMain.handle('ssm-save-as-from-url', async (ev, url, suggestedName) => {{
  try{{
    if (!win) return {{ ok:false, err:'no_window' }};
    if (!url) return {{ ok:false, err:'invalid_url' }};

    const name = (suggestedName && String(suggestedName)) || 'download.bin';
    const ext  = (path.extname(name) || '').replace('.','').toLowerCase();

    const defPath = (DEFAULT_SAVE_DIR ? path.join(DEFAULT_SAVE_DIR, name) : name);

    const filters = [];
    if (ext) {{
      filters.push({{ name: ext.toUpperCase(), extensions: [ext] }});
    }}
    filters.push({{ name: STR.allFiles, extensions: ['*'] }});

    const res = await dialog.showSaveDialog(win, {{
      title: STR.saveFile,
      defaultPath: defPath,
      filters
    }});

    if (res.canceled || !res.filePath){{
      return {{ ok:false, canceled:true }};
    }}

    let out = String(res.filePath);
    const wantExt = (path.extname(name) || '').toLowerCase();
    if (wantExt && !out.toLowerCase().endsWith(wantExt)) {{
      out += wantExt;
    }}

// Token downloads (/download/<token>) should not open a temporary preview.
// We ask the server to write bytes directly to the chosen path via /file/save_token.
    try{{
      const u = new URL(String(url));
      const m = u.pathname.match(/\/download\/([A-Za-z0-9\-_]+)/);
      if (m && m[1]){{
        const token = m[1];
        const saveUrl = u.origin + '/file/save_token';
        if (typeof fetch === 'function'){{
          const r = await fetch(saveUrl, {{
            method: 'POST',
            headers: {{'Content-Type':'application/json', 'X-SSM-Token': SSM_TOKEN}},
            body: JSON.stringify({{ token: token, path: out, open_after: false }})
          }});
          const j = await r.json().catch(()=>null);
          if (j && j.ok){{
            if (j.canceled) return {{ ok:false, canceled:true }};
            return {{ ok:true, path: out }};
          }}
        }}
      }}
    }}catch(_e){{}}

    await downloadTo(String(url), out);
return {{ ok:true, path: out }};
  }}catch(e){{
    return {{ ok:false, err: String(e && e.message ? e.message : e) }};
  }}
}});


function mimeFromPath(p){{
  const s = String(p||'').toLowerCase();
  if (s.endsWith('.svg')) return 'image/svg+xml';
  if (s.endsWith('.png')) return 'image/png';
  if (s.endsWith('.jpg') || s.endsWith('.jpeg')) return 'image/jpeg';
  if (s.endsWith('.gif')) return 'image/gif';
  if (s.endsWith('.bmp')) return 'image/bmp';
  if (s.endsWith('.webp')) return 'image/webp';
  if (s.endsWith('.ico')) return 'image/x-icon';
  if (s.endsWith('.tif') || s.endsWith('.tiff')) return 'image/tiff';
  return 'application/octet-stream';
}}

ipcMain.handle('ssm-select-image', async (ev) => {{
  try{{
    if (!win) return {{ ok:false, cancelled:true }};
    const res = await dialog.showOpenDialog(win, {{
      title: STR.openFile,
      properties: ['openFile'],
      filters: [
        {{ name: STR.images, extensions: ['png','jpg','jpeg','bmp','gif','webp','tif','tiff','svg','ico'] }},
        {{ name: STR.allFiles, extensions: ['*'] }},
      ]
    }});
    if (res.canceled || !res.filePaths || !res.filePaths[0]) return {{ ok:false, cancelled:true }};
    const p = res.filePaths[0];
    const raw = fs.readFileSync(p);
    const mime = mimeFromPath(p);
    const b64 = raw.toString('base64');
    return {{ ok:true, dataurl:`data:${{mime}};base64,${{b64}}`, filename:path.basename(p), path:p }};
  }}catch(e){{
    return {{ ok:false, error:String(e && e.message ? e.message : e) }};
  }}
}});

ipcMain.handle('ssm-read-file-dataurl', async (ev, p) => {{
  try{{
    if (!p) return {{ ok:false, error:'invalid_path' }};
    const fp = String(p);
    if (!fs.existsSync(fp) || !fs.statSync(fp).isFile()) return {{ ok:false, error:'invalid_path' }};
    const raw = fs.readFileSync(fp);
    const mime = mimeFromPath(fp);
    const b64 = raw.toString('base64');
    return {{ ok:true, dataurl:`data:${{mime}};base64,${{b64}}`, filename:path.basename(fp) }};
  }}catch(e){{
    return {{ ok:false, error:String(e && e.message ? e.message : e) }};
  }}
}});

ipcMain.handle('ssm-apply-theme', async (ev, mode, wantsDark) => {{
  try{{
    const m = String(mode||'auto').toLowerCase();
    if (m === 'dark') nativeTheme.themeSource = 'dark';
    else if (m === 'light') nativeTheme.themeSource = 'light';
    else nativeTheme.themeSource = 'system';
    return {{ ok:true }};
  }}catch(e){{
    return {{ ok:false }};
  }}
}});

ipcMain.handle('ssm-apply-language', async (ev, tag) => {{
  try{{
    if (!win) return {{ ok:false }};
    let t = canon(tag);
    if (t === 'auto'){{
      try{{ t = canon(app.getLocale()); }}catch(e){{ t = 'en'; }}
    }}

    // ✅ título conforme languages.py (via TITLE_MAP embedado)
    try{{
      const nextTitle = titleFor(t);
      if (nextTitle && nextTitle !== currentTitle){{
        currentTitle = nextTitle;
        win.setTitle(currentTitle);
      }}
    }}catch(e){{}}

    // ✅ Idioma agora funciona como o tema: o main process NÃO navega mais
    // a janela. Quem re-renderiza o conteúdo no novo idioma é o próprio
    // renderer (reload leve que mantém o popup de Configurações aberto e o
    // scroll da lista). Antes, o win.loadURL() daqui era o que "fechava o
    // popup e recarregava o programa" a cada troca de idioma. Aqui só
    // ajustamos o cookie `lang` (tem prioridade no servidor, ver
    // _select_locale) para o reload do renderer já sair traduzido.
    try{{
      const origin = new URL(win.webContents.getURL() || START_URL).origin;
      await session.defaultSession.cookies.set({{
        url: origin,
        name: 'lang',
        value: t,
        sameSite: 'lax',
        expirationDate: Math.floor(Date.now() / 1000) + 60 * 60 * 24 * 365
      }});
    }}catch(e){{}}

    return {{ ok:true, lang:t }};
  }}catch(e){{
    return {{ ok:false }};
  }}
}});
"""
    _write_text(tmp / "main.js", main_js.strip() + "\n")

    # preload.js: dispara pywebviewready + sincroniza título somente quando o lang REAL mudar (evita “recarregar” a cada página)
    preload_js = r"""
const { contextBridge, ipcRenderer } = require('electron');

function invoke(ch, ...args){
  return ipcRenderer.invoke(ch, ...args);
}

const api = {
  open_directory: (p) => invoke('ssm-open-directory', p),
  open_file: (p) => invoke('ssm-open-file', p),

  choose_directory: (current='') => invoke('ssm-choose-directory', current),
  choose_save_path: (suggestedName=null) => invoke('ssm-choose-save-path', suggestedName),

  download_pdf_to: (url, destPath) => invoke('ssm-download-to', url, destPath),

  // ✅ Export data.json: abre Save As e só retorna ok se salvou de verdade
  save_as_from_url: (url, suggestedName='data.json') => invoke('ssm-save-as-from-url', url, suggestedName),

  select_image: () => invoke('ssm-select-image'),
  read_file_to_dataurl: (p=null) => invoke('ssm-read-file-dataurl', p),

  // aliases usados no front
  choose_open_image: () => invoke('ssm-select-image'),
  choose_open_file: () => invoke('ssm-select-image'),
  open_file_dialog: () => invoke('ssm-select-image'),
  open_image: () => invoke('ssm-select-image'),

  apply_theme_mode: (mode='auto', wantsDark=null) => invoke('ssm-apply-theme', mode, wantsDark),
  apply_language: (tag) => invoke('ssm-apply-language', tag),
  set_explorer_dir: (dir) => invoke('ssm-set-explorer-dir', dir),
};

contextBridge.exposeInMainWorld('ssm', {
  api,
  chooseDirectory: (current='') => api.choose_directory(current),
  chooseSavePath: (name=null) => api.choose_save_path(name),
  saveAsFromUrl: (url, name='data.json') => api.save_as_from_url(url, name),
  selectImage: () => api.select_image(),
  readFileToDataURL: (p=null) => api.read_file_to_dataurl(p),
  openDirectory: (p) => api.open_directory(p),
  openFile: (p) => api.open_file(p),
  applyThemeMode: (mode='auto', wantsDark=null) => api.apply_theme_mode(mode, wantsDark),
  applyLanguage: (tag) => api.apply_language(tag),
  setExplorerDir: (dir) => api.set_explorer_dir(dir),
});

contextBridge.exposeInMainWorld('electronAPI', {
  api,
  chooseDirectory: (current='') => api.choose_directory(current),
  chooseSavePath: (name=null) => api.choose_save_path(name),
  saveAsFromUrl: (url, name='data.json') => api.save_as_from_url(url, name),
  selectImage: () => api.select_image(),
  readFileToDataURL: (p=null) => api.read_file_to_dataurl(p),
  openDirectory: (p) => api.open_directory(p),
  openFile: (p) => api.open_file(p),
  applyThemeMode: (mode='auto', wantsDark=null) => api.apply_theme_mode(mode, wantsDark),
  applyLanguage: (tag) => api.apply_language(tag),
  setExplorerDir: (dir) => api.set_explorer_dir(dir),
});

contextBridge.exposeInMainWorld('pywebview', {
  api: {
    open_directory: (p) => api.open_directory(p),
    open_file: (p) => api.open_file(p),
    choose_directory: (current='') => api.choose_directory(current),
    choose_save_path: (name=null) => api.choose_save_path(name),
    download_pdf_to: (url, destPath) => api.download_pdf_to(url, destPath),
    save_as_from_url: (url, name='data.json') => api.save_as_from_url(url, name),
    select_image: () => api.select_image(),
    read_file_to_dataurl: (p=null) => api.read_file_to_dataurl(p),
    apply_theme_mode: (mode='auto', wantsDark=null) => api.apply_theme_mode(mode, wantsDark),
    apply_language: (tag) => api.apply_language(tag),
  }
});

function __canonForStorage(lang){
  try{
    const t = String(lang || '').trim().toLowerCase().replace('_','-');
    if(!t) return '';
    return t;
  }catch(e){
    return '';
  }
}

try{
  window.addEventListener('DOMContentLoaded', () => {
    try{ document.dispatchEvent(new Event('pywebviewready')); }catch(e){}

    // ✅ sincroniza título só quando o lang do documento mudar (ex.: após trocar em settings)
    try{
      const lraw = (document.documentElement && document.documentElement.lang) ? document.documentElement.lang : '';
      const lkey = __canonForStorage(lraw);
      let prev = '';
      try{ prev = sessionStorage.getItem('ssm_last_lang') || ''; }catch(_){}
      if (lkey && lkey !== prev){
        try{ sessionStorage.setItem('ssm_last_lang', lkey); }catch(_){}
        ipcRenderer.send('ssm-sync-title', lraw);
      }
    }catch(e){}
  });
}catch(e){}
"""
    _write_text(tmp / "preload.js", preload_js.strip() + "\n")

    return tmp

# --------------------------------------------------------------------
# Main
# --------------------------------------------------------------------
def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    os.environ.setdefault("APP_DATA_DIR", str(_default_app_data_dir()))

    if os.name == "nt":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
        except Exception:
            pass

    effective_env_lang = _detect_os_locale()
    os.environ.setdefault("APP_OS_LANG", effective_env_lang)

    from server import app as flask_app
    port = find_free_port()

    threading.Thread(
        target=lambda: flask_app.run(host=APP_HOST, port=port, debug=False, use_reloader=False, threaded=True),
        daemon=True
    ).start()

    if not wait_http_up(APP_HOST, port, timeout=20):
        print("[FATAL] Flask server did not respond in time.", file=sys.stderr)
        return 2

    base = f"http://{APP_HOST}:{port}"
    preferred, theme_mode = _preferred_locale_from_settings()

    # Language decision order:
    #   1) If user explicitly chose a language (preferred_locale != auto), honor it.
    #   2) Else, if a seed file exists (installer or win-unpacked root), use it.
    #   3) Else, force English (do NOT fallback to OS locale).
    if preferred:
        effective = preferred
    else:
        seed = _read_install_lang_seed()
        effective = seed or "en"
    effective_tag = effective.replace("_", "-")

    # ?k=<token> só na 1ª navegação: o /i18n/set valida, o server seta o
    # cookie ssm_auth e redireciona pra "/" (o token some da barra de endereço).
    start_url = f"{base}/i18n/set?lang={effective_tag}&next=/&k={SESSION_TOKEN}"
    print(f"[INFO] Started at {base} (lang={effective}, theme={theme_mode})")



    # --- NEW: backend-only mode (Electron empacotado controla a janela) ---
    # Quando SSM_NO_ELECTRON=1, apenas inicia o Flask e imprime a URL para o Electron empacotado abrir.
    if os.environ.get("SSM_NO_ELECTRON") == "1":
        # O Electron (main.js) captura isso pelo stdout
        print(f"SSM_URL={start_url}", flush=True)
        print(f"SSM_TOKEN={SESSION_TOKEN}", flush=True)
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            return 0

    electron = _find_electron_executable()
    if not electron:
        print("[FATAL] Electron not found. Install Electron (npm i -D electron) or set ELECTRON_PATH.", file=sys.stderr)
        return 3

    electron = _prefer_electron_exe(electron)

    icon_path = _find_app_icon()

    # ✅ título e mapa de títulos vindos do languages.py (com fallback)
    title = _localized_title(effective_tag)
    title_map = _title_map_from_languages()

    app_dir = _make_temp_electron_app(
        start_url=start_url,
        title=title,
        icon_path=icon_path,
        theme_mode=theme_mode,
        lang_tag=effective_tag,
        title_map=title_map,
    )

    start_url_arg = start_url
    if os.name == "nt" and str(electron).lower().endswith((".cmd", ".bat")):
        start_url_arg = _escape_cmd_meta(start_url)

    explorer_dir = _explorer_dir_from_settings()

    args = [
        electron,
        str(app_dir),
        f"--url={start_url_arg}",
        f"--token={SESSION_TOKEN}",
        f"--title={title}",
        f"--theme={theme_mode}",
        f"--lang={effective_tag}",
        f"--explorer={explorer_dir}",
        f"--appid={APP_USER_MODEL_ID}",
    ]
    if icon_path and icon_path.exists():
        args.append(f"--icon={str(icon_path)}")

    try:
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        # Em build congelado (PyInstaller), Path(__file__).parent não é confiável;
        # usa a pasta do executável real.
        cwd_for_popen = str(Path(sys.executable).parent) if getattr(sys, "frozen", False) else str(Path(__file__).parent)
        proc = subprocess.Popen(args, cwd=cwd_for_popen, creationflags=creationflags)
        return proc.wait()
    except Exception as e:
        import traceback
        print(f"[FATAL] Failed to launch Electron: {e}", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)
        sys.stderr.flush()
        return 4

def _bake_resources_app(target_dir: Path) -> int:
    """
    Gera um app Electron ESTATICO (package.json + main.js + preload.js + app.ico)
    dentro de target_dir (tipicamente <electron>/resources/app). Usado apenas em
    tempo de BUILD (build_ssm.ps1), para que o Electron empacotado consiga abrir
    o app mesmo quando iniciado "cru" (sem passar pelo launcher ssm.exe), evitando
    a tela padrao do Electron.

    Não depende de nenhuma configuração dinâmica do usuário: usa o idioma padrão
    do sistema de traduções (title_map completo, pt/es/en) e strings de diálogo
    em inglês como base (a UI do app em si continua 100% localizada pelo Flask).
    """
    try:
        title_map = _title_map_from_languages()
    except Exception:
        title_map = {"en": APP_NAME}

    _make_temp_electron_app(
        start_url="http://127.0.0.1:5000/",
        title=APP_NAME,
        icon_path=None,
        theme_mode="dark",
        lang_tag="en",
        title_map=title_map,
        target_dir=target_dir,
    )

    # copia o ícone (se existir ao lado do viewer.py) para dentro do app estático,
    # para o fallback de ICON_PATH_RESOLVED funcionar em lançamentos "crus".
    try:
        src_icon = _find_app_icon()
        if src_icon and src_icon.exists():
            shutil.copyfile(str(src_icon), str(Path(target_dir) / "app.ico"))
    except Exception:
        pass

    print(f"[bake] resources/app gravado em {target_dir}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--bake-resources-app":
        sys.exit(_bake_resources_app(Path(sys.argv[2])))
    try:
        import multiprocessing as _mp
        _mp.freeze_support()
    except Exception:
        pass
    sys.exit(main())
