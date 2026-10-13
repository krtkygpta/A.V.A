"""
test_api_endpoints.py - FastAPI HTTP endpoint integration tests for Phase 1.
"""

import sys
from pathlib import Path
from fastapi.testclient import TestClient

SERVER_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = SERVER_DIR.parent
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from app import app

client = TestClient(app)


def test_health():
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}
    print("  [PASS] /health passed.")


def test_settings_api():
    # 1. GET /api/settings
    res = client.get("/api/settings")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert "settings" in body
    assert "dual_layer_enabled" in body["settings"]
    orig_dual = body["settings"]["dual_layer_enabled"]
    print(f"  [PASS] GET /api/settings: dual_layer_enabled={orig_dual}")

    # 2. POST /api/settings
    res_post = client.post("/api/settings", json={"dual_layer_enabled": True, "layer2_model": "test-deep-model"})
    assert res_post.status_code == 200
    assert res_post.json()["ok"] is True
    print("  [PASS] POST /api/settings: successfully saved settings.")


def test_blackboard_api():
    res = client.get("/api/blackboard/jobs")
    assert res.status_code == 200
    assert res.json()["ok"] is True
    print(f"  [PASS] GET /api/blackboard/jobs: count={res.json()['count']}")

    res_tele = client.get("/api/blackboard/telemetry")
    assert res_tele.status_code == 200
    assert res_tele.json()["ok"] is True
    print(f"  [PASS] GET /api/blackboard/telemetry: count={res_tele.json()['count']}")


def test_presence_api():
    # 1. GET /api/presence
    res_get = client.get("/api/presence")
    assert res_get.status_code == 200
    assert res_get.json()["ok"] is True
    assert res_get.json()["presence"] in ("present", "away")
    print(f"  [PASS] GET /api/presence: presence={res_get.json()['presence']}")

    # 2. POST /api/presence
    res_post = client.post("/api/presence", json={"presence": "away"})
    assert res_post.status_code == 200
    assert res_post.json()["presence"] == "away"

    # 3. Restore to present
    res_restore = client.post("/api/presence", json={"presence": "present"})
    assert res_restore.status_code == 200
    assert res_restore.json()["presence"] == "present"
    print("  [PASS] POST /api/presence: toggled presence successfully.")


def test_events_trigger_api():
    payload = {
        "sentinel": "unit_test_sentinel",
        "severity": "CRITICAL",
        "title": "Unit Test Alert",
        "message": "Simulated hardware thermal spike for verification.",
    }
    res = client.post("/api/events/trigger", json=payload)
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert "entry_id" in body
    assert "diagnosis" in body
    assert "report_text" in body
    assert "Kartikey" in body["report_text"], "Sentinel briefing must speak to Kartikey naturally"
    print(f"  [PASS] POST /api/events/trigger: human report delivered: '{body['report_text'][:60]}...'")


def test_chat_dual_layer_api():
    # Fast Reflex
    res_reflex = client.post("/api/chat/dual_layer", json={"message": "What time is it?", "client_id": "test_client"})
    assert res_reflex.status_code == 200
    assert res_reflex.json()["type"] == "reflex"
    print(f"  [PASS] POST /api/chat/dual_layer (Reflex): '{res_reflex.json()['response']}'")

    # Delegation
    res_deleg = client.post("/api/chat/dual_layer", json={"message": "Build a benchmark script for Redis", "client_id": "test_client"})
    assert res_deleg.status_code == 200
    assert res_deleg.json()["type"] == "delegation"
    assert res_deleg.json()["ticket_id"] is not None
    print(f"  [PASS] POST /api/chat/dual_layer (Delegation): '{res_deleg.json()['response']}' (Ticket: {res_deleg.json()['ticket_id']})")


if __name__ == "__main__":
    print("================================================================")
    print("         A.V.A 2.0 FASTAPI REST API INTEGRATION TESTS           ")
    print("================================================================")
    test_health()
    test_settings_api()
    test_presence_api()
    test_blackboard_api()
    test_events_trigger_api()
    test_chat_dual_layer_api()
    print("\n================================================================")
    print("           ALL REST ENDPOINTS VERIFIED AND PASSING!             ")
    print("================================================================")
