"""Hardware interface, simulated sensors, and relay controllers."""
import math
import random
import threading
import time
from .config import BUZZER_DURATION_SEC

class HardwareController:
    """Manages sensor acquisition and actuator states."""

    def __init__(self):
        self.lock = threading.Lock()
        self.relays = {
            "fan": False,
            "ac": False,
            "evap": False,
            "exhaust": False,
            "cooler": False,
            "mist": False,
            "damper": False,
        }
        self.state = {
            "buzzer": False,
            "buzzer_until": 0.0,
            "buzzer_pattern": "alert",
            "thresholds": {"wbgt": [26, 29, 32], "heatIndex": [27, 33, 41]},
            "temp": 31.0,
            "rh": 62.0,
            "globe_temp": 33.5,
        }

    def read_sensors(self):
        """
        Return (air_temp_c, rh_percent, globe_temp_c).
        Simulates thermal inertia and actuator cooling effects until physical STM32 bridge is attached.
        """
        with self.lock:
            cooling = (
                0.05 * self.relays.get("fan", False)
                + 0.08 * self.relays.get("exhaust", False)
                + 0.15 * (self.relays.get("evap", False) or self.relays.get("cooler", False))
                + 0.30 * self.relays.get("ac", False)
                + 0.10 * self.relays.get("mist", False)
            )
            # Drift towards baseline 33.0C ambient unless actively cooled
            self.state["temp"] += (33.0 - self.state["temp"]) * 0.02 - cooling * 0.2 + random.uniform(-0.05, 0.05)
            self.state["rh"] = min(95.0, max(25.0, self.state["rh"] + random.uniform(-0.3, 0.3)))
            self.state["globe_temp"] = self.state["temp"] + 2.5 + math.sin(time.time() / 60.0)

            if self.state["buzzer"] and time.time() > self.state["buzzer_until"]:
                self.state["buzzer"] = False

            air = round(self.state["temp"], 2)
            rh = round(self.state["rh"], 1)
            globe = round(self.state["globe_temp"], 2)
        return air, rh, globe

    def get_telemetry_frame(self, node_id="uno-q-01"):
        """Constructs telemetry frame adhering to PROTOCOL.md."""
        air, rh, globe = self.read_sensors()
        with self.lock:
            active_relays = {
                "fan": bool(self.relays["fan"]),
                "ac": bool(self.relays["ac"]),
                "evap": bool(self.relays["evap"]),
                "exhaust": bool(self.relays["exhaust"]),
            }
            # Optional extra channels
            for ch in ("cooler", "mist", "damper"):
                if ch in self.relays:
                    active_relays[ch] = bool(self.relays[ch])

            return {
                "type": "telemetry",
                "nodeId": node_id,
                "roomTemp": air,
                "humidity": rh,
                "radiantTemp": globe,
                "relays": active_relays,
                "buzzer": self.state["buzzer"],
                "sensors": [
                    {"id": "HDC1080", "ok": True},
                    {"id": "DS18B20 globe", "ok": True},
                    {"id": "BMP280", "ok": True},
                ],
                "rssi": -65,
                "timestamp": int(time.time() * 1000),
            }

    def set_relay(self, relay_id, on):
        """Toggle an actuator relay."""
        with self.lock:
            if relay_id in self.relays:
                self.relays[relay_id] = bool(on)
                # Sync aliases
                if relay_id == "cooler":
                    self.relays["evap"] = bool(on)
                elif relay_id == "evap":
                    self.relays["cooler"] = bool(on)
                return True
            return False

    def get_relays(self):
        with self.lock:
            return dict(self.relays)

    def set_buzzer(self, on, pattern="alert"):
        """Toggle audio buzzer alarm."""
        with self.lock:
            self.state["buzzer"] = bool(on)
            self.state["buzzer_pattern"] = pattern
            self.state["buzzer_until"] = time.time() + BUZZER_DURATION_SEC if on else 0.0

    def set_thresholds(self, wbgt, heat_index):
        """Update adaptive risk thresholds."""
        with self.lock:
            self.state["thresholds"] = {"wbgt": wbgt, "heatIndex": heat_index}

    def speak_on_node(self, text):
        """Forward speech to local speaker / Piper / eSpeak-ng."""
        print(f"[Hardware Speaker Audio Output]: {text}")

hardware = HardwareController()
