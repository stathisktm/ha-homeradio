"""The Home Radio player: picks what to play (a station, a YouTube track and the queue after it),
and hands the actual audio to a player on the Home Assistant machine itself — by default the VLC
add-on, which plays through the host's own speakers. State, volume and position mirror that
player; title, artwork and the queue are this entity's own."""

from __future__ import annotations

import asyncio
from datetime import datetime
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components import media_source
from homeassistant.components.media_player import (
    ATTR_MEDIA_VOLUME_LEVEL,
    BrowseMedia,
    MediaClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    STATE_BUFFERING,
    STATE_PAUSED,
    STATE_PLAYING,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, ServiceResponse, SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    CONF_TARGET,
    DEFAULT_TARGET,
    DOMAIN,
    KIND_STATION,
    KIND_YOUTUBE,
    RECENT_SEARCHES,
    SERVICE_PLAY_STATION,
    SERVICE_PLAY_YOUTUBE,
    SERVICE_SEARCH_YOUTUBE,
    YT_MORE_SIZE,
    YT_MUSIC_MAX_S,
    YT_MUSIC_MIN_S,
    YT_SEARCH_SIZE,
)
from .stations import StationStore
from .stream_view import short_url
from .youtube import ResolvedAudio, YoutubeTrack, async_resolve, async_search

_LOGGER = logging.getLogger(__name__)

SUPPORTED = (
    MediaPlayerEntityFeature.PLAY
    | MediaPlayerEntityFeature.PAUSE
    | MediaPlayerEntityFeature.STOP
    | MediaPlayerEntityFeature.NEXT_TRACK
    | MediaPlayerEntityFeature.PREVIOUS_TRACK
    | MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_STEP
    | MediaPlayerEntityFeature.VOLUME_MUTE
    | MediaPlayerEntityFeature.SELECT_SOURCE
    | MediaPlayerEntityFeature.PLAY_MEDIA
    | MediaPlayerEntityFeature.BROWSE_MEDIA
    | MediaPlayerEntityFeature.SEEK
    | MediaPlayerEntityFeature.TURN_ON
    | MediaPlayerEntityFeature.TURN_OFF
)

SOURCE_YOUTUBE = "YouTube"
MAX_SKIPS = 4
# A track that "ends" this soon after starting really failed to play; this many in a row stops
# autoplay instead of racing through the whole queue.
QUICK_END_S = 8
MAX_QUICK_ENDS = 3


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    player = HomeRadioPlayer(entry, data["stations"])
    data["player"] = player
    async_add_entities([player])

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_PLAY_STATION, {vol.Required("station"): cv.string}, "async_play_station",
    )
    platform.async_register_entity_service(
        SERVICE_PLAY_YOUTUBE,
        {vol.Optional("query"): cv.string, vol.Optional("url"): cv.string},
        "async_play_youtube",
    )
    platform.async_register_entity_service(
        SERVICE_SEARCH_YOUTUBE,
        {vol.Required("query"): cv.string},
        "async_search_youtube",
        supports_response=SupportsResponse.ONLY,
    )


class HomeRadioPlayer(MediaPlayerEntity):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_icon = "mdi:radio"
    _attr_supported_features = SUPPORTED
    _attr_media_content_type = MediaType.MUSIC
    # Station list and queue are for the card, not for history.
    _unrecorded_attributes = frozenset({"stations", "queue", "recent_searches"})

    def __init__(self, entry: ConfigEntry, stations: StationStore) -> None:
        self._entry = entry
        self._target: str = entry.options.get(CONF_TARGET) or entry.data.get(CONF_TARGET, DEFAULT_TARGET)
        self._stations = stations
        self._attr_unique_id = f"{entry.entry_id}_player"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)}, name="Home Radio", manufacturer="stathisktm", model="Home Radio",
        )
        self._kind: str | None = None
        self._station: dict[str, str] | None = None
        self._queue: list[YoutubeTrack] = []
        self._index = -1
        self._query: str | None = None
        self._played: set[str] = set()
        self._now: ResolvedAudio | None = None
        self._recent: list[str] = []
        self._search_cache: dict[str, list[YoutubeTrack]] = {}
        self._loading = False
        self._stopping = False
        self._seq = 0
        self._started_at = 0.0
        self._quick_ends = 0

    # ---- lifecycle -------------------------------------------------------------------------

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_track_state_change_event(self.hass, [self._target], self._on_target_change)
        )
        self.async_on_remove(self._stations.add_listener(self.async_write_ha_state))

    @callback
    def _on_target_change(self, event: Event[EventStateChangedData]) -> None:
        old, new = event.data["old_state"], event.data["new_state"]
        # A YouTube track that ran out (playing -> idle, not something we stopped) continues
        # with the next one, like the TV app's autoplay.
        if (
            self._kind == KIND_YOUTUBE
            and not self._loading
            and not self._stopping
            and old is not None
            and new is not None
            and old.state == STATE_PLAYING
            and new.state not in (STATE_PLAYING, STATE_PAUSED, STATE_BUFFERING, STATE_UNAVAILABLE, STATE_UNKNOWN)
        ):
            quick = self.hass.loop.time() - self._started_at < QUICK_END_S
            self._quick_ends = self._quick_ends + 1 if quick else 0
            if self._quick_ends >= MAX_QUICK_ENDS:
                _LOGGER.warning("home_radio: %d tracks in a row stopped right away on %s — autoplay paused",
                                self._quick_ends, self._target)
                self._quick_ends = 0
                self._stopping = True
            else:
                self.hass.async_create_task(self._async_advance(1, autoplay=True))
        self.async_write_ha_state()

    # ---- mirrored state ---------------------------------------------------------------------

    @property
    def _tgt(self):
        return self.hass.states.get(self._target)

    @property
    def available(self) -> bool:
        tgt = self._tgt
        return tgt is not None and tgt.state != STATE_UNAVAILABLE

    @property
    def state(self) -> MediaPlayerState | None:
        if self._loading:
            return MediaPlayerState.BUFFERING
        if self._kind is None:
            return MediaPlayerState.IDLE
        tgt = self._tgt
        if tgt is None:
            return MediaPlayerState.IDLE
        return {
            STATE_PLAYING: MediaPlayerState.PLAYING,
            STATE_PAUSED: MediaPlayerState.PAUSED,
            STATE_BUFFERING: MediaPlayerState.BUFFERING,
        }.get(tgt.state, MediaPlayerState.IDLE)

    def _tattr(self, key: str) -> Any:
        tgt = self._tgt
        return tgt.attributes.get(key) if tgt else None

    @property
    def volume_level(self) -> float | None:
        return self._tattr("volume_level")

    @property
    def is_volume_muted(self) -> bool | None:
        return self._tattr("is_volume_muted")

    @property
    def media_title(self) -> str | None:
        if self._kind == KIND_YOUTUBE and self._now:
            return self._now.title
        if self._kind == KIND_STATION and self._station:
            # VLC reports the stream's ICY "Artist - Track" here when the station sends one.
            live = self._tattr("media_title")
            if live and not str(live).startswith(("http", "/")) and live != self._station["url"].rsplit("/", 1)[-1]:
                return live
            return self._station["name"]
        return None

    @property
    def media_artist(self) -> str | None:
        if self._kind == KIND_YOUTUBE and self._now:
            return self._now.channel
        if self._kind == KIND_STATION and self._station and self.media_title != self._station["name"]:
            return self._station["name"]
        return None

    @property
    def media_image_url(self) -> str | None:
        if self._kind == KIND_YOUTUBE and self._now:
            return self._now.thumbnail
        if self._kind == KIND_STATION and self._station:
            return self._station.get("logo") or None
        return None

    @property
    def media_image_remotely_accessible(self) -> bool:
        return True

    @property
    def media_duration(self) -> int | None:
        if self._kind == KIND_YOUTUBE:
            return self._tattr("media_duration") or (self._now.duration if self._now else None)
        return None

    @property
    def media_position(self) -> int | None:
        return self._tattr("media_position") if self._kind == KIND_YOUTUBE else None

    @property
    def media_position_updated_at(self) -> datetime | None:
        return self._tattr("media_position_updated_at") if self._kind == KIND_YOUTUBE else None

    @property
    def source(self) -> str | None:
        if self._kind == KIND_STATION and self._station:
            return self._station["name"]
        if self._kind == KIND_YOUTUBE:
            return SOURCE_YOUTUBE
        return None

    @property
    def source_list(self) -> list[str]:
        return [s["name"] for s in self._stations.stations]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "kind": self._kind,
            "station_id": self._station["id"] if self._kind == KIND_STATION and self._station else None,
            "youtube_query": self._query,
            "queue_index": self._index if self._kind == KIND_YOUTUBE else None,
            "queue": [t.as_dict() for t in self._queue] if self._kind == KIND_YOUTUBE else [],
            "recent_searches": self._recent,
            "stations": [
                {"id": s["id"], "name": s["name"], "logo": s.get("logo", ""), "category": s.get("category", "")}
                for s in self._stations.stations
            ],
            "target": self._target,
        }

    # ---- playing -----------------------------------------------------------------------------

    async def _send(self, service: str, data: dict[str, Any] | None = None) -> None:
        await self.hass.services.async_call(
            "media_player", service, {ATTR_ENTITY_ID: self._target, **(data or {})}, blocking=True,
        )

    async def _play_url(self, url: str) -> None:
        self._stopping = False
        self._started_at = self.hass.loop.time()
        await self._send("play_media", {"media_content_id": url, "media_content_type": "music"})

    async def _async_start_station(self, station: dict[str, str]) -> None:
        self._seq += 1
        self._kind, self._station, self._now = KIND_STATION, station, None
        self._queue, self._index = [], -1
        self.async_write_ha_state()
        await self._play_url(station["url"])

    async def _async_play_queue_item(self, index: int, skips: int = 0) -> None:
        if not 0 <= index < len(self._queue):
            return
        self._seq += 1
        seq = self._seq
        track = self._queue[index]
        self._kind, self._index, self._loading = KIND_YOUTUBE, index, True
        self._now = ResolvedAudio("", track.title, track.channel, track.duration, track.thumbnail)
        self.async_write_ha_state()
        resolved = await async_resolve(self.hass, track.url)
        if seq != self._seq:
            return  # something else was asked for meanwhile
        if resolved is None:
            self._loading = False
            if skips < MAX_SKIPS and index + 1 < len(self._queue):
                await self._async_play_queue_item(index + 1, skips + 1)
            else:
                self.async_write_ha_state()
            return
        self._now = resolved
        self._played.add(track.video_id)
        try:
            # Short HA-served redirect instead of the ~1100-char googlevideo URL — see stream_view.
            await self._play_url(short_url(self.hass, resolved.stream_url))
        finally:
            self._loading = False
            self.async_write_ha_state()

    async def _async_more_for_queue(self) -> bool:
        """Autoplay ran past the results: fetch a bigger page for the same search (or one built
        from the last title) and append what hasn't been played yet."""
        current = self._queue[self._index] if 0 <= self._index < len(self._queue) else None
        query = self._query or (f"{current.channel} {current.title}" if current else None)
        if not query:
            return False
        known = {t.video_id for t in self._queue} | self._played
        fresh = [t for t in await async_search(self.hass, query, YT_MORE_SIZE) if t.video_id not in known]
        music = [t for t in fresh if YT_MUSIC_MIN_S <= t.duration <= YT_MUSIC_MAX_S] or fresh
        self._queue.extend(music)
        return bool(music)

    async def _async_advance(self, step: int, autoplay: bool = False) -> None:
        if self._kind == KIND_YOUTUBE:
            nxt = self._index + step
            if autoplay:
                # Skip non-music-length results on the way, as the TV app does.
                while 0 <= nxt < len(self._queue) and not (YT_MUSIC_MIN_S <= self._queue[nxt].duration <= YT_MUSIC_MAX_S):
                    nxt += 1
            if nxt >= len(self._queue) and step > 0 and not await self._async_more_for_queue():
                return
            await self._async_play_queue_item(max(0, min(nxt, len(self._queue) - 1)))
        elif self._kind == KIND_STATION and self._station and self._stations.stations:
            ids = [s["id"] for s in self._stations.stations]
            i = ids.index(self._station["id"]) if self._station["id"] in ids else 0
            await self._async_start_station(self._stations.stations[(i + step) % len(ids)])

    def _remember(self, query: str) -> None:
        self._recent = [query] + [q for q in self._recent if q.lower() != query.lower()]
        self._recent = self._recent[:RECENT_SEARCHES]

    async def _async_search(self, query: str) -> list[YoutubeTrack]:
        key = query.strip().lower()
        if key not in self._search_cache:
            self._search_cache[key] = await async_search(self.hass, query, YT_SEARCH_SIZE)
            if len(self._search_cache) > 20:
                self._search_cache.pop(next(iter(self._search_cache)))
        return self._search_cache[key]

    # ---- services ----------------------------------------------------------------------------

    async def async_play_station(self, station: str) -> None:
        found = self._stations.find(station)
        if found is None:
            raise HomeAssistantError(f"Δεν βρέθηκε σταθμός: {station}")
        await self._async_start_station(found)

    async def async_play_youtube(self, query: str | None = None, url: str | None = None) -> None:
        """A search plays its first result and queues the rest; a URL (optionally with the search
        it came from, as the card sends it) plays that video with the search as its queue."""
        if not query and not url:
            raise HomeAssistantError("Δώσε query ή url")
        if query and not url and query.startswith(("http://", "https://")) and "youtu" in query:
            url, query = query, None
        results: list[YoutubeTrack] = []
        if query:
            self._remember(query)
            results = await self._async_search(query)
        start = 0
        if url:
            vid = url.split("v=")[-1].split("&")[0] if "v=" in url else url.rstrip("/").rsplit("/", 1)[-1]
            match = next((i for i, t in enumerate(results) if t.video_id == vid), None)
            if match is None:
                results = [YoutubeTrack(vid, f"https://www.youtube.com/watch?v={vid}", "", "", 0,
                                        f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg")] + results
            else:
                start = match
        if not results:
            raise HomeAssistantError(f"Δεν βρέθηκαν αποτελέσματα για: {query}")
        self._query = query
        self._queue, self._played = list(results), set()
        await self._async_play_queue_item(start)

    async def async_search_youtube(self, query: str) -> ServiceResponse:
        results = await self._async_search(query)
        self._remember(query)
        self.async_write_ha_state()
        return {"query": query, "results": [t.as_dict() for t in results]}

    # ---- media player commands ---------------------------------------------------------------

    async def async_media_play(self) -> None:
        tgt = self._tgt
        if tgt is not None and tgt.state == STATE_PAUSED:
            await self._send("media_play")
        elif self._kind == KIND_STATION and self._station:
            await self._async_start_station(self._station)
        elif self._kind == KIND_YOUTUBE and self._queue:
            # Stream URLs expire, so a stopped track is resolved again.
            await self._async_play_queue_item(max(self._index, 0))
        elif self._stations.stations:
            await self._async_start_station(self._stations.stations[0])

    async def async_media_pause(self) -> None:
        # A live radio stream can't really pause in VLC without drifting behind, so it stops;
        # Play starts it fresh.
        if self._kind == KIND_STATION:
            await self.async_media_stop()
        else:
            # Paused isn't "ended", so autoplay stays armed for when Play resumes the track.
            await self._send("media_pause")

    async def async_media_stop(self) -> None:
        self._stopping = True
        self._seq += 1
        self._loading = False
        await self._send("media_stop")
        self.async_write_ha_state()

    async def async_turn_on(self) -> None:
        await self.async_media_play()

    async def async_turn_off(self) -> None:
        await self.async_media_stop()

    async def async_media_next_track(self) -> None:
        await self._async_advance(1)

    async def async_media_previous_track(self) -> None:
        await self._async_advance(-1)

    async def async_media_seek(self, position: float) -> None:
        await self._send("media_seek", {"seek_position": position})

    async def async_set_volume_level(self, volume: float) -> None:
        await self._send("volume_set", {ATTR_MEDIA_VOLUME_LEVEL: volume})

    async def async_mute_volume(self, mute: bool) -> None:
        await self._send("volume_mute", {"is_volume_muted": mute})

    async def async_select_source(self, source: str) -> None:
        await self.async_play_station(source)

    async def async_play_media(self, media_type: str, media_id: str, **kwargs: Any) -> None:
        if media_source.is_media_source_id(media_id):
            play = await media_source.async_resolve_media(self.hass, media_id, self.entity_id)
            media_id = play.url if play.url.startswith("http") else self.hass.config.api.base_url + play.url
        if media_id.startswith("station:"):
            await self.async_play_station(media_id.removeprefix("station:"))
        elif media_id.startswith("ytq:"):
            await self.async_play_youtube(query=media_id.removeprefix("ytq:"))
        elif media_id.startswith("yt:"):
            video, _, query = media_id.removeprefix("yt:").partition("|")
            await self.async_play_youtube(query=query or None, url=video)
        elif media_type in ("youtube", "yt") or media_id.startswith("youtube:"):
            await self.async_play_youtube(query=media_id.removeprefix("youtube:"))
        elif self._stations.find(media_id):
            await self.async_play_station(media_id)
        elif media_id.startswith(("http://", "https://")) and "youtu" in media_id:
            await self.async_play_youtube(url=media_id)
        else:
            # Any other URL plays as an ad-hoc station.
            await self._async_start_station({"id": media_id, "name": media_id.rsplit("/", 1)[-1] or media_id,
                                             "url": media_id, "logo": "", "category": ""})

    async def async_browse_media(self, media_content_type: str | None = None, media_content_id: str | None = None) -> BrowseMedia:
        def station_item(s: dict[str, str]) -> BrowseMedia:
            return BrowseMedia(
                media_class=MediaClass.CHANNEL, media_content_id=f"station:{s['id']}", media_content_type="station",
                title=s["name"], can_play=True, can_expand=False, thumbnail=s.get("logo") or None,
            )

        stations_dir = BrowseMedia(
            media_class=MediaClass.DIRECTORY, media_content_id="stations", media_content_type="home_radio",
            title="Σταθμοί", can_play=False, can_expand=True, children_media_class=MediaClass.CHANNEL,
            children=[station_item(s) for s in self._stations.stations],
            thumbnail="https://fonts.gstatic.com/s/e/notoemoji/latest/1f4fb/512.png",
        )
        recent_dir = BrowseMedia(
            media_class=MediaClass.DIRECTORY, media_content_id="recent", media_content_type="home_radio",
            title="YouTube — πρόσφατες αναζητήσεις", can_play=False, can_expand=True,
            children_media_class=MediaClass.PLAYLIST,
            children=[
                BrowseMedia(media_class=MediaClass.PLAYLIST, media_content_id=f"ytq:{q}", media_content_type="youtube",
                            title=q, can_play=True, can_expand=False)
                for q in self._recent
            ],
            thumbnail="https://fonts.gstatic.com/s/e/notoemoji/latest/1f3b5/512.png",
        )
        queue_dir = BrowseMedia(
            media_class=MediaClass.DIRECTORY, media_content_id="queue", media_content_type="home_radio",
            title="Ουρά YouTube", can_play=False, can_expand=True, children_media_class=MediaClass.TRACK,
            children=[
                BrowseMedia(media_class=MediaClass.TRACK, media_content_id=f"yt:{t.url}|{self._query or ''}",
                            media_content_type="youtube", title=t.title, can_play=True, can_expand=False,
                            thumbnail=t.thumbnail)
                for t in self._queue
            ],
        )
        folders = {"stations": stations_dir, "recent": recent_dir, "queue": queue_dir}
        if media_content_id in folders:
            return folders[media_content_id]
        shown = [stations_dir, recent_dir] + ([queue_dir] if self._queue else [])
        return BrowseMedia(
            media_class=MediaClass.DIRECTORY, media_content_id="root", media_content_type="home_radio",
            title="Home Radio", can_play=False, can_expand=True,
            children=[
                # The root lists the folders only; each one is expanded on its own request.
                BrowseMedia(media_class=f.media_class, media_content_id=f.media_content_id,
                            media_content_type=f.media_content_type, title=f.title, can_play=False,
                            can_expand=True, thumbnail=f.thumbnail)
                for f in shown
            ],
        )
