from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from errors import ProxyError, proxy_error_from_httpx, proxy_error_from_upstream, proxy_error_response
from mapper import anthropic_request_to_responses, responses_to_anthropic_message
from models import ProxySettings, build_upstream_headers, load_codex_credentials, resolve_model
from schemas import AnthropicMessagesRequest, UpstreamResponse
from streaming import aggregate_responses_message, translate_responses_sse

logger = logging.getLogger("p3c3")
logger.setLevel(logging.INFO)
if not logger.handlers:
    # Add a stderr handler so boot/debug lines land in the proxy log even
    # though uvicorn doesn't configure the root logger at INFO.
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("p3c3 %(levelname)s: %(message)s"))
    logger.addHandler(_handler)
    logger.propagate = False


@asynccontextmanager
async def _lifespan(_: FastAPI):
    """Log the effective config on boot - the first thing to check in the proxy log."""
    s = ProxySettings.from_env()
    detail = ""
    if s.codex_mode:
        token, account_id = load_codex_credentials()
        detail = (
            f" codex_auth={'present' if token else 'MISSING'}"
            f" account_id={'present' if account_id else 'MISSING'}"
        )
    logger.info(
        "p3c3 proxy up: mode=%s upstream=%s host=%s port=%d read_timeout=%ss debug=%s%s",
        s.upstream_mode, s.base_url, s.host, s.port, s.timeout.read, s.debug, detail,
    )
    yield


app = FastAPI(lifespan=_lifespan)


def build_async_client(settings: ProxySettings) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=settings.base_url, timeout=settings.timeout)


def _debug_log(settings: ProxySettings, **fields: Any) -> None:
    if settings.debug:
        logger.info("proxy_debug %s", json.dumps(fields, ensure_ascii=False, sort_keys=True))


@app.exception_handler(RequestValidationError)
async def request_validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return proxy_error_response(ProxyError(400, "invalid_request_error", json.dumps(exc.errors(), ensure_ascii=False)))


@app.get("/health")
async def health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/debug")
async def debug() -> dict[str, Any]:
    """Config + readiness snapshot for diagnostics. Never returns the token itself."""
    s = ProxySettings.from_env()
    info: dict[str, Any] = {
        "ok": True,
        "upstream_mode": s.upstream_mode,
        "upstream_base": s.base_url,
        "host": s.host,
        "port": s.port,
        "read_timeout_s": s.timeout.read,
        "model_map": s.model_map,
        "debug_logging": s.debug,
        "openai_api_key_set": bool(s.api_key),
        "tuning": {
            "temperature": s.temperature,
            "reasoning_effort": s.reasoning_effort,
            "reasoning_summary": s.reasoning_summary,
            "verbosity": s.verbosity,
        },
    }
    if s.codex_mode:
        token, account_id = load_codex_credentials()
        info["codex_auth_path"] = os.getenv("CODEX_AUTH_PATH", "~/.codex/auth.json")
        info["codex_auth_present"] = bool(token)
        info["codex_account_id_present"] = bool(account_id)
    return info


@app.post("/v1/messages/count_tokens")
async def count_tokens(body: dict[str, Any]) -> dict[str, int]:
    return {"input_tokens": max(1, len(json.dumps(body, ensure_ascii=False)) // 4)}


async def _read_upstream_error(response: httpx.Response) -> str:
    try:
        return (await response.aread()).decode("utf-8", errors="replace")
    except Exception:
        return ""


async def _open_upstream_stream(
    payload: dict[str, Any],
    settings: ProxySettings,
) -> tuple[httpx.AsyncClient, httpx.Response]:
    client = build_async_client(settings)
    request = client.build_request("POST", "/responses", json=payload, headers=build_upstream_headers(settings))
    try:
        response = await client.send(request, stream=True)
    except Exception:
        await client.aclose()
        raise
    if response.status_code >= 400:
        body = await _read_upstream_error(response)
        await response.aclose()
        await client.aclose()
        raise proxy_error_from_upstream(response.status_code, body)
    return client, response


async def _relay_stream(
    client: httpx.AsyncClient,
    response: httpx.Response,
    requested_model: str,
) -> AsyncIterator[bytes]:
    try:
        async def upstream_lines() -> AsyncIterator[str]:
            async for line in response.aiter_lines():
                yield line

        async for chunk in translate_responses_sse(upstream_lines(), requested_model=requested_model):
            yield chunk
    finally:
        await response.aclose()
        await client.aclose()


@app.post("/v1/messages", response_model=None)
async def create_message(body: AnthropicMessagesRequest):
    settings = ProxySettings.from_env()
    mapped_model = resolve_model(body.model, settings.model_map)
    payload = anthropic_request_to_responses(
        body,
        settings.model_map,
        codex_mode=settings.codex_mode,
        temperature=settings.temperature,
        reasoning_effort=settings.reasoning_effort,
        reasoning_summary=settings.reasoning_summary,
        verbosity=settings.verbosity,
    )
    _debug_log(
        settings,
        requested_model=body.model,
        mapped_model=mapped_model,
        stream=body.stream,
        message_count=len(body.messages),
        tools_present=bool(body.tools),
    )
    if settings.debug and settings.codex_mode:
        import sys
        shape = [
            (it.get("role") or it.get("type"), [p.get("type") for p in (it.get("content") or [])])
            for it in payload.get("input", [])
        ]
        print(f"codex_input_shape={shape}", file=sys.stderr, flush=True)
    if body.stream:
        try:
            client, response = await _open_upstream_stream(payload, settings)
        except ProxyError as exc:
            if settings.debug:
                import sys
                print(f"upstream_error keys={sorted(payload.keys())} err={str(getattr(exc, 'message', exc))[:400]}", file=sys.stderr, flush=True)
            return proxy_error_response(exc)
        except Exception as exc:
            return proxy_error_response(proxy_error_from_httpx(exc))
        _debug_log(settings, upstream_status=response.status_code)
        return StreamingResponse(
            _relay_stream(client, response, requested_model=body.model),
            media_type="text/event-stream",
        )
    # The ChatGPT codex backend is stream-only and 400s on non-stream requests.
    # For non-stream clients (e.g. `claude -p`), force upstream streaming, collect the
    # terminal response.completed event, and return a single non-stream Anthropic JSON.
    if settings.codex_mode:
        payload["stream"] = True
        try:
            client, response = await _open_upstream_stream(payload, settings)
        except ProxyError as exc:
            if settings.debug:
                import sys
                print(f"upstream_error(codex-nonstream) err={str(getattr(exc, 'message', exc))[:400]}", file=sys.stderr, flush=True)
            return proxy_error_response(exc)
        except Exception as exc:
            return proxy_error_response(proxy_error_from_httpx(exc))
        try:
            async def _lines() -> AsyncIterator[str]:
                async for line in response.aiter_lines():
                    yield line

            message = await aggregate_responses_message(_lines(), requested_model=body.model)
        except Exception as exc:
            return proxy_error_response(proxy_error_from_httpx(exc))
        finally:
            await response.aclose()
            await client.aclose()
        return JSONResponse(content=message)

    try:
        async with build_async_client(settings) as client:
            response = await client.post("/responses", json=payload, headers=build_upstream_headers(settings))
        _debug_log(settings, upstream_status=response.status_code)
        if response.status_code >= 400:
            raise proxy_error_from_upstream(response.status_code, response.text)
        validated = UpstreamResponse.model_validate(response.json())
        return JSONResponse(content=responses_to_anthropic_message(validated, requested_model=body.model))
    except ValidationError as exc:
        return proxy_error_response(ProxyError(502, "api_error", str(exc)))
    except Exception as exc:
        return proxy_error_response(proxy_error_from_httpx(exc))
