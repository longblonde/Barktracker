"use strict";
// Dashboard: the day at a glance, two weeks, when barking happens, where it comes from (when two
// microphones are in use), and a link to the clips waiting for review.

async function renderDashboard(soft = false) {
  if (!S.overviewDate) S.overviewDate = isoToday();
  const date = S.overviewDate;
  if (!soft) view.innerHTML = `<p class="muted">Loading…</p>`;
  loadDogFilter();
  await loadDogs();
  for (const k of [...S.dogFilter]) if (k !== "u" && !S.dogs[k]) S.dogFilter.delete(k);   // a dog was deleted
  const dq = dogsQS();
  const [day, sum, heat, counts, dirs] = await Promise.all([
    api(`/api/day?date=${date}${dq}`), api(`/api/summary?days=14${dq}`), api(`/api/heatmap?days=28${dq}`),
    api(`/api/clips/counts?since=${clipsSeen()}`), api(`/api/bearings?date=${date}&days=${S.dirDays || 1}${dq}`),
  ]);
  if (route.name !== "dashboard") return;
  if (day.labels) RULE_LABELS = day.labels;
  const eps = day.episodes;
  const who = filterLabel();
  const total = eps.reduce((a, e) => a + e.duration * (e.share ?? 1), 0);
  const longest = eps.reduce((a, e) => (e.duration > (a?.duration ?? -1) ? e : a), null);
  const isToday = date === isoToday();
  const when = isToday ? "today" : prettyIso(date) === "Yesterday" ? "yesterday" : `on ${prettyIso(date)}`;

  // per-dog share for the sentence
  const perDog = {};
  for (const e of eps) {
    const tot = Object.values(e.dogs).reduce((a, b) => a + b, 0) || 1;
    for (const [k, n] of Object.entries(e.dogs)) {
      if (S.dogFilter.size && !S.dogFilter.has(k)) continue;
      perDog[k] = (perDog[k] || 0) + (e.duration * n) / tot;
    }
  }
  const top = Object.entries(perDog).sort((a, b) => b[1] - a[1]);
  let headline, detail = "";
  if (!eps.length) {
    headline = who ? `No barking from ${who} ${isToday ? "so far today" : when}.`
      : isToday ? "No barking so far today." : `No barking logged ${when}.`;
    detail = isToday && S.status?.detector ? "Listening. Episodes show up here as soon as they end." : "";
  } else {
    headline = `${who ? `${who} barked` : "Barked"} ${fmtMinutes(total)} ${when} across ${eps.length} episode${eps.length === 1 ? "" : "s"}.`;
    detail = `Longest was ${fmtDur(longest.duration)} at ${fmtTime(longest.start_ts)}.`;
    if (top.length && top[0][0] !== "u" && total > 0 && (!who || top.length > 1)) {
      detail += ` ${dogName(top[0][0])} did ${Math.round((100 * top[0][1]) / total)}% of it.`;
    }
    const hits = (day.incidents || []).filter((i) => i.flags.length).length;
    if (hits) detail += ` ${hits} incident${hits === 1 ? "" : "s"} met your report rules.`;
  }
  const cw = chartWidths();
  const dogsSeen = new Set([...Object.keys(perDog)]);
  let legend = [...dogsSeen].map((k) => `<span style="--c:${dogColor(k)}">${esc(dogName(k))}</span>`).join("");
  if ((day.quiet || []).length) legend += `<span class="nodot"><span class="qkey" aria-hidden="true"></span> Quiet hours, ${clock12(day.quiet_start)} to ${clock12(day.quiet_end)}</span>`;
  if ((day.incidents || []).some((i) => i.flags.length)) legend += `<span style="--c:var(--danger)"><a href="#/report">Meets a report rule</a></span>`;

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
      ${dogFilterBar()}
      <div><h1>${esc(headline)}</h1><p class="sub">${esc(detail)}</p></div>
      <div class="strip-wrap">${stripSvg(day, isToday, cw.strip)}
        <div class="legend" style="margin-top:6px">${legend}</div></div>
    </section>
    <section class="grid2">
      <div class="panel"><h2>Last two weeks${who ? `: ${esc(who)}` : ""}</h2>${barsSvg(sum, cw.bars)}<p class="small muted" style="margin:6px 0 0">Minutes of barking per day. Select a day to see it above.</p></div>
      <div class="panel"><h2>When it happens${who ? `: ${esc(who)}` : ""}</h2>${heatSvg(heat, cw.heat)}</div>
    </section>
    ${dirs.on ? `<section class="panel dirpanel">${bearingPanel(dirs)}</section>` : ""}
    <section>${clipsCard(counts)}</section>`;
  wireDogFilter(() => renderDashboard(true));
  wireHeat(heat);
  view.querySelectorAll("[data-dirdays]").forEach((b) => b.onclick = () => { S.dirDays = Number(b.dataset.dirdays); renderDashboard(true); });
  $("#prevDay").onclick = () => { S.overviewDate = shiftIso(date, -1); renderDashboard(); };
  $("#nextDay").onclick = () => { S.overviewDate = shiftIso(date, 1); renderDashboard(); };
  const tb = $("#todayBtn"); if (tb) tb.onclick = () => { S.overviewDate = isoToday(); renderDashboard(); };
  view.querySelectorAll("[data-day]").forEach((g) => {
    const go = () => { S.overviewDate = g.dataset.day; renderDashboard(); };
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
  g += `<defs><pattern id="qstripe-day" patternUnits="userSpaceOnUse" width="6" height="6" patternTransform="rotate(45)">
    <rect width="2" height="6" fill="var(--panel)"/></pattern></defs>`;
  g += `<rect x="0" y="22" width="${W}" height="30" fill="var(--faint)" rx="3"/>`;
  for (const [a, b] of day.quiet || []) {        // quiet hours: striped stretch of the track
    const x0 = Math.max(0, x(a)), x1 = Math.min(W, x(b));
    if (x1 <= x0) continue;
    g += `<rect x="${x0}" y="22" width="${x1 - x0}" height="30" fill="url(#qstripe-day)"><title>Quiet hours, ${clock12(day.quiet_start)} to ${clock12(day.quiet_end)}</title></rect>`;
  }
  for (const e of day.episodes) {
    const w = Math.max(3, x(e.end_ts) - x(e.start_ts));
    const label = `${fmtTime(e.start_ts)} · ${fmtDur(e.duration)} · ${e.bark_count} barks`;
    g += `<a href="#/clips/${e.id}" aria-label="${esc(label)}"><rect x="${x(e.start_ts)}" y="16" width="${w}" height="42" rx="2" fill="${dogColor(dominant(e.dogs))}"><title>${esc(label)}</title></rect></a>`;
  }
  for (const inc of day.incidents || []) {
    if (!inc.flags.length) continue;
    const w = Math.max(4, x(inc.end_ts) - x(inc.start_ts));
    g += `<rect x="${x(inc.start_ts)}" y="10" width="${w}" height="3" fill="var(--danger)"><title>${esc(inc.flags.map(ruleLabel).join(", "))}</title></rect>`;
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
  const quiet = heat.quiet || [];
  // diagonal stripes in the panel's own color, cut through the cells that fall in quiet hours
  let g = `<defs><pattern id="qstripe" patternUnits="userSpaceOnUse" width="5" height="5" patternTransform="rotate(45)">
    <rect width="1.6" height="5" fill="var(--panel)"/></pattern></defs>`;
  heat.grid.forEach((row, r) => {
    g += `<text x="0" y="${r * ch + 14}">${days[r]}</text>`;
    row.forEach((v, c) => {
      const op = v ? 0.12 + 0.88 * Math.sqrt(v / max) : 0;
      const x = lx + c * cw, y = r * ch + 1, w = cw - 2, h = ch - 3;
      g += `<rect class="hcell" data-r="${r}" data-c="${c}" x="${x}" y="${y}" width="${w}" height="${h}" rx="2" fill="${v ? "var(--ink)" : "var(--faint)"}" fill-opacity="${v ? op.toFixed(2) : 1}"/>`;
      for (const [a, b] of quiet[c] || []) {
        g += `<rect class="hq" x="${x + a * w}" y="${y}" width="${(b - a) * w}" height="${h}" rx="${a === 0 && b === 1 ? 2 : 0}" fill="url(#qstripe)"/>`;
      }
    });
  });
  for (const hr of [0, 6, 12, 18]) g += `<text x="${lx + hr * cw}" y="${H - 2}">${hr === 0 ? "12a" : hr === 12 ? "12p" : hr > 12 ? hr - 12 + "p" : hr + "a"}</text>`;
  // hover: thin white border, with a hairline of ink outside it so it also shows on a light panel
  g += `<rect class="hfocus hring" x="0" y="0" width="0" height="0" rx="3.5" fill="none" stroke="var(--ink)" stroke-width="1" visibility="hidden"/>`;
  g += `<rect class="hfocus hwhite" x="0" y="0" width="0" height="0" rx="2.5" fill="none" stroke="#fff" stroke-width="1.5" visibility="hidden"/>`;
  return `<div class="heat-wrap"><svg class="heat" viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Barking by weekday and hour. Striped cells are quiet hours.">${g}</svg><div class="heat-info" aria-live="polite">${heatCaption(heat)}</div></div>`;
}

function heatInfoHtml(heat, r, c, pinned) {
  const n = heat.weekdays?.[r] || 0, secs = heat.grid[r][c], barks = heat.barks?.[r]?.[c] || 0, eps = heat.episodes?.[r]?.[c] || 0;
  const q = heat.quiet?.[c] || [];
  const qFull = q.some(([a, b]) => a === 0 && b === 1), qPart = !qFull && q.length;
  const dogs = Object.entries(heat.dogs?.[r]?.[c] || {}).sort((a, b) => b[1] - a[1]);
  const qText = qFull ? `<span class="hi-quiet">Quiet hours</span>`
    : qPart ? `<span class="hi-quiet">Quiet hours ${q[0][0] > 0 ? `from ${clock12(heat.quiet_start)}` : `until ${clock12(heat.quiet_end)}`}</span>` : "";
  return `<div class="hi-head"><strong>${DAY_NAMES[r]}s, ${hourName(c)} to ${hourName(c + 1)}</strong>${qText}
      ${pinned ? `<button type="button" class="linkish hi-close">Close</button>` : ""}</div>
    <div class="hi-body">${secs || barks
      ? `<span><b>${fmtDur(secs)}</b> of barking</span><span><b>${barks}</b> bark${barks === 1 ? "" : "s"}</span><span><b>${eps}</b> episode${eps === 1 ? "" : "s"}</span>`
      : `<span class="muted">No barking</span>`}<span class="muted">over ${n} ${DAY_NAMES[r]}${n === 1 ? "" : "s"}</span></div>
    ${dogs.length ? `<div class="hi-dogs">${dogs.map(([k, v]) => `<span class="tag" style="--c:${dogColor(k)}"><i></i>${esc(dogName(k))} ×${v}</span>`).join("")}</div>` : ""}`;
}
function heatCaption(heat) {
  return `<p class="small muted hi-cap">Past 4 weeks by hour. Darker means more barking. <span class="qkey" aria-hidden="true"></span> Striped: quiet hours, ${clock12(heat.quiet_start)} to ${clock12(heat.quiet_end)}. Point at or tap an hour for details.</p>`;
}

function wireHeat(heat) {
  const wrap = view.querySelector(".heat-wrap"); if (!wrap) return;
  const svg = wrap.querySelector("svg"), info = wrap.querySelector(".heat-info");
  const white = svg.querySelector(".hwhite"), ring = svg.querySelector(".hring");
  let cur = null, pinned = null;
  const outline = (cell) => {
    if (!cell) { white.setAttribute("visibility", "hidden"); ring.setAttribute("visibility", "hidden"); return; }
    const x = +cell.getAttribute("x"), y = +cell.getAttribute("y"), w = +cell.getAttribute("width"), h = +cell.getAttribute("height");
    Object.entries({ x: x + 0.75, y: y + 0.75, width: w - 1.5, height: h - 1.5 }).forEach(([k, v]) => white.setAttribute(k, v));
    Object.entries({ x: x - 1, y: y - 1, width: w + 2, height: h + 2 }).forEach(([k, v]) => ring.setAttribute(k, v));
    white.setAttribute("visibility", "visible"); ring.setAttribute("visibility", "visible");
  };
  const show = (cell) => {
    if (cur === cell) return;
    cur = cell;
    outline(cell);
    if (!cell) { info.innerHTML = heatCaption(heat); return; }
    info.innerHTML = heatInfoHtml(heat, Number(cell.dataset.r), Number(cell.dataset.c), cell === pinned);
    const x = info.querySelector(".hi-close");
    if (x) x.onclick = () => { pinned = null; cur = undefined; show(null); };
  };
  const cellAt = (e) => e.target.closest?.(".hcell");
  svg.addEventListener("pointermove", (e) => { if (e.pointerType === "mouse" && !pinned) show(cellAt(e) || null); });
  svg.addEventListener("pointerleave", () => { if (!pinned) show(null); });
  svg.addEventListener("click", (e) => {           // click or tap keeps an hour's details up
    const cell = cellAt(e);
    pinned = cell && cell !== pinned ? cell : null;
    cur = undefined; show(pinned || (e.pointerType === "mouse" ? cell : null) || null);
  });
}

// --- clips waiting: counted since you last opened the Clips page (kept per browser)
function clipsSeen() { try { return Number(localStorage.getItem("bt.clipsSeen")) || 0; } catch (_) { return 0; } }
function clipsCard(c) {
  const seen = clipsSeen();
  return `<a class="panel clipscard" href="#/clips">
    <span class="cc-n">${c.to_review}</span>
    <span class="cc-txt"><strong>${c.to_review === 1 ? "clip needs" : "clips need"} review</strong>
      <span class="muted small">${seen && c.new ? `${c.new} new since you last looked · ` : ""}${c.total} clip${c.total === 1 ? "" : "s"} in all</span></span>
    <span class="cc-go">Open clips →</span></a>`;
}

// --- where barking came from: wedges by direction, each colored by the dog heard most there
function bearingPanel(d) {
  const days = S.dirDays || 1;
  const head = `<div class="row spread"><h2>Where it comes from</h2>
    <span class="seg" role="radiogroup" aria-label="Period"><button type="button" role="radio" data-dirdays="1" aria-checked="${days === 1}">This day</button><button type="button" role="radio" data-dirdays="7" aria-checked="${days === 7}">7 days</button><button type="button" role="radio" data-dirdays="30" aria-checked="${days === 30}">30 days</button></span></div>`;
  if (!d.barks.length) return head + `<p class="small muted">${d.total ? "No barks with a clear direction in this period." : "No barking in this period."} Directions come from the two microphones (Settings → Microphone).</p>`;
  const N = 18, bins = Array.from({ length: N }, () => ({}));
  for (const [b, dog] of d.barks) { const k = dog == null ? "u" : String(dog); const i = Math.min(N - 1, Math.floor(b / (180 / N))); bins[i][k] = (bins[i][k] || 0) + 1; }
  const tot = bins.map((m) => Object.values(m).reduce((a, x) => a + x, 0)), mx = Math.max(1, ...tot);
  const W = 360, H = 200, cx = W / 2, cy = H - 18, R = 160, r0 = 26;
  const pt = (deg, rr) => [cx - Math.cos((deg * Math.PI) / 180) * rr, cy - Math.sin((deg * Math.PI) / 180) * rr];
  let g = "";
  for (const f of [0.33, 0.66, 1]) g += `<path d="M${cx - (r0 + (R - r0) * f)},${cy} A${r0 + (R - r0) * f},${r0 + (R - r0) * f} 0 0 1 ${cx + r0 + (R - r0) * f},${cy}" fill="none" stroke="var(--faint)"/>`;
  bins.forEach((m, i) => {
    if (!tot[i]) return;
    let acc = r0;
    for (const [k, n] of Object.entries(m).sort((a, b) => b[1] - a[1])) {     // stacked by dog
      const rr = acc + ((R - r0) * n) / mx, a0 = i * (180 / N) + 0.8, a1 = (i + 1) * (180 / N) - 0.8;
      const [x0, y0] = pt(a0, acc), [x1, y1] = pt(a0, rr), [x2, y2] = pt(a1, rr), [x3, y3] = pt(a1, acc);
      g += `<path d="M${x0},${y0} L${x1},${y1} A${rr},${rr} 0 0 1 ${x2},${y2} L${x3},${y3} A${acc},${acc} 0 0 0 ${x0},${y0} Z" fill="${dogColor(k)}"><title>${Math.round(i * 180 / N)}–${Math.round((i + 1) * 180 / N)}°: ${esc(dogName(k))} ×${n}</title></path>`;
      acc = rr;
    }
  });
  for (const a of [0, 45, 90, 135, 180]) { const [x, y] = pt(a, R + 10); g += `<text x="${x}" y="${y + 4}" text-anchor="middle">${a}°</text>`; }
  const seen = {}; for (const [, dog] of d.barks) { const k = dog == null ? "u" : String(dog); seen[k] = (seen[k] || 0) + 1; }
  const legend = Object.entries(seen).sort((a, b) => b[1] - a[1]).map(([k, n]) => `<span style="--c:${dogColor(k)}">${esc(dogName(k))} ${n}</span>`).join("");
  return head + `<div class="dirwrap"><svg viewBox="0 0 ${W} ${H}" class="dirchart" role="img" aria-label="Barks by direction">${g}<text x="6" y="${cy + 14}">Mic A</text><text x="${W - 6}" y="${cy + 14}" text-anchor="end">Mic B</text></svg>
    <div><div class="legend">${legend}</div><p class="small muted">${d.barks.length} of ${d.total} barks had a clear direction. Each wedge is 10°; longer means more barks from that way. A dog that stays in one wedge is easy to tell apart.</p></div></div>`;
}

PAGES.dashboard = renderDashboard;
let resizeT;
window.addEventListener("resize", () => { clearTimeout(resizeT); resizeT = setTimeout(() => { if (route.name === "dashboard") renderDashboard(true); }, 250); });
