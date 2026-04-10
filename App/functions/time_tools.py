import requests
import json
from datetime import datetime
import re
import threading
import time

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

# Timer functionality (currently referenced out in original code)
# def threadoftimer(timer_length, tool_id):
#     # timer_length = re.sub("/D", "", timer_length)
#     time.sleep(int(timer_length))
#     message_list.append({'role': 'tool', 'content': f'Timer for {timer_length} completed', 'tool_call_id': tool_id})  # Simulating the message_list update
#     # Simulating setting an event
#     # print("timer up")
#     checkevent.set()  # Uncomment if using an actual threading event
# 
# def timer(timer_length, tool_id):
#     thread1090 = threading.Thread(target=threadoftimer, args=(timer_length, tool_id))
#     thread1090.start()
#     # print("HIIIIII")
#     return json.dumps({'status': 'started', 'message': f'please wait for the timer to finish'})
