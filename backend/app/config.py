"""Backend configuration and environment defaults."""
import os

NODE_PORT = int(os.environ.get("NODE_PORT", 8080))
AI_PORT = int(os.environ.get("AI_PORT", 8081))
HOST = os.environ.get("HOST", "0.0.0.0")

# Telemetry broadcast interval in seconds
TELEMETRY_INTERVAL_SEC = float(os.environ.get("TELEMETRY_INTERVAL_SEC", 2.0))

# Default buzzer auto-shutoff time
BUZZER_DURATION_SEC = 8.0
