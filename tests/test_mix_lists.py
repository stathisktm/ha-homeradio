"""Mixing while a saved Spotify list plays (the real-life case)."""

from unittest.mock import patch

from custom_components.home_radio.const import DOMAIN
from custom_components.home_radio.youtube import YoutubeTrack

from test_home_radio import ENTITY, MP, _fixture, _near_end, _resolved, _target, _tick, calls, last_url, plays  # noqa: F401


async def test_mix_in_a_spotify_list(hass, aioclient_mock, calls):  # noqa: F811
    aioclient_mock.get("https://open.spotify.com/embed/album/4LH4d3cOWNNsVw41Gqt2kv", text=_fixture("spotify_album.html"))
    n = {"i": 0}

    async def search(hass_, query, size):
        n["i"] += 1
        return [YoutubeTrack(f"s{n['i']}", f"https://www.youtube.com/watch?v=s{n['i']}", "x", "c", 200, "t")]

    with patch(f"{MP}.async_search", side_effect=search), patch(f"{MP}.async_resolve", side_effect=_resolved) as res:
        await hass.services.async_call(DOMAIN, "play_spotify", {
            "entity_id": ENTITY, "url": "https://open.spotify.com/album/4LH4d3cOWNNsVw41Gqt2kv"}, blocking=True)
        await hass.async_block_till_done()
        await hass.services.async_call("number", "set_value", {"entity_id": "number.home_radio_crossfade", "value": 0},
                                       blocking=True)
        r0 = res.call_count
        await _near_end(hass, 180)
        await _tick(hass)
        await _tick(hass)
        print("after prepare", res.call_count, "prepared:", next(iter(hass.data[DOMAIN].values()))["player"]._prepared)
        await _target(hass, "idle")
        print("plays", plays(calls)[-2:], "resolves", res.call_count, r0)
        assert res.call_count == r0 + 1


async def test_crossfade_set_from_the_player_shows_on_the_slider(hass, calls):  # noqa: F811
    await hass.services.async_call(DOMAIN, "set_crossfade", {"entity_id": ENTITY, "seconds": 9}, blocking=True)
    assert hass.states.get(ENTITY).attributes["crossfade"] == 9
    assert hass.states.get("number.home_radio_crossfade").state == "9.0"
    await hass.services.async_call("number", "set_value", {"entity_id": "number.home_radio_crossfade", "value": 3},
                                   blocking=True)
    assert hass.states.get(ENTITY).attributes["crossfade"] == 3


async def test_mix_with_vlc_like_polling(hass, aioclient_mock, calls, freezer):  # noqa: F811
    """VLC (vlc_telnet) reports position only every 10 s; time really passes."""
    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    aioclient_mock.get("https://open.spotify.com/embed/album/4LH4d3cOWNNsVw41Gqt2kv", text=_fixture("spotify_album.html"))
    n = {"i": 0}

    async def search(hass_, query, size):
        n["i"] += 1
        return [YoutubeTrack(f"s{n['i']}", f"https://www.youtube.com/watch?v=s{n['i']}", "x", "c", 0, "t")]

    with patch(f"{MP}.async_search", side_effect=search), patch(f"{MP}.async_resolve", side_effect=_resolved) as res:
        await hass.services.async_call(DOMAIN, "play_spotify", {
            "entity_id": ENTITY, "url": "https://open.spotify.com/album/4LH4d3cOWNNsVw41Gqt2kv"}, blocking=True)
        await hass.async_block_till_done()
        fake = hass.data["fake_vlc"]
        start = dt_util.utcnow()
        await _target(hass, "playing", media_duration=169, media_position=0, media_position_updated_at=start)
        n_plays, log = len(plays(calls)), []
        for sec in range(1, 175):
            freezer.tick(1)
            async_fire_time_changed(hass)
            await hass.async_block_till_done()
            if len(plays(calls)) > n_plays:
                log.append(("switched at", sec))
                break
            if sec % 10 == 0:  # a vlc_telnet poll
                await _target(hass, "playing", media_duration=169, media_position=sec,
                              media_position_updated_at=dt_util.utcnow())
            if sec >= 169:
                await _target(hass, "idle")
        vols = [v for c, v in calls.calls if c == "volume_set"]
        print("log", log, "vols", vols[:3], "...", len(vols), "resolves", res.call_count)
        assert log and log[0][1] < 169, "the next song should start before the old one ran out"
        assert vols, "it should have faded"
