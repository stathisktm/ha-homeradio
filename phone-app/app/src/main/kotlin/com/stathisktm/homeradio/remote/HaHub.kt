package com.stathisktm.homeradio.remote

import android.content.Context
import android.util.Log
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener

/** One media_player entity as Home Assistant reports it. */
data class EntityState(val state: String, val attributes: JsonObject, val receivedAtMs: Long) {
    fun str(key: String): String? = (attributes[key] as? JsonPrimitive)?.contentOrNull
    fun num(key: String): Double? = (attributes[key] as? JsonPrimitive)?.doubleOrNull
    fun bool(key: String): Boolean? = (attributes[key] as? JsonPrimitive)?.booleanOrNull
    fun list(key: String): JsonArray? = attributes[key] as? JsonArray
}

/**
 * The single live connection to Home Assistant for the whole app (playback service, tile and
 * screen all share it): the WebSocket API, authenticated with a long-lived token, subscribed to
 * just the one entity via `subscribe_entities` — so every change (title, state, volume, ...)
 * arrives pushed, not polled. Reconnects on its own, forever, while started.
 */
object HaHub {
    private const val TAG = "HaHub"

    private val _state = MutableStateFlow<EntityState?>(null)
    val state: StateFlow<EntityState?> = _state

    private val _status = MutableStateFlow("Αποσύνδεση")
    val status: StateFlow<String> = _status

    private val json = Json { ignoreUnknownKeys = true }
    private val client = OkHttpClient.Builder().pingInterval(30, TimeUnit.SECONDS).build()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val nextId = AtomicInteger(1)

    private var socket: WebSocket? = null
    @Volatile private var connected = false
    // Calls made before the connection is up (a share that opened the app) go out once it is.
    private val waiting = java.util.concurrent.ConcurrentLinkedQueue<String>()
    private var loop: Job? = null
    private var prefs: Prefs? = null
    @Volatile private var generation = 0

    val baseUrl: String get() = prefs?.haUrl.orEmpty()

    fun start(context: Context) {
        if (prefs == null) prefs = Prefs(context)
        if (loop?.isActive == true) return
        loop = scope.launch { runForever() }
    }

    /** Settings changed: drop the current connection; the loop reconnects with the new ones. */
    fun restart(context: Context) {
        prefs = Prefs(context)
        generation++
        socket?.cancel()
        _state.value = null
        start(context)
    }

    private suspend fun runForever() {
        var backoffMs = 2_000L
        while (true) {
            val p = prefs ?: return
            if (!p.isConfigured) {
                _status.value = "Χρειάζονται ρυθμίσεις"
                delay(2_000)
                continue
            }
            val gen = generation
            val closed = kotlinx.coroutines.CompletableDeferred<Unit>()
            _status.value = "Σύνδεση…"
            val wsUrl = p.haUrl.replaceFirst(Regex("^http"), "ws") + "/api/websocket"
            socket = client.newWebSocket(Request.Builder().url(wsUrl).build(), Listener(p, gen, closed))
            closed.await()
            if (_status.value == "Συνδέθηκε") backoffMs = 2_000L
            delay(backoffMs)
            backoffMs = (backoffMs * 2).coerceAtMost(30_000L)
        }
    }

    private class Listener(
        private val prefs: Prefs,
        private val gen: Int,
        private val closed: kotlinx.coroutines.CompletableDeferred<Unit>,
    ) : WebSocketListener() {

        override fun onMessage(webSocket: WebSocket, text: String) {
            if (gen != generation) return
            val msg = runCatching { json.parseToJsonElement(text).jsonObject }.getOrNull() ?: return
            when ((msg["type"] as? JsonPrimitive)?.contentOrNull) {
                "auth_required" -> webSocket.send(buildJsonObject {
                    put("type", "auth")
                    put("access_token", prefs.token)
                }.toString())
                "auth_ok" -> {
                    _status.value = "Συνδέθηκε"
                    connected = true
                    while (true) webSocket.send(waiting.poll() ?: break)
                    webSocket.send(buildJsonObject {
                        put("id", nextId.getAndIncrement())
                        put("type", "subscribe_entities")
                        put("entity_ids", JsonArray(listOf(JsonPrimitive(prefs.entityId))))
                    }.toString())
                }
                "auth_invalid" -> {
                    _status.value = "Λάθος token"
                    webSocket.close(1000, null)
                }
                "event" -> (msg["event"] as? JsonObject)?.let { applyEntityEvent(it, prefs.entityId) }
                "result" -> if ((msg["success"] as? JsonPrimitive)?.booleanOrNull == false) {
                    Log.w(TAG, "HA error: $text")
                }
            }
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            Log.w(TAG, "connection failed", t)
            if (gen == generation) connected = false
            if (gen == generation) _status.value = "Χωρίς σύνδεση με το HA"
            closed.complete(Unit)
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            if (gen == generation) connected = false
            if (gen == generation && _status.value == "Συνδέθηκε") _status.value = "Αποσύνδεση"
            closed.complete(Unit)
        }
    }

    /** `subscribe_entities` compressed format: "a" = full states, "c" = diffs ("+" set / "-"
     *  removed attribute keys), "r" = removed entities. */
    private fun applyEntityEvent(event: JsonObject, entityId: String) {
        val now = System.currentTimeMillis()
        (event["a"] as? JsonObject)?.get(entityId)?.jsonObject?.let { full ->
            _state.value = EntityState(
                full["s"]?.jsonPrimitive?.contentOrNull ?: "unknown",
                full["a"] as? JsonObject ?: JsonObject(emptyMap()),
                now,
            )
        }
        (event["c"] as? JsonObject)?.get(entityId)?.jsonObject?.let { diff ->
            val current = _state.value ?: return@let
            val plus = diff["+"] as? JsonObject
            val minus = (diff["-"] as? JsonObject)?.get("a")?.jsonArray?.mapNotNull { it.jsonPrimitive.contentOrNull }.orEmpty()
            val attrs = current.attributes.toMutableMap<String, JsonElement>()
            minus.forEach { attrs.remove(it) }
            (plus?.get("a") as? JsonObject)?.forEach { (k, v) -> attrs[k] = v }
            _state.value = EntityState(
                plus?.get("s")?.jsonPrimitive?.contentOrNull ?: current.state,
                JsonObject(attrs),
                now,
            )
        }
        (event["r"] as? JsonArray)?.let { removed ->
            if (removed.any { it.jsonPrimitive.contentOrNull == entityId }) _state.value = null
        }
    }

    /** Fire-and-forget service call on the configured entity; its effect comes back as a state
     *  change through the subscription. */
    fun call(domain: String, service: String, data: Map<String, Any> = emptyMap()) {
        val entity = prefs?.entityId ?: return
        val payload = buildJsonObject {
            put("id", nextId.getAndIncrement())
            put("type", "call_service")
            put("domain", domain)
            put("service", service)
            put("target", buildJsonObject { put("entity_id", entity) })
            put("service_data", buildJsonObject {
                data.forEach { (k, v) ->
                    when (v) {
                        is Number -> put(k, v)
                        is Boolean -> put(k, v)
                        else -> put(k, v.toString())
                    }
                }
            })
        }
        if (socket?.send(payload.toString()) != true) Log.w(TAG, "not connected, dropped $domain.$service")
    }

    /** Like [call], but held until the connection is up if it isn't yet. */
    fun callSoon(domain: String, service: String, data: Map<String, Any> = emptyMap()) {
        if (connected) return call(domain, service, data)
        val entity = prefs?.entityId ?: return
        waiting.add(buildJsonObject {
            put("id", nextId.getAndIncrement())
            put("type", "call_service")
            put("domain", domain)
            put("service", service)
            put("target", buildJsonObject { put("entity_id", entity) })
            put("service_data", buildJsonObject { data.forEach { (k, v) -> put(k, v.toString()) } })
        }.toString())
    }

    fun mediaPlayer(service: String, data: Map<String, Any> = emptyMap()) = call("media_player", service, data)

    // Several quick volume presses arrive faster than HA reports back the new level, so each step
    // builds on the last level asked for (for a moment) rather than on the not-yet-updated state.
    private var pendingVolume: Int? = null
    private var pendingAtMs = 0L

    fun volumePercent(): Int {
        val reported = ((_state.value?.num("volume_level") ?: 0.5) * 100).toInt()
        return pendingVolume?.takeIf { System.currentTimeMillis() - pendingAtMs < 2_000 } ?: reported
    }

    /** Sets HA's volume to [percent] (0..100) and returns it. */
    fun setVolume(percent: Int): Int {
        val v = percent.coerceIn(0, 100)
        pendingVolume = v
        pendingAtMs = System.currentTimeMillis()
        mediaPlayer("volume_set", mapOf("volume_level" to v / 100.0))
        return v
    }

    fun stepVolume(delta: Int): Int = setVolume(volumePercent() + delta)

    val isPlaying: Boolean get() = _state.value?.state == "playing"

    fun togglePlay() {
        if (_state.value?.state == "playing") mediaPlayer("media_pause") else mediaPlayer("media_play")
    }

    /** entity_picture may be a full URL (station logos, YouTube thumbnails) or an HA-relative
     *  proxy path that already carries its own access token. */
    fun pictureUrl(es: EntityState?): String? {
        val pic = es?.str("entity_picture") ?: return null
        return if (pic.startsWith("http")) pic else baseUrl + pic
    }
}
