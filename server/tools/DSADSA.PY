from ytmusicapi import YTMusic

def get_yt_music_url(search_term):
    ytmusic = YTMusic() # For public info, no auth required
    search_results = ytmusic.search(search_term)
    first_result = search_results[0]
    video_id = first_result['videoId']
    yt_music_url = f"https://music.youtube.com/watch?v={video_id}"
    return yt_music_url
