"""Route-level tests through Flask's test client: the session-token gate,
new-sale validation, and an export -> import round trip.
"""

from __future__ import annotations

import io
import json

import pytest

TOKEN = "pytest-token"  # tests/conftest.py sets APP_SESSION_TOKEN to this


@pytest.fixture
def client(srv, data_dir):
    return srv.app.test_client()


def _auth(**kw):
    kw.setdefault("headers", {})["X-SSM-Token"] = TOKEN
    return kw


# --------------------------------------------------------------------------------------
# _require_session_token
# --------------------------------------------------------------------------------------

def test_request_without_token_is_forbidden(client):
    r = client.get("/sales")
    assert r.status_code == 403


def test_request_with_header_token_is_allowed(client):
    r = client.get("/sales", **_auth())
    assert r.status_code == 200


def test_query_param_token_sets_the_auth_cookie(client):
    r = client.get("/sales?k=" + TOKEN)
    assert r.status_code == 200
    set_cookie = r.headers.get("Set-Cookie", "")
    assert "ssm_auth=" in set_cookie
    assert "HttpOnly" in set_cookie
    # cookie alone now authenticates
    r2 = client.get("/sales")
    assert r2.status_code == 200


def test_wrong_token_is_forbidden(client):
    r = client.get("/sales", headers={"X-SSM-Token": "not-the-token"})
    assert r.status_code == 403


# --------------------------------------------------------------------------------------
# POST /sales  — new sale validation
# --------------------------------------------------------------------------------------

def _new_sale_form(**over):
    form = {
        "name": "Ana",
        "product": "Lens",
        "value": "100,00",
        "no_discount": "on",
    }
    form.update(over)
    return form


def test_new_sale_is_persisted(client, srv):
    srv.SALES.clear()
    r = client.post("/sale", data=_new_sale_form(), **_auth())
    assert r.status_code in (302, 303)
    assert r.headers["Location"].endswith("/sales")   # success -> the list
    assert len(srv.SALES) == 1
    assert srv.SALES[-1]["product"] == "Lens"


def test_new_sale_rejects_final_greater_than_amount(client, srv):
    srv.SALES.clear()
    r = client.post("/sale", data=_new_sale_form(
        no_discount="", final_value="150,00"), **_auth())
    # bounced back to the form (/sale), nothing stored
    assert r.status_code in (302, 303)
    assert "/sale" in r.headers["Location"] and "/sales" not in r.headers["Location"]
    assert srv.SALES == []


def test_new_sale_rejects_negative_amount(client, srv):
    srv.SALES.clear()
    r = client.post("/sale", data=_new_sale_form(value="-5"), **_auth())
    assert r.status_code in (302, 303)
    assert srv.SALES == []


# --------------------------------------------------------------------------------------
# /data/export  ->  /download/<token>  ->  /data/import
# --------------------------------------------------------------------------------------

def test_export_download_import_round_trip(client, srv):
    # seed one client + one sale on disk
    srv._save_clients_db([{"code": "000001", "name": "Ana", "address": "",
                           "contact": "", "client_description": ""}])
    srv._save_json(srv.SALES_FILE, [{
        "id": 1, "code": "000001", "name": "Ana", "product": "Frame",
        "amount": 200.0, "final_amount": 200.0, "no_discount": True,
        "created_at": "2024-05-01T09:00:00",
    }])
    srv._reload_sales_from_disk()

    # export -> token
    r = client.get("/data/export", **_auth())
    assert r.status_code == 200
    tok = r.get_json()["download_url"].rsplit("/", 1)[-1]

    # download the bundle bytes
    d = client.get("/download/" + tok, **_auth())
    assert d.status_code == 200
    bundle = json.loads(d.data)
    assert bundle["ssm_bundle"] is True and bundle["format"] == "SSM_DATA_V1"
    assert srv._verify_bundle(bundle) == (True, "ok")

    # wipe, then import the same bundle back
    srv._save_clients_db([])
    srv._save_json(srv.SALES_FILE, [])
    srv._reload_sales_from_disk()
    assert srv._load_clients_db() == []

    imp = client.post(
        "/data/import",
        data={"file": (io.BytesIO(d.data), "backup.json")},
        content_type="multipart/form-data",
        **_auth(),
    )
    body = imp.get_json()
    assert imp.status_code == 200 and body["ok"] is True

    codes = {c["code"] for c in srv._load_clients_db()}
    assert "000001" in codes
    sales, _n = srv._load_sales()
    assert any(s.get("product") == "Frame" for s in sales)


def test_import_flags_a_tampered_bundle_as_unverified(client, srv):
    # NOTE: /data/import currently *reports* a bad signature (verified=False,
    # verify_reason="bad_mac") but still imports the data. This test pins that
    # behaviour; if import is ever made to reject on a bad HMAC, update it.
    r = client.get("/data/export", **_auth())
    tok = r.get_json()["download_url"].rsplit("/", 1)[-1]
    raw = client.get("/download/" + tok, **_auth()).data
    bundle = json.loads(raw)
    bundle["data"]["clients"] = [{"code": "999999", "name": "Mallory"}]  # after signing

    imp = client.post(
        "/data/import",
        data={"file": (io.BytesIO(json.dumps(bundle).encode()), "b.json")},
        content_type="multipart/form-data",
        **_auth(),
    )
    body = imp.get_json()
    assert body["ok"] is True
    assert body["verified"] is False
    assert body["verify_reason"] == "bad_mac"


def test_import_reports_a_well_formed_bundle_as_verified(client, srv):
    srv._save_clients_db([{"code": "000042", "name": "Bob", "address": "",
                           "contact": "", "client_description": ""}])
    srv._save_json(srv.SALES_FILE, [])
    srv._reload_sales_from_disk()
    r = client.get("/data/export", **_auth())
    tok = r.get_json()["download_url"].rsplit("/", 1)[-1]
    raw = client.get("/download/" + tok, **_auth()).data

    imp = client.post(
        "/data/import",
        data={"file": (io.BytesIO(raw), "b.json")},
        content_type="multipart/form-data",
        **_auth(),
    )
    body = imp.get_json()
    assert body["ok"] is True and body["verified"] is True
    assert body["verify_reason"] == "ok"
