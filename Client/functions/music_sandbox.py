import asyncio
import threading
from concurrent.futures import TimeoutError
from playwright.async_api import async_playwright
from ytmusicapi import YTMusic
import time


class MusicController:
    def __init__(self):
        self.ytmusic = YTMusic()
        self.song_queue = []
        self.queue_lock = threading.Lock()
        self.last_song = None
        self.is_navigating = False
        self.browser = None
        self.page = None
        
        # Create dedicated event loop in background thread (isolates from gevent)
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        
        # Initialize browser in the async loop
        self._run_async(self._init_browser())
        self._start_monitor()

    def _run_loop(self):
        """Runs in dedicated thread - isolated from AI's greenlet environment"""
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _run_async(self, coro, timeout=10):
        """Execute async coroutine in the background loop from sync code"""
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=timeout)
        except TimeoutError:
            future.cancel()
            raise

    # ------------------ ASYNC INTERNALS ------------------

    async def _init_browser(self):
        """Async browser initialization"""
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch_persistent_context(
            user_data_dir="./Client/data/yt_profile",
            ignore_default_args=['--mute-audio'],
            headless=True,
            channel="chromium",
            executable_path=r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            args=[
                "--autoplay-policy=no-user-gesture-required",
                "--disable-blink-features=AutomationControlled",
                "--disable-features=AudioServiceOutOfProcess,AudioServiceSandbox",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu"
            ]
        )
        self.page = await self.browser.new_page()
        await self.page.goto("https://music.youtube.com")
        await asyncio.sleep(3)

    async def _monitor_loop(self):
        """Async background monitor - no threading conflicts"""
        while True:
            try:
                if self.is_navigating:
                    await asyncio.sleep(0.5)
                    continue
                
                current = await self.current_song_async()
                if not current or current in ["Nothing playing", "Error fetching current song"]:
                    await asyncio.sleep(1)
                    continue
                
                with self.queue_lock:
                    # Song changed naturally and we have queue items
                    if current != self.last_song and self.song_queue:
                        next_song = self.song_queue.pop(0)
                        print(f"[Queue] Auto-playing: {next_song['title']}")
                        await self._play_song_async(next_song)
                        self.last_song = f"{next_song['title']} — {next_song['artist']}"
                        continue
                    
                    # Update tracking
                    if current != self.last_song:
                        self.last_song = current
                        
            except Exception as e:
                pass
            await asyncio.sleep(1.5)

    async def _play_song_async(self, song_info):
        """Internal async play"""
        self.is_navigating = True
        try:
            await self.page.goto(f"https://music.youtube.com/watch?v={song_info['videoId']}")
            await asyncio.sleep(1)
            self.last_song = f"{song_info['title']} — {song_info['artist']}"
        finally:
            self.is_navigating = False

    async def current_song_async(self):
        try:
            title = await self.page.evaluate("""document.querySelector('ytmusic-player-bar .title')?.innerText || ''""")
            artist = await self.page.evaluate("""document.querySelector('ytmusic-player-bar .byline')?.innerText || ''""")
            return f"{title} — {artist}" if title else "Nothing playing"
        except:
            return "Error fetching current song"

    def _start_monitor(self):
        """Start monitor in the async loop"""
        asyncio.run_coroutine_threadsafe(self._monitor_loop(), self._loop)

    # ------------------ SYNC PUBLIC API (For your AI) ------------------

    def control(self, action, song_name=None):
        """
        SYNCHRONOUS interface for your AI assistant
        No greenlet conflicts - all Playwright operations happen in isolated thread
        """
        try:
            if action == "play_new":
                if not song_name:
                    return "Error: song_name required"
                return self._run_async(self._play_new_async(song_name))
            
            elif action == "play_next":  # Add to queue and play immediately
                if not song_name:
                    return "Error: song_name required"
                return self._run_async(self._play_next_async(song_name))
            
            elif action == "add_queue":  # Add to end of queue
                if not song_name:
                    return "Error: song_name required"
                return self._add_queue_sync(song_name)

            elif action == "resume":
                return self._run_async(self._resume_async())
            
            elif action == "pause":
                return self._run_async(self._pause_async())
            
            elif action == "next":
                return self._run_async(self._next_async())
            
            elif action == "previous":
                return self._run_async(self._previous_async())
            
            elif action == "current":
                return self._run_async(self.current_song_async())
            
            elif action == "queue":
                return self._show_queue_sync()
            
            elif action == "clear_queue":
                return self._clear_queue_sync()

            else:
                return f"Unknown action: {action}"
                
        except Exception as e:
            return f"Error: {str(e)}"

    # ------------------ ACTION IMPLEMENTATIONS ------------------

    async def _play_new_async(self, query):
        """Play immediately, clearing queue"""
        with self.queue_lock:
            self.song_queue.clear()
        
        results = self.ytmusic.search(query, filter="songs")
        if not results:
            return "No results found"
        
        song = {
            'videoId': results[0]['videoId'],
            'title': results[0]['title'],
            'artist': results[0]['artists'][0]['name'] if results[0].get('artists') else 'Unknown'
        }
        
        await self._play_song_async(song)
        return f"Playing: {song['title']} — {song['artist']}"

    async def _play_next_async(self, query):
        """Add to front of queue and skip to it"""
        results = self.ytmusic.search(query, filter="songs")
        if not results:
            return "No results found"
        
        song = {
            'videoId': results[0]['videoId'],
            'title': results[0]['title'],
            'artist': results[0]['artists'][0]['name'] if results[0].get('artists') else 'Unknown'
        }
        
        with self.queue_lock:
            self.song_queue.insert(0, song)
        
        return await self._next_async()

    def _add_queue_sync(self, query):
        """Add to end of queue (sync version - no browser interaction)"""
        results = self.ytmusic.search(query, filter="songs")
        if not results:
            return "No results found"
        
        song = {
            'videoId': results[0]['videoId'],
            'title': results[0]['title'],
            'artist': results[0]['artists'][0]['name'] if results[0].get('artists') else 'Unknown'
        }
        
        with self.queue_lock:
            self.song_queue.append(song)
        
        return f"Added to queue ({len(self.song_queue)}): {song['title']} — {song['artist']}"

    async def _next_async(self):
        """Play next from queue or native next"""
        with self.queue_lock:
            if self.song_queue:
                next_song = self.song_queue.pop(0)
                self.is_navigating = True
                try:
                    await self.page.goto(f"https://music.youtube.com/watch?v={next_song['videoId']}")
                    await asyncio.sleep(0.5)
                    self.last_song = f"{next_song['title']} — {next_song['artist']}"
                    return f"Playing from queue: {next_song['title']} — {next_song['artist']}"
                finally:
                    self.is_navigating = False
        
        # No queue items - use native
        await self.page.keyboard.press("Shift+N")
        await asyncio.sleep(0.5)
        return "Next song (auto-play)"

    async def _previous_async(self):
        await self.page.keyboard.press("Shift+P")
        return "Previous song"

    async def _pause_async(self):
        await self.page.keyboard.press("Space")
        return "Paused"

    async def _resume_async(self):
        await self.page.keyboard.press("Space")
        return "Resumed"

    def _show_queue_sync(self):
        with self.queue_lock:
            if not self.song_queue:
                return "Queue is empty"
            return [f"{i+1}. {s['title']} — {s['artist']}" for i, s in enumerate(self.song_queue)]

    def _clear_queue_sync(self):
        with self.queue_lock:
            count = len(self.song_queue)
            self.song_queue.clear()
        return f"Cleared {count} songs from queue"

    def close(self):
        """Cleanup"""
        try:
            self._run_async(self.browser.close(), timeout=5)
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=2)
        except:
            pass


# ================== USAGE (Same as before) ==================
mc = MusicController()
if __name__ == "__main__":
    
    # Your AI calls this - completely synchronous, no threading issues
    print(mc.control("play_new", "Ed Sheeran Shape of You"))
    time.sleep(2)
    print(mc.control("add_queue", "Taylor Swift"))
    print(mc.control("add_queue", "Adele"))
    print(mc.control("queue"))
    print(mc.control("current"))