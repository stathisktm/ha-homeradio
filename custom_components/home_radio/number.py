"""How many seconds one YouTube track fades into the next (0 turns it off). The next track is
always made ready ahead of time, so even at 0 there's barely a gap."""

from __future__ import annotations

from homeassistant.components.number import NumberMode, RestoreNumber
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DEFAULT_CROSSFADE_S, DOMAIN, MAX_CROSSFADE_S


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([CrossfadeNumber(hass, entry)])


class CrossfadeNumber(RestoreNumber):
    _attr_has_entity_name = True
    _attr_translation_key = "crossfade"
    _attr_icon = "mdi:transition"
    _attr_native_min_value = 0
    _attr_native_max_value = MAX_CROSSFADE_S
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_mode = NumberMode.SLIDER

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_crossfade"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})
        self._attr_native_value = DEFAULT_CROSSFADE_S

    @property
    def _player(self):
        return self.hass.data[DOMAIN][self._entry.entry_id]["player"]

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            self._attr_native_value = last.native_value
        self._player.crossfade_s = float(self._attr_native_value)

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self._player.crossfade_s = float(value)
        self.async_write_ha_state()
