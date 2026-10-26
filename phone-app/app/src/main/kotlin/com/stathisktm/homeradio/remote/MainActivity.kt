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
import androidx.compose.foundation.clickable
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
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.SkipNext
import androidx.compose.material.icons.filled.SkipPrevious
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material.icons.automirrored.filled.VolumeUp
import androidx.compose.material3.Button
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
import androidx.compose.runtime.collectAsState
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
import androidx.core.content.ContextCompat
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import coil.compose.AsyncImage
import com.google.common.util.concurrent.ListenableFuture
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
        setContent { MaterialTheme(colorScheme = Colors) { App(prefs) } }
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
                item(span = { GridItemSpan(maxLineSpan) }) { VolumeKeys(prefs) }
                item(span = { GridItemSpan(maxLineSpan) }) { YoutubeSearch() }
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
                }
                Spacer(Modifier.height(16.dp))
                Text(s?.str("media_title") ?: "Διάλεξε σταθμό ή τραγούδι", fontSize = 20.sp, fontWeight = FontWeight.Bold,
                    textAlign = TextAlign.Center, maxLines = 2, overflow = TextOverflow.Ellipsis)
                Text(s?.str("media_artist") ?: s?.str("source").orEmpty(), color = Color(0xFFCFD8DC), maxLines = 1, overflow = TextOverflow.Ellipsis)
                Spacer(Modifier.height(16.dp))
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
        // Re-check when coming back from the system's accessibility settings.
        LifecycleResumeEffect(Unit) {
            enabled = VolumeKeyService.isEnabled(this@MainActivity)
            onPauseOrDispose { }
        }
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
                    if (enabled) Switch(checked = wanted, onCheckedChange = { wanted = it; prefs.volumeKeys = it })
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
            placeholder = { Text("Αναζήτηση στο YouTube…") },
            trailingIcon = { IconButton(onClick = search) { Icon(Icons.Default.Search, "Αναζήτηση") } },
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
            keyboardActions = KeyboardActions(onSearch = { search() }),
        )
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


