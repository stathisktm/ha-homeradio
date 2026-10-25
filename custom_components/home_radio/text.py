"""A text box that plays whatever is typed into it on YouTube — handy on a dashboard, in a script,
or from Assist ("set YouTube to ...")."""

from __future__ import annotations

from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([YoutubeSearchText(hass, entry)])


class YoutubeSearchText(TextEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "youtube_search"
    _attr_icon = "mdi:youtube"
    _attr_native_max = 200

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_youtube_search"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})
        self._attr_native_value = ""

    async def async_set_value(self, value: str) -> None:
        self._attr_native_value = value
        self.async_write_ha_state()
        if value.strip():
            player = self.hass.data[DOMAIN][self._entry.entry_id]["player"]
            await player.async_play_youtube(query=value.strip())
