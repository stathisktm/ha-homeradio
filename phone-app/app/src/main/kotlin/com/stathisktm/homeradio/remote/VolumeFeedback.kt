package com.stathisktm.homeradio.remote

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.widget.Toast

/** A short "Home Radio — ένταση 45%" toast for volume changes made away from the app's own
 *  screen (media controls, hardware keys); one toast reused, so holding a key doesn't queue many. */
object VolumeFeedback {
    private var toast: Toast? = null
    private val main = Handler(Looper.getMainLooper())

    fun show(context: Context, percent: Int) {
        main.post {
            toast?.cancel()
            toast = Toast.makeText(context.applicationContext, "Home Radio — ένταση $percent%", Toast.LENGTH_SHORT).also { it.show() }
        }
    }
}
