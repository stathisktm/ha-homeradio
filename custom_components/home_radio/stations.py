"""The station list: the bundled copy of the TV app's stations, replaced by the TV app's own live
list (hand-added stations, renames) whenever it can be reached, and remembered across restarts —
the TV only serves it while the app's playback service is up."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import json
import logging
from pathlib import Path
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .const import DOMAIN, STORAGE_VERSION, TV_STATIONS_PATH, TV_STATIONS_TIMEOUT_S

_LOGGER = logging.getLogger(__name__)


def _from_tv(item: dict[str, Any]) -> dict[str, str] | None:
    if not item.get("id") or not item.get("streamUrl"):
        return None
    logo = item.get("logoUrl") or ""
    # Logos picked from a file on the TV are content:// URIs only that TV can open.
    if not logo.startswith("http"):
        logo = ""
    return {
        "id": item["id"],
        "name": item.get("name") or item["id"],
        "url": item["streamUrl"],
        "logo": logo,
        "category": item.get("category") or "",
    }


class StationStore:
    def __init__(self, hass: HomeAssistant, entry_id: str, tv_urls: list[str]) -> None:
        self.hass = hass
        self._tv_urls = tv_urls
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}")
        self.stations: list[dict[str, str]] = []
        self.source = "bundled"
        self._listeners: list[Callable[[], None]] = []

    def add_listener(self, callback: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(callback)
        return lambda: self._listeners.remove(callback)

    async def async_load(self) -> None:
        saved = await self._store.async_load()
        if saved and saved.get("stations"):
            self.stations = saved["stations"]
            self.source = saved.get("source", "tv")
        else:
            path = Path(__file__).parent / "stations.json"
            self.stations = await self.hass.async_add_executor_job(
                lambda: json.loads(path.read_text(encoding="utf-8"))
            )

    async def async_sync(self) -> bool:
        """Takes the first TV that answers; keeps the current list if none does."""
        session = async_get_clientsession(self.hass)
        for base in self._tv_urls:
            url = base.rstrip("/") + TV_STATIONS_PATH
            try:
                async with asyncio.timeout(TV_STATIONS_TIMEOUT_S):
                    resp = await session.get(url)
                    resp.raise_for_status()
                    data = await resp.json(content_type=None)
            except (aiohttp.ClientError, TimeoutError, ValueError):
                _LOGGER.debug("home_radio: no station list from %s", url)
                continue
            stations = [s for s in map(_from_tv, data if isinstance(data, list) else []) if s]
            if not stations:
                continue
            if stations != self.stations:
                self.stations = stations
                self.source = base
                await self._store.async_save({"stations": stations, "source": base})
                for callback in list(self._listeners):
                    callback()
                _LOGGER.info("home_radio: %d stations synced from %s", len(stations), base)
            return True
        return False

    def find(self, key: str) -> dict[str, str] | None:
        key_l = key.strip().lower()
        for station in self.stations:
            if station["id"] == key or station["name"].lower() == key_l:
                return station
        return None
