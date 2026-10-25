"""Config flow: which player makes the sound, and where the TV app's station list can be read."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector

from .const import CONF_TARGET, CONF_TV_URLS, DEFAULT_TARGET, DOMAIN

TV_APP_PORT = 8698


def _suggested_tv_urls(hass: HomeAssistant) -> str:
    """Every TV the Android TV integration knows — the radio app, if installed, answers there."""
    hosts = [e.data.get("host") for e in hass.config_entries.async_entries("androidtv") if e.data.get("host")]
    return ", ".join(f"http://{h}:{TV_APP_PORT}" for h in hosts)


def _suggested_target(hass: HomeAssistant) -> str:
    if hass.states.get(DEFAULT_TARGET):
        return DEFAULT_TARGET
    vlc = [s.entity_id for s in hass.states.async_all("media_player") if "vlc" in s.entity_id]
    return vlc[0] if vlc else ""


def _schema(target: str, tv_urls: str) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_TARGET, default=target): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="media_player")
            ),
            vol.Optional(CONF_TV_URLS, default=tv_urls): selector.TextSelector(),
        }
    )


class HomeRadioConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_TARGET])
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title="Home Radio", data=user_input)
        return self.async_show_form(
            step_id="user", data_schema=_schema(_suggested_target(self.hass), _suggested_tv_urls(self.hass)),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return HomeRadioOptionsFlow()


class HomeRadioOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="init",
            data_schema=_schema(current.get(CONF_TARGET, DEFAULT_TARGET), current.get(CONF_TV_URLS, "")),
        )
