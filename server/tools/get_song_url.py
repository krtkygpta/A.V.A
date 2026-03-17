from ytmusicapi import YTMusic

def song_url(song_name):
    """
    Search for a song on YouTube Music and return its URL.
    
    Args:
        song_name (str): Name of the song to search for
        
    Returns:
        str: YouTube Music URL of the closest match, or None if not found
    """
    try:
        ytmusic = YTMusic()
        results = ytmusic.search(song_name, filter="songs", limit=1)
        
        if results:
            video_id = results[0]['videoId']
            return f"https://music.youtube.com/watch?v={video_id}"
        else:
            return None
    except Exception as e:
        print(f"Error: {e}")
        return None

# Example usage
if __name__ == "__main__":
    url = get_song_url("Photograph by Ed-Sheeran")
    if url:
        print(f"Found: {url}")
    else:
        print("Song not found")