"""A small rolling diagnostics file (config/home_radio_debug.log): what was handed to the player,
whether the player came to fetch it, what YouTube answered, how the player's state moved. Plain
text so it can be read straight off the config share when something doesn't play."""

from __future__ import annotations

from datetime import datetime
import os

from homeassistant.core import HomeAssistant

FILENAME = "home_radio_debug.log"
MAX_BYTES = 256 * 1024


def _append(path: str, line: str) -> None:
    try:
        if os.path.exists(path) and os.path.getsize(path) > MAX_BYTES:
            with open(path, encoding="utf-8", errors="replace") as f:
                tail = f.read()[-MAX_BYTES // 2 :]
            with open(path, "w", encoding="utf-8") as f:
                f.write(tail)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


def dlog(hass: HomeAssistant, message: str) -> None:
    line = f"{datetime.now().isoformat(timespec='milliseconds')} {message}\n"
    hass.async_add_executor_job(_append, hass.config.path(FILENAME), line)
