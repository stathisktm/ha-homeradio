package com.stathisktm.homeradio.remote

import android.content.Context

/** Where Home Assistant is, how to authenticate, and which player to control. */
class Prefs(context: Context) {
    private val prefs = context.applicationContext.getSharedPreferences("home_radio_remote", Context.MODE_PRIVATE)

    var haUrl: String
        get() = prefs.getString(KEY_URL, DEFAULT_URL) ?: DEFAULT_URL
        set(value) = prefs.edit().putString(KEY_URL, value.trim().trimEnd('/')).apply()

    var token: String
        get() = prefs.getString(KEY_TOKEN, "") ?: ""
        set(value) = prefs.edit().putString(KEY_TOKEN, value.trim()).apply()

    var entityId: String
        get() = prefs.getString(KEY_ENTITY, DEFAULT_ENTITY) ?: DEFAULT_ENTITY
        set(value) = prefs.edit().putString(KEY_ENTITY, value.trim()).apply()

    /** Whether the phone's volume keys drive Home Radio (through [VolumeKeyService]). */
    var volumeKeys: Boolean
        get() = prefs.getBoolean(KEY_VOLUME_KEYS, true)
        set(value) = prefs.edit().putBoolean(KEY_VOLUME_KEYS, value).apply()

    val isConfigured: Boolean get() = haUrl.isNotBlank() && token.isNotBlank() && entityId.isNotBlank()

    companion object {
        const val DEFAULT_URL = "http://homeassistant.local:8123"
        const val DEFAULT_ENTITY = "media_player.home_radio"
        private const val KEY_URL = "ha_url"
        private const val KEY_TOKEN = "token"
        private const val KEY_ENTITY = "entity_id"
        private const val KEY_VOLUME_KEYS = "volume_keys"
    }
}
