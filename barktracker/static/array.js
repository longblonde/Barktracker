"use strict";
// EXPERIMENTAL: dual-microphone direction test page (#/array). Shown only when switched on under
// Settings > System. Self-contained on purpose so it can be reworked or removed without touching
// the rest of the dashboard. Uses app.js helpers at call time (api, $, esc, toast, view, route, S, LUT).

const ARR = { N: 1200, FRAME: 800, keep: 40, MAX: 200, MAX_AGE: 15 * 60, MAX_TAGS: 80 };
const ARR_KIND = {
  bark: ["Bark", "var(--ok-bark)"], not_bark: ["Not bark-like", "var(--caution)"],
  quiet: ["Too quiet", "var(--muted)"], filtered: ["Filtered", "var(--danger)"],
};

async function renderArray() {
  clearTimeout(S.arrTimer);
  let st;
  try { st = await api("/api/settings"); } catch (e) { view.innerHTML = `<div class="notice err">${esc(e.message)}</div>`; return; }
  if (route.name !== "array") return;
  const v = st.values;
  if (!v["dev.array"]) {
    view.innerHTML = `<section><h1>Direction test</h1><p class="sub">This experimental page is off. Turn it on under <a href="#/settings/system">Settings → System</a>.</p></section>`;
    return;
  }
  const first = await api("/api/array/feed?since=0");
  if (route.name !== "array") return;
  const devs = first.devices || [];
  view.innerHTML = `
    <section class="arr-head">
      <div><h1>Direction test <span class="exp-pill">experimental</span></h1>
        <p class="sub">Estimates which way each sound came from, using two microphones. It only watches: nothing here changes what's detected or logged.</p></div>
    </section>
    <section class="panel arr-polar-panel">
      <div class="row spread"><h2>Bearing</h2><span class="row"><span class="small muted" id="arrStatus"></span><button type="button" class="btn" id="arrReset">Clear chart</button></span></div>
      <canvas id="arrPolar" class="arr-polar" aria-label="Estimated direction of recent sounds, 0 to 180 degrees"></canvas>
      <div class="legend">${Object.values(ARR_KIND).map(([t, c]) => `<span style="--c:${c}">${t}</span>`).join("")}<span class="nodot"><i class="arr-hist-key"></i><span id="arrHistKey">Barks so far (spread)</span></span></div>
      <p class="small muted" style="margin:6px 0 0">0° points at mic A, 180° at mic B, 90° is straight ahead (or straight behind: two mics can't tell front from back). Newest sounds sit on the outer ring. A kennel that doesn't move should land in the same place every time.</p>
    </section>
    <section class="panel tl-panel">
      <div class="row spread"><h2>Live</h2><span class="small muted" id="arrLive"></span></div>
      <div class="tl">
        <canvas class="tl-spec" id="arrSpec"></canvas>
        <div class="tl-lane" id="arrLane"></div>
      </div>
      <div class="tl-axis"><span>−60 s</span><span>now</span></div>
      <div class="tl-detail" id="arrDetail"><span class="muted">Each sound gets a number here and on the bearing chart above. Point at or tap one for the numbers.</span></div>
    </section>
    <section class="grid2 tune-grid">
      <div class="panel"><h2>Setup</h2>
        <form id="arrForm" class="arr-form">
          <fieldset class="arr-modes"><legend class="small muted">Microphones</legend>
            <label class="check"><input type="radio" name="dev.array_mode" value="stereo" ${v["dev.array_mode"] !== "pair" ? "checked" : ""}> Stereo: the main microphone's two channels <span class="help">The C920 webcam has two capsules a few cm apart, on one clock. Good timing, coarse angle.</span></label>
            <label class="check"><input type="radio" name="dev.array_mode" value="pair" ${v["dev.array_mode"] === "pair" ? "checked" : ""}> Pair: main microphone + a second USB microphone <span class="help">Spread them a metre or more apart for a finer angle. Two clocks, so calibrate first and again now and then.</span></label>
          </fieldset>
          <label>Second microphone (pair)
            <select name="dev.array_device"><option value="">Choose…</option>${devs.map((d) => `<option ${d === v["dev.array_device"] ? "selected" : ""}>${esc(d)}</option>`).join("")}${v["dev.array_device"] && !devs.includes(v["dev.array_device"]) ? `<option selected>${esc(v["dev.array_device"])}</option>` : ""}</select></label>
          <label>Distance between the microphones<span class="unit"><input type="number" name="dev.array_spacing_cm" value="${v["dev.array_spacing_cm"]}" min="0.5" max="2000" step="0.5">cm</span>
            <span class="help">Measure it. For the webcam, the distance between its two mic holes.</span></label>
          <label class="check"><input type="checkbox" name="dev.array_swap" ${v["dev.array_swap"] ? "checked" : ""}> Mic A is on the other side (mirror the chart)</label>
          <div class="row"><button class="btn primary" type="submit">Save</button></div>
        </form>
      </div>
      <div class="panel"><h2>Calibrate (pair)</h2>
        <p class="small">The two microphones' clocks are never in step. Stand straight ahead of the pair, halfway between them, press <strong>Calibrate</strong>, then clap once. That sound defines 90°.</p>
        <div class="row"><button class="btn" id="arrCal">Calibrate</button><button class="btn" id="arrCalClear">Clear</button></div>
        <p class="small muted" id="arrCalState"></p>
        <h2 style="margin-top:18px">How to read it</h2>
        <ul class="small arr-notes">
          <li><strong>Clarity</strong> under 4 means no clear match between the mics (echo, two sounds at once, wind): those points are left off the chart.</li>
          <li><strong>Level difference</strong> (A − B) needs no calibration: a dish-aimed mic shows sounds from its direction much louder.</li>
          <li>Two microphones give an angle, not a distance.</li>
        </ul>
      </div>
    </section>
    <section class="panel"><h2>Recent estimates</h2><div class="tablewrap"><table class="cand-table" id="arrTable"></table></div></section>`;

  const form = $("#arrForm");
  form.onsubmit = async (ev) => {
    ev.preventDefault();
    const vals = {
      "dev.array_mode": form.querySelector('[name="dev.array_mode"]:checked').value,
      "dev.array_device": form.elements["dev.array_device"].value,
      "dev.array_spacing_cm": Number(form.elements["dev.array_spacing_cm"].value),
      "dev.array_swap": form.elements["dev.array_swap"].checked,
    };
    try { await api("/api/settings", { method: "PUT", body: { values: vals } }); toast("Saved; the estimates start afresh"); resetChart(); drawPolar(); drawTable(); }
    catch (e) { toast(e.message); }
  };
  $("#arrCal").onclick = async () => { try { await api("/api/array/calibrate", { method: "POST", body: {} }); toast("Now clap once, straight ahead"); } catch (e) { toast(e.message); } };
  $("#arrCalClear").onclick = async () => { try { await api("/api/array/calibrate", { method: "POST", body: { clear: true } }); toast("Calibration cleared"); } catch (e) { toast(e.message); } };

  // ---- state: only recent estimates are kept (newest 200, last 15 minutes); the spread fan
  // keeps running counts, so it covers the whole visit without holding every sound
  const gen = S.arrGen = (S.arrGen || 0) + 1;       // a newer visit to the page stops this one's loop
  const events = [];                                  // direction estimates, oldest first
  const byId = new Map();                             // detector event id -> estimate
  const bins = new Array(36).fill(0);
  let binTotal = 0;
  function addEvents(list) {
    for (const e of list) {
      events.push(e); byId.set(e.id, e);
      if (e.valid && e.kind === "bark") { bins[Math.min(35, Math.floor(e.angle / 5))]++; binTotal++; }
    }
    const cutoff = Date.now() / 1000 - ARR.MAX_AGE;
    let drop = Math.max(0, events.length - ARR.MAX);
    while (drop < events.length && events[drop].ts < cutoff) drop++;
    if (drop) for (const e of events.splice(0, drop)) byId.delete(e.id);
  }
  function resetChart() { events.length = 0; byId.clear(); bins.fill(0); binTotal = 0; sel = null; }
  addEvents(first.events);
  let since = first.events.length ? first.events[first.events.length - 1].n : 0, status = first.status;
  const live = { last: -1, since: 0, ev: [] };
  let sel = null;
  const off = document.createElement("canvas");
  off.width = ARR.N; off.height = 64;
  const octx = off.getContext("2d");
  octx.fillStyle = "rgb(15,20,24)"; octx.fillRect(0, 0, ARR.N, 64);

  function drawStatus() {
    const s = status;
    const bits = s.mode === "stereo"
      ? [s.stereo_live ? `stereo input live (${s.main_channels} ch @ ${s.main_rate} Hz)` : s.main_channels === 1 ? "the main microphone is mono: stereo mode needs a two-channel input" : "waiting for stereo audio"]
      : [s.second_live ? `second mic live: ${s.second_name} @ ${s.second_rate} Hz` : s.device_b ? `opening ${s.device_b}…` : "choose the second microphone below",
         s.offset_ms ? `calibrated offset ${s.offset_ms} ms` : "not calibrated"];
    bits.push(`spacing ${s.spacing_cm} cm (max delay ${Math.round(s.max_delay_us)} µs)`);
    $("#arrStatus").textContent = bits.join(" · ");
    $("#arrCalState").textContent = s.mode !== "pair" ? "Only needed in pair mode." : s.calibrating ? "Listening for the clap…" : s.offset_ms ? `Offset ${s.offset_ms} ms.` : "Not calibrated yet.";
    if (s.error) $("#arrStatus").textContent += ` · ${s.error}`;
  }

  function drawPolar() {
    const c = $("#arrPolar"); if (!c) return;
    const dpr = window.devicePixelRatio || 1, W = c.clientWidth, H = c.clientHeight;
    if (c.width !== Math.round(W * dpr) || c.height !== Math.round(H * dpr)) { c.width = Math.round(W * dpr); c.height = Math.round(H * dpr); }
    const g = c.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0);
    const css = getComputedStyle(document.body), ink = css.getPropertyValue("--ink"), muted = css.getPropertyValue("--muted"), faint = css.getPropertyValue("--faint");
    const cx = W / 2, cy = H - 24, R = Math.min(W / 2 - 40, H - 44);
    const pt = (deg, r) => [cx - Math.cos((deg * Math.PI) / 180) * r, cy - Math.sin((deg * Math.PI) / 180) * r];
    g.font = `12px ${css.fontFamily}`;
    g.clearRect(0, 0, W, H);
    // spread of every clear bark estimate this visit: 5 degree wedges (running counts)
    const mx = Math.max(1, ...bins);
    const hk = $("#arrHistKey"); if (hk) hk.textContent = `Barks so far (${binTotal})`;
    g.fillStyle = css.getPropertyValue("--ok-bark"); g.globalAlpha = 0.18;
    bins.forEach((n, i) => {
      if (!n) return;
      const r = R * 0.25 + (R * 0.75 * n) / mx;
      g.beginPath(); g.moveTo(cx, cy);
      g.arc(cx, cy, r, Math.PI + (i * 5 * Math.PI) / 180, Math.PI + ((i + 1) * 5 * Math.PI) / 180);
      g.closePath(); g.fill();
    });
    g.globalAlpha = 1;
    // grid
    g.strokeStyle = faint; g.lineWidth = 1;
    for (const f of [0.25, 0.5, 0.75, 1]) { g.beginPath(); g.arc(cx, cy, R * f, Math.PI, 2 * Math.PI); g.stroke(); }
    g.fillStyle = muted;
    for (let a = 0; a <= 180; a += 30) {
      const [x0, y0] = pt(a, R * 0.25), [x1, y1] = pt(a, R);
      g.beginPath(); g.moveTo(x0, y0); g.lineTo(x1, y1); g.stroke();
      const [tx, ty] = pt(a, R + 16);
      g.textAlign = "center"; g.fillText(`${a}°`, tx, ty + 4);
    }
    g.textAlign = "left"; g.fillText("Mic A", 4, cy + 18);
    g.textAlign = "right"; g.fillText("Mic B", W - 4, cy + 18);
    g.textAlign = "center"; g.fillText("ahead", cx, cy + 18);
    g.beginPath(); g.fillStyle = ink; g.arc(cx, cy, 3, 0, 2 * Math.PI); g.fill();
    // recent points: newest on the outer ring
    const recent = events.filter((e) => e.valid).slice(-ARR.keep);
    recent.forEach((e, i) => {
      const age = recent.length - 1 - i, r = R * (1 - (age / ARR.keep) * 0.72);
      const [x, y] = pt(e.angle, r), col = (ARR_KIND[e.kind] || ARR_KIND.quiet)[1];
      g.globalAlpha = Math.max(0.25, 1 - age / ARR.keep);
      g.beginPath(); g.arc(x, y, e.n === sel ? 12 : 10, 0, 2 * Math.PI);
      g.fillStyle = css.getPropertyValue(col.slice(4, -1)) || col; g.fill();
      if (e.n === sel) { g.lineWidth = 2; g.strokeStyle = ink; g.stroke(); g.lineWidth = 1; }
      g.fillStyle = "#fff"; g.font = `600 10px ${css.fontFamily}`; g.textAlign = "center";
      g.fillText(String(e.n), x, y + 3.5);
      g.font = `12px ${css.fontFamily}`;
    });
    g.globalAlpha = 1;
  }

  function xOf(idx, W) { return W - ((live.last - idx) / ARR.FRAME) * (W / ARR.N); }
  function drawLane() {
    const spec = $("#arrSpec"), lane = $("#arrLane"); if (!spec) return;
    const W = spec.clientWidth, dpr = window.devicePixelRatio || 1;
    if (spec.width !== Math.round(W * dpr)) { spec.width = Math.round(W * dpr); spec.height = Math.round(spec.clientHeight * dpr); }
    spec.getContext("2d").drawImage(off, 0, 0, spec.width, spec.height);
    let right = -Infinity;
    lane.innerHTML = live.ev.slice(-ARR.MAX_TAGS).map((de) => {
      const e = byId.get(de.id), x = xOf(de.idx, W);
      if (x < 0) return "";
      const kind = ARR_KIND[de.kind] || ARR_KIND.quiet;
      const txt = e ? (e.valid ? `#${e.n} ${Math.round(e.angle)}°` : `#${e.n} ?`) : "…";
      const w = txt.length * 6.5 + 14, dot = x - w / 2 < right + 3;
      right = dot ? Math.max(right, x + 6) : x + w / 2;
      return `<button type="button" class="tag-ev k-${de.kind}${dot ? " dot" : ""}${e && e.n === sel ? " sel" : ""}" data-n="${e ? e.n : ""}" style="left:${x}px;--c:${kind[1]}" title="${esc(txt)}">${esc(txt)}</button>`;
    }).join("");
  }
  function drawTable() {
    const t = $("#arrTable"); if (!t) return;
    const rows = events.slice(-25).reverse();
    t.innerHTML = `<thead><tr><th>#</th><th>At</th><th>Sound</th><th>Angle</th><th>Delay</th><th>Clarity</th><th>Level A / B</th><th>Difference</th></tr></thead>
      <tbody>${rows.map((e) => `<tr class="${e.n === sel ? "sel" : ""}"><td>${e.n}</td><td>${fmtTimeS(e.ts)}</td>
        <td><span class="kind-pill" style="--c:${(ARR_KIND[e.kind] || ARR_KIND.quiet)[1]}">${(ARR_KIND[e.kind] || ARR_KIND.quiet)[0]}</span></td>
        <td class="num">${e.valid ? `${Math.round(e.angle)}°` : `<span class="muted">${Math.round(e.angle)}° ?</span>`}</td>
        <td class="num">${Math.round(e.tau_us)} µs</td><td class="num">${e.sharp}</td>
        <td class="num">${Math.round(e.level_a)} / ${Math.round(e.level_b)} dBFS</td><td class="num">${e.ild_db > 0 ? "+" : ""}${e.ild_db} dB</td></tr>`).join("")
        || `<tr><td colspan="8" class="muted">No sounds yet. Estimates appear about a second after each sound.</td></tr>`}</tbody>`;
  }
  function showDetail(n) {
    const e = events.find((x) => x.n === n); if (!e) return;
    sel = n;
    $("#arrDetail").innerHTML = `<div><strong>#${e.n}</strong> ${fmtTimeS(e.ts)} <span class="kind-pill" style="--c:${(ARR_KIND[e.kind] || ARR_KIND.quiet)[1]}">${(ARR_KIND[e.kind] || ARR_KIND.quiet)[0]}</span></div>
      <div>${e.valid ? `About <strong>${Math.round(e.angle)}°</strong>` : `No clear direction (best guess ${Math.round(e.angle)}°)`}: mic B heard it ${Math.abs(Math.round(e.tau_us))} µs ${e.tau_us >= 0 ? "after" : "before"} mic A; clarity ${e.sharp}.</div>
      <div class="small muted">Level A ${Math.round(e.level_a)} dBFS, B ${Math.round(e.level_b)} dBFS (A − B = ${e.ild_db} dB)${e.mode === "pair" ? `; raw delay ${Math.round(e.raw_us)} µs before calibration` : ""}.</div>`;
    drawPolar(); drawLane(); drawTable();
  }
  $("#arrLane").addEventListener("click", (ev) => { const t = ev.target.closest?.(".tag-ev"); if (t && t.dataset.n) showDetail(Number(t.dataset.n)); });
  $("#arrLane").addEventListener("pointermove", (ev) => { const t = ev.target.closest?.(".tag-ev"); if (t && t.dataset.n && sel == null) showDetail(Number(t.dataset.n)); });

  async function poll() {
    if (route.name !== "array" || gen !== S.arrGen) return;
    if (document.hidden) { S.arrTimer = setTimeout(poll, 1000); return; }
    try {
      const [f, a] = await Promise.all([api(`/api/tuning/feed?since_idx=${live.last}&since_event=${live.since}`), api(`/api/array/feed?since=${since}`)]);
      status = a.status;
      addEvents(a.events);
      if (a.events.length) since = a.events[a.events.length - 1].n;
      if (f.listening) {
        const raw = atob(f.cols || ""), n = f.levels.length;
        if (n) {
          const k = Math.min(n, ARR.N);
          octx.drawImage(off, k, 0, ARR.N - k, 64, 0, 0, ARR.N - k, 64);
          const img = octx.createImageData(k, 64);
          for (let c = n - k; c < n; c++) for (let r = 0; r < 64; r++) {
            const v = raw.charCodeAt(c * 64 + r) * 3, px = ((63 - r) * k + (c - (n - k))) * 4;
            img.data[px] = LUT[v]; img.data[px + 1] = LUT[v + 1]; img.data[px + 2] = LUT[v + 2]; img.data[px + 3] = 255;
          }
          octx.putImageData(img, ARR.N - k, 0);
          live.last = f.last_idx; ARR.FRAME = f.frame || 800;
        }
        for (const e of f.events) if (!e.soft) live.ev.push(e);
        if (f.events.length) live.since = f.events[f.events.length - 1].id;
        live.ev = live.ev.filter((e) => e.idx > live.last - ARR.N * ARR.FRAME);
        if (live.ev.length > 400) live.ev = live.ev.slice(-400);
        $("#arrLive").textContent = `Background ${Math.round(f.floor)} dBFS`;
      } else $("#arrLive").textContent = "Not listening";
      drawStatus(); drawPolar(); drawLane();
      if (a.events.length) drawTable();
    } catch (_) { $("#arrStatus").textContent = "Can't reach the Pi; retrying…"; }
    if (gen === S.arrGen) S.arrTimer = setTimeout(poll, 300);
  }
  drawTable();
  $("#arrReset").onclick = () => { resetChart(); drawPolar(); drawTable(); $("#arrDetail").innerHTML = `<span class="muted">Chart cleared.</span>`; };
  S.arrResize = () => { drawPolar(); drawLane(); };
  if (!S.arrResizeWired) { S.arrResizeWired = true; window.addEventListener("resize", () => { if (route.name === "array" && S.arrResize) S.arrResize(); }); }
  poll();
}

// show or hide the menu link
async function arrayNav(on) {
  const a = document.getElementById("navArray"); if (!a) return;
  if (on === undefined) { try { on = (await api("/api/settings")).values["dev.array"]; } catch (_) { return; } }
  a.hidden = !on;
}
