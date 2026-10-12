#!/usr/bin/env python3
"""
HeatGuard Edge API & WebSocket Server
Runs on Arduino UNO Q (Dragonwing QRB2210 Linux Core) or for local development.

Ports:
  - 8080: Node Telemetry API, Hardware Actuation & WebSocket (/ws)
  - 8081: Edge AI Assistant Chat API
"""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from .config import AI_PORT, HOST, NODE_PORT
from .hardware import hardware
from .model import model_status, rule_reply, run_model
from .websocket import ws_server


class HeatGuardHandler(BaseHTTPRequestHandler):
    server_version = "HeatGuardEdge/2.0"

    def log_message(self, fmt, *args):
        # Clean logging format
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {self.address_string()} {fmt % args}")

    def _send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Requested-With")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _send_json(self, code, body=None):
        raw = json.dumps(body).encode("utf-8") if body is not None else b""
        self.send_response(code)
        self._send_cors_headers()
        if body is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_body(self):
        content_length = int(self.headers.get("Content-Length") or 0)
        if content_length == 0:
            return {}
        try:
            raw = self.rfile.read(content_length)
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    def do_OPTIONS(self):
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self):
        url_path = urlsplit(self.path).path.rstrip("/")
        if not url_path:
            url_path = "/"

        # WebSocket Upgrade on /ws
        if url_path == "/ws" and self.headers.get("Upgrade", "").lower() == "websocket":
            client = ws_server.handle_handshake(self.connection, dict(self.headers))
            if client:
                # Send immediate initial telemetry frame
                client.send_text(json.dumps(hardware.get_telemetry_frame()))
                ws_server.client_loop(client)
            return

        if url_path == "" or url_path == "/" or url_path == "/health":
            return self._send_json(200, {
                "ok": True,
                "service": "HeatGuard Edge Gateway",
                "version": "2.0.0",
                "model": model_status(),
                "timestamp": int(time.time() * 1000)
            })

        if url_path == "/api/telemetry":
            frame = hardware.get_telemetry_frame()
            return self._send_json(200, frame)

        if url_path == "/api/relays":
            return self._send_json(200, {"ok": True, "relays": hardware.get_relays()})

        self._send_json(404, {"error": "Endpoint not found", "path": url_path})

    def do_POST(self):
        url_path = urlsplit(self.path).path.rstrip("/")
        body = self._read_body()
        if body is None:
            return self._send_json(400, {"error": "Invalid JSON payload"})

        if url_path == "/api/relay":
            relay_id = body.get("id")
            state = body.get("on")
            if not relay_id or not isinstance(state, bool):
                return self._send_json(400, {"error": "Missing 'id' or boolean 'on' parameter"})
            ok = hardware.set_relay(relay_id, state)
            if not ok:
                return self._send_json(400, {"error": f"Invalid relay id: {relay_id}"})
            return self._send_json(200, {"ok": True, "relays": hardware.get_relays()})

        if url_path == "/api/buzzer":
            state = bool(body.get("on", False))
            pattern = body.get("pattern", "alert")
            hardware.set_buzzer(state, pattern)
            return self._send_json(200, {"ok": True, "buzzer": state, "pattern": pattern})

        if url_path == "/api/config":
            wbgt = body.get("wbgt")
            heat_index = body.get("heatIndex")
            hardware.set_thresholds(wbgt, heat_index)
            return self._send_json(200, {"ok": True, "configured": {"wbgt": wbgt, "heatIndex": heat_index}})

        if url_path == "/api/speak":
            text = str(body.get("text", ""))
            hardware.speak_on_node(text)
            return self._send_json(200, {"ok": True, "spoken": text})

        if url_path in ("/v1/assistant/chat", "/api/assistant/chat"):
            query = str(body.get("query") or body.get("prompt") or "")
            ctx = body.get("context", {})
            history = body.get("history", [])
            started = time.time()
            out = run_model(query, ctx, history)
            if out is None:
                out = rule_reply(query, ctx)
            if isinstance(out, str):
                out = {"reply": out}
            out.setdefault("meta", {})
            out["meta"].setdefault("model", "heatguard-edge-rules")
            out["meta"]["latency_ms"] = int((time.time() - started) * 1000)
            return self._send_json(200, out)

        self._send_json(404, {"error": "Endpoint not found", "path": url_path})


def serve(port, name="Edge Server"):
    server = ThreadingHTTPServer((HOST, port), HeatGuardHandler)
    print(f"[{name}] Listening on http://{HOST}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(f"[{name}] Shutting down.")
        server.server_close()


def main():
    ai_thread = threading.Thread(target=serve, args=(AI_PORT, "Assistant API"), daemon=True)
    ai_thread.start()
    serve(NODE_PORT, "Node Gateway")


if __name__ == "__main__":
    main()
