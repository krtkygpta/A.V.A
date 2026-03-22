from pyautogui import press
import requests
import json
import webbrowser
from ytmusicapi import YTMusic

# Singleton YTMusic client — avoids slow re-initialization on every song search
_ytmusic = YTMusic()

def song_url(song_name):
    """
    Search for a song on YouTube Music and return its URL.
    
    Args:
        song_name (str): Name of the song to search for
        
    Returns:
        str: YouTube Music URL of the closest match, or None if not found
    """
    try:
        results = _ytmusic.search(song_name, filter="songs", limit=1)
        
        if results:
            video_id = results[0]['videoId']
            return f"https://music.youtube.com/watch?v={video_id}"
        else:
            return None
    except Exception as e:
        print(f"[Music] Error: {e}")
        return None
# def music(song):
# 	url = 'http://127.0.0.1:5000/edith/tools'

# 	if song == "":
# 		song = 'beliver'
# 	data = {
# 		'tool': 'song_url',
# 		'song_title': song
# 	}
# 	response = requests.post(url, json=data)
# 	if response.status_code != 200:  # Check for successful response
# 		print("Error: Unable to retrieve song URL")
# 		return ""
# 	print((response))
# 	return response.json()



def music_control(action: str, song_name: str = "") -> str:

	if action == "play_new":
		hi = song_url(song_name)
		webbrowser.open(hi)
		return json.dumps({'status': 'success', 'message': f'started playing the song on ytmusic'})
	elif "resume" in action:
		press('playpause')
		return json.dumps({'status': 'success', 'message': 'Music resumed'})
	elif "pause" in action:
		press('playpause')
		return json.dumps({'status': 'success', 'message': 'Music paused'})
	elif action == "add_next":
		return json.dumps({'status': 'success', 'message': f'Added {song_name} to queue'})
	elif action == "previous":
		press('prevtrack')
		return json.dumps({'status': 'success', 'message': 'done'})
	elif action == "next":
		press('nexttrack')
		return json.dumps({'status': 'success', 'message': 'done'})
	else:
		return json.dumps({'status': 'error', 'message': 'Invalid action'})

# music_control("play_new", "shape of you")