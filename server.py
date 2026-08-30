# server.py — completo — adaptado para o management.html atual (r.amount / r.final_amount / r.sale_description)
from __future__ import annotations

import sys
import os
import re
import unicodedata
import json
import logging
import base64
import mimetypes
import random
import shutil
import contextlib
import time as _time
import hmac
import hashlib
import secrets
from typing import TYPE_CHECKING
from uuid import uuid4
from pathlib import Path
from datetime import datetime
from collections import defaultdict

if TYPE_CHECKING:
    from fpdf import FPDF

from flask import (
    Flask, abort, flash, jsonify, make_response, redirect,
    render_template, request, url_for, g, send_from_directory
)
from flask_babel import Babel, get_locale
from babel.dates import get_month_names, format_datetime
from babel.core import Locale

# === i18n fallbacks externos (languages.py) ===
try:
    from languages import (
        t as lang_t, is_rtl, canon_locale as _canon, os_locale_tag as _os_locale_tag_raw,
    )
except Exception:
    def lang_t(msgid: str, locale_str: str | None = None) -> str:
        return msgid
    def is_rtl(locale_str: str | None) -> bool:
        return False
    def _canon(tag: str | None) -> str:
        return "en"
    def _os_locale_tag_raw() -> str:
        return "en"

# Module logger. server.py is always imported (by viewer.py, or by the test
# suite), never run as __main__, so it configures nothing itself — the
# entry point (viewer.py) calls logging.basicConfig.
logger = logging.getLogger("ssm.server")

# --- PDF (fpdf2) --- (lazy import to speed startup)
def _get_FPDF():
    """Importa fpdf2 somente quando necessário (rotas de recibo)."""
    try:
        from fpdf import FPDF  # fpdf2
        return FPDF
    except Exception as e:
        raise RuntimeError("Dependência faltando: instale fpdf2 (pip install fpdf2).") from e


# --------------------------------------------------------------------------------------
# Paths / bootstrap
# --------------------------------------------------------------------------------------
BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))

def _resolve_templates_dir(base_dir: Path) -> Path:
    # Allow explicit override (useful for Electron portable)
    for k in ("SSM_TEMPLATES_DIR", "SSM_VIEWABLES_DIR"):
        v = os.environ.get(k)
        if v:
            p = Path(v).expanduser()
            if p.is_dir():
                return p

    required = (
        "base.html",
        "welcome.html",
        "management.html",
        "settings.html",
        "business.html",
        "app-common.js",
    )

    candidates: list[Path] = []

    # Electron can pass one of these (we try both common layouts)
    for k in ("SSM_APP_DIR", "SSM_RESOURCES_DIR"):
        v = os.environ.get(k)
        if v:
            root = Path(v).expanduser()
            candidates.extend([
                root / "viewables",
                root / "app" / "viewables",
            ])

    # Common layouts:
    # - next to server.py (base_dir/viewables)
    # - next to the app folder (base_dir.parent/viewables)  <-- electron-builder extraResources often land here
    # - current working directory (dev runs)
    candidates.extend([
        base_dir / "viewables",
        base_dir.parent / "viewables",
        Path.cwd() / "viewables",
    ])

    def has_required(p: Path) -> bool:
        try:
            return p.is_dir() and all((p / f).exists() for f in required)
        except Exception:
            return False

    for p in candidates:
        if has_required(p):
            return p

    # fallback: first existing directory
    for p in candidates:
        try:
            if p.is_dir():
                return p
        except Exception:
            pass

    return base_dir / "viewables"

TEMPLATES_DIR = _resolve_templates_dir(BASE_DIR)

with contextlib.suppress(Exception):
    logger.info("[SSM] Templates dir: %s", TEMPLATES_DIR)
CONFIG_DIR = Path(os.environ.get("SSM_CONFIG_DIR", str(Path.home() / ".SimpleSalesManagement")))
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
_BOOTSTRAP_FILE = CONFIG_DIR / "bootstrap.json"


def _posix(p: str | Path) -> str:
    try:
        return str(Path(str(p)).as_posix())
    except Exception:
        return str(p).replace("\\", "/")



# === AppData placeholder handling ===
# The UI may use a pseudo-path like "/Appdata/Local/..." to mean the user's LOCALAPPDATA.
# Never assume the OS is installed on C:. Expand using environment variables.

_APPDATA_LOCAL_RE = re.compile(r"^/*appdata/local(?:/|$)", re.IGNORECASE)

def _local_appdata_dir() -> Path:
    p = os.environ.get("LOCALAPPDATA")
    if p:
        return Path(p)
    return Path.home() / "AppData" / "Local"

def _default_appdata_ssm_data_dir() -> Path:
    return _local_appdata_dir() / "SimpleSalesManagement" / "data"

def _expand_appdata_placeholder(p) -> str:
    s = str(p or "").strip().replace("\\\\", "/").replace("\\", "/")
    if not s:
        return ""
    if _APPDATA_LOCAL_RE.match(s):
        base = str(_local_appdata_dir()).replace("\\\\", "/").replace("\\", "/").rstrip("/")
        rest = _APPDATA_LOCAL_RE.sub("", s).lstrip("/")
        return (base + "/" + rest) if rest else base
    return s

def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _is_writable_dir(path: Path) -> bool:
    try:
        _ensure_dir(path)
        probe = path / ".ssm_write_probe"
        probe.write_text("ok", "utf-8")
        with contextlib.suppress(Exception):
            probe.unlink()
        return True
    except Exception:
        return False


def _bootstrap_data_dir() -> Path | None:
    try:
        if _BOOTSTRAP_FILE.exists():
            raw = json.loads(_BOOTSTRAP_FILE.read_text("utf-8"))
            if isinstance(raw, dict) and raw.get("data_dir"):
                val = _expand_appdata_placeholder(raw["data_dir"])
                return Path(os.path.expanduser(str(val))).resolve()
    except Exception:
        pass
    return None


def _save_bootstrap_dir(data_dir: Path) -> None:
    try:
        payload = {"data_dir": _posix(data_dir)}
        _BOOTSTRAP_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    except Exception:
        pass


def _ensure_ssm_data_dir(base: Path) -> Path:
    """
    Garante a estrutura <base>/SimpleSalesManagement/data.
    Se 'base' já for .../SimpleSalesManagement/data, mantém.
    """
    s = str(base).replace("\\", "/").rstrip("/").lower()
    if s.endswith("/simplesalesmanagement/data"):
        return base
    if s.endswith("/data") and "/simplesalesmanagement" in s:
        return base
    return (base / "SimpleSalesManagement" / "data")


def _choose_initial_data_dir() -> Path:
    # Prioridade:
    # 1-4) QUALQUER candidato que JÁ tenha dados (env, bootstrap, Documents, AppData, legado)
    # 5) respeita env/bootstrap explícito mesmo vazio
    # 6) fallback AppData

    signals = ("settings.json", "sales.json", "business.json", "clients.json", "filters.json")

    def _has_data(p: Path | None) -> bool:
        try:
            return bool(p) and any((p / s).exists() for s in signals)
        except Exception:
            return False

    env_dir = None
    _env = os.environ.get("APP_DATA_DIR")
    if _env:
        try:
            env_dir = Path(os.path.expanduser(_env)).resolve()
        except Exception:
            env_dir = Path(_env)

    boot = _bootstrap_data_dir()
    appdata_default = _default_appdata_ssm_data_dir().resolve()
    docs_default = (Path.home() / "Documents" / "SimpleSalesManagement" / "data").resolve()
    legacy_home = (Path.home() / "SimpleSalesManagement" / "data").resolve()

    # 1) preferir qualquer candidato que já tenha dados
    for c in (env_dir, boot, docs_default, appdata_default, legacy_home, (BASE_DIR / "data").resolve()):
        if _has_data(c) and _is_writable_dir(c):
            return c

    # 2) sem dados em lugar nenhum: respeita env, depois bootstrap
    if env_dir and _is_writable_dir(env_dir):
        return env_dir
    if boot and _is_writable_dir(boot):
        return boot

    # 3) fallback AppData
    _ensure_dir(appdata_default)
    return appdata_default


DATA_DIR = _choose_initial_data_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)

SETTINGS_FILE = DATA_DIR / "settings.json"
BUSINESS_FILE = DATA_DIR / "business.json"
SALES_FILE = DATA_DIR / "sales.json"
FILTERS_FILE = DATA_DIR / "filters.json"
CLIENTS_FILE = DATA_DIR / "clients.json"

try:
    _save_bootstrap_dir(DATA_DIR)
except Exception:
    pass


def _set_data_dir(new_dir: Path) -> None:
    global DATA_DIR, SETTINGS_FILE, BUSINESS_FILE, SALES_FILE, FILTERS_FILE, CLIENTS_FILE
    DATA_DIR = Path(new_dir).resolve()
    _ensure_dir(DATA_DIR)
    os.environ["APP_DATA_DIR"] = _posix(DATA_DIR)
    SETTINGS_FILE = DATA_DIR / "settings.json"
    BUSINESS_FILE = DATA_DIR / "business.json"
    SALES_FILE = DATA_DIR / "sales.json"
    FILTERS_FILE = DATA_DIR / "filters.json"
    CLIENTS_FILE = DATA_DIR / "clients.json"
    _save_bootstrap_dir(DATA_DIR)


def _iter_known_jsons(folder: Path):
    names = [
        "business.json", "sale.json", "sales.json",
        "clients.json", "settings.json", "prefs.json",
        "filters.json"
    ]
    for n in names:
        p = folder / n
        if p.exists():
            yield p
    seen = set(names)
    for p in folder.glob("*.json"):
        if p.name not in seen:
            yield p


def _migrate_data_dir_to(base_choice: str | Path) -> Path:
    old_dir = DATA_DIR
    base_choice = _expand_appdata_placeholder(base_choice)
    base_in = Path(os.path.expanduser(str(base_choice))).resolve()
    target = _ensure_ssm_data_dir(base_in)
    # Never allow a read-only target (e.g., Program Files). Fall back to LOCALAPPDATA.
    if not _is_writable_dir(target):
        target = _default_appdata_ssm_data_dir().resolve()
    _ensure_dir(target)

    if target.resolve() != old_dir.resolve():
        for src in _iter_known_jsons(old_dir):
            dst = target / src.name
            try:
                if dst.exists():
                    with contextlib.suppress(Exception):
                        dst.replace(dst.with_suffix(dst.suffix + ".bak"))
                shutil.move(str(src), str(dst))
            except Exception:
                pass

    _set_data_dir(target)
    return target


# --------------------------------------------------------------------------------------
# Flask app
# --------------------------------------------------------------------------------------
app = Flask(__name__, template_folder=str(TEMPLATES_DIR))

# --------------------------------------------------------------------------------------
# Trava de sessao local (offline / acesso restrito)
# --------------------------------------------------------------------------------------
# O launcher (viewer.py) sorteia um segredo por execucao e o passa em
# APP_SESSION_TOKEN. Toda requisicao precisa apresenta-lo (cookie ja setado,
# header X-SSM-Token injetado pelo Electron, ou ?k=<token> na 1a navegacao).
# Assim, mesmo outro usuario/processo da MESMA maquina abrindo
# http://127.0.0.1:<porta> no navegador recebe 403.
#
# A trava esta SEMPRE ativa. Se APP_SESSION_TOKEN nao vier do ambiente
# (ex.: rodar server.py sozinho em dev), um token e sorteado aqui e
# registrado no log -- copie-o e use como ?k=<token> na primeira navegacao.
_SESSION_TOKEN = os.environ.get("APP_SESSION_TOKEN", "").strip()
_SESSION_TOKEN_FROM_ENV = bool(_SESSION_TOKEN)
if not _SESSION_TOKEN:
    _SESSION_TOKEN = secrets.token_urlsafe(24)
_AUTH_COOKIE = "ssm_auth"

# secret_key da sessao Flask: deriva do token (sempre definido) em vez de "dev".
app.secret_key = _SESSION_TOKEN

if not _SESSION_TOKEN_FROM_ENV:
    app.logger.warning(
        "APP_SESSION_TOKEN nao definido no ambiente; token de sessao sorteado "
        "para esta execucao: %s (use ?k=<token> na primeira navegacao)",
        _SESSION_TOKEN,
    )


@app.before_request
def _require_session_token():
    got = (request.cookies.get(_AUTH_COOKIE)
           or request.headers.get("X-SSM-Token")
           or request.args.get("k")
           or "")
    if secrets.compare_digest(str(got), _SESSION_TOKEN):
        g._ssm_need_cookie = (request.cookies.get(_AUTH_COOKIE) != _SESSION_TOKEN)
        return None
    return ("Forbidden", 403, {"Content-Type": "text/plain; charset=utf-8"})


@app.after_request
def _grant_session_cookie(resp):
    try:
        if getattr(g, "_ssm_need_cookie", False):
            resp.set_cookie(_AUTH_COOKIE, _SESSION_TOKEN,
                            httponly=True, samesite="Strict", path="/")
    except Exception:
        # If the auth cookie never gets set, every later request 403s until
        # the user re-navigates with ?k=<token>. Make that diagnosable.
        logger.exception("[auth] failed to set session cookie")
    return resp
# --------------------------------------------------------------------------------------
# TEMP BIN (download token) — usado para exportar XLSX/PDF sem gravar em disco
# --------------------------------------------------------------------------------------
_TEMP_BIN: dict[str, tuple[bytes, str, str, float]] = {}


def _bin_put(b: bytes, filename: str, mime: str, ttl: int = 600) -> str:
    token = str(uuid4())
    _TEMP_BIN[token] = (b, filename, mime, _time.time() + float(ttl))
    return token


def _bin_peek(token: str):
    v = _TEMP_BIN.get(token)
    if not v:
        return None
    if v[3] < _time.time():
        _TEMP_BIN.pop(token, None)
        return None
    return v


def _bin_gc():
    now = _time.time()
    for k, v in list(_TEMP_BIN.items()):
        if v[3] < now:
            _TEMP_BIN.pop(k, None)


@app.get("/assets/app-common.js")
def app_common_js():
    resp = make_response(send_from_directory(TEMPLATES_DIR, "app-common.js"))
    resp.headers["Content-Type"] = "application/javascript; charset=utf-8"
    return resp


@app.get("/assets/money-mask.js")
def money_mask_js():
    resp = make_response(send_from_directory(TEMPLATES_DIR, "money-mask.js"))
    resp.headers["Content-Type"] = "application/javascript; charset=utf-8"
    return resp


@app.get("/download/<token>")
def download_token(token: str):
    _bin_gc()
    v = _bin_peek(token)
    if not v:
        abort(404)
    b, fn, mime, _ = v
    resp = make_response(b)
    resp.headers["Content-Type"] = mime
    resp.headers["Content-Disposition"] = f'attachment; filename="{fn}"'
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp




# --------------------------------------------------------------------------------------
# i18n / locale selection
# --------------------------------------------------------------------------------------
# _canon() (canonicalização 'en' / 'en_US' / 'pt_BR', mantendo 'auto') vem de
# languages.py -- ver import no topo do arquivo. Usada também por viewer.py.


def _safe_locale(tag: str | None) -> str:
    """Return a Babel-parseable locale tag (fallback to 'en')."""
    c = _canon(tag)
    if c == "auto":
        return "en"

    try:
        Locale.parse(c)
        return c
    except Exception:
        pass

    try:
        base = c.split("_", 1)[0]
        Locale.parse(base)
        return base
    except Exception:
        return "en"


def _best_accept() -> str:
    h = request.headers.get("Accept-Language", "") or ""
    first = (h.split(",", 1)[0] or "").split(";", 1)[0].strip() or "en"
    return _safe_locale(first)


def _os_locale_tag() -> str:
    """Idioma do SO, garantidamente parseável pelo Babel. A detecção em si
    vem de languages.os_locale_tag() (mesma usada pelo viewer.py)."""
    try:
        return _safe_locale(_os_locale_tag_raw())
    except Exception:
        return "en"


_DEFAULT_SETTINGS = {
    "preferred_locale": "auto",
    "theme_mode": "auto",
    "data_dir": _posix(DATA_DIR),
    "explorer_dir": _posix(Path.home() / "Documents"),
    "pdf_dir": _posix(Path.home() / "Documents"),
    "setup_done": False,
}


def _load_settings() -> dict:
    try:
        if SETTINGS_FILE.exists():
            raw = json.loads(SETTINGS_FILE.read_text("utf-8"))
            if isinstance(raw, dict):
                out = dict(_DEFAULT_SETTINGS)
                out.update(raw)
                out["data_dir"] = _posix(out.get("data_dir") or _DEFAULT_SETTINGS["data_dir"])
                out["explorer_dir"] = _posix(out.get("explorer_dir") or _DEFAULT_SETTINGS["explorer_dir"])
                out["pdf_dir"] = _posix(out.get("pdf_dir") or out["explorer_dir"])
                out["setup_done"] = bool(out.get("setup_done"))
                # Expand AppData placeholder (e.g. "/Appdata/Local/..." -> "%LOCALAPPDATA%/...")
                try:
                    out['data_dir'] = _posix(_expand_appdata_placeholder(out.get('data_dir', '')))
                except Exception:
                    pass
                return out
    except Exception as e:
        logger.warning("[SETTINGS] load fail: %s", e)
    return dict(_DEFAULT_SETTINGS)


def _save_settings(obj: dict) -> bool:
    try:
        d = dict(obj)
        for k in ("data_dir", "explorer_dir", "pdf_dir"):
            if k in d and d[k]:
                d[k] = _posix(d[k])
        tmp = SETTINGS_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), "utf-8")
        tmp.replace(SETTINGS_FILE)
        return True
    except Exception as e:
        logger.error("[SETTINGS] write fail: %s", e)
        return False


babel = Babel()


def _select_locale():
    # Priority: query param -> cookie -> settings -> OS -> Accept-Language
    q = request.args.get("lang")
    if q:
        c0 = _canon(q)
        if c0 == "auto":
            g._forced_lang = "auto"
            return _os_locale_tag()
        c = _safe_locale(c0)
        g._forced_lang = c0
        return c

    c = request.cookies.get("lang")
    if c:
        c0 = _canon(c)
        if c0 != "auto":
            return _safe_locale(c0)
        return _os_locale_tag()

    try:
        s = _load_settings()
        pref = (s.get("preferred_locale") or "auto").strip()
        if pref.lower() != "auto":
            return _safe_locale(_canon(pref))
    except Exception:
        pass

    sys_tag = _os_locale_tag()
    if sys_tag and sys_tag != "en":
        return sys_tag
    return _best_accept()


babel.init_app(app, locale_selector=_select_locale, default_locale="en")


def _t(msgid: str) -> str:
    try:
        loc = str(get_locale() or 'en')
        Locale.parse(loc)
    except Exception:
        loc = _safe_locale(getattr(g, '_forced_lang', None) or _select_locale())
    loc = loc.replace('_','-')
    return lang_t(msgid, loc)


@app.after_request
def _content_language(resp):
    try:
        try:
            tag = str(get_locale() or 'en')
            Locale.parse(tag)
        except Exception:
            tag = _safe_locale(getattr(g, '_forced_lang', None) or _select_locale())
        tag = tag.replace('_','-')
        resp.headers["Content-Language"] = tag
        if getattr(g, "_forced_lang", None) is not None:
            resp.set_cookie("lang", g._forced_lang, max_age=60 * 60 * 24 * 365, samesite="Lax")
    except Exception:
        # Cosmetic: missing Content-Language header / the lang choice not
        # sticking as a cookie. Log at warning, do not fail the response.
        logger.warning("[i18n] could not set Content-Language / lang cookie", exc_info=True)
    return resp


@app.get("/i18n/set")
def i18n_set():
    lang = request.args.get("lang", "")
    ref = request.args.get("next") or request.referrer or url_for("home")
    resp = redirect(ref)
    if lang:
        resp.set_cookie("lang", _canon(lang), max_age=60 * 60 * 24 * 365, samesite="Lax")
    return resp


# --------------------------------------------------------------------------------------
# Template filters / inject
# --------------------------------------------------------------------------------------
@app.template_filter("dt")
def jinja_dt(d, style="short"):
    try:
        if isinstance(d, str):
            d = datetime.fromisoformat(d)
    except Exception:
        pass
    if not isinstance(d, datetime):
        return d
    loc = str(get_locale() or "en")
    try:
        fmt = "short" if style == "short" else ("medium" if style == "medium" else "long")
        return format_datetime(d, format=fmt, locale=loc)
    except Exception:
        return d.strftime("%d/%m/%Y %H:%M")


# --------------------------------------------------------------------------------------
# PDF helpers (fpdf2 core fonts are Latin-1; sanitize to avoid UnicodeEncodeError)
# --------------------------------------------------------------------------------------
_PDF_PUNCT_MAP = {
    "\u00A0": " ",   # NBSP
    "\u2013": "-",   # en dash
    "\u2014": "-",   # em dash
    "\u2212": "-",   # minus
    "\u2018": "'", "\u2019": "'", "\u201B": "'",  # single quotes
    "\u201C": '"', "\u201D": '"', "\u201F": '"',  # double quotes
    "\u2026": "...", # ellipsis
}

def _sanitize_text_pdf(txt) -> str:
    """Sanitize text for FPDF core fonts (keeps Latin-1; replaces unsupported chars)."""
    if txt is None:
        return ""
    s = str(txt)
    # normalize newlines
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    # replace common punctuation with safe ASCII
    for k, v in _PDF_PUNCT_MAP.items():
        s = s.replace(k.encode("utf-8").decode("unicode_escape"), v)
    # drop other control chars (except \n and \t -> space)
    s = "".join((" " if ch == "\t" else ch) for ch in s if (ch == "\n" or ord(ch) >= 32))
    # keep latin-1, replace anything else
    try:
        return s.encode("latin-1", "replace").decode("latin-1")
    except Exception:
        # last resort: strip accents to ASCII
        try:
            nk = unicodedata.normalize("NFKD", s)
            nk = "".join(ch for ch in nk if not unicodedata.combining(ch))
            return nk.encode("ascii", "ignore").decode("ascii")
        except Exception:
            return s

@app.context_processor
def inject():
    def current_lang_tag():
        try:
            loc = str(get_locale() or 'en')
            # validate
            Locale.parse(loc)
        except Exception:
            loc = _safe_locale(getattr(g, '_forced_lang', None) or _select_locale())
        return loc.replace('_','-')

    def text_dir():
        return "rtl" if is_rtl(current_lang_tag()) else "ltr"

    def current_theme_mode():
        return (_load_settings().get("theme_mode") or "auto").lower()

    return dict(
        _=_t,
        current_lang_tag=current_lang_tag,
        text_dir=text_dir,
        current_theme_mode=current_theme_mode,
        DEFAULT_DATA_DIR=_posix(_default_appdata_ssm_data_dir()),
        BUSINESS=_load_business(),
        # ✅ Settings agora também é implementado como popup embutido em
        # Vendas/Clientes/Nova Venda (management.html), então precisa estar
        # disponível globalmente, igual ao BUSINESS acima.
        SETTINGS=_load_settings(),
        THEME_CHOICES=[("light", "Light"), ("dark", "Dark"), ("auto", "Auto")],
    )


# --------------------------------------------------------------------------------------
# JSON helpers
# --------------------------------------------------------------------------------------
def _load_json(p: Path, default):
    try:
        return json.loads(p.read_text("utf-8")) if p.exists() else default
    except Exception:
        return default


def _save_json(p: Path, obj):
    try:
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), "utf-8")
        tmp.replace(p)
        return True
    except Exception as e:
        logger.error("[JSON] write fail: %s", e)
        return False


# --------------------------------------------------------------------------------------
# Export/Import bundle (SSM signed format) — "recognizable only by the program"
# --------------------------------------------------------------------------------------
_EXPORT_KEY_FILE = CONFIG_DIR / "export_key.json"


# --- DPAPI (Windows) --- protects the HMAC export key at rest, tied to the
# current Windows user profile. Same mechanism Windows apps normally use for
# local secrets; no extra dependency (ctypes + crypt32.dll, already how
# viewer.py talks to other Windows APIs).
def _dpapi_available() -> bool:
    return sys.platform == "win32"


def _dpapi_protect(data: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    class _DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))
    blob_out = _DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(blob_in), "SSM export key", None, None, None, 0, ctypes.byref(blob_out)
    )
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _dpapi_unprotect(data: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    class _DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))
    blob_out = _DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    )
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _save_export_key(key: bytes) -> None:
    """Persist `key`, DPAPI-protected when available, plaintext as a last resort
    (e.g. non-Windows dev environment) so the app keeps working either way."""
    try:
        if _dpapi_available():
            protected = _dpapi_protect(key)
            payload = {"protected_b64": base64.b64encode(protected).decode("ascii")}
        else:
            payload = {"key_b64": base64.b64encode(key).decode("ascii")}
        _EXPORT_KEY_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    except Exception:
        logger.error("[export_key] could not persist the export key", exc_info=True)


def _get_export_key() -> bytes:
    """
    Key persisted on disk (CONFIG_DIR) so exported files can be validated later.
    DPAPI-protected at rest on Windows (tied to the current user profile) —
    the key VALUE never changes across that migration, so bundles signed
    before this change keep validating.
    """
    try:
        if _EXPORT_KEY_FILE.exists():
            raw = json.loads(_EXPORT_KEY_FILE.read_text("utf-8"))
            if isinstance(raw, dict):
                protected_b64 = raw.get("protected_b64")
                if isinstance(protected_b64, str) and protected_b64.strip():
                    return _dpapi_unprotect(base64.b64decode(protected_b64.encode("ascii")))

                # Legacy plaintext key file (pre-DPAPI). Read it, then migrate
                # it to the protected format in place — same key bytes, so
                # already-exported bundles keep verifying.
                legacy_b64 = raw.get("key_b64")
                if isinstance(legacy_b64, str) and legacy_b64.strip():
                    key = base64.b64decode(legacy_b64.encode("ascii"))
                    if _dpapi_available():
                        _save_export_key(key)
                    return key
    except Exception:
        logger.error("[export_key] could not read the export key; generating a new one", exc_info=True)

    key = secrets.token_bytes(32)
    _save_export_key(key)
    return key


def _kid_from_key(key: bytes) -> str:
    try:
        return hashlib.sha256(key).hexdigest()[:10]
    except Exception:
        return "default"


def _canon_json_bytes(obj) -> bytes:
    s = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return s.encode("utf-8")


def _hmac_b64url(key: bytes, msg: bytes) -> str:
    mac = hmac.new(key, msg, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode("ascii").rstrip("=")


def _sign_bundle_payload(payload: dict) -> dict:
    key = _get_export_key()
    kid = _kid_from_key(key)
    mac = _hmac_b64url(key, _canon_json_bytes(payload))
    return {"alg": "HS256", "kid": kid, "mac": mac}


def _verify_bundle(bundle: dict) -> tuple[bool, str]:
    if not isinstance(bundle, dict):
        return (False, "invalid_json")

    if bundle.get("format") != "SSM_DATA_V1" or bundle.get("ssm_bundle") is not True:
        return (False, "invalid_format")

    sig = bundle.get("sig")
    if not isinstance(sig, dict):
        return (False, "missing_sig")

    got_mac = str(sig.get("mac") or "")
    got_kid = str(sig.get("kid") or "")
    if not got_mac or not got_kid:
        return (False, "bad_sig")

    payload = dict(bundle)
    payload.pop("sig", None)

    key = _get_export_key()
    if _kid_from_key(key) != got_kid:
        return (False, "wrong_key")

    exp = _hmac_b64url(key, _canon_json_bytes(payload))
    if not hmac.compare_digest(exp, got_mac):
        return (False, "bad_mac")

    return (True, "ok")


# --------------------------------------------------------------------------------------
# Business
# --------------------------------------------------------------------------------------
def _load_business() -> dict:
    raw = _load_json(BUSINESS_FILE, {})
    return raw if isinstance(raw, dict) else {}


def _save_business(data: dict) -> bool:
    return _save_json(BUSINESS_FILE, data or {})


_IMG_DATAURL_RE = re.compile(
    r'^data:image/(png|jpe?g|webp|gif|bmp|svg\+xml);base64,[A-Za-z0-9+/=\s]+$',
    re.IGNORECASE
)

def _dataurl_to_image_bytes(dataurl: str) -> tuple[bytes | None, str | None]:
    """Decode a data:image/...;base64,... into bytes and return (bytes, ext).

    We convert WEBP/GIF/BMP to PNG when Pillow is available, because some fpdf2 builds
    don't support these formats directly.
    """
    try:
        s = (dataurl or "").strip()
        if not s.startswith("data:image/") or ";base64," not in s:
            return None, None
        head, b64 = s.split(";base64,", 1)
        mime = head[len("data:"):]  # image/png
        ext = mime.split("/", 1)[1].lower()
        raw = base64.b64decode(b64.encode("utf-8"), validate=False)

        if ext in ("png", "jpg", "jpeg"):
            return raw, ext

        # Convert to PNG if possible
        try:
            from PIL import Image  # type: ignore
            import io as _io
            im = Image.open(_io.BytesIO(raw))
            out = _io.BytesIO()
            im.save(out, format="PNG")
            return out.getvalue(), "png"
        except Exception:
            return raw, ext
    except Exception:
        return None, None



# --------------------------------------------------------------------------------------
# Clients DB (persistente)
# --------------------------------------------------------------------------------------
def _load_clients_db() -> list[dict]:
    raw = _load_json(CLIENTS_FILE, [])
    items = raw if isinstance(raw, list) else []
    out = []
    for c in items:
        d = dict(c or {})
        code = str(d.get("code", "") or "").strip()
        if not re.fullmatch(r"\d{6}", code or ""):
            continue
        d.setdefault("name", "")
        d.setdefault("address", "")
        d.setdefault("contact", "")
        d.setdefault("client_description", d.get("description", "") or "")
        d.setdefault("sales_count", int(d.get("sales_count", 0) or 0))

        ca = d.get("created_at")
        if isinstance(ca, datetime):
            d["created_at"] = ca.isoformat()
        elif not isinstance(ca, str):
            d["created_at"] = datetime.now().isoformat()

        out.append(d)

    out.sort(key=lambda x: (x.get("created_at") or ""), reverse=True)
    return out


def _save_clients_db(items: list[dict]) -> bool:
    return _save_json(CLIENTS_FILE, items)


# --------------------------------------------------------------------------------------
# Sales load/save (CORRIGIDO p/ aceitar formatos antigos e expor amount/final_amount)
# --------------------------------------------------------------------------------------
def _parse_money_any(v) -> float:
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return 0.0
    s = re.sub(r"[^\d,.\-]", "", s)
    if not s:
        return 0.0

    # 1.234,56 / 1,234.56
    if "," in s and "." in s:
        lc = s.rfind(",")
        ld = s.rfind(".")
        dec = "," if lc > ld else "."
        grp = "." if dec == "," else ","
        s = s.replace(grp, "").replace(dec, ".")
        try:
            return float(s)
        except Exception:
            return 0.0

    # 1.234 (pt) ou 1,234 (en) -> tenta inferir
    if "," in s:
        # se parece "1,234" e não tem decimais -> remove vírgula como grupo
        if re.fullmatch(r"-?\d{1,3}(,\d{3})+", s):
            s = s.replace(",", "")
        else:
            s = s.replace(".", "").replace(",", ".")
    else:
        # só ponto
        if re.fullmatch(r"-?\d{1,3}(\.\d{3})+", s):
            s = s.replace(".", "")
    try:
        return float(s)
    except Exception:
        return 0.0


def _safe_str(v) -> str:
    """Texto enxuto: str() tolerante a None + strip(). Usado nos exports CSV."""
    return (str(v) if v is not None else "").strip()


def _as_dt(v):
    """datetime a partir de datetime ou string ISO ('Z' aceito); None se nao der.
    Diferente de _coerce_created_at, que sempre devolve um datetime (fallback now)."""
    if isinstance(v, datetime):
        return v
    if isinstance(v, str) and v.strip():
        try:
            return datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
        except Exception:
            return None
    return None


def _coerce_created_at(v) -> datetime:
    if isinstance(v, datetime):
        return v
    if isinstance(v, (int, float)) and v > 0:
        try:
            return datetime.fromtimestamp(float(v))
        except Exception:
            return datetime.now()
    if isinstance(v, str) and v.strip():
        s = v.strip()
        # ISO
        try:
            return datetime.fromisoformat(s)
        except Exception:
            pass
        # dd/mm/yyyy hh:mm
        m = re.match(r"^(\d{2})/(\d{2})/(\d{4})(?:\s+(\d{2}):(\d{2})(?::(\d{2}))?)?$", s)
        if m:
            dd, mm, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
            hh = int(m.group(4) or 0)
            mi = int(m.group(5) or 0)
            ss = int(m.group(6) or 0)
            try:
                return datetime(yy, mm, dd, hh, mi, ss)
            except Exception:
                return datetime.now()
    return datetime.now()


def _normalize_sale_dict(raw: dict) -> dict:
    d = dict(raw or {})

    # aliases (formatos antigos)
    if "amount" not in d and "value" in d:
        d["amount"] = d.get("value")
    if "final_amount" not in d and "final_value" in d:
        d["final_amount"] = d.get("final_value")
    if "sale_description" not in d and "description" in d:
        d["sale_description"] = d.get("description")

    d.setdefault("code", "")
    d.setdefault("name", "")
    d.setdefault("address", "")
    d.setdefault("contact", "")
    d.setdefault("client_description", "")
    d.setdefault("product", "")
    d.setdefault("sale_description", "")

    d["amount"] = _parse_money_any(d.get("amount"))
    if "final_amount" in d and d.get("final_amount") not in (None, ""):
        d["final_amount"] = _parse_money_any(d.get("final_amount"))
    else:
        d["final_amount"] = float(d.get("amount") or 0.0)

    d["created_at"] = _coerce_created_at(d.get("created_at") or d.get("date") or d.get("datetime"))

    # id (garante int)
    try:
        d["id"] = int(d.get("id", 0) or 0)
    except Exception:
        d["id"] = 0

    return d


def _load_sales() -> tuple[list[dict], int]:
    raw = _load_json(SALES_FILE, [])
    data_list: list

    if isinstance(raw, list):
        data_list = raw
    elif isinstance(raw, dict):
        # formatos antigos: {"sales":[...]} / {"items":[...]}
        if isinstance(raw.get("sales"), list):
            data_list = raw["sales"]
        elif isinstance(raw.get("items"), list):
            data_list = raw["items"]
        else:
            data_list = []
    else:
        data_list = []

    sales: list[dict] = []
    used_ids: set[int] = set()
    mx = 0
    changed = False

    for item in data_list:
        if not isinstance(item, dict):
            continue
        d = _normalize_sale_dict(item)

        sid = int(d.get("id", 0) or 0)
        if sid <= 0 or sid in used_ids:
            sid = mx + 1
            d["id"] = sid
            changed = True

        used_ids.add(sid)
        mx = max(mx, sid)

        sales.append(d)

    # ordena (mais recente primeiro por padrão)
    sales.sort(key=lambda x: x.get("created_at") or datetime.min, reverse=True)

    # se ajustou ids/formato, persiste list normalizada
    if changed:
        out = []
        for v in sales:
            e = dict(v)
            if isinstance(e.get("created_at"), datetime):
                e["created_at"] = e["created_at"].isoformat()
            out.append(e)
        _save_json(SALES_FILE, out)

    next_id = (mx + 1) if sales else 1
    return sales, next_id


SALES, NEXT_ID = _load_sales()


def _reload_sales_from_disk():
    global SALES, NEXT_ID
    SALES, NEXT_ID = _load_sales()


def _save_sales():
    # salva SEM perder campos esperados pelo management.html
    out = []
    for v in SALES:
        d = dict(v)
        if isinstance(d.get("created_at"), datetime):
            d["created_at"] = d["created_at"].isoformat()
        out.append(d)
    ok = _save_json(SALES_FILE, out)
    _sync_clients_db_from_sales()
    return ok


# --------------------------------------------------------------------------------------
# Clients derived from sales (sincroniza sales_count, etc.)
# --------------------------------------------------------------------------------------
def _clients_map_from_sales_latest() -> dict[str, dict]:
    m: dict[str, dict] = {}
    for v in SALES:
        code = str(v.get("code", "") or "").strip()
        if not re.fullmatch(r"\d{6}", code or ""):
            continue
        cur = m.get(code)
        if (not cur) or (v.get("created_at") > cur.get("created_at")):
            m[code] = {
                "code": code,
                "name": v.get("name", "") or "",
                "address": v.get("address", "") or "",
                "contact": v.get("contact", "") or "",
                "client_description": v.get("client_description", "") or "",
                "created_at": v.get("created_at"),
            }
    return m


def _sync_clients_db_from_sales():
    db_list = _load_clients_db()
    db = {c["code"]: c for c in db_list}

    cnt = defaultdict(int)
    for s in SALES:
        code = str(s.get("code", "") or "").strip()
        if re.fullmatch(r"\d{6}", code or ""):
            cnt[code] += 1

    sales_latest = _clients_map_from_sales_latest()
    for code, info in sales_latest.items():
        if not re.fullmatch(r"\d{6}", code or ""):
            continue

        if code not in db:
            ca = info.get("created_at")
            db[code] = {
                "code": code,
                "name": info.get("name", "") or "",
                "address": info.get("address", "") or "",
                "contact": info.get("contact", "") or "",
                "client_description": info.get("client_description", "") or "",
                "created_at": ca.isoformat() if isinstance(ca, datetime) else datetime.now().isoformat(),
                "sales_count": 0,
            }
        else:
            if info.get("name"):
                db[code]["name"] = info["name"]
            if info.get("address"):
                db[code]["address"] = info["address"]
            if info.get("contact"):
                db[code]["contact"] = info["contact"]
            if info.get("client_description") is not None:
                db[code]["client_description"] = info.get("client_description", "") or db[code].get("client_description", "")

    for code, c in db.items():
        c["sales_count"] = int(cnt.get(code, 0))

    out = list(db.values())
    out.sort(key=lambda x: (x.get("created_at") or ""), reverse=True)
    _save_clients_db(out)


def clients_map_latest() -> dict[str, dict]:
    db = {c["code"]: c for c in _load_clients_db()}
    sales_latest = _clients_map_from_sales_latest()

    for code, info in sales_latest.items():
        if not re.fullmatch(r"\d{6}", code or ""):
            continue
        if code in db:
            db[code]["name"] = info.get("name", "") or db[code].get("name", "")
            db[code]["address"] = info.get("address", "") or db[code].get("address", "")
            db[code]["contact"] = info.get("contact", "") or db[code].get("contact", "")
            db[code]["client_description"] = info.get("client_description", "") or db[code].get("client_description", "")

            dt_sales = info.get("created_at")
            dt_db = None
            try:
                dt_db = datetime.fromisoformat(db[code].get("created_at")) if isinstance(db[code].get("created_at"), str) else None
            except Exception:
                dt_db = None
            newest = max([d for d in [dt_sales, dt_db] if isinstance(d, datetime)], default=dt_sales or dt_db)
            if isinstance(newest, datetime):
                db[code]["created_at"] = newest.isoformat()
        else:
            ca = info.get("created_at")
            db[code] = {
                "code": code,
                "name": info.get("name", "") or "",
                "address": info.get("address", "") or "",
                "contact": info.get("contact", "") or "",
                "client_description": info.get("client_description", "") or "",
                "created_at": ca.isoformat() if isinstance(ca, datetime) else datetime.now().isoformat(),
                "sales_count": 0
            }

    return db


def generate_unique_code() -> str:
    existing = set(clients_map_latest().keys())
    for _ in range(2000):
        c = f"{random.randint(0, 999999):06d}"
        if c not in existing:
            return c
    raise RuntimeError("unique code fail")


def update_client_everywhere(code, name, addr, cont, desc):
    for v in SALES:
        if v.get("code") == code:
            v["name"] = name
            v["address"] = addr
            v["contact"] = cont
            v["client_description"] = desc


def available_years(sales_list: list[dict] | None = None) -> list[int]:
    src = sales_list if sales_list is not None else SALES
    ys = sorted({(v.get("created_at") or datetime.now()).year for v in src if isinstance(v.get("created_at"), datetime)})
    return ys or [datetime.now().year]


def _month_pairs():
    loc = str(get_locale() or "en")
    try:
        names = get_month_names("wide", locale=loc)
        return [(i, names[i].capitalize()) for i in range(1, 13)]
    except Exception:
        base = ["January", "February", "March", "April", "May", "June",
                "July", "August", "September", "October", "November", "December"]
        return [(i, _t(n)) for i, n in enumerate(base, 1)]


# --------------------------------------------------------------------------------------
# Filters (sales/clients)
# --------------------------------------------------------------------------------------
def _load_filters():
    default = {
        "sales": {"q": "", "field": "code", "order": "desc", "month": None, "year": None},
        "clients": {"q": "", "field": "code", "order": "desc", "month": None, "year": None},
    }
    raw = _load_json(FILTERS_FILE, default)
    if not isinstance(raw, dict):
        raw = default
    for sec in ("sales", "clients"):
        raw.setdefault(sec, default[sec].copy())
        for k in default[sec]:
            raw[sec].setdefault(k, default[sec][k])
    return raw


def _save_filters(f):
    _save_json(FILTERS_FILE, f)


def _apply_sales_filters(sales: list[dict], sf: dict) -> list[dict]:
    order = (sf.get("order") or "desc").lower()
    month = sf.get("month", None)
    year = sf.get("year", None)
    q = (sf.get("q") or "").strip()
    field = (sf.get("field") or "code").lower()

    data = []
    for v in sales:
        dt = v.get("created_at")
        if not isinstance(dt, datetime):
            continue
        if month is not None and dt.month != month:
            continue
        if year is not None and dt.year != year:
            continue
        data.append(v)

    if q:
        qn = q.lower()

        def match(v):
            if field == "code":
                return qn in (str(v.get("code", "")) or "").lower()
            if field == "product":
                return qn in (str(v.get("product", "")) or "").lower()
            if field == "description":
                return qn in (str(v.get("sale_description", "")) or "").lower()
            return True

        data = [v for v in data if match(v)]

    data.sort(key=lambda v: v.get("created_at") or datetime.min, reverse=(order != "asc"))
    return data


def _get_param_int(n):
    if n not in request.args:
        return None, False
    raw = (request.args.get(n, "") or "").strip()
    if raw == "":
        return None, True
    try:
        return int(raw), True
    except Exception:
        return None, True


def _get_param_str(n):
    if n not in request.args:
        return None, False
    return request.args.get(n), True


# --------------------------------------------------------------------------------------
# Home / Welcome / First setup
# --------------------------------------------------------------------------------------
@app.route("/")
def home():
    s = _load_settings()

    if bool(s.get("setup_done")):
        return redirect(url_for("list_sales"))

    d = _load_business()
    has_any = any((d.get(k) or "").strip() for k in (
        "name", "phone", "addr1", "tax_id", "logo_dataurl"
    ))
    if has_any:
        s["setup_done"] = True
        _save_settings(s)
        with contextlib.suppress(Exception):
            _save_bootstrap_dir(Path(s.get("data_dir") or str(DATA_DIR)))
        return redirect(url_for("list_sales"))

    return redirect(url_for("welcome"))


@app.get("/welcome", endpoint="welcome")
def welcome():
    s = _load_settings()
    if bool(s.get("setup_done")):
        return redirect(url_for("list_sales"))

    data = _load_business()
    default_dir = (s.get("explorer_dir") or s.get("pdf_dir") or _DEFAULT_SETTINGS["explorer_dir"])
    return render_template("welcome.html", BUSINESS=data, DEFAULT_DIR=default_dir, SETTINGS=s)


@app.post("/first_setup/save", endpoint="first_setup_save")
def first_setup_save():
    lang = (request.form.get("lang") or request.form.get("lang_select") or "en").strip()
    theme_mode = (request.form.get("theme_mode") or "auto").strip().lower()
    base_dir_in = (request.form.get("default_directory") or request.form.get("dir") or request.form.get("dirInput") or "").strip()

    if theme_mode not in ("light", "dark", "auto"):
        theme_mode = "auto"

    target_dir = DATA_DIR
    try:
        lower = base_dir_in.lower()
    except Exception:
        lower = ""
    if base_dir_in and ("browser" not in lower) and ("selected folder" not in lower):
        try:
            target_dir = _migrate_data_dir_to(base_dir_in)
        except Exception as e:
            logger.error("[first_setup] migrate error: %s", e)
            target_dir = DATA_DIR

    s = _load_settings()
    explorer_dir = base_dir_in or s.get("explorer_dir") or _DEFAULT_SETTINGS["explorer_dir"]
    pdf_dir = base_dir_in or s.get("pdf_dir") or explorer_dir or _DEFAULT_SETTINGS["pdf_dir"]

    s.update({
        "preferred_locale": _canon(lang),
        "theme_mode": theme_mode,
        "data_dir": _posix(target_dir),
        "explorer_dir": _posix(explorer_dir),
        "pdf_dir": _posix(pdf_dir),
        "setup_done": True,
    })
    if not _save_settings(s):
        flash(_t("Failed to save."), "error")
        return redirect(url_for("welcome"))
    with contextlib.suppress(Exception):
        _save_bootstrap_dir(Path(s.get("data_dir") or str(DATA_DIR)))

    data = _load_business()
    data.update({
        "name": (request.form.get("name") or "").strip(),
        "phone": (request.form.get("phone") or "").strip(),
        "addr1": (request.form.get("addr1") or "").strip(),
        "tax_id": (request.form.get("tax_id") or "").strip(),
    })

    remove_logo = (request.form.get("remove_logo") or "").lower() in ("on", "1", "true", "yes")
    posted_logo = (request.form.get("logo_dataurl") or "").strip()
    if remove_logo:
        data["logo_dataurl"] = ""
    elif posted_logo and _IMG_DATAURL_RE.match(posted_logo):
        data["logo_dataurl"] = posted_logo

    if not _save_business(data):
        flash(_t("Failed to save."), "error")
        return redirect(url_for("welcome"))

    _reload_sales_from_disk()

    resp = redirect(url_for("list_sales"))
    resp.set_cookie("lang", _canon(lang), max_age=60 * 60 * 24 * 365, samesite="Lax")
    return resp


# --------------------------------------------------------------------------------------
# Settings / Business
# --------------------------------------------------------------------------------------
@app.get("/settings", endpoint="settings_page")
def settings_page():
    settings = _load_settings()
    # Só os idiomas realmente suportados (languages._SUPPORTED_BASES) — igual
    # ao settingspopup.html. Antes havia fr/de/it/ja/ko/ar/hi, que só caíam
    # em inglês.
    language_choices = [
        ("auto", "Auto"),
        ("en", "English"),
        ("pt_BR", "Português (Brasil)"),
        ("es", "Español"),
    ]
    theme_choices = [("light", "Light"), ("dark", "Dark"), ("auto", "Auto")]
    return render_template(
        "settings.html",
        active="settings",
        SETTINGS=settings,
        LANGUAGE_CHOICES=language_choices,
        THEME_CHOICES=theme_choices
    )


@app.post("/settings/save", endpoint="save_settings")
def save_settings():
    s = _load_settings()

    pref_locale = (request.form.get("preferred_locale") or s["preferred_locale"]).strip()
    theme_mode = (request.form.get("theme_mode") or s["theme_mode"]).strip().lower()
    data_dir_in = (request.form.get("data_dir") or s["data_dir"]).strip()
    data_dir_in = _expand_appdata_placeholder(data_dir_in)
    explorer_in = (request.form.get("explorer_dir") or s["explorer_dir"]).strip()
    pdf_dir_in = (request.form.get("pdf_dir") or s.get("pdf_dir") or s.get("explorer_dir") or _DEFAULT_SETTINGS["pdf_dir"]).strip()

    if theme_mode not in ("light", "dark", "auto"):
        theme_mode = "auto"
    if not pref_locale:
        pref_locale = "auto"

    try:
        base_choice = Path(os.path.expanduser(data_dir_in)).resolve()
        target_dir = _migrate_data_dir_to(base_choice)
    except Exception as e:
        logger.error("[settings/save] migrate error: %s", e)
        target_dir = DATA_DIR

    # Keep user-selected directories.
    if not explorer_in:
        explorer_in = (s.get("explorer_dir") or _DEFAULT_SETTINGS["explorer_dir"]).strip()
    if not pdf_dir_in:
        pdf_dir_in = (s.get("pdf_dir") or explorer_in or _DEFAULT_SETTINGS["pdf_dir"]).strip()

    s.update({
        "preferred_locale": pref_locale,
        "theme_mode": theme_mode,
        "data_dir": _posix(target_dir),
        "explorer_dir": _posix(explorer_in),
        "pdf_dir": _posix(pdf_dir_in),
    })
    if not _save_settings(s):
        # settingspopup.html / welcome.html post this via fetch(); a non-2xx
        # status is the signal they can act on. (The current JS does not yet
        # surface it — that is a separate front-end fix.)
        return (_t("Failed to save."), 500)
    with contextlib.suppress(Exception):
        _save_bootstrap_dir(Path(s.get("data_dir") or str(DATA_DIR)))

    _reload_sales_from_disk()

    resp = redirect(url_for("settings_page"))
    resp.set_cookie("lang", _canon(pref_locale), max_age=60 * 60 * 24 * 365, samesite="Lax")
    return resp


# --------------------------------------------------------------------------------------
# Data export/import/reset (used by settings.html)
# --------------------------------------------------------------------------------------
@app.get("/data/export")
def data_export():
    """
    Exporta clientes + vendas no formato legado (data.json) usado pelo Settings.html.
    Esse formato é um bundle assinado (SSM_DATA_V1) para manter compatibilidade.
    """
    try:
        _bin_gc()

        # Carrega do disco (para não depender do estado em memória)
        clients = _load_clients_db()
        sales, _next_id = _load_sales()
        business = _load_business()

        # Serializa created_at (datetime -> ISO)
        sales_out = []
        for s in sales:
            d = dict(s or {})
            ca = d.get("created_at")
            if isinstance(ca, datetime):
                d["created_at"] = ca.isoformat()
            elif not isinstance(ca, str):
                d["created_at"] = datetime.now().isoformat()
            sales_out.append(d)

        payload = {
            "ssm_bundle": True,
            "format": "SSM_DATA_V1",
            "created_at": datetime.now().isoformat(),
            "data": {
                "clients": clients,
                "sales": sales_out,
                "business": business,
            },
            "meta": {
                "clients_count": int(len(clients)),
                "sales_count": int(len(sales_out)),
            },
        }
        # Assina SEM incluir "sig" no payload assinado
        payload["sig"] = _sign_bundle_payload(dict(payload))

        b = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        fname = "data.json"
        token = _bin_put(b, fname, "application/json", ttl=600)

        return jsonify({
            "ok": True,
            "filename": fname,
            "download_url": url_for("download_token", token=token, _external=True)
        })
    except Exception as e:
        app.logger.exception("data_export failed")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.post("/data/import")
def data_import():
    """
    Importa clientes + vendas.
    Aceita:
      - Bundle legado assinado: {"ssm_bundle":true,"format":"SSM_DATA_V1","data":{"clients":[...],"sales":[...]},...}
      - Formato simples antigo: {"clients":[...],"sales":[...], ...}
      - Wrapper sem assinatura: {"data":{"clients":[...],"sales":[...]}}
    """
    try:
        up = request.files.get("file")
        if not up:
            return jsonify({"ok": False, "error": "No file"}), 400

        raw = up.read()
        try:
            data = json.loads(raw.decode("utf-8", errors="ignore"))
        except Exception:
            return jsonify({"ok": False, "error": "Invalid JSON"}), 400

        if not isinstance(data, dict):
            return jsonify({"ok": False, "error": "Invalid JSON"}), 400

        verified = None
        verify_reason = None

        clients = None
        sales = None
        business = None

        # 1) Bundle SSM_DATA_V1
        if data.get("ssm_bundle") is True and data.get("format") == "SSM_DATA_V1" and isinstance(data.get("data"), dict):
            ok, reason = _verify_bundle(data)
            verified = bool(ok)
            verify_reason = str(reason)
            inner = data.get("data") or {}
            clients = inner.get("clients")
            sales = inner.get("sales")
            if isinstance(inner.get("business"), dict):
                business = inner.get("business")

        # 2) Formato simples / wrapper sem assinatura
        if not isinstance(clients, list) or not isinstance(sales, list):
            clients = data.get("clients")
            sales = data.get("sales")

        if (not isinstance(clients, list) or not isinstance(sales, list)) and isinstance(data.get("data"), dict):
            inner = data.get("data") or {}
            if isinstance(inner.get("clients"), list) and isinstance(inner.get("sales"), list):
                clients = inner.get("clients")
                sales = inner.get("sales")

        # alguns dumps antigos salvavam sales como [list, next_id]
        if isinstance(sales, (list, tuple)) and len(sales) == 2 and isinstance(sales[0], list) and isinstance(sales[1], int):
            sales = sales[0]

        if not isinstance(clients, list) or not isinstance(sales, list):
            return jsonify({"ok": False, "error": "Missing clients/sales"}), 400

        # --- normalização leve (mantém compatibilidade com versões antigas) ---
        clients_out = []
        for c in clients:
            if not isinstance(c, dict):
                continue
            d = dict(c or {})
            code = str(d.get("code", "") or "").strip()
            if not re.fullmatch(r"\d{6}", code or ""):
                continue
            d["code"] = code

            # chaves antigas
            if "description" in d and "client_description" not in d:
                d["client_description"] = d.get("description", "") or ""

            d.setdefault("name", "")
            d.setdefault("address", "")
            d.setdefault("contact", "")
            d.setdefault("client_description", "")
            d["sales_count"] = int(d.get("sales_count", 0) or 0)

            ca = d.get("created_at")
            if isinstance(ca, datetime):
                d["created_at"] = ca.isoformat()
            elif not isinstance(ca, str):
                d["created_at"] = datetime.now().isoformat()

            clients_out.append(d)

        sales_out = []
        for s in sales:
            if not isinstance(s, dict):
                continue
            d = dict(s or {})

            # chaves antigas
            if "description" in d and "sale_description" not in d:
                d["sale_description"] = d.get("description", "") or ""
            if "final" in d and "final_amount" not in d:
                d["final_amount"] = d.get("final")
            if "value" in d and "amount" not in d:
                d["amount"] = d.get("value")

            ca = d.get("created_at")
            if isinstance(ca, datetime):
                d["created_at"] = ca.isoformat()
            elif isinstance(ca, (int, float)):
                d["created_at"] = datetime.fromtimestamp(float(ca)).isoformat()
            elif not isinstance(ca, str):
                d["created_at"] = datetime.now().isoformat()

            sales_out.append(d)

        # salva no disco (formato normalizado do app)
        ok_clients = _save_clients_db(clients_out)
        ok_sales = _save_json(SALES_FILE, sales_out)
        ok_business = _save_business(business) if isinstance(business, dict) else True
        if not (ok_clients and ok_sales and ok_business):
            failed = [n for n, ok in
                      (("clients.json", ok_clients), ("sales.json", ok_sales), ("business.json", ok_business))
                      if not ok]
            logger.error("[data_import] partial write, failed: %s", failed)
            return jsonify({
                "ok": False,
                "error": _t("Failed to save."),
                "partial_write": failed,
            }), 500

        # recarrega estado em memória e sincroniza contagens
        _reload_sales_from_disk()
        _sync_clients_db_from_sales()

        return jsonify({
            "ok": True,
            "added_sales": int(len(sales_out)),
            "added_clients": int(len(clients_out)),
            "imported_business": bool(business),
            "verified": verified,
            "verify_reason": verify_reason,
        })
    except Exception as e:
        app.logger.exception("data_import failed")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.post("/data/reset")
def data_reset():
    try:
        ok_clients = _save_clients_db([])
        ok_sales = _save_json(SALES_FILE, [])
        if not (ok_clients and ok_sales):
            failed = [n for n, ok in (("clients.json", ok_clients), ("sales.json", ok_sales)) if not ok]
            logger.error("[data_reset] partial write, failed: %s", failed)
            return jsonify({"ok": False, "error": _t("Failed to save."), "partial_write": failed}), 500

        with contextlib.suppress(Exception):
            _save_json(FILTERS_FILE, {})

        # "Redefinir" precisa voltar pro estado de instalação nova: também
        # zera Business (nome/logo/endereço/contato) — senão o Welcome
        # reaparece mas com os dados antigos da empresa ainda preenchidos.
        with contextlib.suppress(Exception):
            _save_business({})

        # Marca setup como NÃO concluído para o Welcome reaparecer
        with contextlib.suppress(Exception):
            s = _load_settings()
            s["setup_done"] = False
            _save_settings(s)

        _reload_sales_from_disk()
        _sync_clients_db_from_sales()

        return jsonify({"ok": True, "redirect": url_for("welcome")})
    except Exception as e:
        app.logger.exception("data_reset failed")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.get("/api/settings")
def api_settings():
    return jsonify({"ok": True, "data": _load_settings()})


@app.get("/business")
def business():
    # mantém as opções de anos sempre atualizadas (recarrega do disco)
    _reload_sales_from_disk()

    data = _load_business()
    return render_template(
        "business.html",
        active="business",
        BUSINESS=data,
        MONTHS=_month_pairs(),
        YEARS=available_years(SALES)
    )


def _file_to_dataurl(up) -> str:
    if not up or up.filename == "":
        return ""
    raw = up.read()
    mime, _ = mimetypes.guess_type(up.filename)
    mime = mime or "image/png"
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


@app.post("/business/save", endpoint="save_business")
def save_business():
    data = _load_business()
    data.update({
        "name": (request.form.get("name") or "").strip(),
        "phone": (request.form.get("phone") or "").strip(),
        "addr1": (request.form.get("addr1") or "").strip(),
        "tax_id": (request.form.get("tax_id") or "").strip(),
    })

    remove_logo = (request.form.get("remove_logo") or "").lower() in ("on", "1", "true", "yes")
    posted_logo = (request.form.get("logo_dataurl") or "").strip()
    up = request.files.get("logo")

    if remove_logo:
        data["logo_dataurl"] = ""
    elif posted_logo and _IMG_DATAURL_RE.match(posted_logo):
        data["logo_dataurl"] = posted_logo
    elif up and up.filename:
        data["logo_dataurl"] = _file_to_dataurl(up)

    if not _save_business(data):
        flash(_t("Failed to save."), "error")
        return (_t("Failed to save."), 500)
    return redirect(url_for("business"))


@app.get("/api/business")
def api_business():
    return jsonify({"ok": True, "data": _load_business()})


# --------------------------------------------------------------------------------------
# Sale form / create
# --------------------------------------------------------------------------------------
@app.get("/sale")
def new_sale():
    code = (request.args.get("code") or "").strip()
    name = address = contact = client_description = ""
    if code and re.fullmatch(r"\d{6}", code):
        info = clients_map_latest().get(code)
        if info:
            name = info.get("name", "")
            address = info.get("address", "")
            contact = info.get("contact", "")
            client_description = info.get("client_description", "")
    return render_template(
        "management.html",
        mode="sale",
        active="sale",
        code=code,
        auto_checked=True,
        name=name,
        address=address,
        contact=contact,
        client_description=client_description
    )


@app.get("/api/clients")
def api_clients_list():
    q = (request.args.get("q") or "").strip()

    def norm(s: str) -> str:
        return (s or "").strip().lower().encode("latin1", "ignore").decode("latin1")

    items = []
    for c in _load_clients_db():
        items.append({
            "code": c.get("code", "") or "",
            "name": c.get("name", "") or "",
            "contact": c.get("contact", "") or "",
            "address": c.get("address", "") or "",
            "client_description": c.get("client_description", "") or "",
            "created_at": c.get("created_at"),
        })

    if q:
        nq = norm(q)

        def match(it):
            blob = " ".join([it.get("name", ""), it.get("contact", ""), it.get("address", ""), it.get("code", "")])
            return nq in norm(blob)

        items = [it for it in items if match(it)]

    # ordena por created_at desc (iso) e name
    items.sort(key=lambda it: (it.get("created_at") or "", (it.get("name") or "").lower()), reverse=True)
    for it in items:
        it.pop("created_at", None)

    return jsonify(items)


@app.post("/api/clients")
def api_clients_create():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    contact = (data.get("contact") or "").strip()
    address = (data.get("address") or "").strip()
    desc = (data.get("client_description") or data.get("description") or data.get("clientDescription") or "").strip()

    if not name:
        return jsonify(ok=False, error=_t("Name is required.")), 400

    code = generate_unique_code()
    rec = {
        "code": code,
        "name": name,
        "contact": contact,
        "address": address,
        "client_description": desc,
        "created_at": datetime.now().isoformat(),
        "sales_count": 0,
    }

    db = _load_clients_db()
    db.append(rec)
    _save_clients_db(db)

    return jsonify(ok=True, client=rec), 201


@app.get("/api/client/<code>", endpoint="api_client")
def api_client(code):
    if not re.fullmatch(r"\d{6}", code or ""):
        return jsonify({
            "exists": False, "invalid": True,
            "message": _t("Code must have exactly 6 digits (e.g., 012345).")
        })
    info = clients_map_latest().get(code)
    if not info:
        return jsonify({"exists": False, "invalid": False})
    return jsonify({"exists": True, "invalid": False, "client": {
        "code": info.get("code", code),
        "name": info.get("name", "") or "",
        "address": info.get("address", "") or "",
        "contact": info.get("contact", "") or "",
        "client_description": info.get("client_description", "") or "",
    }})


@app.post("/sale", endpoint="post_new_sale")
def post_new_sale():
    global NEXT_ID

    form = request.form
    code_in = (form.get("client_code") or form.get("code") or "").strip()
    existing_flag = (form.get("existing_client") or "").strip().lower() in ("1", "true", "yes", "on")

    name_in = (form.get("name") or "").strip()
    addr_in = (form.get("address") or "").strip()
    cont_in = (form.get("contact") or "").strip()
    desc_in = (form.get("clientDescription") or "").strip()

    product = (form.get("product") or "").strip()
    value_str = (form.get("value") or "").strip()
    sale_desc = (form.get("saleDescription") or "").strip()
    no_discount = (form.get("no_discount") in ("on", "1", "true", "yes"))
    final_value_str = (form.get("final_value") or "").strip()

    amount = _parse_money_any(value_str)
    if amount < 0:
        flash(_t("Enter a valid numeric amount (e.g., 1,234.56 or 1.234,56)."), "error")
        return redirect(url_for("new_sale", code=code_in or ""))

    if no_discount or not final_value_str:
        final_amount = amount
    else:
        final_amount = _parse_money_any(final_value_str)
        if final_amount < 0:
            flash(_t("Enter a valid final amount (e.g., 1,000.00) or keep 'No Discount'."), "error")
            return redirect(url_for("new_sale", code=code_in or ""))

    if final_amount > amount + 1e-9:
        flash(_t("Final amount cannot be greater than the original amount."), "error")
        return redirect(url_for("new_sale", code=code_in or ""))

    cmap = clients_map_latest()
    if existing_flag and re.fullmatch(r"\d{6}", code_in or ""):
        code_eff = code_in
        info = cmap.get(code_eff, {}) or {}
        name_eff = name_in or (info.get("name") or "")
        addr_eff = addr_in or (info.get("address") or "")
        cont_eff = cont_in or (info.get("contact") or "")
        desc_eff = desc_in or (info.get("client_description") or "")
    else:
        code_eff = generate_unique_code()
        name_eff, addr_eff, cont_eff, desc_eff = name_in, addr_in, cont_in, desc_in

    if not product:
        flash(_t("Fill the product."), "error")
        return redirect(url_for("new_sale", code=code_eff or ""))

    if not name_eff:
        flash(_t("Name is required."), "error")
        return redirect(url_for("new_sale", code=code_eff or ""))

    record = {
        "id": NEXT_ID,
        "code": code_eff,
        "name": name_eff,
        "address": addr_eff,
        "contact": cont_eff,
        "client_description": desc_eff,
        "product": product,
        "amount": float(amount),
        "final_amount": float(final_amount),
        "no_discount": bool(no_discount),
        "sale_description": sale_desc,
        "created_at": datetime.now(),
    }
    SALES.append(record)
    NEXT_ID += 1

    update_client_everywhere(code_eff, name_eff, addr_eff, cont_eff, desc_eff)
    if not _save_sales():
        # Rollback the in-memory append so the list view (which reloads from
        # disk) and NEXT_ID stay consistent with what actually persisted.
        SALES.pop()
        NEXT_ID -= 1
        flash(_t("Failed to save."), "error")
        return redirect(url_for("new_sale", code=code_eff or ""))

    return redirect(url_for("list_sales"))


# --------------------------------------------------------------------------------------
# SALES list (AGORA IGUAL AO CLIENTS: recarrega do disco em toda requisição)
# --------------------------------------------------------------------------------------
@app.get("/sales")
def list_sales():
    # ✅ MESMO COMPORTAMENTO DO CLIENTS (snapshot sempre atual)
    _reload_sales_from_disk()

    filters = _load_filters()
    sf = filters.get("sales", {})

    field_in, fset = _get_param_str("field")
    order_in, oset = _get_param_str("order")
    month_in, mset = _get_param_int("month")
    year_in, yset = _get_param_int("year")
    q_in, qset = _get_param_str("q")

    if fset:
        v = (field_in or "").lower()
        sf["field"] = v if v in ("code", "product", "description") else "code"
    if oset:
        sf["order"] = "asc" if (order_in or "desc").lower() == "asc" else "desc"
    if mset:
        sf["month"] = month_in
    if yset:
        sf["year"] = year_in
    if qset:
        sf["q"] = (q_in or "").strip()

    filters["sales"] = sf
    _save_filters(filters)

    filtered = _apply_sales_filters(SALES, sf)

    # >>> IMPORTANTE: NÃO renomear campos. O management.html usa r.amount e r.final_amount.
    view = []
    for r in filtered:
        view.append({
            "id": int(r.get("id", 0) or 0),
            "code": r.get("code", "") or "",
            "name": r.get("name", "") or "",
            "product": r.get("product", "") or "",
            "sale_description": r.get("sale_description", "") or "",
            "amount": float(r.get("amount", 0.0) or 0.0),
            "final_amount": float(r.get("final_amount", r.get("amount", 0.0)) or 0.0),
            "created_at": r.get("created_at") if isinstance(r.get("created_at"), datetime) else _coerce_created_at(r.get("created_at")),
        })

    subtotal_gross = sum(v.get("amount", 0.0) for v in view)
    subtotal_final = sum(v.get("final_amount", v.get("amount", 0.0)) for v in view)

    return render_template(
        "management.html",
        mode="sales",
        active="sales",
        sales=view,
        subtotal_gross=subtotal_gross,
        subtotal_final=subtotal_final,
        order=sf.get("order", "desc"),
        month=sf.get("month"),
        year=sf.get("year"),
        q=sf.get("q", ""),
        field=sf.get("field", "code"),
        MONTHS=_month_pairs(),
        YEARS=available_years(SALES),
    )


# --------------------------------------------------------------------------------------
# CLIENTS list (mantido)
# --------------------------------------------------------------------------------------
def _load_clients_snapshot():
    raw = _load_json(CLIENTS_FILE, [])
    return raw if isinstance(raw, list) else []


@app.get("/clients")
def list_clients():
    filters = _load_filters()
    cf = filters.get("clients", {})

    field_in, fset = _get_param_str("field")
    order_in, oset = _get_param_str("order")
    month_in, mset = _get_param_int("month")
    year_in, yset = _get_param_int("year")
    q_in, qset = _get_param_str("q")

    if fset:
        v = (field_in or "").lower()
        cf["field"] = v if v in ("code", "name", "address", "contact", "description") else "code"
    if oset:
        cf["order"] = "asc" if (order_in or "desc").lower() == "asc" else "desc"
    if mset:
        cf["month"] = month_in
    if yset:
        cf["year"] = year_in
    if qset:
        cf["q"] = (q_in or "").strip()

    filters["clients"] = cf
    _save_filters(filters)

    if cf.get("month") is not None or cf.get("year") is not None:
        # (mantém coerência: garante SALES atualizado antes do recorte por período)
        _reload_sales_from_disk()

        sales_period = [
            v for v in SALES
            if (cf.get("month") is None or (v.get("created_at") and v["created_at"].month == cf["month"]))
            and (cf.get("year") is None or (v.get("created_at") and v["created_at"].year == cf["year"]))
        ]
        cmap = {}
        count = defaultdict(int)
        for v in sales_period:
            code = v.get("code", "")
            if not re.fullmatch(r"\d{6}", str(code or "")):
                continue
            count[code] += 1
            cur = cmap.get(code)
            if (not cur) or (v.get("created_at") > cur.get("created_at")):
                cmap[code] = {
                    "code": code,
                    "name": v.get("name", "") or "",
                    "address": v.get("address", "") or "",
                    "contact": v.get("contact", "") or "",
                    "client_description": v.get("client_description", "") or "",
                    "created_at": v.get("created_at"),
                }
        clients = [{**d, "sales_in_period": count[c]} for c, d in cmap.items()]
    else:
        snapshot = _load_clients_snapshot()
        clients = []
        for c in snapshot:
            ca = c.get("created_at")
            try:
                created_at = datetime.fromisoformat(ca) if isinstance(ca, str) else ca
            except Exception:
                created_at = datetime.now()
            clients.append({
                "code": c.get("code", ""),
                "name": c.get("name") or "",
                "address": c.get("address") or "",
                "contact": c.get("contact") or "",
                "client_description": c.get("client_description") or "",
                "created_at": created_at if isinstance(created_at, datetime) else datetime.now(),
                "sales_in_period": int(c.get("sales_count", 0)),
            })

    if cf.get("q"):
        qn = (cf["q"] or "").lower()
        f = (cf.get("field") or "code")

        def match(c):
            if f == "code":
                return qn in (str(c.get("code", "")) or "").lower()
            if f == "name":
                return qn in (str(c.get("name", "")) or "").lower()
            if f == "address":
                return qn in (str(c.get("address", "")) or "").lower()
            if f == "contact":
                return qn in (str(c.get("contact", "")) or "").lower()
            if f == "description":
                return qn in (str(c.get("client_description", "")) or "").lower()
            return True

        clients = [c for c in clients if match(c)]

    clients.sort(key=lambda c: c.get("created_at") or datetime.min, reverse=(cf.get("order", "desc") == "desc"))

    return render_template(
        "management.html",
        mode="clients",
        active="clients",
        clients=clients,
        order=cf.get("order", "desc"),
        month=cf.get("month"),
        year=cf.get("year"),
        MONTHS=_month_pairs(),
        YEARS=available_years(SALES),
        q=cf.get("q", ""),
        field=cf.get("field", "code"),
    )


# --------------------------------------------------------------------------------------
# Deletes / Updates
# --------------------------------------------------------------------------------------
@app.post("/clients/delete/<code>")
def delete_client(code: str):
    if not re.fullmatch(r"\d{6}", code or ""):
        abort(400)
    global SALES
    before = len(SALES)
    SALES = [v for v in SALES if v.get("code") != code]
    if len(SALES) != before and not _save_sales():
        logger.error("[delete_client] could not write sales.json for code %s", code)
        return (_t("Failed to save."), 500)

    # também remove do clients.json (cadastro)
    db = [c for c in _load_clients_db() if c.get("code") != code]
    if not _save_clients_db(db):
        logger.error("[delete_client] could not write clients.json for code %s", code)
        return (_t("Failed to save."), 500)

    return ("", 200)



@app.post("/api/clients/delete_sales")
def api_clients_delete_sales():
    """
    Bulk delete (usado pelo management.html):
    remove todas as vendas associadas aos códigos informados e remove os clientes do cadastro.
    """
    try:
        payload = request.get_json(silent=True) or {}
        codes = payload.get("codes") or []
        if not isinstance(codes, list):
            return jsonify({"ok": False, "error": "Invalid codes"}), 400

        clean = []
        for c in codes:
            code = str(c or "").strip()
            if re.fullmatch(r"\d{6}", code or ""):
                clean.append(code)

        if not clean:
            return jsonify({"ok": False, "error": "No codes"}), 400

        sset = set(clean)

        # remove vendas (memória + disco)
        global SALES
        before = len(SALES)
        SALES = [v for v in SALES if str(v.get("code", "") or "").strip() not in sset]
        removed_sales = before - len(SALES)
        if removed_sales and not _save_sales():
            logger.error("[api_clients_delete_sales] could not write sales.json for %s", clean)
            return jsonify({"ok": False, "error": _t("Failed to save.")}), 500

        # remove do cadastro (clients.json)
        db = _load_clients_db()
        db = [c for c in db if str(c.get("code", "") or "").strip() not in sset]
        if not _save_clients_db(db):
            logger.error("[api_clients_delete_sales] could not write clients.json for %s", clean)
            return jsonify({"ok": False, "error": _t("Failed to save.")}), 500

        return jsonify({"ok": True, "removed_sales": int(removed_sales)})
    except Exception as e:
        app.logger.exception("api_clients_delete_sales failed")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.post("/sales/delete/<int:sid>")
def delete_sale(sid: int):
    for i, v in enumerate(SALES):
        if int(v.get("id", 0) or 0) == int(sid):
            removed = SALES.pop(i)
            if not _save_sales():
                SALES.insert(i, removed)
                logger.error("[delete_sale] could not write sales.json for id %s", sid)
                return (_t("Failed to save."), 500)
            return ("", 200)
    return ("", 404)

@app.post("/sales/update/<int:sid>")
def sales_update(sid: int):
    data = request.get_json(silent=True) or {}
    product = (data.get("product") or "").strip()
    desc = (data.get("sale_description") or data.get("description") or "").strip()

    # Accept empty strings (user may want to clear fields)
    amount_raw = data.get("amount")
    final_raw = data.get("final_amount")
    amount = _parse_money_any(amount_raw)
    final_amount = _parse_money_any(final_raw) if final_raw not in (None, "") else amount

    for v in SALES:
        if int(v.get("id", 0) or 0) == int(sid):
            v["product"] = product
            v["sale_description"] = desc
            v["amount"] = float(amount)
            v["final_amount"] = float(final_amount)
            ok = _save_sales()
            if not ok:
                return jsonify(ok=False, error=_t("Failed to save.")), 500
            return jsonify(ok=True, sale={
                "id": int(sid),
                "product": v.get("product", ""),
                "sale_description": v.get("sale_description", ""),
                "amount": float(v.get("amount", 0.0) or 0.0),
                "final_amount": float(v.get("final_amount", v.get("amount", 0.0)) or 0.0),
            })
    return jsonify(ok=False, error=_t("Sale not found.")), 404



@app.post("/clients/update/<code>")
def clients_update(code: str):
    code = (code or "").strip()
    if not re.fullmatch(r"\d{6}", code):
        return jsonify(ok=False, error=_t("Code must have 6 digits.")), 400

    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    addr = (data.get("address") or "").strip()
    cont = (data.get("contact") or "").strip()
    desc = (data.get("client_description") or data.get("description") or "").strip()

    if not any([name, addr, cont, desc]):
        return jsonify(ok=False, error=_t("Nothing to update.")), 400

    # atualiza no DB de clientes
    db = _load_clients_db()
    found_db = False
    for c in db:
        if c.get("code") == code:
            if name != "":
                c["name"] = name
            if addr != "":
                c["address"] = addr
            if cont != "":
                c["contact"] = cont
            if desc != "":
                c["client_description"] = desc
            found_db = True
            break
    ok_db = True
    if found_db:
        ok_db = _save_clients_db(db)

    # atualiza em todas as vendas
    found = False
    for v in SALES:
        if v.get("code") == code:
            if name != "":
                v["name"] = name
            if addr != "":
                v["address"] = addr
            if cont != "":
                v["contact"] = cont
            if desc != "":
                v["client_description"] = desc
            found = True

    if not found and not found_db:
        return jsonify(ok=False, error=_t("Client not found.")), 404

    ok_sales = True
    if found:
        ok_sales = _save_sales()

    if (not ok_db) or (not ok_sales):
        return jsonify(ok=False, error=_t("Failed to save.")), 500

    return jsonify(ok=True, client={
        "code": code,
        "name": name,
        "address": addr,
        "contact": cont,
        "client_description": desc,
    })

@app.post("/clients/export_csv")
def clients_export_csv():
    """
    Exporta clientes selecionados para CSV.
    - CSV (nao XLSX): formato padrao de mercado para exportacao de dados tabulares
      simples -- leve, texto puro, abre em qualquer planilha (Excel/Sheets/Numbers/
      LibreOffice) sem depender do formato binario proprietario do Excel.
    - Cabecalhos localizados conforme idioma selecionado em Settings (via _t()).
    """
    try:
        _bin_gc()

        codes: list[str] = []
        data = request.get_json(silent=True)
        if isinstance(data, dict) and isinstance(data.get("codes"), list):
            for v in data["codes"]:
                s = str(v or "").strip()
                if s:
                    codes.append(s)
        else:
            for v in request.form.getlist("codes") + request.values.getlist("codes"):
                s = str(v or "").strip()
                if s:
                    codes.append(s)

        if not codes:
            for v in request.form.getlist("ids") + request.values.getlist("ids"):
                s = str(v or "").strip()
                if s:
                    codes.append(s)

        codes = list(dict.fromkeys(codes))
        if not codes:
            return jsonify({"ok": False, "error": "No selection."}), 400

        all_clients = _load_clients_db()
        by_code = {str(c.get("code", "")).strip(): c for c in all_clients}

        picked: list[dict] = []
        for code in codes:
            c = by_code.get(str(code).strip())
            if c:
                picked.append(c)

        if not picked:
            return jsonify({"ok": False, "error": "No matching clients."}), 404

        import csv as _csv
        import io as _io

        # _safe_str / _as_dt: helpers de modulo (compartilhados com sales_export_csv)

        def _pick_desc(c: dict) -> str:
            for k in ("client_description", "description", "desc", "notes", "note", "obs", "observations"):
                v = c.get(k)
                if v not in (None, ""):
                    return _safe_str(v)
            return ""

        sio = _io.StringIO()
        w = _csv.writer(sio, lineterminator="\r\n")
        w.writerow([
            _t("Identification"),
            _t("Date/Hour"),
            _t("Name"),
            _t("Contact"),
            _t("Address"),
            _t("Description"),
            _t("Sales"),
        ])
        for c in picked:
            created = _as_dt(c.get("created_at"))
            w.writerow([
                _safe_str(c.get("code", "")),
                created.strftime("%Y-%m-%d %H:%M") if created else "",
                _safe_str(c.get("name", "")),
                _safe_str(c.get("contact", "")),
                _safe_str(c.get("address", "")),
                _pick_desc(c),
                int(c.get("sales_count", 0) or 0),
            ])

        # UTF-8 com BOM: Excel no Windows so detecta acentuacao corretamente com o BOM.
        b = sio.getvalue().encode("utf-8-sig")

        stamp = datetime.now().strftime("%Y-%m-%d_%Hh%M")
        # Nome sugerido: localiza o "basename" (se existir em languages.py) mantendo o mesmo padrão de data/estrutura
        _base_name = _t("clients_spreadsheet.csv")  # ex.: planilha_clientes.csv / planilla_clientes.csv
        _stem = _base_name[:-4] if _base_name.lower().endswith(".csv") else _base_name
        filename = f"{_stem}_{stamp}.csv"
        _saved_path, _opened = (None, False)

        token = _bin_put(b, filename, "text/csv; charset=utf-8", ttl=600)
        return jsonify({
            "ok": True,
            "filename": filename,
            "download_url": url_for("download_token", token=token, _external=True),
            "saved_path": _saved_path,
            "opened": bool(_opened),
        })
    except Exception as e:
        app.logger.exception("clients_export_csv failed")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.post("/sales/export_csv")
def sales_export_csv():
    """
    Exporta a selecao de vendas para CSV.
    - CSV (nao XLSX): formato padrao de mercado para exportacao de dados tabulares
      simples -- leve, texto puro, abre em qualquer planilha (Excel/Sheets/Numbers/
      LibreOffice) sem depender do formato binario proprietario do Excel.
    - Cabecalhos localizados conforme idioma selecionado em Settings (via _t()).
    """
    try:
        _bin_gc()

        ids: list[int] = []
        data = request.get_json(silent=True)
        if isinstance(data, dict) and isinstance(data.get("ids"), list):
            for v in data["ids"]:
                try:
                    ids.append(int(v))
                except Exception:
                    pass
        else:
            for v in request.form.getlist("ids") + request.values.getlist("ids"):
                try:
                    ids.append(int(v))
                except Exception:
                    pass
            vid = request.form.get("id") or request.values.get("id")
            if vid not in (None, ""):
                try:
                    ids.append(int(vid))
                except Exception:
                    pass

        ids = [int(x) for x in ids]
        ids = list(dict.fromkeys(ids))

        if not ids:
            return jsonify({"ok": False, "error": "no_ids"}), 400

        selected = [v for v in SALES if int(v.get("id", 0) or 0) in set(ids)]
        if not selected:
            return jsonify({"ok": False, "error": "not_found"}), 404

        selected.sort(key=lambda r: (r.get("created_at") or datetime.min))

        import csv as _csv
        import io as _io

        # _as_dt / _safe_str: helpers de modulo (compartilhados com clients_export_csv)

        sio = _io.StringIO()
        w = _csv.writer(sio, lineterminator="\r\n")
        w.writerow([
            _t("Identification"),
            _t("Date/Hour"),
            _t("Name"),
            _t("Product"),
            _t("Description"),
            _t("Value"),
            _t("Discount"),
            _t("Final Value"),
        ])

        for r in selected:
            created = _as_dt(r.get("created_at"))
            code = _safe_str(r.get("code", ""))
            name = _safe_str(r.get("name", ""))
            product = _safe_str(r.get("product", ""))
            desc = _safe_str(r.get("sale_description") or r.get("description") or "")

            try:
                amount = float(r.get("amount", 0) or 0.0)
            except Exception:
                amount = 0.0
            try:
                final_amount = float(r.get("final_amount") if r.get("final_amount") is not None else amount)
            except Exception:
                final_amount = amount

            discount = max(0.0, amount - final_amount)

            w.writerow([
                code,
                created.strftime("%Y-%m-%d %H:%M") if created else "",
                name,
                product,
                desc,
                f"{amount:.2f}",
                f"{discount:.2f}",
                f"{final_amount:.2f}",
            ])

        # UTF-8 com BOM: Excel no Windows so detecta acentuacao corretamente com o BOM.
        csv_bytes = sio.getvalue().encode("utf-8-sig")

        dts = [_as_dt(r.get("created_at")) for r in selected]
        dts = [d for d in dts if isinstance(d, datetime)]
        if dts:
            dmin, dmax = min(dts), max(dts)
            base = f"{dmin.strftime('%Y-%m-%d')}_{dmax.strftime('%Y-%m-%d')}" if dmin.date() != dmax.date() else dmin.strftime("%Y-%m-%d")
        else:
            base = datetime.now().strftime("%Y-%m-%d_%Hh%M")

        # Nome sugerido: localiza o "basename" (se existir em languages.py) mantendo o mesmo padrão de data/estrutura
        _base_name = _t("sales_spreadsheet.csv")  # ex.: planilha_vendas.csv / planilla_ventas.csv
        _stem = _base_name[:-4] if _base_name.lower().endswith(".csv") else _base_name
        filename = f"{_stem}_{base}.csv"
        _saved_path, _opened = (None, False)

        token = _bin_put(csv_bytes, filename, "text/csv; charset=utf-8", ttl=600)

        return jsonify({
            "ok": True,
            "token": token,
            "filename": filename,
            "download_url": url_for("download_token", token=token, _external=True),
            "saved_path": _saved_path,
            "opened": bool(_opened),
        })
    except Exception as e:
        app.logger.exception("sales_export_csv failed")
        return jsonify({"ok": False, "error": str(e)}), 500
# --------------------------------------------------------------------
# Receipts (PDF)
# --------------------------------------------------------------------
def _pdf_bytes(pdf: FPDF) -> bytes:
    """
    Compat fpdf/fpdf2:
    - fpdf2 mais novo pode retornar bytes/bytearray em dest="S"
    - fpdf antigo retorna str (latin-1)
    """
    out = pdf.output(dest="S")
    if isinstance(out, (bytes, bytearray)):
        return bytes(out)
    return str(out).encode("latin-1", errors="ignore")


def _slugify_short(s: str, max_len: int = 22) -> str:
    if not s:
        return ""
    t = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode("ascii")
    t = re.sub(r"[^A-Za-z0-9]+", "_", t).strip("_").lower()
    t = re.sub(r"_+", "_", t)
    return t[:max_len].strip("_")


def _find_sale_by_id(sid: str) -> dict | None:
    _reload_sales_from_disk()
    s_sid = str(sid or "").strip()
    if not s_sid:
        return None
    for it in (SALES or []):
        try:
            if str(it.get("code", "")).strip() == s_sid or str(it.get("id", "")).strip() == s_sid:
                return it
        except Exception:
            continue
    return None


def _receipt_filename_for_sale(sale: dict, sid: str) -> str:
    stamp = datetime.now().strftime("%Y-%m-%d_%Hh%M")
    raw_prefix = _t("server_receipt")
    if raw_prefix == "server_receipt":
        raw_prefix = _t("receipt")
    prefix = _slugify_short(raw_prefix, 16) or "receipt"

    # sugestão curta baseada em dados (quando fizer sentido)
    code = _slugify_short(str(sid or ""), 18)
    name = _slugify_short(str(sale.get("name") or sale.get("client_name") or ""), 22)

    parts = [prefix]
    if code:
        parts.append(code)
    if name and name not in parts:
        parts.append(name)

    base = "_".join([p for p in parts if p])[:48].rstrip("_")
    return f"{base}_{stamp}.pdf"


def _render_one_receipt_pdf(
    pdf: FPDF,
    sale: dict,
    sid: str,
    store: dict,
    img_dataurl: str | None = None,
    paper_w_mm: float = 58.0,
    paper_h_mm: float = 210.0,
) -> None:
    """Classic SSM receipt layout (58x210mm) rendered with fpdf2.

    IMPORTANT: This is the *legacy* layout (like 072... / 174... PDFs), not the HTML preview.
    """
    import tempfile
    import datetime as _dt

    W_MM = float(paper_w_mm or 58.0)
    H_MM = float(paper_h_mm or 210.0)
    MARGIN_L = 4.0
    MARGIN_R = 4.0
    CONTENT_W = max(10.0, W_MM - MARGIN_L - MARGIN_R)

    try:
        pdf.set_auto_page_break(auto=True, margin=8.0)
    except Exception:
        pass

    try:
        pdf.add_page(format=(W_MM, H_MM))
    except Exception:
        pdf.add_page()

    try:
        pdf.set_margins(MARGIN_L, 6.0, MARGIN_R)
    except Exception:
        pass

    # Base font
    try:
        pdf.set_font("Helvetica", "", 10)
    except Exception:
        pdf.set_font("Arial", "", 10)

    def tr(key: str) -> str:
        try:
            return _t(key)
        except Exception:
            return key

    def set_font(bold: bool = False, size: int = 10):
        try:
            pdf.set_font("Helvetica", "B" if bold else "", size)
        except Exception:
            pdf.set_font("Arial", "B" if bold else "", size)

    def write_center(txt: str, bold: bool = False, size: int = 10) -> None:
        set_font(bold, size)
        pdf.set_x(MARGIN_L)
        pdf.multi_cell(CONTENT_W, 4, str(txt or ""), align="C")

    def write_left(txt: str, bold: bool = False, size: int = 10) -> None:
        set_font(bold, size)
        pdf.set_x(MARGIN_L)
        pdf.multi_cell(CONTENT_W, 4, str(txt or ""), align="L")

    def dashed_sep() -> None:
        y = pdf.get_y() + 1.5
        x1 = MARGIN_L
        x2 = MARGIN_L + CONTENT_W
        try:
            if hasattr(pdf, "dashed_line"):
                pdf.dashed_line(x1, y, x2, y, 1, 1)  # type: ignore[attr-defined]
            else:
                pdf.line(x1, y, x2, y)
        except Exception:
            try:
                pdf.line(x1, y, x2, y)
            except Exception:
                pass
        pdf.ln(3.2)

    def parse_dt(raw) -> _dt.datetime | None:
        if raw is None:
            return None
        if isinstance(raw, _dt.datetime):
            return raw
        if isinstance(raw, (int, float)):
            try:
                return _dt.datetime.fromtimestamp(raw)
            except Exception:
                return None
        s = str(raw).strip()
        if not s:
            return None
        fmts = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%d",
            "%d/%m/%Y %H:%M:%S",
            "%d/%m/%Y %H:%M",
            "%d/%m/%Y",
        ]
        for fmt in fmts:
            try:
                return _dt.datetime.strptime(s, fmt)
            except Exception:
                pass
        # strip timezone
        try:
            s2 = s.replace("Z", "")
            if "+" in s2:
                s2 = s2.split("+", 1)[0].strip()
            if "-" in s2[10:]:
                s2 = s2.rsplit("-", 1)[0].strip()
            for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
                try:
                    return _dt.datetime.strptime(s2, fmt)
                except Exception:
                    pass
        except Exception:
            pass
        return None

    def locale_code() -> str:
        try:
            loc = str(get_locale() or "en").lower().replace("_", "-")
            return loc
        except Exception:
            return "en"

    def money(v) -> str:
        try:
            n = float(v)
        except Exception:
            n = 0.0
        s = f"{n:,.2f}"
        loc = locale_code()
        if loc.startswith(("pt", "es")):
            s = s.replace(",", "X").replace(".", ",").replace("X", ".")
            return f"R$ {s}"
        return f"$ {s}"

    # --- Image / Logo ---
    if not img_dataurl:
        # fallback from business settings (if any)
        try:
            img_dataurl = (_load_business().get("logo_dataurl") or "").strip() or None
        except Exception:
            img_dataurl = None

    if img_dataurl:
        try:
            img_bytes, ext = _dataurl_to_image_bytes(img_dataurl)
            if img_bytes:
                suf = ".png" if (ext or "").lower() not in ("jpg", "jpeg") else ".jpg"
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suf)
                tmp.write(img_bytes)
                tmp.close()

                x = MARGIN_L
                y = pdf.get_y()
                w = CONTENT_W
                try:
                    pdf.image(tmp.name, x=x, y=y, w=w)
                except Exception:
                    pass
                try:
                    os.unlink(tmp.name)
                except Exception:
                    pass

                # move down below image (approx)
                pdf.ln(62)
        except Exception:
            pass

    # --- Header (store) ---
    store_name = (store.get("name") or store.get("business_name") or "").strip()
    store_phone = (store.get("phone") or store.get("tel") or "").strip()
    addr1 = (store.get("addr1") or store.get("address") or store.get("addr") or "").strip()
    ziptax = (store.get("ziptax") or "").strip()

    if store_name:
        write_center(store_name, bold=True)
    if store_phone:
        write_center(store_phone)
    if addr1:
        write_center(addr1)
    if ziptax:
        write_center(ziptax)

    dashed_sep()

    # --- CLIENT block ---
    write_center(f"{tr('CLIENT')}:".rstrip(), bold=True)
    code = str(sale.get("code") or sale.get("client_code") or "").strip()
    name = str(sale.get("name") or sale.get("client_name") or "").strip()
    if code:
        write_center(f"{tr('CODE')}: {code}")
    if name:
        write_center(f"{tr('NAME')}: {name}")

    dashed_sep()

    # --- SALE block ---
    write_center(f"{tr('SALE')}:".rstrip(), bold=True)
    prod = str(sale.get("product") or sale.get("item") or "").strip()
    desc = str(sale.get("description") or "").strip()
    if prod:
        write_center(f"{tr('PRODUCT')}: {prod}")
    if desc:
        write_center(f"{tr('DESCRIPTION')}: {desc}")

    # price + discount
    price = sale.get("price")
    final_price = sale.get("final_price") or sale.get("final_amount") or sale.get("final")
    discount = sale.get("discount")

    try:
        p = float(price)
    except Exception:
        p = None
    try:
        fp = float(final_price) if final_price is not None and str(final_price).strip() != "" else None
    except Exception:
        fp = None
    try:
        disc = float(discount) if discount is not None and str(discount).strip() != "" else 0.0
    except Exception:
        disc = 0.0

    if p is not None:
        if fp is not None and fp != p and disc > 0:
            # original
            write_center(f"{tr('PRICE')}: {money(p)}")
            # strike line across last row
            try:
                y = pdf.get_y() - 2.0
                x1 = MARGIN_L + 10
                x2 = MARGIN_L + CONTENT_W - 10
                pdf.line(x1, y, x2, y)
            except Exception:
                pass
            write_center(money(fp), bold=True)
        else:
            write_center(f"{tr('PRICE')}: {money(p)}", bold=True)

    dashed_sep()

    # --- Date footer ---
    created_raw = sale.get("created_at") or sale.get("created") or sale.get("date") or ""
    dt = parse_dt(created_raw)
    if dt:
        loc = locale_code()
        if loc.startswith(("pt", "es")):
            ds = dt.strftime("%d/%m/%Y %H:%M")
        else:
            ds = dt.strftime("%Y-%m-%d %H:%M")
        write_center(f"{tr('Date')}: {ds}")




@app.post("/receipt/<sid>")
def receipt_single(sid: str):
    """
    Gera PDF de recibo para uma venda específica (usado pelo Sales -> Print receipt).
    Retorna JSON com token + download_url.
    """
    try:
        _bin_gc()

        sale = _find_sale_by_id(sid)
        if not sale:
            return jsonify({"ok": False, "error": "Sale not found."}), 404

        # dados da loja vindo do preview (FormData)
        store = {
            "name": (request.form.get("store_name") or "").strip(),
            "phone": (request.form.get("store_phone") or "").strip(),
            "addr1": (request.form.get("store_addr1") or "").strip(),
            "ziptax": (request.form.get("store_ziptax") or "").strip(),
        }
        img_dataurl = (request.form.get("img_dataurl") or "").strip() or None
        FPDF = _get_FPDF()
        pdf = FPDF()
        _render_one_receipt_pdf(pdf, sale, str(sid), store, img_dataurl=img_dataurl, paper_w_mm=58.0, paper_h_mm=210.0)

        pdf_bytes = _pdf_bytes(pdf)
        filename = _receipt_filename_for_sale(sale, str(sid))

        token = _bin_put(pdf_bytes, filename, "application/pdf", ttl=600)
        return jsonify({
            "ok": True,
            "token": token,
            "filename": filename,
            "download_url": url_for("download_token", token=token, _external=True),
        })
    except Exception as e:
        app.logger.exception("receipt_single failed")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.post("/receipt/batch")
def receipt_batch():
    try:
        _bin_gc()

        ids: list[str] = []
        data = request.get_json(silent=True)
        if isinstance(data, dict) and isinstance(data.get("ids"), list):
            ids = [str(x).strip() for x in data["ids"] if str(x).strip()]
        if not ids:
            ids = [str(x).strip() for x in request.form.getlist("ids") + request.values.getlist("ids") if str(x).strip()]

        ids = list(dict.fromkeys(ids))
        if not ids:
            return jsonify({"ok": False, "error": "No selection."}), 400

        store = {
            "name": (request.form.get("store_name") or "").strip(),
            "phone": (request.form.get("store_phone") or "").strip(),
            "addr1": (request.form.get("store_addr1") or "").strip(),
            "ziptax": (request.form.get("store_ziptax") or "").strip(),
        }
        img_dataurl = (request.form.get("img_dataurl") or "").strip() or None
        FPDF = _get_FPDF()
        # Mesmo construtor do /receipt/<sid>: cada pagina define seu proprio
        # formato (58x210) dentro de _render_one_receipt_pdf, entao nao ha
        # motivo para um tamanho de pagina default diferente aqui.
        pdf = FPDF()
        wrote_any = False
        for sid in ids:
            sale = _find_sale_by_id(sid)
            if sale:
                _render_one_receipt_pdf(pdf, sale, str(sid), store, img_dataurl=img_dataurl, paper_w_mm=58.0, paper_h_mm=210.0)
                wrote_any = True

        if not wrote_any:
            return jsonify({"ok": False, "error": "No matching sales."}), 404

        pdf_bytes = _pdf_bytes(pdf)

        stamp = datetime.now().strftime("%Y-%m-%d_%Hh%M")
        raw_prefix = _t("server_receipt")
        if raw_prefix == "server_receipt":
            raw_prefix = _t("receipt")
        prefix = _slugify_short(raw_prefix, 16) or "receipt"
        filename = f"{prefix}_batch_{stamp}.pdf"
        token = _bin_put(pdf_bytes, filename, "application/pdf", ttl=600)

        return jsonify({
            "ok": True,
            "token": token,
            "filename": filename,
            "download_url": url_for("download_token", token=token, _external=True),
        })
    except Exception as e:
        app.logger.exception("receipt_batch failed")
        return jsonify({"ok": False, "error": str(e)}), 500
