package com.stathisktm.homeradio.remote

import android.Manifest
import android.content.ComponentName
import android.content.Intent
import android.provider.Settings as AndroidSettings
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.background
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items as rowItems
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.GridItemSpan
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.SkipNext
import androidx.compose.material.icons.filled.SkipPrevious
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material.icons.automirrored.filled.VolumeUp
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.FilledIconButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.IconButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Slider
import androidx.compose.material3.Switch
import androidx.compose.material3.TextButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.lifecycle.compose.LifecycleResumeEffect
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.blur
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import android.widget.Toast
import androidx.core.content.ContextCompat
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import coil.compose.AsyncImage
import com.google.common.util.concurrent.ListenableFuture
import java.time.Instant
import kotlinx.coroutines.delay
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull

private val Accent = Color(0xFF4FC3F7)
private val Colors = darkColorScheme(primary = Accent, background = Color(0xFF0E1116), surface = Color(0xFF171B22))

class MainActivity : ComponentActivity() {

    private var controller: ListenableFuture<MediaController>? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        }
        val prefs = Prefs(this)
        if (prefs.haUrl == Prefs.DEFAULT_URL && BuildConfig.DEFAULT_HA_URL.isNotBlank()) prefs.haUrl = BuildConfig.DEFAULT_HA_URL
        HaHub.start(this)
        if (savedInstanceState == null) handleShare(intent)
        setContent { MaterialTheme(colorScheme = Colors) { App(prefs) } }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleShare(intent)
    }

    /** Share -> Home Radio: a Spotify playlist/album/song or a YouTube link plays (and a Spotify
     *  list is kept among the Lists); anything else is searched on YouTube. */
    private fun handleShare(intent: Intent?) {
        if (intent?.action != Intent.ACTION_SEND) return
        val text = intent.getStringExtra(Intent.EXTRA_TEXT)?.trim().orEmpty()
        if (text.isEmpty()) return
        val link = Regex("""https?://\S+""").findAll(text).map { it.value }
            .firstOrNull { "spotify" in it || "youtu" in it }
        HaHub.callSoon("home_radio", "play_youtube", mapOf("query" to (link ?: text)))
        Toast.makeText(this, if (link != null && "spotify" in link) "Παίζει στο Home Radio · κρατιέται στις Λίστες"
            else "Παίζει στο Home Radio", Toast.LENGTH_SHORT).show()
    }

    // Connecting a controller is what brings PlaybackService (and so the shade controls) up.
    override fun onStart() {
        super.onStart()
        controller = MediaController.Builder(this, SessionToken(this, ComponentName(this, PlaybackService::class.java))).buildAsync()
    }

    override fun onStop() {
        controller?.let { MediaController.releaseFuture(it) }
        controller = null
        super.onStop()
    }

    @Composable
    private fun App(prefs: Prefs) {
        val s by HaHub.state.collectAsState()
        val status by HaHub.status.collectAsState()
        var showSettings by rememberSaveable { mutableStateOf(!prefs.isConfigured) }
        Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
            LazyVerticalGrid(
                columns = GridCells.Adaptive(96.dp),
                modifier = Modifier.safeDrawingPadding().padding(horizontal = 16.dp),
                horizontalArrangement = Arrangement.spacedBy(12.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                item(span = { GridItemSpan(maxLineSpan) }) {
                    Row(Modifier.fillMaxWidth().padding(top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                        Text("Home Radio", fontSize = 24.sp, fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
                        Text(status, color = if (status == "Συνδέθηκε") Accent else Color(0xFFFFB74D), fontSize = 13.sp)
                        IconButton(onClick = { showSettings = !showSettings }) { Icon(Icons.Default.Settings, "Ρυθμίσεις") }
                    }
                }
                if (showSettings) item(span = { GridItemSpan(maxLineSpan) }) { Settings(prefs) { showSettings = false } }
                item(span = { GridItemSpan(maxLineSpan) }) { NowPlaying(s) }
                val upNext = upNext(s)
                if (upNext.isNotEmpty()) item(span = { GridItemSpan(maxLineSpan) }) { UpNext(upNext) }
                item(span = { GridItemSpan(maxLineSpan) }) { YoutubeSearch() }
                s?.let { st ->
                    val lists = st.list("saved_lists").orEmpty().mapNotNull { it as? JsonObject }
                    item(span = { GridItemSpan(maxLineSpan) }) { SavedLists(lists, st.str("queue_list_id")) }
                }
                item(span = { GridItemSpan(maxLineSpan) }) { VolumeKeys(prefs) }
                val stations = s?.list("stations").orEmpty().mapNotNull { it as? JsonObject }
                if (stations.isNotEmpty()) {
                    item(span = { GridItemSpan(maxLineSpan) }) {
                        Text("Σταθμοί", fontWeight = FontWeight.Bold, color = Color.Gray, modifier = Modifier.padding(top = 8.dp))
                    }
                }
                items(stations) { st -> StationTile(st, active = s?.str("station_id") == st.text("id") && s?.str("kind") == "station") }
                item(span = { GridItemSpan(maxLineSpan) }) { Spacer(Modifier.height(24.dp)) }
            }
        }
    }

    @Composable
    private fun Settings(prefs: Prefs, onSaved: () -> Unit) {
        var url by rememberSaveable { mutableStateOf(prefs.haUrl) }
        var token by rememberSaveable { mutableStateOf(prefs.token) }
        var entity by rememberSaveable { mutableStateOf(prefs.entityId) }
        Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text("Σύνδεση με το Home Assistant", fontWeight = FontWeight.Bold)
                OutlinedTextField(url, { url = it }, label = { Text("Διεύθυνση HA") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(
                    token, { token = it }, label = { Text("Long-lived token") }, singleLine = true,
                    visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth(),
                )
                Text(
                    "HA → πάτα το όνομά σου κάτω αριστερά → Ασφάλεια → Long-lived access tokens → Δημιουργία → αντιγραφή εδώ.",
                    fontSize = 12.sp, color = Color.Gray,
                )
                OutlinedTextField(entity, { entity = it }, label = { Text("Player") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                Button(onClick = {
                    prefs.haUrl = url; prefs.token = token; prefs.entityId = entity
                    HaHub.restart(this@MainActivity)
                    onSaved()
                }, modifier = Modifier.fillMaxWidth()) { Text("Αποθήκευση") }
            }
        }
    }

    @Composable
    private fun NowPlaying(s: EntityState?) {
        val playing = s?.state == "playing"
        val loading = s?.state == "buffering"
        val pic = HaHub.pictureUrl(s)
        Box(Modifier.fillMaxWidth().clip(RoundedCornerShape(24.dp)).background(Color(0xFF1B2230))) {
            if (pic != null) {
                AsyncImage(pic, null, contentScale = ContentScale.Crop, modifier = Modifier.matchParentSize().blur(40.dp), alpha = 0.55f)
            }
            Column(Modifier.padding(20.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                Box(Modifier.size(180.dp).clip(RoundedCornerShape(20.dp)).background(Color.White), contentAlignment = Alignment.Center) {
                    if (pic != null) {
                        AsyncImage(pic, null, contentScale = if (s?.str("kind") == "youtube") ContentScale.Crop else ContentScale.Fit,
                            modifier = Modifier.fillMaxSize().padding(if (s?.str("kind") == "youtube") 0.dp else 14.dp))
                    } else {
                        Text("📻", fontSize = 64.sp)
                    }
                    if (loading) {
                        Box(Modifier.matchParentSize().background(Color.Black.copy(alpha = 0.45f)), contentAlignment = Alignment.Center) {
                            CircularProgressIndicator(color = Color.White, strokeWidth = 4.dp)
                        }
                    }
                }
                Spacer(Modifier.height(14.dp))
                sourceLabel(s)?.let { label ->
                    Text(label, fontSize = 12.sp, fontWeight = FontWeight.Medium, color = Color.White, maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.clip(RoundedCornerShape(99.dp)).background(Color.White.copy(alpha = 0.16f))
                            .padding(horizontal = 12.dp, vertical = 4.dp))
                    Spacer(Modifier.height(10.dp))
                }
                Text(s?.str("media_title") ?: "Διάλεξε σταθμό ή τραγούδι", fontSize = 20.sp, fontWeight = FontWeight.Bold,
                    textAlign = TextAlign.Center, maxLines = 2, overflow = TextOverflow.Ellipsis)
                Text(s?.str("media_artist") ?: s?.str("source").orEmpty(), color = Color(0xFFCFD8DC), maxLines = 1, overflow = TextOverflow.Ellipsis)
                Progress(s, playing)
                Spacer(Modifier.height(12.dp))
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                    RoundButton({ HaHub.mediaPlayer("media_previous_track") }) { Icon(Icons.Default.SkipPrevious, "Προηγούμενο") }
                    RoundButton({ HaHub.mediaPlayer("media_stop") }) { Icon(Icons.Default.Stop, "Στοπ") }
                    FilledIconButton(
                        onClick = { HaHub.togglePlay() }, modifier = Modifier.size(68.dp),
                        colors = IconButtonDefaults.filledIconButtonColors(containerColor = Color.White, contentColor = Color.Black),
                    ) { Icon(if (playing) Icons.Default.Pause else Icons.Default.PlayArrow, "Play", Modifier.size(36.dp)) }
                    RoundButton({ HaHub.mediaPlayer("media_next_track") }) { Icon(Icons.Default.SkipNext, "Επόμενο") }
                }
                val haVolume = (s?.num("volume_level") ?: 0.0).toFloat()
                var dragging by remember { mutableStateOf<Float?>(null) }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(Icons.AutoMirrored.Filled.VolumeUp, null)
                    Slider(
                        value = dragging ?: haVolume,
                        onValueChange = { dragging = it },
                        onValueChangeFinished = {
                            dragging?.let { HaHub.setVolume((it * 100).toInt()) }
                            dragging = null
                        },
                        modifier = Modifier.weight(1f).padding(start = 8.dp),
                    )
                }
            }
        }
    }

    /** Where the music comes from, shown above the title. */
    private fun sourceLabel(s: EntityState?): String? {
        if (s == null) return null
        return when {
            s.str("cast_sender") != null -> "📱 Cast από ${s.str("cast_sender")}"
            s.str("queue_name") != null -> "♫ ${s.str("queue_name")}"
            s.str("kind") == "station" -> "📻 Ραδιόφωνο"
            s.str("kind") == "youtube" -> "▶ YouTube"
            else -> null
        }
    }

    /** Position bar with times, for tracks with a known length; drag to seek. */
    @Composable
    private fun Progress(s: EntityState?, playing: Boolean) {
        val duration = s?.num("media_duration")?.takeIf { it > 0 } ?: return
        var now by remember { mutableLongStateOf(System.currentTimeMillis()) }
        LaunchedEffect(playing) {
            while (playing) {
                delay(1_000)
                now = System.currentTimeMillis()
            }
        }
        val reported = s.num("media_position") ?: 0.0
        val since = s.str("media_position_updated_at")?.let { runCatching { Instant.parse(it).toEpochMilli() }.getOrNull() } ?: s.receivedAtMs
        val position = (reported + if (playing) (now - since) / 1000.0 else 0.0).coerceIn(0.0, duration)
        var dragging by remember { mutableStateOf<Float?>(null) }
        Column(Modifier.fillMaxWidth().padding(top = 12.dp)) {
            Slider(
                value = dragging ?: (position / duration).toFloat(),
                onValueChange = { dragging = it },
                onValueChangeFinished = {
                    dragging?.let { HaHub.mediaPlayer("media_seek", mapOf("seek_position" to it * duration)) }
                    dragging = null
                },
                colors = SliderDefaults.colors(thumbColor = Color.White, activeTrackColor = Color.White,
                    inactiveTrackColor = Color.White.copy(alpha = 0.25f)),
            )
            Row(Modifier.fillMaxWidth()) {
                Text(clock(dragging?.let { it * duration } ?: position), fontSize = 12.sp, color = Color(0xFFCFD8DC), modifier = Modifier.weight(1f))
                Text(clock(duration), fontSize = 12.sp, color = Color(0xFFCFD8DC))
            }
        }
    }

    private fun clock(seconds: Double): String {
        val t = seconds.toInt().coerceAtLeast(0)
        return if (t >= 3600) "%d:%02d:%02d".format(t / 3600, t / 60 % 60, t % 60) else "%d:%02d".format(t / 60, t % 60)
    }

    /** The next few songs of the queue, with their place in it. */
    private fun upNext(s: EntityState?): List<Pair<Int, JsonObject>> {
        if (s == null || s.str("kind") != "youtube" || s.str("cast_sender") != null) return emptyList()
        val index = s.num("queue_index")?.toInt() ?: return emptyList()
        return s.list("queue").orEmpty().mapIndexedNotNull { i, e -> (e as? JsonObject)?.let { i to it } }
            .filter { it.first > index }.take(5)
    }

    @Composable
    private fun UpNext(items: List<Pair<Int, JsonObject>>) {
        Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
            Column(Modifier.padding(vertical = 10.dp)) {
                Text("Επόμενα", fontWeight = FontWeight.Bold, color = Color.Gray, modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp))
                items.forEach { (i, t) ->
                    Row(
                        Modifier.fillMaxWidth().clickable {
                            HaHub.mediaPlayer("play_media", mapOf("media_content_id" to "queue:$i", "media_content_type" to "music"))
                        }.padding(horizontal = 16.dp, vertical = 6.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Box(Modifier.size(width = 64.dp, height = 40.dp).clip(RoundedCornerShape(8.dp)).background(Color(0xFF2A3140))) {
                            val thumb = t.text("thumbnail")
                            if (!thumb.isNullOrBlank()) AsyncImage(thumb, null, contentScale = ContentScale.Crop, modifier = Modifier.fillMaxSize())
                        }
                        Column(Modifier.weight(1f).padding(start = 12.dp)) {
                            Text(t.text("title").orEmpty(), fontSize = 14.sp, fontWeight = FontWeight.Medium, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            Text(t.text("channel").orEmpty(), fontSize = 12.sp, color = Color.Gray, maxLines = 1, overflow = TextOverflow.Ellipsis)
                        }
                    }
                }
            }
        }
    }

    @Composable
    private fun RoundButton(onClick: () -> Unit, content: @Composable () -> Unit) {
        FilledIconButton(onClick = onClick, modifier = Modifier.size(48.dp),
            colors = IconButtonDefaults.filledIconButtonColors(containerColor = Color.White.copy(alpha = 0.16f), contentColor = Color.White),
        ) { content() }
    }

    /** Hardware volume keys -> Home Radio needs the accessibility service switched on once. */
    @Composable
    private fun VolumeKeys(prefs: Prefs) {
        var enabled by remember { mutableStateOf(VolumeKeyService.isEnabled(this)) }
        var wanted by remember { mutableStateOf(prefs.volumeKeys) }
        var hidden by remember { mutableStateOf(prefs.volumeKeysHintHidden) }
        // Re-check when coming back from the system's accessibility settings.
        LifecycleResumeEffect(Unit) {
            enabled = VolumeKeyService.isEnabled(this@MainActivity)
            onPauseOrDispose { }
        }
        if (!enabled && hidden) return
        Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
            Column(Modifier.padding(16.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text("Πλήκτρα έντασης κινητού", fontWeight = FontWeight.Bold)
                        Text(
                            if (enabled) "Όσο παίζει το Home Radio, αλλάζουν την ένταση των ηχείων του HA — και στην οθόνη κλειδώματος."
                            else "Χρειάζεται να ενεργοποιήσεις μία φορά την υπηρεσία «Home Radio: πλήκτρα έντασης» στην Προσβασιμότητα.",
                            fontSize = 12.sp, color = Color.Gray,
                        )
                    }
                    if (enabled) {
                        Switch(checked = wanted, onCheckedChange = { wanted = it; prefs.volumeKeys = it })
                    } else {
                        IconButton(onClick = { hidden = true; prefs.volumeKeysHintHidden = true }) { Icon(Icons.Default.Close, "Κλείσιμο") }
                    }
                }
                if (!enabled) {
                    TextButton(onClick = {
                        startActivity(Intent(AndroidSettings.ACTION_ACCESSIBILITY_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                    }) { Text("Άνοιγμα Προσβασιμότητας") }
                }
            }
        }
    }

    @Composable
    private fun YoutubeSearch() {
        var query by rememberSaveable { mutableStateOf("") }
        val search = { if (query.isNotBlank()) HaHub.call("home_radio", "play_youtube", mapOf("query" to query.trim())) }
        OutlinedTextField(
            query, { query = it }, singleLine = true, modifier = Modifier.fillMaxWidth(),
            placeholder = { Text("Αναζήτηση στο YouTube ή link Spotify…") },
            trailingIcon = { IconButton(onClick = search) { Icon(Icons.Default.Search, "Αναζήτηση") } },
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
            keyboardActions = KeyboardActions(onSearch = { search() }),
        )
    }

    /** Spotify playlists/albums played before, kept by Home Radio: tap plays, long press removes. */
    @OptIn(ExperimentalFoundationApi::class)
    @Composable
    private fun SavedLists(lists: List<JsonObject>, playingId: String?) {
        var removing by remember { mutableStateOf<JsonObject?>(null) }
        Column {
            Text("Λίστες", fontWeight = FontWeight.Bold, color = Color.Gray, modifier = Modifier.padding(top = 8.dp, bottom = 8.dp))
            if (lists.isEmpty()) {
                Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
                    Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
                        Text("🎶", fontSize = 32.sp)
                        Text("Στο Spotify άνοιξε μια λίστα ή άλμπουμ → Κοινοποίηση → «Παίξε στο Home Radio». Παίζει εδώ και κρατιέται για την επόμενη φορά.",
                            fontSize = 13.sp, color = Color(0xFFCFD8DC), modifier = Modifier.padding(start = 12.dp))
                    }
                }
                return@Column
            }
            LazyRow(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                rowItems(lists, key = { it.text("id").orEmpty() }) { l ->
                    val id = l.text("id") ?: return@rowItems
                    val active = id == playingId
                    Column(Modifier.width(112.dp).combinedClickable(
                        onClick = {
                            HaHub.mediaPlayer("play_media", mapOf("media_content_id" to "list:$id", "media_content_type" to "playlist"))
                        },
                        onLongClick = { removing = l },
                    )) {
                        Box(
                            Modifier.size(112.dp).clip(RoundedCornerShape(18.dp))
                                .background(if (active) Accent else Color(0xFF2A3140)).padding(if (active) 3.dp else 0.dp)
                                .clip(RoundedCornerShape(16.dp)).background(Color(0xFF2A3140)),
                            contentAlignment = Alignment.Center,
                        ) {
                            val img = l.text("image")
                            if (!img.isNullOrBlank()) {
                                AsyncImage(img, l.text("name"), contentScale = ContentScale.Crop, modifier = Modifier.fillMaxSize())
                            } else {
                                Text("♫", fontSize = 40.sp, color = Color.White)
                            }
                            Text(l.text("count").orEmpty(), fontSize = 11.sp, fontWeight = FontWeight.Bold, color = Color.White,
                                modifier = Modifier.align(Alignment.BottomStart).padding(6.dp)
                                    .clip(RoundedCornerShape(99.dp)).background(Color.Black.copy(alpha = 0.55f))
                                    .padding(horizontal = 7.dp, vertical = 2.dp))
                        }
                        Text(l.text("name").orEmpty(), fontSize = 12.sp, maxLines = 2, overflow = TextOverflow.Ellipsis,
                            modifier = Modifier.padding(top = 4.dp))
                    }
                }
            }
            Text("Πρόσθεσε λίστα: στο Spotify → Κοινοποίηση → Home Radio. Κράτα πατημένη μια λίστα για αφαίρεση.",
                fontSize = 11.sp, color = Color.Gray, modifier = Modifier.padding(top = 6.dp))
        }
        removing?.let { l ->
            AlertDialog(
                onDismissRequest = { removing = null },
                title = { Text("Αφαίρεση λίστας") },
                text = { Text("Να αφαιρεθεί η «${l.text("name").orEmpty()}» από τις Λίστες;") },
                confirmButton = {
                    TextButton(onClick = {
                        HaHub.call("home_radio", "remove_list", mapOf("list" to l.text("id").orEmpty()))
                        removing = null
                    }) { Text("Αφαίρεση") }
                },
                dismissButton = { TextButton(onClick = { removing = null }) { Text("Άκυρο") } },
            )
        }
    }

    @Composable
    private fun StationTile(st: JsonObject, active: Boolean) {
        val id = st.text("id") ?: return
        Column(horizontalAlignment = Alignment.CenterHorizontally,
            modifier = Modifier.clickable { HaHub.call("home_radio", "play_station", mapOf("station" to id)) }) {
            Box(
                Modifier.fillMaxWidth().aspectRatio(1f).clip(RoundedCornerShape(18.dp))
                    .background(if (active) Accent else Color.White).padding(if (active) 3.dp else 0.dp)
                    .clip(RoundedCornerShape(16.dp)).background(Color.White),
                contentAlignment = Alignment.Center,
            ) {
                val logo = st.text("logo")
                if (!logo.isNullOrBlank()) {
                    AsyncImage(logo, st.text("name"), contentScale = ContentScale.Fit, modifier = Modifier.fillMaxSize().padding(12.dp))
                } else {
                    Box(Modifier.fillMaxSize().background(Accent), contentAlignment = Alignment.Center) {
                        Text(st.text("name").orEmpty().take(2), fontWeight = FontWeight.Bold, fontSize = 22.sp, color = Color.White)
                    }
                }
            }
            Text(st.text("name").orEmpty(), fontSize = 12.sp, maxLines = 2, textAlign = TextAlign.Center,
                overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(top = 4.dp))
        }
    }
}

private fun JsonObject.text(key: String): String? = (this[key] as? JsonPrimitive)?.contentOrNull


