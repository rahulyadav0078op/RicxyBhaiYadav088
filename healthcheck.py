"""
healthcheck.py — tiny HTTP endpoint for hosts that expect a port to be open.

Railway does not require this for a worker (no inbound port), but some platforms
and uptime monitors do, and a 200 here is a cheap liveness signal. It reports
whether Google credentials loaded and how the queue is doing. No secrets are
exposed — only booleans and counters.

Run alongside the bot:  python healthcheck.py &
"""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer


def snapshot() -> dict:
    try:
        import bot as botmod

        state = botmod.STATE
        if state is None:
            return {"ok": False, "reason": "bot not initialised"}
        return {
            "ok": True,
            "authorized": bool(state.client.authorized),
            "api_key": bool(state.client.api_key),
            "folder_order": state.cfg.folder_order,
            "queue_depth": state.jobs.queue.qsize() if state.jobs else 0,
            "uptime_seconds": int(time.time() - state.started_at),
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": repr(exc)}


class Handler(BaseHTTPRequestHandler):
    # Bind to all interfaces; hosts like Render/Fly route $PORT to the container.

    def do_GET(self):  # noqa: N802
        if self.path not in ("/", "/health", "/healthz"):
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps(snapshot()).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep container logs clean
        return


def serve_in_background(port: int | None = None) -> threading.Thread:
    port = port or int(os.getenv("PORT", "8080"))
    server = HTTPServer(("0.0.0.0", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"health endpoint on 0.0.0.0:{port}")
    return thread


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", int(os.getenv("PORT", "8080"))), Handler)
    print(f"health endpoint on 0.0.0.0:{os.getenv('PORT', '8080')}")
    server.serve_forever()
