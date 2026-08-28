"""Shared pytest setup for the Python test-suite.

`server` runs real work at import time: it creates ``CONFIG_DIR``
(``~/.SimpleSalesManagement`` by default) and picks a ``DATA_DIR``, and its
picker prefers any directory that already holds ``settings.json`` — which on a
developer machine is the *real* business data under ``~/Documents``.

To keep the tests hermetic we point every one of those at a throwaway
directory **before** importing ``server`` (this file is imported by pytest
before it collects any test module):

* ``SSM_CONFIG_DIR``     -> <tmp>/config   (honoured by server.CONFIG_DIR)
* ``APP_DATA_DIR``       -> <tmp>/data     (honoured by _choose_initial_data_dir)
* ``APP_SESSION_TOKEN``  -> a fixed value so the auth check is deterministic

We also drop an empty ``settings.json`` into <tmp>/data so the data-dir picker
selects that env directory (branch 1: "any candidate that already has data")
instead of walking off to Documents/AppData.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

_TMP_ROOT = Path(tempfile.mkdtemp(prefix="ssm_pytest_"))
_CFG_DIR = _TMP_ROOT / "config"
_DATA_DIR = _TMP_ROOT / "data"
_CFG_DIR.mkdir(parents=True, exist_ok=True)
_DATA_DIR.mkdir(parents=True, exist_ok=True)
(_DATA_DIR / "settings.json").write_text("{}", encoding="utf-8")

os.environ["SSM_CONFIG_DIR"] = str(_CFG_DIR)
os.environ["APP_DATA_DIR"] = str(_DATA_DIR)
os.environ.setdefault("APP_SESSION_TOKEN", "pytest-token")

import server  # noqa: E402  (import after the env is in place, on purpose)


@pytest.fixture
def srv():
    """The imported ``server`` module."""
    return server


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Repoint ``server``'s per-file path globals at a fresh directory.

    ``server`` captures ``SETTINGS_FILE``/``SALES_FILE``/... as module globals
    at import time; ``_set_data_dir`` rewrites all of them together. Each test
    that touches the filesystem gets its own empty directory this way.
    """
    d = tmp_path / "data"
    d.mkdir()
    server._set_data_dir(d)
    return d
