package com.stathisktm.homeradio.remote

import android.content.ComponentName
import android.graphics.drawable.Icon
import android.service.quicksettings.Tile
import android.service.quicksettings.TileService
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import com.google.common.util.concurrent.MoreExecutors

/** A Quick Settings tile: tap = play/pause on Home Radio, shows what's on. Also wakes the playback
 *  service (by connecting to its session) so the full media controls appear right after. */
class RadioTileService : TileService() {

    override fun onStartListening() {
        super.onStartListening()
        HaHub.start(this)
        refresh()
    }

    override fun onClick() {
        super.onClick()
        HaHub.start(this)
        HaHub.togglePlay()
        val future = MediaController.Builder(this, SessionToken(this, ComponentName(this, PlaybackService::class.java))).buildAsync()
        future.addListener({ MediaController.releaseFuture(future) }, MoreExecutors.directExecutor())
        refresh(optimisticToggle = true)
    }

    private fun refresh(optimisticToggle: Boolean = false) {
        val tile = qsTile ?: return
        val s = HaHub.state.value
        var playing = s?.state == "playing"
        if (optimisticToggle) playing = !playing
        tile.state = if (playing) Tile.STATE_ACTIVE else Tile.STATE_INACTIVE
        tile.label = "Home Radio"
        if (android.os.Build.VERSION.SDK_INT >= 29) tile.subtitle = s?.str("media_title") ?: HaHub.status.value
        tile.icon = Icon.createWithResource(this, R.drawable.ic_radio)
        tile.updateTile()
    }
}
