"""The Home Radio player: picks what to play (a station, a YouTube track and the queue after it),
and hands the actual audio to a player on the Home Assistant machine itself — by default the VLC
add-on, which plays through the host's own speakers. State, volume and position mirror that
player; title, artwork and the queue are this entity's own."""

from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta
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
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    CONF_TARGET,
    DEFAULT_CROSSFADE_S,
    DEFAULT_TARGET,
    DOMAIN,
    EVENT_CAST,
    MAX_CROSSFADE_S,
    KIND_STATION,
    KIND_YOUTUBE,
    PREPARE_AHEAD_S,
    PREPARED_MAX_AGE_S,
    RECENT_SEARCHES,
    SETTLE_S,
    SERVICE_GET_LIST,
    SERVICE_PLAY_CAST,
    SERVICE_PLAY_SPOTIFY,
    SERVICE_PLAY_STATION,
    SERVICE_REMOVE_LIST,
    SERVICE_PLAY_YOUTUBE,
    SERVICE_SEARCH_YOUTUBE,
    SERVICE_SET_CROSSFADE,
    SPOTIFY_MATCH_RESULTS,
    SPOTIFY_MATCH_TOLERANCE_S,
    YT_MORE_SIZE,
    YT_MUSIC_MAX_S,
    YT_MUSIC_MIN_S,
    YT_SEARCH_SIZE,
)
from . import mixer, spotify
from .playlists import PlaylistStore
from .stations import StationStore
from .debuglog import dlog
from .stream_view import register_mix, short_url
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
SOURCE_CAST = "YouTube Cast"
# How long a cast track may take to start before its start position is given up on.
CAST_SEEK_WAIT_S = 20
MAX_SKIPS = 4
# A track that "ends" this soon after starting really failed to play; this many in a row stops
# autoplay instead of racing through the whole queue.
QUICK_END_S = 8
MAX_QUICK_ENDS = 3


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    player = HomeRadioPlayer(entry, data["stations"], data["playlists"])
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
    platform.async_register_entity_service(
        SERVICE_PLAY_CAST,
        {
            vol.Required("video_id"): cv.string,
            vol.Optional("position", default=0): vol.Coerce(float),
            vol.Optional("sender"): cv.string,
        },
        "async_play_cast",
        supports_response=SupportsResponse.OPTIONAL,
    )
    platform.async_register_entity_service(
        SERVICE_PLAY_SPOTIFY, {vol.Required("url"): cv.string}, "async_play_spotify",
    )
    platform.async_register_entity_service(
        SERVICE_REMOVE_LIST, {vol.Required("list"): cv.string}, "async_remove_list",
    )
    platform.async_register_entity_service(
        SERVICE_GET_LIST, {vol.Required("list"): cv.string}, "async_get_list",
        supports_response=SupportsResponse.ONLY,
    )
    platform.async_register_entity_service(
        SERVICE_SET_CROSSFADE, {vol.Required("seconds"): vol.All(vol.Coerce(float), vol.Range(min=0, max=MAX_CROSSFADE_S))},
        "async_set_crossfade",
    )


class HomeRadioPlayer(MediaPlayerEntity):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_icon = "mdi:radio"
    _attr_supported_features = SUPPORTED
    _attr_media_content_type = MediaType.MUSIC
    # Station list and queue are for the card, not for history.
    _unrecorded_attributes = frozenset({"stations", "queue", "recent_searches", "saved_lists"})

    def __init__(self, entry: ConfigEntry, stations: StationStore, playlists: PlaylistStore) -> None:
        self._entry = entry
        self._playlists = playlists
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
        # Set while the current track came from a phone casting through the Home Cast add-on.
        self._cast_sender: str | None = None
        # The Spotify playlist/album the queue came from (a saved list's id, and its name).
        self._list_id: str | None = None
        self._list_name: str | None = None
        # Mixing into the next track (set by the crossfade number entity).
        self.crossfade_s: float = DEFAULT_CROSSFADE_S
        # Set by the crossfade number entity, so a change made here shows there too.
        self.on_crossfade_change: Any = None
        self._tick_logged = 0.0
        self._prepared: tuple[int, str, ResolvedAudio, float] | None = None  # index, video, audio, when
        self._preparing = False
        self._mixing = False
        # The mixed stream (real crossfades): the session, what each of its tags is, and what's
        # being heard of it now.
        self._mix: mixer.MixSession | None = None
        self._mix_tracks: dict[int, tuple[int, ResolvedAudio]] = {}
        self._mix_tag = 0
        self._heard: tuple[int, float] | None = None
        self._heard_at: datetime | None = None
        self._heard_written = 0.0

    # ---- lifecycle -------------------------------------------------------------------------

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_track_state_change_event(self.hass, [self._target], self._on_target_change)
        )
        self.async_on_remove(self._stations.add_listener(self.async_write_ha_state))
        self.async_on_remove(self._playlists.add_listener(self.async_write_ha_state))
        self.async_on_remove(async_track_time_interval(self.hass, self._tick, timedelta(seconds=1)))

    @callback
    def _on_target_change(self, event: Event[EventStateChangedData]) -> None:
        old, new = event.data["old_state"], event.data["new_state"]
        if old is None or new is None or old.state != new.state:
            dlog(self.hass, f"TARGET {old.state if old else None} -> {new.state if new else None} "
                            f"title={new.attributes.get('media_title') if new else None!r} kind={self._kind}")
        # A YouTube track that ran out (playing -> idle, not something we stopped) continues
        # with the next one, like the TV app's autoplay.
        if (
            self._mix is not None
            and old is not None and new is not None
            and old.state == STATE_PLAYING and new.state not in (STATE_PLAYING, STATE_PAUSED, STATE_BUFFERING)
            and not self._loading and self.hass.loop.time() - self._started_at > 5
        ):
            # The mixed stream ran out (end of the queue) or VLC was stopped.
            dlog(self.hass, "MIX stream over")
            self._close_mix()
        elif (
            self._kind == KIND_YOUTUBE
            and self._mix is None
            and not self._loading
            and not self._stopping
            and not self._mixing
            and old is not None
            and new is not None
            and old.state == STATE_PLAYING
            and new.state not in (STATE_PLAYING, STATE_PAUSED, STATE_BUFFERING, STATE_UNAVAILABLE, STATE_UNKNOWN)
        ):
            quick = self.hass.loop.time() - self._started_at < QUICK_END_S
            self._quick_ends = self._quick_ends + 1 if quick else 0
            if self._cast_sender is not None:
                # The phone's queue decides what comes next.
                self._stopping = True
                self._cast_event("stop" if self._quick_ends >= MAX_QUICK_ENDS else "ended")
            elif self._quick_ends >= MAX_QUICK_ENDS:
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
        if self._mixed:
            return (self._now.duration or None) if self._now else None
        if self._kind == KIND_YOUTUBE:
            # VLC says -1 until it knows the length: fall back to what YouTube/Spotify said.
            vlc = self._tattr("media_duration")
            if isinstance(vlc, (int, float)) and vlc > 0:
                return vlc
            return (self._now.duration or None) if self._now else None
        return None

    @property
    def media_position(self) -> int | None:
        if self._mixed and self._heard is not None:
            # what's been sent is a little ahead of what's heard
            return max(0, int(self._heard_pos - mixer.LEAD_S - mixer.VLC_CACHE_S))
        pos = self._tattr("media_position") if self._kind == KIND_YOUTUBE else None
        return pos if isinstance(pos, (int, float)) and pos >= 0 else None

    @property
    def media_position_updated_at(self) -> datetime | None:
        if self._mixed:
            return self._heard_at
        return self._tattr("media_position_updated_at") if self._kind == KIND_YOUTUBE else None

    @property
    def source(self) -> str | None:
        if self._kind == KIND_STATION and self._station:
            return self._station["name"]
        if self._kind == KIND_YOUTUBE:
            return SOURCE_CAST if self._cast_sender is not None else SOURCE_YOUTUBE
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
            "cast_sender": self._cast_sender,
            "crossfade": self.crossfade_s,
            "queue_name": self._list_name if self._kind == KIND_YOUTUBE else None,
            "queue_list_id": self._list_id if self._kind == KIND_YOUTUBE else None,
            "saved_lists": self._playlists.summary(),
        }

    # ---- playing -----------------------------------------------------------------------------

    async def _send(self, service: str, data: dict[str, Any] | None = None) -> None:
        await self.hass.services.async_call(
            "media_player", service, {ATTR_ENTITY_ID: self._target, **(data or {})}, blocking=True,
        )

    async def _play_url(self, url: str) -> None:
        dlog(self.hass, f"PLAY {self._target} <- {url[:120]}")
        self._stopping = False
        self._started_at = self.hass.loop.time()
        await self._send("play_media", {"media_content_id": url, "media_content_type": "music"})

    @callback
    def _cast_event(self, command: str) -> None:
        self.hass.bus.async_fire(EVENT_CAST, {ATTR_ENTITY_ID: self.entity_id, "command": command})

    @callback
    def _end_cast(self) -> None:
        """Something other than the phone takes over the player."""
        if self._cast_sender is not None:
            self._cast_sender = None
            self._cast_event("stop")

    async def _async_start_station(self, station: dict[str, str]) -> None:
        self._end_cast()
        self._close_mix()
        self._list_id = self._list_name = None
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
        prepared, self._prepared = self._prepared, None
        if (prepared is not None and prepared[0] == index and prepared[1] == track.video_id
                and self.hass.loop.time() - prepared[3] < PREPARED_MAX_AGE_S):
            resolved = prepared[2]  # made ready while the previous track played
            dlog(self.hass, f"USE prepared #{index} {track.video_id}")
        else:
            if prepared is not None:
                dlog(self.hass, f"PREPARED unused: had #{prepared[0]} {prepared[1]}, now #{index} {track.video_id}")
            found = await self._async_match(track)
            resolved = await async_resolve(self.hass, track.url) if found and seq == self._seq else None
        if seq != self._seq:
            return  # something else was asked for meanwhile
        if resolved is None:
            dlog(self.hass, f"RESOLVE failed {track.url or track.query}")
            self._loading = False
            if skips < MAX_SKIPS and index + 1 < len(self._queue):
                await self._async_play_queue_item(index + 1, skips + 1)
            else:
                self.async_write_ha_state()
            return
        dlog(self.hass, f"RESOLVE ok {track.url} url_len={len(resolved.stream_url)} {resolved.diag}")
        if track.query:
            # Spotify's clean title and artist rather than the YouTube upload's "(Official Video)".
            resolved.title, resolved.channel = track.title, track.channel
        self._now = resolved
        nxt = self._queue[index + 1] if index + 1 < len(self._queue) else None
        if nxt is not None and nxt.query and not nxt.video_id:
            # Look the next song up now, so it starts without the search delay.
            self.hass.async_create_task(self._async_match(nxt))
        self._played.add(track.video_id)
        if self._use_mixer():
            try:
                if await self._async_mix_in(index, resolved):
                    return
            finally:
                self._loading = False
                self.async_write_ha_state()
        self._close_mix()
        try:
            # Short HA-served redirect instead of the ~1100-char googlevideo URL — see stream_view.
            await self._play_url(short_url(self.hass, resolved.stream_url, resolved.headers, resolved.opener))
        finally:
            self._loading = False
            self.async_write_ha_state()

    # ---- mixing into the next track ---------------------------------------------------------

    def _next_index(self) -> int | None:
        """What autoplay will play next, if it's already in the queue."""
        nxt = self._index + 1
        while 0 <= nxt < len(self._queue) and not self._queue[nxt].query \
                and not (YT_MUSIC_MIN_S <= self._queue[nxt].duration <= YT_MUSIC_MAX_S):
            nxt += 1
        return nxt if nxt < len(self._queue) else None

    def _remaining_s(self) -> float | None:
        tgt = self._tgt
        duration = self.media_duration
        pos = tgt.attributes.get("media_position") if tgt else None
        if tgt is None or tgt.state != STATE_PLAYING or not duration or not isinstance(pos, (int, float)) or pos < 0:
            return None
        at = tgt.attributes.get("media_position_updated_at")
        if isinstance(at, datetime):
            pos += (dt_util.utcnow() - at).total_seconds()
        return duration - pos

    @callback
    def _tick(self, _now: datetime) -> None:
        if (self._kind != KIND_YOUTUBE or self._cast_sender is not None or self._loading
                or self._mixing or self._stopping):
            return
        if self._mixed:
            # The mixer itself brings the next track in; it only has to be ready by then.
            nxt = self._next_index()
            duration = self._now.duration if self._now else 0
            if nxt is not None and duration and self._heard is not None and not self._preparing \
                    and duration - self._heard_pos <= PREPARE_AHEAD_S \
                    and not (self._prepared is not None and self._prepared[0] == nxt):
                self.hass.async_create_task(self._async_prepare(nxt))
            return
        if self.hass.loop.time() - self._started_at < SETTLE_S:
            return
        remaining = self._remaining_s()
        if remaining is None or (self.media_duration or 0) < YT_MUSIC_MIN_S:
            return
        nxt = self._next_index()
        if nxt is None:
            return
        ready = self._prepared is not None and self._prepared[0] == nxt
        if remaining <= PREPARE_AHEAD_S and self.hass.loop.time() - self._tick_logged >= 2:
            self._tick_logged = self.hass.loop.time()
            tgt = self._tgt
            dlog(self.hass, f"TICK left={remaining:.1f} next={nxt} ready={ready} mix={self.crossfade_s} "
                            f"dur={self.media_duration} vlc_pos={tgt.attributes.get('media_position') if tgt else None} "
                            f"vlc_at={tgt.attributes.get('media_position_updated_at') if tgt else None!r} "
                            f"vol={self.volume_level}")
        if remaining <= PREPARE_AHEAD_S and not ready and not self._preparing:
            self.hass.async_create_task(self._async_prepare(nxt))
        elif ready and self.crossfade_s > 0 and remaining <= self.crossfade_s:
            self._mixing = True
            self.hass.async_create_task(self._async_mix(remaining))

    async def _async_prepare(self, index: int) -> None:
        self._preparing = True
        seq = self._seq
        try:
            track = self._queue[index]
            if not await self._async_match(track):
                return
            resolved = await async_resolve(self.hass, track.url)
            if resolved is not None and self._use_mixer():
                resolved.data = await self._async_fetch(resolved)
            if resolved is not None and seq == self._seq:
                self._prepared = (index, track.video_id, resolved, self.hass.loop.time())
                dlog(self.hass, f"PREPARED {track.url}")
        finally:
            self._preparing = False

    # ---- the mixed stream ---------------------------------------------------------------------

    @property
    def _mixed(self) -> bool:
        return self._mix is not None and self._kind == KIND_YOUTUBE

    @property
    def _heard_pos(self) -> float:
        return self._heard[1] if self._heard else 0.0

    def _use_mixer(self) -> bool:
        return mixer.AVAILABLE and self.crossfade_s > 0 and self._cast_sender is None

    @callback
    def _close_mix(self) -> None:
        if self._mix is not None:
            self._mix.close()
            self._mix = None
        self._mix_tracks.clear()
        self._heard = None

    async def _async_fetch(self, resolved: ResolvedAudio) -> bytes | None:
        if resolved.data:
            return resolved.data
        try:
            return await self.hass.async_add_executor_job(mixer.fetch, resolved)
        except Exception as err:  # noqa: BLE001 - then this track plays the plain way
            dlog(self.hass, f"MIX fetch failed: {err!r}")
            return None

    def _new_tag(self, index: int, resolved: ResolvedAudio) -> int:
        self._mix_tag += 1
        self._mix_tracks[self._mix_tag] = (index, resolved)
        for old in [t for t in self._mix_tracks if t < self._mix_tag - 4]:  # (each holds a few MB)
            self._mix_tracks.pop(old)
        return self._mix_tag

    async def _async_mix_in(self, index: int, resolved: ResolvedAudio, start_s: float = 0.0) -> bool:
        """Plays a track through the mixer: mixed quickly into what's playing if the stream is
        already on, else as a new stream. False if it can't (then it's played the plain way)."""
        resolved.data = await self._async_fetch(resolved)
        if not resolved.data:
            return False
        track = mixer.Track(self._new_tag(index, resolved), resolved, start_s)
        tgt = self._tgt
        live = self._mix is not None and not self._mix.ended and not self._mix.closed.is_set()
        if live and tgt is not None and tgt.state in (STATE_PLAYING, STATE_BUFFERING, STATE_PAUSED):
            dlog(self.hass, f"MIX jump #{index} at {start_s:.0f}s")
            self._mix.jump(track)
            if tgt.state == STATE_PAUSED:
                await self._send("media_play")
            return True
        self._close_mix()
        session = mixer.MixSession(track, self._mix_ask_next, lambda: self.crossfade_s)
        session.on_heard = self._on_heard
        self._mix = session
        self._heard, self._heard_at = (track.tag, start_s), dt_util.utcnow()
        dlog(self.hass, f"MIX stream #{index}")
        await self._play_url(register_mix(self.hass, session))
        return True

    def _mix_ask_next(self) -> mixer.Track | None:
        """From the mixer's thread: the track to mix in next."""
        future = asyncio.run_coroutine_threadsafe(self._async_mix_next(), self.hass.loop)
        try:
            return future.result(timeout=60)
        except Exception:  # noqa: BLE001 - nothing to mix in: the stream ends after this track
            _LOGGER.warning("home_radio: no next track for the mix", exc_info=True)
            return None

    async def _async_mix_next(self) -> mixer.Track | None:
        nxt = self._next_index()
        if nxt is None and await self._async_more_for_queue():
            nxt = self._next_index()
        for _ in range(MAX_SKIPS):
            if nxt is None:
                return None
            track = self._queue[nxt]
            prepared, self._prepared = self._prepared, None
            if prepared is not None and prepared[0] == nxt and prepared[1] == track.video_id:
                resolved = prepared[2]
            else:
                resolved = await async_resolve(self.hass, track.url) if await self._async_match(track) else None
            if resolved is not None:
                resolved.data = await self._async_fetch(resolved)
            if resolved is not None and resolved.data:
                if track.query:
                    resolved.title, resolved.channel = track.title, track.channel
                dlog(self.hass, f"MIX next #{nxt} {track.video_id}")
                return mixer.Track(self._new_tag(nxt, resolved), resolved)
            nxt = nxt + 1 if nxt + 1 < len(self._queue) else None
        return None

    @callback
    def _on_heard(self, tag: int, pos: float) -> None:
        """From the stream view, as it sends: which track (and where in it) is being heard."""
        entry = self._mix_tracks.get(tag)
        if entry is None or self._mix is None:
            return
        changed = self._heard is None or self._heard[0] != tag
        self._heard = (tag, pos)
        now = self.hass.loop.time()
        if changed:
            index, resolved = entry
            self._index, self._now = index, resolved
            if 0 <= index < len(self._queue):
                self._played.add(self._queue[index].video_id)
                nxt = self._queue[index + 1] if index + 1 < len(self._queue) else None
                if nxt is not None and nxt.query and not nxt.video_id:
                    self.hass.async_create_task(self._async_match(nxt))
        if changed or now - self._heard_written >= 5:
            self._heard_written = now
            self._heard_at = dt_util.utcnow()
            self.async_write_ha_state()

    async def _async_mix(self, remaining: float) -> None:
        """Starts the prepared track [crossfade] seconds before this one ends, at full volume:
        the music never stops (the last seconds of the old track, usually its outro, are left
        out — a single VLC can't play two tracks at once)."""
        dlog(self.hass, f"MIX start left={remaining:.1f}")
        try:
            await self._async_advance(1, autoplay=True)
        except Exception as err:  # noqa: BLE001 - logged; the old track just runs out as usual
            dlog(self.hass, f"MIX failed: {err!r}")
            _LOGGER.exception("home_radio: starting the next track early failed")
        finally:
            self._mixing = False

    async def _async_match(self, track: YoutubeTrack) -> bool:
        """Finds a Spotify song on YouTube: the first result about as long as the song."""
        if track.video_id:
            return True
        results = await async_search(self.hass, track.query, SPOTIFY_MATCH_RESULTS)
        if track.video_id:
            return True  # found meanwhile by the look-ahead
        if not results:
            return False
        close = [t for t in results if track.duration and t.duration
                 and abs(t.duration - track.duration) <= SPOTIFY_MATCH_TOLERANCE_S]
        music = [t for t in results if YT_MUSIC_MIN_S <= t.duration <= YT_MUSIC_MAX_S]
        best = (close or music or results)[0]
        track.video_id, track.url, track.thumbnail = best.video_id, best.url, best.thumbnail
        if not track.duration:
            track.duration = best.duration
        if self._list_id is not None:
            index = next((i for i, t in enumerate(self._queue) if t is track), -1)
            self._playlists.remember_match(self._list_id, index, best.video_id, best.thumbnail)
        return True

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
                # (Spotify songs are songs already.)
                while 0 <= nxt < len(self._queue) and not self._queue[nxt].query \
                        and not (YT_MUSIC_MIN_S <= self._queue[nxt].duration <= YT_MUSIC_MAX_S):
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
        if spotify.is_link(url or "") or (not url and spotify.is_link(query or "")):
            await self.async_play_spotify(url or query or "")
            return
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
        self._end_cast()
        self._query, self._list_id, self._list_name = query, None, None
        self._queue, self._played = list(results), set()
        await self._async_play_queue_item(start)

    async def async_play_spotify(self, url: str) -> None:
        """A Spotify playlist, album or song (the link as shared from the app): its songs become
        the queue, each one played from YouTube. No Spotify account is involved."""
        link = spotify.parse_link(url)
        list_id = f"{link[0]}:{link[1]}" if link else None
        found = await spotify.async_fetch(self.hass, url)
        if found is None or not found.songs:
            saved = self._playlists.get(list_id) if list_id else None
            if saved is None:
                raise HomeAssistantError("Δεν μπόρεσα να διαβάσω αυτό το link του Spotify (είναι δημόσια η λίστα;)")
            # Spotify unreachable right now: the saved copy plays.
            await self._async_start_list(saved)
            return
        dlog(self.hass, f"SPOTIFY {found.kind} {found.name!r}: {len(found.songs)} songs")
        if list_id and found.kind in ("playlist", "album", "youtube"):
            # Kept for next time (a single song isn't worth a place among the lists).
            await self._async_start_list(self._playlists.save(list_id, url, found))
            return
        self._end_cast()
        self._query, self._list_id, self._list_name = None, None, found.name
        self._queue = [YoutubeTrack("", "", s.title, s.artists, s.duration, found.image, query=s.query)
                       for s in found.songs]
        self._played = set()
        await self._async_play_queue_item(0)

    async def _async_start_list(self, saved: dict[str, Any], start: int = 0, shuffle: bool = False) -> None:
        """Plays a saved list from song [start] (or shuffled); songs matched before start without
        searching."""
        self._end_cast()
        self._query, self._list_id, self._list_name = None, saved["id"], saved["name"]
        self._queue = [
            YoutubeTrack(vid, f"https://www.youtube.com/watch?v={vid}" if vid else "", s.title, s.artists,
                         s.duration, f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg" if vid else saved.get("image", ""),
                         query=s.query)
            for s, vid in self._playlists.songs(saved)
        ]
        if shuffle:
            random.shuffle(self._queue)
            start = 0
        self._played = set()
        self._playlists.touch(saved["id"])
        await self._async_play_queue_item(max(0, min(start, len(self._queue) - 1)))

    async def async_play_saved(self, key: str) -> None:
        """`<id>`, `<id>@<song number from 0>` or `<id>@shuffle`."""
        key, _, how = key.partition("@")
        saved = self._playlists.find(key)
        if saved is None:
            raise HomeAssistantError(f"Δεν βρέθηκε αποθηκευμένη λίστα: {key}")
        await self._async_start_list(saved, start=int(how) if how.isdigit() else 0, shuffle=how == "shuffle")
        # Meanwhile pick up songs added to it on Spotify since, for next time.
        self.hass.async_create_task(self._async_refresh_saved(saved["id"], saved["url"]))

    async def _async_refresh_saved(self, list_id: str, url: str) -> None:
        found = await spotify.async_fetch(self.hass, url)
        if found is not None and found.songs and self._playlists.get(list_id) is not None:
            self._playlists.save(list_id, url, found)
            self._playlists.touch(list_id)

    async def async_get_list(self, list: str) -> ServiceResponse:  # noqa: A002 - the service field's name
        """A saved list's songs, for picking one in the phone app or a dashboard."""
        saved = self._playlists.find(list)
        if saved is None:
            raise HomeAssistantError(f"Δεν βρέθηκε αποθηκευμένη λίστα: {list}")
        return {
            "id": saved["id"],
            "name": saved["name"],
            "image": saved.get("image", ""),
            "songs": [
                {"title": s.title, "artists": s.artists, "duration": s.duration,
                 "thumbnail": f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg" if vid else saved.get("image", "")}
                for s, vid in self._playlists.songs(saved)
            ],
        }

    async def async_set_crossfade(self, seconds: float) -> None:
        self.crossfade_s = float(seconds)
        if self.on_crossfade_change is not None:
            self.on_crossfade_change(self.crossfade_s)
        self.async_write_ha_state()

    async def async_remove_list(self, list: str) -> None:  # noqa: A002 - the service field's name
        saved = self._playlists.find(list)
        if saved is None or not self._playlists.remove(saved["id"]):
            raise HomeAssistantError(f"Δεν βρέθηκε αποθηκευμένη λίστα: {list}")
        if self._list_id == saved["id"]:
            self._list_id = None
            self.async_write_ha_state()

    async def async_play_cast(self, video_id: str, position: float = 0, sender: str | None = None) -> ServiceResponse:
        """One track sent by the Home Cast add-on. The phone keeps the queue, so there's no
        autoplay here: the end of the track is reported back with a home_radio_cast event."""
        self._close_mix()
        self._cast_sender = sender or "YouTube"
        self._query, self._list_id, self._list_name = None, None, None
        self._queue = [YoutubeTrack(video_id, f"https://www.youtube.com/watch?v={video_id}", "", "", 0,
                                    f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg")]
        self._played = set()
        await self._async_play_queue_item(0)
        seq = self._seq
        ok = self._now is not None and bool(self._now.stream_url)
        dlog(self.hass, f"CAST {video_id} pos={position} from={sender!r} ok={ok}")
        if ok and position > 1:
            self.hass.async_create_task(self._async_seek_when_playing(seq, position))
        return {"ok": ok, "title": self._now.title if self._now else None}

    async def _async_seek_when_playing(self, seq: int, position: float) -> None:
        for _ in range(CAST_SEEK_WAIT_S * 2):
            if seq != self._seq:
                return
            tgt = self._tgt
            if tgt is not None and tgt.state == STATE_PLAYING:
                await self._send("media_seek", {"seek_position": position})
                return
            await asyncio.sleep(0.5)

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
        self._end_cast()
        self._close_mix()
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
        if self._cast_sender is not None:
            self._cast_event("next")
        else:
            await self._async_advance(1)

    async def async_media_previous_track(self) -> None:
        if self._cast_sender is not None:
            self._cast_event("previous")
        else:
            await self._async_advance(-1)

    async def async_media_seek(self, position: float) -> None:
        if self._mixed and self._now is not None and await self._async_mix_in(self._index, self._now, start_s=position):
            return  # (the same track again, from there, mixed in quickly)
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
        if media_id.startswith("queue:") and media_id.removeprefix("queue:").isdigit():
            await self._async_play_queue_item(int(media_id.removeprefix("queue:")))
        elif media_id.startswith("list:"):
            await self.async_play_saved(media_id.removeprefix("list:"))
        elif spotify.is_link(media_id):
            await self.async_play_spotify(media_id)
        elif media_id.startswith("station:"):
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
                BrowseMedia(media_class=MediaClass.TRACK, media_content_id=f"queue:{i}",
                            media_content_type="youtube", title=t.title, can_play=True, can_expand=False,
                            thumbnail=t.thumbnail)
                for i, t in enumerate(self._queue)
            ],
        )
        lists = self._playlists.summary()
        lists_dir = BrowseMedia(
            media_class=MediaClass.DIRECTORY, media_content_id="lists", media_content_type="home_radio",
            title="Λίστες", can_play=False, can_expand=True, children_media_class=MediaClass.PLAYLIST,
            children=[
                BrowseMedia(media_class=MediaClass.PLAYLIST, media_content_id=f"list:{p['id']}",
                            media_content_type="playlist", title=p["name"], can_play=True, can_expand=False,
                            thumbnail=p["image"] or None)
                for p in lists
            ],
            thumbnail="https://fonts.gstatic.com/s/e/notoemoji/latest/1f3b6/512.png",
        )
        folders = {"stations": stations_dir, "lists": lists_dir, "recent": recent_dir, "queue": queue_dir}
        if media_content_id in folders:
            return folders[media_content_id]
        shown = [stations_dir] + ([lists_dir] if lists else []) + [recent_dir] + ([queue_dir] if self._queue else [])
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
