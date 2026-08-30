"""When a JSON write fails, the mutating endpoints must report it instead of
answering "ok". These tests force ``_save_json`` to fail and check the HTTP
status and that in-memory state is not left ahead of disk.
"""

from __future__ import annotations

import pytest

TOKEN = "pytest-token"  # set by tests/conftest.py as APP_SESSION_TOKEN


@pytest.fixture
def client(srv, data_dir):
    return srv.app.test_client()


@pytest.fixture
def failing_save(srv, monkeypatch):
    """Make every _save_json call fail (also covers _save_sales / _save_clients_db /
    _save_business, which all funnel through it)."""
    monkeypatch.setattr(srv, "_save_json", lambda *a, **k: False)


def _auth(**kw):
    kw.setdefault("headers", {})["X-SSM-Token"] = TOKEN
    return kw


def test_data_reset_reports_write_failure(srv, client, failing_save):
    r = client.post("/data/reset", **_auth())
    assert r.status_code == 500
    body = r.get_json()
    assert body["ok"] is False
    assert set(body["partial_write"]) == {"clients.json", "sales.json"}


def test_delete_sale_reports_write_failure_and_keeps_sale(srv, client, failing_save):
    srv.SALES.append({"id": 991, "code": "000991", "product": "x", "amount": 1.0,
                      "final_amount": 1.0, "created_at": srv.datetime.now()})
    before = len(srv.SALES)

    r = client.post("/sales/delete/991", **_auth())

    assert r.status_code == 500
    # rolled back: the sale is still in memory, matching what is on disk
    assert len(srv.SALES) == before
    assert any(s.get("id") == 991 for s in srv.SALES)


def test_delete_sale_succeeds_when_save_works(srv, client):
    srv.SALES.append({"id": 992, "code": "000992", "product": "y", "amount": 2.0,
                      "final_amount": 2.0, "created_at": srv.datetime.now()})

    r = client.post("/sales/delete/992", **_auth())

    assert r.status_code == 200
    assert not any(s.get("id") == 992 for s in srv.SALES)


def test_delete_client_reports_write_failure(srv, client, failing_save):
    srv.SALES.append({"id": 993, "code": "000993", "product": "z", "amount": 3.0,
                      "final_amount": 3.0, "created_at": srv.datetime.now()})

    r = client.post("/clients/delete/000993", **_auth())

    assert r.status_code == 500
