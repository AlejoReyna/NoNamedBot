"""Lightweight health check HTTP server (stdlib only)."""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING

from src.deployment.chat_api import build_chat_reply
from src.research.hourly_pnl import read_hourly_pnl

if TYPE_CHECKING:
    from src.deployment.health_state import HealthState

LOGGER = logging.getLogger(__name__)

# Request body limits
_MAX_BODY_BYTES = 8_192
_MAX_MESSAGE_BYTES = 4_096

# Per-IP rate limit for POST /api/chat: 30 requests per 60 seconds
_RATE_LIMIT = 30
_RATE_WINDOW = 60.0
_rate_data: dict[str, tuple[int, float]] = {}
_rate_lock = threading.Lock()

# session_id must be alphanumeric + hyphen/underscore, max 64 chars
_SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")


def _check_rate(ip: str) -> bool:
    """Return True if the IP is within the rate limit window."""
    now = time.monotonic()
    with _rate_lock:
        count, start = _rate_data.get(ip, (0, now))
        if now - start > _RATE_WINDOW:
            _rate_data[ip] = (1, now)
            return True
        if count >= _RATE_LIMIT:
            return False
        _rate_data[ip] = (count + 1, start)
        return True


def _tail_lines(path: Path, n: int = 50) -> list[str]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return lines[-n:]


def start_health_server(
    state: HealthState,
    *,
    host: str = "0.0.0.0",
    port: int = 8080,
    decision_log_path: str | Path = "decision_log.jsonl",
    dashboard_path: str | Path = "dashboard.html",
    chat_path: str | Path = "static/chat.html",
) -> ThreadingHTTPServer:
    """Start daemon health server; returns server handle for shutdown."""

    decision_path = Path(decision_log_path)
    dashboard_file = Path(dashboard_path)
    chat_file = Path(chat_path)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            LOGGER.debug("health %s - %s", self.address_string(), format % args)

        def _check_auth(self) -> bool:
            """Timing-safe Bearer token check. Passes when HEALTH_API_TOKEN is unset (dev mode)."""
            token = os.getenv("HEALTH_API_TOKEN", "").strip()
            if not token:
                return True
            header = self.headers.get("Authorization", "")
            provided = header.removeprefix("Bearer ").strip()
            if not provided:
                return False
            return hmac.compare_digest(token.encode(), provided.encode())

        def _send_security_headers(self) -> None:
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")

        def _send_json(self, payload: dict, status: int = 200) -> None:
            body = json.dumps(payload, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self._send_security_headers()
            self.end_headers()
            self.wfile.write(body)

        def _send_text(self, body: str, content_type: str, status: int = 200) -> None:
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self._send_security_headers()
            self.end_headers()
            self.wfile.write(encoded)

        def _serve_chat(self) -> None:
            if chat_file.exists():
                self._send_text(chat_file.read_text(encoding="utf-8"), "text/html")
            else:
                self._send_json({"error": "static/chat.html not found"}, status=404)

        def do_GET(self) -> None:
            if not self._check_auth():
                self._send_json({"error": "unauthorized"}, status=401)
                return
            path = self.path.split("?", 1)[0]
            if path in ("/", "/chat"):
                self._serve_chat()
            elif path.startswith("/health"):
                payload = state.snapshot()
                status = 503 if state.is_stalled() else 200
                if state.is_stalled():
                    payload["status"] = "stalled"
                self._send_json(payload, status=status)
            elif path.startswith("/status"):
                payload = state.snapshot()
                self._send_json(payload, status=200)
            elif path == "/logs/hourly-pnl":
                records = read_hourly_pnl()
                self._send_json({"records": records, "count": len(records)})
            elif path.startswith("/logs"):
                lines = _tail_lines(decision_path, 50)
                self._send_json({"lines": lines})
            elif path.startswith("/dashboard"):
                if dashboard_file.exists():
                    self._send_text(dashboard_file.read_text(encoding="utf-8"), "text/html")
                else:
                    self._send_json({"error": "dashboard.html not found"}, status=404)
            else:
                self._send_json({"error": "not found"}, status=404)

        def do_POST(self) -> None:
            path = self.path.split("?", 1)[0]
            if not path.startswith("/api/chat"):
                self._send_json({"error": "not found"}, status=404)
                return

            # Rate limit before auth to prevent timing oracle on auth check
            if not _check_rate(self.client_address[0]):
                self._send_json({"error": "too many requests"}, status=429)
                return

            if not self._check_auth():
                self._send_json({"error": "unauthorized"}, status=401)
                return

            try:
                raw_length = int(self.headers.get("Content-Length", "0") or "0")
            except ValueError:
                raw_length = 0
            length = min(raw_length, _MAX_BODY_BYTES)
            raw = self.rfile.read(length) if length > 0 else b"{}"

            try:
                body = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                self._send_json({"error": "invalid json"}, status=400)
                return

            # Validate session_id format before using it as a dict key
            session_id = str(body.get("session_id", "default"))
            if not _SESSION_ID_RE.match(session_id):
                self._send_json({"error": "invalid session_id"}, status=400)
                return

            msg = str(body.get("message", ""))
            if len(msg.encode("utf-8")) > _MAX_MESSAGE_BYTES:
                self._send_json({"error": "message too long"}, status=413)
                return

            reply = build_chat_reply(
                msg,
                session_id=session_id,
                health_snapshot=state.snapshot(),
                decision_log_path=decision_path,
            )
            self._send_json(reply)

    class _ReuseAddrServer(ThreadingHTTPServer):
        allow_reuse_address = True

    server = _ReuseAddrServer((host, port), Handler)
    thread = threading.Thread(target=server.serve_forever, name="health-server", daemon=True)
    thread.start()
    LOGGER.info("Health server listening on %s:%s", host, port)
    return server
