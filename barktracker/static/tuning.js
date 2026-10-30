"use strict";
// Detection and tuning: a live view of what the microphone(s) hear and what the detector decides,
// a bearing chart when two microphones are in use, "I hear barking" captures, and what-if levels.

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

// --------------------------------------------------------------- the Tuning page
// What the microphone(s) hear right now and why each sound was or wasn't counted. With two
// microphones (Settings > Microphone: Stereo or Two microphones) a bearing chart sits on top: the
// live band's EQ wrapped around a half-circle, one bar per wedge of direction, as tall as the
// sound coming from that way is loud over the background.
const POLAR = { RISE_FULL: 36, SEGS: 12, MARKS: 12 };

async function renderTune() {
  const [sug, pool, st] = await Promise.all([api("/api/tuning/suggest"), api("/api/missed"), api("/api/settings"), loadDogs()]);
  if (route.name !== "tune") return;
  const v = st.values, dirOn = !!v["direction.enabled"], pair = v["direction.mode"] === "pair";
  const cur = { ...sug.current };
  view.innerHTML = `
    <section class="tune-head">
      <div><h1>Detection and tuning</h1>
        <p class="sub">What the Pi hears right now, and why each sound was or wasn't counted. A sound has to pass three checks: loud enough over the background, bark-like to the sound model, and not like a clip you marked “Not a bark”.</p>
        <p class="small muted">Microphone: <strong>${esc(st.info.audio_device || "none")}</strong>, ${dirOn ? (pair ? "two microphones (direction on)" : "stereo (direction on)") : "mixed to mono"}. <a href="#/settings/mic">Change</a></p></div>
      <div class="hear">
        <button class="btn primary big" id="hearNow">I hear barking</button>
        <label class="small muted">Save the last <select id="hearSecs"><option>15</option><option selected>30</option><option>60</option></select> seconds</label>
      </div>
    </section>
    ${dirOn ? `<section class="panel polar-panel">
      <div class="row spread"><h2>Bearing</h2><span class="small muted" id="arrStatus"></span></div>
      <div class="polar-wrap"><canvas id="polar" class="polar" aria-label="Sound by direction: one bar per wedge, taller when louder over the background"></canvas></div>
      <div class="row spread polar-ctl">
        <div class="legend"><span style="--c:var(--ink)">Sound over the background</span><span style="--c:var(--alert)">While barking</span><span class="nodot"><i class="mkkey"></i>Numbered: sounds the detector decided about</span></div>
        <span class="seg" role="radiogroup" aria-label="Wedge size">${[10, 15, 30].map((w) => `<button type="button" role="radio" data-wedge="${w}" aria-checked="${(S.wedge || 10) === w}">${w}°</button>`).join("")}</span>
      </div>
      <p class="small muted" style="margin:6px 0 0">0° points at mic A, 180° at mic B, 90° straight ahead (or behind: two microphones can't tell front from back, so mount them against a wall). ${pair ? "" : "Clap at each end of the microphone to check the sides: mic A's end should light near 0°."}</p>
      ${pair ? `<div class="row calib"><span class="small"><strong>Two microphones run on separate clocks.</strong> Stand straight ahead, halfway between them, press Calibrate and clap once.</span>
        <button class="btn" id="arrCal">Calibrate</button><button class="btn" id="arrCalClear">Clear</button><span class="small muted" id="arrCalState"></span></div>` : ""}
    </section>` : ""}
    <section class="panel tl-panel">
      <div class="row spread"><h2>Live</h2><span class="small muted" id="tuneStatus">Connecting…</span></div>
      <div class="tl" id="tl">
        <canvas class="tl-spec" id="tlSpec" aria-label="Live spectrogram, low pitch at the bottom"></canvas>
        <div class="tl-lane" id="tlLane" aria-label="What the detector decided"></div>
        <canvas class="tl-level" id="tlLevel" aria-label="Sound level, background and trigger"></canvas>
      </div>
      <div class="tl-axis"><span id="tlStart">−60 s</span><span>now</span></div>
      <div class="legend tl-legend">
        ${Object.entries(TUNE.KINDS).filter(([k]) => k !== "unscored").map(([k, x]) => `<span style="--c:${x.color}">${x.label}</span>`).join("")}
        <span class="nodot"><i class="ln ln-level"></i>Level</span><span class="nodot"><i class="ln ln-floor"></i>Background</span>
        <span class="nodot"><i class="ln ln-trig"></i>Trigger</span><span class="nodot"><i class="ln ln-min"></i>Ignore below</span>
      </div>
      <div class="tl-detail" id="tlDetail" aria-live="polite"><span class="muted">Point at (or tap) a tag to see why the detector decided what it did${dirOn ? " and where it came from" : ""}.</span></div>
    </section>
    <section class="grid2 tune-grid">
      <div class="panel"><h2>Detection levels</h2><p class="small muted intro">Tags above recolor to show what would have been counted in the last minute. Nothing changes until you press Use these settings.</p>
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
    if (id) { const p = await api("/api/missed"); if (route.name === "tune") { view.querySelector(".pool").outerHTML = poolList(p.items); wirePool(); } }
  };
  wireSuggest(cur);
  const live = { last: -1, ev: [], since: 0, settings: cur, frame: 800 };
  let alive = true;
  LOOPS.add(() => { alive = false; });
  wireWhatIf($("#whatIf"), cur, () => { drawTimeline(); summarize(); });
  wirePool();

  // ---- direction: estimates per sound (numbered) and the live wedge scan
  const dir = { since: 0, scansSince: 0, byId: new Map(), list: [], status: null };
  const polar = dirOn ? makePolar($("#polar")) : null;
  if (polar) LOOPS.add(() => polar.stop());
  view.querySelectorAll("[data-wedge]").forEach((b) => b.onclick = () => {
    S.wedge = Number(b.dataset.wedge);
    view.querySelectorAll("[data-wedge]").forEach((x) => x.setAttribute("aria-checked", String(x === b)));
    polar?.setWedge(S.wedge);
  });
  const cal = $("#arrCal");
  if (cal) {
    cal.onclick = async () => { try { await api("/api/array/calibrate", { method: "POST", body: {} }); toast("Now clap once, straight ahead"); } catch (e) { toast(e.message); } };
    $("#arrCalClear").onclick = async () => { try { await api("/api/array/calibrate", { method: "POST", body: { clear: true } }); toast("Calibration cleared"); } catch (e) { toast(e.message); } };
  }

  const spec = $("#tlSpec"), lvl = $("#tlLevel"), lane = $("#tlLane"), detail = $("#tlDetail");
  const N = Math.round(TUNE.WINDOW_S / TUNE.FRAME_S);
  const off = document.createElement("canvas");          // 1 px per 50 ms frame, 64 rows
  off.width = N; off.height = 64;
  const octx = off.getContext("2d");
  octx.fillStyle = "rgb(15,20,24)"; octx.fillRect(0, 0, N, 64);
  let frames = [];

  async function poll() {
    if (!alive) return;
    if (document.hidden) { S.tuneTimer = setTimeout(poll, 1000); return; }
    try {
      const [f, a] = await Promise.all([
        api(`/api/tuning/feed?since_idx=${live.last}&since_event=${live.since}`),
        dirOn ? api(`/api/array/feed?since=${dir.since}&scans_since=${dir.scansSince}`) : null]);
      if (!alive) return;
      if (a) {
        dir.status = a.status;
        for (const e of a.events) { dir.byId.set(e.id, e); dir.list.push(e); }
        if (a.events.length) dir.since = a.events[a.events.length - 1].n;
        if (dir.list.length > 200) { for (const e of dir.list.splice(0, dir.list.length - 200)) dir.byId.delete(e.id); }
        for (const sc of a.scans) polar.hit(sc[1], sc[2], sc[3]);
        if (a.scans.length) dir.scansSince = a.scans[a.scans.length - 1][0];
        polar.marks(dir.list.filter((e) => e.valid).slice(-POLAR.MARKS), live.selId);
        drawArrStatus(dir.status);
      }
      if (!f.listening) { $("#tuneStatus").textContent = "Not listening: started without a microphone"; S.tuneTimer = setTimeout(poll, 3000); return; }
      live.settings = f.settings; Object.assign(cur, { trigger_db: f.settings.trigger_db, min_level: f.settings.min_level, threshold: f.settings.threshold });
      const raw = atob(f.cols || ""), n = f.levels.length;
      if (n) {
        const k = Math.min(n, N);
        octx.drawImage(off, k, 0, N - k, 64, 0, 0, N - k, 64);
        const img = octx.createImageData(k, 64);
        for (let c = n - k; c < n; c++) {
          for (let r = 0; r < 64; r++) {
            const val = raw.charCodeAt(c * 64 + r) * 3, px = ((63 - r) * k + (c - (n - k))) * 4;
            img.data[px] = LUT[val]; img.data[px + 1] = LUT[val + 1]; img.data[px + 2] = LUT[val + 2]; img.data[px + 3] = 255;
          }
        }
        octx.putImageData(img, N - k, 0);
        for (let c = 0; c < n; c++) frames.push({ idx: f.first_idx + c * f.frame, level: f.levels[c][0], floor: f.levels[c][1] });
        if (frames.length > N) frames = frames.slice(frames.length - N);
        live.last = f.last_idx; live.frame = f.frame;
      }
      for (const e of f.events) if (!e.soft) live.ev.push(e);
      if (f.events.length) live.since = f.events[f.events.length - 1].id;
      live.ev = live.ev.filter((e) => e.idx > live.last - N * live.frame);
      if (live.ev.length > 400) live.ev = live.ev.slice(-400);       // a noisy minute can't pile up
      $("#tuneStatus").textContent = `Background ${Math.round(f.floor)} dBFS · trigger at ${Math.round(f.trigger_level)} dBFS${f.pending_review ? ` · ${f.pending_review} capture${f.pending_review === 1 ? "" : "s"} to review` : ""}`;
      drawTimeline(); summarize();
    } catch (_) { $("#tuneStatus").textContent = "Can't reach the Pi; retrying…"; }
    if (alive) S.tuneTimer = setTimeout(poll, 250);
  }
  LOOPS.add(() => clearTimeout(S.tuneTimer));

  function drawArrStatus(s) {
    const el = $("#arrStatus"); if (!el || !s) return;
    const bits = s.mode === "stereo"
      ? [s.stereo_live ? `stereo, ${s.main_channels} ch at ${Math.round(s.main_rate / 1000)} kHz` : s.main_channels === 1 ? "this microphone is mono: choose a stereo one, or Two microphones" : "waiting for stereo audio"]
      : [s.second_live ? `second mic ${s.second_name} at ${Math.round(s.second_rate / 1000)} kHz` : s.device_b ? `opening ${s.device_b}…` : "choose the second microphone in Settings", s.offset_ms ? `calibrated (${s.offset_ms} ms)` : "not calibrated"];
    bits.push(`${s.spacing_cm} cm apart`);
    el.textContent = bits.join(" · ") + (s.error ? ` · ${s.error}` : "");
    const cs = $("#arrCalState"); if (cs) cs.textContent = s.calibrating ? "Listening for the clap…" : s.offset_ms ? `Offset ${s.offset_ms} ms` : "Not calibrated yet";
  }

  function xOf(idx, W) { return W - ((live.last - idx) / (live.frame || 800)) * (W / N); }
  let tcol = null, tcolAt = 0;
  function timelineColors() {
    const now = performance.now();
    if (!tcol || now - tcolAt > 2000) {
      tcol = { danger: cssVar("--danger"), muted: cssVar("--muted"), caution: cssVar("--caution"), ink: cssVar("--ink"),
               font: getComputedStyle(document.body).fontFamily };
      tcolAt = now;
    }
    return tcol;
  }
  let laneKey = "";
  function drawTimeline() {
    const s = tuneSettings(cur), dpr = window.devicePixelRatio || 1;
    const W = spec.clientWidth;
    if (!W) return;
    const H1 = spec.clientHeight;
    if (spec.width !== Math.round(W * dpr)) { spec.width = Math.round(W * dpr); spec.height = Math.round(H1 * dpr); }
    const sc = spec.getContext("2d");
    sc.imageSmoothingEnabled = true;
    sc.drawImage(off, 0, 0, spec.width, spec.height);
    const H2 = lvl.clientHeight;
    if (lvl.width !== Math.round(W * dpr)) { lvl.width = Math.round(W * dpr); lvl.height = Math.round(H2 * dpr); }
    const lc = lvl.getContext("2d");
    lc.setTransform(dpr, 0, 0, dpr, 0, 0);
    lc.clearRect(0, 0, W, H2);
    const y = (db) => H2 - 4 - ((Math.max(-90, Math.min(-10, db)) + 90) / 80) * (H2 - 8);
    const tc0 = timelineColors();
    lc.font = `11px ${tc0.font}`; lc.fillStyle = tc0.muted;
    for (const db of [-80, -60, -40, -20]) {
      lc.globalAlpha = 0.35; lc.fillRect(0, Math.round(y(db)), W, 1); lc.globalAlpha = 1;
      lc.fillText(`${db}`, 2, y(db) - 2);
    }
    const line = (vals, color, dash, width = 1.5) => {
      lc.beginPath(); lc.setLineDash(dash); lc.strokeStyle = color; lc.lineWidth = width;
      let started = false;
      frames.forEach((fr) => {
        const val = vals(fr); if (val == null) { started = false; return; }
        const xx = xOf(fr.idx, W), yy = y(val);
        if (!started) { lc.moveTo(xx, yy); started = true; } else lc.lineTo(xx, yy);
      });
      lc.stroke(); lc.setLineDash([]);
    };
    const tc = timelineColors();
    line(() => s.min_level, tc.danger, [2, 4], 1);
    line((fr) => fr.floor, tc.muted, [5, 4]);
    line((fr) => fr.floor == null ? null : Math.max(fr.floor + s.trigger_db, s.min_level), tc.caution, [], 1.5);
    line((fr) => fr.level, tc.ink, [], 1.2);
    let right = -Infinity;                        // tags that would overlap the previous one shrink to dots
    const key = `${live.ev.length}:${live.ev[live.ev.length - 1]?.id}:${Math.round(live.last / ((live.frame || 800) * 4))}:${live.selId}:${JSON.stringify(S.whatIf)}:${dir.list.length}:${W}`;
    if (key === laneKey) return;
    laneKey = key;
    lane.innerHTML = live.ev.slice(-150).map((e) => {
      const vd = tuneVerdict(e, s), k = TUNE.KINDS[vd.kind], x = xOf(e.idx, W);
      if (x < 0) return "";
      const changed = S.whatIf && tuneVerdict(e, live.settings).kind !== vd.kind;
      const de = dir.byId.get(e.id);
      let txt = vd.kind === "bark" ? (e.prob != null ? `${Math.round(e.prob * 100)}%` : "bark")
        : vd.kind === "not_bark" ? (e.other && (e.other_score || 0) >= 0.5 ? e.other.split(",")[0].toLowerCase() : `${Math.round((e.prob || 0) * 100)}%`)
        : vd.kind === "quiet" ? `+${Math.max(0, e.rise || 0).toFixed(0)} dB` : k.short;
      if (de) txt = `#${de.n} ${de.valid ? Math.round(de.angle) + "°" : "?"}${vd.kind === "bark" ? "" : " " + txt}`;
      const w = txt.length * 6.5 + 14, dot = x - w / 2 < right + 3;
      right = dot ? Math.max(right, x + 6) : x + w / 2;
      return `<button type="button" class="tag-ev k-${vd.kind}${changed ? " changed" : ""}${dot ? " dot" : ""}${e.id === live.selId ? " sel" : ""}" data-id="${e.id}" style="left:${x}px;--c:${k.color}" aria-label="${esc(k.label)}: ${esc(txt)}" title="${esc(txt)}">${esc(txt)}</button>`;
    }).join("");
    if (live.selId) showDetail(live.selId, false);
    $("#tlStart").textContent = `−${Math.round(N * TUNE.FRAME_S)} s`;
  }
  function showDetail(id, select = true) {
    const e = live.ev.find((x) => x.id === id);
    if (!e) return;
    if (select) { live.selId = id; polar?.marks(dir.list.filter((x) => x.valid).slice(-POLAR.MARKS), id); }
    const s = tuneSettings(cur), vd = tuneVerdict(e, s), was = tuneVerdict(e, live.settings), de = dir.byId.get(e.id);
    detail.innerHTML = `<div><strong>${fmtTimeS(e.ts)}</strong> <span class="kind-pill" style="--c:${TUNE.KINDS[vd.kind].color}">${TUNE.KINDS[vd.kind].label}</span>
        ${live.selId === id ? `<button type="button" class="linkish small" id="tlUnpin">Clear</button>` : ""}</div>
      <div>${esc(vd.why[0].toUpperCase() + vd.why.slice(1))}.${S.whatIf && was.kind !== vd.kind ? ` With the saved settings: ${esc(was.why)}.` : ""}</div>
      <div class="small muted">${esc(tuneEventFacts(e))}${e.kind === "bark" && e.dog ? ` · ${esc(dogName(String(e.dog)))}` : ""}</div>
      ${de ? `<div class="small muted">#${de.n}: ${de.valid ? `from about <strong>${Math.round(de.angle)}°</strong>` : `no clear direction (best guess ${Math.round(de.angle)}°)`}; mic B heard it ${Math.abs(Math.round(de.tau_us))} µs ${de.tau_us >= 0 ? "after" : "before"} mic A, clarity ${de.sharp}; level A ${Math.round(de.level_a)}, B ${Math.round(de.level_b)} dBFS.</div>` : ""}`;
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
    const s = tuneSettings(cur), count = (stt) => live.ev.filter((e) => tuneVerdict(e, stt).kind === "bark").length;
    const now = count(live.settings), then = count(s);
    const quiet = live.ev.filter((e) => tuneVerdict(e, s).kind === "quiet").length;
    const nb = live.ev.filter((e) => tuneVerdict(e, s).kind === "not_bark").length;
    el.innerHTML = live.ev.length
      ? `In the last minute: <strong>${then}</strong> sound${then === 1 ? "" : "s"} counted as barks${S.whatIf ? ` (${now} with the saved settings)` : ""}, ${quiet} too quiet to check, ${nb} not bark-like enough.`
      : `<span class="muted">No sounds over the background in the last minute.</span>`;
  }
  const onResize = () => drawTimeline();
  window.addEventListener("resize", onResize);
  LOOPS.add(() => window.removeEventListener("resize", onResize));
  poll();
}

// The bearing chart: an LED-segment bar per wedge of direction, like the live band's EQ bent
// around a half-circle. Bars jump up with a sound and fall back under gravity; the top segment
// is a peak cap that hangs for a moment. Bars turn the alert color while barking.
function makePolar(canvas) {
  const reduceMQ = window.matchMedia("(prefers-reduced-motion: reduce)");
  const calm = () => reduceMQ.matches || document.documentElement.dataset.motion === "reduced";
  let wedge = S.wedge || 10, bars = [], marks = [], sel = null, last = performance.now(), running = true, raf = 0;
  const reset = () => { bars = Array.from({ length: Math.round(180 / wedge) }, () => ({ h: 0, peak: 0, hold: 0, v: 0, barkUntil: 0 })); };
  reset();
  function hit(angle, rise, barking) {
    const i = Math.min(bars.length - 1, Math.max(0, Math.floor(angle / wedge)));
    const b = bars[i], t = Math.min(1, Math.max(0, rise / POLAR.RISE_FULL));
    if (t > b.h) b.h = t;
    if (barking) b.barkUntil = performance.now() + 1500;
  }
  // theme colors, read once a second rather than every frame (style lookups are slow on a Pi)
  let colors = null, colorsAt = 0, idleFrames = 0, lastDraw = 0;
  const readColors = (now) => {
    if (!colors || now - colorsAt > 1000) {
      colors = { ink: cssVar("--ink"), faint: cssVar("--faint"), muted: cssVar("--muted"), alert: cssVar("--alert") || "#e8a800",
                 font: getComputedStyle(document.body).fontFamily, kinds: {} };
      for (const [k, v] of Object.entries(TUNE.KINDS)) colors.kinds[k] = resolveColor(v.color);
      colorsAt = now;
    }
    return colors;
  };
  let dirty = true;
  function frame(now) {
    if (!running) return;
    if (now - lastDraw < 32 && !calm()) { raf = requestAnimationFrame(frame); return; }      // at most ~30 fps
    lastDraw = now;
    const dt = Math.min(0.1, (now - last) / 1000); last = now;
    const dpr = window.devicePixelRatio || 1, W = canvas.clientWidth, H = canvas.clientHeight;
    const moving = bars.some((b) => b.h > 0 || b.peak > 0);
    idleFrames = moving || dirty ? 0 : idleFrames + 1;
    if (W && H && idleFrames < 2) {                     // nothing moving: leave the last picture up
      dirty = false;
      if (canvas.width !== Math.round(W * dpr) || canvas.height !== Math.round(H * dpr)) { canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr); }
      const g = canvas.getContext("2d", { alpha: true }); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, W, H);
      const c = readColors(now), font = c.font, ink = c.ink, faint = c.faint, muted = c.muted, alert = c.alert;
      const cx = W / 2, cy = H - 26, R = Math.max(40, Math.min(W / 2 - 46, H - 52)), r0 = R * 0.2;
      const S_ = POLAR.SEGS, gap = Math.max(2, R / 70), segH = (R - r0 - gap * (S_ - 1)) / S_;
      const pad = Math.min(2.2, wedge * 0.12);          // degrees of gap between wedges
      const cr = Math.min(2.5, segH / 3, (r0 * (wedge - pad) * Math.PI) / 720);   // corners like the live EQ's cells
      const at = (rho, t) => [cx + Math.cos(t) * rho, cy + Math.sin(t) * rho];
      const turn = ([x, y], a, b) => {                 // a corner: the short way round a circle of radius cr
        let d = b - a;
        while (d > Math.PI) d -= 2 * Math.PI;
        while (d <= -Math.PI) d += 2 * Math.PI;
        g.arc(x, y, cr, a, a + d, d < 0);
      };
      const toward = ([x, y], [px, py]) => Math.atan2(py - y, px - x);
      const arc = (a0, a1, ri, ro) => {                // an annular sector with rounded corners; 0 deg = left (mic A)
        const t0 = Math.PI + (a0 * Math.PI) / 180, t1 = Math.PI + (a1 * Math.PI) / 180;
        g.beginPath();
        if (cr < 0.5) { g.arc(cx, cy, ro, t0, t1); g.arc(cx, cy, ri, t1, t0, true); }
        else {
          // each corner circle touches a straight side and a curved edge; po/pi: its centre's radius
          const po = ro - cr, pi = ri + cr, so = Math.asin(cr / po), si = Math.asin(cr / pi);
          const eo = Math.sqrt(po * po - cr * cr), ei = Math.sqrt(pi * pi - cr * cr);   // where it meets the side
          const c00 = at(pi, t0 + si), c01 = at(po, t0 + so), c11 = at(po, t1 - so), c10 = at(pi, t1 - si);
          g.moveTo(...at(ei, t0));
          g.lineTo(...at(eo, t0)); turn(c01, toward(c01, at(eo, t0)), t0 + so);
          g.arc(cx, cy, ro, t0 + so, t1 - so); turn(c11, t1 - so, toward(c11, at(eo, t1)));
          g.lineTo(...at(ei, t1)); turn(c10, toward(c10, at(ei, t1)), t1 - si + Math.PI);
          g.arc(cx, cy, ri, t1 - si, t0 + si, true); turn(c00, t0 + si + Math.PI, toward(c00, at(ei, t0)));
        }
        g.closePath(); g.fill();
      };
      bars.forEach((b, i) => {
        if (calm()) { b.peak = Math.max(b.h, b.peak - 0.6 * dt); }
        else {
          b.h = Math.max(0, b.h - 1.1 * dt);
          if (b.h >= b.peak) { b.peak = b.h; b.hold = 0.5; b.v = 0; }
          else if (b.hold > 0) b.hold -= dt;
          else { b.v += 2.0 * dt; b.peak = Math.max(b.h, b.peak - b.v * dt); }
        }
        const lit = Math.round(b.h * S_), top = b.peak > 0.04 || lit > 0 ? Math.min(S_ - 1, Math.max(lit - 1, Math.floor(b.peak * S_))) : -1;
        const on = b.barkUntil > now ? alert : ink;
        for (let j = 0; j < S_; j++) {
          const ri = r0 + j * (segH + gap);
          if (j === top) { g.fillStyle = on; g.globalAlpha = 1; }
          else if (j < lit) { g.fillStyle = on; g.globalAlpha = 0.55; }
          else { g.fillStyle = faint; g.globalAlpha = 1; }
          arc(i * wedge + pad / 2, (i + 1) * wedge - pad / 2, ri, ri + segH);
        }
      });
      if (calm() && bars.some((b) => b.h > 0)) bars.forEach((b) => { b.h = Math.max(0, b.h - 0.6 * dt); });
      g.globalAlpha = 1;
      // degree labels
      g.font = `11px ${font}`; g.fillStyle = muted; g.textAlign = "center";
      for (let a = 0; a <= 180; a += 30) {
        const t = Math.PI + (a * Math.PI) / 180, x = cx + Math.cos(t) * (R + 30), y = cy + Math.sin(t) * (R + 30);
        g.fillText(`${a}°`, x, y + 4);
      }
      g.textAlign = "left"; g.fillText("Mic A", 4, cy + 18);
      g.textAlign = "right"; g.fillText("Mic B", W - 4, cy + 18);
      g.textAlign = "center"; g.fillText("ahead", cx, cy + 18);
      // numbered sounds just outside the bars, newest boldest
      marks.forEach((e, i) => {
        const age = marks.length - 1 - i, t = Math.PI + (e.angle * Math.PI) / 180;
        const x = cx + Math.cos(t) * (R + 14), y = cy + Math.sin(t) * (R + 14);
        g.globalAlpha = Math.max(0.3, 1 - age / POLAR.MARKS);
        g.beginPath(); g.arc(x, y, e.id === sel ? 9 : 7.5, 0, 2 * Math.PI);
        g.fillStyle = c.kinds[e.kind] || muted; g.fill();
        if (e.id === sel) { g.lineWidth = 2; g.strokeStyle = ink; g.stroke(); }
        g.fillStyle = "#fff"; g.font = `600 9px ${font}`; g.fillText(String(e.n % 100), x, y + 3);
      });
      g.globalAlpha = 1;
    }
    if (calm()) raf = setTimeout(() => frame(performance.now()), 250); else raf = requestAnimationFrame(frame);
  }
  raf = requestAnimationFrame(frame);
  return {
    hit(a, r, b) { hit(a, r, b); dirty = true; }, setWedge(w) { wedge = w; reset(); dirty = true; },
    marks(list, selected) {
      const key = list.map((e) => e.n).join(",") + "|" + selected;
      if (key !== marks.key) { marks = list; marks.key = key; sel = selected ?? null; dirty = true; }
    },
    stop() { running = false; cancelAnimationFrame(raf); clearTimeout(raf); },
  };
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
    <a class="ep pool-row" href="#/tune/capture/${m.id}">
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
async function renderCapture() {
  clearTimeout(S.tuneTimer);
  const id = Number(route.arg);
  const qs = S.whatIf ? `?${new URLSearchParams(Object.entries(S.whatIf).map(([k, v]) => [k, String(v)]))}` : "";
  let m;
  try { [m] = await Promise.all([api(`/api/missed/${id}${qs}`), loadDogs()]); }
  catch (e) { view.innerHTML = `<div class="notice err">${esc(e.message)}</div>`; return; }
  if (route.name !== "capture") return;
  if (!m.analyzed && m.status === "new") {
    view.innerHTML = `<section><a href="#/tune" class="small">Detection and tuning</a><h1>Scoring the sounds…</h1><p class="sub">Every sound in the capture is measured and checked the way the detector does it. This takes a few seconds.</p></section>`;
    S.tuneTimer = setTimeout(() => { if (route.name === "capture") renderCapture(); }, 1200);
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
      <div class="row spread" style="margin-bottom:14px"><a href="#/tune" class="small">Detection and tuning</a>
        ${m.next_new ? `<a class="btn" href="#/tune/capture/${m.next_new}">Next capture to review</a>` : ""}</div>
      <h1>Heard at ${fmtTimeS(m.start_ts + len)}</h1>
      <p class="sub">${Math.round(len)} seconds saved by “I hear barking” on ${fmtDay(m.start_ts)}.
        ${m.status === "added" ? `Added to the log: <a href="#/clips/${m.episode_id}">open the clip</a>.` : m.status === "dismissed" ? "You marked this as no barking; the audio was deleted, and its measurements still help the suggestions." : "Select the barks the Pi missed, then add them under the right dog."}</p>
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
    api(`/api/missed/${id}/spectrogram?width=900&height=80`).then((sp) => { const c = $("#specCanvas"); if (c) paintSpec(c, sp); }).catch(() => {});
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
        toastLink(`Added ${sel.size} bark${sel.size === 1 ? "" : "s"} to the log`, `#/clips/${r.episode_id}`, "Open clip");
        location.hash = m.next_new ? `#/tune/capture/${m.next_new}` : "#/tune";
      } catch (e) { toast(e.message); }
    });
    $("#dismiss").onclick = async () => {
      if (!confirm("Discard this capture? Its audio is deleted; the measurements are kept to improve suggestions.")) return;
      await api(`/api/missed/${id}/dismiss`, { method: "POST" });
      toast("Discarded"); location.hash = m.next_new ? `#/tune/capture/${m.next_new}` : "#/tune";
    };
  }

  wireWhatIf($("#whatIf"), cur, draw);
  draw();
  const onR = () => { if (route.name === "capture") draw(); };
  window.addEventListener("resize", onR); LOOPS.add(() => window.removeEventListener("resize", onR));
}

PAGES.tune = renderTune;
PAGES.capture = renderCapture;
