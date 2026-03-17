import requests
import json
from datetime import datetime


def timedate(type='both'):
    """Get current time and/or date.
    
    Args:
        type: 'time', 'date', or 'both' (default)
    """
    try:
        url = 'http://127.0.0.1:5000/edith/tools'
        data = {
            'tool': 'datetime',
            'type': type  # Fixed: actually pass the type parameter
        }
        response = requests.post(url, json=data, timeout=5)
        return response.json()
    except requests.exceptions.RequestException:
        # Fallback to local time if server is unavailable
        now = datetime.now()
        if type == 'time':
            return json.dumps({'status': 'success', 'content': now.strftime("%H:%M:%S")})
        elif type == 'date':
            return json.dumps({'status': 'success', 'content': now.strftime("%Y-%m-%d")})
        else:  # 'both'
            return json.dumps({'status': 'success', 'content': {
                'time': now.strftime("%H:%M:%S"),
                'date': now.strftime("%Y-%m-%d"),
                'datetime': now.strftime("%Y-%m-%d %H:%M:%S")
            }})


# print(type(str(timedate())))
# print(str(timedate()))