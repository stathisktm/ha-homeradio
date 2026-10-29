"""How many seconds before a YouTube track ends the next one starts (0: when it ends). The next
track is always made ready ahead of time, so even at 0 there's barely a gap."""

from __future__ import annotations

from homeassistant.components.number import NumberMode, RestoreNumber
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later

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
        return self.hass.data[DOMAIN][self._entry.entry_id].get("player")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            self._attr_native_value = last.native_value
        self._link()

    @callback
    def _link(self, _now=None) -> None:
        """Hands the value to the player; the platforms are set up side by side, so the player
        may not be there yet."""
        player = self._player
        if player is None or player.hass is None:
            async_call_later(self.hass, 1, self._link)
            return
        player.crossfade_s = float(self._attr_native_value)
        player.on_crossfade_change = self._from_player
        player.async_write_ha_state()

    def _from_player(self, value: float) -> None:
        self._attr_native_value = value
        self.async_write_ha_state()

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self.async_write_ha_state()
        if (player := self._player) is not None and player.hass is not None:
            player.crossfade_s = float(value)
            player.async_write_ha_state()
