from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib import error, request


class EvalAppError(RuntimeError):
    pass


@dataclass(frozen=True)
class EvalAppUserSession:
    username: str
    token: str
    user: dict[str, Any] | None = None


class EvalAppClient:
    def __init__(self, base_url: str, *, timeout_s: float = 20.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def reset(self, reset_token: str, *, fixture_variant: str | None = None) -> dict[str, Any]:
        payload = {"fixture_variant": fixture_variant} if fixture_variant else None
        return self._request(
            "POST",
            "/api/eval/reset",
            headers={"X-RPA-Eval-Reset-Token": reset_token},
            json_body=payload,
        )

    def login(self, username: str, password: str) -> EvalAppUserSession:
        payload = {"username": username, "password": password}
        token_response = self._request("POST", "/api/auth/login", json_body=payload)
        token = token_response["access_token"]
        user = self._request("GET", "/api/auth/me", token=token)
        return EvalAppUserSession(username=username, token=token, user=user)

    def get_json(self, path: str, token: str) -> Any:
        return self._request("GET", path, token=token)

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        token: str | None = None,
    ) -> Any:
        body = None
        request_headers = dict(headers or {})
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        if token:
            request_headers["Authorization"] = f"Bearer {token}"

        req = request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers=request_headers,
            method=method,
        )
        try:
            with request.urlopen(req, timeout=self.timeout_s) as response:
                raw = response.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise EvalAppError(f"{method} {path} failed with HTTP {exc.code}: {detail}") from exc
        except error.URLError as exc:
            raise EvalAppError(f"{method} {path} failed: {exc.reason}") from exc
        except TimeoutError as exc:
            raise EvalAppError(f"{method} {path} timed out after {self.timeout_s}s") from exc

        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise EvalAppError(f"{method} {path} returned non-JSON response") from exc
