"""End-to-end checks against a real Home Assistant core, with the VLC player replaced by a fake
media_player entity: every command Home Radio sends it is recorded, and its state is driven by hand."""

from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockModule,
    MockPlatform,
    mock_integration,
    mock_platform,
)

from homeassistant.components.media_player import MediaPlayerEntity, MediaPlayerEntityFeature, MediaPlayerState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component

from custom_components.home_radio.const import DOMAIN
from custom_components.home_radio.youtube import ResolvedAudio, YoutubeTrack

TARGET = "media_player.vlc"
ENTITY = "media_player.home_radio"
MP = "custom_components.home_radio.media_player"


class FakeVlc(MediaPlayerEntity):
    _attr_name = "vlc"
    _attr_supported_features = (
        MediaPlayerEntityFeature.PLAY_MEDIA | MediaPlayerEntityFeature.STOP | MediaPlayerEntityFeature.PAUSE
        | MediaPlayerEntityFeature.PLAY | MediaPlayerEntityFeature.VOLUME_SET | MediaPlayerEntityFeature.VOLUME_MUTE
        | MediaPlayerEntityFeature.SEEK
    )

    def __init__(self):
        self.calls = []
        self._attr_state = MediaPlayerState.IDLE
        self._attr_volume_level = 0.5
        self._attr_is_volume_muted = False

    def set(self, state, **attrs):
        self._attr_state = MediaPlayerState(state)
        for key, value in attrs.items():
            setattr(self, f"_attr_{key}", value)
        self.async_write_ha_state()

    async def async_play_media(self, media_type, media_id, **kwargs):
        self.calls.append(("play_media", media_id))

    async def async_media_stop(self):
        self.calls.append(("media_stop", None))

    async def async_media_pause(self):
        self.calls.append(("media_pause", None))

    async def async_media_play(self):
        self.calls.append(("media_play", None))

    async def async_set_volume_level(self, volume):
        self.calls.append(("volume_set", volume))

    async def async_mute_volume(self, mute):
        self.calls.append(("volume_mute", mute))

    async def async_media_seek(self, position):
        self.calls.append(("media_seek", position))


def _tracks(n=5, prefix="v"):
    return [YoutubeTrack(f"{prefix}{i}", f"https://www.youtube.com/watch?v={prefix}{i}", f"Song {i}", "Artist",
                         200, f"https://i.ytimg.com/vi/{prefix}{i}/hqdefault.jpg") for i in range(n)]


def _resolved(hass_, url):
    return ResolvedAudio("https://audio/" + url.rsplit("=", 1)[-1], "T", "Artist", 200, "thumb")


async def _fake_vlc(hass):
    fake = FakeVlc()

    async def setup_platform(hass_, config, add_entities, discovery_info=None):
        add_entities([fake])

    mock_integration(hass, MockModule("fakevlc"))
    mock_platform(hass, "fakevlc.media_player", MockPlatform(async_setup_platform=setup_platform))
    assert await async_setup_component(hass, "media_player", {"media_player": {"platform": "fakevlc"}})
    await hass.async_block_till_done()
    assert hass.states.get(TARGET) is not None
    hass.data["fake_vlc"] = fake
    return fake


@pytest.fixture
async def calls(hass: HomeAssistant):
    fake = await _fake_vlc(hass)
    entry = MockConfigEntry(domain=DOMAIN, data={"target": TARGET, "tv_urls": ""}, title="Home Radio")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return fake


def plays(fake):
    return [c for c in fake.calls if c[0] == "play_media"]


def last_url(fake):
    """What the player was asked to play, with Home Radio's short YouTube redirect unwrapped."""
    from custom_components.home_radio.stream_view import STREAM_PATH, resolve_token

    url = plays(fake)[-1][1]
    if url.startswith(STREAM_PATH):
        assert len(url) < 200  # the whole point: VLC's telnet line limit is 1024
        return resolve_token(fake.hass, url.rsplit("/", 1)[-1])
    return url


async def _target(hass, state, **attrs):
    hass.data["fake_vlc"].set(state, **attrs)
    await hass.async_block_till_done()


async def test_entity_and_stations(hass, calls):
    st = hass.states.get(ENTITY)
    assert st is not None, [s.entity_id for s in hass.states.async_all()]
    assert st.state == "idle"
    assert len(st.attributes["source_list"]) == 19
    assert "Fly FM" in st.attributes["source_list"]
    assert st.attributes["volume_level"] == 0.5
    assert hass.states.get("text.home_radio_youtube") is not None


async def test_select_source_plays_station_and_shows_icy(hass, calls):
    await hass.services.async_call("media_player", "select_source", {"entity_id": ENTITY, "source": "Fly FM"}, blocking=True)
    assert plays(calls)
    url = last_url(calls)
    assert url.startswith("http")
    await _target(hass, "playing", volume_level=0.5, media_title="Queen - Bohemian Rhapsody")
    st = hass.states.get(ENTITY)
    assert st.state == "playing"
    assert st.attributes["source"] == "Fly FM"
    assert st.attributes["media_title"] == "Queen - Bohemian Rhapsody"
    assert st.attributes["media_artist"] == "Fly FM"
    await hass.services.async_call("media_player", "media_next_track", {"entity_id": ENTITY}, blocking=True)
    assert last_url(calls) != url


async def test_youtube_queue_and_autoplay(hass, calls):
    with patch(f"{MP}.async_search", return_value=_tracks()) as srch, patch(f"{MP}.async_resolve", side_effect=_resolved):
        await hass.services.async_call(DOMAIN, "play_youtube", {"entity_id": ENTITY, "query": "rock"}, blocking=True)
        assert last_url(calls) == "https://audio/v0"
        await _target(hass, "playing", media_position=10, media_duration=200)
        st = hass.states.get(ENTITY)
        assert st.attributes["kind"] == "youtube" and st.attributes["queue_index"] == 0
        assert len(st.attributes["queue"]) == 5 and st.attributes["recent_searches"] == ["rock"]
        assert st.attributes["media_duration"] == 200
        # the track ends on its own -> the next one plays
        await _target(hass, "idle")
        assert last_url(calls) == "https://audio/v1"
        # a user stop must NOT advance
        await _target(hass, "playing")
        n = len(plays(calls))
        await hass.services.async_call("media_player", "media_stop", {"entity_id": ENTITY}, blocking=True)
        await _target(hass, "idle")
        assert len(plays(calls)) == n
        # play resumes the same track; previous goes back
        await hass.services.async_call("media_player", "media_play", {"entity_id": ENTITY}, blocking=True)
        assert last_url(calls) == "https://audio/v1"
        await hass.services.async_call("media_player", "media_previous_track", {"entity_id": ENTITY}, blocking=True)
        assert last_url(calls) == "https://audio/v0"
        # the card plays a chosen result with the search as its queue (search is cached)
        await hass.services.async_call(DOMAIN, "play_youtube", {"entity_id": ENTITY, "query": "rock",
                                       "url": "https://www.youtube.com/watch?v=v3"}, blocking=True)
        assert last_url(calls) == "https://audio/v3"
        assert srch.call_count == 1


async def test_autoplay_fetches_more_at_end(hass, calls):
    with patch(f"{MP}.async_search", side_effect=[_tracks(2, "a"), _tracks(3, "b")]), \
         patch(f"{MP}.async_resolve", side_effect=_resolved):
        await hass.services.async_call(DOMAIN, "play_youtube", {"entity_id": ENTITY, "query": "x"}, blocking=True)
        await hass.services.async_call("media_player", "media_next_track", {"entity_id": ENTITY}, blocking=True)
        assert last_url(calls) == "https://audio/a1"
        await _target(hass, "playing")
        await _target(hass, "idle")
        assert last_url(calls) == "https://audio/b0"
        assert len(hass.states.get(ENTITY).attributes["queue"]) == 5


async def test_unresolvable_track_is_skipped(hass, calls):
    def resolved(hass_, url):
        return None if url.endswith("v0") else _resolved(hass_, url)

    with patch(f"{MP}.async_search", return_value=_tracks()), patch(f"{MP}.async_resolve", side_effect=resolved):
        await hass.services.async_call(DOMAIN, "play_youtube", {"entity_id": ENTITY, "query": "x"}, blocking=True)
    assert last_url(calls) == "https://audio/v1"


async def test_search_response_volume_and_text(hass, calls):
    with patch(f"{MP}.async_search", return_value=_tracks(3)):
        res = await hass.services.async_call(DOMAIN, "search_youtube", {"entity_id": ENTITY, "query": "jazz"},
                                             blocking=True, return_response=True)
    assert len(res[ENTITY]["results"]) == 3
    await hass.services.async_call("media_player", "volume_set", {"entity_id": ENTITY, "volume_level": 0.3}, blocking=True)
    assert ("volume_set", 0.3) in calls.calls
    with patch(f"{MP}.async_search", return_value=_tracks(2)), patch(f"{MP}.async_resolve", side_effect=_resolved):
        await hass.services.async_call("text", "set_value", {"entity_id": "text.home_radio_youtube", "value": "lofi"}, blocking=True)
    assert last_url(calls) == "https://audio/v0"


async def test_browse_and_play_media(hass, calls):
    player = next(iter(hass.data[DOMAIN].values()))["player"]
    root = await player.async_browse_media()
    assert [c.media_content_id for c in root.children] == ["stations", "recent"]
    stations = await player.async_browse_media("home_radio", "stations")
    assert len(stations.children) == 19
    pick = stations.children[2].media_content_id
    await hass.services.async_call("media_player", "play_media", {"entity_id": ENTITY,
        "media_content_id": pick, "media_content_type": "station"}, blocking=True)
    assert hass.states.get(ENTITY).attributes["station_id"] == pick.split(":", 1)[1]
    await hass.services.async_call("media_player", "play_media", {"entity_id": ENTITY,
        "media_content_id": "http://example.com/stream.mp3", "media_content_type": "music"}, blocking=True)
    assert last_url(calls) == "http://example.com/stream.mp3"


async def test_config_flow_suggests_tvs(hass):
    MockConfigEntry(domain="androidtv", data={"host": "10.0.0.5"}).add_to_hass(hass)
    await _fake_vlc(hass)
    with patch("custom_components.home_radio.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        assert result["type"] is FlowResultType.FORM
        defaults = {str(k): k.default() for k in result["data_schema"].schema}
        assert defaults["target"] == TARGET and defaults["tv_urls"] == "http://10.0.0.5:8698"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"target": TARGET, "tv_urls": ""})
        assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_station_sync_from_tv(hass, aioclient_mock):
    aioclient_mock.get("http://tv:8698/stations", json=[
        {"id": "custom:1", "name": "Δικός μου", "streamUrl": "http://s/1", "logoUrl": "content://x", "category": "Δικά μου"},
        {"id": "vanilla_radio", "name": "Vanilla", "streamUrl": "http://s/2", "logoUrl": "https://l/2.png", "category": "greek"},
    ])
    await _fake_vlc(hass)
    entry = MockConfigEntry(domain=DOMAIN, data={"target": TARGET, "tv_urls": "http://tv:8698"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    st = hass.states.get(ENTITY)
    assert st.attributes["source_list"] == ["Δικός μου", "Vanilla"]
    assert st.attributes["stations"][0]["logo"] == ""


async def test_youtube_stream_is_relayed(hass, aioclient_mock, calls, hass_client_no_auth):
    long_url = "https://rr1.googlevideo.com/videoplayback?" + "x" * 1100
    aioclient_mock.get(long_url, content=b"OPUSDATA" * 1000, headers={"Content-Type": "audio/webm"})
    with patch(f"{MP}.async_search", return_value=_tracks(1)),          patch(f"{MP}.async_resolve", return_value=ResolvedAudio(long_url, "T", "A", 200, "t", {"User-Agent": "yt"})):
        await hass.services.async_call(DOMAIN, "play_youtube", {"entity_id": ENTITY, "query": "x"}, blocking=True)
    short = plays(calls)[-1][1]
    assert short.startswith("/api/home_radio/yt/") and len(short) < 100
    client = await hass_client_no_auth()
    resp = await client.get(short, headers={"Range": "bytes=0-"})
    assert resp.status == 200 and await resp.read() == b"OPUSDATA" * 1000
    assert resp.headers["Content-Type"] == "audio/webm"
    sent = aioclient_mock.mock_calls[-1]
    assert sent[3]["User-Agent"] == "yt" and sent[3]["Range"] == "bytes=0-"
    assert (await client.get("/api/home_radio/yt/nope")).status == 404
    await hass.async_block_till_done()
    log = open(hass.config.path("home_radio_debug.log"), encoding="utf-8").read()
    assert "PLAY media_player.vlc <- /api/home_radio/yt/" in log and "upstream 200" in log


async def test_runaway_autoplay_stops(hass, calls):
    """Tracks that die instantly (a player that can't open them) must not race through the queue."""
    with patch(f"{MP}.async_search", return_value=_tracks(10)), patch(f"{MP}.async_resolve", side_effect=_resolved):
        await hass.services.async_call(DOMAIN, "play_youtube", {"entity_id": ENTITY, "query": "x"}, blocking=True)
        for _ in range(6):
            await _target(hass, "playing")
            await _target(hass, "idle")
    assert len(plays(calls)) == 3


async def test_youtube_stream_via_ytdlp_session(hass, calls, hass_client_no_auth):
    """The real path: bytes come through the yt-dlp opener that resolved the URL."""
    import io as _io

    seen = {}

    class FakeResp:
        status = 206
        headers = {"Content-Type": "audio/webm", "Content-Range": "bytes 0-7999/8000"}

        def __init__(self):
            self._buf = _io.BytesIO(b"OPUS" * 2000)

        def read(self, n):
            return self._buf.read(n)

        def close(self):
            seen["closed"] = True

    def opener(extra):
        seen["extra"] = extra
        return FakeResp()

    audio = ResolvedAudio("https://rr.googlevideo.com/x?" + "y" * 1100, "T", "A", 200, "t", {}, opener, "diag")
    with patch(f"{MP}.async_search", return_value=_tracks(1)), patch(f"{MP}.async_resolve", return_value=audio):
        await hass.services.async_call(DOMAIN, "play_youtube", {"entity_id": ENTITY, "query": "x"}, blocking=True)
    client = await hass_client_no_auth()
    resp = await client.get(plays(calls)[-1][1], headers={"Range": "bytes=0-"})
    assert resp.status == 206 and await resp.read() == b"OPUS" * 2000
    assert resp.headers["Content-Range"] == "bytes 0-7999/8000"
    assert seen == {"extra": {"Range": "bytes=0-"}, "closed": True}
