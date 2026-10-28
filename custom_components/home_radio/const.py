"""Constants for Home Radio."""

DOMAIN = "home_radio"

CONF_TARGET = "target"
CONF_TV_URLS = "tv_urls"

DEFAULT_TARGET = "media_player.vlc"

# The TV app (com.pestpoint.radioauto.tv) serves its station list here while it's running.
TV_STATIONS_PATH = "/stations"
TV_STATIONS_TIMEOUT_S = 5
STATION_SYNC_INTERVAL_S = 15 * 60

STORAGE_VERSION = 1

# YouTube results kept per search; the queue continues through these, then searches for more.
YT_SEARCH_SIZE = 15
YT_MORE_SIZE = 40
# A plausible song length — filters reviews, reactions and hour-long mixes out of autoplay.
YT_MUSIC_MIN_S = 30
YT_MUSIC_MAX_S = 720
RECENT_SEARCHES = 12

SERVICE_PLAY_STATION = "play_station"
SERVICE_PLAY_YOUTUBE = "play_youtube"
SERVICE_SEARCH_YOUTUBE = "search_youtube"
SERVICE_REFRESH_STATIONS = "refresh_stations"
SERVICE_PLAY_CAST = "play_cast"
SERVICE_PLAY_SPOTIFY = "play_spotify"
SERVICE_REMOVE_LIST = "remove_list"

# A Spotify song is matched to the first YouTube result this close to its length.
SPOTIFY_MATCH_RESULTS = 6
SPOTIFY_MATCH_TOLERANCE_S = 20

# Fired while a phone casts through the Home Cast add-on: the add-on owns the queue, so a track
# that ends, Next/Previous pressed in Home Assistant, or another source taking over go to it.
EVENT_CAST = "home_radio_cast"

KIND_STATION = "station"
KIND_YOUTUBE = "youtube"

CARD_URL = "/home_radio/home-radio-card.js"
