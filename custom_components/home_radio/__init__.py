"""Home Radio — the TV app's radio stations and its YouTube trick, played by Home Assistant itself
through the speakers of the machine it runs on (via the VLC add-on, or any other media player)."""

from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_interval

from .const import CARD_URL, CONF_TV_URLS, DOMAIN, SERVICE_REFRESH_STATIONS, STATION_SYNC_INTERVAL_S
from .playlists import PlaylistStore
from .stations import StationStore
from .stream_view import YoutubeStreamView

PLATFORMS: list[Platform] = [Platform.MEDIA_PLAYER, Platform.TEXT]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


def tv_urls(entry: ConfigEntry) -> list[str]:
    raw = entry.options.get(CONF_TV_URLS, entry.data.get(CONF_TV_URLS, ""))
    return [u.strip() for u in raw.replace("\n", ",").split(",") if u.strip()]


async def _async_register_card(hass: HomeAssistant) -> None:
    """Serves the dashboard card from inside the integration and loads it on every dashboard, so
    `custom:home-radio-card` works without adding a resource by hand."""
    if (
        hass.data.get(f"{DOMAIN}_card")
        or getattr(hass, "http", None) is None
        or "frontend" not in hass.config.components
    ):
        return
    from homeassistant.components.frontend import add_extra_js_url
    from homeassistant.components.http import StaticPathConfig

    hass.data[f"{DOMAIN}_card"] = True
    path = Path(__file__).parent / "frontend" / "home-radio-card.js"
    await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(path), False)])
    manifest = await hass.async_add_executor_job(
        lambda: json.loads((Path(__file__).parent / "manifest.json").read_text(encoding="utf-8"))
    )
    add_extra_js_url(hass, f"{CARD_URL}?v={manifest['version']}")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if not hass.data.get(f"{DOMAIN}_view") and getattr(hass, "http", None) is not None:
        hass.data[f"{DOMAIN}_view"] = True
        hass.http.register_view(YoutubeStreamView())
    stations = StationStore(hass, entry.entry_id, tv_urls(entry))
    await stations.async_load()
    playlists = PlaylistStore(hass, entry.entry_id)
    await playlists.async_load()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {"stations": stations, "playlists": playlists}

    await _async_register_card(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # The TV only answers while its app's playback service is up, so this keeps trying gently.
    entry.async_create_background_task(hass, stations.async_sync(), "home_radio_station_sync")
    entry.async_on_unload(
        async_track_time_interval(
            hass, lambda _now: hass.async_create_task(stations.async_sync()),
            timedelta(seconds=STATION_SYNC_INTERVAL_S),
        )
    )
    entry.async_on_unload(entry.add_update_listener(_async_reload))

    if not hass.services.has_service(DOMAIN, SERVICE_REFRESH_STATIONS):
        async def _refresh(call: ServiceCall) -> None:
            for data in hass.data.get(DOMAIN, {}).values():
                await data["stations"].async_sync()

        hass.services.async_register(DOMAIN, SERVICE_REFRESH_STATIONS, _refresh)
    return True


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded
