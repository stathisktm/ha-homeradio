package com.stathisktm.homeradio.remote

import android.app.PendingIntent
import android.content.Intent
import androidx.annotation.OptIn
import androidx.media3.common.util.UnstableApi
import androidx.media3.session.MediaSession
import androidx.media3.session.MediaSessionService
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.launch

/** Hosts the MediaSession that the system's media controls talk to, and keeps the HA connection
 *  alive behind it. Held in the foreground at all times (not only while playing), so pausing
 *  from the shade never lets Android kill the connection that Play needs a moment later. */
@OptIn(UnstableApi::class)
class PlaybackService : MediaSessionService() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private lateinit var player: RemotePlayer
    private lateinit var session: MediaSession

    override fun onCreate() {
        super.onCreate()
        HaHub.start(this)
        player = RemotePlayer(mainLooper)
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        session = MediaSession.Builder(this, player).setSessionActivity(open).build()
        scope.launch {
            combine(HaHub.state, HaHub.status) { s, st -> s to st }.collect { (s, st) -> player.update(s, st) }
        }
    }

    override fun onGetSession(controllerInfo: MediaSession.ControllerInfo): MediaSession = session

    override fun onUpdateNotification(session: MediaSession, startInForegroundRequired: Boolean) {
        super.onUpdateNotification(session, true)
    }

    // Swiping the app away from Recents must not end the controls.
    override fun onTaskRemoved(rootIntent: Intent?) = Unit

    override fun onDestroy() {
        scope.cancel()
        session.release()
        player.release()
        super.onDestroy()
    }
}
