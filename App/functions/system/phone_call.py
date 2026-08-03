import json
import os
from html import escape

import requests


def make_phone_call(phone_number: str, message: str, voice: str = "alice") -> str:
    """
    Place an outbound phone call using Twilio-compatible credentials.

    Required environment variables:
    TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER
    """
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    from_number = os.getenv("TWILIO_FROM_NUMBER")

    if not account_sid or not auth_token or not from_number:
        return json.dumps(
            {
                "status": "error",
                "content": (
                    "Phone calling is not configured. Set TWILIO_ACCOUNT_SID, "
                    "TWILIO_AUTH_TOKEN, and TWILIO_FROM_NUMBER."
                ),
            }
        )

    phone_number = (phone_number or "").strip()
    message = (message or "").strip()
    if not phone_number or not message:
        return json.dumps(
            {"status": "error", "content": "phone_number and message are required"}
        )

    twiml = f"<Response><Say voice=\"{escape(voice)}\">{escape(message)}</Say></Response>"
    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Calls.json"

    try:
        response = requests.post(
            url,
            data={"To": phone_number, "From": from_number, "Twiml": twiml},
            auth=(account_sid, auth_token),
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        return json.dumps(
            {
                "status": "success",
                "content": {
                    "call_sid": data.get("sid"),
                    "to": phone_number,
                    "state": data.get("status"),
                },
            }
        )
    except requests.RequestException as exc:
        return json.dumps({"status": "error", "content": str(exc)})
