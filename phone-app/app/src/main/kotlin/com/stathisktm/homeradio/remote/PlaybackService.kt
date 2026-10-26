package com.stathisktm.homeradio.remote

import android.app.PendingIntent
import android.content.Intent
import android.os.Bundle
import androidx.annotation.OptIn
import androidx.media3.common.util.UnstableApi
import androidx.media3.session.CommandButton
import androidx.media3.session.MediaSession
import androidx.media3.session.MediaSessionService
import androidx.media3.session.SessionCommand
import androidx.media3.session.SessionResult
import com.google.common.collect.ImmutableList
import com.google.common.util.concurrent.Futures
import com.google.common.util.concurrent.ListenableFuture
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

    // Volume buttons right in the media controls (shade and lock screen): since Android 12 the
    // hardware volume keys no longer reach a third-party remote session like this one.
    private val volDown = SessionCommand(CMD_VOLUME_DOWN, Bundle.EMPTY)
    private val volUp = SessionCommand(CMD_VOLUME_UP, Bundle.EMPTY)
    private val customLayout: ImmutableList<CommandButton> by lazy {
        ImmutableList.of(
            CommandButton.Builder().setDisplayName("Ένταση −").setIconResId(R.drawable.ic_volume_down)
                .setSessionCommand(volDown).build(),
            CommandButton.Builder().setDisplayName("Ένταση +").setIconResId(R.drawable.ic_volume_up)
                .setSessionCommand(volUp).build(),
        )
    }

    override fun onCreate() {
        super.onCreate()
        HaHub.start(this)
        player = RemotePlayer(mainLooper)
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        session = MediaSession.Builder(this, player)
            .setSessionActivity(open)
            .setCallback(Callback())
            .setCustomLayout(customLayout)
            .build()
        scope.launch {
            combine(HaHub.state, HaHub.status) { s, st -> s to st }.collect { (s, st) -> player.update(s, st) }
        }
    }

    private inner class Callback : MediaSession.Callback {
        override fun onConnect(session: MediaSession, controller: MediaSession.ControllerInfo): MediaSession.ConnectionResult {
            val commands = MediaSession.ConnectionResult.DEFAULT_SESSION_COMMANDS.buildUpon().add(volDown).add(volUp).build()
            return MediaSession.ConnectionResult.AcceptedResultBuilder(session)
                .setAvailableSessionCommands(commands)
                .setCustomLayout(customLayout)
                .build()
        }

        override fun onCustomCommand(
            session: MediaSession,
            controller: MediaSession.ControllerInfo,
            customCommand: SessionCommand,
            args: Bundle,
        ): ListenableFuture<SessionResult> {
            when (customCommand.customAction) {
                CMD_VOLUME_DOWN -> VolumeFeedback.show(this@PlaybackService, HaHub.stepVolume(-VOLUME_STEP))
                CMD_VOLUME_UP -> VolumeFeedback.show(this@PlaybackService, HaHub.stepVolume(+VOLUME_STEP))
            }
            return Futures.immediateFuture(SessionResult(SessionResult.RESULT_SUCCESS))
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

    companion object {
        const val CMD_VOLUME_DOWN = "home_radio.volume_down"
        const val CMD_VOLUME_UP = "home_radio.volume_up"
        const val VOLUME_STEP = 5
    }
}
