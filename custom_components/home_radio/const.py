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

KIND_STATION = "station"
KIND_YOUTUBE = "youtube"

CARD_URL = "/home_radio/home-radio-card.js"
