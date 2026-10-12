"""
Lightweight RFC 6455 WebSocket server using standard library Python.
Streams telemetry JSON frames to connected clients every TELEMETRY_INTERVAL_SEC
and receives NodeCommand JSON messages.
"""
import base64
import hashlib
import json
import socket
import struct
import threading
import time
from .config import TELEMETRY_INTERVAL_SEC
from .hardware import hardware

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

class WebSocketClient:
    def __init__(self, sock, addr):
        self.sock = sock
        self.addr = addr
        self.closed = False

    def send_text(self, text):
        if self.closed:
            return
        payload = text.encode("utf-8")
        length = len(payload)
        header = bytearray()
        # Fin bit + Text opcode 0x1
        header.append(0x81)
        if length <= 125:
            header.append(length)
        elif length <= 65535:
            header.append(126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(127)
            header.extend(struct.pack("!Q", length))

        try:
            self.sock.sendall(header + payload)
        except Exception:
            self.close()

    def read_frame(self):
        """Reads a masked client WebSocket frame."""
        head = self._recv_exact(2)
        if not head:
            return None, None
        b1, b2 = head
        opcode = b1 & 0x0F
        masked = (b2 & 0x80) != 0
        length = b2 & 0x7F

        if length == 126:
            data = self._recv_exact(2)
            if not data:
                return None, None
            length = struct.unpack("!H", data)[0]
        elif length == 127:
            data = self._recv_exact(8)
            if not data:
                return None, None
            length = struct.unpack("!Q", data)[0]

        mask = self._recv_exact(4) if masked else None
        payload = self._recv_exact(length)
        if payload is None:
            return None, None

        if masked and mask:
            unmasked = bytearray(length)
            for i in range(length):
                unmasked[i] = payload[i] ^ mask[i % 4]
            payload = bytes(unmasked)

        return opcode, payload

    def _recv_exact(self, n):
        buf = bytearray()
        while len(buf) < n:
            try:
                chunk = self.sock.recv(n - len(buf))
                if not chunk:
                    return None
                buf.extend(chunk)
            except Exception:
                return None
        return bytes(buf)

    def close(self):
        self.closed = True
        try:
            self.sock.close()
        except Exception:
            pass


class WebSocketServer:
    def __init__(self):
        self.clients = []
        self.lock = threading.Lock()

    def handle_handshake(self, sock, headers):
        """Perform RFC 6455 handshake."""
        key = headers.get("Sec-WebSocket-Key", "").strip()
        if not key:
            return None
        accept_token = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        handshake_response = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept_token}\r\n"
            "\r\n"
        )
        sock.sendall(handshake_response.encode())
        client = WebSocketClient(sock, None)
        with self.lock:
            self.clients.append(client)
        return client

    def client_loop(self, client):
        """Listens for commands from WebSocket client and dispatches to hardware."""
        try:
            while not client.closed:
                opcode, data = client.read_frame()
                if opcode is None or opcode == 0x8:  # Connection close
                    break
                if opcode == 0x9:  # Ping
                    # Pong opcode 0xA
                    client.sock.sendall(b"\x8a\x00")
                    continue
                if opcode == 0x1:  # Text frame
                    try:
                        msg = json.loads(data.decode("utf-8"))
                        cmd_type = msg.get("type")
                        if cmd_type == "relay":
                            hardware.set_relay(msg.get("id"), msg.get("on", False))
                        elif cmd_type == "buzzer":
                            hardware.set_buzzer(msg.get("on", False), msg.get("pattern", "alert"))
                        elif cmd_type == "thresholds":
                            hardware.set_thresholds(msg.get("wbgt"), msg.get("heatIndex"))
                        elif cmd_type == "speak":
                            hardware.speak_on_node(msg.get("text", ""))
                    except Exception as e:
                        print(f"[WS Command Error]: {e}")
        finally:
            with self.lock:
                if client in self.clients:
                    self.clients.remove(client)
            client.close()

    def broadcast(self, frame_json_str):
        with self.lock:
            active = list(self.clients)
        for c in active:
            c.send_text(frame_json_str)

    def start_telemetry_stream(self):
        """Background thread streaming telemetry frames to all clients."""
        def run():
            while True:
                time.sleep(TELEMETRY_INTERVAL_SEC)
                if self.clients:
                    frame = hardware.get_telemetry_frame()
                    frame_str = json.dumps(frame)
                    self.broadcast(frame_str)
        t = threading.Thread(target=run, daemon=True)
        t.start()

ws_server = WebSocketServer()
ws_server.start_telemetry_stream()
