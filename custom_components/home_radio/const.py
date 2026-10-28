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
SERVICE_GET_LIST = "get_list"

# Mixing YouTube tracks: the next one is resolved this long before the current one ends (so it
# starts at once), then the last DEFAULT_CROSSFADE_S fade out and the new one fades in.
DEFAULT_CROSSFADE_S = 6
MAX_CROSSFADE_S = 12
PREPARE_AHEAD_S = 25
PREPARED_MAX_AGE_S = 300
# Right after a track starts the player may still report the last one's position: not trusted.
SETTLE_S = 15
# The fade goes down to this share of the volume; the new track comes up from it.
FADE_FLOOR = 0.15
FADE_STEP_S = 0.25
# Volume changes per fade, however long it is (each is one command to VLC).
FADE_STEPS = 16
FADE_IN_S = 2.5

# A Spotify song is matched to the first YouTube result this close to its length.
SPOTIFY_MATCH_RESULTS = 6
SPOTIFY_MATCH_TOLERANCE_S = 20

# Fired while a phone casts through the Home Cast add-on: the add-on owns the queue, so a track
# that ends, Next/Previous pressed in Home Assistant, or another source taking over go to it.
EVENT_CAST = "home_radio_cast"

KIND_STATION = "station"
KIND_YOUTUBE = "youtube"

CARD_URL = "/home_radio/home-radio-card.js"
