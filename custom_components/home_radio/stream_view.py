"""Short stand-in URLs for YouTube audio streams. A resolved googlevideo.com URL is ~1100
characters, and VLC's telnet interface (what the VLC add-on integration drives) reads at most 1024
per command line, so the URL arrived truncated and VLC silently failed to open it. The player is
handed `/api/home_radio/yt/<token>` instead, which redirects to the real stream."""

from __future__ import annotations

from collections import OrderedDict
import secrets

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN

STREAM_PATH = "/api/home_radio/yt"
_KEY = f"{DOMAIN}_streams"
_MAX_TOKENS = 64


def _tokens(hass: HomeAssistant) -> OrderedDict[str, str]:
    return hass.data.setdefault(_KEY, OrderedDict())


def short_url(hass: HomeAssistant, stream_url: str) -> str:
    """Registers [stream_url] under a fresh unguessable token and returns its short path."""
    tokens = _tokens(hass)
    token = secrets.token_urlsafe(16)
    tokens[token] = stream_url
    while len(tokens) > _MAX_TOKENS:
        tokens.popitem(last=False)
    return f"{STREAM_PATH}/{token}"


def resolve_token(hass: HomeAssistant, token: str) -> str | None:
    return _tokens(hass).get(token)


class YoutubeStreamView(HomeAssistantView):
    """No auth: VLC can't send any — the random token is the credential, and it only ever leads
    to a public, short-lived YouTube audio URL."""

    url = STREAM_PATH + "/{token}"
    name = "api:home_radio:yt"
    requires_auth = False

    async def get(self, request: web.Request, token: str) -> web.StreamResponse:
        target = resolve_token(request.app["hass"], token)
        if target is None:
            return web.Response(status=404)
        raise web.HTTPFound(target)
