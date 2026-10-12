#!/usr/bin/env python3
"""Automated unit and integration test suite for HeatGuard Edge Backend."""
import json
import threading
import time
import urllib.request
import urllib.parse
from http.server import ThreadingHTTPServer

from app.config import AI_PORT, NODE_PORT, HOST
from app.server import HeatGuardHandler
from app.hardware import hardware
from app.model import model_status

def run_tests():
    print("=== Starting HeatGuard Edge Backend Test Suite ===")

    # Start servers on test ports if not already running
    test_node_port = 8880
    test_ai_port = 8881
    
    server_node = ThreadingHTTPServer((HOST, test_node_port), HeatGuardHandler)
    server_ai = ThreadingHTTPServer((HOST, test_ai_port), HeatGuardHandler)

    t1 = threading.Thread(target=server_node.serve_forever, daemon=True)
    t2 = threading.Thread(target=server_ai.serve_forever, daemon=True)
    t1.start()
    t2.start()
    time.sleep(0.5)

    base_node = f"http://127.0.0.1:{test_node_port}"
    base_ai = f"http://127.0.0.1:{test_ai_port}"

    # 1. Test Health endpoint (with query param to test URL parser)
    req = urllib.request.Request(f"{base_node}/health?param=test")
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode())
        assert resp.status == 200
        assert data["ok"] is True
        assert data["model"] in ("trained-cooling-model", "rules-fallback")
        print("[PASS] GET /health (with query param)")

    # 2. Test Telemetry endpoint
    req = urllib.request.Request(f"{base_node}/api/telemetry")
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode())
        assert resp.status == 200
        assert "roomTemp" in data
        assert "relays" in data
        assert "radiantTemp" in data
        print(f"[PASS] GET /api/telemetry (roomTemp={data['roomTemp']} C, rh={data['humidity']}%)")

    # 3. Test Relay Actuation
    relay_payload = json.dumps({"type": "relay", "id": "fan", "on": True}).encode()
    req = urllib.request.Request(f"{base_node}/api/relay", data=relay_payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode())
        assert resp.status == 200
        assert data["ok"] is True
        assert data["relays"]["fan"] is True
        print("[PASS] POST /api/relay (fan ON)")

    # 4. Test Buzzer Control
    buzzer_payload = json.dumps({"type": "buzzer", "on": True, "pattern": "alert"}).encode()
    req = urllib.request.Request(f"{base_node}/api/buzzer", data=buzzer_payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode())
        assert resp.status == 200
        assert data["buzzer"] is True
        print("[PASS] POST /api/buzzer")

    # 5. Test Thresholds Configuration
    thresh_payload = json.dumps({"type": "thresholds", "wbgt": [25, 28, 31], "heatIndex": [26, 32, 40]}).encode()
    req = urllib.request.Request(f"{base_node}/api/config", data=thresh_payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode())
        assert resp.status == 200
        assert data["configured"]["wbgt"] == [25, 28, 31]
        print("[PASS] POST /api/config")

    # 6. Test Assistant Chat API
    chat_payload = json.dumps({
        "query": "Is it safe to work outside?",
        "context": {
            "metrics": {"airTemp": 36, "humidity": 65, "wbgt": 31},
            "risk": {"level": "High"},
            "profile": {"conditions": ["hypertension"]}
        }
    }).encode()
    req = urllib.request.Request(f"{base_ai}/v1/assistant/chat", data=chat_payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode())
        assert resp.status == 200
        assert "reply" in data
        assert "meta" in data
        preview = data["reply"][:60].replace("°C", " C")
        print(f"[PASS] POST /v1/assistant/chat: \"{preview}...\"")

    # 7. Test Assistant Relay Control Intent
    chat_relay = json.dumps({
        "query": "Turn on the AC now",
        "context": {"metrics": {"airTemp": 38}}
    }).encode()
    req = urllib.request.Request(f"{base_ai}/v1/assistant/chat", data=chat_relay, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode())
        assert resp.status == 200
        assert "actions" in data
        assert data["actions"][0]["id"] == "ac"
        print("[PASS] POST /v1/assistant/chat (actuation action trigger)")

    # The optional model answers only cooling-demand questions and never replaces safety rules.
    cooling_payload = json.dumps({
        "query": "Estimate cooling demand",
        "context": {"metrics": {"airTemp": 32, "humidity": 60}}
    }).encode()
    req = urllib.request.Request(
        f"{base_ai}/v1/assistant/chat",
        data=cooling_payload,
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode())
        assert resp.status == 200
        assert "reply" in data
        if model_status() == "trained-cooling-model":
            assert data["meta"]["model"] == "heatguard-cooling-random-forest"
            assert 0 <= data["meta"]["cooling_demand_percent"] <= 100
            assert "synthetic demonstration data" in data["reply"]
        else:
            assert data["meta"]["model"] == "heatguard-edge-rules"
        print(f"[PASS] Cooling-demand model integration ({data['meta']['model']})")

    print("\nALL BACKEND UNIT AND INTEGRATION TESTS PASSED SUCCESSFULLY! (8/8)")

if __name__ == "__main__":
    run_tests()
