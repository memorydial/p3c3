from __future__ import annotations

import json

from fastapi.testclient import TestClient

from server import app


def test_debug_reports_config_without_secrets(monkeypatch):
    monkeypatch.setenv("PROXY_UPSTREAM_MODE", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    body = TestClient(app).get("/debug").json()
    assert body["ok"] is True
    assert body["upstream_mode"] == "openai"
    assert "upstream_base" in body
    assert body["openai_api_key_set"] is False
    # codex-only fields absent outside codex mode
    assert "codex_auth_present" not in body


def test_debug_codex_mode_reports_auth_presence_not_value(tmp_path, monkeypatch):
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"tokens": {"access_token": "tok_SECRET", "account_id": "acct_SECRET"}}))
    monkeypatch.setenv("PROXY_UPSTREAM_MODE", "codex")
    monkeypatch.setenv("CODEX_AUTH_PATH", str(auth))
    body = TestClient(app).get("/debug").json()
    assert body["upstream_mode"] == "codex"
    assert body["codex_auth_present"] is True
    assert body["codex_account_id_present"] is True
    # presence flags only - the actual token/account values must never appear
    blob = json.dumps(body)
    assert "tok_SECRET" not in blob
    assert "acct_SECRET" not in blob


def test_debug_codex_mode_flags_missing_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("PROXY_UPSTREAM_MODE", "codex")
    monkeypatch.setenv("CODEX_AUTH_PATH", str(tmp_path / "nonexistent.json"))
    body = TestClient(app).get("/debug").json()
    assert body["codex_auth_present"] is False


def test_health_still_minimal(monkeypatch):
    body = TestClient(app).get("/health").json()
    assert body == {"ok": True}
