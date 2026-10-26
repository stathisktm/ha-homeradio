package com.stathisktm.homeradio.remote

import android.accessibilityservice.AccessibilityService
import android.content.ComponentName
import android.content.Context
import android.media.AudioManager
import android.provider.Settings
import android.view.KeyEvent
import android.view.accessibility.AccessibilityEvent

/**
 * Makes the phone's own volume keys change Home Radio's volume — Android 12+ stopped routing them
 * to third-party remote media sessions, and an accessibility service's key filter is the one
 * remaining way to see them, including over the lock screen (screen on).
 *
 * Only takes the keys while Home Radio is actually playing and the phone isn't playing anything
 * itself or in a call; otherwise they pass through untouched to the phone's own volume.
 */
class VolumeKeyService : AccessibilityService() {

    private val audio by lazy { getSystemService(AUDIO_SERVICE) as AudioManager }

    override fun onServiceConnected() {
        super.onServiceConnected()
        HaHub.start(this)
    }

    override fun onKeyEvent(event: KeyEvent): Boolean {
        val step = when (event.keyCode) {
            KeyEvent.KEYCODE_VOLUME_UP -> +PlaybackService.VOLUME_STEP
            KeyEvent.KEYCODE_VOLUME_DOWN -> -PlaybackService.VOLUME_STEP
            else -> return false
        }
        if (!shouldTakeKeys()) return false
        // Consume both down and up so the phone's own volume panel never moves; act on each down
        // (holding the key repeats it).
        if (event.action == KeyEvent.ACTION_DOWN) VolumeFeedback.show(this, HaHub.stepVolume(step))
        return true
    }

    private fun shouldTakeKeys(): Boolean {
        if (!Prefs(this).volumeKeys || !HaHub.isPlaying) return false
        if (audio.isMusicActive) return false
        if (audio.mode == AudioManager.MODE_IN_CALL || audio.mode == AudioManager.MODE_IN_COMMUNICATION) return false
        return true
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) = Unit

    override fun onInterrupt() = Unit

    companion object {
        fun isEnabled(context: Context): Boolean {
            val me = ComponentName(context, VolumeKeyService::class.java).flattenToString()
            val enabled = Settings.Secure.getString(context.contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES) ?: return false
            return enabled.split(':').any { it.equals(me, ignoreCase = true) }
        }
    }
}
