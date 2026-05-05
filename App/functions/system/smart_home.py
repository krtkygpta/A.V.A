"""Merged SmartHome.py containing AC and Lights control"""

import asyncio
import json
import threading

from pywizlight import PilotBuilder, discovery, wizlight

asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


main_dict = {"Lights": "d8a0118d79e9", "Lamp": "d8a011fe9baf"}


bulbs_ip_dict = {"Lights": "192.168.1.101", "Lamp": "192.168.1.100"}

# Persistent event loop — shared across all async light operations
_loop = None
_loop_thread = None


def _get_loop():
    """Get or create a persistent asyncio event loop running on a background thread."""
    global _loop, _loop_thread
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
        _loop_thread = threading.Thread(target=_loop.run_forever, daemon=True)
        _loop_thread.start()
    return _loop


def scan_and_store_bulbs_sync(retries=3, timeout=15):
    async def async_scan():
        global bulbs_ip_dict

        for attempt in range(1, retries + 1):
            try:
                bulbs = await asyncio.wait_for(
                    discovery.discover_lights(broadcast_space="192.168.1.255"),
                    timeout=timeout,
                )
                for bulb in bulbs:
                    for name, mac in main_dict.items():
                        if bulb.mac == mac:
                            bulbs_ip_dict[name] = bulb.ip
                            break

                if bulbs_ip_dict:
                    print(f"[LightCtrl] Found: {list(bulbs_ip_dict.keys())}")
                    return

            except Exception:
                pass  # silently retry

            if attempt < retries:
                await asyncio.sleep(2)

        print("[LightCtrl] Bulbs not found")

    loop = _get_loop()
    future = asyncio.run_coroutine_threadsafe(async_scan(), loop)
    future.result(timeout=timeout * retries + 10)


def control_bulb_sync(
    light_name: str,
    action: str = "turn_on",
    brightness: int = 255,
    color: tuple = (255, 255, 255),
):
    """
    Synchronous wrapper for controlling bulbs.
    Uses the shared persistent event loop instead of creating a new one each time.
    """

    async def async_control():
        if light_name not in bulbs_ip_dict:
            print(f"[LightCtrl] Light '{light_name}' not found. Run scan first.")
            return False

        ip_address = bulbs_ip_dict[light_name]
        light = wizlight(ip_address)
        brightness_val = max(0, min(brightness, 255))
        try:
            if action == "turn_on":
                await light.turn_on(PilotBuilder(brightness=brightness_val))
                return True
            elif action == "turn_off":
                await light.turn_off()
                return True
            elif action == "set":
                await light.turn_on(PilotBuilder(rgb=color, brightness=brightness_val))
                return True
            else:
                print(f"[LightCtrl] Unknown action: {action}")
            return True
        except Exception as e:
            print(f"[LightCtrl] Control error: {e}")
            return False
        finally:
            await light.async_close()
            return True

    loop = _get_loop()
    future = asyncio.run_coroutine_threadsafe(async_control(), loop)
    result = future.result(timeout=10)
    return bool(result)


def control_lights(Light_name, action, brightness=100, color=(255, 255, 255)):
    """Control smart lights (WiZ bulbs).

    Args:
        Light_name: Name of the light ('Lights' or 'Lamp')
        action: 'turn_on', 'turn_off', or 'set'
        brightness: Brightness level 1-100
        color: RGB tuple for color (only used with 'set' action)
    """
    try:
        brightness = float(brightness) * 2.5
        brightness = int(brightness)

        if isinstance(color, list):
            color = tuple(color)

        if not bulbs_ip_dict:
            scan_and_store_bulbs_sync()
            if not bulbs_ip_dict:
                return json.dumps(
                    {"status": "error", "content": "Couldn't find bulbs on the network"}
                )

        if action == "set":
            if len(color) != 3:
                return json.dumps(
                    {
                        "status": "error",
                        "content": "Invalid color input. Must be 3 integers (R, G, B)",
                    }
                )

            success = control_bulb_sync(Light_name, action, brightness, color)
            if success:
                return json.dumps(
                    {
                        "status": "success",
                        "content": f"Set {Light_name} to brightness {brightness // 2.5:.0f}%",
                    }
                )
            else:
                return json.dumps(
                    {"status": "error", "content": f"Failed to set {Light_name}"}
                )
        else:
            success = control_bulb_sync(Light_name, action)
            if success:
                return json.dumps(
                    {"status": "success", "content": f"{Light_name} {action} completed"}
                )
            else:
                return json.dumps(
                    {"status": "error", "content": f"Failed to {action} {Light_name}"}
                )
    except Exception as e:
        return json.dumps(
            {"status": "error", "content": f"Light control error: {str(e)}"}
        )


# scan_and_store_bulbs_sync()


# ----- LG AC CODE BELOW -----

import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import Any

from aiohttp import ClientSession
from thinqconnect.thinq_api import ThinQApi

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEVICE_TYPE = "DEVICE_AIR_CONDITIONER"


def _load_settings() -> dict:
    pat = os.getenv("pat")
    country_code = os.getenv("country_code")
    if not pat or not country_code:
        raise ValueError(
            "Environment variables 'pat' and 'country_code' are required. "
            "Make sure they are set in settings.json and loaded into the environment."
        )
    return {
        "pat": pat,
        "country_code": country_code,
        "client_id": os.getenv("client_id", str(uuid.uuid4())),
    }


# ---------------------------------------------------------------------------
# Low-level API helpers
# ---------------------------------------------------------------------------


async def _get_api(session: ClientSession) -> ThinQApi:
    cfg = _load_settings()
    return ThinQApi(
        session=session,
        access_token=cfg["pat"],
        country_code=cfg["country_code"],
        client_id=cfg["client_id"],
    )


def _extract_device_id(device: dict) -> str:
    for key in ("deviceId", "device_id", "id", "deviceID"):
        if key in device:
            return device[key]
    raise RuntimeError(f"Could not find a device ID field in: {device}")


def _get_device_type(device: dict) -> str:
    info = device.get("deviceInfo", {})
    return (
        info.get("deviceType")
        or info.get("device_type")
        or device.get("deviceType")
        or device.get("device_type")
        or device.get("type")
        or ""
    )


def _get_device_alias(device: dict) -> str:
    info = device.get("deviceInfo", {})
    return info.get("alias") or device.get("alias") or device.get("name") or "Unnamed"


async def _list_ac_devices(api: ThinQApi) -> list[dict]:
    response = await api.async_get_device_list()
    if isinstance(response, dict):
        for key in ("result", "devices", "items", "list", "data"):
            if key in response and isinstance(response[key], list):
                response = response[key]
                break
    devices = response if isinstance(response, list) else []
    if not devices:
        raise RuntimeError(
            "Device list came back empty. "
            "Check that your PAT and country_code in settings.json are correct."
        )
    AC_KEYWORDS = ("air_conditioner", "airconditioner", "air conditioner", "ac")
    ac_devices = []
    for d in devices:
        t = _get_device_type(d).lower()
        if t == DEVICE_TYPE.lower() or any(kw in t for kw in AC_KEYWORDS):
            ac_devices.append(d)
    if not ac_devices:
        all_types = [_get_device_type(d) for d in devices]
        raise RuntimeError(f"No air conditioner found. Types on account: {all_types}")
    return ac_devices


_selected_device: dict | None = None
_current_target_ac_name: str | None = None


async def _find_ac_device_id(api: ThinQApi) -> str:
    global _selected_device, _current_target_ac_name

    target_name = _current_target_ac_name
    if not target_name:
        try:
            cfg = _load_settings()
            target_name = cfg.get("main_ac_alias")
        except Exception:
            pass

    ac_devices = await _list_ac_devices(api)

    if target_name:
        for d in ac_devices:
            if _get_device_alias(d).lower() == target_name.lower():
                return _extract_device_id(d)
        for d in ac_devices:
            if target_name.lower() in _get_device_alias(d).lower():
                return _extract_device_id(d)

    # Auto-select if only one AC, or use previously selected
    if _selected_device:
        return _extract_device_id(_selected_device)

    if len(ac_devices) == 1:
        _selected_device = ac_devices[0]
        print(f"[AC] Auto-selected: {_get_device_alias(_selected_device)}")
        return _extract_device_id(_selected_device)

    aliases = [_get_device_alias(d) for d in ac_devices]
    raise RuntimeError(
        f"Multiple ACs found, set 'main_ac_alias' in settings.json. Available: {aliases}"
    )


# ---------------------------------------------------------------------------
# Unified LGTHINQAC function
# ---------------------------------------------------------------------------

# Valid options for enum-like parameters
_VALID_MODES = {"COOL", "AIR_DRY", "FAN"}
_VALID_SPEEDS = {"LOW", "MID", "HIGH"}
_VALID_ACTIONS = {
    "get_status",
    "get_air_quality",
    "get_filter_info",
    "list_devices",
    "set_power",
    "set_mode",
    "set_temperature",
    "set_fan_speed",
    "set_fan_step",
    "set_wind_direction",
    "set_power_save",
    "set_display_light",
    "set_sleep_timer",
    "set_schedule_on",
    "set_schedule_off",
}


async def LGTHINQAC(
    action: str,
    # Power / mode
    on: bool = None,
    mode: str = None,
    # Temperature
    temperature: float = None,
    unit: str = "C",
    # Fan
    speed: str = None,
    step: int = None,
    # Wind direction
    up_down: str = None,
    left_right: str = None,
    # Feature toggles
    enabled: bool = None,  # power_save
    # Timer / schedule
    hours: int = None,
    minutes: int = None,
    hour: int = None,
    minute: int = None,
    # Device targeting
    ac_name: str = None,
) -> dict:
    """
    Single entry-point for all LG ThinQ AC operations.

    action options
    --------------
    get_status          – full device status
    get_air_quality     – PM / humidity / odor sensors
    get_filter_info     – filter usage & remaining life
    list_devices        – all ThinQ devices on the account

    set_power           – on=True/False
    set_mode            – mode=COOL|HEAT|AIR_DRY|AIR_CLEAN|FAN
    set_temperature     – temperature=<float>, unit=C|F
    set_fan_speed       – speed=LOW|MID|HIGH|AUTO
    set_fan_step        – step=1-8
    set_wind_direction  – up_down=SWING|OFF|FIXED, left_right=SWING|OFF|FIXED
    set_power_save      – enabled=True/False
    set_display_light   – on=True/False
    set_sleep_timer     – hours=<int>, minutes=<int>
    set_schedule_on     – hour=<int>, minute=<int>
    set_schedule_off    – hour=<int>, minute=<int>
    """
    global _current_target_ac_name
    _current_target_ac_name = ac_name

    action = action.strip().lower()

    if action not in _VALID_ACTIONS:
        return {
            "error": f"Unknown action '{action}'. Valid actions: {sorted(_VALID_ACTIONS)}"
        }

    # --- Read-only queries ---------------------------------------------------

    if action == "get_status":
        async with ClientSession() as session:
            api = await _get_api(session)
            device_id = await _find_ac_device_id(api)
            status = await api.async_get_device_status(device_id)
            return {"device_id": device_id, "status": status}

    if action == "get_air_quality":
        async with ClientSession() as session:
            api = await _get_api(session)
            device_id = await _find_ac_device_id(api)
            status = await api.async_get_device_status(device_id)
            aq = (
                status.get("airQualitySensor", status.get("air_quality_sensor", {}))
                if isinstance(status, dict)
                else {}
            )
            return {"device_id": device_id, "air_quality": aq}

    if action == "get_filter_info":
        async with ClientSession() as session:
            api = await _get_api(session)
            device_id = await _find_ac_device_id(api)
            status = await api.async_get_device_status(device_id)
            fi = (
                status.get("filterInfo", status.get("filter_info", {}))
                if isinstance(status, dict)
                else {}
            )
            return {"device_id": device_id, "filter_info": fi}

    if action == "list_devices":
        async with ClientSession() as session:
            api = await _get_api(session)
            raw = await api.async_get_device_list()
            return {"raw_response": raw}

    # --- Control commands ----------------------------------------------------

    async with ClientSession() as session:
        api = await _get_api(session)
        device_id = await _find_ac_device_id(api)

        if action == "set_power":
            if on is None:
                return {"error": "set_power requires 'on' (bool)."}
            command = {
                "operation": {"airConOperationMode": "POWER_ON" if on else "POWER_OFF"}
            }

        elif action == "set_mode":
            if mode is None:
                return {"error": "set_mode requires 'mode'."}
            mode = mode.upper()
            if mode not in _VALID_MODES:
                return {
                    "error": f"Invalid mode '{mode}'. Choose from {sorted(_VALID_MODES)}."
                }
            command = {"airConJobMode": {"currentJobMode": mode}}

        elif action == "set_temperature":
            if temperature is None:
                return {"error": "set_temperature requires 'temperature'."}
            unit = unit.upper()
            if unit not in ("C", "F"):
                return {"error": "unit must be 'C' or 'F'."}
            key = "targetTemperature" if unit == "C" else "targetTemperatureF"
            command = {"temperature": {key: temperature}}

        elif action == "set_fan_speed":
            if speed is None:
                return {"error": "set_fan_speed requires 'speed'."}
            speed = speed.upper()
            if speed not in _VALID_SPEEDS:
                return {
                    "error": f"Invalid speed '{speed}'. Choose from {sorted(_VALID_SPEEDS)}."
                }
            command = {"airFlow": {"windStrength": speed}}

        elif action == "set_fan_step":
            if step is None:
                return {"error": "set_fan_step requires 'step'."}
            if not (1 <= step <= 8):
                return {"error": "step must be between 1 and 8."}
            command = {"airFlow": {"windStep": step}}

        elif action == "set_wind_direction":
            if up_down is None and left_right is None:
                return {
                    "error": "set_wind_direction requires at least one of 'up_down' or 'left_right'."
                }
            wind_dir = {}
            if up_down is not None:
                wind_dir["windRotateUpDown"] = up_down.upper()
            if left_right is not None:
                wind_dir["windRotateLeftRight"] = left_right.upper()
            command = {"windDirection": wind_dir}

        elif action == "set_power_save":
            if enabled is None:
                return {"error": "set_power_save requires 'enabled' (bool)."}
            command = {"powerSave": {"powerSaveEnabled": enabled}}

        elif action == "set_display_light":
            if on is None:
                return {"error": "set_display_light requires 'on' (bool)."}
            command = {"display": {"displayLight": "ON" if on else "OFF"}}

        elif action == "set_sleep_timer":
            if hours is None or minutes is None:
                return {"error": "set_sleep_timer requires 'hours' and 'minutes'."}
            command = {
                "sleepTimer": {
                    "sleepTimerRelativeHourToStop": hours,
                    "sleepTimerRelativeMinuteToStop": minutes,
                }
            }

        elif action == "set_schedule_on":
            if hour is None or minute is None:
                return {"error": "set_schedule_on requires 'hour' and 'minute'."}
            command = {
                "timer": {"absoluteHourToStart": hour, "absoluteMinuteToStart": minute}
            }

        elif action == "set_schedule_off":
            if hour is None or minute is None:
                return {"error": "set_schedule_off requires 'hour' and 'minute'."}
            command = {
                "timer": {"absoluteHourToStop": hour, "absoluteMinuteToStop": minute}
            }

        result = await api.async_post_device_control(device_id, command)

        if not result:
            return {"status": "success", "message": f"{action} command sent"}

        if isinstance(result, dict) and not result.get("result"):
            return {"status": "success", "message": f"{action} command sent"}

        return {"device_id": device_id, "command": command, "result": result}


# ---------------------------------------------------------------------------
# Single tool schema definition
# ---------------------------------------------------------------------------

AC_TOOLS = [
    {
        "name": "LGTHINQAC",
        "description": (
            "Control or query an LG ThinQ air conditioner. "
            "Pass 'action' to select the operation, then supply only the "
            "parameters that action requires.\n\n"
            "READ actions (no extra params needed):\n"
            "  get_status        – full device state\n"
            "  get_air_quality   – PM1/2.5/10, humidity, odor\n"
            "  get_filter_info   – filter usage & remaining life\n"
            "  list_devices      – all ThinQ devices on account\n\n"
            "WRITE actions:\n"
            "  set_power           → on (bool)\n"
            "  set_mode            → mode (COOL|HEAT|AIR_DRY|AIR_CLEAN|FAN)\n"
            "  set_temperature     → temperature (float)\n"
            "  set_fan_speed       → speed (LOW|MID|HIGH|AUTO)\n"
            "  set_fan_step        → step (1-8)\n"
            "  set_wind_direction  → up_down, left_right (SWING|OFF|FIXED)\n"
            "  set_power_save      → enabled (bool)\n"
            "  set_display_light   → on (bool)\n"
            "  set_sleep_timer     → hours, minutes\n"
            "  set_schedule_on     → hour, minute\n"
            "  set_schedule_off    → hour, minute"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "The operation to perform.",
                    "enum": sorted(_VALID_ACTIONS),
                },
                "on": {
                    "type": "boolean",
                    "description": "Used by set_power (power on/off) and set_display_light (display on/off).",
                },
                "mode": {
                    "type": "string",
                    "enum": ["COOL", "HEAT", "AIR_DRY", "AIR_CLEAN", "FAN"],
                    "description": "Operating mode — used by set_mode.",
                },
                "temperature": {
                    "type": "number",
                    "description": "Target temperature — used by set_temperature.",
                },
                "unit": {
                    "type": "string",
                    "description": "Temperature unit — used by set_temperature. Defaults to C.",
                },
                "speed": {
                    "type": "string",
                    "enum": ["LOW", "MID", "HIGH", "AUTO"],
                    "description": "Fan speed by name — used by set_fan_speed.",
                },
                "step": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 8,
                    "description": "Fan speed by step 1-8 — used by set_fan_step.",
                },
                "up_down": {
                    "type": "string",
                    "enum": ["SWING", "OFF", "FIXED"],
                    "description": "Vertical louver direction — used by set_wind_direction.",
                },
                "left_right": {
                    "type": "string",
                    "enum": ["SWING", "OFF", "FIXED"],
                    "description": "Horizontal louver direction — used by set_wind_direction.",
                },
                "enabled": {
                    "type": "boolean",
                    "description": "Enable/disable power-save mode — used by set_power_save.",
                },
                "hours": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 23,
                    "description": "Hours component — used by set_sleep_timer.",
                },
                "minutes": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 59,
                    "description": "Minutes component — used by set_sleep_timer.",
                },
                "hour": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 23,
                    "description": "Hour of day — used by set_schedule_on / set_schedule_off.",
                },
                "minute": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 59,
                    "description": "Minute of hour — used by set_schedule_on / set_schedule_off.",
                },
                "ac_name": {
                    "type": "string",
                    "description": "Optional: name of a specific AC to target. Leave blank for the default/main AC.",
                },
            },
            "required": ["action"],
        },
    }
]


# ---------------------------------------------------------------------------
# Tool dispatcher — call this from your LLM tool-use handler
# ---------------------------------------------------------------------------

import inspect


async def execute_tool(tool_name: str, tool_input: dict):
    """Route tool calls from the LLM to LGTHINQAC (the only AC tool)."""
    if tool_name != "LGTHINQAC":
        return {"error": f"Unknown tool '{tool_name}'"}

    # Coerce string booleans that some LLMs emit
    for bool_param in ("on", "enabled"):
        if isinstance(tool_input.get(bool_param), str):
            tool_input[bool_param] = tool_input[bool_param].lower() in (
                "true",
                "1",
                "on",
            )

    print(f"[EXECUTOR] LGTHINQAC → {tool_input}")
    # In execute_tool — replace the result normalization block
    try:
        result = await LGTHINQAC(
            **{
                k: v
                for k, v in tool_input.items()
                if k in inspect.signature(LGTHINQAC).parameters
            }
        )

        print(
            f"[EXECUTOR] Raw result: {result}"
        )  # ← add this to see what the API returns

        if result is None:
            return {"status": "success", "message": "Command sent (no response body)"}

        # Only treat it as success if there's no error key
        if isinstance(result, dict) and "error" in result:
            return result  # pass the error through, don't mask it

        if isinstance(result, dict) and not result.get("result"):
            return {"status": "success", "message": "Command sent"}

        return result

    except Exception as e:
        return {"error": "LGTHINQAC failed", "details": str(e)}


# ---------------------------------------------------------------------------
# CLI helpers (unchanged)
# ---------------------------------------------------------------------------


def _print_result(result: Any):
    print("\n" + json.dumps(result, indent=2, default=str))


def _ask(prompt: str) -> str:
    return input(f"  {prompt}: ").strip()


def _ask_int(prompt: str, default: int) -> int:
    raw = input(f"  {prompt} [{default}]: ").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        print(f"  Invalid, using default: {default}")
        return default


def _ask_float(prompt: str, default: float) -> float:
    raw = input(f"  {prompt} [{default}]: ").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        print(f"  Invalid, using default: {default}")
        return default


def _choose(prompt: str, options: list[str]) -> str:
    print(f"\n  {prompt}")
    for i, opt in enumerate(options, 1):
        print(f"    {i}) {opt}")
    while True:
        raw = input("  choice: ").strip()
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(options):
                return options[idx]
        except ValueError:
            pass
        print("  Invalid, try again.")


def _sep():
    print("  " + "-" * 48)


# ---------------------------------------------------------------------------
# Main interactive menu
# ---------------------------------------------------------------------------


async def _handle(choice: str):
    match choice:
        case "1":
            _print_result(await LGTHINQAC("get_status"))
        case "2":
            _print_result(await LGTHINQAC("get_air_quality"))
        case "3":
            _print_result(await LGTHINQAC("get_filter_info"))
        case "4":
            _print_result(await LGTHINQAC("list_devices"))
        case "5":
            _print_result(await LGTHINQAC("set_power", on=True))
        case "6":
            _print_result(await LGTHINQAC("set_power", on=False))
        case "7":
            mode = _choose(
                "Select mode:", ["COOL", "HEAT", "AIR_DRY", "AIR_CLEAN", "FAN"]
            )
            _print_result(await LGTHINQAC("set_mode", mode=mode))
        case "8":
            temp = _ask_float("Target temperature", 24.0)
            unit = _choose("Unit:", ["C", "F"])
            _print_result(
                await LGTHINQAC("set_temperature", temperature=temp, unit=unit)
            )
        case "9":
            speed = _choose("Fan speed:", ["LOW", "MID", "HIGH", "AUTO"])
            _print_result(await LGTHINQAC("set_fan_speed", speed=speed))
        case "10":
            step = _ask_int("Fan step (1-8)", 3)
            _print_result(await LGTHINQAC("set_fan_step", step=step))
        case "11":
            print("\n  Leave blank to skip an axis.")
            ud = _ask("Up/down  (SWING / OFF / FIXED, or blank)")
            lr = _ask("Left/right (SWING / OFF / FIXED, or blank)")
            _print_result(
                await LGTHINQAC(
                    "set_wind_direction",
                    up_down=ud.upper() if ud else None,
                    left_right=lr.upper() if lr else None,
                )
            )
        case "12":
            state = _choose("Power save:", ["ON", "OFF"])
            _print_result(await LGTHINQAC("set_power_save", enabled=(state == "ON")))
        case "13":
            state = _choose("Display light:", ["ON", "OFF"])
            _print_result(await LGTHINQAC("set_display_light", on=(state == "ON")))
        case "14":
            h = _ask_int("Hours until auto-off", 2)
            m = _ask_int("Minutes until auto-off", 0)
            _print_result(await LGTHINQAC("set_sleep_timer", hours=h, minutes=m))
        case "15":
            h = _ask_int("Turn-on hour   (0-23)", 7)
            m = _ask_int("Turn-on minute (0-59)", 30)
            _print_result(await LGTHINQAC("set_schedule_on", hour=h, minute=m))
        case "16":
            h = _ask_int("Turn-off hour   (0-23)", 22)
            m = _ask_int("Turn-off minute (0-59)", 0)
            _print_result(await LGTHINQAC("set_schedule_off", hour=h, minute=m))
        case "0":
            pass
        case _:
            print("\n  Unknown option.")


async def _pick_device() -> bool:
    global _selected_device
    try:
        async with ClientSession() as session:
            api = await _get_api(session)
            ac_devices = await _list_ac_devices(api)
    except Exception as e:
        print(f"\n  [error fetching devices] {e}")
        return False

    if len(ac_devices) == 1:
        _selected_device = ac_devices[0]
        print(f"\n  Auto-selected: {_get_device_alias(_selected_device)}")
        return True

    print("\n  Multiple ACs found — pick one:")
    for i, d in enumerate(ac_devices, 1):
        alias = _get_device_alias(d)
        did = _extract_device_id(d)
        model = d.get("deviceInfo", {}).get("modelName", "")
        print(f"    {i}) {alias}  ({model})  [{did[:12]}...]")

    while True:
        raw = input("  choice: ").strip()
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(ac_devices):
                _selected_device = ac_devices[idx]
                print(f"  Selected: {_get_device_alias(_selected_device)}")
                return True
        except ValueError:
            pass
        print("  Invalid, try again.")


async def _main():
    global _selected_device
    print("\n" + "=" * 52)
    print("   LG ThinQ AC — test menu")
    print("=" * 52)
    print("  Credentials loaded from settings.json")

    await _pick_device()

    while True:
        active = (
            f"  Active AC : {_get_device_alias(_selected_device)}"
            if _selected_device
            else "  Active AC : none selected"
        )
        print()
        _sep()
        print(active)
        _sep()
        print("  STATUS / INFO")
        print("   1) Get full AC status")
        print("   2) Get air quality")
        print("   3) Get filter info")
        print("   4) List all raw devices")
        _sep()
        print("  POWER")
        print("   5) Turn ON")
        print("   6) Turn OFF")
        _sep()
        print("  CONTROL")
        print("   7) Set mode  (COOL / HEAT / DRY / FAN ...)")
        print("   8) Set temperature")
        print("   9) Set fan speed  (LOW / MID / HIGH / AUTO)")
        print("  10) Set fan step   (1-8)")
        print("  11) Set swing direction")
        _sep()
        print("  SETTINGS")
        print("  12) Power save on/off")
        print("  13) Display light on/off")
        _sep()
        print("  TIMERS")
        print("  14) Sleep timer  (auto turn-off)")
        print("  15) Schedule turn-ON time")
        print("  16) Schedule turn-OFF time")
        _sep()
        print("   S) Switch AC device")
        print("   0) Quit")
        print()

        choice = input("  Enter option: ").strip().upper()

        if choice == "0":
            print("\n  Bye!\n")
            break

        if choice == "S":
            await _pick_device()
            continue

        try:
            await _handle(choice)
        except FileNotFoundError as e:
            print(f"\n  [config error] {e}")
        except RuntimeError as e:
            print(f"\n  [device error] {e}")
        except Exception as e:
            print(f"\n  [error] {type(e).__name__}: {e}")

        input("\n  Press Enter to continue...")


if __name__ == "__main__":
    asyncio.run(_main())
