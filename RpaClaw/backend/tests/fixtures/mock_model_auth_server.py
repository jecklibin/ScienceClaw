from __future__ import annotations

import argparse
import hashlib
import json
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse


MOCK_STATIC_MODEL = "mock-static-model"
MOCK_DYNAMIC_MODEL = "mock-dynamic-model"
STATIC_AUTH_HEADERS = {
    "Authorization": "Bearer sk-test",
    "X-Gateway-Token": "static-token",
    "X-Tenant": "tenant-a",
}


class ModelAuthMockServer:
    """Test-only OpenAI-compatible server for static and dynamic model auth."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        self.host = host
        self.port = port
        self.requests: list[dict[str, Any]] = []
        self.issued_tokens: dict[str, dict[str, Any]] = {}
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "ModelAuthMockServer":
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                owner._handle(self)

            def do_POST(self):
                owner._handle(self)

            def log_message(self, format, *args):
                return

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def close(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        if self._thread:
            self._thread.join(timeout=2)
        self._server = None
        self._thread = None

    @property
    def origin(self) -> str:
        if not self._server:
            raise RuntimeError("mock server is not running")
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    @property
    def base_url(self) -> str:
        return f"{self.origin}/v1"

    def _handle(self, handler: BaseHTTPRequestHandler) -> None:
        parsed = urlparse(handler.path)
        query = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
        body = self._read_body(handler)
        request = {
            "method": handler.command,
            "path": parsed.path,
            "query": query,
            "headers": dict(handler.headers),
            "body": body,
        }
        self.requests.append(request)
        self._log("request", request)

        if parsed.path == "/token":
            self._handle_token(handler, request)
            return

        if parsed.path.startswith("/v1/models/"):
            model_name = parsed.path.rsplit("/", 1)[-1]
            self._handle_model_metadata(handler, request, model_name)
            return

        if parsed.path == "/v1/chat/completions":
            model_name = str((body if isinstance(body, dict) else {}).get("model") or "")
            self._handle_chat_completion(handler, request, model_name)
            return

        self._send_json(handler, 404, {"error": {"message": "unknown mock endpoint"}})

    def _read_body(self, handler: BaseHTTPRequestHandler) -> Any:
        length = int(handler.headers.get("Content-Length") or "0")
        if length <= 0:
            return {}
        raw = handler.rfile.read(length).decode("utf-8")
        content_type = handler.headers.get("Content-Type") or ""
        if "application/json" in content_type:
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return raw
        if "application/x-www-form-urlencoded" in content_type:
            return {key: values[-1] for key, values in parse_qs(raw).items()}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    def _handle_token(self, handler: BaseHTTPRequestHandler, request: dict[str, Any]) -> None:
        body = request["body"] if isinstance(request["body"], dict) else {}
        query = request["query"]
        headers = request["headers"]

        client_id = str(
            body.get("client_id")
            or query.get("client_id")
            or headers.get("X-Client-Id")
            or headers.get("X-Client")
            or "anonymous"
        )
        client_secret = str(body.get("client_secret") or query.get("client_secret") or "")
        tenant = str(body.get("tenant") or query.get("tenant") or query.get("aud") or "default-tenant")

        if not client_secret:
            self._send_json(handler, 401, {"error": "missing client_secret"})
            return

        token = self._make_dynamic_token(client_id, client_secret, tenant)
        expires_in = 3600
        self.issued_tokens[token] = {
            "client_id": client_id,
            "tenant": tenant,
            "expires_at": time.time() + expires_in,
        }
        self._send_json(
            handler,
            200,
            {
                "code": 0,
                "message": "ok",
                "data": {
                    "access_token": token,
                    "token_type": "Bearer",
                    "expires_in": expires_in,
                    "tenant": {
                        "id": tenant,
                        "name": f"{tenant}-name",
                    },
                    "client": {
                        "id": client_id,
                    },
                },
                "trace_id": f"trace-{token[-8:]}",
            },
        )

    def _handle_model_metadata(
        self,
        handler: BaseHTTPRequestHandler,
        request: dict[str, Any],
        model_name: str,
    ) -> None:
        if model_name == MOCK_STATIC_MODEL:
            if not self._has_static_auth(request["headers"]):
                self._send_json(handler, 401, {"error": "missing static auth headers"})
                return
        elif model_name == MOCK_DYNAMIC_MODEL:
            if not self._has_dynamic_auth(request["headers"]):
                self._send_json(handler, 401, {"error": "missing or invalid dynamic token"})
                return
        else:
            self._send_json(handler, 404, {"error": {"message": "unknown model"}})
            return

        self._send_json(
            handler,
            200,
            {
                "id": model_name,
                "object": "model",
                "context_window": 8192,
            },
        )

    def _handle_chat_completion(
        self,
        handler: BaseHTTPRequestHandler,
        request: dict[str, Any],
        model_name: str,
    ) -> None:
        if model_name == MOCK_STATIC_MODEL:
            if not self._has_static_auth(request["headers"]):
                self._send_json(handler, 401, {"error": "missing static auth headers"})
                return
            content = "static auth ok"
        elif model_name == MOCK_DYNAMIC_MODEL:
            if not self._has_dynamic_auth(request["headers"]):
                self._send_json(handler, 401, {"error": "missing or invalid dynamic token"})
                return
            content = "dynamic auth ok"
        else:
            self._send_json(handler, 404, {"error": {"message": "unknown model"}})
            return

        self._send_json(
            handler,
            200,
            {
                "id": f"chatcmpl-{model_name}",
                "object": "chat.completion",
                "created": 0,
                "model": model_name,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    def _has_static_auth(self, headers: dict[str, str]) -> bool:
        return all(headers.get(name) == value for name, value in STATIC_AUTH_HEADERS.items())

    def _has_dynamic_auth(self, headers: dict[str, str]) -> bool:
        value = headers.get("Authorization") or ""
        prefix = "Bearer "
        if not value.startswith(prefix):
            return False
        token = value[len(prefix) :]
        issued = self.issued_tokens.get(token)
        if not issued or issued["expires_at"] <= time.time():
            return False
        tenant_header = headers.get("X-Tenant-Id")
        return not tenant_header or tenant_header == issued["tenant"]

    def _make_dynamic_token(self, client_id: str, client_secret: str, tenant: str) -> str:
        digest = hashlib.sha256(f"{client_id}:{client_secret}:{tenant}".encode("utf-8")).hexdigest()
        return f"dyn-{digest[:32]}"

    def _send_json(self, handler: BaseHTTPRequestHandler, status: int, payload: Any) -> None:
        self._log("response", {"status": status, "body": payload})
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)

    def _log(self, event: str, payload: Any) -> None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(
            f"[mock-auth] {timestamp} {event}\n"
            f"{json.dumps(payload, ensure_ascii=False, indent=2, default=str)}",
            flush=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the test-only model auth mock server.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=18080, type=int)
    args = parser.parse_args()

    server = ModelAuthMockServer(host=args.host, port=args.port)
    with server:
        print(f"Mock server: {server.origin}")
        print(f"Static model base_url: {server.base_url}, model: {MOCK_STATIC_MODEL}")
        print("Static model headers: Authorization=Bearer sk-test, X-Gateway-Token=static-token, X-Tenant=tenant-a")
        print(f"Dynamic token URL: {server.origin}/token")
        print(f"Dynamic model base_url: {server.base_url}, model: {MOCK_DYNAMIC_MODEL}")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            print("\nStopped.")


if __name__ == "__main__":
    main()
