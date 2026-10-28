package com.stathisktm.homeradio.remote

import android.net.Uri
import android.os.Looper
import androidx.annotation.OptIn
import androidx.media3.common.C
import androidx.media3.common.DeviceInfo
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.Player
import androidx.media3.common.SimpleBasePlayer
import androidx.media3.common.util.UnstableApi
import com.google.common.util.concurrent.Futures
import com.google.common.util.concurrent.ListenableFuture
import java.time.Instant
import kotlin.math.roundToInt

/**
 * A Player that plays nothing itself: its state is Home Assistant's media_player.home_radio and
 * every command goes back there as a service call. Wrapped in a MediaSession, it gives the phone
 * real media controls for the HA machine's speakers — the notification-shade/quick-settings media
 * panel, the lock screen, and the hardware volume keys (a REMOTE device, so they set HA's volume
 * instead of the phone's).
 *
 * The playlist is always [previous, current, next] with the current item in the middle: a single
 * item would make the system controls grey out Previous/Next, and selecting a neighbour is simply
 * translated into the HA previous/next track call.
 */
@OptIn(UnstableApi::class)
class RemotePlayer(looper: Looper) : SimpleBasePlayer(looper) {

    private var es: EntityState? = null
    private var status: String = ""

    fun update(state: EntityState?, connection: String) {
        es = state
        status = connection
        invalidateState()
    }

    private val deviceInfo = DeviceInfo.Builder(DeviceInfo.PLAYBACK_TYPE_REMOTE).setMinVolume(0).setMaxVolume(100).build()

    override fun getState(): State {
        val s = es
        val playing = s?.state == "playing"
        val duration = s?.num("media_duration")?.takeIf { it > 0 }
        val title = s?.str("media_title") ?: if (s == null) "Home Radio" else "Διάλεξε σταθμό"
        val artist = s?.str("media_artist") ?: if (s == null) status else s.str("source").orEmpty()
        val meta = MediaMetadata.Builder()
            .setTitle(title)
            .setArtist(artist)
            .setDisplayTitle(title)
            .setSubtitle(artist)
            .setStation(s?.str("source"))
            .setArtworkUri(HaHub.pictureUrl(s)?.let(Uri::parse))
            .setIsPlayable(true)
            .build()
        val current = MediaItemData.Builder("current")
            .setMediaItem(MediaItem.Builder().setMediaId("current").setMediaMetadata(meta).build())
            .setMediaMetadata(meta)
            .setDurationUs(duration?.let { (it * 1_000_000).toLong() } ?: C.TIME_UNSET)
            .setIsSeekable(duration != null)
            .setIsDynamic(duration == null)
            .build()
        val neighbour = { id: String ->
            MediaItemData.Builder(id).setMediaItem(MediaItem.Builder().setMediaId(id).build()).build()
        }

        val commands = Player.Commands.Builder().addAll(
            Player.COMMAND_PLAY_PAUSE,
            Player.COMMAND_STOP,
            Player.COMMAND_SEEK_TO_NEXT,
            Player.COMMAND_SEEK_TO_NEXT_MEDIA_ITEM,
            Player.COMMAND_SEEK_TO_PREVIOUS,
            Player.COMMAND_SEEK_TO_PREVIOUS_MEDIA_ITEM,
            Player.COMMAND_GET_CURRENT_MEDIA_ITEM,
            Player.COMMAND_GET_METADATA,
            Player.COMMAND_GET_TIMELINE,
            Player.COMMAND_GET_DEVICE_VOLUME,
            Player.COMMAND_SET_DEVICE_VOLUME_WITH_FLAGS,
            Player.COMMAND_ADJUST_DEVICE_VOLUME_WITH_FLAGS,
        ).apply { if (duration != null) add(Player.COMMAND_SEEK_IN_CURRENT_MEDIA_ITEM) }.build()

        return State.Builder()
            .setAvailableCommands(commands)
            // READY even when HA is idle/paused/unreachable, so the media notification — and with
            // it the foreground service holding the connection — never disappears.
            .setPlaybackState(if (s?.state == "buffering") Player.STATE_BUFFERING else Player.STATE_READY)
            .setPlayWhenReady(playing, Player.PLAY_WHEN_READY_CHANGE_REASON_REMOTE)
            .setPlaylist(listOf(neighbour("prev"), current, neighbour("next")))
            .setCurrentMediaItemIndex(1)
            .setContentPositionMs { positionMs(s, playing) }
            .setDeviceInfo(deviceInfo)
            .setDeviceVolume(((s?.num("volume_level") ?: 0.0) * 100).roundToInt())
            .setIsDeviceMuted(s?.bool("is_volume_muted") ?: false)
            .build()
    }

    /** HA reports position as of `media_position_updated_at`; extrapolate while playing. */
    private fun positionMs(s: EntityState?, playing: Boolean): Long {
        val pos = s?.num("media_position") ?: return 0
        var ms = (pos * 1000).toLong()
        if (playing) {
            val updated = s.str("media_position_updated_at")?.let { runCatching { Instant.parse(it).toEpochMilli() }.getOrNull() }
            ms += System.currentTimeMillis() - (updated ?: s.receivedAtMs)
        }
        // VLC reports -1 while it doesn't know the length yet (and for live streams).
        val dur = s.num("media_duration")?.takeIf { it > 0 }?.let { (it * 1000).toLong() }
        return if (dur != null) ms.coerceIn(0, dur) else ms.coerceAtLeast(0)
    }

    private fun done(): ListenableFuture<*> = Futures.immediateVoidFuture()

    override fun handleSetPlayWhenReady(playWhenReady: Boolean): ListenableFuture<*> {
        HaHub.mediaPlayer(if (playWhenReady) "media_play" else "media_pause")
        return done()
    }

    override fun handleStop(): ListenableFuture<*> {
        HaHub.mediaPlayer("media_stop")
        return done()
    }

    override fun handleSeek(mediaItemIndex: Int, positionMs: Long, seekCommand: Int): ListenableFuture<*> {
        when {
            mediaItemIndex == 2 || seekCommand == Player.COMMAND_SEEK_TO_NEXT || seekCommand == Player.COMMAND_SEEK_TO_NEXT_MEDIA_ITEM ->
                HaHub.mediaPlayer("media_next_track")
            mediaItemIndex == 0 || seekCommand == Player.COMMAND_SEEK_TO_PREVIOUS_MEDIA_ITEM ->
                HaHub.mediaPlayer("media_previous_track")
            else -> HaHub.mediaPlayer("media_seek", mapOf("seek_position" to positionMs.coerceAtLeast(0) / 1000.0))
        }
        return done()
    }

    override fun handleSetDeviceVolume(deviceVolume: Int, flags: Int): ListenableFuture<*> {
        HaHub.setVolume(deviceVolume)
        return done()
    }

    override fun handleIncreaseDeviceVolume(flags: Int): ListenableFuture<*> = stepVolume(+5)

    override fun handleDecreaseDeviceVolume(flags: Int): ListenableFuture<*> = stepVolume(-5)

    private fun stepVolume(delta: Int): ListenableFuture<*> {
        HaHub.stepVolume(delta)
        return done()
    }

    override fun handleSetDeviceMuted(muted: Boolean, flags: Int): ListenableFuture<*> {
        HaHub.mediaPlayer("volume_mute", mapOf("is_volume_muted" to muted))
        return done()
    }
}
