"use strict";
// BarkTracker dashboard: shared helpers, the router and the live band. Vanilla JS, no build step,
// works offline on the LAN. Each page lives in its own file (dashboard.js, clips.js, report.js,
// dogs.js, tuning.js, settings.js) and registers itself in PAGES.

const $ = (s, el = document) => el.querySelector(s);
const view = $("#view");
const S = { dogs: {}, status: null, tz: undefined, overviewDate: null, clipFilters: { dog: "", review: "", min: 0, offset: 0 } };
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
  const t = $("#toast"); t.textContent = msg; t.classList.remove("has-link"); t.classList.add("show");
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
function dogTags(dogs, match) {
  const e = Object.entries(dogs || {}).sort((a, b) => b[1] - a[1]);
  if (!e.length) return `<span class="tag">No dog</span>`;
  return e.map(([k, n]) => `<span class="tag" style="--c:${dogColor(k)}"><i></i>${esc(dogName(k))} ×${n}${match && match[k] != null ? ` <span class="match" title="Matched automatically, ${Math.round(match[k] * 100)}% sure it's ${esc(dogName(k))}">${Math.round(match[k] * 100)}% match</span>` : ""}</span>`).join("");
}
function confPill(p, fp) {
  if (p == null) return "";
  const pct = Math.round(p * 100);
  const lvl = pct >= 70 ? "hi" : pct >= 45 ? "mid" : "lo";
  return `<span class="conf conf-${lvl}" title="${fp ? "Before it was marked “Not a bark”, the" : "The"} detector was ${pct}% sure this is barking">${pct}% bark</span>`;
}
const MARK = {
  auto: ["Auto", "Found, and matched to a dog if possible, automatically. Nobody has checked it yet."],
  manual: ["Manual", "You labelled the barks or reviewed this clip."],
  mixed: ["Partly manual", "You labelled some of the barks; the rest were matched automatically."],
};
function markBadge(m) {
  if (!MARK[m]) return "";
  return `<span class="mark mark-${m}" title="${MARK[m][1]}">${m === "auto" ? "Auto" : m === "manual" ? "✓ Manual" : "◐ Partly manual"}</span>`;
}

// --- which dogs the overview shows (kept per browser)
function loadDogFilter() {
  if (S.dogFilter) return;
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem("bt.dogFilter") || "{}"); } catch (_) { saved = {}; }
  S.dogFilter = new Set(Array.isArray(saved.dogs) ? saved.dogs : []);
  S.filterBy = saved.by === "households" ? "households" : "dogs";
}
function saveDogFilter() {
  try { localStorage.setItem("bt.dogFilter", JSON.stringify({ dogs: [...S.dogFilter], by: S.filterBy })); } catch (_) { /* private mode */ }
}
function households() {
  // group dogs by address (or owner when there's no address)
  const m = new Map();
  for (const d of Object.values(S.dogs)) {
    const addr = (d.address || "").trim(), owner = (d.owner || "").trim();
    const key = (addr || owner).toLowerCase() || `dog:${d.id}`;
    if (!m.has(key)) m.set(key, { label: addr || owner || d.name, sub: addr && owner ? owner : addr || owner ? "" : "no household set", dogs: [] });
    m.get(key).dogs.push(String(d.id));
  }
  return [...m.values()];
}
function dogsQS() {
  return S.dogFilter && S.dogFilter.size ? `&dogs=${[...S.dogFilter].join(",")}` : "";
}
function filterLabel() {
  if (!S.dogFilter?.size) return "";
  const names = [...S.dogFilter].map(dogName);
  return names.length <= 2 ? names.join(" and ") : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}
function dogFilterBar() {
  const dogs = Object.values(S.dogs);
  if (!dogs.length) return "";
  const sel = S.dogFilter;
  const chip = (keys, label, sub, color) => {
    const on = keys.length && keys.every((k) => sel.has(k));
    return `<button type="button" class="chip" aria-pressed="${on}" data-keys="${keys.join(",")}">${color ? `<i style="--c:${color}"></i>` : ""}${esc(label)}${sub ? `<small>${esc(sub)}</small>` : ""}</button>`;
  };
  const chips = S.filterBy === "households"
    ? households().map((h) => chip(h.dogs, h.label, h.sub || h.dogs.map(dogName).join(", "), null))
    : dogs.map((d) => chip([String(d.id)], d.name, "", d.color)).concat(chip(["u"], "Unidentified", "", UNKNOWN));
  return `<div class="dogfilter" role="group" aria-label="Show barking from">
    <span class="seg" role="radiogroup" aria-label="Pick by">
      <button type="button" role="radio" data-by="dogs" aria-checked="${S.filterBy === "dogs"}">Dogs</button>
      <button type="button" role="radio" data-by="households" aria-checked="${S.filterBy === "households"}">Households</button>
    </span>
    <button type="button" class="chip" data-keys="" aria-pressed="${!sel.size}">Everyone</button>
    ${chips.join("")}
  </div>`;
}
function wireDogFilter(rerender) {
  view.querySelectorAll(".dogfilter [data-by]").forEach((b) => b.onclick = () => { S.filterBy = b.dataset.by; saveDogFilter(); rerender(); });
  view.querySelectorAll(".dogfilter .chip").forEach((b) => b.onclick = () => {
    const keys = b.dataset.keys ? b.dataset.keys.split(",") : [];
    if (!keys.length) S.dogFilter.clear();
    else if (keys.every((k) => S.dogFilter.has(k))) keys.forEach((k) => S.dogFilter.delete(k));
    else keys.forEach((k) => S.dogFilter.add(k));
    saveDogFilter(); rerender();
  });
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

function fmtClock(s) { s = Math.round(s); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60; return h ? `${h}:${String(m).padStart(2, "0")}:${String(x).padStart(2, "0")}` : `${m}:${String(x).padStart(2, "0")}`; }
function shortDate(iso) { return new Date(iso + "T12:00:00Z").toLocaleDateString([], { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" }); }
function longDate(iso) { return new Date(iso + "T12:00:00Z").toLocaleDateString([], { weekday: "short", year: "numeric", month: "short", day: "numeric", timeZone: "UTC" }); }
const hourName = (h) => { h = ((h % 24) + 24) % 24; return h === 0 ? "12 AM" : h === 12 ? "12 PM" : h > 12 ? `${h - 12} PM` : `${h} AM`; };
const clock12 = (hhmm) => { const [h, m] = String(hhmm).split(":").map(Number); const t = hourName(h); return m ? t.replace(" ", `:${String(m).padStart(2, "0")} `) : t; };
const DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

const PAGES = {};                    // name -> render function (registered by each page file)
const NAV = ["dashboard", "clips", "report", "dogs", "tune", "settings"];

// spectrogram image from the API ({width, height, data: base64 rows, top = high pitch}) onto a canvas
function paintSpec(canvas, sp) {
  canvas.width = sp.width; canvas.height = sp.height;
  const ctx = canvas.getContext("2d"), img = ctx.createImageData(sp.width, sp.height), raw = atob(sp.data);
  for (let i = 0; i < raw.length; i++) { const v = raw.charCodeAt(i) * 3; img.data[i * 4] = LUT[v]; img.data[i * 4 + 1] = LUT[v + 1]; img.data[i * 4 + 2] = LUT[v + 2]; img.data[i * 4 + 3] = 255; }
  ctx.putImageData(img, 0, 0);
}
// the colors a canvas needs, read from the theme
function cssVar(name, el = document.body) { return getComputedStyle(el).getPropertyValue(name).trim(); }
function resolveColor(c) { return c && c.startsWith("var(") ? cssVar(c.slice(4, -1)) : c; }

// rule names for flags, from the report settings (cached; refreshed when they're saved)
let RULE_LABELS = { sustained: "Sustained", quiet_hours: "Quiet hours", frequent: "Frequent day", habitual: "Habitual pattern" };
function ruleLabel(id) { return RULE_LABELS[id] || id; }

// ---------------------------------------------------------- status poll
async function pollStatus() {
  try {
    const s = await api("/api/status");
    S.status = s;
    if (s.settings.timezone && s.settings.timezone !== "system") S.tz = s.settings.timezone;
    if (route.name === "dashboard" && s.detector && S.lastEpisodes !== s.counts.episodes) {
      if (S.lastEpisodes !== undefined && S.overviewDate === isoToday()) PAGES.dashboard(true);
      S.lastEpisodes = s.counts.episodes;
    }
    if (route.name === "settings" && PAGES.settingsTick) PAGES.settingsTick();
  } catch (_) { /* the live band shows connection problems */ }
}

// ---------------------------------------------------------------- router
// #/              dashboard          #/clips[/id]       clips (a clip opens in place)
// #/report        report             #/dogs             dogs
// #/tune          tuning             #/tune/capture/id  reviewing an "I hear barking" capture
// #/settings[/section]               #/live             full-screen live band
const route = { name: "", arg: null, sub: null };
const LEGACY = { overview: "", clip: "clips", array: "tune", missed: "tune/capture", ordinance: "report", system: "settings/system" };
function navigate() {
  const parts = location.hash.replace(/^#\/?/, "").split("/");
  if (parts[0] in LEGACY) {                     // links from older versions keep working
    const rest = parts.slice(1).join("/");
    location.replace(`#/${LEGACY[parts[0]]}${rest ? "/" + rest : ""}`); return;
  }
  route.name = parts[0] || "dashboard"; route.arg = parts[1]; route.sub = parts[2];
  if (route.name === "tune" && route.arg === "capture") { route.name = "capture"; route.arg = route.sub; }
  stopLoops();
  document.querySelectorAll("[data-nav]").forEach((a) => a.toggleAttribute("aria-current", false));
  const navName = route.name === "capture" ? "tune" : route.name;
  $(`[data-nav="${navName}"]`)?.setAttribute("aria-current", "page");
  player.pause(); resetBtn();
  document.onkeydown = null;
  document.body.classList.toggle("kiosk", route.name === "live");
  const page = PAGES[route.name] || PAGES.dashboard;
  page();
  if (!(route.name === "clips" && route.arg)) window.scrollTo(0, 0);
}
// pages with live loops register a stop function here; leaving the page stops them
const LOOPS = new Set();
function stopLoops() { for (const f of LOOPS) { try { f(); } catch (_) { /* already gone */ } } LOOPS.clear(); }
window.addEventListener("hashchange", navigate);

// "I hear barking": save the last few seconds for review (Tuning page and the live band)
async function hearBarking(seconds = 30) {
  try {
    const r = await api("/api/missed", { method: "POST", body: { seconds } });
    toastLink(`Saved the last ${seconds} s for review`, `#/tune/capture/${r.id}`, "Review");
    return r.id;
  } catch (e) { toast(e.message); return null; }
}
function toastLink(msg, href, label) {
  const t = $("#toast");
  t.innerHTML = `${esc(msg)} <a href="${href}">${esc(label)}</a>`;
  t.classList.add("show", "has-link");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove("show", "has-link"), 6000);
}

// ---------------------------------------------------------------- theme
// Colors come from /theme.css (the active theme). Behaviors (BARK words, tab alert, motion) are
// read here and handed to the live band.
const Theme = {
  current: null,
  async load() {
    try { const r = await api("/api/themes"); this.apply(r.theme, false); } catch (_) { /* defaults */ }
  },
  apply(t, reloadCss = true) {
    this.current = t;
    document.documentElement.dataset.motion = t.behaviors.motion;
    Beat.setPrefs({ barkWords: t.behaviors.bark_words, alertColor: t.alert_color, tabAlert: t.behaviors.tab_alert, motion: t.behaviors.motion });
    if (reloadCss) { const l = $("#themeCss"); if (l) l.href = `/theme.css?t=${Date.now()}`; }
  },
};

// -------------------------------------------------------------- heartbeat
// The band at the top of every page. One picture per mode (nap: quiet hours and calm,
// sit: daytime or noisy without barking, bark: bark detected), a live EQ of the mic with
// falling bars and peak caps (monotone normally, full color while barking), and BARK splashes.
const Beat = (() => {
  const band = $("#beat"), mediaEl = $("#beatMedia"), splash = $("#splash");
  const title = $("#beatTitle"), sub = $("#beatSub"), canvas = $("#beatEq"), heard = $("#beatHeard");
  const reduceMQ = window.matchMedia("(prefers-reduced-motion: reduce)");
  const reduce = { get matches() { return reduceMQ.matches || prefs.motion === "reduced"; } };
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
    const h = l.heard && l.state !== "off" && !(l.error && l.state !== "barking") ? l.heard : null;
    if (heard) {
      heard.hidden = !h;
      if (h) { heard.textContent = `Hearing ${h.text}`; heard.title = `The sound model is ${Math.round(h.score * 100)}% sure: “${h.label}”`; }
    }
    tabState(l.mode === "bark" && l.state !== "off");
    const started = l.state === "barking" && prevState !== "barking";
    if (lastBarks !== null && l.barks > lastBarks) spawn(l.barks - lastBarks + (started ? 2 : 0));
    else if (started) spawn(3);
    lastBarks = l.barks; prevState = l.state;
  }
  function lost() {
    if (++failures < 3) return;
    live = { ...live, state: "error", bands: [] };
    band.dataset.state = "error"; title.textContent = "Can't reach the Pi"; sub.textContent = "Retrying…";
    if (heard) heard.hidden = true;
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
  let bars = [], last = performance.now(), looping = false, cssCache = null, cssCacheAt = 0;
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
    if (!W || !H) { looping = !reduce.matches; if (looping) requestAnimationFrame(frame); return; }
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
    if (!cssCache || now - cssCacheAt > 1000) {      // plain values: style lookups every frame are slow on a Pi
      const cs = getComputedStyle(band), v = {};
      for (const k of ["--ink", "--faint", "--muted"]) v[k] = cs.getPropertyValue(k).trim();
      cssCache = { fontFamily: cs.fontFamily, getPropertyValue: (k) => v[k] ?? cs.getPropertyValue(k) };
      cssCacheAt = now;
    }
    const css = cssCache;
    const on = barking ? prefs.alertColor : css.getPropertyValue("--ink").trim();
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
    looping = !reduce.matches;
    if (looping) requestAnimationFrame(frame);
  }

  // ---- display preferences: from the active theme (see Theme)
  function setPrefs(p) { Object.assign(prefs, p); if (!prefs.barkWords) splash.replaceChildren(); if ("alertColor" in p) { iconState = null; tabState(lastBarking); } }

  // ---- browser tab: just the dog (black on a light tab bar, white on a dark one); it turns the
  // alert color while barking, and the title says so
  const LOGO_D = "M10.92,30.42c-3.89,1.44-7.65,2.04-10.92-1.12l.03-.31c.22,0,.43-.02.65,0,4.1.48,7.82-.56,10.9-3.24,3.04-2.64,6.54-3.89,10.43-4.48,3.18-.48,6.13-1.64,8.73-3.62.19-.15.65-.15.87-.03,3.12,1.83,6.4,3.23,9.93,4.05.23.05.57.35.57.55.08,2.2.18,4.41-.56,6.55-.8,2.3-2.18,4.15-4.17,5.57-.36.26-.72.79-.75,1.22-.17,2.53-.23,5.08-.34,7.61-.03.61.15,1,.76,1.25.84.35,1.32,1.03,1.47,1.94.1.6-.05.94-.74.92-.87-.03-1.75.01-2.62-.01-1.08-.03-1.46-.35-1.7-1.41-.73-3.2-1.45-6.4-2.14-9.6-.14-.67-.41-.92-1.15-1.03-2.71-.39-5.41-.92-8.11-1.39-.13-.02-.25-.06-.38-.07-.71-.07-1.53-.37-2.11-.12-.5.22-.73,1.07-1.07,1.65-.73,1.27-1.73,2.29-2.97,3.07-.33.2-.64.47-1,.57-2.57.75-2.98,2.92-3.35,5.09-.04.22.28.62.54.75.76.39,1.2.94,1.24,1.79.02.44-.13.71-.59.7-1.2-.03-2.41-.04-3.61-.11-.67-.04-1.04-.47-.97-1.14.21-2.35.43-4.7.73-7.04.06-.48.43-1.01.8-1.34,1.53-1.36,2.19-2.98,1.81-5.02-.13-.7-.14-1.42-.22-2.22Z M20.82,47.12c-2.51-.02-2.45.24-3.43-2.34-.56-1.48-1.13-2.97-1.71-4.44-.21-.53-.11-.9.41-1.22,1.58-.96,2.77-2.28,3.5-3.98.23-.53.55-.68,1.08-.58.83.16,1.66.32,2.5.45.52.08.55.33.35.75-.61,1.34-1.5,2.48-2.76,3.23-1.16.7-1.4,1.58-.97,2.77.12.34.2.69.27,1.04.18.91.41,1.67,1.56,1.86.75.12,1.22.79,1.4,1.59.13.58,0,.89-.66.86-.51-.02-1.03,0-1.54,0Z M40.58,32.78c-.08,2.51-.16,4.88-.24,7.26-.03,1-.04,2-.09,3-.03.54.2.84.7,1.05,1.07.45,1.62,1.28,1.67,2.45.02.4-.13.57-.52.56-.67,0-1.33,0-2,.02-.45,0-.61-.18-.65-.64-.09-1.18-.69-2.05-1.66-2.72-.28-.19-.54-.65-.54-.98.04-2.28.16-4.56.27-6.84.01-.24.1-.54.26-.69.88-.81,1.79-1.58,2.81-2.47Z M49.23,11.44c-1.12-.11-2.25-.21-3.37-.32-.89-.09-1.01-.32-.49-1.05.95-1.34,1.93-2.66,2.87-4.01.23-.33.36-.72.56-1.13-.26-.72-.46-1.44-.78-2.1-.4-.85-.68-.94-1.52-.51-1.25.65-2.49,1.33-3.69,2.06-.62.38-1.18.5-1.87.23-1.08-.42-2.18-.36-3.2.21-.65.36-1.29.76-1.84,1.25-.57.51-1.08.54-1.74.24-1.24-.56-2.5-1.07-3.75-1.6-.51-.21-.83-.12-.68.52.13.55.18,1.15.46,1.61.84,1.4,1.8,2.73,2.65,4.13.21.35.35.86.27,1.25-.15.8-.41,1.59-.71,2.35-.25.62-.14.96.46,1.29,2.62,1.43,5.33,2.63,8.24,3.36,1.32.34,1.36.35,1.6-1.01.4-2.21,1.69-3.49,3.88-3.91.63-.12,1.26-.25,1.87-.42.74-.21,1.24-.71,1.46-1.45.21-.72.07-.93-.68-1ZM39.62,8.4c-.56,0-1.01-.45-1.01-1.01s.45-1.01,1.01-1.01,1.01.45,1.01,1.01-.45,1.01-1.01,1.01Z";
  const favicon = document.getElementById("favicon");
  var prefs = { barkWords: true, alertColor: "#e8a800", tabAlert: true, motion: "full" };
  let iconState = null, lastBarking = false;
  function tabState(barking) {
    lastBarking = barking;
    if (!prefs.tabAlert) barking = false;
    const want = barking ? prefs.alertColor : "auto";
    if (favicon && iconState !== want) {
      iconState = want;
      const style = barking ? `path{fill:${want}}` : "path{fill:#000}@media (prefers-color-scheme:dark){path{fill:#fff}}";
      const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 50 50"><style>${style}</style><path fill-rule="evenodd" d="${LOGO_D}"/></svg>`;
      favicon.href = "data:image/svg+xml," + encodeURIComponent(svg);
    }
    const t = barking ? "BarkTracker - Barking Detected" : "BarkTracker";
    if (document.title !== t) document.title = t;
  }

  function start() {
    loadMedia(); connect();
    const hb = document.getElementById("hearBtn");
    if (hb) hb.onclick = () => hearBarking(30);
    // calm mode redraws four times a second; full motion animates every frame
    setInterval(() => { if (reduce.matches) frame(performance.now()); else if (!looping) { looping = true; requestAnimationFrame(frame); } }, 250);
    looping = true; requestAnimationFrame(frame);
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) disconnect(); else { last = performance.now(); connect(); }
    });
  }
  return { start, loadMedia, spawn, setPrefs, latest: () => live };
})();

// ------------------------------------------------------------- kiosk view
PAGES.live = () => { view.innerHTML = `<a class="kiosk-exit btn" href="#/">Back to dashboard</a>`; };
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && route.name === "live") location.hash = "#/"; });
