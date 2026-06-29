from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path

import httpx

CODEX_ACCOUNT_ID_CLAIM = "https://api.openai.com/auth.chatgpt_account_id"


def _decode_jwt_claim(token: str, claim: str) -> str | None:
    """Read one claim from a JWT payload without verifying the signature."""
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception:
        return None
    value = payload.get(claim)
    return value if isinstance(value, str) else None


def load_codex_credentials(auth_path: str | None = None) -> tuple[str | None, str | None]:
    """Return (access_token, chatgpt_account_id) from the codex auth store.

    Default source is the OpenAI `codex` CLI: ~/.codex/auth.json, schema
    `tokens.{access_token, account_id, refresh_token}` (account_id is a plain field).
    Falls back to a legacy nested schema (providers.openai-codex.tokens, account_id
    derived from the JWT claim). Override the path with CODEX_AUTH_PATH.
    Returns (None, None) if unavailable. Never logs the token value.
    """
    path = Path(auth_path or os.getenv("CODEX_AUTH_PATH", str(Path.home() / ".codex" / "auth.json")))
    try:
        data = json.loads(path.read_text())
    except Exception:
        return None, None
    # codex CLI schema: top-level "tokens" with a plain account_id.
    tokens = data.get("tokens") if isinstance(data.get("tokens"), dict) else None
    # legacy nested schema: providers.openai-codex.tokens
    if not tokens:
        tokens = (data.get("providers", {}).get("openai-codex", {}) or {}).get("tokens", {}) or {}
    access_token = tokens.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        return None, None
    account_id = tokens.get("account_id")
    if not isinstance(account_id, str) or not account_id:
        account_id = _decode_jwt_claim(access_token, CODEX_ACCOUNT_ID_CLAIM)
    return access_token, account_id


def _env_float(name: str) -> float | None:
    """Read a float env var; return None when unset or unparseable (never crash boot)."""
    raw = os.getenv(name)
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _env_choice(name: str, allowed: set[str]) -> str | None:
    """Read a lower-cased enum env var, accepting only known values; else None."""
    raw = os.getenv(name)
    if not raw:
        return None
    value = raw.strip().lower()
    return value if value in allowed else None


def load_model_map(raw: str | None = None) -> dict[str, str]:
    payload = os.getenv("PROXY_MODEL_MAP", "") if raw is None else raw
    if not payload:
        return {}
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(key): str(value) for key, value in parsed.items()}


def resolve_model(model_name: str, model_map: dict[str, str] | None = None) -> str:
    resolved_map = load_model_map() if model_map is None else model_map
    return resolved_map.get(model_name, model_name)


@dataclass(frozen=True)
class ProxySettings:
    base_url: str
    api_key: str | None
    debug: bool
    host: str
    port: int
    model_map: dict[str, str]
    timeout: httpx.Timeout
    upstream_mode: str = "openai"
    # Generation tuning (env-driven). When set, the proxy injects these into the
    # upstream Responses payload — the only place to steer a codex/GPT-5 backend,
    # since Claude Code's Anthropic request carries no reasoning/verbosity field.
    temperature: float | None = None
    reasoning_effort: str | None = None
    reasoning_summary: str | None = None
    verbosity: str | None = None

    @property
    def codex_mode(self) -> bool:
        return self.upstream_mode == "codex"

    @classmethod
    def from_env(cls) -> "ProxySettings":
        mode = os.getenv("PROXY_UPSTREAM_MODE", "openai")
        default_base = (
            "https://chatgpt.com/backend-api/codex"
            if mode == "codex"
            else "http://localhost:11434/v1"
        )
        return cls(
            base_url=os.getenv("OPENAI_BASE_URL", default_base).rstrip("/"),
            api_key=os.getenv("OPENAI_API_KEY") or None,
            debug=os.getenv("PROXY_DEBUG", "0") == "1",
            host=os.getenv("PROXY_HOST", "127.0.0.1"),
            port=int(os.getenv("PROXY_PORT", "4000")),
            model_map=load_model_map(),
            timeout=httpx.Timeout(
                connect=5.0,
                read=float(os.getenv("PROXY_READ_TIMEOUT", "300")),
                write=10.0,
                pool=5.0,
            ),
            upstream_mode=mode,
            temperature=_env_float("PROXY_TEMPERATURE"),
            reasoning_effort=_env_choice(
                "PROXY_REASONING_EFFORT", {"minimal", "low", "medium", "high"}
            ),
            reasoning_summary=_env_choice(
                "PROXY_REASONING_SUMMARY", {"auto", "concise", "detailed", "none"}
            ),
            verbosity=_env_choice("PROXY_VERBOSITY", {"low", "medium", "high"}),
        )


def build_upstream_headers(settings: ProxySettings) -> dict[str, str]:
    headers = {"content-type": "application/json"}
    if settings.codex_mode:
        # ChatGPT codex backend: the same identity headers the official codex CLI sends + JWT-derived account id.
        # Token comes from the codex auth store, not OPENAI_API_KEY.
        access_token, account_id = load_codex_credentials()
        token = settings.api_key or access_token
        if token:
            headers["authorization"] = f"Bearer {token}"
        headers["originator"] = "codex_cli_rs"
        headers["User-Agent"] = "codex_cli_rs/0.0.0 (p3c3)"
        if account_id:
            headers["ChatGPT-Account-ID"] = account_id
        return headers
    if settings.api_key:
        headers["authorization"] = f"Bearer {settings.api_key}"
    return headers
