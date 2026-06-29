import sys
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server
from server import app


class FakeAsyncClient:
    def __init__(self, response: httpx.Response | Exception):
        self._response = response

    async def __aenter__(self) -> "FakeAsyncClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def post(self, *args, **kwargs) -> httpx.Response:
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def test_invalid_body_returns_anthropic_error_shape() -> None:
    client = TestClient(app)
    response = client.post("/v1/messages", json={"model": "llama3.2"})
    assert response.status_code == 400
    assert response.json()["type"] == "error"
    assert response.json()["error"]["type"] == "invalid_request_error"
    assert response.json()["request_id"] is None


def test_upstream_401_maps_to_authentication_error(monkeypatch) -> None:
    response = httpx.Response(401, json={"error": {"message": "bad key"}})
    monkeypatch.setattr(server, "build_async_client", lambda settings: FakeAsyncClient(response))
    client = TestClient(app)
    result = client.post(
        "/v1/messages",
        json={"model": "llama3.2", "max_tokens": 8, "messages": [{"role": "user", "content": "Hi"}]},
    )
    assert result.status_code == 401
    assert result.json()["error"] == {"type": "authentication_error", "message": "bad key"}


def test_upstream_404_maps_to_not_found_error(monkeypatch) -> None:
    response = httpx.Response(404, json={"error": {"message": "model not found"}})
    monkeypatch.setattr(server, "build_async_client", lambda settings: FakeAsyncClient(response))
    client = TestClient(app)
    result = client.post(
        "/v1/messages",
        json={"model": "missing", "max_tokens": 8, "messages": [{"role": "user", "content": "Hi"}]},
    )
    assert result.status_code == 404
    assert result.json()["error"]["type"] == "not_found_error"


def test_timeout_maps_to_504(monkeypatch) -> None:
    timeout = httpx.ReadTimeout("timed out")
    monkeypatch.setattr(server, "build_async_client", lambda settings: FakeAsyncClient(timeout))
    client = TestClient(app)
    result = client.post(
        "/v1/messages",
        json={"model": "llama3.2", "max_tokens": 8, "messages": [{"role": "user", "content": "Hi"}]},
    )
    assert result.status_code == 504
    assert result.json()["error"]["type"] == "api_error"
