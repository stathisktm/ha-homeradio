/*! Home Radio card — MIT License */
// type: custom:home-radio-card
// entity: media_player.home_radio     # optional, found automatically
// tabs: [stations, lists, youtube, queue]    # optional

const ICONS = {
  play: "M8 5.14v13.72L19 12z",
  pause: "M6 5h4v14H6zm8 0h4v14h-4z",
  next: "M16 6h2v12h-2zM6 18l8.5-6L6 6z",
  prev: "M6 6h2v12H6zm3.5 6L18 18V6z",
  stop: "M6 6h12v12H6z",
  vol: "M3 9v6h4l5 5V4L7 9zm13.5 3A4.5 4.5 0 0 0 14 8v8a4.47 4.47 0 0 0 2.5-4M14 3.23v2.06a7 7 0 0 1 0 13.42v2.06A9 9 0 0 0 14 3.23",
  mute: "M16.5 12A4.5 4.5 0 0 0 14 8v2.18l2.45 2.45q.05-.3.05-.63M19 12a7 7 0 0 1-.57 2.8l1.51 1.51A8.8 8.8 0 0 0 21 12a9 9 0 0 0-7-8.77v2.06A7 7 0 0 1 19 12M4.27 3 3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25A7 7 0 0 1 14 18.7v2.06a9 9 0 0 0 3.69-1.81L19.73 21 21 19.73l-9-9zM12 4 9.91 6.09 12 8.18z",
  search: "M9.5 3A6.5 6.5 0 0 1 16 9.5c0 1.61-.59 3.09-1.56 4.23l.27.27h.79l5 5-1.5 1.5-5-5v-.79l-.27-.27A6.52 6.52 0 0 1 9.5 16 6.5 6.5 0 0 1 3 9.5 6.5 6.5 0 0 1 9.5 3m0 2C7 5 5 7 5 9.5S7 14 9.5 14 14 12 14 9.5 12 5 9.5 5",
  radio: "M20 6H8.3l8.26-3.34L15.88 1 3.24 6.15C2.51 6.43 2 7.17 2 8v12a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2m0 2v3h-2V9h-2v2H4V8zM7 20a3 3 0 1 1 0-6 3 3 0 0 1 0 6",
};
const svg = (d, size = 24) => `<svg viewBox="0 0 24 24" width="${size}" height="${size}"><path fill="currentColor" d="${d}"/></svg>`;
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const fmt = (s) => {
  if (!s || s < 0 || !isFinite(s)) return "0:00";
  s = Math.floor(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${x}` : `${m}:${x}`;
};

const STYLE = `
:host { --hr-accent: var(--primary-color, #03a9f4); display:block; }
ha-card { display:block; position:relative; overflow:hidden; padding:0; }
.hero { position:relative; padding:20px; color:#fff; min-height:170px; isolation:isolate; overflow:hidden; }
[hidden] { display:none !important; }
.hero .bg { position:absolute; inset:-40px; background-size:cover; background-position:center;
  filter:blur(38px) saturate(1.5) brightness(.55); transform:scale(1.15); z-index:-2; transition:background-image .6s; }
.hero::after { content:""; position:absolute; inset:0; z-index:-1;
  background:linear-gradient(180deg, rgba(0,0,0,.05), rgba(0,0,0,.45)); }
.hero.empty .bg { background:linear-gradient(135deg, var(--hr-accent), #6a1b9a); filter:none; }
.top { display:flex; gap:18px; align-items:center; }
.art { flex:none; width:118px; height:118px; border-radius:18px; background:#fff center/contain no-repeat;
  box-shadow:0 12px 30px rgba(0,0,0,.45); position:relative; overflow:hidden; }
.art.yt { background-size:cover; }
.art.none { background:rgba(255,255,255,.14); display:grid; place-items:center; color:rgba(255,255,255,.8); }
.meta { min-width:0; flex:1; }
.chip { display:inline-flex; align-items:center; gap:6px; font-size:.72rem; letter-spacing:.06em; text-transform:uppercase;
  background:rgba(255,255,255,.18); padding:4px 10px; border-radius:99px; backdrop-filter:blur(6px); }
.title { font-size:1.35rem; font-weight:700; line-height:1.2; margin:10px 0 4px; display:-webkit-box;
  -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
.artist { opacity:.82; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.eq { display:inline-flex; gap:2px; align-items:flex-end; height:12px; }
.eq i { width:3px; background:currentColor; border-radius:2px; height:30%; }
.playing .eq i { animation:eq 1s ease-in-out infinite; }
.eq i:nth-child(2) { animation-delay:-.3s !important; } .eq i:nth-child(3) { animation-delay:-.6s !important; } .eq i:nth-child(4) { animation-delay:-.15s !important; }
@keyframes eq { 0%,100% { height:25%; } 50% { height:100%; } }
.progress { margin-top:16px; display:flex; align-items:center; gap:10px; font-size:.75rem; opacity:.9; font-variant-numeric:tabular-nums; }
.bar { flex:1; height:5px; border-radius:5px; background:rgba(255,255,255,.25); cursor:pointer; position:relative; }
.bar b { position:absolute; inset:0 auto 0 0; border-radius:5px; background:#fff; }
.controls { display:flex; align-items:center; justify-content:center; gap:14px; margin-top:16px; }
.controls button { border:0; background:rgba(255,255,255,.14); color:#fff; width:44px; height:44px; border-radius:50%;
  display:grid; place-items:center; cursor:pointer; transition:transform .15s, background .2s; backdrop-filter:blur(6px); }
.controls button:hover { background:rgba(255,255,255,.26); }
.controls button:active { transform:scale(.92); }
.controls .main { width:62px; height:62px; background:#fff; color:#111; box-shadow:0 8px 24px rgba(0,0,0,.35); }
.controls .main:hover { background:#fff; transform:scale(1.05); }
.vol { display:flex; align-items:center; gap:10px; margin-top:14px; }
.vol button { border:0; background:none; color:#fff; cursor:pointer; padding:0; display:grid; }
.vol input { flex:1; accent-color:#fff; }
.tabs { display:flex; border-bottom:1px solid var(--divider-color, rgba(127,127,127,.2)); }
.tabs button { flex:1; border:0; background:none; padding:12px 4px; font:inherit; font-weight:600; cursor:pointer;
  color:var(--secondary-text-color); border-bottom:3px solid transparent; }
.tabs button.on { color:var(--hr-accent); border-bottom-color:var(--hr-accent); }
.pane { padding:14px; max-height:420px; overflow:auto; }
.pane[hidden] { display:none; }
.cat { font-size:.75rem; font-weight:700; text-transform:uppercase; letter-spacing:.08em; color:var(--secondary-text-color); margin:4px 2px 10px; }
.grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(96px, 1fr)); gap:12px; margin-bottom:14px; }
.st { border:0; background:none; padding:0; cursor:pointer; text-align:center; color:var(--primary-text-color); font:inherit; }
.st .logo { aspect-ratio:1; border-radius:16px; background:#fff center/72% no-repeat; position:relative;
  box-shadow:0 2px 8px rgba(0,0,0,.12); transition:transform .15s, box-shadow .2s; }
.st .logo.text { display:grid; place-items:center; font-weight:800; font-size:1.4rem; color:#fff;
  background:linear-gradient(135deg, var(--hr-accent), #7b1fa2); }
.st:hover .logo { transform:translateY(-2px); box-shadow:0 8px 18px rgba(0,0,0,.18); }
.st.on .logo { box-shadow:0 0 0 3px var(--hr-accent), 0 8px 18px rgba(0,0,0,.2); }
.st .logo .eq { position:absolute; right:8px; bottom:8px; color:var(--hr-accent); }
.st .name { font-size:.8rem; margin-top:6px; line-height:1.2; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
.searchbox { display:flex; gap:8px; }
.searchbox input { flex:1; min-width:0; padding:11px 14px; border-radius:12px; font:inherit;
  border:1px solid var(--divider-color, rgba(127,127,127,.3)); background:var(--secondary-background-color, transparent); color:var(--primary-text-color); }
.searchbox button { border:0; border-radius:12px; padding:0 16px; background:var(--hr-accent); color:#fff; cursor:pointer; display:grid; place-items:center; }
.recent { display:flex; flex-wrap:wrap; gap:6px; margin:10px 0 4px; }
.recent button { border:1px solid var(--divider-color, rgba(127,127,127,.3)); background:none; color:var(--primary-text-color);
  border-radius:99px; padding:5px 12px; font:inherit; font-size:.8rem; cursor:pointer; }
.list { margin-top:10px; display:flex; flex-direction:column; gap:4px; }
.row { display:flex; gap:12px; align-items:center; border:0; background:none; color:var(--primary-text-color); text-align:left;
  padding:6px; border-radius:12px; cursor:pointer; font:inherit; }
.row:hover { background:var(--secondary-background-color, rgba(127,127,127,.1)); }
.row.on { background:color-mix(in srgb, var(--hr-accent) 14%, transparent); }
.row img { width:84px; height:48px; object-fit:cover; border-radius:8px; flex:none; background:#000; }
.row .t { min-width:0; flex:1; }
.row .t div:first-child { font-weight:600; font-size:.9rem; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
.row .t div:last-child { font-size:.78rem; color:var(--secondary-text-color); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.row .d { font-size:.78rem; color:var(--secondary-text-color); font-variant-numeric:tabular-nums; }
.muted { color:var(--secondary-text-color); text-align:center; padding:24px 8px; }
.spin { width:22px; height:22px; border:3px solid rgba(127,127,127,.25); border-top-color:var(--hr-accent); border-radius:50%;
  animation:spin .8s linear infinite; margin:24px auto; }
@keyframes spin { to { transform:rotate(360deg); } }
.pl { position:relative; }
.pl .logo { background-size:cover; background-color:#222; }
.pl .count { position:absolute; left:8px; bottom:8px; font-size:.68rem; font-weight:700; color:#fff;
  background:rgba(0,0,0,.55); padding:2px 7px; border-radius:99px; }
.pl .del { position:absolute; top:4px; right:4px; width:24px; height:24px; border:0; border-radius:50%; cursor:pointer;
  background:rgba(0,0,0,.55); color:#fff; font-size:15px; line-height:24px; padding:0; opacity:0; transition:opacity .2s; }
.pl:hover .del, .pl:focus-within .del { opacity:1; }
@media (hover: none) { .pl .del { opacity:.8; } }
@media (max-width: 420px) { .art { width:92px; height:92px; } .title { font-size:1.15rem; } }
`;

class HomeRadioCard extends HTMLElement {
  static getStubConfig(hass) {
    const e = Object.values(hass?.states || {}).find((s) => s.entity_id.startsWith("media_player.") && Array.isArray(s.attributes.stations));
    return { entity: e?.entity_id || "media_player.home_radio" };
  }

  setConfig(config) {
    this._config = { tabs: ["stations", "lists", "youtube", "queue"], ...config };
    this._tab = this._config.tabs[0];
    this._built = false;
  }

  getCardSize() { return 9; }

  set hass(hass) {
    this._hass = hass;
    if (!this._config.entity) this._config.entity = HomeRadioCard.getStubConfig(hass).entity;
    if (!this._built) this._build();
    this._update();
  }

  get _st() { return this._hass?.states[this._config.entity]; }

  _call(service, data = {}, response = false) {
    return this._hass.callService(service.includes(".") ? service.split(".")[0] : "media_player",
      service.includes(".") ? service.split(".")[1] : service, data, { entity_id: this._config.entity }, true, response);
  }

  _build() {
    this._built = true;
    // A rebuilt DOM (card re-attached) starts empty, so every cached render key must go too.
    this._img = this._stKey = this._recentKey = this._queueKey = this._listsKey = undefined;
    const root = this.shadowRoot || this.attachShadow({ mode: "open" });
    const tabNames = { stations: "Σταθμοί", lists: "Λίστες", youtube: "YouTube", queue: "Ουρά" };
    root.innerHTML = `<style>${STYLE}</style>
      <ha-card>
        <div class="hero empty">
          <div class="bg"></div>
          <div class="top">
            <div class="art none">${svg(ICONS.radio, 44)}</div>
            <div class="meta">
              <span class="chip"><span class="eq"><i></i><i></i><i></i><i></i></span><span class="kind">Home Radio</span></span>
              <div class="title">Διάλεξε σταθμό ή τραγούδι</div>
              <div class="artist"></div>
            </div>
          </div>
          <div class="progress" hidden><span class="pos">0:00</span><div class="bar"><b></b></div><span class="dur">0:00</span></div>
          <div class="controls">
            <button data-a="prev" title="Προηγούμενο">${svg(ICONS.prev)}</button>
            <button data-a="stop" title="Στοπ">${svg(ICONS.stop, 20)}</button>
            <button class="main" data-a="toggle" title="Play/Pause">${svg(ICONS.play, 32)}</button>
            <button data-a="next" title="Επόμενο">${svg(ICONS.next)}</button>
          </div>
          <div class="vol"><button data-a="mute">${svg(ICONS.vol, 22)}</button><input type="range" min="0" max="100" step="1"></div>
        </div>
        <div class="tabs">${this._config.tabs.map((t) => `<button data-tab="${t}">${tabNames[t] || t}</button>`).join("")}</div>
        <div class="pane" data-pane="stations"></div>
        <div class="pane" data-pane="youtube">
          <form class="searchbox"><input placeholder="Αναζήτηση στο YouTube ή link Spotify…" enterkeyhint="search"><button>${svg(ICONS.search, 22)}</button></form>
          <div class="recent"></div><div class="list results"><div class="muted">Γράψε καλλιτέχνη, τραγούδι ή είδος.</div></div>
        </div>
        <div class="pane" data-pane="lists"></div>
        <div class="pane" data-pane="queue"><div class="list queue"></div></div>
      </ha-card>`;
    const $ = (s) => root.querySelector(s);
    this.$ = $;

    root.querySelector(".controls").addEventListener("click", (ev) => {
      const a = ev.target.closest("button")?.dataset.a;
      if (!a) return;
      if (a === "toggle") this._call(this._st?.state === "playing" ? "media_pause" : "media_play");
      if (a === "next") this._call("media_next_track");
      if (a === "prev") this._call("media_previous_track");
      if (a === "stop") this._call("media_stop");
    });
    $(".vol button").addEventListener("click", () => this._call("volume_mute", { is_volume_muted: !this._st?.attributes.is_volume_muted }));
    $(".vol input").addEventListener("change", (ev) => this._call("volume_set", { volume_level: ev.target.value / 100 }));
    $(".vol input").addEventListener("input", () => { this._dragVol = Date.now(); });
    $(".bar").addEventListener("click", (ev) => {
      const a = this._st?.attributes, r = ev.currentTarget.getBoundingClientRect();
      if (a?.media_duration) this._call("media_seek", { seek_position: ((ev.clientX - r.left) / r.width) * a.media_duration });
    });
    $(".tabs").addEventListener("click", (ev) => {
      const t = ev.target.closest("button")?.dataset.tab;
      if (t) { this._tab = t; this._update(); }
    });
    $('[data-pane="stations"]').addEventListener("click", (ev) => {
      const id = ev.target.closest(".st")?.dataset.id;
      if (id) this._call("home_radio.play_station", { station: id });
    });
    $('[data-pane="lists"]').addEventListener("click", (ev) => {
      const tile = ev.target.closest(".pl");
      if (!tile) return;
      if (ev.target.closest(".del")) {
        if (confirm(`Αφαίρεση της λίστας «${tile.dataset.name}»;`)) this._call("home_radio.remove_list", { list: tile.dataset.id });
        return;
      }
      this._call("play_media", { media_content_id: `list:${tile.dataset.id}`, media_content_type: "playlist" });
    });
    $(".searchbox").addEventListener("submit", (ev) => { ev.preventDefault(); this._search($(".searchbox input").value); });
    $(".recent").addEventListener("click", (ev) => {
      const q = ev.target.closest("button")?.dataset.q;
      if (q) { $(".searchbox input").value = q; this._search(q); }
    });
    const playRow = (ev) => {
      const row = ev.target.closest(".row");
      if (row) this._call("home_radio.play_youtube", { url: row.dataset.url, ...(row.dataset.q ? { query: row.dataset.q } : {}) });
    };
    $(".results").addEventListener("click", playRow);
    // A queue row jumps to that place in the queue (Spotify songs have no YouTube link until then).
    $(".queue").addEventListener("click", (ev) => {
      const row = ev.target.closest(".row");
      if (row) this._call("play_media", { media_content_id: `queue:${row.dataset.i}`, media_content_type: "music" });
    });
    this._tick = setInterval(() => this._updateProgress(), 1000);
  }

  disconnectedCallback() { clearInterval(this._tick); this._built = false; }

  async _search(q) {
    q = (q || "").trim();
    if (!q) return;
    if (/open\.spotify\.com\/|spotify:(playlist|album|track):/.test(q)) {
      // A Spotify link: play it as the queue.
      this.$(".searchbox input").value = "";
      this._call("home_radio.play_spotify", { url: q });
      if (this._config.tabs.includes("queue")) { this._tab = "queue"; this._update(); }
      return;
    }
    const list = this.$(".results");
    list.innerHTML = `<div class="spin"></div>`;
    try {
      const res = await this._call("home_radio.search_youtube", { query: q }, true);
      const items = res?.response?.[this._config.entity]?.results || res?.response?.results || [];
      this._results = { q, items };
      this._renderRows(list, items, q, null);
    } catch (e) {
      list.innerHTML = `<div class="muted">Η αναζήτηση απέτυχε.</div>`;
    }
  }

  _renderRows(el, items, q, activeIndex) {
    if (!items.length) { el.innerHTML = `<div class="muted">Τίποτα εδώ ακόμα.</div>`; return; }
    el.innerHTML = items.map((t, i) => `
      <button class="row${i === activeIndex ? " on" : ""}" data-i="${i}" data-url="${esc(t.url)}" data-q="${esc(q || "")}">
        <img loading="lazy" src="${esc(t.thumbnail)}" alt="">
        <div class="t"><div>${esc(t.title)}</div><div>${esc(t.channel)}</div></div>
        <span class="d">${t.duration ? fmt(t.duration) : ""}</span>
      </button>`).join("");
  }

  _update() {
    const st = this._st, $ = this.$;
    if (!st || !$) return;
    const a = st.attributes;
    const playing = st.state === "playing" || st.state === "buffering";
    const hero = $(".hero");
    hero.classList.toggle("playing", playing);
    const img = a.entity_picture ? (a.entity_picture.startsWith("http") ? a.entity_picture : this._hass.hassUrl(a.entity_picture)) : "";
    hero.classList.toggle("empty", !img);
    if (this._img !== img) {
      this._img = img;
      $(".bg").style.backgroundImage = img ? `url("${img}")` : "";
      const art = $(".art");
      art.className = "art" + (img ? (a.kind === "youtube" ? " yt" : "") : " none");
      art.style.backgroundImage = img ? `url("${img}")` : "";
      art.innerHTML = img ? "" : svg(ICONS.radio, 44);
    }
    $(".kind").textContent = st.state === "buffering" ? "Φόρτωση…" : a.kind === "youtube" ? "YouTube" : a.kind === "station" ? "Ραδιόφωνο" : "Home Radio";
    $(".title").textContent = a.media_title || "Διάλεξε σταθμό ή τραγούδι";
    $(".artist").textContent = a.media_artist || (a.kind === "youtube" && a.youtube_query ? `«${a.youtube_query}»` : "");
    $(".main").innerHTML = svg(playing ? ICONS.pause : ICONS.play, 32);
    $(".vol button").innerHTML = svg(a.is_volume_muted ? ICONS.mute : ICONS.vol, 22);
    if (a.volume_level != null && !(this._dragVol && Date.now() - this._dragVol < 1500)) $(".vol input").value = Math.round(a.volume_level * 100);
    this._updateProgress();

    for (const b of this.shadowRoot.querySelectorAll(".tabs button")) b.classList.toggle("on", b.dataset.tab === this._tab);
    for (const p of this.shadowRoot.querySelectorAll(".pane")) p.hidden = p.dataset.pane !== this._tab;

    const stations = a.stations || [];
    const key = JSON.stringify(stations) + "|" + a.station_id + "|" + (a.kind === "station" && playing);
    if (this._stKey !== key) {
      this._stKey = key;
      const cats = {};
      for (const s of stations) (cats[s.category || ""] ||= []).push(s);
      const names = Object.keys(cats);
      const label = (c) => ({ greek: "Ελληνικοί", "": "Σταθμοί" })[c] || c;
      $('[data-pane="stations"]').innerHTML = stations.length ? names.map((c) => `
        ${names.length > 1 ? `<div class="cat">${esc(label(c))}</div>` : ""}
        <div class="grid">${cats[c].map((s) => `
          <button class="st${a.kind === "station" && a.station_id === s.id ? " on" : ""}" data-id="${esc(s.id)}">
            <div class="logo${s.logo ? "" : " text"}" ${s.logo ? `style="background-image:url('${esc(s.logo)}')"` : ""}>
              ${s.logo ? "" : esc(s.name.slice(0, 2))}
              ${a.kind === "station" && a.station_id === s.id && playing ? `<span class="eq"><i></i><i></i><i></i><i></i></span>` : ""}
            </div>
            <div class="name">${esc(s.name)}</div>
          </button>`).join("")}</div>`).join("") : `<div class="muted">Δεν υπάρχουν σταθμοί.</div>`;
      $('[data-pane="stations"]').classList.toggle("playing", playing);
    }

    const recent = a.recent_searches || [];
    if (this._recentKey !== recent.join("|")) {
      this._recentKey = recent.join("|");
      $(".recent").innerHTML = recent.map((q) => `<button data-q="${esc(q)}">${esc(q)}</button>`).join("");
    }

    const lists = a.saved_lists || [];
    const lkey = JSON.stringify(lists) + "|" + (a.queue_list_id || "");
    if (this._listsKey !== lkey && $('[data-pane="lists"]')) {
      this._listsKey = lkey;
      $('[data-pane="lists"]').innerHTML = lists.length ? `<div class="grid">${lists.map((l) => `
        <div class="st pl${l.id === a.queue_list_id ? " on" : ""}" role="button" tabindex="0" data-id="${esc(l.id)}" data-name="${esc(l.name)}">
          <div class="logo${l.image ? "" : " text"}" ${l.image ? `style="background-image:url('${esc(l.image)}')"` : ""}>${l.image ? "" : "♫"}
            <span class="count">${l.count}</span>${l.id === a.queue_list_id ? `<span class="eq"><i></i><i></i><i></i><i></i></span>` : ""}
            <button class="del" title="Αφαίρεση">×</button></div>
          <div class="name">${esc(l.name)}</div>
        </div>`).join("")}</div>`
        : `<div class="muted">Καμία λίστα ακόμα.<br>Επικόλλησε ένα link λίστας ή άλμπουμ του Spotify στην καρτέλα YouTube· κρατιέται εδώ για την επόμενη φορά.</div>`;
    }

    const qkey = JSON.stringify(a.queue || []) + "|" + a.queue_index;
    if (this._queueKey !== qkey) {
      this._queueKey = qkey;
      this._renderRows($(".queue"), a.queue || [], a.youtube_query, a.queue_index);
    }
  }

  _updateProgress() {
    const st = this._st, $ = this.$;
    if (!st || !$) return;
    const a = st.attributes, prog = $(".progress");
    const show = a.kind === "youtube" && a.media_duration > 0;
    prog.hidden = !show;
    if (!show) return;
    let pos = a.media_position || 0;
    if (st.state === "playing" && a.media_position_updated_at) pos += (Date.now() - new Date(a.media_position_updated_at).getTime()) / 1000;
    pos = Math.min(pos, a.media_duration);
    $(".pos").textContent = fmt(pos);
    $(".dur").textContent = fmt(a.media_duration);
    $(".bar b").style.width = `${(pos / a.media_duration) * 100}%`;
  }
}

if (!customElements.get("home-radio-card")) {
  customElements.define("home-radio-card", HomeRadioCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "home-radio-card",
    name: "Home Radio",
    description: "Ραδιόφωνο και YouTube που παίζουν από το ίδιο το Home Assistant",
    preview: true,
  });
}
