"use strict";
// Tuning: a live view of what the detector hears and decides, "I hear barking" captures, and
// what-if settings. Loaded before app.js; everything here runs only when called from the router,
// so it can use app.js helpers (api, $, esc, toast, view, route, S, LUT, dogColor, dogName...).

const TUNE = {
  FRAME_S: 0.05,
  WINDOW_S: 60,
  KINDS: {
    bark: { label: "Counted as a bark", short: "bark", color: "var(--ok-bark)" },
    quiet: { label: "Too quiet (check 1: loudness)", short: "quiet", color: "var(--muted)" },
    unscored: { label: "Loud enough, not scored yet", short: "loud", color: "var(--unknown)" },
    not_bark: { label: "Not bark-like enough (check 2: sound model)", short: "", color: "var(--caution)" },
    filtered: { label: "Matched a “Not a bark” clip (check 3)", short: "filtered", color: "var(--danger)" },
  },
};

// The same three checks as barktracker/tuning.py, so what-if settings recolor instantly.
function tuneVerdict(c, s) {
  const peak = c.peak, rise = c.rise, prob = c.prob;
  if (peak != null && peak <= s.min_level) return { kind: "quiet", check: 1, why: `${Math.round(peak)} dBFS is quieter than the “ignore quieter than” level (${Math.round(s.min_level)} dBFS)` };
  if (rise != null && rise < s.trigger_db) return { kind: "quiet", check: 1, why: `only ${rise.toFixed(1)} dB over the background; the loudness trigger needs ${s.trigger_db} dB` };
  if (prob == null) return { kind: "unscored", check: 2, why: "loud enough to be checked by the sound model" };
  if (prob < s.threshold) {
    const other = c.other && (c.other_score || 0) >= 0.3 ? `; it sounded most like ${c.other.split(",")[0].toLowerCase()}` : "";
    return { kind: "not_bark", check: 2, why: `the sound model was ${Math.round(prob * 100)}% sure it's barking; it needs ${Math.round(s.threshold * 100)}%${other}` };
  }
  if (c.filtered && s.suppress !== false) return { kind: "filtered", check: 3, why: "it matched sounds you marked “Not a bark”" };
  return { kind: "bark", check: 0, why: "counted as a bark" };
}
function tuneEventFacts(e) {
  const bits = [];
  if (e.peak != null) bits.push(`${Math.round(e.peak)} dBFS`);
  if (e.rise != null) bits.push(`${e.rise >= 0 ? "+" : ""}${e.rise.toFixed(1)} dB over background`);
  if (e.prob != null) bits.push(`${Math.round(e.prob * 100)}% bark`);
  if (e.other && (e.other_score || 0) >= 0.3) bits.push(`sounds like ${e.other.split(",")[0].toLowerCase()} ${Math.round(e.other_score * 100)}%`);
  return bits.join(" · ");
}
function tuneSettings(cur) {
  return S.whatIf ? { ...cur, ...S.whatIf } : cur;
}

// --------------------------------------------------------------- what-if controls
function whatIfHtml(cur) {
  const s = tuneSettings(cur), changed = !!S.whatIf;
  const row = (key, label, val, min, max, step, unit, help) => `
    <label class="wi-row">${label}
      <span class="wi-ctl"><input type="range" data-wi="${key}" min="${min}" max="${max}" step="${step}" value="${val}"><output>${unit(val)}</output></span>
      <span class="help">${help} Saved: ${unit(cur[key])}.</span></label>`;
  return `
    ${row("trigger_db", "Loudness trigger", s.trigger_db, 3, 30, 0.5, (v) => `${(+v).toFixed(1)} dB`, "How far over the background a sound must rise to be checked.")}
    ${row("min_level", "Ignore sounds quieter than", s.min_level, -90, -20, 1, (v) => `${Math.round(v)} dBFS`, "Nothing below this is checked at all.")}
    ${row("threshold", "Bark confidence needed", s.threshold, 0.05, 0.95, 0.05, (v) => `${Math.round(v * 100)}%`, "How sure the sound model must be.")}
    <div class="row wi-actions">
      <button type="button" class="btn primary" id="wiApply" ${changed ? "" : "disabled"}>Use these settings</button>
      <button type="button" class="btn" id="wiReset" ${changed ? "" : "disabled"}>Back to saved</button>
      <span class="small muted" id="wiNote">${changed ? "Trying settings: tags show what these would do. Nothing changes until you press Use these settings." : "Move a slider to see what would change."}</span>
    </div>`;
}
function wireWhatIf(root, cur, onChange) {
  root.querySelectorAll("[data-wi]").forEach((inp) => {
    inp.oninput = () => {
      const k = inp.dataset.wi, v = Number(inp.value);
      S.whatIf = { ...(S.whatIf || {}), [k]: v };
      if (["trigger_db", "min_level", "threshold"].every((x) => (S.whatIf[x] ?? cur[x]) === cur[x])) S.whatIf = null;
      inp.nextElementSibling.textContent = k === "threshold" ? `${Math.round(v * 100)}%` : k === "min_level" ? `${Math.round(v)} dBFS` : `${v.toFixed(1)} dB`;
      const changed = !!S.whatIf;
      root.querySelector("#wiApply").disabled = !changed;
      root.querySelector("#wiReset").disabled = !changed;
      root.querySelector("#wiNote").textContent = changed ? "Trying settings: tags show what these would do. Nothing changes until you press Use these settings." : "Move a slider to see what would change.";
      onChange();
    };
  });
  root.querySelector("#wiReset").onclick = () => { S.whatIf = null; root.innerHTML = whatIfHtml(cur); wireWhatIf(root, cur, onChange); onChange(); };
  root.querySelector("#wiApply").onclick = async () => {
    const s = tuneSettings(cur);
    try {
      await api("/api/settings", { method: "PUT", body: { values: {
        "detection.trigger_db": s.trigger_db, "detection.min_level_dbfs": s.min_level, "detection.bark_threshold": s.threshold } } });
      Object.assign(cur, { trigger_db: s.trigger_db, min_level: s.min_level, threshold: s.threshold });
      S.whatIf = null;
      toast("Detection settings saved; they apply right away");
      root.innerHTML = whatIfHtml(cur); wireWhatIf(root, cur, onChange); onChange();
    } catch (e) { toast(e.message); }
  };
}

// --------------------------------------------------------------- "I hear barking"
async function hearBarking(seconds = 30) {
  try {
    const r = await api("/api/missed", { method: "POST", body: { seconds } });
    toastLink(`Saved the last ${seconds} s for review`, `#/missed/${r.id}`, "Review");
    return r.id;
  } catch (e) { toast(e.message); return null; }
}
function toastLink(msg, href, label) {
  const t = $("#toast");
  t.innerHTML = `${esc(msg)} <a href="${href}">${esc(label)}</a>`;
  t.classList.add("show", "has-link");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove("show", "has-link"), 6000);
}

// --------------------------------------------------------------- live tuning page
async function renderTune() {
  clearTimeout(S.tuneTimer);
  const [sug, pool] = await Promise.all([api("/api/tuning/suggest"), api("/api/missed"), loadDogs()]);
  if (route.name !== "tune") return;
  const cur = { ...sug.current };
  view.innerHTML = `
    <section class="tune-head">
      <div><h1>Tuning</h1>
        <p class="sub">What the Pi hears right now, and why each sound was or wasn't counted. Every sound has to pass three checks: loud enough over the background, bark-like to the sound model, and not matching a clip you marked “Not a bark”.</p></div>
      <div class="hear">
        <button class="btn primary big" id="hearNow">I hear barking</button>
        <label class="small muted">Save the last <select id="hearSecs"><option>15</option><option selected>30</option><option>60</option></select> seconds</label>
      </div>
    </section>
    <section class="panel tl-panel">
      <div class="row spread"><h2>Live</h2><span class="small muted" id="tuneStatus">Connecting…</span></div>
      <div class="tl" id="tl">
        <canvas class="tl-spec" id="tlSpec" aria-label="Live spectrogram, low pitch at the bottom"></canvas>
        <div class="tl-lane" id="tlLane" aria-label="What the detector decided"></div>
        <canvas class="tl-level" id="tlLevel" aria-label="Sound level, background and trigger"></canvas>
        <div class="tl-cursor" id="tlCursor" hidden></div>
      </div>
      <div class="tl-axis"><span id="tlStart">−60 s</span><span>now</span></div>
      <div class="legend tl-legend">
        ${Object.entries(TUNE.KINDS).filter(([k]) => k !== "unscored").map(([k, v]) => `<span style="--c:${v.color}">${v.label}</span>`).join("")}
        <span class="nodot"><i class="ln ln-level"></i>Level</span><span class="nodot"><i class="ln ln-floor"></i>Background</span>
        <span class="nodot"><i class="ln ln-trig"></i>Trigger</span><span class="nodot"><i class="ln ln-min"></i>Ignore below</span>
      </div>
      <div class="tl-detail" id="tlDetail" aria-live="polite"><span class="muted">Point at (or tap) a tag to see why the detector decided what it did.</span></div>
    </section>
    <section class="grid2 tune-grid">
      <div class="panel"><h2>Try settings</h2><p class="small muted intro">Tags above recolor to show what would have been counted in the last minute.</p>
        <div id="whatIf">${whatIfHtml(cur)}</div>
        <p class="small" id="wiSummary"></p></div>
      <div class="panel" id="sugPanel">${suggestHtml(sug)}</div>
    </section>
    <section>
      <div class="row spread"><h2>Captures to review <span class="count">${pool.counts.new || 0}</span></h2></div>
      <p class="small muted sec-sub">Saved when you pressed “I hear barking”. Open one, mark the barks the Pi missed, and add them to the log under the right dog. Each one you review sharpens the suggestions.</p>
      ${poolList(pool.items)}
    </section>`;

  $("#hearNow").onclick = async () => {
    const id = await hearBarking(Number($("#hearSecs").value));
    if (id) { const p = await api("/api/missed"); if (route.name === "tune") view.querySelector(".pool").outerHTML = poolList(p.items); }
  };
  wireSuggest(cur);
  const live = { cols: [], last: -1, ev: [], since: 0, first: true, settings: cur, floor: null };
  S.tuneLive = live;
  wireWhatIf($("#whatIf"), cur, () => { drawTimeline(); summarize(); });
  wirePool();

  const spec = $("#tlSpec"), lvl = $("#tlLevel"), lane = $("#tlLane"), detail = $("#tlDetail");
  const off = document.createElement("canvas");          // 1 px per 50 ms frame, 64 rows
  const N = Math.round(TUNE.WINDOW_S / TUNE.FRAME_S);
  off.width = N; off.height = 64;
  const octx = off.getContext("2d");
  octx.fillStyle = "rgb(15,20,24)"; octx.fillRect(0, 0, N, 64);
  let frames = [];                                       // [{idx, level, floor}] newest last

  async function poll() {
    if (route.name !== "tune" || document.hidden) { if (route.name === "tune") S.tuneTimer = setTimeout(poll, 1000); return; }
    try {
      const f = await api(`/api/tuning/feed?since_idx=${live.last}&since_event=${live.since}`);
      if (!f.listening) { $("#tuneStatus").textContent = "Not listening: started without a microphone"; S.tuneTimer = setTimeout(poll, 3000); return; }
      live.settings = f.settings; Object.assign(cur, { trigger_db: f.settings.trigger_db, min_level: f.settings.min_level, threshold: f.settings.threshold });
      const raw = atob(f.cols || ""), n = f.levels.length;
      if (n) {
        // scroll the offscreen spectrogram left and paint the new columns on the right
        const k = Math.min(n, N);
        octx.drawImage(off, k, 0, N - k, 64, 0, 0, N - k, 64);
        const img = octx.createImageData(k, 64);
        for (let c = n - k; c < n; c++) {
          for (let r = 0; r < 64; r++) {
            const v = raw.charCodeAt(c * 64 + r) * 3, px = ((63 - r) * k + (c - (n - k))) * 4;
            img.data[px] = LUT[v]; img.data[px + 1] = LUT[v + 1]; img.data[px + 2] = LUT[v + 2]; img.data[px + 3] = 255;
          }
        }
        octx.putImageData(img, N - k, 0);
        for (let c = 0; c < n; c++) frames.push({ idx: f.first_idx + c * f.frame, level: f.levels[c][0], floor: f.levels[c][1] });
        if (frames.length > N) frames = frames.slice(frames.length - N);
        live.last = f.last_idx; live.frame = f.frame; live.sr = f.sr;
      }
      for (const e of f.events) if (!e.soft) live.ev.push(e);
      if (f.events.length) live.since = f.events[f.events.length - 1].id;
      const oldest = live.last - N * (live.frame || 800);
      live.ev = live.ev.filter((e) => e.idx > oldest);
      $("#tuneStatus").textContent = `Background ${Math.round(f.floor)} dBFS · trigger at ${Math.round(f.trigger_level)} dBFS${f.pending_review ? ` · ${f.pending_review} capture${f.pending_review === 1 ? "" : "s"} to review` : ""}`;
      drawTimeline(); summarize();
    } catch (_) { $("#tuneStatus").textContent = "Can't reach the Pi; retrying…"; }
    S.tuneTimer = setTimeout(poll, 250);
  }

  function xOf(idx, W) { return W - ((live.last - idx) / (live.frame || 800)) * (W / N); }
  function drawTimeline() {
    const s = tuneSettings(cur), dpr = window.devicePixelRatio || 1;
    const W = spec.clientWidth;
    if (!W) return;
    // spectrogram
    const H1 = spec.clientHeight;
    if (spec.width !== Math.round(W * dpr)) { spec.width = Math.round(W * dpr); spec.height = Math.round(H1 * dpr); }
    const sc = spec.getContext("2d");
    sc.imageSmoothingEnabled = true;
    sc.drawImage(off, 0, 0, spec.width, spec.height);
    // level graph: -90..-10 dBFS
    const H2 = lvl.clientHeight;
    if (lvl.width !== Math.round(W * dpr)) { lvl.width = Math.round(W * dpr); lvl.height = Math.round(H2 * dpr); }
    const lc = lvl.getContext("2d");
    lc.setTransform(dpr, 0, 0, dpr, 0, 0);
    lc.clearRect(0, 0, W, H2);
    const css = getComputedStyle(document.body);
    const y = (db) => H2 - 4 - ((Math.max(-90, Math.min(-10, db)) + 90) / 80) * (H2 - 8);
    lc.font = `11px ${css.fontFamily}`; lc.fillStyle = css.getPropertyValue("--muted");
    for (const db of [-80, -60, -40, -20]) {
      lc.globalAlpha = 0.35; lc.fillRect(0, Math.round(y(db)), W, 1); lc.globalAlpha = 1;
      lc.fillText(`${db}`, 2, y(db) - 2);
    }
    const line = (vals, color, dash, width = 1.5) => {
      lc.beginPath(); lc.setLineDash(dash); lc.strokeStyle = color; lc.lineWidth = width;
      let started = false;
      frames.forEach((fr) => {
        const v = vals(fr); if (v == null) { started = false; return; }
        const xx = xOf(fr.idx, W), yy = y(v);
        if (!started) { lc.moveTo(xx, yy); started = true; } else lc.lineTo(xx, yy);
      });
      lc.stroke(); lc.setLineDash([]);
    };
    line(() => s.min_level, css.getPropertyValue("--danger"), [2, 4], 1);
    line((fr) => fr.floor, css.getPropertyValue("--muted"), [5, 4]);
    line((fr) => fr.floor == null ? null : Math.max(fr.floor + s.trigger_db, s.min_level), css.getPropertyValue("--caution"), [], 1.5);
    line((fr) => fr.level, css.getPropertyValue("--ink"), [], 1.2);
    // decision tags
    let right = -Infinity;                        // tags that would overlap the previous one shrink to dots
    lane.innerHTML = live.ev.map((e) => {
      const v = tuneVerdict(e, s), k = TUNE.KINDS[v.kind], x = xOf(e.idx, W);
      if (x < 0) return "";
      const changed = S.whatIf && tuneVerdict(e, live.settings).kind !== v.kind;
      const txt = v.kind === "bark" ? (e.prob != null ? `${Math.round(e.prob * 100)}%` : "bark")
        : v.kind === "not_bark" ? (e.other && (e.other_score || 0) >= 0.5 ? e.other.split(",")[0].toLowerCase() : `${Math.round((e.prob || 0) * 100)}%`)
        : v.kind === "quiet" ? `+${Math.max(0, e.rise || 0).toFixed(0)} dB` : k.short;
      const w = txt.length * 6.5 + 14, dot = x - w / 2 < right + 3;
      right = dot ? Math.max(right, x + 6) : x + w / 2;
      return `<button type="button" class="tag-ev k-${v.kind}${changed ? " changed" : ""}${dot ? " dot" : ""}${e.id === live.selId ? " sel" : ""}" data-id="${e.id}" style="left:${x}px;--c:${k.color}" aria-label="${esc(k.label)}: ${esc(txt)}" title="${esc(txt)}">${esc(txt)}</button>`;
    }).join("");
    if (live.selId) showDetail(live.selId, false);
    const span = Math.round(N * TUNE.FRAME_S);
    $("#tlStart").textContent = `−${span} s`;
  }
  // tags move every refresh, so pointing and clicking go through the lane (and a click keeps it)
  function showDetail(id, select = true) {
    const e = live.ev.find((x) => x.id === id);
    if (!e) return;
    if (select) live.selId = id;
    const s = tuneSettings(cur), v = tuneVerdict(e, s), was = tuneVerdict(e, live.settings);
    detail.innerHTML = `<div><strong>${fmtTimeS(e.ts)}</strong> <span class="kind-pill" style="--c:${TUNE.KINDS[v.kind].color}">${TUNE.KINDS[v.kind].label}</span>
        ${live.selId === id ? `<button type="button" class="linkish small" id="tlUnpin">Clear</button>` : ""}</div>
      <div>${esc(v.why[0].toUpperCase() + v.why.slice(1))}.${S.whatIf && was.kind !== v.kind ? ` With the saved settings: ${esc(was.why)}.` : ""}</div>
      <div class="small muted">${esc(tuneEventFacts(e))}${e.kind === "bark" && e.dog ? ` · ${esc(dogName(String(e.dog)))}` : ""}</div>`;
    const u = $("#tlUnpin"); if (u) u.onclick = () => { live.selId = null; detail.innerHTML = `<span class="muted">Point at (or tap) a tag to see why the detector decided what it did.</span>`; drawTimeline(); };
  }
  lane.addEventListener("pointermove", (ev) => {
    const t = ev.target.closest?.(".tag-ev");
    if (t && !live.selId) showDetail(Number(t.dataset.id), false);
  });
  lane.addEventListener("click", (ev) => {
    const t = ev.target.closest?.(".tag-ev");
    if (t) { showDetail(Number(t.dataset.id)); drawTimeline(); }
  });
  function summarize() {
    const el = $("#wiSummary"); if (!el) return;
    const s = tuneSettings(cur), count = (st) => live.ev.filter((e) => tuneVerdict(e, st).kind === "bark").length;
    const now = count(live.settings), then = count(s);
    const quiet = live.ev.filter((e) => tuneVerdict(e, s).kind === "quiet").length;
    const nb = live.ev.filter((e) => tuneVerdict(e, s).kind === "not_bark").length;
    el.innerHTML = live.ev.length
      ? `In the last minute: <strong>${then}</strong> sound${then === 1 ? "" : "s"} counted as barks${S.whatIf ? ` (${now} with the saved settings)` : ""}, ${quiet} too quiet to check, ${nb} not bark-like enough.`
      : `<span class="muted">No sounds over the background in the last minute.</span>`;
  }
  window.addEventListener("resize", () => { if (route.name === "tune") drawTimeline(); });
  poll();
}

function suggestHtml(sug) {
  if (!sug.barks) {
    return `<h2>Suggestions</h2><p class="small muted">When you review a capture and mark the barks the Pi missed, this works out the gentlest settings that would have counted them, and what else those settings would let through.</p>`;
  }
  const c = sug.current, g = sug.suggested || c, by = sug.missed_by_check || {};
  const fmt = { trigger_db: (v) => `${v} dB`, min_level: (v) => `${Math.round(v)} dBFS`, threshold: (v) => `${Math.round(v * 100)}%` };
  const names = { trigger_db: "Loudness trigger", min_level: "Ignore sounds quieter than", threshold: "Bark confidence needed" };
  const why = [by[1] ? `${by[1]} too quiet` : "", by[2] ? `${by[2]} not bark-like enough` : "", by[3] ? `${by[3]} matched “Not a bark” clips` : ""].filter(Boolean).join(", ");
  return `<h2>Suggestions</h2>
    <p class="small">From <strong>${sug.barks}</strong> bark${sug.barks === 1 ? "" : "s"} you marked in captures, the saved settings miss <strong>${sug.missed_now}</strong>${why ? ` (${why})` : ""}.</p>
    ${sug.changed && sug.changed.length ? `
      <ul class="sug-list">${sug.changed.map((k) => `<li>${names[k]}: <strong>${fmt[k](g[k])}</strong> <span class="muted">(now ${fmt[k](c[k])})</span></li>`).join("")}</ul>
      <p class="small">These would count <strong>${sug.caught} of ${sug.barks}</strong>. ${sug.others ? `Of ${sug.others} other sound${sug.others === 1 ? "" : "s"} in those captures, ${sug.extra} would also count (${sug.extra_now} now)${sug.extra > sug.extra_now ? ", so expect a few more “Not a bark” clips to mark" : ""}.` : ""}</p>
      ${sug.caught < sug.barks ? `<p class="small muted">The other ${sug.barks - sug.caught} scored so low with the sound model (under 10%) that no sensible setting would count them without logging lots of other sounds. They're in the log now because you added them, and labelling them teaches dog matching this voice.</p>` : ""}
      ${g.threshold <= 0.15 && g.threshold < c.threshold ? `<p class="small muted">A bark confidence this low lets more non-bark sounds through; mark those “Not a bark” and the filter learns to drop them.</p>` : ""}
      <button type="button" class="btn" id="sugTry">Try these above</button>`
      : `<p class="small muted">${sug.missed_now ? "The missed barks are stopped by the “Not a bark” filter; review those clips instead of changing levels." : "The saved settings already count every bark you marked."}</p>`}`;
}
function wireSuggest(cur) {
  const b = $("#sugTry"); if (!b) return;
  b.onclick = async () => {
    const sug = await api("/api/tuning/suggest");
    S.whatIf = { trigger_db: sug.suggested.trigger_db, min_level: sug.suggested.min_level, threshold: sug.suggested.threshold };
    const root = $("#whatIf"); root.innerHTML = whatIfHtml(cur);
    wireWhatIf(root, cur, () => {}); root.querySelector("[data-wi]").dispatchEvent(new Event("input"));
    root.scrollIntoView({ behavior: "smooth", block: "center" });
  };
}

function poolList(items) {
  if (!items.length) return `<div class="list pool"><div class="empty"><strong>No captures yet</strong>When you hear barking the Pi didn't pick up, press “I hear barking” here or in the live band.</div></div>`;
  const st = { new: ["To review", "new"], added: ["Added to the log", ""], dismissed: ["No barking", ""] };
  return `<div class="list pool">${items.map((m) => `
    <a class="ep pool-row" href="#/missed/${m.id}">
      <span></span>
      <span class="when">${fmtTime(m.start_ts)}<span class="day">${fmtDay(m.start_ts)}</span></span>
      <span class="dur">${Math.round(m.seconds)} s</span>
      <span class="n">${m.analyzed ? "" : "scoring…"}</span>
      <span class="dogs small muted">${m.note ? esc(m.note) : ""}</span>
      <span class="state ${st[m.status]?.[1] || ""}">${st[m.status]?.[0] || m.status}${m.status === "dismissed" ? ` <button type="button" class="linkish small" data-del="${m.id}">Remove</button>` : ""}</span>
    </a>`).join("")}</div>`;
}
function wirePool() {
  view.querySelectorAll("[data-del]").forEach((b) => b.onclick = async (ev) => {
    ev.preventDefault(); ev.stopPropagation();
    await api(`/api/missed/${b.dataset.del}`, { method: "DELETE" });
    const p = await api("/api/missed"); view.querySelector(".pool").outerHTML = poolList(p.items); wirePool();
  });
}

// --------------------------------------------------------------- reviewing one capture
async function renderMissed() {
  clearTimeout(S.tuneTimer);
  const id = Number(route.arg);
  const qs = S.whatIf ? `?${new URLSearchParams(Object.entries(S.whatIf).map(([k, v]) => [k, String(v)]))}` : "";
  let m;
  try { [m] = await Promise.all([api(`/api/missed/${id}${qs}`), loadDogs()]); }
  catch (e) { view.innerHTML = `<div class="notice err">${esc(e.message)}</div>`; return; }
  if (route.name !== "missed") return;
  if (!m.analyzed && m.status === "new") {
    view.innerHTML = `<section><a href="#/tune" class="small">Tuning</a><h1>Scoring the sounds…</h1><p class="sub">Every sound in the capture is measured and checked the way the detector does it. This takes a few seconds.</p></section>`;
    S.tuneTimer = setTimeout(() => { if (route.name === "missed") renderMissed(); }, 1200);
    return;
  }
  const cur = { ...m.current };
  const sel = new Set((m.candidates || []).filter((c) => c.marked).map((c) => c.i));
  // faint flickers (barely over the background, and nothing like a bark) are tucked away
  const faint = (c) => !c.marked && c.live !== "bark" && (c.rise ?? 0) < 4 && (c.prob ?? 0) < 0.15;
  let showFaint = false;
  const len = m.seconds, editable = m.status === "new";
  const dogs = Object.values(S.dogs);
  view.innerHTML = `
    <section>
      <div class="row spread" style="margin-bottom:14px"><a href="#/tune" class="small">Tuning</a>
        ${m.next_new ? `<a class="btn" href="#/missed/${m.next_new}">Next capture to review</a>` : ""}</div>
      <h1>Heard at ${fmtTimeS(m.start_ts + len)}</h1>
      <p class="sub">${Math.round(len)} seconds saved by “I hear barking” on ${fmtDay(m.start_ts)}.
        ${m.status === "added" ? `Added to the log: <a href="#/clip/${m.episode_id}">open the clip</a>.` : m.status === "dismissed" ? "You marked this as no barking; the audio was deleted, and its measurements still help the suggestions." : "Select the barks the Pi missed, then add them under the right dog."}</p>
      ${m.has_audio ? `
      <div class="spec-wrap" id="spec"><canvas id="specCanvas" width="900" height="80"></canvas><div class="spec-marks" id="cands"></div><div class="playhead" id="ph" style="left:0"></div></div>
      <canvas class="tl-level miss-level" id="missLevel"></canvas>
      <div class="spec-time"><span>0 s</span><span>${editable ? "Select the sounds that are barking. Each tag shows which check stopped it." : "Each tag shows which check stopped it."}</span><span>${len.toFixed(1)} s</span></div>
      <audio id="aud" controls preload="auto" src="/api/missed/${id}/audio"></audio>` : ""}
    </section>
    ${editable ? `<section class="panel labeler" id="addPanel"></section>` : ""}
    <section class="panel">
      <div class="row spread"><h2>Every sound in the capture</h2><span class="small muted" id="candSum"></span></div>
      <div class="tablewrap"><table class="cand-table" id="candTable"></table></div>
      <button type="button" class="linkish small" id="faintBtn" hidden></button>
    </section>
    <section class="panel"><h2>Try settings</h2><p class="small muted intro">See which of these sounds other settings would have counted.</p><div id="whatIf">${whatIfHtml(cur)}</div></section>`;

  const aud = $("#aud");
  let stopAt = null;
  if (m.has_audio) {
    api(`/api/missed/${id}/spectrogram?width=900&height=80`).then((sp) => {
      const c = $("#specCanvas"); if (!c) return;
      c.width = sp.width; c.height = sp.height;
      const ctx = c.getContext("2d"), img = ctx.createImageData(sp.width, sp.height), raw = atob(sp.data);
      for (let i = 0; i < raw.length; i++) { const v = raw.charCodeAt(i) * 3; img.data[i * 4] = LUT[v]; img.data[i * 4 + 1] = LUT[v + 1]; img.data[i * 4 + 2] = LUT[v + 2]; img.data[i * 4 + 3] = 255; }
      ctx.putImageData(img, 0, 0);
    }).catch(() => {});
    const tick = () => {
      if (!document.body.contains(aud)) return;
      $("#ph").style.left = `${(aud.currentTime / len) * 100}%`;
      if (stopAt != null && aud.currentTime >= stopAt) { aud.pause(); stopAt = null; }
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
    $("#spec").addEventListener("click", (ev) => {
      if (ev.target.closest(".cand")) return;
      const r = ev.currentTarget.getBoundingClientRect();
      aud.currentTime = ((ev.clientX - r.left) / r.width) * len; stopAt = null; aud.play();
    });
  }

  function draw() {
    const s = tuneSettings(cur), cands = m.candidates || [];
    const marks = $("#cands");
    if (marks) {
      marks.innerHTML = cands.filter((c) => showFaint || !faint(c)).map((c) => {
        const v = tuneVerdict(c, s), k = TUNE.KINDS[v.kind], logged = c.live === "bark";
        return `<button type="button" class="cand k-${v.kind}${logged ? " logged" : ""}" data-i="${c.i}" aria-pressed="${sel.has(c.i)}" ${editable && !logged ? "" : "data-ro"}
          style="--c:${k.color};left:${(c.t / len) * 100}%;width:${Math.max(0.6, ((c.end - c.t) / len) * 100)}%"
          title="${esc(`${c.t.toFixed(1)} s: ${logged ? "already logged as a bark" : v.why}`)}"></button>`;
      }).join("");
      marks.querySelectorAll(".cand").forEach((b) => b.onclick = (ev) => {
        const i = Number(b.dataset.i), c = cands[i];
        if (aud) { aud.currentTime = Math.max(0, c.t - 0.25); stopAt = c.end + 0.35; aud.play(); }
        if (b.hasAttribute("data-ro")) return;
        sel.has(i) ? sel.delete(i) : sel.add(i);
        draw();
      });
    }
    // level graph under the spectrogram, same time axis
    const lv = $("#missLevel");
    if (lv && m.trace.length) {
      const dpr = window.devicePixelRatio || 1, W = lv.clientWidth, H = lv.clientHeight;
      lv.width = Math.round(W * dpr); lv.height = Math.round(H * dpr);
      const lc = lv.getContext("2d"); lc.setTransform(dpr, 0, 0, dpr, 0, 0);
      const css = getComputedStyle(document.body), y = (db) => H - 3 - ((Math.max(-90, Math.min(-10, db)) + 90) / 80) * (H - 6);
      const x = (t) => (t / len) * W;
      const line = (f, color, dash, w = 1.4) => { lc.beginPath(); lc.setLineDash(dash); lc.strokeStyle = color; lc.lineWidth = w;
        m.trace.forEach((p, j) => { const v = f(p); j ? lc.lineTo(x(p[0]), y(v)) : lc.moveTo(x(p[0]), y(v)); }); lc.stroke(); lc.setLineDash([]); };
      line(() => s.min_level, css.getPropertyValue("--danger"), [2, 4], 1);
      line((p) => p[2], css.getPropertyValue("--muted"), [5, 4]);
      line((p) => Math.max(p[2] + s.trigger_db, s.min_level), css.getPropertyValue("--caution"), []);
      line((p) => p[1], css.getPropertyValue("--ink"), [], 1.1);
    }
    // table
    const tb = $("#candTable");
    const nFaint = cands.filter(faint).length, shown = cands.filter((c) => showFaint || !faint(c));
    const fb = $("#faintBtn");
    fb.hidden = !nFaint;
    fb.textContent = showFaint ? `Hide ${nFaint} faint sound${nFaint === 1 ? "" : "s"}` : `Show ${nFaint} faint sound${nFaint === 1 ? "" : "s"} (under 4 dB over the background)`;
    fb.onclick = () => { showFaint = !showFaint; draw(); };
    const cnt = {}; cands.forEach((c) => { const k = c.live === "bark" ? "logged" : tuneVerdict(c, s).kind; cnt[k] = (cnt[k] || 0) + 1; });
    $("#candSum").textContent = [cnt.logged && `${cnt.logged} logged live`, cnt.bark && `${cnt.bark} would count`, cnt.quiet && `${cnt.quiet} too quiet`,
      cnt.not_bark && `${cnt.not_bark} not bark-like`, cnt.filtered && `${cnt.filtered} filtered`].filter(Boolean).join(" · ");
    tb.innerHTML = `<thead><tr>${editable ? "<th>Barking</th>" : ""}<th>At</th><th>Level</th><th>Over background</th><th>Bark score</th><th>Sounds most like</th><th>Result</th></tr></thead>
      <tbody>${shown.map((c) => {
        const v = tuneVerdict(c, s), logged = c.live === "bark";
        return `<tr class="${sel.has(c.i) ? "sel" : ""}">
          ${editable ? `<td>${logged ? `<span class="small muted">logged</span>` : `<input type="checkbox" data-pick="${c.i}" ${sel.has(c.i) ? "checked" : ""} aria-label="Barking at ${c.t.toFixed(1)} s">`}</td>` : ""}
          <td><button type="button" class="linkish" data-play="${c.i}">${c.t.toFixed(1)} s</button></td>
          <td class="num">${Math.round(c.peak)} dBFS</td><td class="num">${c.rise >= 0 ? "+" : ""}${c.rise.toFixed(1)} dB</td>
          <td class="num">${c.prob != null ? `${Math.round(c.prob * 100)}%` : "–"}</td>
          <td>${c.other && (c.other_score || 0) >= 0.2 ? `${esc(c.other.split(",")[0])} ${Math.round(c.other_score * 100)}%` : "–"}</td>
          <td><span class="kind-pill" style="--c:${TUNE.KINDS[v.kind].color}">${logged ? "Logged live" : TUNE.KINDS[v.kind].label.split(" (")[0]}</span> <span class="small muted">${logged ? "" : esc(v.why)}</span></td></tr>`;
      }).join("") || `<tr><td colspan="7" class="muted">No sounds rose above the background in this capture.</td></tr>`}</tbody>`;
    tb.querySelectorAll("[data-pick]").forEach((cb) => cb.onchange = () => { const i = Number(cb.dataset.pick); cb.checked ? sel.add(i) : sel.delete(i); draw(); });
    tb.querySelectorAll("[data-play]").forEach((b) => b.onclick = () => { const c = cands[Number(b.dataset.play)]; if (aud) { aud.currentTime = Math.max(0, c.t - 0.25); stopAt = c.end + 0.35; aud.play(); } });
    drawAdd();
  }

  function drawAdd() {
    const p = $("#addPanel"); if (!p) return;
    const n = sel.size;
    p.innerHTML = `
      <h2>${n ? `Add ${n} bark${n === 1 ? "" : "s"} to the log` : "Select the barks the Pi missed"}</h2>
      <p class="small muted" style="margin:0">${n ? "Whose barks are they? They're logged as a new episode and labelled, so dog matching learns this voice." : "Tap tags on the spectrogram or tick them in the table below. Tags that were logged live are already counted."}</p>
      <div class="row">
        ${dogs.map((d) => `<button class="chip" data-add="${d.id}" style="--c:${d.color}" ${n ? "" : "disabled"}><i></i>${esc(d.name)}</button>`).join("")}
        <button class="chip" data-add="-1" style="--c:${UNKNOWN}" ${n ? "" : "disabled"} title="Logged as barking, credited to no dog, not learned from"><i></i>Unknown dog</button>
        <button class="chip" data-add="" ${n ? "" : "disabled"} title="Logged as barking; the Pi guesses the dog"><i></i>Let the Pi guess</button>
      </div>
      <div class="row spread"><span class="small muted">${dogs.length ? "" : `<a href="#/dogs">Add the dog</a> first to label these barks.`}</span>
        <button class="btn" id="dismiss">No barking here, discard</button></div>`;
    p.querySelectorAll("[data-add]").forEach((b) => b.onclick = async () => {
      const dog = b.dataset.add === "" ? null : Number(b.dataset.add);
      try {
        const r = await api(`/api/missed/${id}/add`, { method: "POST", body: { candidates: [...sel], dog_id: dog } });
        toastLink(`Added ${sel.size} bark${sel.size === 1 ? "" : "s"} to the log`, `#/clip/${r.episode_id}`, "Open clip");
        location.hash = m.next_new ? `#/missed/${m.next_new}` : "#/tune";
      } catch (e) { toast(e.message); }
    });
    $("#dismiss").onclick = async () => {
      if (!confirm("Discard this capture? Its audio is deleted; the measurements are kept to improve suggestions.")) return;
      await api(`/api/missed/${id}/dismiss`, { method: "POST" });
      toast("Discarded"); location.hash = m.next_new ? `#/missed/${m.next_new}` : "#/tune";
    };
  }

  wireWhatIf($("#whatIf"), cur, draw);
  draw();
  window.onresize = () => { if (route.name === "missed") draw(); };
}
