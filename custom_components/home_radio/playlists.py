"""Saved lists: every Spotify playlist/album that's played is kept, so it plays again with one tap
from the card or the phone app. Each song also remembers the YouTube video it was matched to,
so a saved list starts without searching again. Kept in HA's storage across restarts."""

from __future__ import annotations

from collections.abc import Callable
import time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN, STORAGE_VERSION
from .spotify import SpotifyList, SpotifySong

SAVE_DELAY_S = 10


class PlaylistStore:
    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.playlists")
        self._lists: dict[str, dict[str, Any]] = {}
        self._listeners: list[Callable[[], None]] = []

    def add_listener(self, callback: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(callback)
        return lambda: self._listeners.remove(callback)

    def _changed(self) -> None:
        self._store.async_delay_save(lambda: {"lists": self._lists}, SAVE_DELAY_S)
        for cb in list(self._listeners):
            cb()

    async def async_load(self) -> None:
        saved = await self._store.async_load()
        self._lists = (saved or {}).get("lists") or {}

    def get(self, list_id: str) -> dict[str, Any] | None:
        return self._lists.get(list_id)

    def find(self, key: str) -> dict[str, Any] | None:
        """By id, or by name (case-insensitive)."""
        if key in self._lists:
            return self._lists[key]
        key = key.strip().lower()
        return next((v for v in self._lists.values() if v["name"].lower() == key), None)

    def summary(self) -> list[dict[str, Any]]:
        """For the card and the phone app: most recently played first."""
        ordered = sorted(self._lists.values(), key=lambda v: v.get("played", 0), reverse=True)
        return [{"id": v["id"], "name": v["name"], "image": v.get("image", ""), "kind": v["kind"],
                 "count": len(v["songs"])} for v in ordered]

    def save(self, list_id: str, url: str, found: SpotifyList) -> dict[str, Any]:
        """Keeps (or refreshes) a list as Spotify has it now; matches already found for songs
        that are still in it are kept."""
        old = {(s["title"], s["artists"]): s.get("video_id", "") for s in (self._lists.get(list_id) or {}).get("songs", [])}
        entry = {
            "id": list_id,
            "url": url,
            "kind": found.kind,
            "name": found.name or "Spotify",
            "image": found.image or (self._lists.get(list_id) or {}).get("image", ""),
            "songs": [{"title": s.title, "artists": s.artists, "duration": s.duration,
                       "video_id": old.get((s.title, s.artists), "")} for s in found.songs],
            "played": time.time(),
        }
        self._lists[list_id] = entry
        self._changed()
        return entry

    def songs(self, entry: dict[str, Any]) -> list[tuple[SpotifySong, str]]:
        return [(SpotifySong(s["title"], s["artists"], s.get("duration", 0)), s.get("video_id", ""))
                for s in entry["songs"]]

    def touch(self, list_id: str) -> None:
        if list_id in self._lists:
            self._lists[list_id]["played"] = time.time()
            self._changed()

    def remember_match(self, list_id: str, index: int, video_id: str, thumbnail: str) -> None:
        entry = self._lists.get(list_id)
        if not entry or not 0 <= index < len(entry["songs"]) or entry["songs"][index].get("video_id") == video_id:
            return
        entry["songs"][index]["video_id"] = video_id
        if not entry.get("image"):
            entry["image"] = thumbnail  # albums have no cover in the embed: use the first song's
        self._changed()

    def remove(self, list_id: str) -> bool:
        if self._lists.pop(list_id, None) is None:
            return False
        self._changed()
        return True
