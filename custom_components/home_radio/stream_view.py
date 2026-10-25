"""YouTube audio is relayed through Home Assistant itself. The player (VLC) is handed a short HA
URL — `/api/home_radio/yt/<token>` — and HA fetches the googlevideo.com stream and passes the bytes
on, Range requests included (seeking). Two reasons: a resolved googlevideo URL is ~1100 characters
while VLC's telnet interface reads at most 1024 per command, and this way the player never has to
talk to YouTube at all (no redirects, TLS or request headers on its side)."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
import secrets

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


def _tokens(hass: HomeAssistant) -> OrderedDict[str, tuple[str, dict[str, str]]]:
    return hass.data.setdefault(_KEY, OrderedDict())


def short_url(hass: HomeAssistant, stream_url: str, headers: dict[str, str] | None = None) -> str:
    """Registers [stream_url] (and the request headers yt-dlp says it needs) under a fresh
    unguessable token and returns its short path."""
    tokens = _tokens(hass)
    token = secrets.token_urlsafe(16)
    tokens[token] = (stream_url, headers or {})
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
        url, extra = entry
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
