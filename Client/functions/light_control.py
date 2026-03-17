from ast import Try
from asyncio.exceptions import TimeoutError
from pywizlight import wizlight, discovery, PilotBuilder
import asyncio
import json

main_dict = {
    'Lights': 'd8a0118d79e9',
    'Lamp': 'd8a011fe9baf'
}


bulbs_ip_dict = {}

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
                    print(f"Found: {list(bulbs_ip_dict.keys())}")
                    return

            except Exception:
                pass  # silently retry

            if attempt < retries:
                await asyncio.sleep(2)

        print("Could not find bulbs. Continuing anyway.")

    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(async_scan())
    finally:
        loop.close()
        asyncio.set_event_loop(None)
def control_bulb_sync(light_name: str, action: str = "turn_on", brightness: int = 255, color: tuple = (255, 255, 255)):
    """
    Synchronous wrapper for controlling bulbs.
    """
    async def async_control():
        if light_name not in bulbs_ip_dict:
            print(f"Light '{light_name}' not found in the discovered bulbs. Run the scan first.")
            return False


        ip_address = bulbs_ip_dict[light_name]
        light = wizlight(ip_address)
        brightness_val = max(0, min(brightness, 255))
        try:
            if action == "turn_on":
                await light.turn_on(PilotBuilder(brightness=brightness_val))
                # print(f"Turned on '{light_name}' with brightness {brightness_val}.")
                return True
            elif action == "turn_off":
                await light.turn_off()
                # print(f"Turned off '{light_name}'.")
                return True
            elif action == "set":
                await light.turn_on(PilotBuilder(rgb=color, brightness=brightness_val))
                # print(f"Set '{light_name}' to color {color} and brightness {brightness_val}.")
                return True
            else:
                print(f"Unknown action: {action}")
            return True
        except Exception as e:
            print(f"Error controlling light: {e}")
            return False
        finally:
            await light.async_close()
            return True
    a  = asyncio.run(async_control())
    if a:
        return True
    else:
        return False


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
