"use strict";
// Barklog dashboard — vanilla JS, no build step, works offline on the LAN.

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
  if (!e.length) return `<span class="tag">No barks left</span>`;
  return e.map(([k, n]) => `<span class="tag" style="--c:${dogColor(k)}"><i></i>${esc(dogName(k))} ×${n}</span>`).join("");
}
async function loadDogs() {
  const r = await api("/api/dogs");
  S.dogs = Object.fromEntries(r.dogs.map((d) => [String(d.id), d]));
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

// ------------------------------------------------------------ live pill
async function pollStatus() {
  try {
    const s = await api("/api/status");
    S.status = s;
    if (s.settings.timezone && s.settings.timezone !== "system") S.tz = s.settings.timezone;
    const el = $("#live"), d = s.detector;
    el.className = "live";
    if (s.error && (!d || d.stats.frames === undefined)) { el.classList.add("off"); el.lastChild.textContent = "Microphone problem"; }
    else if (!d) { el.lastChild.textContent = "Not listening"; }
    else if (d.barking_now) { el.classList.add("barking"); el.lastChild.textContent = `Barking now, ${d.current_episode.barks} bark${d.current_episode.barks === 1 ? "" : "s"}`; }
    else { el.classList.add("on"); el.lastChild.textContent = "Listening"; }
    if (route.name === "system") renderSystemLive();
    if (route.name === "overview" && d && S.lastEpisodes !== s.counts.episodes) {
      if (S.lastEpisodes !== undefined && S.overviewDate === isoToday()) renderOverview(true);
      S.lastEpisodes = s.counts.episodes;
    }
  } catch (_) {
    const el = $("#live"); el.className = "live off"; el.lastChild.textContent = "Can't reach the Pi";
  }
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
  ({ overview: renderOverview, clips: renderClips, clip: renderClip, dogs: renderDogs, system: renderSystem }[route.name] || renderOverview)();
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
  }
  const cw = chartWidths();
  const dogsSeen = new Set([...Object.keys(perDog)]);
  const legend = [...dogsSeen].map((k) => `<span style="--c:${dogColor(k)}">${esc(dogName(k))}</span>`).join("");

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
      <span class="n">${e.bark_count} bark${e.bark_count === 1 ? "" : "s"}</span>
      <span class="dogs">${dogTags(e.dogs)}</span>
      <span class="state ${e.reviewed ? "" : "new"}">${e.live ? "Recording" : e.reviewed ? "Reviewed" : "New"}</span>
    </a>`).join("")}</div>`;
}

// ------------------------------------------------------------------ clips
async function renderClips() {
  await loadDogs();
  const f = S.clipFilters;
  const qs = new URLSearchParams({ limit: 30, offset: f.offset });
  if (f.dog) qs.set("dog", f.dog);
  if (f.unreviewed) qs.set("unreviewed", "true");
  if (f.min) qs.set("min_duration", f.min);
  const r = await api(`/api/episodes?${qs}`);
  if (route.name !== "clips") return;
  const opts = Object.values(S.dogs).map((d) => `<option value="${d.id}" ${f.dog == d.id ? "selected" : ""}>${esc(d.name)}</option>`).join("");
  view.innerHTML = `
    <section>
      <h1 style="margin-bottom:16px">Clips</h1>
      <div class="filters">
        <select id="fDog" aria-label="Filter by dog"><option value="">All dogs</option>${opts}<option value="u" ${f.dog === "u" ? "selected" : ""}>Unidentified</option></select>
        <label class="row" style="gap:6px"><input type="checkbox" id="fNew" ${f.unreviewed ? "checked" : ""}> Not yet reviewed</label>
        <label class="row" style="gap:6px">At least <input type="number" id="fMin" min="0" step="5" value="${f.min || 0}" style="width:5em"> seconds</label>
        <span class="muted small" style="margin-left:auto">${r.total} clip${r.total === 1 ? "" : "s"}</span>
      </div>
      ${episodeList(r.items, "No clips match", "Try clearing a filter.")}
      <div class="pager">
        <button class="btn" id="pPrev" ${f.offset === 0 ? "disabled" : ""}>Newer</button>
        <span class="small muted">${r.total ? `${f.offset + 1}–${Math.min(f.offset + 30, r.total)}` : ""}</span>
        <button class="btn" id="pNext" ${f.offset + 30 >= r.total ? "disabled" : ""}>Older</button>
      </div>
    </section>`;
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
        <div><dt>Barking for</dt><dd>${fmtDur(ep.duration)}</dd></div>
        <div><dt>Barks</dt><dd>${ep.bark_count}</dd></div>
        <div><dt>Loudest</dt><dd>${ep.peak_db != null ? ep.peak_db.toFixed(0) + " dBFS" : "–"}</dd></div>
        <div><dt>Detector confidence</dt><dd>${ep.max_prob != null ? Math.round(ep.max_prob * 100) + "%" : "–"}</dd></div>
      </dl>
      ${ep.live ? `<div class="notice">Still recording. Refresh when the barking stops to hear the full clip.</div>` : ""}
      ${ep.has_clip ? `
        <div class="spec-wrap" id="spec"><canvas id="specCanvas" width="900" height="80"></canvas><div class="spec-marks" id="marks"></div><div class="playhead" id="ph" style="left:0"></div></div>
        <div class="spec-time"><span>0 s</span><span>Select a bark to hear it on its own and label it. Shift-select to pick several.</span><span>${clipLen.toFixed(1)} s</span></div>
        <audio id="aud" controls preload="auto" src="/api/episodes/${id}/audio"></audio>`
        : `<div class="notice">The audio for this episode was removed by the retention policy. The log entry is kept.</div>`}
    </section>
    <section class="panel labeler" id="labeler"></section>
    <section class="panel">
      <label for="note"><h2>Note</h2></label>
      <textarea id="note" rows="2" style="width:100%" placeholder="Mail carrier, squirrel, doorbell…">${esc(ep.note)}</textarea>
      <div class="row spread" style="margin-top:10px">
        <button class="btn" id="saveNote">Save note</button>
        <button class="btn danger" id="delEp">Delete episode</button>
      </div>
    </section>`;

  $("#prevEp").onclick = () => (location.hash = `#/clip/${ep.prev_id}`);
  $("#nextEp").onclick = () => (location.hash = `#/clip/${ep.next_id}`);
  $("#saveNote").onclick = async () => { await api(`/api/episodes/${id}`, { method: "PATCH", body: { note: $("#note").value } }); toast("Note saved"); };
  $("#delEp").onclick = async () => {
    if (!confirm("Delete this episode and its clip? This can't be undone.")) return;
    await api(`/api/episodes/${id}`, { method: "DELETE" }); toast("Episode deleted");
    location.hash = ep.next_id ? `#/clip/${ep.next_id}` : "#/clips";
  };

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
    $("#labeler").innerHTML = `
      <h2>${sel.size ? `Who made ${target}?` : "Who's barking in this clip?"}</h2>
      <div class="row">
        ${dogs.map((d) => `<button class="chip" data-label="${d.id}" style="--c:${d.color}" aria-pressed="${current === d.id}"><i></i>${esc(d.name)}</button>`).join("")}
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
  view.innerHTML = `
    <section>
      <h1>Dogs</h1>
      <p class="sub" style="max-width:62ch">Add each dog, then label a few clips per dog. After ${need} or more labelled barks a dog, new barks are matched to the closest voice automatically. More labels from different days make it more reliable.</p>
    </section>
    <section class="list" id="dogList">
      ${r.dogs.length ? r.dogs.map((d) => `
        <div class="dogcard" style="--c:${d.color}">
          <input type="color" value="${d.color}" data-color="${d.id}" aria-label="Color for ${esc(d.name)}">
          <div>
            <input class="name" value="${esc(d.name)}" data-name="${d.id}" aria-label="Name">
            <div class="small muted">${d.labelled_barks} labelled · ${d.predicted_barks} matched automatically</div>
            <div class="meter" title="${Math.min(d.labelled_barks, need)} of ${need} labels needed to start matching"><b style="width:${Math.min(100, (100 * d.labelled_barks) / need)}%"></b></div>
          </div>
          <div class="row">
            <a class="btn" href="#/clips" data-filter="${d.id}">Clips</a>
            <button class="btn danger" data-del="${d.id}">Remove</button>
          </div>
        </div>`).join("") : `<div class="empty"><strong>No dogs yet</strong>Add one below. Barks are still logged without dogs; they just show as unidentified.</div>`}
    </section>
    <section>
      <form class="row" id="addDog">
        <input type="text" id="newName" placeholder="Dog's name" required maxlength="40" aria-label="Dog's name">
        <button class="btn primary" type="submit">Add dog</button>
      </form>
    </section>
    <section class="panel">
      <h2>Ignoring false alarms</h2>
      <p class="small muted" style="margin:0">${r.not_bark_examples} sound${r.not_bark_examples === 1 ? "" : "s"} marked “Not a bark”. ${st.identifier.ready ? "Similar sounds are now dropped before they're logged." : "Mark at least " + need + " to start filtering them out."}</p>
    </section>`;
  $("#addDog").onsubmit = async (ev) => {
    ev.preventDefault();
    try { await api("/api/dogs", { method: "POST", body: { name: $("#newName").value } }); toast("Dog added"); renderDogs(); }
    catch (e) { toast(e.message.includes("UNIQUE") ? "There's already a dog with that name" : e.message); }
  };
  view.querySelectorAll("[data-name]").forEach((i) => i.addEventListener("change", async () => {
    await api(`/api/dogs/${i.dataset.name}`, { method: "PATCH", body: { name: i.value } }); toast("Name saved");
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
}

// ----------------------------------------------------------------- system
function renderSystem() {
  view.innerHTML = `
    <section><h1>System</h1><p class="sub">Live microphone level and detector health.</p></section>
    <section class="panel"><h2>Microphone</h2><div id="lvl"></div>
      <p class="small muted" style="margin:6px 0 0">Grey is the background noise level the Pi has learned; sounds above the dashed line wake the bark detector. If normal room noise keeps crossing it, raise <code>trigger_db</code>. If barks don't reach it, move the mic closer or add <code>gain_db</code>.</p></section>
    <section class="grid2">
      <div class="panel"><h2>Detector</h2><dl class="kv" id="detKv"></dl></div>
      <div class="panel"><h2>Storage</h2><dl class="kv" id="diskKv"></dl>
        <div class="row" style="margin-top:14px"><a class="btn" href="/api/export.csv">Download log as CSV</a></div></div>
    </section>`;
  renderSystemLive();
}
function renderSystemLive() {
  const s = S.status; if (!s || !$("#lvl")) return;
  const d = s.detector;
  if (!d) { $("#lvl").innerHTML = `<div class="notice ${s.error ? "err" : ""}">${s.error ? esc(s.error) : "Not listening: started with --no-audio."}</div>`; }
  else {
    const hist = d.history, W = 1000, H = 140, lo = -90, hi = 0;
    const y = (v) => H - ((Math.min(hi, Math.max(lo, v)) - lo) / (hi - lo)) * H;
    const n = Math.max(hist.length - 1, 1);
    const pts = (i) => hist.map((p, j) => `${(j / n) * W},${y(p[i])}`).join(" ");
    let g = "";
    for (let v = -80; v < 0; v += 20) g += `<line x1="0" x2="${W}" y1="${y(v)}" y2="${y(v)}" stroke="var(--faint)"/><text x="4" y="${y(v) - 3}">${v} dB</text>`;
    g += `<polyline points="${pts(2)}" fill="none" stroke="var(--unknown)" stroke-width="2"/>`;
    g += `<line x1="0" x2="${W}" y1="${y(d.trigger_db)}" y2="${y(d.trigger_db)}" stroke="var(--ink)" stroke-dasharray="5 4"/>`;
    g += `<polyline points="${pts(1)}" fill="none" stroke="${d.barking_now ? "var(--live)" : "var(--ink)"}" stroke-width="1.5"/>`;
    $("#lvl").innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Input level over the last minute">${g}</svg>`;
  }
  const det = $("#detKv");
  if (det) det.innerHTML = d ? `
      <dt>Detector</dt><dd>${d.classifier === "yamnet" ? "YAMNet sound model" : "Basic (no model installed)"}</dd>
      <dt>Level now</dt><dd>${d.level_db} dBFS</dd>
      <dt>Background</dt><dd>${d.floor_db} dBFS</dd>
      <dt>Last check</dt><dd>${Math.round(d.last_prob * 100)}% bark${d.last_detail?.top ? ` · heard “${esc(d.last_detail.top)}”` : ""} · ${d.classify_ms} ms</dd>
      <dt>Barks since start</dt><dd>${d.stats.barks || 0} (${d.stats.suppressed || 0} ignored as known false alarms)</dd>
      <dt>Dog matching</dt><dd>${s.identifier.ready ? "On" : `Waiting for ${s.identifier.min_labels}+ labels per dog`}</dd>
      <dt>Running for</dt><dd>${fmtDur(d.uptime_s)}</dd>` : `<dt>Status</dt><dd>Not listening</dd>`;
  const dk = $("#diskKv");
  if (dk) dk.innerHTML = `
      <dt>Clips</dt><dd>${s.disk.clips_mb} MB of ${s.disk.cap_mb} MB cap</dd>
      <dt>Free space</dt><dd>${(s.disk.free_mb / 1024).toFixed(1)} GB</dd>
      <dt>Keeping audio</dt><dd>${s.settings.retention_days} days</dd>
      <dt>Episodes logged</dt><dd>${s.counts.episodes} (${s.counts.unreviewed || 0} not reviewed)</dd>
      <dt>Episode gap</dt><dd>${s.settings.episode_gap} s of quiet ends an episode</dd>`;
}

// -------------------------------------------------------------------- go
pollStatus().finally(navigate);
setInterval(pollStatus, 2000);
