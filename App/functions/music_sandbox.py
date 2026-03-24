import subprocess
import json

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
        return json.loads(result)

mc = MusicControllerSubprocess()