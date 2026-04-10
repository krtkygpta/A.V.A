from pywizlight import wizlight, discovery, PilotBuilder
import asyncio
import json
import threading

asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


main_dict = {
    'Lights': 'd8a0118d79e9',
    'Lamp': 'd8a011fe9baf'
}


bulbs_ip_dict = {}

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
                    timeout=timeout
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

def control_bulb_sync(light_name: str, action: str = "turn_on", brightness: int = 255, color: tuple = (255, 255, 255)):
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


# scan_and_store_bulbs_sync()
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
            # Try to scan for bulbs if not found
            scan_and_store_bulbs_sync()
            if not bulbs_ip_dict:
                return json.dumps({'status': 'error', 'content': "Couldn't find bulbs on the network"})
        
        if action == "set":
            if len(color) != 3:
                return json.dumps({'status': 'error', 'content': 'Invalid color input. Must be 3 integers (R, G, B)'})
            
            success = control_bulb_sync(Light_name, action, brightness, color)
            if success:
                return json.dumps({'status': 'success', 'content': f'Set {Light_name} to brightness {brightness//2.5:.0f}%'})
            else:
                return json.dumps({'status': 'error', 'content': f'Failed to set {Light_name}'})
        else:
            success = control_bulb_sync(Light_name, action)
            if success:
                return json.dumps({'status': 'success', 'content': f'{Light_name} {action} completed'})
            else:
                return json.dumps({'status': 'error', 'content': f'Failed to {action} {Light_name}'})
    except Exception as e:
        return json.dumps({'status': 'error', 'content': f'Light control error: {str(e)}'})

scan_and_store_bulbs_sync()

if __name__ == "__main__":
    print(control_bulb_sync("Lights", "turn_on"))
