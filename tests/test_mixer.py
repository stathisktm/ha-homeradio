"""The real-crossfade mixer: two tracks really play together for a moment, and Home Radio keeps
title, queue and controls in step with what's being heard."""

from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from custom_components.home_radio import mixer
from custom_components.home_radio.const import DOMAIN
from custom_components.home_radio.youtube import ResolvedAudio

from test_home_radio import ENTITY, MP, _target, _tracks, calls, plays  # noqa: F401

LEVEL = {"A": 0.5, "B": 0.3}


def tone(track):
    """A synthetic track: a constant level for its whole length (from start_s)."""
    level = LEVEL.get(getattr(track.audio, "title", ""), 0.2)
    left = int((track.audio.duration - track.start_s) * mixer.RATE)
    while left > 0:
        n = min(left, 4800)
        yield np.full((n, mixer.CHANNELS), level, dtype=np.float32)
        left -= n


def _audio(name, seconds):
    return SimpleNamespace(title=name, duration=seconds, data=b"x", opener=None)


def _drain(session, limit_s=60):
    out = []
    while len(out) < limit_s * 10:
        item = session.get()
        if item is None:
            break
        out.append(item)
    return out


def test_two_tracks_overlap_with_equal_power():
    asked = []

    def nxt():
        asked.append(1)
        return mixer.Track("B", _audio("B", 4)) if len(asked) == 1 else None

    session = mixer.MixSession(mixer.Track("A", _audio("A", 4)), nxt, lambda: 2.0, decoder=tone)
    chunks = _drain(session)
    pcm = np.concatenate([np.frombuffer(c[0], "<i2").reshape(-1, 2) for c in chunks]) / 32767
    tags = [c[1] for c in chunks]
    # A alone (2 s), A+B together (2 s), then B alone (2 s): 6 s, not 8
    assert abs(len(pcm) / mixer.RATE - 6.0) < 0.15
    assert abs(pcm[int(1.0 * mixer.RATE), 0] - 0.5) < 0.01  # A
    mid = pcm[int(3.0 * mixer.RATE), 0]  # halfway through the mix: both, equal-power
    assert abs(mid - (0.5 + 0.3) * np.cos(np.pi / 4)) < 0.02
    assert abs(pcm[int(5.0 * mixer.RATE), 0] - 0.3) < 0.01  # B
    # "now playing" moves to B halfway through the mix
    first_b = tags.index("B") / 10
    assert 2.8 <= first_b <= 3.2
    assert len(asked) == 2 and session.ended


def test_jump_mixes_quickly_into_a_picked_track():
    session = mixer.MixSession(mixer.Track("A", _audio("A", 30)), lambda: None, lambda: 6.0, decoder=tone)
    head = [session.get() for _ in range(10)]  # 1 s of A
    session.jump(mixer.Track("B", _audio("B", 30)))
    rest = [session.get() for _ in range(40)]
    session.close()
    assert all(t == "A" for _, t, _ in head)
    tags = [t for _, t, _ in rest]
    # B is heard within the buffer + half the quick fade, and A is gone after it
    assert "B" in tags[: mixer.BUFFER_CHUNKS + 15]
    last = np.frombuffer(rest[-1][0], "<i2").reshape(-1, 2) / 32767
    assert abs(last[0, 0] - 0.3) < 0.01


def test_pacer_never_bursts_after_a_pause():
    with patch("custom_components.home_radio.mixer.time.monotonic", side_effect=[0.0, 0.0, 0.0, 10.0]):
        p = mixer.Pacer()
        assert p.delay_for(2.0) == pytest.approx(1.0)  # 1 s ahead allowed
        p.delay_for(0.0)
        # ten seconds of VLC paused: no catching up in a rush, and no long wait either
        assert p.delay_for(0.1) == pytest.approx(0.0, abs=0.11)


@pytest.mark.mixer
async def test_player_plays_queue_through_the_mixed_stream(hass, calls, hass_client_no_auth):  # noqa: F811
    def resolved(hass_, url):
        vid = url.rsplit("=", 1)[-1]
        return ResolvedAudio("https://audio/" + vid, {"v0": "A", "v1": "B"}.get(vid, vid), "Artist", 3, "thumb",
                             data=b"x")

    with patch(f"{MP}.async_search", return_value=_tracks()), patch(f"{MP}.async_resolve", side_effect=resolved), \
         patch("custom_components.home_radio.mixer.decode", side_effect=tone):
        await hass.services.async_call("number", "set_value", {"entity_id": "number.home_radio_crossfade", "value": 1},
                                       blocking=True)
        await hass.services.async_call(DOMAIN, "play_youtube", {"entity_id": ENTITY, "query": "rock"}, blocking=True)
        url = plays(calls)[-1][1]
        assert url.startswith("/api/home_radio/mix/")  # VLC gets one stream
        await _target(hass, "playing")
        client = await hass_client_no_auth()
        resp = await client.get(url)
        assert resp.status == 200 and resp.headers["Content-Type"] == "audio/wav"
        header = await resp.content.readexactly(44)
        assert header[:4] == b"RIFF" and header[8:12] == b"WAVE"
        titles = set()
        got = 0
        while got < 6 * mixer.RATE * mixer.BYTES_PER_FRAME:  # 6 s of sound: A, A+B, B, ...
            data = await resp.content.read(65536)
            assert data, f"the stream ended after {got} bytes"
            got += len(data)
            titles.add(hass.states.get(ENTITY).attributes.get("media_title"))
        resp.close()
        assert {"A", "B"} <= titles  # the title followed what was heard
        assert len(plays(calls)) == 1  # ... with no new play_media for VLC
        st = hass.states.get(ENTITY)
        assert st.attributes["queue_index"] >= 1 and st.attributes["media_duration"] == 3
        # Next from the app/card: mixed in on the same stream, still no new play_media
        await hass.services.async_call("media_player", "media_next_track", {"entity_id": ENTITY}, blocking=True)
        assert len(plays(calls)) == 1
        # a radio station takes over: the mix is closed (and its thread ends)
        player = next(iter(hass.data[DOMAIN].values()))["player"]
        session = player._mix
        await hass.services.async_call("media_player", "select_source", {"entity_id": ENTITY, "source": "Fly FM"},
                                       blocking=True)
        assert player._mix is None and session.closed.is_set()
        await hass.async_add_executor_job(session._thread.join, 5)
        assert not session._thread.is_alive()
