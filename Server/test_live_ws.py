import asyncio
import json
import websockets
import httpx

async def run_live_simulation():
    uri = "ws://127.0.0.1:8765/ws/device"

    # Connect Client 1 (Phone)
    ws_phone = await websockets.connect(uri)
    await ws_phone.send(json.dumps({
        "type": "register",
        "device_id": "phone_kartikey",
        "name": "Kartikey Phone",
        "device_type": "android",
        "capabilities": ["get_location", "get_battery_status", "ring_device"]
    }))
    phone_ack = json.loads(await ws_phone.recv())
    print("Phone Connected:", phone_ack.get("device_id"), phone_ack.get("status"))

    # Connect Client 2 (Laptop Web)
    ws_web = await websockets.connect(uri)
    await ws_web.send(json.dumps({
        "type": "register",
        "device_id": "web_laptop",
        "name": "Kartikey Laptop",
        "device_type": "web",
        "capabilities": ["get_location", "music"]
    }))
    web_ack = json.loads(await ws_web.recv())
    print("Web Laptop Connected:", web_ack.get("device_id"), web_ack.get("status"))

    # Phone RPC Responder Loop
    async def phone_worker():
        try:
            while True:
                msg = json.loads(await ws_phone.recv())
                if msg.get("type") == "tool_call":
                    rpc_id = msg.get("rpc_id")
                    tool = msg.get("tool")
                    print(f">>> Phone received tool call: {tool}")
                    if tool == "get_location":
                        res = {"latitude": 28.6139, "longitude": 77.2090, "address": "Connaught Place, New Delhi, India"}
                    elif tool == "get_battery_status":
                        res = {"battery": 88, "charging": True}
                    elif tool == "ring_device":
                        res = {"ringing": True, "duration": 10, "message": "Ringing loudly on Android"}
                    else:
                        res = {"status": "executed"}
                    await ws_phone.send(json.dumps({
                        "type": "tool_result",
                        "rpc_id": rpc_id,
                        "result": res
                    }))
                    print(f"<<< Phone replied for: {tool}")
        except asyncio.CancelledError:
            pass

    phone_task = asyncio.create_task(phone_worker())

    # Now verify Device Registry endpoint lists both
    async with httpx.AsyncClient() as client:
        r = await client.get("http://127.0.0.1:8765/devices/list")
        devs = r.json().get("devices", [])
        print(f"Online Devices Count: {len(devs)}")
        for d in devs:
            print(f" - {d.get('device_id')} ({d.get('name')}) [{d.get('device_type')}] online={d.get('online')}")

        # Cross-device execute 1: Locate phone
        r1 = await client.post("http://127.0.0.1:8765/devices/execute", json={
            "target_device": "phone_kartikey",
            "tool_name": "get_location",
            "caller_id": "web_laptop"
        })
        print("Cross-device Location:", r1.json())

        # Cross-device execute 2: Battery of phone
        r2 = await client.post("http://127.0.0.1:8765/devices/execute", json={
            "target_device": "phone_kartikey",
            "tool_name": "get_battery_status",
            "caller_id": "web_laptop"
        })
        print("Cross-device Battery:", r2.json())

        # Cross-device execute 3: Ring phone
        r3 = await client.post("http://127.0.0.1:8765/devices/execute", json={
            "target_device": "phone_kartikey",
            "tool_name": "ring_device",
            "caller_id": "web_laptop"
        })
        print("Cross-device Ring:", r3.json())

    phone_task.cancel()
    await ws_phone.close()
    await ws_web.close()

if __name__ == "__main__":
    asyncio.run(run_live_simulation())
