"""Real crossfades: Home Assistant decodes the YouTube tracks itself and hands VLC one endless
stream, in which the end of each track and the start of the next play together for a few
seconds — the way a DJ mixes. VLC (and the speakers) stay as they are; what VLC plays is just
"a radio station" served by Home Radio.

The decoding/mixing runs in a worker thread and fills a small buffer; the HTTP view sends it to
VLC at the pace it plays, so a change (next track, a song picked from the queue) is heard within
a second or two."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
import io
import logging
import queue
import secrets
import struct
import threading
import time
from typing import Any

_LOGGER = logging.getLogger(__name__)

RATE = 48_000
CHANNELS = 2
BYTES_PER_FRAME = CHANNELS * 2  # s16
CHUNK_FRAMES = RATE // 10  # 100 ms
BUFFER_CHUNKS = 30  # decoded ahead of what's been sent: 3 s
# How far ahead of real time the stream is sent; VLC buffers about this much more itself.
LEAD_S = 1.0
VLC_CACHE_S = 1.0
# A track picked by hand (next, previous, from the queue) still mixes in, just quickly.
QUICK_FADE_S = 1.5

try:  # PyAV and numpy come with Home Assistant (its camera stream uses them)
    import av
    import numpy as np
except ImportError:  # pragma: no cover - then Home Radio falls back to starting tracks early
    av = None
    np = None

AVAILABLE = av is not None


def wav_header() -> bytes:
    """A WAV header for a stream of unknown length (VLC plays it as it comes)."""
    size = 0xFFFFFFFF
    return (b"RIFF" + struct.pack("<I", size) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, CHANNELS, RATE,
            RATE * BYTES_PER_FRAME, BYTES_PER_FRAME, 16) + b"data" + struct.pack("<I", size))


@dataclass
class Track:
    """What the mixer plays: a tag the player knows it by, the audio, and where to start."""
    tag: Any
    audio: Any  # ResolvedAudio (needs .opener; .duration as a hint)
    start_s: float = 0.0


def fetch(audio: Any) -> bytes:
    """The whole audio file (a few MB). Asked for with a Range header, as VLC does: without it
    YouTube trickles the stream at about real-time speed and eventually drops it."""
    resp = audio.opener({"Range": "bytes=0-"})
    try:
        return resp.read()
    finally:
        close = getattr(resp, "close", None)
        if close:
            close()


def decode(track: Track) -> Iterator["np.ndarray"]:
    """The track as float32 stereo frames at RATE, in pieces, from start_s."""
    data = getattr(track.audio, "data", None) or fetch(track.audio)
    container = av.open(io.BytesIO(data), mode="r")
    try:
        stream = container.streams.audio[0]
        if track.start_s > 1 and stream.time_base:
            container.seek(int(track.start_s / stream.time_base), stream=stream, any_frame=False, backward=True)
        resampler = av.AudioResampler(format="flt", layout="stereo", rate=RATE)
        skip = None  # frames to drop after the seek, worked out from the first frame's time
        for packet in container.demux(stream):
            for frame in packet.decode():
                if skip is None:
                    first = float(frame.pts * frame.time_base) if frame.pts is not None else 0.0
                    skip = max(0, int((track.start_s - first) * RATE))
                for out in resampler.resample(frame):
                    pcm = out.to_ndarray().reshape(-1, CHANNELS)
                    if skip:
                        if skip >= len(pcm):
                            skip -= len(pcm)
                            continue
                        pcm, skip = pcm[skip:], 0
                    yield pcm
        for out in resampler.resample(None):
            yield out.to_ndarray().reshape(-1, CHANNELS)
    finally:
        container.close()


_DECODE = decode  # the real one (tests swap in synthetic audio)

SILENCE = 0.003  # about -50 dBFS
TAIL_SCAN_S = 30


def audible_end(data: bytes) -> float | None:
    """Where the sound really ends: many uploads finish with seconds of silence, and mixing
    into silence would just sound like a gap. Only the last TAIL_SCAN_S are decoded."""
    try:
        container = av.open(io.BytesIO(data), mode="r")
    except Exception:  # noqa: BLE001 - unusual files: no trimming
        return None
    try:
        stream = container.streams.audio[0]
        total = float(stream.duration * stream.time_base) if stream.duration else (container.duration or 0) / 1e6
        if total <= TAIL_SCAN_S:
            return None
        container.seek(int((total - TAIL_SCAN_S) / stream.time_base), stream=stream, backward=True)
        resampler = av.AudioResampler(format="flt", layout="mono", rate=RATE // 10)
        last_loud, t = None, None
        for frame in container.decode(stream):
            if t is None:
                t = float(frame.pts * frame.time_base) if frame.pts is not None else total - TAIL_SCAN_S
            for out in resampler.resample(frame):
                pcm = out.to_ndarray().reshape(-1)
                step = len(pcm) / (RATE // 10)
                if pcm.size and float(np.sqrt(np.mean(pcm * pcm))) > SILENCE:
                    last_loud = t + step
                t += step
        return last_loud
    except Exception:  # noqa: BLE001 - unusual files: no trimming
        return None
    finally:
        container.close()


class _Reader:
    """Pulls fixed-size chunks from a decoder; knows how far into the track it is."""

    def __init__(self, track: Track, decoder: Callable[[Track], Iterator[Any]]) -> None:
        self.track = track
        self._it = decoder(track)
        self._rest = np.zeros((0, CHANNELS), dtype=np.float32)
        self.frames = int(track.start_s * RATE)
        self.done = False
        duration = getattr(track.audio, "duration", 0) or 0
        data = getattr(track.audio, "data", None)
        end = audible_end(data) if data and decoder is _DECODE else None
        if end:
            duration = min(duration, end) if duration > 0 else end
        self.length = int(duration * RATE) if duration > 0 else None

    @property
    def remaining(self) -> int | None:
        return None if self.length is None else self.length - self.frames

    def read(self, n: int) -> "np.ndarray":
        parts, have = [self._rest], len(self._rest)
        while have < n and not self.done:
            try:
                pcm = next(self._it)
            except StopIteration:
                self.done = True
                break
            parts.append(pcm.astype(np.float32, copy=False))
            have += len(pcm)
        data = np.concatenate(parts) if len(parts) > 1 else parts[0]
        out, self._rest = data[:n], data[n:]
        self.frames += len(out)
        if self.done and not len(self._rest) and self.length is not None:
            self.length = self.frames  # the real end
        return out


class MixSession:
    """One endless stream. `ask_next()` (called from the worker thread) returns the next Track,
    or None when there's nothing more; chunks carry (tag, position_s) of what's being heard."""

    def __init__(self, first: Track, ask_next: Callable[[], Track | None], crossfade_s: Callable[[], float],
                 decoder: Callable[[Track], Iterator[Any]] | None = None) -> None:
        self.token = secrets.token_urlsafe(12)
        self._ask_next = ask_next
        self._crossfade_s = crossfade_s
        self._decoder = decoder or decode
        self._out: queue.Queue = queue.Queue(maxsize=BUFFER_CHUNKS)
        self._cmd: queue.Queue = queue.Queue()
        self._first = first
        self.closed = threading.Event()
        self.ended = False
        # Called (in the event loop) with (tag, position_s) as the stream is sent.
        self.on_heard: Callable[[Any, float], None] | None = None
        self._thread = threading.Thread(target=self._run, name="home_radio_mixer", daemon=True)
        self._thread.start()

    # ---- control (from the event loop) ------------------------------------------------------

    def jump(self, track: Track) -> None:
        """Mix quickly into a track picked by hand."""
        self._cmd.put(("jump", track))

    def close(self) -> None:
        self.closed.set()
        self._cmd.put(("close", None))

    def get(self, poll: float = 0.2) -> tuple[bytes, Any, float] | None:
        """Next chunk for the listener: (pcm bytes, tag, position_s); None once the stream has
        ended (or was closed). Waits while the next track is still being fetched."""
        while not self.closed.is_set():
            try:
                return self._out.get(timeout=poll)
            except queue.Empty:
                if self.ended:
                    return None
        return None

    # ---- worker thread ------------------------------------------------------------------------

    def _put(self, pcm: "np.ndarray", reader: _Reader) -> bool:
        data = (np.clip(pcm, -1.0, 1.0) * 32767).astype("<i2").tobytes()
        item = (data, reader.track.tag, reader.frames / RATE)
        while not self.closed.is_set():
            try:
                self._out.put(item, timeout=0.5)
                return True
            except queue.Full:
                continue
        return False

    def _open(self, track: Track | None) -> _Reader | None:
        if track is None:
            return None
        try:
            return _Reader(track, self._decoder)
        except Exception:  # noqa: BLE001 - a broken stream: skip it
            _LOGGER.warning("home_radio mixer: could not open %s", track.tag, exc_info=True)
            return None

    def _run(self) -> None:
        try:
            self._mix(self._open(self._first))
        except Exception:  # noqa: BLE001 - never take HA down; the stream just ends
            _LOGGER.exception("home_radio mixer stopped")
        finally:
            self.ended = True

    def _mix(self, cur: _Reader | None) -> None:
        nxt: _Reader | None = None
        fade_total = fade_done = 0
        asked = False
        while cur is not None and not self.closed.is_set():
            # commands
            try:
                cmd, arg = self._cmd.get_nowait()
            except queue.Empty:
                cmd = None
            if cmd == "close":
                return
            if cmd == "jump":
                picked = self._open(arg)
                if picked is not None:
                    nxt, fade_total, fade_done, asked = picked, int(QUICK_FADE_S * RATE), 0, True

            # time to bring the next one in?
            xf = int(max(0.0, self._crossfade_s()) * RATE)
            rem = cur.remaining
            if nxt is None and not asked and (cur.done or (rem is not None and rem <= max(xf, CHUNK_FRAMES))):
                asked = True
                nxt = self._open(self._ask_next())
                fade_total = max(1, min(xf, rem if rem and rem > 0 else xf)) if nxt is not None else 0
                fade_done = 0

            a = cur.read(CHUNK_FRAMES)
            if nxt is not None and fade_total:
                b = nxt.read(CHUNK_FRAMES)
                n = max(len(a), len(b))
                t = (fade_done + np.arange(n, dtype=np.float32)) / fade_total
                t = np.clip(t, 0.0, 1.0)[:, None]
                # equal-power: the sum doesn't dip in the middle
                ga, gb = np.cos(t * np.pi / 2), np.sin(t * np.pi / 2)
                mix = np.zeros((n, CHANNELS), dtype=np.float32)
                mix[: len(a)] += a * ga[: len(a)]
                mix[: len(b)] += b * gb[: len(b)]
                fade_done += n
                # what's heard is the new track from halfway through the fade
                lead = nxt if fade_done * 2 >= fade_total else cur
                if not self._put(mix, lead):
                    return
                if fade_done >= fade_total or (cur.done and not len(a)):
                    cur, nxt, fade_total, fade_done, asked = nxt, None, 0, 0, False
                continue
            if not len(a):
                if cur.done:
                    # ended with nothing mixed in (no next track, or crossfade 0)
                    if not asked:
                        asked = True
                        nxt = self._open(self._ask_next())
                    cur, nxt, asked = nxt, None, False
                continue
            if not self._put(a, cur):
                return


class Pacer:
    """Sends at the pace of playback, LEAD_S ahead; after a pause (VLC stops reading) it starts
    counting again instead of rushing to catch up."""

    def __init__(self) -> None:
        self._t0 = time.monotonic()
        self._sent = 0.0

    def delay_for(self, seconds: float) -> float:
        now = time.monotonic()
        if self._sent < now - self._t0 - LEAD_S:
            self._t0 = now - self._sent
        self._sent += seconds
        return max(0.0, self._sent - (now - self._t0) - LEAD_S)
