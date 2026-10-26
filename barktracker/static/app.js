"use strict";
// Barktracker dashboard — vanilla JS, no build step, works offline on the LAN.

const $ = (s, el = document) => el.querySelector(s);
const view = $("#view");
const S = { dogs: {}, status: null, tz: undefined, overviewDate: null, clipFilters: { dog: "", unreviewed: false, min: 0, offset: 0 } };
const UNKNOWN = "#9aa5ad";
const NOT_BARK = 0;

// ---------------------------------------------------------------- helpers
async function api(path, opts = {}) {
  const o = { ...opts, headers: { "Content-Type": "application/json", ...(opts.headers || {}) } };
  if (o.body && typeof o.body !== "string") o.body = JSON.stringify(o.body);
  const r = await fetch(path, o);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (_) { /* not json */ }
    throw new Error(msg);
  }
  return r.json();
}
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
function h(html) { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstElementChild; }

function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.add("show");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove("show"), 2200);
}
function fmtDur(s) {
  if (s > 0 && s < 1) return "under 1 s";
  s = Math.max(0, Math.round(s));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60), r = s % 60;
  if (m < 60) return r ? `${m} min ${r} s` : `${m} min`;
  const hh = Math.floor(m / 60), mm = m % 60;
  return mm ? `${hh} h ${mm} min` : `${hh} h`;
}
function fmtMinutes(s) {
  if (s < 90) return `${Math.round(s)} seconds`;
  const m = s / 60;
  if (m < 90) return `${Math.round(m)} minute${Math.round(m) === 1 ? "" : "s"}`;
  return `${(m / 60).toFixed(1)} hours`;
}
const timeOpts = () => (S.tz ? { timeZone: S.tz } : {});
const fmtTime = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", ...timeOpts() });
const fmtTimeS = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit", ...timeOpts() });
const fmtDay = (ts) => new Date(ts * 1000).toLocaleDateString([], { weekday: "short", month: "short", day: "numeric", ...timeOpts() });
function isoToday() {
  const p = new Intl.DateTimeFormat("en-CA", { year: "numeric", month: "2-digit", day: "2-digit", ...timeOpts() }).format(new Date());
  return p;
}
function shiftIso(iso, days) {
  const d = new Date(iso + "T12:00:00Z"); d.setUTCDate(d.getUTCDate() + days); return d.toISOString().slice(0, 10);
}
function prettyIso(iso) {
  if (iso === isoToday()) return "Today";
  if (iso === shiftIso(isoToday(), -1)) return "Yesterday";
  return new Date(iso + "T12:00:00Z").toLocaleDateString([], { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" });
}
function dogName(k) { if (k === "u" || k == null) return "Unidentified"; return S.dogs[k]?.name ?? "Unknown dog"; }
function dogColor(k) { if (k === "u" || k == null) return UNKNOWN; return S.dogs[k]?.color ?? UNKNOWN; }
function dominant(dogs) {
  let best = null, n = -1;
  for (const [k, v] of Object.entries(dogs || {})) if (v > n) { best = k; n = v; }
  return best;
}
function dogTags(dogs) {
  const e = Object.entries(dogs || {}).sort((a, b) => b[1] - a[1]);
  if (!e.length) return `<span class="tag">No dog</span>`;
  return e.map(([k, n]) => `<span class="tag" style="--c:${dogColor(k)}"><i></i>${esc(dogName(k))} ×${n}</span>`).join("");
}
async function loadDogs() {
  const r = await api("/api/dogs");
  S.dogs = Object.fromEntries(r.dogs.map((d) => [String(d.id), d]));
  S.minLabels = r.min_labels;
  return r;
}

// ------------------------------------------------------- shared playback
const player = new Audio();
player.preload = "none";
let playingBtn = null;
const ICON_PLAY = '<svg viewBox="0 0 12 12"><path d="M2 1l9 5-9 5z"/></svg>';
const ICON_PAUSE = '<svg viewBox="0 0 12 12"><path d="M2 1h3v10H2zM7 1h3v10H7z"/></svg>';
function playBtn(id) {
  return `<button class="play" data-play="${id}" aria-label="Play clip">${ICON_PLAY}</button>`;
}
function resetBtn() { if (playingBtn) { playingBtn.classList.remove("playing"); playingBtn.innerHTML = ICON_PLAY; playingBtn.setAttribute("aria-label", "Play clip"); playingBtn = null; } }
player.addEventListener("ended", resetBtn);
document.addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-play]");
  if (!b) return;
  ev.preventDefault(); ev.stopPropagation();
  if (playingBtn === b) { player.pause(); resetBtn(); return; }
  resetBtn();
  player.src = `/api/episodes/${b.dataset.play}/audio`;
  player.play().catch(() => toast("This clip couldn't be played"));
  playingBtn = b; b.classList.add("playing"); b.innerHTML = ICON_PAUSE; b.setAttribute("aria-label", "Pause clip");
});

// ---------------------------------------------------------- status poll
async function pollStatus() {
  try {
    const s = await api("/api/status");
    S.status = s;
    if (s.settings.timezone && s.settings.timezone !== "system") S.tz = s.settings.timezone;
    const d = s.detector;
    if (route.name === "settings") renderSystemLive();
    if (route.name === "overview" && d && S.lastEpisodes !== s.counts.episodes) {
      if (S.lastEpisodes !== undefined && S.overviewDate === isoToday()) renderOverview(true);
      S.lastEpisodes = s.counts.episodes;
    }
  } catch (_) { /* the heartbeat band shows connection problems */ }
}

// ---------------------------------------------------------------- router
const route = { name: "", arg: null };
function navigate() {
  const [, name = "", arg] = location.hash.split("/");
  route.name = name || "overview"; route.arg = arg;
  if (name === "clip") route.name = "clip";
  document.querySelectorAll("[data-nav]").forEach((a) => a.toggleAttribute("aria-current", false));
  const navName = route.name === "clip" ? "clips" : route.name;
  $(`[data-nav="${navName}"]`)?.setAttribute("aria-current", "page");
  player.pause(); resetBtn();
  document.body.classList.toggle("kiosk", route.name === "live");
  if (route.name === "ordinance" || route.name === "system") { location.replace(`#/settings/${route.name === "ordinance" ? "code" : "system"}`); return; }
  ({ overview: renderOverview, clips: renderClips, clip: renderClip, dogs: renderDogs, report: renderReport, settings: renderSettings, live: renderLive }[route.name] || renderOverview)();
  window.scrollTo(0, 0);
}
window.addEventListener("hashchange", navigate);
let resizeT;
window.addEventListener("resize", () => { clearTimeout(resizeT); resizeT = setTimeout(() => { if (route.name === "overview") renderOverview(true); }, 250); });

// --------------------------------------------------------------- overview
async function renderOverview(soft = false) {
  if (!S.overviewDate) S.overviewDate = isoToday();
  const date = S.overviewDate;
  if (!soft) view.innerHTML = `<p class="muted">Loading…</p>`;
  const [day, sum, heat, recent] = await Promise.all([
    api(`/api/day?date=${date}`), api("/api/summary?days=14"), api("/api/heatmap?days=28"),
    api("/api/episodes?limit=6"), loadDogs(),
  ]);
  if (route.name !== "overview") return;
  const eps = day.episodes;
  const total = eps.reduce((a, e) => a + e.duration, 0);
  const longest = eps.reduce((a, e) => (e.duration > (a?.duration ?? -1) ? e : a), null);
  const isToday = date === isoToday();
  const when = isToday ? "today" : prettyIso(date) === "Yesterday" ? "yesterday" : `on ${prettyIso(date)}`;

  // per-dog share for the sentence
  const perDog = {};
  for (const e of eps) {
    const tot = Object.values(e.dogs).reduce((a, b) => a + b, 0) || 1;
    for (const [k, n] of Object.entries(e.dogs)) perDog[k] = (perDog[k] || 0) + (e.duration * n) / tot;
  }
  const top = Object.entries(perDog).sort((a, b) => b[1] - a[1]);
  let headline, detail = "";
  if (!eps.length) {
    headline = isToday ? "No barking so far today." : `No barking logged ${when}.`;
    detail = isToday && S.status?.detector ? "Listening. Episodes show up here as soon as they end." : "";
  } else {
    headline = `Barked ${fmtMinutes(total)} ${when} across ${eps.length} episode${eps.length === 1 ? "" : "s"}.`;
    detail = `Longest was ${fmtDur(longest.duration)} at ${fmtTime(longest.start_ts)}.`;
    if (top.length && top[0][0] !== "u" && total > 0) {
      detail += ` ${dogName(top[0][0])} did ${Math.round((100 * top[0][1]) / total)}% of it.`;
    }
    const hits = (day.incidents || []).filter((i) => i.flags.length).length;
    if (hits) detail += ` ${hits} incident${hits === 1 ? "" : "s"} met your ordinance criteria.`;
  }
  const cw = chartWidths();
  const dogsSeen = new Set([...Object.keys(perDog)]);
  let legend = [...dogsSeen].map((k) => `<span style="--c:${dogColor(k)}">${esc(dogName(k))}</span>`).join("");
  if ((day.incidents || []).some((i) => i.flags.length)) legend += `<span style="--c:var(--danger)"><a href="#/report">Meets ordinance criteria</a></span>`;

  view.innerHTML = `
    <section class="hero">
      <div class="row spread">
        <div class="daynav" aria-label="Choose day">
          <button class="btn icon" id="prevDay" aria-label="Previous day">‹</button>
          <strong>${prettyIso(date)}</strong>
          <button class="btn icon" id="nextDay" aria-label="Next day" ${isToday ? "disabled" : ""}>›</button>
        </div>
        ${!isToday ? `<button class="btn" id="todayBtn">Back to today</button>` : ""}
      </div>
      <div><h1>${esc(headline)}</h1><p class="sub">${esc(detail)}</p></div>
      <div class="strip-wrap">${stripSvg(day, isToday, cw.strip)}
        <div class="legend" style="margin-top:6px">${legend}</div></div>
    </section>
    <section class="grid2">
      <div class="panel"><h2>Last two weeks</h2>${barsSvg(sum, cw.bars)}<p class="small muted" style="margin:6px 0 0">Minutes of barking per day. Select a day to see it above.</p></div>
      <div class="panel"><h2>When it happens</h2>${heatSvg(heat, cw.heat)}<p class="small muted" style="margin:6px 0 0">Past 4 weeks by hour. Darker means more barking.</p></div>
    </section>
    <section>
      <div class="row spread"><h2>Recent clips</h2><a href="#/clips" class="small">All clips</a></div>
      ${episodeList(recent.items, "Nothing recorded yet", "When the Pi hears barking it saves a clip here for you to review.")}
    </section>`;
  $("#prevDay").onclick = () => { S.overviewDate = shiftIso(date, -1); renderOverview(); };
  $("#nextDay").onclick = () => { S.overviewDate = shiftIso(date, 1); renderOverview(); };
  const tb = $("#todayBtn"); if (tb) tb.onclick = () => { S.overviewDate = isoToday(); renderOverview(); };
  view.querySelectorAll("[data-day]").forEach((g) => {
    const go = () => { S.overviewDate = g.dataset.day; renderOverview(); };
    g.addEventListener("click", go);
    g.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } });
  });
}

function chartWidths() {
  const cs = getComputedStyle(view), mainW = view.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight), two = window.innerWidth > 860, inner = 34;
  return { strip: Math.max(280, mainW - inner), bars: Math.max(280, two ? ((mainW - 24) * 1.4) / 2.4 - inner : mainW - inner),
           heat: Math.max(280, two ? (mainW - 24) / 2.4 - inner : mainW - inner) };
}
function stripSvg(day, isToday, W = 1000) {
  const H = 96, t0 = day.start_ts, span = day.end_ts - day.start_ts;
  const x = (ts) => ((ts - t0) / span) * W;
  let g = "";
  const every = W < 520 ? 6 : 3;
  for (let hr = 0; hr <= 24; hr += every) {
    const xx = (hr / 24) * W;
    g += `<line x1="${xx}" x2="${xx}" y1="6" y2="70" stroke="var(--faint)"/>`;
    if (hr < 24) g += `<text x="${xx + 3}" y="88">${hr === 0 ? "12a" : hr === 12 ? "12p" : hr > 12 ? hr - 12 + "p" : hr + "a"}</text>`;
  }
  g += `<rect x="0" y="22" width="${W}" height="30" fill="var(--faint)" rx="3"/>`;
  for (const e of day.episodes) {
    const w = Math.max(3, x(e.end_ts) - x(e.start_ts));
    const label = `${fmtTime(e.start_ts)} · ${fmtDur(e.duration)} · ${e.bark_count} barks`;
    g += `<a href="#/clip/${e.id}" aria-label="${esc(label)}"><rect x="${x(e.start_ts)}" y="16" width="${w}" height="42" rx="2" fill="${dogColor(dominant(e.dogs))}"><title>${esc(label)}</title></rect></a>`;
  }
  for (const inc of day.incidents || []) {
    if (!inc.flags.length) continue;
    const w = Math.max(4, x(inc.end_ts) - x(inc.start_ts));
    g += `<rect x="${x(inc.start_ts)}" y="10" width="${w}" height="3" fill="var(--danger)"><title>${esc(inc.flags.map((f) => FLAG_NAMES[f]).join(", "))}</title></rect>`;
  }
  for (const [ts, d] of day.barks) g += `<line x1="${x(ts)}" x2="${x(ts)}" y1="62" y2="70" stroke="${dogColor(d == null ? "u" : String(d))}" stroke-width="1.5"/>`;
  if (isToday) {
    const nx = x(Date.now() / 1000);
    g += `<line x1="${nx}" x2="${nx}" y1="4" y2="72" stroke="var(--ink)" stroke-dasharray="2 3"/><text x="${nx + 4}" y="12" style="fill:var(--ink)">now</text>`;
  }
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Barking across the day">${g}</svg>`;
}

function barsSvg(sum, W = 560) {
  const H = 190, pad = 22, gut = 34, days = sum.days;
  const max = Math.max(60, ...days.map((d) => d.seconds));
  const step = (W - gut) / days.length, bw = step * 0.66;
  const scale = (s) => ((H - pad - 14) * s) / max;
  let g = "";
  const tickMin = niceTick(max / 60);
  for (let m = 0; m * 60 <= max; m += tickMin) {
    const y = H - pad - scale(m * 60);
    g += `<line x1="${gut}" x2="${W}" y1="${y}" y2="${y}" stroke="var(--faint)"/><text x="${gut - 6}" y="${y + 4}" text-anchor="end">${m ? m + " min" : "0"}</text>`;
  }
  days.forEach((d, i) => {
    let y = H - pad;
    const bx = gut + i * step + (step - bw) / 2;
    const parts = Object.entries(d.by_dog).sort((a, b) => (a[0] === "u") - (b[0] === "u"));
    let rects = "";
    for (const [k, s] of parts) { const hh = scale(s); y -= hh; rects += `<rect x="${bx}" y="${y}" width="${bw}" height="${Math.max(hh, 0)}" fill="${dogColor(k)}"/>`; }
    const wd = new Date(d.date + "T12:00:00Z").toLocaleDateString([], { weekday: "narrow", timeZone: "UTC" });
    const sel = d.date === S.overviewDate;
    const label = `${prettyIso(d.date)}: ${fmtDur(d.seconds)}, ${d.episodes} episodes`;
    g += `<g data-day="${d.date}" style="cursor:pointer" role="button" tabindex="0" aria-label="${esc(label)}"><title>${esc(label)}</title><rect x="${gut + i * step}" y="0" width="${step}" height="${H}" fill="${sel ? "var(--faint)" : "transparent"}" fill-opacity="${sel ? 0.7 : 1}"/>${rects}<text x="${bx + bw / 2}" y="${H - 6}" text-anchor="middle" ${sel ? 'style="fill:var(--ink);font-weight:600"' : ""}>${wd}</text></g>`;
  });
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Minutes of barking per day">${g}</svg>`;
}
function niceTick(maxMin) { for (const t of [1, 2, 5, 10, 15, 30, 60, 120]) if (maxMin / t <= 4) return t; return 240; }

function heatSvg(heat, Wt = 394) {
  const days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const lx = 34, cw = Math.max(9, (Wt - lx) / 24), ch = Math.min(22, Math.max(16, cw * 1.25)), W = lx + 24 * cw, H = 7 * ch + 20;
  const max = Math.max(1, ...heat.grid.flat());
  let g = "";
  heat.grid.forEach((row, r) => {
    g += `<text x="0" y="${r * ch + 14}">${days[r]}</text>`;
    row.forEach((v, c) => {
      const op = v ? 0.12 + 0.88 * Math.sqrt(v / max) : 0;
      g += `<rect x="${lx + c * cw}" y="${r * ch + 1}" width="${cw - 2}" height="${ch - 3}" rx="2" fill="${v ? "var(--ink)" : "var(--faint)"}" fill-opacity="${v ? op.toFixed(2) : 1}"><title>${days[r]} ${c}:00 · ${fmtDur(v)}</title></rect>`;
    });
  });
  for (const hr of [0, 6, 12, 18]) g += `<text x="${lx + hr * cw}" y="${H - 2}">${hr === 0 ? "12a" : hr === 12 ? "12p" : hr > 12 ? hr - 12 + "p" : hr + "a"}</text>`;
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Barking by weekday and hour">${g}</svg>`;
}

function episodeList(items, emptyTitle, emptyText) {
  if (!items.length) return `<div class="list"><div class="empty"><strong>${esc(emptyTitle)}</strong>${esc(emptyText)}</div></div>`;
  return `<div class="list">${items.map((e) => `
    <a class="ep ${e.live ? "live-ep" : ""}" href="#/clip/${e.id}">
      ${e.has_clip ? playBtn(e.id) : `<span></span>`}
      <span class="when">${fmtTime(e.start_ts)}<span class="day">${fmtDay(e.start_ts)}</span></span>
      <span class="dur">${fmtDur(e.duration)}</span>
      <span class="n">${e.bark_count} ${e.false_positive ? "sound" : "bark"}${e.bark_count === 1 ? "" : "s"}</span>
      <span class="dogs">${dogTags(e.dogs)}</span>
      <span class="state ${e.reviewed || e.false_positive ? "" : "new"}">${e.live ? "Recording" : e.false_positive ? (e.has_clip ? "Not a bark" : "Not a bark, audio deleted") : e.reviewed ? "Reviewed" : "New"}</span>
    </a>`).join("")}</div>`;
}

// ------------------------------------------------------------------ clips
async function renderClips() {
  await loadDogs();
  const f = S.clipFilters;
  f.kind = f.kind || "barking";
  const qs = new URLSearchParams({ limit: 30, offset: f.offset, kind: f.kind });
  if (f.dog) qs.set("dog", f.dog);
  if (f.unreviewed) qs.set("unreviewed", "true");
  if (f.min) qs.set("min_duration", f.min);
  const [r, nb] = await Promise.all([api(`/api/episodes?${qs}`), api("/api/not-bark")]);
  if (route.name !== "clips") return;
  const notBark = f.kind === "not_bark";
  const opts = Object.values(S.dogs).map((d) => `<option value="${d.id}" ${f.dog == d.id ? "selected" : ""}>${esc(d.name)}</option>`).join("");
  view.innerHTML = `
    <section>
      <h1 style="margin-bottom:16px">Clips</h1>
      <div class="tabs" role="tablist">
        <button role="tab" class="tab" data-kind="barking" aria-selected="${!notBark}">Barking</button>
        <button role="tab" class="tab" data-kind="not_bark" aria-selected="${notBark}">Not a bark <span class="count">${nb.episodes}</span></button>
      </div>
      ${notBark ? `<div class="panel nbinfo">
        <p style="margin:0">These clips were marked “Not a bark”. They're left out of stats and reports${nb.filter_active ? ", and the Pi uses them to ignore similar sounds" : ". After a few, the Pi starts ignoring similar sounds"}.${nb.sounds_inside_barking_clips ? ` ${nb.sounds_inside_barking_clips} more single sound${nb.sounds_inside_barking_clips === 1 ? " was" : "s were"} marked inside barking clips.` : ""}</p>
        <p class="small muted" style="margin:6px 0 0">Deleting only the audio frees disk space and keeps what was learned. “Delete and forget” on a clip also removes it from the filter.</p>
        ${nb.with_audio ? `<div class="row" style="margin-top:10px"><button class="btn" id="nbBulk">Delete audio of all ${nb.with_audio} (${nb.audio_mb} MB), keep what was learned</button></div>` : ""}
      </div>` : ""}
      <div class="filters" ${notBark ? 'hidden' : ""}>
        <select id="fDog" aria-label="Filter by dog"><option value="">All dogs</option>${opts}<option value="u" ${f.dog === "u" ? "selected" : ""}>Unidentified</option></select>
        <label class="row" style="gap:6px"><input type="checkbox" id="fNew" ${f.unreviewed ? "checked" : ""}> Not yet reviewed</label>
        <label class="row" style="gap:6px">At least <input type="number" id="fMin" min="0" step="5" value="${f.min || 0}" style="width:5em"> seconds</label>
        <span class="muted small" style="margin-left:auto">${r.total} clip${r.total === 1 ? "" : "s"}</span>
      </div>
      ${notBark ? episodeList(r.items, "Nothing marked “Not a bark”", "On a clip, choose “Not a bark” for sounds that aren't a dog: they show up here.")
                : episodeList(r.items, "No clips match", "Try clearing a filter.")}
      <div class="pager">
        <button class="btn" id="pPrev" ${f.offset === 0 ? "disabled" : ""}>Newer</button>
        <span class="small muted">${r.total ? `${f.offset + 1}–${Math.min(f.offset + 30, r.total)}` : ""}</span>
        <button class="btn" id="pNext" ${f.offset + 30 >= r.total ? "disabled" : ""}>Older</button>
      </div>
    </section>`;
  view.querySelectorAll("[data-kind]").forEach((b) => b.onclick = () => { f.kind = b.dataset.kind; f.offset = 0; renderClips(); });
  const bulk = $("#nbBulk");
  if (bulk) bulk.onclick = async () => {
    const res = await api("/api/not-bark/delete-audio", { method: "POST" });
    toast(`Deleted audio of ${res.deleted} clip${res.deleted === 1 ? "" : "s"}, freed ${res.freed_mb} MB`); renderClips();
  };
  $("#fDog").onchange = (e) => { f.dog = e.target.value; f.offset = 0; renderClips(); };
  $("#fNew").onchange = (e) => { f.unreviewed = e.target.checked; f.offset = 0; renderClips(); };
  $("#fMin").onchange = (e) => { f.min = Number(e.target.value) || 0; f.offset = 0; renderClips(); };
  $("#pPrev").onclick = () => { f.offset = Math.max(0, f.offset - 30); renderClips(); };
  $("#pNext").onclick = () => { f.offset += 30; renderClips(); };
}

// ------------------------------------------------------------ clip detail
const LUT = (() => {
  const stops = [[0, [15, 20, 24]], [0.35, [24, 70, 104]], [0.6, [43, 152, 150]], [0.82, [226, 160, 63]], [1, [255, 246, 220]]];
  const lut = new Uint8ClampedArray(256 * 3);
  for (let i = 0; i < 256; i++) {
    const t = i / 255; let k = 0; while (k < stops.length - 2 && t > stops[k + 1][0]) k++;
    const [a, ca] = stops[k], [b, cb] = stops[k + 1], u = (t - a) / (b - a);
    for (let j = 0; j < 3; j++) lut[i * 3 + j] = ca[j] + (cb[j] - ca[j]) * u;
  }
  return lut;
})();

async function renderClip() {
  const id = Number(route.arg);
  const [ep] = await Promise.all([api(`/api/episodes/${id}`), loadDogs()]).catch((e) => { view.innerHTML = `<div class="notice err">${esc(e.message)}</div>`; return []; });
  if (!ep || route.name !== "clip") return;
  const clipLen = ep.clip_seconds || (ep.duration + 4);
  const sel = new Set();

  view.innerHTML = `
    <section>
      <div class="row spread" style="margin-bottom:14px">
        <a href="#/clips" class="small">All clips</a>
        <div class="row">
          <button class="btn" id="prevEp" ${ep.prev_id ? "" : "disabled"}>Earlier</button>
          <button class="btn" id="nextEp" ${ep.next_id ? "" : "disabled"}>Later</button>
        </div>
      </div>
      <h1>${fmtDay(ep.start_ts)}, ${fmtTimeS(ep.start_ts)}</h1>
      <dl class="facts">
        <div><dt>${ep.false_positive ? "Lasted" : "Barking for"}</dt><dd>${fmtDur(ep.duration)}</dd></div>
        <div><dt>${ep.false_positive ? "Sounds" : "Barks"}</dt><dd>${ep.bark_count}</dd></div>
        <div><dt>Loudest</dt><dd>${ep.peak_db != null ? ep.peak_db.toFixed(0) + " dBFS" : "–"}</dd></div>
        <div><dt>Detector confidence</dt><dd>${ep.max_prob != null ? Math.round(ep.max_prob * 100) + "%" : "–"}</dd></div>
      </dl>
      ${ep.live ? `<div class="notice">Still recording. Refresh when the barking stops to hear the full clip.</div>` : ""}
      ${ep.false_positive ? `<div class="notice nb">
        <p style="margin:0 0 8px"><strong>Marked “Not a bark”.</strong> It's left out of stats and reports, and its sounds teach the Pi to ignore similar ones.</p>
        <div class="row">
          <button class="btn" id="nbRestore">Restore as barking</button>
          ${ep.has_clip ? `<button class="btn" id="nbAudio">Delete audio, keep what it learned</button>` : ""}
          <button class="btn danger" id="nbForget">Delete and forget</button>
        </div></div>` : ""}
      ${ep.has_clip ? `
        <div class="spec-wrap" id="spec"><canvas id="specCanvas" width="900" height="80"></canvas><div class="spec-marks" id="marks"></div><div class="playhead" id="ph" style="left:0"></div></div>
        <div class="spec-time"><span>0 s</span><span>Select a bark to hear it on its own and label it. Shift-select to pick several.</span><span>${clipLen.toFixed(1)} s</span></div>
        <audio id="aud" controls preload="auto" src="/api/episodes/${id}/audio"></audio>`
        : `<div class="notice">${ep.false_positive ? "The audio was deleted. The sounds are still used to recognize this false alarm." : "The audio for this episode was removed by the retention policy. The log entry is kept."}</div>`}
    </section>
    <section class="panel labeler" id="labeler"></section>
    <section class="panel">
      <label for="note"><h2>Note</h2></label>
      <textarea id="note" rows="2" style="width:100%" placeholder="Mail carrier, squirrel, doorbell…">${esc(ep.note)}</textarea>
      <div class="row spread" style="margin-top:10px">
        <button class="btn" id="saveNote">Save note</button>
        ${ep.false_positive ? "" : `<button class="btn danger" id="delEp">Delete episode</button>`}
      </div>
    </section>`;

  $("#prevEp").onclick = () => (location.hash = `#/clip/${ep.prev_id}`);
  $("#nextEp").onclick = () => (location.hash = `#/clip/${ep.next_id}`);
  $("#saveNote").onclick = async () => { await api(`/api/episodes/${id}`, { method: "PATCH", body: { note: $("#note").value } }); toast("Note saved"); };
  const de = $("#delEp");
  if (de) de.onclick = async () => {
    if (!confirm("Delete this episode and its clip? This can't be undone.")) return;
    await api(`/api/episodes/${id}`, { method: "DELETE" }); toast("Episode deleted");
    location.hash = ep.next_id ? `#/clip/${ep.next_id}` : "#/clips";
  };
  if (ep.false_positive) {
    $("#nbRestore").onclick = async () => {
      await api(`/api/episodes/${id}/label`, { method: "POST", body: { dog_id: null } });
      toast("Restored as barking"); renderClip();
    };
    const na = $("#nbAudio");
    if (na) na.onclick = async () => {
      await api(`/api/episodes/${id}?keep_training=true`, { method: "DELETE" });
      toast("Audio deleted. The filter still remembers this sound."); renderClip();
    };
    $("#nbForget").onclick = async () => {
      if (!confirm("Delete this clip and stop using it to recognize false alarms? This can't be undone.")) return;
      await api(`/api/episodes/${id}`, { method: "DELETE" });
      toast("Deleted and forgotten"); S.clipFilters.kind = "not_bark"; location.hash = "#/clips";
    };
  }

  const aud = $("#aud");
  let stopAt = null;
  if (ep.has_clip) {
    api(`/api/episodes/${id}/spectrogram?width=900&height=80`).then((sp) => {
      const c = $("#specCanvas"); if (!c) return;
      c.width = sp.width; c.height = sp.height;
      const ctx = c.getContext("2d"), img = ctx.createImageData(sp.width, sp.height);
      const raw = atob(sp.data);
      for (let i = 0; i < raw.length; i++) { const v = raw.charCodeAt(i) * 3; img.data[i * 4] = LUT[v]; img.data[i * 4 + 1] = LUT[v + 1]; img.data[i * 4 + 2] = LUT[v + 2]; img.data[i * 4 + 3] = 255; }
      ctx.putImageData(img, 0, 0);
    }).catch(() => {});
    const tick = () => {
      if (!document.body.contains(aud)) return;
      $("#ph").style.left = `${(aud.currentTime / clipLen) * 100}%`;
      if (stopAt != null && aud.currentTime >= stopAt) { aud.pause(); stopAt = null; }
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
    $("#spec").addEventListener("click", (ev) => {
      if (ev.target.closest(".mark-bark")) return;
      const r = ev.currentTarget.getBoundingClientRect();
      aud.currentTime = ((ev.clientX - r.left) / r.width) * clipLen; stopAt = null; aud.play();
    });
  }

  function drawMarks() {
    const m = $("#marks"); if (!m) return;
    m.innerHTML = ep.barks.map((b) => {
      const k = b.dog_id === NOT_BARK ? null : b.dog_id == null ? "u" : String(b.dog_id);
      const color = b.dog_id === NOT_BARK ? "#c2453d" : dogColor(k);
      const who = b.dog_id === NOT_BARK ? "Not a bark" : dogName(k);
      const how = b.label_dog_id != null ? "labelled by you" : b.pred_dog_id != null ? `guessed, ${Math.round((b.pred_conf || 0) * 100)}% sure` : "not identified yet";
      return `<button class="mark-bark" data-b="${b.id}" aria-pressed="${sel.has(b.id)}" style="--c:${color};left:${(b.offset / clipLen) * 100}%;width:${Math.max(0.5, (b.duration / clipLen) * 100)}%" title="${esc(who)} (${how}) at ${b.offset.toFixed(1)} s" aria-label="Bark at ${b.offset.toFixed(1)} seconds: ${esc(who)}, ${how}"></button>`;
    }).join("");
    m.querySelectorAll(".mark-bark").forEach((btn) => btn.addEventListener("click", (ev) => {
      const bid = Number(btn.dataset.b), b = ep.barks.find((x) => x.id === bid);
      if (!(ev.shiftKey || ev.metaKey || ev.ctrlKey)) { const had = sel.has(bid) && sel.size === 1; sel.clear(); if (!had) sel.add(bid); }
      else sel.has(bid) ? sel.delete(bid) : sel.add(bid);
      drawMarks(); drawLabeler();
      if (aud && sel.has(bid)) { aud.currentTime = Math.max(0, b.offset - 0.25); stopAt = b.offset + b.duration + 0.35; aud.play(); }
    }));
  }

  function drawLabeler() {
    const dogs = Object.values(S.dogs);
    const target = sel.size ? `${sel.size} selected bark${sel.size === 1 ? "" : "s"}` : "this clip";
    const current = sel.size ? null : (() => { const ids = new Set(ep.barks.map((b) => b.label_dog_id)); return ids.size === 1 ? [...ids][0] : undefined; })();
    const snd = ep.sound;
    const suggest = new Set(snd && !sel.size ? dogs.filter((d) => d.size === snd.sounds_like && d.labelled_barks < (S.minLabels || 3)).map((d) => d.id) : []);
    $("#labeler").innerHTML = `
      <h2>${sel.size ? `Who made ${target}?` : "Who's barking in this clip?"}</h2>
      ${snd && !sel.size ? `<p class="small muted" style="margin:0">These barks sound ${SIZE_SOUND[snd.sounds_like]} (around ${fmtHz(snd.pitch_hz)}), like ${SIZE_WORD[snd.sounds_like]}.${suggest.size ? ` Suggested: ${[...suggest].map((id) => esc(S.dogs[id].name)).join(" or ")}. Tap to confirm.` : ""}</p>` : ""}
      <div class="row">
        ${dogs.map((d) => `<button class="chip ${suggest.has(d.id) ? "suggest" : ""}" data-label="${d.id}" style="--c:${d.color}" aria-pressed="${current === d.id}"><i></i>${esc(d.name)}${suggest.has(d.id) ? ` <span class="sugg">suggested</span>` : ""}</button>`).join("")}
        <button class="chip" data-label="0" style="--c:var(--danger)" aria-pressed="${current === 0}"><i></i>Not a bark</button>
        <button class="btn" data-label="clear">Clear labels</button>
        ${sel.size ? `<button class="btn" id="selNone">Use whole clip instead</button>` : ""}
      </div>
      <p class="small muted" style="margin:0">${dogs.length ? "Labelling teaches the Pi each dog's voice. Marking false alarms as “Not a bark” teaches it to ignore that sound next time." : `<a href="#/dogs">Add your dogs</a> first, then label clips here to teach the Pi who's who.`}</p>
      <p class="small muted" style="margin:0">Detected: ${dogTags(ep.dogs)}</p>`;
    $("#labeler").querySelectorAll("[data-label]").forEach((b) => b.addEventListener("click", async () => {
      const v = b.dataset.label === "clear" ? null : Number(b.dataset.label);
      try {
        let res;
        if (sel.size) res = await api("/api/barks/label", { method: "POST", body: { bark_ids: [...sel], dog_id: v } });
        else res = await api(`/api/episodes/${id}/label`, { method: "POST", body: { dog_id: v } });
        const fresh = await api(`/api/episodes/${id}`);
        Object.assign(ep, fresh); sel.clear(); drawMarks(); drawLabeler();
        const who = v === null ? "Labels cleared" : v === 0 ? "Marked as not a bark" : `Labelled as ${S.dogs[v]?.name}`;
        const info = res.identifier || {};
        toast(info.trained ? `${who}. Retrained on ${Object.values(info.counts).reduce((a, c) => a + c, 0)} labelled barks.` : `${who}.`);
      } catch (e) { toast(e.message); }
    }));
    const sn = $("#selNone"); if (sn) sn.onclick = () => { sel.clear(); drawMarks(); drawLabeler(); };
  }
  drawMarks(); drawLabeler();
  if (!ep.reviewed && !ep.live) api(`/api/episodes/${id}`, { method: "PATCH", body: { reviewed: true } }).catch(() => {});
}

// ------------------------------------------------------------------- dogs
async function renderDogs() {
  const r = await loadDogs();
  if (route.name !== "dogs") return;
  const st = await api("/api/status");
  const need = r.min_labels;
  const addrs = [...new Set(r.dogs.map((d) => d.address).filter(Boolean))];
  view.innerHTML = `
    <section>
      <h1>Dogs</h1>
      <p class="sub" style="max-width:64ch">Add each dog you hear, with its owner and address if it's a neighbor's. Label a few clips per dog; after ${need} or more labelled barks, new barks are matched to the closest voice automatically. Dogs at the same address are grouped together on complaint reports.</p>
    </section>
    <datalist id="addrList">${addrs.map((a) => `<option value="${esc(a)}">`).join("")}</datalist>
    <section class="list" id="dogList">
      ${r.dogs.length ? r.dogs.map((d) => `
        <div class="dogcard" style="--c:${d.color}">
          <input type="color" value="${d.color}" data-color="${d.id}" aria-label="Color for ${esc(d.name)}">
          <div>
            <input class="name" value="${esc(d.name)}" data-field="name" data-id="${d.id}" aria-label="Name">
            <div class="small muted">${d.labelled_barks} labelled, ${d.predicted_barks} matched automatically${d.address ? ` · ${esc(d.address)}` : ""}</div>
            <div class="meter" title="${Math.min(d.labelled_barks, need)} of ${need} labels needed to start matching"><b style="width:${Math.min(100, (100 * d.labelled_barks) / need)}%"></b></div>
            <label class="sizepick">Size <select data-field="size" data-id="${d.id}">
              ${[["", "Not sure"], ["small", "Small"], ["medium", "Medium"], ["large", "Large"]].map(([v, t]) => `<option value="${v}" ${(d.size || "") === v ? "selected" : ""}>${t}</option>`).join("")}
            </select></label>
            ${voiceBlock(d)}
            <details class="dogmore">
              <summary>Owner and address</summary>
              <div class="fields">
                <label>Owner<input type="text" value="${esc(d.owner)}" data-field="owner" data-id="${d.id}" placeholder="e.g. J. Smith"></label>
                <label>Address<input type="text" list="addrList" value="${esc(d.address)}" data-field="address" data-id="${d.id}" placeholder="e.g. 123 Elm St"></label>
                <label class="wide">Description<input type="text" value="${esc(d.description)}" data-field="description" data-id="${d.id}" placeholder="Breed, color, size"></label>
              </div>
            </details>
          </div>
          <div class="row">
            <a class="btn" href="#/clips" data-filter="${d.id}">Clips</a>
            <a class="btn" href="#/report" data-report="${d.id}">Report</a>
            <button class="btn danger" data-del="${d.id}">Remove</button>
          </div>
        </div>`).join("") : `<div class="empty"><strong>No dogs yet</strong>Add one below. Barks are still logged without dogs; they just show as unidentified.</div>`}
    </section>
    <section>
      <form class="addform" id="addDog">
        <label>Dog's name<input type="text" id="newName" required maxlength="40" placeholder="Rex"></label>
        <label>Size<select id="newSize"><option value="">Not sure</option><option value="small">Small</option><option value="medium">Medium</option><option value="large">Large</option></select></label>
        <label>Owner (optional)<input type="text" id="newOwner" maxlength="80"></label>
        <label>Address (optional)<input type="text" id="newAddr" list="addrList" maxlength="120"></label>
        <button class="btn primary" type="submit">Add dog</button>
      </form>
    </section>
    <section class="panel">
      <h2>Ignoring false alarms</h2>
      <p class="small muted" style="margin:0">${r.not_bark_examples} sound${r.not_bark_examples === 1 ? "" : "s"} marked “Not a bark”. ${st.identifier.ready ? "Similar sounds are now dropped before they're logged." : "Mark at least " + need + " to start filtering them out."} <a href="#/clips" id="reviewNb">Review them</a></p>
    </section>`;
  $("#addDog").onsubmit = async (ev) => {
    ev.preventDefault();
    try {
      await api("/api/dogs", { method: "POST", body: { name: $("#newName").value, size: $("#newSize").value, owner: $("#newOwner").value, address: $("#newAddr").value } });
      toast("Dog added"); renderDogs();
    } catch (e) { toast(e.message.includes("UNIQUE") ? "There's already a dog with that name" : e.message); }
  };
  view.querySelectorAll("[data-field]").forEach((i) => i.addEventListener("change", async () => {
    await api(`/api/dogs/${i.dataset.id}`, { method: "PATCH", body: { [i.dataset.field]: i.value } });
    toast(`${i.dataset.field === "name" ? "Name" : i.dataset.field[0].toUpperCase() + i.dataset.field.slice(1)} saved`);
    if (i.dataset.field === "address") loadDogs();
    if (i.dataset.field === "size") renderDogs();
  }));
  view.querySelectorAll("[data-color]").forEach((i) => i.addEventListener("change", async () => {
    await api(`/api/dogs/${i.dataset.color}`, { method: "PATCH", body: { color: i.value } }); renderDogs();
  }));
  view.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async () => {
    const d = S.dogs[b.dataset.del];
    if (!confirm(`Remove ${d.name}? Their barks stay in the log as unidentified.`)) return;
    await api(`/api/dogs/${d.id}`, { method: "DELETE" }); toast(`${d.name} removed`); renderDogs();
  }));
  view.querySelectorAll("[data-filter]").forEach((a) => a.addEventListener("click", () => { S.clipFilters = { ...S.clipFilters, dog: a.dataset.filter, offset: 0 }; }));
  const rn = $("#reviewNb"); if (rn) rn.onclick = () => { S.clipFilters = { ...S.clipFilters, kind: "not_bark", offset: 0 }; };
  view.querySelectorAll("[data-report]").forEach((a) => a.addEventListener("click", () => { S.report = { ...(S.report || {}), subject: `dog:${a.dataset.report}` }; }));
}


// ------------------------------------------------------------ dog voices
const SIZE_WORD = { small: "a small dog", medium: "a medium dog", large: "a large dog" };
const SIZE_SOUND = { small: "high and bright", medium: "mid-range", large: "deep" };
function fmtHz(hz) { return hz >= 1000 ? `${(hz / 1000).toFixed(1)} kHz` : `${Math.round(hz)} Hz`; }
function shapeSvg(shape, color) {
  if (!shape?.length) return "";
  const W = 120, H = 28, n = shape.length, lo = -30;
  const pts = shape.map((v, i) => `${((i / (n - 1)) * W).toFixed(1)},${(H - ((Math.max(lo, v) - lo) / -lo) * (H - 2) - 1).toFixed(1)}`).join(" ");
  return `<svg class="shape" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="Spectral shape of this dog's bark, low to high pitch"><polyline points="0,${H} ${pts} ${W},${H}" fill="${color}" fill-opacity=".18" stroke="${color}" stroke-width="1.5"/></svg>`;
}
function voiceBlock(d) {
  const p = d.profile;
  if (!p) return `<p class="voice small muted">Label a few of ${esc(d.name)}'s barks to build a voice profile.</p>`;
  const sep = d.separation;
  const mismatch = d.size && p.sounds_like !== d.size && p.sounds_like_share >= 0.6
    ? ` Sounds more like ${SIZE_WORD[p.sounds_like]} than the size you set; that's common, and labels decide matching.` : "";
  return `<div class="voice">
    ${shapeSvg(p.shape, d.color)}
    <dl class="voicefacts">
      <div><dt>Pitch</dt><dd>${fmtHz(p.pitch_hz)}</dd></div>
      <div><dt>Tone</dt><dd>${fmtHz(p.brightness_hz)}</dd></div>
      <div><dt>Bark length</dt><dd>${p.length_s.toFixed(2)} s</dd></div>
      <div><dt>Sounds</dt><dd>${SIZE_SOUND[p.sounds_like]}</dd></div>
    </dl>
    <p class="small muted" style="margin:0">From ${p.barks} confirmed bark${p.barks === 1 ? "" : "s"}.${sep ? ` Told apart from your other dogs ${Math.round(sep.accuracy * 100)}% of the time${sep.confused_with && sep.accuracy < 0.9 ? `, sometimes mixed up with ${esc(S.dogs[sep.confused_with]?.name || "another dog")}` : ""}.` : ""}${mismatch}</p>
  </div>`;
}

// --------------------------------------------------------------- settings
const SETTINGS_SECTIONS = [
  ["detection", "Detection"], ["storage", "Storage"], ["code", "Code parameters"], ["ordinance", "Ordinance and contacts"],
  ["pictures", "Live band"], ["notify", "Alerts and dog matching"], ["system", "System"],
];
async function renderSettings() {
  const [st, ord, media] = await Promise.all([api("/api/settings"), api("/api/ordinance"), api("/api/media")]);
  if (route.name !== "settings") return;
  view.innerHTML = `
    <section><h1>Settings</h1><p class="sub">Changes take effect right away and are kept on this Pi.</p></section>
    <nav class="subnav" aria-label="Settings sections">${SETTINGS_SECTIONS.map(([id, t]) => `<a href="#/settings/${id}">${t}</a>`).join("")}</nav>
    ${SETTINGS_SECTIONS.map(([id]) => `<div class="settings-sec" id="sec-${id}"></div>`).join("")}
    <footer class="version">Barktracker ${esc(st.build.version)}${st.build.commit ? `, build ${esc(st.build.commit)}${st.build.commit_date ? ` from ${esc(st.build.commit_date)}` : ""}` : ""}</footer>`;
  drawStorage(st); drawDetection(st); drawCode(ord); drawOrdinance(ord); drawPictures({ ...media, barkWords: st.values["ui.bark_words"] !== false, dayNap: st.values["ui.day_nap"] !== false, dayNapBelow: st.values["ui.day_nap_below_dbfs"] }); drawNotify(st); drawSystem(st);
  if (route.arg) $(`#sec-${route.arg}`)?.scrollIntoView();
}

// form helpers ------------------------------------------------------------
const fieldNum = (key, label, val, { unit = "", step = 1, min = "", max = "", help = "" } = {}) =>
  `<label>${label}<span class="unit"><input type="number" name="${key}" value="${val ?? ""}" step="${step}" min="${min}" max="${max}" required>${unit}</span>${help ? `<span class="help">${help}</span>` : ""}</label>`;
const fieldText = (key, label, val, { type = "text", help = "", placeholder = "" } = {}) =>
  `<label>${label}<input type="${type}" name="${key}" value="${esc(val ?? "")}" placeholder="${esc(placeholder)}">${help ? `<span class="help">${help}</span>` : ""}</label>`;
const fieldCheck = (key, label, val, help = "") =>
  `<label class="check"><input type="checkbox" name="${key}" ${val ? "checked" : ""}> ${label}${help ? `<span class="help">${help}</span>` : ""}</label>`;

function settingsForm(id, title, intro, fieldsHtml, onSave, extra = "") {
  const el = $(`#sec-${id}`);
  el.innerHTML = `<form class="panel form" id="f-${id}">
      <h2>${title}</h2>${intro ? `<p class="small muted intro">${intro}</p>` : ""}
      ${fieldsHtml}
      <div class="row spread"><button class="btn primary" type="submit">Save ${title.toLowerCase()}</button>${extra}</div>
    </form>`;
  $(`#f-${id}`).onsubmit = async (ev) => {
    ev.preventDefault();
    const f = ev.target, vals = {};
    for (const inp of f.querySelectorAll("input[name], select[name], textarea[name]")) {
      vals[inp.name] = inp.type === "checkbox" ? inp.checked : inp.value;
    }
    try { await onSave(vals, f); } catch (e) { toast(e.message); }
  };
  return el;
}
async function saveApp(values, msg) {
  const st = await api("/api/settings", { method: "PUT", body: { values } });
  toast(msg); drawStorage(st); return st;
}

function drawStorage(st) {
  const d = st.disk, v = st.values;
  const other = Math.max(0, d.used_mb - d.clips_mb - d.db_mb);
  const pct = (x) => `${Math.max(0, (100 * x) / d.total_mb).toFixed(2)}%`;
  const capPct = d.cap_mb ? Math.min(100, (100 * d.clips_mb) / d.cap_mb) : 0;
  const gb = (mb) => mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.round(mb)} MB`;
  const est = d.per_day_mb > 0
    ? `Recent barking adds about ${gb(d.per_day_mb)} of audio a day. ${d.audio_days_capacity ? `The cap holds roughly ${Math.round(d.audio_days_capacity)} days of clips` : ""}${d.audio_days_capacity && v["clips.retention_days"] ? `, and clips older than ${v["clips.retention_days"]} days are deleted anyway.` : "."}`
    : "No recent clips yet, so there's no daily estimate.";
  const capWarn = d.audio_days_capacity && v["clips.retention_days"] && d.audio_days_capacity < v["clips.retention_days"]
    ? `<div class="notice">At this rate the disk cap fills before ${v["clips.retention_days"]} days, so the oldest clips will be removed early. Raise the cap to keep the full ${v["clips.retention_days"]} days.</div>` : "";
  settingsForm("storage", "Storage", "", `
    <div class="disk">
      <div class="diskbar" role="img" aria-label="Disk usage: ${gb(d.clips_mb)} clips, ${gb(other + d.db_mb)} system and other, ${gb(d.free_mb)} free">
        <span class="seg clips" style="width:${pct(d.clips_mb)}"></span><span class="seg db" style="width:${pct(d.db_mb)}"></span><span class="seg other" style="width:${pct(other)}"></span>
      </div>
      <div class="legend"><span style="--c:var(--ink)">Clips ${gb(d.clips_mb)} (${d.clip_count})</span><span style="--c:var(--muted)">Log database ${gb(d.db_mb)}</span><span style="--c:var(--unknown)">System and other ${gb(other)}</span><span style="--c:var(--faint)">Free ${gb(d.free_mb)} of ${gb(d.total_mb)}</span></div>
      ${d.cap_mb ? `<div class="capline"><span>Clips are using ${Math.round(capPct)}% of the ${gb(d.cap_mb)} cap</span><div class="meter wide" style="--c:var(--ink)"><b style="width:${capPct}%"></b></div></div>` : ""}
      <p class="small muted" style="margin:0">${est}${d.oldest_clip_ts ? ` The oldest clip is from ${fmtDay(d.oldest_clip_ts)}.` : ""}</p>
      ${capWarn}
    </div>
    <div class="grid3">
      ${fieldNum("clips.retention_days", "Keep clip audio for", v["clips.retention_days"], { unit: "days", min: 0, help: "0 keeps clips until the disk cap is reached. The log itself is never deleted." })}
      ${fieldNum("clips.max_disk_mb", "Disk cap for clips", Math.round(v["clips.max_disk_mb"] / 1024 * 10) / 10, { unit: "GB", step: 0.5, min: 0, help: "Oldest clips are removed above this." })}
      ${fieldNum("clips.max_clip_seconds", "Longest single clip", Math.round(v["clips.max_clip_seconds"] / 60), { unit: "minutes", min: 1, help: "Logging continues past this; the recording stops growing." })}
      ${fieldNum("clips.preroll", "Audio kept before barking starts", v["clips.preroll"], { unit: "seconds", min: 0, max: 10, step: 0.5 })}
      ${fieldNum("clips.postroll", "Audio kept after barking stops", v["clips.postroll"], { unit: "seconds", min: 0, max: 30, step: 0.5 })}
    </div>`, async (vals) => {
      vals["clips.max_disk_mb"] = Math.round(Number(vals["clips.max_disk_mb"]) * 1024);
      vals["clips.max_clip_seconds"] = Number(vals["clips.max_clip_seconds"]) * 60;
      await saveApp(vals, "Storage settings saved");
    });
}

function drawDetection(st) {
  const v = st.values;
  settingsForm("detection", "Detection", "How sensitive the Pi is. Watch the live band above while you adjust: if it misses barks, lower the bark confidence or the loudness trigger; if it logs things that aren't barks, raise them, or mark those clips “Not a bark”. Changes apply as soon as you save.", `
    <label>Bark confidence needed <span class="unit"><input type="range" name="detection.bark_threshold" min="0.05" max="0.95" step="0.05" value="${v["detection.bark_threshold"]}" oninput="this.nextElementSibling.textContent=Math.round(this.value*100)+'%'"><output>${Math.round(v["detection.bark_threshold"] * 100)}%</output></span><span class="help">How sure the sound model must be. Default 35%.</span></label>
    <div class="grid3">
      ${fieldNum("detection.trigger_db", "Loudness trigger", v["detection.trigger_db"], { unit: "dB above background", min: 3, max: 30, help: "A sound must be this much louder than the background noise to be checked." })}
      ${fieldNum("detection.min_level_dbfs", "Ignore sounds quieter than", v["detection.min_level_dbfs"], { unit: "dBFS", min: -90, max: -20 })}
      ${fieldNum("audio.gain_db", "Microphone boost", v["audio.gain_db"], { unit: "dB", min: -12, max: 30, help: "Webcams adjust their own gain; leave at 0 unless barks look quiet." })}
      ${fieldNum("detection.episode_gap", "Quiet time that ends an episode", v["detection.episode_gap"], { unit: "seconds", min: 1, max: 120, step: 0.5 })}
      ${fieldNum("detection.min_episode_barks", "Smallest episode to keep", v["detection.min_episode_barks"], { unit: "barks", min: 1, max: 50, help: "2 skips single woofs." })}
    </div>
    ${fieldCheck("detection.suppress_learned_false_positives", "Drop sounds that match clips marked “Not a bark”", v["detection.suppress_learned_false_positives"])}`,
    (vals) => saveApp(vals, "Detection settings saved"),
    `<button type="button" class="btn" id="resetApp">Restore config.yaml values</button>`);
  $("#resetApp").onclick = async () => {
    if (!confirm("Undo every dashboard change to storage, detection, alerts and matching, and go back to config.yaml?")) return;
    const st2 = await api("/api/settings", { method: "PUT", body: { reset: true } });
    toast("Restored config.yaml values"); drawStorage(st2); drawDetection(st2); drawNotify(st2);
  };
}

function drawNotify(st) {
  const v = st.values;
  settingsForm("notify", "Alerts and dog matching", "", `
    <div class="two">
      ${fieldText("notify.webhook_url", "Alert address (webhook)", v["notify.webhook_url"], { type: "url", placeholder: "https://ntfy.sh/your-topic", help: "Gets a message when an episode ends. Works with ntfy, Home Assistant and similar." })}
      ${fieldNum("notify.min_duration", "Only alert for episodes longer than", v["notify.min_duration"], { unit: "seconds", min: 0 })}
      ${fieldNum("identify.min_labels_per_dog", "Labelled barks needed before matching a dog", v["identify.min_labels_per_dog"], { unit: "barks", min: 1, max: 100 })}
      <label>How sure a match must be <span class="unit"><input type="range" name="identify.min_confidence" min="0.3" max="0.95" step="0.05" value="${v["identify.min_confidence"]}" oninput="this.nextElementSibling.textContent=Math.round(this.value*100)+'%'"><output>${Math.round(v["identify.min_confidence"] * 100)}%</output></span><span class="help">Less sure matches stay “Unidentified”.</span></label>
    </div>`, (vals) => saveApp(vals, "Alert and matching settings saved"));
}

function drawCode(r) {
  const th = r.ordinance.thresholds;
  const el = settingsForm("code", "Code parameters", `${r.ordinance.preset === "burnsville_mn" ? "Burnsville's code says “habitually or frequently” without numbers, so these are your documentation criteria, not legal limits." : "Match these to your ordinance, or to what you consider reportable."} They decide which incidents reports and the overview highlight.`, `
    <div class="grid3">
      ${fieldNum("sustained_minutes", "Sustained barking", th.sustained_minutes, { unit: "minutes or longer", min: 0 })}
      ${fieldNum("max_pause_seconds", "Break allowed within one incident", th.max_pause_seconds, { unit: "seconds", min: 0 })}
      ${fieldNum("daily_minutes", "Frequent day", th.daily_minutes, { unit: "minutes in a day", min: 0 })}
      <label>Quiet hours start<input type="time" name="quiet_start" value="${th.quiet_start}" required></label>
      <label>Quiet hours end<input type="time" name="quiet_end" value="${th.quiet_end}" required></label>
      ${fieldNum("quiet_minutes", "Flag in quiet hours after", th.quiet_minutes, { unit: "minutes", min: 0 })}
      ${fieldNum("habitual_days", "Habitual pattern", th.habitual_days, { unit: "days meeting a criterion", min: 0 })}
      ${fieldNum("habitual_window_days", "…within", th.habitual_window_days, { unit: "days", min: 1 })}
    </div>
    <ul class="criteria">${Object.entries(r.criteria).map(([k, v]) => `<li><strong>${FLAG_NAMES[k]}</strong>: ${esc(v)}</li>`).join("")}</ul>`,
    async (vals) => {
      const res = await api("/api/ordinance", { method: "PUT", body: { thresholds: vals } });
      toast("Code parameters saved"); drawCode(res);
    });
  return el;
}

function drawOrdinance(r) {
  const o = r.ordinance;
  settingsForm("ordinance", "Ordinance and contacts", `The rule your complaint reports cite. Defaults are for ${esc(o.jurisdiction)}.${o.source_url ? ` <a href="${esc(o.source_url)}" target="_blank" rel="noopener">Read the code on the city's site</a>; check it before filing, since codes change.` : ""}`, `
    <div class="row"><label class="inline">Preset <select id="preset">${r.presets.map((p) => `<option value="${p.id}" ${p.id === o.preset ? "selected" : ""}>${esc(p.label)}</option>`).join("")}<option value="custom" ${r.presets.some((p) => p.id === o.preset) ? "" : "selected"}>Custom</option></select></label></div>
    ${fieldText("jurisdiction", "Jurisdiction", o.jurisdiction)}
    ${fieldText("code_section", "Code section", o.code_section)}
    <label>What the rule says (in plain words)<textarea name="rule_text" rows="3">${esc(o.rule_text)}</textarea></label>
    <label>Notes<textarea name="rule_note" rows="3">${esc(o.rule_note)}</textarea></label>
    <div class="two">${fieldText("source_url", "Source link", o.source_url, { type: "url" })}${fieldText("penalty", "Penalty", o.penalty)}</div>
    <label>Where to report<textarea name="contacts" rows="4">${esc(o.contacts.map((c) => `${c.name} | ${c.phone}`).join("\n"))}</textarea><span class="help">One per line: name, a vertical bar, then phone or web address. Printed at the end of reports.</span></label>
    <h3>Your details</h3>
    <p class="small muted intro">Printed on reports as the person submitting the log. Stored only on this Pi.</p>
    <div class="two">${["name", "address", "phone", "email"].map((k) => fieldText(`reporter.${k}`, k[0].toUpperCase() + k.slice(1), r.reporter[k], { type: k === "email" ? "email" : "text" })).join("")}</div>`,
    async (vals) => {
      const body = {};
      for (const k of ["jurisdiction", "code_section", "rule_text", "rule_note", "source_url", "penalty"]) body[k] = vals[k];
      body.contacts = String(vals.contacts).split("\n").map((l) => l.trim()).filter(Boolean).map((l) => {
        const [name, ...rest] = l.split("|"); return { name: name.trim(), phone: rest.join("|").trim() };
      });
      const preset = $("#preset").value;
      body.preset = preset === "custom" || ["jurisdiction", "code_section", "rule_text"].some((k) => body[k] !== o[k]) ? "custom" : preset;
      const reporter = Object.fromEntries(["name", "address", "phone", "email"].map((k) => [k, vals[`reporter.${k}`]]));
      await Promise.all([api("/api/ordinance", { method: "PUT", body }), api("/api/reporter", { method: "PUT", body: reporter })]);
      toast("Ordinance and your details saved");
      const fresh = await api("/api/ordinance"); drawOrdinance(fresh); drawCode(fresh);
    },
    `<button type="button" class="btn" id="resetOrd">Reset to preset</button>`);
  $("#resetOrd").onclick = async () => {
    const preset = $("#preset").value === "custom" ? "burnsville_mn" : $("#preset").value;
    if (!confirm("Replace the rule, code parameters and contacts with the preset? Your details are kept.")) return;
    await api("/api/ordinance", { method: "PUT", body: { reset: true, preset } }); toast("Reset to preset");
    const fresh = await api("/api/ordinance"); drawOrdinance(fresh); drawCode(fresh);
  };
}

function drawPictures(m) {
  const col = (kind, title, help) => {
    const items = m[kind], own = items.some((i) => i.own);
    return `<div class="piccol">
      <h3>${title}</h3><p class="small muted intro">${help}${own ? "" : " Using the built-in drawing until you add your own."}</p>
      <div class="thumbs">${items.map((i) => `<figure class="thumb">${i.video ? `<video src="${i.url}" muted loop autoplay playsinline></video>` : `<img src="${i.url}" alt="">`}
        <figcaption>${esc(i.name)}${i.own ? ` <button type="button" class="linkbtn" data-delpic="${i.folder}/${esc(i.name)}" aria-label="Remove ${esc(i.name)}">Remove</button>` : ""}</figcaption></figure>`).join("")}</div>
      <label class="btn upload">Add pictures<input type="file" accept="image/*,video/mp4,video/webm" multiple data-up="${kind}" hidden></label>
    </div>`;
  };
  $("#sec-pictures").innerHTML = `<div class="panel form">
    <h2>Live band</h2>
    <p class="small muted intro">The band at the top of every page shows one of three pictures. Photos, GIFs, SVGs and short MP4 or WebM loops all work; if a folder has several, one is picked at random. You can also copy files straight into <code>${esc(m.folder)}/quiet</code>, <code>/active</code> and <code>/bark</code> on the Pi.</p>
    <label class="check"><input type="checkbox" id="barkWords" ${m.barkWords ? "checked" : ""}> Show red BARK words when a bark is detected</label>
    <form class="daynap" id="dayNapForm">
      <label class="check"><input type="checkbox" name="ui.day_nap" ${m.dayNap ? "checked" : ""}> Show the napping dog in the daytime when it's calm</label>
      <label>Calm means background noise below<span class="unit"><input type="number" name="ui.day_nap_below_dbfs" value="${m.dayNapBelow}" min="-90" max="-10" step="1" required>dBFS</span>
        <span class="help" id="ambNow">Background noise right now: measuring…</span></label>
      <button class="btn" type="submit">Save</button>
    </form>
    <div class="three">
      ${col("quiet", "Napping", "Quiet hours, or a calm daytime (see above).")}
      ${col("active", "Sitting", "Daytime background noise, or louder sounds without barking.")}
      ${col("bark", "Barking", "While a bark is being detected.")}
    </div>
  </div>`;
  $("#dayNapForm").onsubmit = async (e) => {
    e.preventDefault();
    const f = e.target;
    try {
      await api("/api/settings", { method: "PUT", body: { values: { "ui.day_nap": f.elements["ui.day_nap"].checked, "ui.day_nap_below_dbfs": f.elements["ui.day_nap_below_dbfs"].value } } });
      toast("Daytime napping saved");
    } catch (err) { toast(err.message); }
  };
  clearInterval(S.ambTimer);
  S.ambTimer = setInterval(() => {
    const el = $("#ambNow"); if (!el) { clearInterval(S.ambTimer); return; }
    const l = Beat.latest();
    el.textContent = l.ambient_db != null ? `Background noise right now: ${Math.round(l.ambient_db)} dBFS` : "Background noise right now: not available (not listening)";
  }, 1000);
  $("#barkWords").onchange = async (e) => {
    try {
      await api("/api/settings", { method: "PUT", body: { values: { "ui.bark_words": e.target.checked } } });
      Beat.setPrefs({ barkWords: e.target.checked });
      toast(e.target.checked ? "BARK words on" : "BARK words off");
    } catch (err) { toast(err.message); e.target.checked = !e.target.checked; }
  };
  $("#sec-pictures").querySelectorAll("[data-up]").forEach((inp) => inp.addEventListener("change", async () => {
    let r = m;
    for (const f of inp.files) {
      try { r = await fetch(`/api/media/${inp.dataset.up}/${encodeURIComponent(f.name)}`, { method: "PUT", body: f }).then(async (x) => { if (!x.ok) throw new Error((await x.json()).detail); return x.json(); }); }
      catch (e) { toast(`${f.name}: ${e.message}`); }
    }
    toast("Pictures added"); drawPictures({ ...r, barkWords: $("#barkWords").checked, dayNap: $("#dayNapForm").elements["ui.day_nap"].checked, dayNapBelow: $("#dayNapForm").elements["ui.day_nap_below_dbfs"].value }); Beat.loadMedia();
  }));
  $("#sec-pictures").querySelectorAll("[data-delpic]").forEach((b) => b.addEventListener("click", async () => {
    const r = await api(`/api/media/${b.dataset.delpic.split("/").map(encodeURIComponent).join("/")}`, { method: "DELETE" });
    toast("Picture removed"); drawPictures({ ...r, barkWords: $("#barkWords").checked, dayNap: $("#dayNapForm").elements["ui.day_nap"].checked, dayNapBelow: $("#dayNapForm").elements["ui.day_nap_below_dbfs"].value }); Beat.loadMedia();
  }));
}

function drawSystem(st) {
  $("#sec-system").innerHTML = `<div class="panel form">
    <h2>System</h2>
    <div id="micPick"></div>
    <div id="lvl"></div>
    <p class="small muted intro">Live microphone level. Grey is the background noise the Pi has learned; sounds above the dashed line wake the bark detector.</p>
    <div class="two"><dl class="kv" id="detKv"></dl><dl class="kv" id="sysKv"></dl></div>
    <div class="row"><a class="btn" href="/api/export.csv">Download full log as CSV</a></div>
  </div>`;
  S.settingsInfo = st.info;
  renderSystemLive();
  drawMics(st.values["audio.device"]);
}

async function drawMics(configured) {
  const el = $("#micPick"); if (!el) return;
  let r;
  try { r = await api("/api/audio/devices"); } catch (e) { el.innerHTML = ""; return; }
  const devs = r.devices || [];
  const isAuto = !configured || configured === "auto";
  const opts = [`<option value="auto" ${isAuto ? "selected" : ""}>Automatic (first USB webcam or mic)</option>`]
    .concat(devs.map((d) => `<option value="${esc(d.short)}" ${!isAuto && d.short === configured ? "selected" : ""}>${esc(d.short)}${d.active ? " (in use)" : ""}</option>`))
    .concat(r.unlisted.map((n) => `<option value="${esc(n)}" ${!isAuto && n === configured ? "selected" : ""}>${esc(n)} (newly plugged in)</option>`));
  const usable = devs.length + r.unlisted.length;
  el.innerHTML = `
    <h3>Microphone</h3>
    ${r.error && !devs.length ? `<p class="small muted intro">Couldn't list microphones here (${esc(r.error)}).</p>` : `
    <ul class="miclist">${devs.map((d) => `<li><strong>${esc(d.short)}</strong> <span class="muted">${d.channels} ch, ${Math.round(d.samplerate / 1000)} kHz</span>${d.active ? ' <span class="pill">in use</span>' : ""}</li>`).join("")}${r.unlisted.map((n) => `<li><strong>${esc(n)}</strong> <span class="muted">plugged in after start; select it to switch</span></li>`).join("")}</ul>
    <div class="microw">
      <select id="micSel" ${usable < 2 && isAuto ? "disabled" : ""} aria-label="Microphone">${opts.join("")}</select>
      <button class="btn" id="micUse" type="button" ${usable < 2 && isAuto ? "disabled" : ""}>Use this microphone</button>
      <button class="btn" id="micRefresh" type="button">Refresh list</button>
    </div>
    <p class="small muted intro">${r.current ? `Listening on <strong>${esc(r.current)}</strong>.` : r.listening ? "Opening the microphone…" : "Not listening (started without audio)."}
      ${usable < 2 ? " Only one microphone is plugged in; plug in another to switch." : ` ${usable} microphones found. Switching takes about a second; an episode in progress is closed first.`}</p>`}`;
  const use = $("#micUse");
  if (use) use.onclick = async () => {
    const v = $("#micSel").value;
    try {
      await api("/api/settings", { method: "PUT", body: { values: { "audio.device": v } } });
      toast(v === "auto" ? "Switching to automatic microphone choice" : `Switching to ${v}`);
      setTimeout(() => drawMics(v), 1800);
    } catch (e) { toast(e.message); }
  };
  const ref = $("#micRefresh");
  if (ref) ref.onclick = () => drawMics(configured);
}
const FLAG_NAMES = { sustained: "Sustained", quiet_hours: "Quiet hours", frequent: "Frequent day", habitual: "Habitual pattern" };

// ----------------------------------------------------------------- report
function reportQuery() {
  const r = S.report;
  const q = new URLSearchParams({ from: r.from, to: r.to });
  if (r.subject.startsWith("dog:")) q.set("dog", r.subject.slice(4));
  if (r.subject.startsWith("addr:")) q.set("address", r.subject.slice(5));
  if (r.flagged) q.set("flagged", "true");
  if (r.confirmed) q.set("confirmed", "true");
  return q.toString();
}
async function renderReport() {
  const today = isoToday();
  S.report = { subject: "all", from: shiftIso(today, -29), to: today, flagged: false, confirmed: false, ...(S.report || {}) };
  const R = S.report;
  const [subj, rep] = await Promise.all([api("/api/subjects"), api(`/api/report?${reportQuery()}`)]).catch((e) => { toast(e.message); return []; });
  if (!rep || route.name !== "report") return;
  const opt = (v, label) => `<option value="${esc(v)}" ${R.subject === v ? "selected" : ""}>${esc(label)}</option>`;
  view.innerHTML = `
    <section class="no-print">
      <h1>Complaint report</h1>
      <p class="sub" style="max-width:64ch">A log of barking for one dog or one neighbor's household, ready to print or download for a complaint. Clips for every incident can be downloaded as evidence.</p>
    </section>
    <section class="panel no-print reportctl">
      <label>Report on<select id="rSubj">
        ${opt("all", "All barking")}
        ${subj.dogs.length ? `<optgroup label="Dog">${subj.dogs.map((d) => opt(`dog:${d.id}`, d.name + (d.address ? ` (${d.address})` : ""))).join("")}</optgroup>` : ""}
        ${subj.neighbors.length ? `<optgroup label="Neighbor (all dogs at an address)">${subj.neighbors.map((n) => opt(`addr:${n.address}`, n.address + (n.owner ? ` (${n.owner})` : ""))).join("")}</optgroup>` : ""}
      </select></label>
      <label>From<input type="date" id="rFrom" value="${R.from}" max="${today}"></label>
      <label>To<input type="date" id="rTo" value="${R.to}" max="${today}"></label>
      <div class="quick">
        <button class="btn" data-range="7">Last 7 days</button><button class="btn" data-range="30">Last 30 days</button><button class="btn" data-range="90">Last 90 days</button>
      </div>
      <label class="check"><input type="checkbox" id="rFlag" ${R.flagged ? "checked" : ""}> Only incidents meeting the ordinance criteria</label>
      <label class="check"><input type="checkbox" id="rConf" ${R.confirmed ? "checked" : ""} ${R.subject === "all" ? "disabled" : ""}> Only barks I've confirmed are this dog</label>
      <div class="actions">
        <button class="btn primary" id="rPrint">Print or save as PDF</button>
        <a class="btn" href="/api/report.csv?${reportQuery()}">Download CSV</a>
        <a class="btn" href="/api/report.csv?${reportQuery()}&kind=daily">Daily totals CSV</a>
        <a class="btn" href="/api/report/clips.zip?${reportQuery()}">Clips and CSV (.zip)</a>
      </div>
      ${rep.reporter.name ? "" : `<p class="small muted" style="margin:0;grid-column:1/-1">Add your name and contact details on the <a href="#/settings/ordinance">Settings</a> page so they print on the report.</p>`}
      ${R.subject !== "all" && rep.subject.kind === "dog" && !rep.subject.address ? `<p class="small muted" style="margin:0;grid-column:1/-1">Add this dog's owner and address on the <a href="#/dogs">Dogs</a> page so they print on the report.</p>` : ""}
    </section>
    ${reportDoc(rep)}`;
  const set = (k, v) => { S.report[k] = v; renderReport(); };
  $("#rSubj").onchange = (e) => { S.report.confirmed = S.report.confirmed && e.target.value !== "all"; set("subject", e.target.value); };
  $("#rFrom").onchange = (e) => set("from", e.target.value);
  $("#rTo").onchange = (e) => set("to", e.target.value);
  $("#rFlag").onchange = (e) => set("flagged", e.target.checked);
  $("#rConf").onchange = (e) => set("confirmed", e.target.checked);
  view.querySelectorAll("[data-range]").forEach((b) => b.onclick = () => { S.report.to = today; set("from", shiftIso(today, -(Number(b.dataset.range) - 1))); });
  $("#rPrint").onclick = () => window.print();
}

function fmtClock(s) { s = Math.round(s); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60; return h ? `${h}:${String(m).padStart(2, "0")}:${String(x).padStart(2, "0")}` : `${m}:${String(x).padStart(2, "0")}`; }
function shortDate(iso) { return new Date(iso + "T12:00:00Z").toLocaleDateString([], { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" }); }
function longDate(iso) { return new Date(iso + "T12:00:00Z").toLocaleDateString([], { weekday: "short", year: "numeric", month: "short", day: "numeric", timeZone: "UTC" }); }

function reportDoc(rep) {
  const o = rep.ordinance, sm = rep.summary, sub = rep.subject, rp = rep.reporter;
  const subjectLine = sub.kind === "all" ? "All dogs heard at the recording location"
    : sub.kind === "dog" ? [sub.title, sub.owner && `owned by ${sub.owner}`, sub.address].filter(Boolean).join(", ")
    : `Dogs kept at ${sub.title}${sub.owner ? ` (${sub.owner})` : ""}`;
  const dogsLine = sub.dogs?.length ? sub.dogs.map((d) => d.name + (d.description ? ` (${d.description})` : "")).join("; ") : "";
  const flagTxt = (fl) => fl.map((f) => rep.flag_labels[f] || f).join(", ");
  const activeDays = rep.days.filter((d) => d.incidents);
  const generated = new Date(rep.generated_ts * 1000).toLocaleString([], { dateStyle: "long", timeStyle: "short", ...timeOpts() });
  const clockOk = S.status?.clock?.ntp_synced;
  return `
  <article class="doc">
    <header class="doc-head">
      <h1>Barking complaint log</h1>
      <dl class="doc-meta">
        <div><dt>Subject</dt><dd>${esc(subjectLine)}${dogsLine && sub.kind !== "dog" ? `<br><span class="muted">${esc(dogsLine)}</span>` : sub.dogs?.[0]?.description ? `<br><span class="muted">${esc(sub.dogs[0].description)}</span>` : ""}</dd></div>
        <div><dt>Period</dt><dd>${longDate(rep.period.from)} to ${longDate(rep.period.to)}</dd></div>
        <div><dt>Prepared by</dt><dd>${rp.name ? esc([rp.name, rp.address].filter(Boolean).join(", ")) + (rp.phone || rp.email ? `<br>${esc([rp.phone, rp.email].filter(Boolean).join(", "))}` : "") : `<span class="blank">Name and address</span>`}</dd></div>
        <div><dt>Generated</dt><dd>${esc(generated)} (times in ${esc(rep.timezone)})</dd></div>
      </dl>
    </header>

    <section class="doc-sec">
      <h2>Ordinance</h2>
      <p><strong>${esc(o.code_section)}</strong>, ${esc(o.jurisdiction)}</p>
      <p>${esc(o.rule_text)}</p>
      ${o.rule_note ? `<p class="muted">${esc(o.rule_note)}</p>` : ""}
      ${o.source_url ? `<p class="small muted">Source: ${esc(o.source_url)}</p>` : ""}
    </section>

    <section class="doc-sec">
      <h2>Summary</h2>
      ${sm.incidents ? `<p class="lead">Barking was recorded on <strong>${sm.days_with_barking} of ${sm.days_in_period} days</strong>: ${sm.incidents} incident${sm.incidents === 1 ? "" : "s"} totalling <strong>${fmtDur(sm.total_seconds)}</strong>${sub.kind !== "all" ? ` and ${sm.barks} barks` : ""}. ${sm.flag_counts.sustained} incident${sm.flag_counts.sustained === 1 ? " was" : "s were"} sustained and ${sm.flag_counts.quiet_hours} ran into quiet hours. ${sm.days_flagged.frequent ? `${sm.days_flagged.frequent} day${sm.days_flagged.frequent === 1 ? " was" : "s were"} frequent by the daily total. ` : ""}${sm.days_flagged.habitual ? `${sm.days_flagged.habitual} day${sm.days_flagged.habitual === 1 ? "" : "s"} formed a habitual pattern.` : ""} The longest lasted ${fmtDur(sm.longest.duration)}, on ${fmtDay(sm.longest.start_ts)} at ${fmtTime(sm.longest.start_ts)}.</p>`
        : `<p class="lead">No barking${rep.flagged_only ? " meeting the criteria" : ""} was recorded for this subject in the period.</p>`}
      <table class="crit">
        <thead><tr><th>Criterion</th><th>Definition used</th><th class="num">Incidents</th><th class="num">Days</th></tr></thead>
        <tbody>${Object.entries(rep.criteria).map(([k, v]) => `<tr><td>${esc(rep.flag_labels[k])}</td><td>${esc(v)}</td><td class="num">${k in sm.flag_counts ? sm.flag_counts[k] : "–"}</td><td class="num">${k in sm.flag_counts ? new Set(rep.incidents.filter((i) => i.flags.includes(k)).map((i) => i.date)).size : sm.days_flagged[k]}</td></tr>`).join("")}</tbody>
      </table>
    </section>

    ${activeDays.length ? `<section class="doc-sec">
      <h2>Daily totals</h2>
      <table><thead><tr><th>Date</th><th class="num">Incidents</th><th class="num">Total time</th>${sub.kind !== "all" ? `<th class="num">Barks</th>` : ""}<th class="num">Quiet hours</th><th>Criteria met</th></tr></thead>
      <tbody>${activeDays.map((d) => `<tr><td class="nw">${longDate(d.date)}</td><td class="num">${d.incidents}</td><td class="num">${fmtClock(d.seconds)}</td>${sub.kind !== "all" ? `<td class="num">${d.barks}</td>` : ""}<td class="num">${d.quiet_seconds ? fmtClock(d.quiet_seconds) : "–"}</td><td>${esc(flagTxt([...new Set([...d.flags, ...rep.incidents.filter((i) => i.date === d.date).flatMap((i) => i.flags)])]))}</td></tr>`).join("")}</tbody></table>
    </section>` : ""}

    ${rep.incidents.length ? `<section class="doc-sec">
      <h2>Incident log</h2>
      <table class="log"><thead><tr><th class="num">#</th><th>Date</th><th>Start</th><th>End</th><th class="num">Length</th><th class="num">Barks</th>${sub.kind === "all" ? `<th>Dogs heard</th>` : `<th>Identified</th>`}<th>Criteria met</th><th>Notes</th></tr></thead>
      <tbody>${rep.incidents.map((i) => `<tr class="${i.flags.length ? "hit" : ""}"><td class="num">${i.number}</td><td class="nw">${shortDate(i.date)}</td><td class="nw">${esc(i.start)}</td><td class="nw">${esc(i.end)}</td><td class="num">${fmtClock(i.duration)}</td><td class="num">${sub.kind === "all" ? i.barks : i.subject_barks}</td><td>${sub.kind === "all" ? esc(Object.entries(i.dogs).map(([k, v]) => `${k} ×${v}`).join(", ")) : ({ confirmed: "Confirmed", automatic: "Automatic", mixed: "Partly confirmed" }[i.identified] || "")}</td><td>${esc(flagTxt(i.flags))}</td><td>${esc(i.notes)}</td></tr>`).join("")}</tbody></table>
      <p class="small muted">An incident is continuous barking with no break longer than ${o.thresholds.max_pause_seconds} seconds. ${sub.kind !== "all" ? "“Confirmed” means a person listened to the recording and identified the dog; “Automatic” means it was matched by voice to barks that were confirmed earlier." : ""}</p>
    </section>` : ""}

    <section class="doc-sec">
      <h2>How this was recorded</h2>
      <p>Sound was recorded continuously by a Raspberry Pi 4 with a USB microphone${S.status?.audio_device ? ` (${esc(S.status.audio_device.split(":")[0])})` : ""} at the preparer's home. Each loud sound was checked by an audio classification model trained to recognize dog barking, and barks less than ${S.status?.settings?.episode_gap ?? 8} seconds apart were grouped. Times come from the device clock${clockOk === true ? ", which is synchronized to internet time" : clockOk === false ? " (not confirmed synchronized at the time of this report)" : ""}. An audio clip was saved for each recorded episode and can be provided on request.</p>
    </section>

    ${o.contacts?.length ? `<section class="doc-sec"><h2>Where to report</h2><ul class="contacts">${o.contacts.map((c) => `<li>${esc(c.name)}${c.phone ? `: ${esc(c.phone)}` : ""}</li>`).join("")}</ul></section>` : ""}

    <section class="doc-sec sign">
      <p>I declare that this log was produced from recordings made at my residence and is accurate to the best of my knowledge.</p>
      <div class="sigline"><span>Signature</span><span>Date</span></div>
    </section>
  </article>`;
}

// ------------------------------------------------------------ system live
function renderSystemLive() {
  const s = S.status; if (!s || !$("#lvl")) return;
  const d = s.detector;
  if (!d) { $("#lvl").innerHTML = `<div class="notice ${s.error ? "err" : ""}">${s.error ? esc(s.error) : "Not listening: started without audio."}</div>`; }
  else {
    const hist = d.history, W = 1000, H = 140, lo = -90, hi = 0;
    const y = (v) => H - ((Math.min(hi, Math.max(lo, v)) - lo) / (hi - lo)) * H;
    const n = Math.max(hist.length - 1, 1);
    const pts = (i) => hist.map((p, j) => `${(j / n) * W},${y(p[i])}`).join(" ");
    let g = "";
    for (let v = -80; v < 0; v += 20) g += `<line x1="0" x2="${W}" y1="${y(v)}" y2="${y(v)}" stroke="var(--faint)"/><text x="4" y="${y(v) - 3}">${v} dB</text>`;
    g += `<polyline points="${pts(2)}" fill="none" stroke="var(--unknown)" stroke-width="2"/>`;
    g += `<line x1="0" x2="${W}" y1="${y(d.trigger_db)}" y2="${y(d.trigger_db)}" stroke="var(--ink)" stroke-dasharray="5 4"/>`;
    g += `<polyline points="${pts(1)}" fill="none" stroke="${d.barking_now ? "var(--bark)" : "var(--ink)"}" stroke-width="1.5"/>`;
    $("#lvl").innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Input level over the last minute">${g}</svg>`;
  }
  const det = $("#detKv");
  if (det) det.innerHTML = d ? `
      <dt>Detector</dt><dd>${d.classifier === "yamnet" ? "YAMNet sound model" : "Basic (no model installed)"}</dd>
      <dt>Microphone</dt><dd>${esc(s.audio_device || "Default input")}</dd>
      <dt>Level now</dt><dd>${d.level_db} dBFS (background ${d.floor_db})</dd>
      <dt>Last check</dt><dd>${Math.round(d.last_prob * 100)}% bark${d.last_detail?.top ? `, heard “${esc(d.last_detail.top)}”` : ""} in ${d.classify_ms} ms</dd>
      <dt>Barks since start</dt><dd>${d.stats.barks || 0} (${d.stats.suppressed || 0} ignored as known false alarms)</dd>
      <dt>Dog matching</dt><dd>${s.identifier.ready ? "On" : `Waiting for ${s.identifier.min_labels}+ labels per dog`}</dd>` : `<dt>Status</dt><dd>Not listening</dd>`;
  const sys = $("#sysKv");
  if (sys) sys.innerHTML = `
      <dt>Clock</dt><dd>${s.clock?.ntp_synced === true ? "Synchronized to internet time" : s.clock?.ntp_synced === false ? "Not synchronized: timestamps may drift" : "Unknown"}</dd>
      <dt>Time zone</dt><dd>${esc(s.settings.timezone)}</dd>
      <dt>Running for</dt><dd>${d ? fmtDur(d.uptime_s) : "–"}</dd>
      <dt>Episodes logged</dt><dd>${s.counts.episodes} (${s.counts.unreviewed || 0} not reviewed)</dd>
      <dt>Data folder</dt><dd><code>${esc(S.settingsInfo?.data_dir || "")}</code></dd>`;
}

// -------------------------------------------------------------- heartbeat
// The band at the top of every page. One picture per mode (nap: quiet hours and calm,
// sit: daytime or noisy without barking, bark: bark detected), a live EQ of the mic with
// falling bars and peak caps (monotone normally, full color while barking), and BARK splashes.
const Beat = (() => {
  const band = $("#beat"), mediaEl = $("#beatMedia"), splash = $("#splash");
  const title = $("#beatTitle"), sub = $("#beatSub"), canvas = $("#beatEq");
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)");
  const KIND = { nap: "quiet", sit: "active", bark: "bark" };
  let media = { quiet: [], active: [], bark: [] }, shown = {}, lastBarks = null, prevState = "connecting";
  let live = { state: "connecting", mode: "nap", bands: [] }, failures = 0, es = null, pollTimer = null;
  const WORDS = ["BARK", "BARK!", "Bark!", "BARK!!", "bark", "BARK"];

  // ---- pictures
  async function loadMedia() {
    try { media = await api("/api/media"); } catch (_) { return; }
    shown = {}; mediaEl.dataset.kind = ""; show(KIND[live.mode] || "quiet");
  }
  function pick(kind) {
    const list = media[kind] || [];
    if (list.length < 2) return list[0] || null;
    let item; do { item = list[Math.floor(Math.random() * list.length)]; } while (item.url === shown[kind]?.url);
    return item;
  }
  function show(kind) {
    if (mediaEl.dataset.kind === kind) return;
    const item = pick(kind); if (!item) return;
    shown[kind] = item; mediaEl.dataset.kind = kind;
    const el = document.createElement(item.video ? "video" : "img");
    if (item.video) Object.assign(el, { muted: true, loop: true, autoplay: true, playsInline: true });
    else el.alt = "";
    el.src = item.url;
    const old = [...mediaEl.children];
    const snap = kind === "bark";                     // barking cuts in instantly; calm fades back
    if (snap) el.classList.add("snap");
    const reveal = () => {
      if (snap) { el.classList.add("on"); old.forEach((o) => o.remove()); return; }
      requestAnimationFrame(() => el.classList.add("on")); setTimeout(() => old.forEach((o) => o.remove()), 400);
    };
    el.addEventListener(item.video ? "loadeddata" : "load", reveal, { once: true });
    el.addEventListener("error", reveal, { once: true });
    mediaEl.appendChild(el);
  }

  // ---- BARK splashes
  function spawn(n) {
    if (!prefs.barkWords) return;
    const count = Math.min(5, Math.max(1, n)) + (Math.random() < 0.5 ? 1 : 0);
    const big = document.body.classList.contains("kiosk") ? 2.6 : 1;
    for (let i = 0; i < (reduce.matches ? 1 : count); i++) {
      const w = document.createElement("span");
      w.className = "splash-word";
      w.textContent = WORDS[Math.floor(Math.random() * WORDS.length)];
      const size = (14 + Math.random() ** 1.6 * 44) * big;
      w.style.cssText = `--fs:${size.toFixed(0)}px;--rot:${(Math.random() * 56 - 28).toFixed(1)}deg;` +
        `--dur:${(1.3 + Math.random() * 1.1).toFixed(2)}s;--delay:${(i * 0.09).toFixed(2)}s;` +
        `left:${(4 + Math.random() * 88).toFixed(1)}%;top:${(12 + Math.random() * 72).toFixed(1)}%`;
      w.addEventListener("animationend", () => w.remove());
      splash.appendChild(w);
      setTimeout(() => w.remove(), 4000);
    }
  }

  // ---- state from the server
  function apply(l) {
    failures = 0; live = l;
    const amb = l.ambient_db ?? l.level_db;
    const narrow = window.matchMedia("(max-width: 640px)").matches;
    const bg = amb != null ? `${narrow ? "ambient" : "background noise"} ${Math.round(amb)} dBFS` : "";
    if (l.error && l.state !== "barking") {
      band.dataset.state = "error"; title.textContent = "Microphone problem"; sub.textContent = l.error; show("quiet");
    } else if (l.state === "off") {
      band.dataset.state = "off"; title.textContent = "Not listening"; sub.textContent = "Started without a microphone"; show("quiet");
    } else if (l.mode === "bark") {
      band.dataset.state = "barking"; title.textContent = "Barking";
      sub.textContent = l.episode_barks ? `${l.episode_barks} bark${l.episode_barks === 1 ? "" : "s"} so far` : "just now";
      show("bark");
    } else if (l.mode === "sit") {
      band.dataset.state = "awake"; title.textContent = l.noisy ? "Noisy" : "Listening";
      sub.textContent = l.noisy ? `Sound but no barking, ${bg}` : `Daytime, ${bg}`;
      show("active");
    } else {
      band.dataset.state = "idle"; title.textContent = "Quiet";
      sub.textContent = l.quiet_hours ? `Quiet hours, ${bg}` : `Calm daytime, ${bg}`;
      show("quiet");
    }
    const started = l.state === "barking" && prevState !== "barking";
    if (lastBarks !== null && l.barks > lastBarks) spawn(l.barks - lastBarks + (started ? 2 : 0));
    else if (started) spawn(3);
    lastBarks = l.barks; prevState = l.state;
  }
  function lost() {
    if (++failures < 3) return;
    live = { ...live, state: "error", bands: [] };
    band.dataset.state = "error"; title.textContent = "Can't reach the Pi"; sub.textContent = "Retrying…";
  }
  function connect() {
    if (document.hidden) return;
    if ("EventSource" in window) {
      es = new EventSource("/api/live/stream");
      es.onmessage = (e) => { try { apply(JSON.parse(e.data)); } catch (_) { /* ignore a bad frame */ } };
      es.onerror = () => { failures = Math.max(failures, 2); lost(); };
    } else {
      const poll = async () => { try { apply(await api("/api/live")); } catch (_) { lost(); } };
      poll(); pollTimer = setInterval(poll, 500);
    }
  }
  function disconnect() { es?.close(); es = null; clearInterval(pollTimer); pollTimer = null; }

  // ---- EQ: LED-segment bars with gravity and peak caps
  // Monotone: the site's ink normally, caution yellow while barking. In each column only the top
  // cell (the peak cap) is full brightness; the stack under it is a steady ~55%, so the caps read
  // as a line of sound.
  const ctx = canvas.getContext("2d");
  let bars = [], last = performance.now();
  const round = (x, y, w, h, r) => {
    if (ctx.roundRect) { ctx.beginPath(); ctx.roundRect(x, y, w, h, r); ctx.fill(); } else ctx.fillRect(x, y, w, h);
  };
  function target(i, n) {
    const src = live.bands || [];
    if (!src.length || live.state === "off" || live.state === "error") return 0;
    if (n > src.length) {                              // more bars than bands: interpolate
      const pos = Math.max(0, Math.min(src.length - 1, ((i + 0.5) * src.length) / n - 0.5));
      const k = Math.floor(pos), u = pos - k;
      return src[k] * (1 - u) + (src[Math.min(src.length - 1, k + 1)] ?? 0) * u;
    }
    const a = Math.floor((i * src.length) / n), b = Math.max(a + 1, Math.floor(((i + 1) * src.length) / n));
    let m = 0; for (let k = a; k < b; k++) m = Math.max(m, src[k]); return m;
  }
  // Bands are log-spaced from 90 Hz to 7.6 kHz across the full width (see features.eq_bands).
  const HZ_LO = 90, HZ_HI = 7600;
  const HZ_MARKS = [[100, "100"], [250, "250"], [500, "500"], [1000, "1k"], [2000, "2k"], [4000, "4k"], [7000, "7k Hz"]];
  function drawHz(W, top, h, css) {
    const marks = W < 260 ? HZ_MARKS.filter((m) => [250, 1000, 4000].includes(m[0]))
      : W < 420 ? HZ_MARKS.filter((m) => m[0] !== 100 && m[0] !== 7000) : HZ_MARKS;
    const fs = h > 15 ? 12 : 10;
    ctx.font = `500 ${fs}px ${css.fontFamily}`;
    ctx.fillStyle = css.getPropertyValue("--muted").trim();
    ctx.textBaseline = "bottom";
    const y = top + h;
    let prevRight = -Infinity;
    for (const [f, text] of marks) {
      const x = (W * Math.log(f / HZ_LO)) / Math.log(HZ_HI / HZ_LO);
      const tw = ctx.measureText(text).width;
      let lx = x - tw / 2;
      lx = Math.max(0, Math.min(W - tw, lx));
      if (lx < prevRight + 6) continue;          // skip a label that would collide
      ctx.textAlign = "left";
      ctx.fillText(text, lx, y);
      ctx.globalAlpha = 0.5;
      ctx.fillRect(Math.round(x) - 0.5, top + 1, 1, 3); // small tick under the bars
      ctx.globalAlpha = 1;
      prevRight = lx + tw;
    }
  }
  function frame(now) {
    const dpr = window.devicePixelRatio || 1, W = canvas.clientWidth, H = canvas.clientHeight;
    if (!W || !H) { if (!reduce.matches) requestAnimationFrame(frame); return; }
    if (canvas.width !== Math.round(W * dpr) || canvas.height !== Math.round(H * dpr)) {
      canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
    }
    const dt = Math.min(0.1, (now - last) / 1000); last = now;
    const n = W < 360 ? 14 : W < 640 ? 20 : W < 1100 ? 28 : 36;
    if (bars.length !== n) bars = Array.from({ length: n }, () => ({ h: 0, peak: 0, hold: 0, v: 0 }));
    // Hz labels sit in a row under the bars; its bottom lines up with the bottom of the picture
    const LBL = H > 200 ? 18 : H >= 34 ? (H >= 60 ? 14 : 12) : 0;
    const reflect = H > 200, area = reflect ? (H - LBL) * 0.76 : H - LBL;
    const gapY = area > 120 ? 4 : 3, S = Math.max(4, Math.min(16, Math.floor(area / (area > 120 ? 13 : 11))));
    const segH = (area - gapY * (S - 1)) / S, gapX = W > 600 ? 4 : 3, bw = (W - gapX * (n - 1)) / n;
    const r = Math.min(2.5, segH / 3, bw / 4);
    const barking = live.mode === "bark" && live.state !== "error";
    const css = getComputedStyle(band);
    const on = css.getPropertyValue(barking ? "--caution" : "--ink").trim();
    const off = barking ? on : css.getPropertyValue("--faint").trim(), offAlpha = barking ? 0.16 : 1;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    bars.forEach((bar, i) => {
      const t = target(i, n);
      if (reduce.matches) { bar.h = t; bar.peak = t; }
      else {
        bar.h = t > bar.h ? bar.h + (t - bar.h) * 0.7 : Math.max(t, bar.h - 1.3 * dt);
        if (bar.h >= bar.peak) { bar.peak = bar.h; bar.hold = 0.45; bar.v = 0; }
        else if (bar.hold > 0) bar.hold -= dt;
        else { bar.v += 2.2 * dt; bar.peak = Math.max(bar.h, bar.peak - bar.v * dt); }
      }
      const lit = Math.round(bar.h * S), x = i * (bw + gapX);
      const top = bar.peak > 0.04 || lit > 0 ? Math.min(S - 1, Math.max(lit - 1, Math.floor(bar.peak * S))) : -1;
      for (let j = 0; j < S; j++) {
        const y = area - (j + 1) * segH - j * gapY;
        let alpha, color;
        if (j === top) { color = on; alpha = 1; }
        else if (j < lit) { color = on; alpha = 0.55; }
        else { color = off; alpha = offAlpha; }
        ctx.globalAlpha = alpha; ctx.fillStyle = color;
        round(x, y, bw, segH, r);
        if (reflect && (j < lit || j === top)) {
          const ry = area + LBL + gapY * 2 + j * (segH + gapY);
          if (ry < H) { ctx.globalAlpha = alpha * 0.16 * (1 - j / S); round(x, ry, bw, segH, r); }
        }
      }
    });
    ctx.globalAlpha = 1;
    if (LBL) drawHz(W, area, LBL, css);
    if (!reduce.matches) requestAnimationFrame(frame);
  }

  // ---- display preferences (stored on the Pi, shared by every screen)
  let prefs = { barkWords: true };
  async function loadPrefs() {
    try { const st = await api("/api/settings"); prefs.barkWords = st.values["ui.bark_words"] !== false; } catch (_) { /* keep defaults */ }
  }
  function setPrefs(p) { Object.assign(prefs, p); if (!prefs.barkWords) splash.replaceChildren(); }

  function start() {
    loadPrefs(); loadMedia(); connect();
    if (reduce.matches) setInterval(() => frame(performance.now()), 250); else requestAnimationFrame(frame);
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) disconnect(); else { last = performance.now(); connect(); }
    });
  }
  return { start, loadMedia, spawn, setPrefs, latest: () => live };
})();

// ------------------------------------------------------------- kiosk view
function renderLive() {
  view.innerHTML = `<a class="kiosk-exit btn" href="#/">Back to dashboard</a>`;
}
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && route.name === "live") location.hash = "#/"; });

// -------------------------------------------------------------------- go
Beat.start();
pollStatus().finally(navigate);
setInterval(pollStatus, 2000);
