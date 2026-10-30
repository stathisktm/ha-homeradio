"""Spotify links (playlist, album, track) turned into a list of songs, each later played from YouTube.

Only the public embed page is read — the one Spotify serves for its "embed this playlist" widget —
so no account, Premium or API key is needed. It lists up to 100 tracks of a playlist."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import re

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

# open.spotify.com/[intl-xx/]playlist/<id>, spotify:album:<id>, spotify.link short links aren't
# expanded (they redirect through an app page), so those are asked to be opened in full.
_LINK = re.compile(r"(?:open\.spotify\.com/(?:intl-[a-z-]+/)?|spotify:)(playlist|album|track)[/:]([A-Za-z0-9]{22})")
# YouTube / YouTube Music playlists, albums and mixes: any link carrying list=.
_YT_LIST = re.compile(r"(?:youtube\.com|youtu\.be)\S*?[?&]list=([A-Za-z0-9_-]{10,})")
_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)
EMBED_URL = "https://open.spotify.com/embed/{kind}/{id}"
FETCH_TIMEOUT_S = 20


@dataclass
class SpotifySong:
    title: str
    artists: str
    duration: int  # seconds, 0 if unknown
    video_id: str = ""  # known already for a song from a YouTube list

    @property
    def query(self) -> str:
        return f"{self.artists} - {self.title}" if self.artists else self.title


@dataclass
class SpotifyList:
    kind: str
    name: str
    image: str
    songs: list[SpotifySong]


def parse_link(text: str) -> tuple[str, str] | None:
    """(kind, id) of a Spotify playlist/album/track link anywhere in the text — a shared message
    like "Listen to X on Spotify: https://open.spotify.com/…" works too."""
    m = _LINK.search(text or "")
    if m:
        return (m.group(1), m.group(2))
    m = _YT_LIST.search(text or "")
    return ("ytlist", m.group(1)) if m else None


def is_link(text: str) -> bool:
    return parse_link(text) is not None


def _image(entity: dict) -> str:
    # Playlists carry their cover; albums and single tracks don't, their songs get YouTube's.
    sources = (entity.get("coverArt") or {}).get("sources") or []
    return next((s["url"] for s in sources if s.get("url")), "")


def parse_embed(html: str) -> SpotifyList | None:
    m = _NEXT_DATA.search(html)
    if not m:
        return None
    try:
        entity = json.loads(m.group(1))["props"]["pageProps"]["state"]["data"]["entity"]
    except (KeyError, TypeError, ValueError):
        return None  # not found / private: the page has no list in it
    kind = entity.get("type") or ""
    name = entity.get("name") or entity.get("title") or ""
    if kind == "track":
        artists = ", ".join(a.get("name", "") for a in entity.get("artists") or [] if a.get("name"))
        songs = [SpotifySong(name, artists, int((entity.get("duration") or 0) / 1000))]
    else:
        songs = [
            SpotifySong(t.get("title") or "", t.get("subtitle") or "", int((t.get("duration") or 0) / 1000))
            for t in entity.get("trackList") or []
            if t.get("title") and t.get("isPlayable", True)
        ]
    return SpotifyList(kind, name, _image(entity), songs)


async def async_fetch(hass: HomeAssistant, text: str) -> SpotifyList | None:
    link = parse_link(text)
    if link is None:
        return None
    kind, item_id = link
    if kind == "ytlist":
        from .youtube import async_playlist

        found = await async_playlist(hass, item_id)
        if not found or not found[1]:
            return None
        name, videos = found
        return SpotifyList("youtube", name, videos[0].thumbnail,
                           [SpotifySong(v.title, v.channel, v.duration, v.video_id) for v in videos])
    session = async_get_clientsession(hass)
    try:
        async with session.get(
            EMBED_URL.format(kind=kind, id=item_id),
            headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)", "Accept-Language": "el,en;q=0.8"},
            timeout=FETCH_TIMEOUT_S,
        ) as resp:
            if resp.status != 200:
                _LOGGER.warning("home_radio: Spotify %s %s -> HTTP %s", kind, item_id, resp.status)
                return None
            html = await resp.text()
        return parse_embed(html)
    except Exception:  # noqa: BLE001 - layout changes, timeouts: just "couldn't read the list"
        _LOGGER.warning("home_radio: could not read Spotify %s %s", kind, item_id, exc_info=True)
        return None
