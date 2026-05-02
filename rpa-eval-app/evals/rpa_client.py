from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Iterable
from urllib import error, request


TERMINAL_EVENTS = {"agent_done", "done", "agent_aborted", "error"}


class RpaClawError(RuntimeError):
    pass


class RpaClawTimeoutError(RpaClawError):
    def __init__(
        self,
        message: str,
        *,
        session_id: str | None = None,
        raw_events: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.session_id = session_id
        self.raw_events = raw_events or []


@dataclass(frozen=True)
class RpaRunResult:
    session_id: str | None
    raw_events: list[dict[str, Any]]
    session: dict[str, Any] | None = None


class RpaClawClient:
    def __init__(
        self,
        base_url: str,
        *,
        token: str = "",
        timeout_s: float = 180.0,
        model_name: str = "",
        model_config_id: str = "",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_s = timeout_s
        self.model_name = model_name
        self.model_config_id = model_config_id
        self.model_config_id = model_config_id or (self.resolve_model_config_id(model_name) if model_name else "")

    def run_instruction(
        self,
        *,
        case_id: str,
        start_url: str,
        instruction: str,
        timeout_s: float | None = None,
    ) -> RpaRunResult:
        session_id = self.start_session(case_id)
        try:
            if start_url:
                self.navigate(session_id, start_url)
            events = self.chat_with_wall_timeout(session_id, instruction, timeout_s=timeout_s)
            session = self.get_session(session_id)
            return RpaRunResult(session_id=session_id, raw_events=events, session=session)
        finally:
            self.stop_session(session_id, ignore_errors=True)

    def start_session(self, case_id: str) -> str:
        response = self._json_request(
            "POST",
            "/api/v1/rpa/session/start",
            {"sandbox_session_id": f"rpa-eval-{case_id}"},
        )
        session = response.get("session") or {}
        session_id = session.get("id") or session.get("session_id")
        if not session_id:
            raise RpaClawError("RpaClaw session start response did not include a session id")
        return str(session_id)

    def navigate(self, session_id: str, url: str) -> None:
        self._json_request("POST", f"/api/v1/rpa/session/{session_id}/navigate", {"url": url})

    def chat(
        self,
        session_id: str,
        instruction: str,
        *,
        business_instruction: str | None = None,
    ) -> list[dict[str, Any]]:
        return list(self.iter_chat_events(session_id, instruction, business_instruction=business_instruction))

    def iter_chat_events(
        self,
        session_id: str,
        instruction: str,
        *,
        business_instruction: str | None = None,
    ) -> Iterable[dict[str, Any]]:
        payload = {"message": instruction, "mode": "chat"}
        if business_instruction:
            payload["business_instruction"] = business_instruction
        if self.model_config_id:
            payload["model_config_id"] = self.model_config_id
        req = self._request(
            "POST",
            f"/api/v1/rpa/session/{session_id}/chat",
            payload,
        )
        try:
            with request.urlopen(req, timeout=self.timeout_s) as response:
                yield from parse_sse_lines(
                    (line.decode("utf-8", errors="replace") for line in response),
                    stop_on_terminal=True,
                )
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RpaClawError(f"RPA chat failed with HTTP {exc.code}: {detail}") from exc
        except error.URLError as exc:
            raise RpaClawError(f"RPA chat failed: {exc.reason}") from exc
        except TimeoutError as exc:
            raise RpaClawError(f"RPA chat timed out after {self.timeout_s}s") from exc

    def chat_with_wall_timeout(
        self,
        session_id: str,
        instruction: str,
        *,
        timeout_s: float | None,
        business_instruction: str | None = None,
    ) -> list[dict[str, Any]]:
        if timeout_s is None:
            return self.chat(session_id, instruction, business_instruction=business_instruction)

        events: list[dict[str, Any]] = []
        errors: list[BaseException] = []

        def consume_events() -> None:
            try:
                for event in self.iter_chat_events(session_id, instruction, business_instruction=business_instruction):
                    events.append(event)
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=consume_events, name=f"rpa-eval-chat-{session_id}", daemon=True)
        started = time.perf_counter()
        worker.start()
        deadline = started + timeout_s
        while worker.is_alive() and time.perf_counter() < deadline:
            worker.join(min(1.0, max(0.0, deadline - time.perf_counter())))
            if any(str(event.get("event") or "") in TERMINAL_EVENTS for event in events):
                self.stop_session(session_id, ignore_errors=True)
                worker.join(5)
                return events
        if any(str(event.get("event") or "") in TERMINAL_EVENTS for event in events):
            self.stop_session(session_id, ignore_errors=True)
            worker.join(5)
            return events
        if worker.is_alive():
            elapsed = round(time.perf_counter() - started, 1)
            self.stop_session(session_id, ignore_errors=True)
            worker.join(5)
            raise RpaClawTimeoutError(
                f"RPA chat exceeded case timeout {timeout_s:g}s after {elapsed:g}s",
                session_id=session_id,
                raw_events=list(events),
            )
        if errors:
            raise errors[0]
        return events

    def get_session(self, session_id: str) -> dict[str, Any]:
        response = self._json_request("GET", f"/api/v1/rpa/session/{session_id}")
        return response.get("session", response)

    def generate_script(self, session_id: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._json_request("POST", f"/api/v1/rpa/session/{session_id}/generate", {"params": params or {}})

    def test_script(
        self,
        session_id: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_s: float | None = None,
        setup_navigation: list[str] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"params": params or {}}
        if setup_navigation is not None:
            payload["setup_navigation"] = list(setup_navigation)
        return self._json_request(
            "POST",
            f"/api/v1/rpa/session/{session_id}/test",
            payload,
            timeout_s=timeout_s,
        )

    def stop_session(self, session_id: str, *, ignore_errors: bool = False) -> None:
        try:
            self._json_request("POST", f"/api/v1/rpa/session/{session_id}/stop")
        except RpaClawError:
            if not ignore_errors:
                raise

    def resolve_model_config_id(self, model_name: str) -> str:
        response = self._json_request("GET", "/api/v1/models")
        models = response.get("data", response if isinstance(response, list) else [])
        if not isinstance(models, list):
            raise RpaClawError("Model list response did not include a model array")
        for model in models:
            if not isinstance(model, dict):
                continue
            candidates = {
                str(model.get("model_name") or ""),
                str(model.get("name") or ""),
                str(model.get("id") or model.get("_id") or ""),
            }
            if model_name in candidates:
                model_id = model.get("id") or model.get("_id")
                if not model_id:
                    raise RpaClawError(f"Model '{model_name}' did not include an id")
                return str(model_id)
        available = ", ".join(
            str(model.get("model_name") or model.get("name") or model.get("id"))
            for model in models
            if isinstance(model, dict)
        )
        raise RpaClawError(f"Model '{model_name}' was not found. Available models: {available}")

    def _json_request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        req = self._request(method, path, payload)
        try:
            with request.urlopen(req, timeout=timeout_s or self.timeout_s) as response:
                raw = response.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RpaClawError(f"{method} {path} failed with HTTP {exc.code}: {detail}") from exc
        except error.URLError as exc:
            raise RpaClawError(f"{method} {path} failed: {exc.reason}") from exc
        except TimeoutError as exc:
            raise RpaClawError(f"{method} {path} timed out after {timeout_s or self.timeout_s}s") from exc
        return json.loads(raw or "{}")

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> request.Request:
        if self.model_config_id and path.endswith("/chat") and isinstance(payload, dict):
            payload = {**payload, "model_config_id": self.model_config_id}
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Accept": "text/event-stream, application/json"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return request.Request(f"{self.base_url}{path}", data=body, headers=headers, method=method)


def parse_sse_lines(lines: Iterable[str], *, stop_on_terminal: bool = False) -> Iterable[dict[str, Any]]:
    event_name: str | None = None
    data_lines: list[str] = []

    def should_stop(item: dict[str, Any]) -> bool:
        return stop_on_terminal and str(item.get("event") or "") in TERMINAL_EVENTS

    def flush() -> dict[str, Any] | None:
        nonlocal event_name, data_lines
        if event_name is None and not data_lines:
            return None
        raw_data = "\n".join(data_lines)
        parsed_data: Any = raw_data
        if raw_data:
            try:
                parsed_data = json.loads(raw_data)
            except json.JSONDecodeError:
                parsed_data = raw_data
        item = {"event": event_name or "message", "data": parsed_data, "raw_data": raw_data}
        event_name = None
        data_lines = []
        return item

    for raw_line in lines:
        line = raw_line.rstrip("\r\n")
        if not line:
            item = flush()
            if item is not None:
                yield item
                if should_stop(item):
                    return
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].lstrip())
        else:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                data_lines.append(line)
            else:
                item = {"event": payload.get("event", "message"), "data": payload.get("data", payload), "raw_data": line}
                yield item
                if should_stop(item):
                    return

    item = flush()
    if item is not None:
        yield item
