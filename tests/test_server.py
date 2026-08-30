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


# --------------------------------------------------------------------------------------
# Canonical JSON + HMAC primitives (used to sign the export bundle)
# --------------------------------------------------------------------------------------

def test_canon_json_bytes_is_order_independent_and_compact(srv):
    a = srv._canon_json_bytes({"b": 1, "a": [3, 2], "c": {"y": 1, "x": 2}})
    b = srv._canon_json_bytes({"c": {"x": 2, "y": 1}, "a": [3, 2], "b": 1})
    assert a == b
    assert b" " not in a and b"\n" not in a          # compact separators
    assert a == b'{"a":[3,2],"b":1,"c":{"x":2,"y":1}}'


def test_hmac_b64url_is_stable_urlsafe_and_unpadded(srv):
    key = b"k" * 32
    mac1 = srv._hmac_b64url(key, b"payload")
    mac2 = srv._hmac_b64url(key, b"payload")
    assert mac1 == mac2
    assert "=" not in mac1 and "+" not in mac1 and "/" not in mac1
    assert srv._hmac_b64url(key, b"payload x") != mac1
    assert srv._hmac_b64url(b"j" * 32, b"payload") != mac1


def test_kid_from_key_is_10_hex_chars_and_key_specific(srv):
    k1 = srv._kid_from_key(b"a" * 32)
    assert len(k1) == 10 and all(c in "0123456789abcdef" for c in k1)
    assert srv._kid_from_key(b"a" * 32) == k1
    assert srv._kid_from_key(b"b" * 32) != k1


# --------------------------------------------------------------------------------------
# _coerce_created_at / _normalize_sale_dict
# --------------------------------------------------------------------------------------

def test_coerce_created_at_parses_known_shapes(srv):
    dt = srv.datetime
    assert srv._coerce_created_at("2024-03-05T10:00:00") == dt(2024, 3, 5, 10, 0, 0)
    assert srv._coerce_created_at("2024-03-05") == dt(2024, 3, 5, 0, 0, 0)
    assert srv._coerce_created_at(dt(2020, 1, 1)) == dt(2020, 1, 1)
    # unix seconds
    assert srv._coerce_created_at(1_709_631_600).year == 2024


@pytest.mark.parametrize("junk", ["", None, "not a date", "2024-13-99"])
def test_coerce_created_at_falls_back_to_now(srv, junk):
    before = srv.datetime.now()
    got = srv._coerce_created_at(junk)
    after = srv.datetime.now()
    assert isinstance(got, srv.datetime)
    assert before <= got <= after


def test_normalize_sale_dict_fills_defaults_and_parses_money(srv):
    d = srv._normalize_sale_dict({"code": "12", "value": "1.234,56",
                                  "final_amount": "1.000,00", "id": "7"})
    assert d["amount"] == pytest.approx(1234.56)
    assert d["final_amount"] == pytest.approx(1000.0)
    assert d["id"] == 7
    for k in ("name", "address", "contact", "client_description",
              "product", "sale_description"):
        assert d[k] == ""
    assert isinstance(d["created_at"], srv.datetime)


def test_normalize_sale_dict_defaults_final_to_amount(srv):
    d = srv._normalize_sale_dict({"amount": "50"})
    assert d["final_amount"] == pytest.approx(50.0)


def test_normalize_sale_dict_accepts_legacy_aliases(srv):
    d = srv._normalize_sale_dict({"value": "10", "final_value": "8",
                                  "description": "legacy note"})
    assert d["amount"] == pytest.approx(10.0)
    assert d["final_amount"] == pytest.approx(8.0)
    assert d["sale_description"] == "legacy note"


# --------------------------------------------------------------------------------------
# _apply_sales_filters
# --------------------------------------------------------------------------------------

def _sale(srv, code, product, desc, when):
    return {"code": code, "product": product, "sale_description": desc, "created_at": when}


def test_apply_sales_filters_by_period_and_query(srv):
    dt = srv.datetime
    rows = [
        _sale(srv, "000001", "Lens", "left eye", dt(2024, 1, 10)),
        _sale(srv, "000002", "Frame", "titanium", dt(2024, 3, 4)),
        _sale(srv, "000099", "Lens cloth", "spare", dt(2023, 3, 4)),
        {"code": "x", "product": "no date"},  # skipped: created_at not a datetime
    ]

    only_2024 = srv._apply_sales_filters(rows, {"year": 2024})
    assert {r["code"] for r in only_2024} == {"000001", "000002"}

    march = srv._apply_sales_filters(rows, {"month": 3})
    assert {r["code"] for r in march} == {"000002", "000099"}

    by_product = srv._apply_sales_filters(rows, {"q": "lens", "field": "product"})
    assert {r["code"] for r in by_product} == {"000001", "000099"}

    by_code = srv._apply_sales_filters(rows, {"q": "0001", "field": "code"})
    assert [r["code"] for r in by_code] == ["000001"]


def test_apply_sales_filters_sort_order(srv):
    dt = srv.datetime
    rows = [
        _sale(srv, "a", "p", "d", dt(2024, 1, 1)),
        _sale(srv, "b", "p", "d", dt(2024, 6, 1)),
        _sale(srv, "c", "p", "d", dt(2024, 3, 1)),
    ]
    asc = [r["code"] for r in srv._apply_sales_filters(rows, {"order": "asc"})]
    desc = [r["code"] for r in srv._apply_sales_filters(rows, {"order": "desc"})]
    assert asc == ["a", "c", "b"]
    assert desc == ["b", "c", "a"]


# --------------------------------------------------------------------------------------
# available_years / _month_pairs / generate_unique_code
# --------------------------------------------------------------------------------------

def test_available_years_is_sorted_and_unique(srv):
    dt = srv.datetime
    rows = [
        {"created_at": dt(2021, 5, 1)},
        {"created_at": dt(2023, 1, 1)},
        {"created_at": dt(2021, 12, 31)},
        {"no": "date"},
    ]
    assert srv.available_years(rows) == [2021, 2023]


def test_month_pairs_has_twelve_entries(srv):
    mp = srv._month_pairs()
    assert len(mp) == 12
    assert [n for n, _label in mp] == list(range(1, 13))


def test_generate_unique_code_is_six_digits_and_avoids_existing(srv, data_dir, monkeypatch):
    taken = {f"{i:06d}" for i in range(1000)}
    monkeypatch.setattr(srv, "clients_map_latest", lambda: {c: {} for c in taken})
    for _ in range(50):
        c = srv.generate_unique_code()
        assert len(c) == 6 and c.isdigit()
        assert c not in taken


# --------------------------------------------------------------------------------------
# _sanitize_text_pdf
# --------------------------------------------------------------------------------------

def test_sanitize_text_pdf_maps_smart_punctuation_to_ascii(srv):
    got = srv._sanitize_text_pdf("“quote” – — …  end")
    assert got == '"quote" - - ...  end'


def test_sanitize_text_pdf_normalises_whitespace_and_controls(srv):
    assert srv._sanitize_text_pdf("a\r\nb") == "a\nb"          # CRLF -> LF
    assert srv._sanitize_text_pdf("a" + chr(9) + "b") == "ab"  # tab dropped
    assert srv._sanitize_text_pdf("a" + chr(7) + "b") == "ab"  # bell dropped
    assert srv._sanitize_text_pdf("keep\nnewline") == "keep\nnewline"


def test_sanitize_text_pdf_keeps_latin1_drops_the_rest(srv):
    assert srv._sanitize_text_pdf("café") == "café"        # é survives
    assert srv._sanitize_text_pdf("日本") == "??"            # non-latin-1


def test_sanitize_text_pdf_handles_none_and_numbers(srv):
    assert srv._sanitize_text_pdf(None) == ""
    assert srv._sanitize_text_pdf(1234) == "1234"
