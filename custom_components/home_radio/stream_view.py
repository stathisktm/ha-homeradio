"""YouTube audio is relayed through Home Assistant itself. The player (VLC) is handed a short HA
URL — `/api/home_radio/yt/<token>` — and HA fetches the googlevideo.com stream and passes the bytes
on, Range requests included (seeking). Two reasons: a resolved googlevideo URL is ~1100 characters
while VLC's telnet interface reads at most 1024 per command, and this way the player never has to
talk to YouTube at all (no redirects, TLS or request headers on its side)."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
import secrets
from typing import Any

import aiohttp
from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN
from .debuglog import dlog

STREAM_PATH = "/api/home_radio/yt"
_KEY = f"{DOMAIN}_streams"
_MAX_TOKENS = 64
_CHUNK = 64 * 1024
_UPSTREAM_TIMEOUT = aiohttp.ClientTimeout(total=None, connect=15, sock_read=60)
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_PASS_HEADERS = ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges", "Last-Modified", "ETag")


def _tokens(hass: HomeAssistant) -> OrderedDict[str, tuple[str, dict[str, str], Any]]:
    return hass.data.setdefault(_KEY, OrderedDict())


def short_url(
    hass: HomeAssistant, stream_url: str, headers: dict[str, str] | None = None, opener: Any = None,
) -> str:
    """Registers [stream_url] — with the request headers yt-dlp says it needs and, when given, the
    yt-dlp opener that must fetch it — under a fresh unguessable token; returns its short path."""
    tokens = _tokens(hass)
    token = secrets.token_urlsafe(16)
    tokens[token] = (stream_url, headers or {}, opener)
    while len(tokens) > _MAX_TOKENS:
        tokens.popitem(last=False)
    return f"{STREAM_PATH}/{token}"


def resolve_token(hass: HomeAssistant, token: str) -> str | None:
    entry = _tokens(hass).get(token)
    return entry[0] if entry else None


class YoutubeStreamView(HomeAssistantView):
    """No auth: the player can't send any — the random token is the credential, and it only ever
    leads to a public, short-lived YouTube audio stream."""

    url = STREAM_PATH + "/{token}"
    name = "api:home_radio:yt"
    requires_auth = False

    async def head(self, request: web.Request, token: str) -> web.StreamResponse:
        return await self._relay(request, token, body=False)

    async def get(self, request: web.Request, token: str) -> web.StreamResponse:
        return await self._relay(request, token, body=True)

    async def _relay(self, request: web.Request, token: str, body: bool) -> web.StreamResponse:
        hass: HomeAssistant = request.app["hass"]
        entry = _tokens(hass).get(token)
        who = f"{request.method} from {request.remote} ua={request.headers.get('User-Agent', '-')[:40]} range={request.headers.get('Range', '-')}"
        if entry is None:
            dlog(hass, f"VIEW unknown token ({who})")
            return web.Response(status=404)
        url, extra, opener = entry
        if opener is not None:
            return await self._relay_ytdlp(hass, request, opener, body, who)
        headers = {"User-Agent": _BROWSER_UA, **extra}
        headers.pop("Accept-Encoding", None)
        if "Range" in request.headers:
            headers["Range"] = request.headers["Range"]
        session = async_get_clientsession(hass)
        try:
            upstream = await session.request(
                "GET" if body else "HEAD", url, headers=headers, timeout=_UPSTREAM_TIMEOUT, allow_redirects=True,
            )
        except Exception as err:  # noqa: BLE001 - any upstream failure is a 502 for the player, and logged
            dlog(hass, f"VIEW upstream connect failed: {err!r} ({who})")
            return web.Response(status=502)
        dlog(hass, f"VIEW {who} -> upstream {upstream.status} len={upstream.headers.get('Content-Length', '?')}")
        response = web.StreamResponse(status=upstream.status)
        for name in _PASS_HEADERS:
            if name in upstream.headers:
                response.headers[name] = upstream.headers[name]
        sent = 0
        try:
            await response.prepare(request)
            if body:
                async for chunk in upstream.content.iter_chunked(_CHUNK):
                    await response.write(chunk)
                    sent += len(chunk)
            await response.write_eof()
        except (ConnectionResetError, asyncio.CancelledError, aiohttp.ClientError) as err:
            # The player closing the connection (stop, skip, seek) is normal.
            dlog(hass, f"VIEW relay ended after {sent} bytes: {type(err).__name__}")
            if isinstance(err, asyncio.CancelledError):
                raise
        finally:
            upstream.release()
        return response

    async def _relay_ytdlp(self, hass: HomeAssistant, request: web.Request, opener: Any, body: bool, who: str) -> web.StreamResponse:
        """Streams through the yt-dlp session that resolved the URL (blocking I/O, so each read
        runs in the executor)."""
        extra = {"Range": request.headers["Range"]} if "Range" in request.headers else {}
        try:
            upstream = await hass.async_add_executor_job(opener, extra)
        except Exception as err:  # noqa: BLE001 - yt-dlp's HTTPError carries the status
            status = getattr(getattr(err, "response", None), "status", None) or getattr(err, "status", None)
            dlog(hass, f"VIEW yt-dlp fetch failed status={status} {type(err).__name__}: {str(err)[:160]} ({who})")
            return web.Response(status=502)
        status = getattr(upstream, "status", 200)
        dlog(hass, f"VIEW {who} -> yt-dlp upstream {status} len={upstream.headers.get('Content-Length', '?')}")
        response = web.StreamResponse(status=status)
        for name in _PASS_HEADERS:
            value = upstream.headers.get(name)
            if value:
                response.headers[name] = value
        sent = 0
        try:
            await response.prepare(request)
            if body and request.method != "HEAD":
                while chunk := await hass.async_add_executor_job(upstream.read, _CHUNK):
                    await response.write(chunk)
                    sent += len(chunk)
            await response.write_eof()
            dlog(hass, f"VIEW relay finished, {sent} bytes")
        except (ConnectionResetError, asyncio.CancelledError, aiohttp.ClientError) as err:
            dlog(hass, f"VIEW relay ended after {sent} bytes: {type(err).__name__}")
            if isinstance(err, asyncio.CancelledError):
                raise
        except Exception as err:  # noqa: BLE001 - a read error mid-stream just ends this response
            dlog(hass, f"VIEW relay read error after {sent} bytes: {err!r}")
        finally:
            await hass.async_add_executor_job(upstream.close)
        return response


# ---- the mixed stream ------------------------------------------------------------------------

MIX_PATH = "/api/home_radio/mix"
_MIX_KEY = f"{DOMAIN}_mixes"


def register_mix(hass: HomeAssistant, session: Any) -> str:
    """Makes a MixSession reachable for the player; returns its path."""
    mixes = hass.data.setdefault(_MIX_KEY, {})
    for token in [t for t, s in mixes.items() if s.closed.is_set()]:
        mixes.pop(token)
    mixes[session.token] = session
    return f"{MIX_PATH}/{session.token}"


class MixStreamView(HomeAssistantView):
    """The endless WAV stream a MixSession makes (no auth, like the relay: the token is it)."""

    url = MIX_PATH + "/{token}"
    name = "api:home_radio:mix"
    requires_auth = False

    async def head(self, request: web.Request, token: str) -> web.StreamResponse:
        if token not in request.app["hass"].data.get(_MIX_KEY, {}):
            return web.Response(status=404)
        return web.Response(headers={"Content-Type": "audio/wav", "Cache-Control": "no-cache"})

    async def get(self, request: web.Request, token: str) -> web.StreamResponse:
        from . import mixer

        hass: HomeAssistant = request.app["hass"]
        session = hass.data.get(_MIX_KEY, {}).get(token)
        if session is None or session.closed.is_set():
            return web.Response(status=404)
        dlog(hass, f"MIXVIEW GET from {request.remote} ua={request.headers.get('User-Agent', '-')[:30]}")
        response = web.StreamResponse(headers={"Content-Type": "audio/wav", "Cache-Control": "no-cache"})
        await response.prepare(request)
        pacer, sent = mixer.Pacer(), 0
        try:
            await response.write(mixer.wav_header())
            while True:
                item = await hass.async_add_executor_job(session.get)
                if item is None:
                    break
                data, tag, pos = item
                await response.write(data)
                sent += len(data)
                if session.on_heard is not None:
                    session.on_heard(tag, pos)
                delay = pacer.delay_for(len(data) / (mixer.RATE * mixer.BYTES_PER_FRAME))
                if delay:
                    await asyncio.sleep(delay)
            await response.write_eof()
            dlog(hass, f"MIXVIEW finished after {sent} bytes")
        except (ConnectionResetError, asyncio.CancelledError, aiohttp.ClientError) as err:
            # VLC stopping, or switching to something else. The session is closed by the player.
            dlog(hass, f"MIXVIEW ended after {sent} bytes: {type(err).__name__}")
            if isinstance(err, asyncio.CancelledError):
                raise
        return response
