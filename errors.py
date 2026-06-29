from __future__ import annotations

import json

import httpx
from fastapi.responses import JSONResponse


class ProxyError(Exception):
    def __init__(self, status_code: int, error_type: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type
        self.message = message


def anthropic_error_payload(error_type: str, message: str) -> dict[str, object]:
    return {
        "type": "error",
        "error": {"type": error_type, "message": message},
        "request_id": None,
    }


def error_response(error_type: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=anthropic_error_payload(error_type=error_type, message=message),
    )


def proxy_error_response(exc: ProxyError) -> JSONResponse:
    return error_response(exc.error_type, exc.message, exc.status_code)


def extract_upstream_error_message(body: str) -> str:
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return body or "Upstream request failed"
    if isinstance(parsed, dict):
        error = parsed.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message:
                return message
        message = parsed.get("message")
        if isinstance(message, str) and message:
            return message
    return body or "Upstream request failed"


def proxy_error_from_upstream(status_code: int, body: str) -> ProxyError:
    message = extract_upstream_error_message(body)
    if status_code == 401:
        return ProxyError(401, "authentication_error", message)
    if status_code == 404:
        return ProxyError(404, "not_found_error", message)
    if status_code == 429:
        return ProxyError(429, "rate_limit_error", message)
    return ProxyError(502, "api_error", message)


def proxy_error_from_httpx(exc: Exception) -> ProxyError:
    if isinstance(exc, httpx.TimeoutException):
        return ProxyError(504, "api_error", str(exc) or "Upstream request timed out")
    if isinstance(exc, httpx.RequestError):
        return ProxyError(502, "api_error", str(exc) or "Upstream request failed")
    if isinstance(exc, ProxyError):
        return exc
    return ProxyError(502, "api_error", str(exc) or "Unexpected proxy error")
