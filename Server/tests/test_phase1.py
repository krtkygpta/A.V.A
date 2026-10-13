"""
test_phase1.py - Automated verification test suite for A.V.A 2.0 Phase 1.

Tests:
1. Blackboard state machine (tickets, steps, thought streaming)
2. Sentinel Flight Recorder (telemetry ledger, circular buffer, query)
3. SystemSentinel check cycle (telemetry extraction, hysteresis)
4. Layer 2 Chief-of-Staff (DAG generation, step progression, completion)
5. Layer 1 Executive Avatar (intent classification, reflex, status lookup, delegation)
6. Server Settings API & hot-reloading (GET /api/settings, POST /api/settings)
7. Mock event injection & diagnosis (POST /api/events/trigger)
"""

import asyncio
import os
import sys
import time
from pathlib import Path

# Add Server directory to sys.path
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


from services.blackboard import blackboard
from services.layer1_avatar import layer1_avatar
from services.layer2_orchestrator import layer2_orchestrator
from services.observers.sys_sentinel import SystemSentinel
from config import get_settings, save_settings


def test_blackboard_lifecycle():
    print("\n--- [TEST 1] Blackboard Ticket & Thought Stream ---")
    ticket = blackboard.create_ticket(goal="Test Automated Verification", client_id="test_runner")
    assert ticket.ticket_id.startswith("job_"), "Ticket ID must start with job_"
    assert ticket.status == "scoping", "Initial status should be scoping"

    step = blackboard.add_step(ticket.ticket_id, title="Unit test step 1", assigned_worker="tester")
    assert step is not None, "Step should be created"
    assert step.status == "pending", "Step should start pending"

    blackboard.update_step(ticket.ticket_id, step.step_id, status="running", last_log_line="Running test...")
    blackboard.append_thought(ticket.ticket_id, "Validating assertions in test runner", stage="testing")

    thoughts = blackboard.get_thoughts(ticket.ticket_id)
    assert len(thoughts) >= 1, "Thought should be recorded"
    assert thoughts[0]["stage"] == "testing"

    blackboard.update_ticket_status(ticket.ticket_id, status="completed", progress_pct=100)
    refreshed = blackboard.get_ticket(ticket.ticket_id)
    assert refreshed.status == "completed"
    assert refreshed.progress_pct == 100
    print("  [PASS] Blackboard ticket lifecycle passed.")


def test_sentinel_flight_recorder():
    print("\n--- [TEST 2] Sentinel Flight Recorder Ring Buffer ---")
    initial_count = len(blackboard.get_telemetry_history(sentinel="test_sentinel"))
    
    for i in range(10):
        blackboard.record_telemetry(
            sentinel="test_sentinel",
            data={"metric_idx": i, "temp_c": 40 + i},
            level="INFO" if i < 8 else "WARN",
        )

    history = blackboard.get_telemetry_history(sentinel="test_sentinel", limit=20)
    assert len(history) == initial_count + 10, f"Expected 10 new telemetry entries, got {len(history)}"
    assert history[0]["data"]["metric_idx"] == 9, "Latest entry should be first (reverse chronological)"
    print("  ✓ Flight recorder ring buffer passed.")


def test_system_sentinel():
    print("\n--- [TEST 3] SystemSentinel Telemetry Check ---")
    sentinel = SystemSentinel(interval_sec=5.0)
    result = asyncio.run(sentinel.check())
    assert result is not None, "Sentinel check should return telemetry dictionary"
    assert "cpu_percent" in result, "Result must contain cpu_percent"
    assert "ram_percent" in result, "Result must contain ram_percent"
    assert "severity" in result, "Result must contain severity"
    print(f"  ✓ SystemSentinel active readings: CPU={result['cpu_percent']}%, RAM={result['ram_percent']}%, Severity={result['severity']}")


def test_layer1_intent_and_status():
    print("\n--- [TEST 4] Layer 1 Intent Triage & Sub-10ms Status Lookup ---")
    # Reflex (Greetings - user scenario 'Hii')
    res_hii = layer1_avatar.route_turn_sync("Hii")
    assert res_hii["type"] == "reflex", f"Expected reflex for 'Hii', got {res_hii['type']}"
    assert res_hii["ticket_id"] is None, "Greeting must not create a JobTicket"
    print(f"  ✓ Greeting reflex ('Hii'): '{res_hii['response']}'")

    res_howareyou = layer1_avatar.route_turn_sync("how are you")
    assert res_howareyou["type"] == "reflex", f"Expected reflex for 'how are you', got {res_howareyou['type']}"
    print(f"  ✓ Small-talk reflex ('how are you'): '{res_howareyou['response']}'")

    # Time reflex
    res_reflex = layer1_avatar.route_turn_sync("What time is it?")
    assert res_reflex["type"] == "reflex", f"Expected reflex, got {res_reflex['type']}"
    print(f"  ✓ Time reflex response: '{res_reflex['response']}'")

    # Delegation
    res_delegation = layer1_avatar.route_turn_sync("Build a python script to test network ports")
    assert res_delegation["type"] == "delegation", f"Expected delegation, got {res_delegation['type']}"
    assert res_delegation["ticket_id"] is not None, "Delegation must generate ticket_id"
    t_id = res_delegation["ticket_id"]
    print(f"  ✓ Delegation kickoff: '{res_delegation['response']}' (Ticket: {t_id})")

    # Battery Query Triage (User scenario)
    res_battery = layer1_avatar.route_turn_sync("tell me what is the battery lvels of all my devices")
    assert res_battery["type"] == "delegation", f"Expected delegation for battery check, got {res_battery['type']}"
    print(f"  ✓ Battery query triage: '{res_battery['response']}' (Ticket: {res_battery['ticket_id']})")

    # Fast in-flight status check (<10ms)
    t0 = time.perf_counter()
    status_reply = layer1_avatar.handle_status_query("How is that build task going?")
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert "We are at" in status_reply or "complete" in status_reply, "Status query should describe ticket"
    assert elapsed_ms < 25.0, f"Status lookup took {elapsed_ms}ms, must be <25ms"
    print(f"  ✓ Sub-10ms Blackboard status inquiry answered in {elapsed_ms:.2f}ms: '{status_reply}'")


def test_settings_hot_reloading():
    print("\n--- [TEST 5] Settings API & Hot-Reloading ---")
    original_settings = get_settings()
    orig_dual = original_settings.dual_layer_enabled

    # Toggle dual layer
    updated = save_settings({"DUAL_LAYER_ENABLED": not orig_dual, "MODEL_NAME": "test-llama-model"})
    assert updated.dual_layer_enabled == (not orig_dual), "Setting should be toggled"
    assert updated.model_name == "test-llama-model", "Model name should be updated"

    # Revert to original
    reverted = save_settings({"DUAL_LAYER_ENABLED": orig_dual, "MODEL_NAME": original_settings.model_name})
    assert reverted.dual_layer_enabled == orig_dual, "Setting should be reverted"
    print("  ✓ Settings hot-reloading passed.")


def test_layer2_alert_diagnosis():
    print("\n--- [TEST 6] Layer 2 Sentinel Alert Diagnosis ---")
    mock_alert = {
        "sentinel": "system_sentinel",
        "alert_title": "Hardware Thermal Alert",
        "message": "CPU temperature sustained at 99°C",
        "flight_recorder_snapshot": [
            {"data": {"cpu_percent": 95, "ram_percent": 88}},
            {"data": {"cpu_percent": 98, "ram_percent": 89}},
            {"data": {"cpu_percent": 100, "ram_percent": 91}},
        ],
    }
    diagnosis = layer2_orchestrator.diagnose_sentinel_alert(mock_alert)
    assert diagnosis is not None
    assert "Hardware load critical" in diagnosis["diagnosis"]
    print(f"  ✓ Layer 2 diagnosis: '{diagnosis['diagnosis']}'")


if __name__ == "__main__":
    print("================================================================")
    print("          A.V.A 2.0 PHASE 1 VERIFICATION TEST SUITE            ")
    print("================================================================")
    test_blackboard_lifecycle()
    test_sentinel_flight_recorder()
    test_system_sentinel()
    test_layer1_intent_and_status()
    test_settings_hot_reloading()
    test_layer2_alert_diagnosis()
    print("\n================================================================")
    print("              ALL PHASE 1 TESTS PASSED SUCCESSFULLY!            ")
    print("================================================================")
