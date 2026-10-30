"""YouTube search and audio-stream resolution through yt-dlp — the same trick the TV app does with
NewPipeExtractor: find a video, hand the player its bare audio stream, never any video."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


@dataclass
class YoutubeTrack:
    video_id: str
    url: str
    title: str
    channel: str
    duration: int
    thumbnail: str
    # For a song from a Spotify list: what to search YouTube for. video_id/url stay empty until
    # it's looked up, just before it plays.
    query: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "video_id": self.video_id,
            "url": self.url,
            "title": self.title,
            "channel": self.channel,
            "duration": self.duration,
            "thumbnail": self.thumbnail,
        }


@dataclass
class ResolvedAudio:
    stream_url: str
    title: str
    channel: str
    duration: int
    thumbnail: str
    # What yt-dlp says a request for stream_url must carry; the relay sends it upstream.
    headers: dict[str, str] = field(default_factory=dict)
    # Opens stream_url through the SAME yt-dlp session that resolved it (same connection, IP
    # family, cookies, headers). googlevideo URLs are bound to the requester; fetched any other
    # way from the HA host they came back 403. Takes extra headers (Range), blocking.
    opener: Any = None
    diag: str = ""
    # The whole audio file, once fetched for the mixer.
    data: bytes | None = None


def _thumb(video_id: str) -> str:
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


def _search(query: str, size: int) -> list[YoutubeTrack]:
    import yt_dlp

    opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch{size}:{query}", download=False)
    tracks = []
    for entry in (info or {}).get("entries") or []:
        video_id = entry.get("id")
        if not video_id or entry.get("live_status") == "is_live":
            continue
        tracks.append(
            YoutubeTrack(
                video_id=video_id,
                url=f"https://www.youtube.com/watch?v={video_id}",
                title=entry.get("title") or "",
                channel=entry.get("channel") or entry.get("uploader") or "",
                duration=int(entry.get("duration") or 0),
                thumbnail=_thumb(video_id),
            )
        )
    return tracks


def _resolve(url: str) -> ResolvedAudio | None:
    import yt_dlp
    from yt_dlp.networking import Request

    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "format": "bestaudio/best"}
    # Deliberately not closed here: its network session is what the relay later streams through.
    ydl = yt_dlp.YoutubeDL(opts)
    info = ydl.extract_info(url, download=False)
    if not info or not info.get("url"):
        ydl.close()
        return None
    stream_url = info["url"]
    headers = {k: v for k, v in (info.get("http_headers") or {}).items() if isinstance(v, str)}

    def opener(extra: dict[str, str]):
        return ydl.urlopen(Request(stream_url, headers={**headers, **extra}))

    client = stream_url.split("&c=", 1)[1].split("&", 1)[0] if "&c=" in stream_url else "?"
    return ResolvedAudio(
        stream_url=stream_url,
        title=info.get("title") or "",
        channel=info.get("channel") or info.get("uploader") or "",
        duration=int(info.get("duration") or 0),
        thumbnail=_thumb(info["id"]) if info.get("id") else info.get("thumbnail") or "",
        headers=headers,
        opener=opener,
        diag=f"yt-dlp={yt_dlp.version.__version__} format={info.get('format_id')} client={client}",
    )


def _playlist(list_id: str, size: int) -> tuple[str, list[YoutubeTrack]] | None:
    """A YouTube / YouTube Music playlist, album or mix: its name and up to [size] videos."""
    import yt_dlp

    opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True, "playlistend": size}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/playlist?list={list_id}", download=False)
    if not info:
        return None
    tracks = [
        YoutubeTrack(
            video_id=e["id"],
            url=f"https://www.youtube.com/watch?v={e['id']}",
            title=e.get("title") or "",
            channel=e.get("channel") or e.get("uploader") or "",
            duration=int(e.get("duration") or 0),
            thumbnail=_thumb(e["id"]),
        )
        for e in info.get("entries") or []
        if e and e.get("id") and e.get("title") not in ("[Private video]", "[Deleted video]")
    ]
    return (info.get("title") or "YouTube"), tracks


async def async_playlist(hass: HomeAssistant, list_id: str, size: int = 300) -> tuple[str, list[YoutubeTrack]] | None:
    try:
        return await hass.async_add_executor_job(_playlist, list_id, size)
    except Exception:  # noqa: BLE001 - private/removed lists: just "couldn't read it"
        _LOGGER.warning("home_radio: could not read YouTube list %s", list_id, exc_info=True)
        return None


async def async_search(hass: HomeAssistant, query: str, size: int) -> list[YoutubeTrack]:
    try:
        return await hass.async_add_executor_job(_search, query, size)
    except Exception:  # noqa: BLE001 - yt-dlp raises many types; a failed search is just empty
        _LOGGER.exception("home_radio: YouTube search failed for %r", query)
        return []


async def async_resolve(hass: HomeAssistant, url: str) -> ResolvedAudio | None:
    try:
        return await hass.async_add_executor_job(_resolve, url)
    except Exception:  # noqa: BLE001 - unavailable/age-restricted videos just get skipped
        _LOGGER.warning("home_radio: could not resolve %s", url, exc_info=True)
        return None
