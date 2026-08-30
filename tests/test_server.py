"""Unit tests for the pure / near-pure helpers in ``server.py``.

Scope on purpose kept to functions that can be exercised without a running
Flask request: money parsing, the signed-bundle verifier, the atomic JSON
writer, and the receipt-filename builders.
"""

from __future__ import annotations

import base64
import json
import re

import pytest


# --------------------------------------------------------------------------------------
# _parse_money_any
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "value, expected",
    [
        # Brazilian formatting: "." thousands, "," decimals.
        ("1.234,56", 1234.56),
        ("R$ 1.234,56", 1234.56),
        ("1.234", 1234.0),          # bare "1.234" reads as thousands, not 1.234
        ("0,50", 0.50),
        ("-1.234,56", -1234.56),
        # US / international formatting: "," thousands, "." decimals.
        ("1,234.56", 1234.56),
        ("1,234", 1234.0),
        ("1234.56", 1234.56),
        # Already numeric.
        (12.5, 12.5),
        (0, 0.0),
        (-50, -50.0),
        # Negatives as text.
        ("-50", -50.0),
        # Junk / empty -> 0.0 (never raises).
        ("", 0.0),
        ("   ", 0.0),
        ("abc", 0.0),
        ("R$ ...", 0.0),
        (None, 0.0),
    ],
)
def test_parse_money_any(srv, value, expected):
    assert srv._parse_money_any(value) == pytest.approx(expected)


def test_parse_money_any_never_raises(srv):
    for weird in ([], {}, object(), "1.2.3.4", "--,,.."):
        # Should return a float, not blow up.
        assert isinstance(srv._parse_money_any(weird), float)


# --------------------------------------------------------------------------------------
# _verify_bundle  (format + HMAC signature)
# --------------------------------------------------------------------------------------

def _make_bundle(srv, **payload_extra):
    payload = {
        "format": "SSM_DATA_V1",
        "ssm_bundle": True,
        "clients": [{"code": "000001", "name": "Alice"}],
        "sales": [],
    }
    payload.update(payload_extra)
    bundle = dict(payload)
    bundle["sig"] = srv._sign_bundle_payload(payload)
    return bundle


def test_verify_bundle_accepts_a_freshly_signed_bundle(srv):
    assert srv._verify_bundle(_make_bundle(srv)) == (True, "ok")


def test_verify_bundle_rejects_tampered_payload(srv):
    bundle = _make_bundle(srv)
    # Flip a value after the signature was computed.
    bundle["clients"] = [{"code": "999999", "name": "Mallory"}]
    ok, reason = srv._verify_bundle(bundle)
    assert ok is False
    assert reason == "bad_mac"


def test_verify_bundle_rejects_tampered_mac(srv):
    bundle = _make_bundle(srv)
    bundle["sig"] = dict(bundle["sig"], mac="AAAA" + bundle["sig"]["mac"][4:])
    assert srv._verify_bundle(bundle) == (False, "bad_mac")


def test_verify_bundle_rejects_wrong_kid(srv):
    bundle = _make_bundle(srv)
    bundle["sig"] = dict(bundle["sig"], kid="deadbeef00")
    assert srv._verify_bundle(bundle) == (False, "wrong_key")


@pytest.mark.parametrize(
    "mutate, expected_reason",
    [
        (lambda b: b.pop("format"), "invalid_format"),
        (lambda b: b.update(format="SSM_DATA_V2"), "invalid_format"),
        (lambda b: b.update(ssm_bundle=False), "invalid_format"),
        (lambda b: b.pop("ssm_bundle"), "invalid_format"),
        (lambda b: b.pop("sig"), "missing_sig"),
        (lambda b: b.update(sig="not-a-dict"), "missing_sig"),
        (lambda b: b.update(sig={"kid": "x"}), "bad_sig"),          # no mac
        (lambda b: b.update(sig={"mac": "x"}), "bad_sig"),          # no kid
    ],
)
def test_verify_bundle_rejects_malformed(srv, mutate, expected_reason):
    bundle = _make_bundle(srv)
    mutate(bundle)
    ok, reason = srv._verify_bundle(bundle)
    assert ok is False
    assert reason == expected_reason


@pytest.mark.parametrize("not_a_dict", ["nope", 123, None, ["a"]])
def test_verify_bundle_rejects_non_dict(srv, not_a_dict):
    assert srv._verify_bundle(not_a_dict) == (False, "invalid_json")


# --------------------------------------------------------------------------------------
# _get_export_key  (DPAPI-protected at rest on Windows; migrates the legacy
# plaintext file in place, keeping the key VALUE so old bundles still verify)
# --------------------------------------------------------------------------------------

def test_export_key_is_protected_at_rest_and_stable(srv, tmp_path, monkeypatch):
    monkeypatch.setattr(srv, "_EXPORT_KEY_FILE", tmp_path / "export_key.json")

    key = srv._get_export_key()
    assert len(key) == 32

    raw = json.loads(srv._EXPORT_KEY_FILE.read_text("utf-8"))
    if srv._dpapi_available():
        assert "protected_b64" in raw and "key_b64" not in raw
        # the file on disk must not contain the raw key bytes
        assert base64.b64encode(key).decode("ascii") not in raw["protected_b64"]
    else:
        assert "key_b64" in raw  # non-Windows fallback

    assert srv._get_export_key() == key  # stable across calls


def test_export_key_migrates_legacy_plaintext_file(srv, tmp_path, monkeypatch):
    monkeypatch.setattr(srv, "_EXPORT_KEY_FILE", tmp_path / "export_key.json")
    legacy_key = srv.secrets.token_bytes(32)
    srv._EXPORT_KEY_FILE.write_text(
        json.dumps({"key_b64": base64.b64encode(legacy_key).decode("ascii")}), "utf-8"
    )

    got = srv._get_export_key()
    assert got == legacy_key  # same key value -> bundles signed before still verify

    if srv._dpapi_available():
        raw = json.loads(srv._EXPORT_KEY_FILE.read_text("utf-8"))
        assert "protected_b64" in raw and "key_b64" not in raw
        assert srv._get_export_key() == legacy_key  # still stable post-migration


# --------------------------------------------------------------------------------------
# _save_json  (atomic write via <name>.tmp + os.replace)
# --------------------------------------------------------------------------------------

def test_save_json_writes_and_leaves_no_tmp(srv, data_dir):
    target = data_dir / "sales.json"
    tmp = data_dir / "sales.json.tmp"

    obj = {"sales": [{"id": 1, "amount": 10.5}], "acentuação": " oké"}
    assert srv._save_json(target, obj) is True

    assert target.exists()
    assert json.loads(target.read_text("utf-8")) == obj
    assert not tmp.exists()


def test_save_json_overwrite_is_not_truncating(srv, data_dir):
    target = data_dir / "sales.json"
    tmp = data_dir / "sales.json.tmp"

    big = {"rows": list(range(5000))}
    small = {"rows": [1]}
    assert srv._save_json(target, big) is True
    assert srv._save_json(target, small) is True

    # The file is exactly the small object — not the small object followed by
    # leftover bytes from the big one, and not a half-written file.
    text = target.read_text("utf-8")
    assert json.loads(text) == small
    assert not tmp.exists()


def test_save_json_failure_returns_false_and_keeps_original(srv, data_dir):
    target = data_dir / "sales.json"
    target.write_text('["original"]', encoding="utf-8")
    tmp = data_dir / "sales.json.tmp"

    # A set is not JSON-serialisable: json.dumps raises inside _save_json,
    # so it must report failure without touching the existing file.
    assert srv._save_json(target, {"bad": {1, 2, 3}}) is False
    assert json.loads(target.read_text("utf-8")) == ["original"]
    assert not tmp.exists()


# --------------------------------------------------------------------------------------
# _slugify_short / _receipt_filename_for_sale
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw, max_len, expected",
    [
        ("", 22, ""),
        ("Ção Ãcçéntéd Nõme", 22, "cao_accented_nome"),
        ("  many   spaces  ", 22, "many_spaces"),
        ("weird!!!___chars###", 40, "weird_chars"),
        ("--- ___ ---", 10, ""),
        ("abcdefghij", 5, "abcde"),
        ("abcde_fghij", 6, "abcde"),          # trailing "_" trimmed after cut
        ("Recibo", 16, "recibo"),
    ],
)
def test_slugify_short(srv, raw, max_len, expected):
    assert srv._slugify_short(raw, max_len) == expected


_RECEIPT_RE = re.compile(r"^[a-z0-9_]+_\d{4}-\d{2}-\d{2}_\d{2}h\d{2}\.pdf$")


def test_receipt_filename_shape_and_parts(srv):
    with srv.app.test_request_context("/"):
        name = srv._receipt_filename_for_sale({"name": "João da Silva"}, "ABC-123")

    assert _RECEIPT_RE.match(name), name
    assert "abc_123" in name          # slug of the sale id
    assert "joao_da_silva" in name    # slug of the client name


def test_receipt_filename_without_name_is_still_valid(srv):
    with srv.app.test_request_context("/"):
        name = srv._receipt_filename_for_sale({}, "42")

    assert _RECEIPT_RE.match(name), name
    assert "_42_" in name
