from __future__ import annotations

import base64
import json

import httpx

from mapper import anthropic_request_to_responses
from models import (
    CODEX_ACCOUNT_ID_CLAIM,
    ProxySettings,
    build_upstream_headers,
    load_codex_credentials,
)
from schemas import AnthropicMessagesRequest


def _fake_jwt(claims: dict) -> str:
    """Build an unsigned JWT-shaped token (header.payload.sig) for claim-read tests."""
    def part(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")

    return f"{part({'alg': 'none'})}.{part(claims)}.sig"


def _settings(mode: str, api_key: str | None = None) -> ProxySettings:
    return ProxySettings(
        base_url="https://chatgpt.com/backend-api/codex",
        api_key=api_key,
        debug=False,
        host="127.0.0.1",
        port=4000,
        model_map={},
        timeout=httpx.Timeout(5.0),
        upstream_mode=mode,
    )


def test_codex_mode_omits_max_output_tokens():
    req = AnthropicMessagesRequest(
        model="gpt-5.5", max_tokens=4096, messages=[{"role": "user", "content": "hi"}]
    )
    payload = anthropic_request_to_responses(req, {}, codex_mode=True)
    assert "max_output_tokens" not in payload


def test_openai_mode_keeps_max_output_tokens():
    req = AnthropicMessagesRequest(
        model="qwen2.5", max_tokens=4096, messages=[{"role": "user", "content": "hi"}]
    )
    payload = anthropic_request_to_responses(req, {}, codex_mode=False)
    assert payload["max_output_tokens"] == 4096


def test_jwt_account_id_claim_extraction(tmp_path):
    token = _fake_jwt({CODEX_ACCOUNT_ID_CLAIM: "acct_test_123"})
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"providers": {"openai-codex": {"tokens": {"access_token": token}}}}))
    access, account = load_codex_credentials(str(auth))
    assert access == token
    assert account == "acct_test_123"


def test_missing_auth_file_returns_none(tmp_path):
    access, account = load_codex_credentials(str(tmp_path / "nope.json"))
    assert access is None and account is None


def test_codex_cli_schema_plain_account_id(tmp_path):
    # OpenAI codex CLI: top-level tokens with a plain account_id (no JWT decode).
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({
        "auth_mode": "chatgpt",
        "tokens": {"access_token": "tok_abc", "account_id": "acct_plain", "refresh_token": "r"},
    }))
    access, account = load_codex_credentials(str(auth))
    assert access == "tok_abc"
    assert account == "acct_plain"


def test_codex_headers_include_codex_cli_identity(tmp_path, monkeypatch):
    token = _fake_jwt({CODEX_ACCOUNT_ID_CLAIM: "acct_abc"})
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"providers": {"openai-codex": {"tokens": {"access_token": token}}}}))
    monkeypatch.setenv("CODEX_AUTH_PATH", str(auth))
    headers = build_upstream_headers(_settings("codex"))
    assert headers["originator"] == "codex_cli_rs"
    assert headers["User-Agent"].startswith("codex_cli_rs/")
    assert headers["ChatGPT-Account-ID"] == "acct_abc"
    assert headers["authorization"] == f"Bearer {token}"


def test_openai_mode_headers_have_no_codex_fields():
    headers = build_upstream_headers(_settings("openai", api_key="sk-x"))
    assert "originator" not in headers
    assert headers["authorization"] == "Bearer sk-x"
