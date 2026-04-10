from pyautogui import press
import requests
import json
import webbrowser
from ytmusicapi import YTMusic
import subprocess
import sys

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


class MusicControllerSubprocess:
    def __init__(self):
        self.proc = None
    
    def _ensure_started(self):
        if self.proc is None or self.proc.poll() is not None:
            # Start separate Python interpreter with just the server file
            self.proc = subprocess.Popen(
                [sys.executable, "music_server.py"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
    
    def control(self, action, song_name=None):
        self._ensure_started()
        cmd = {"action": action, "song": song_name}
        self.proc.stdin.write(json.dumps(cmd) + "\n")
        self.proc.stdin.flush()
        result = self.proc.stdout.readline()
        if result:
            return json.loads(result)
        return {"status": "error", "message": "No response from subprocess"}

mc = MusicControllerSubprocess()
