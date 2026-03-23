"""Minimal local-only evaluator stub (optional).

This service is NOT required for AgentX submission.
It's a small developer tool you can run locally to validate wiring, health checks,
or to extend with custom evaluation hooks.

Endpoints:
- GET / or /health  -> {"ok": true, "service": "a3_evaluator_stub"}
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/health"):
            return self._send(200, {"ok": True, "service": "a3_evaluator_stub"})
        return self._send(404, {"ok": False, "error": "not found"})


def main(host: str = "127.0.0.1", port: int = 9100) -> None:
    httpd = HTTPServer((host, port), Handler)
    print(f"A3 evaluator stub listening on http://{host}:{port}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
