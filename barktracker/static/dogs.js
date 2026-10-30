"use strict";
// Dogs: the dogs you've named, grouped by household; dogs the Pi keeps hearing that you haven't
// named yet; and for each dog its voice, where it barks from and its rhythm.

async function renderDogs() {
  if (route.arg) return renderDog(Number(route.arg));
  const r = await loadDogs();
  if (route.name !== "dogs") return;
  const need = r.min_labels;
  const homes = householdGroups(r.dogs);
  view.innerHTML = `
    <section class="dogs-head">
      <div><h1>Dogs</h1>
      <p class="sub" style="max-width:66ch">Name each dog you hear and put it in a household (the address it lives at). Label a few of its barks; after ${need} labelled barks the Pi matches new barks to it by voice${r.direction_on ? " and by where they come from" : ""}. Reports can cover one dog or a whole household.</p></div>
      ${r.direction_on ? `<label class="check dirmatch"><input type="checkbox" id="useDir" ${r.use_direction ? "checked" : ""}> Use direction when matching
        <span class="help">A bark from where a dog usually barks counts toward that dog. Learned only from barks you labelled.</span></label>` : ""}
    </section>
    <datalist id="addrList">${homes.filter((g) => g.key).map((g) => `<option value="${esc(g.address)}">`).join("")}</datalist>
    ${r.dogs.length ? homes.map((g) => householdHtml(g, r, need)).join("") : `<section class="list"><div class="empty"><strong>No dogs yet</strong>Add one below, or name one the Pi has found. Barks are still logged without dogs; they show as unidentified.</div></section>`}
    <section>
      <form class="addform panel" id="addDog">
        <label>Dog's name<input type="text" id="newName" required maxlength="40" placeholder="Rex"></label>
        <label>Size<select id="newSize"><option value="">Not sure</option><option value="small">Small</option><option value="medium">Medium</option><option value="large">Large</option></select></label>
        <label>Household (address)<input type="text" id="newAddr" list="addrList" maxlength="120" placeholder="123 Elm St"></label>
        <label>Owner<input type="text" id="newOwner" maxlength="80" placeholder="optional"></label>
        <button class="btn primary" type="submit">Add dog</button>
      </form>
    </section>
    <section id="discovered"><h2>Dogs you haven't named <span class="count" id="discCount"></span></h2><p class="small muted">Looking for groups of unidentified barks that sound alike…</p></section>
    <section class="panel">
      <h2>Ignoring false alarms</h2>
      <p class="small muted" style="margin:0">${r.not_bark_examples} sound${r.not_bark_examples === 1 ? "" : "s"} marked “Not a bark”. ${S.status?.identifier?.ready ? "Similar sounds are dropped before they're logged." : `Mark at least ${need} to start filtering them out.`} Review them on the <a href="#/clips" id="reviewNb">Clips</a> page with “Hide clips that aren't barking” unticked.</p>
    </section>`;
  wireDogs(r);
  loadDiscovered(r);
}

function householdGroups(dogs) {
  const m = new Map();
  for (const d of dogs) {
    const addr = (d.address || "").trim(), key = addr.toLowerCase();
    if (!m.has(key)) m.set(key, { key, address: addr, owner: "", dogs: [] });
    const g = m.get(key);
    g.dogs.push(d);
    if (!g.owner && d.owner) g.owner = d.owner;
  }
  return [...m.values()].sort((a, b) => (a.key === "") - (b.key === "") || a.address.localeCompare(b.address));
}

function householdHtml(g, r, need) {
  return `<section class="household" data-home="${esc(g.address)}">
    <div class="home-head">
      ${g.key ? `<h2>${esc(g.address)}</h2><span class="muted small">${g.owner ? esc(g.owner) + " · " : ""}${g.dogs.length} dog${g.dogs.length === 1 ? "" : "s"}</span>
        <button type="button" class="linkish small" data-edithome>Rename or set owner</button>
        <a class="small" href="#/report" data-reporthome="${esc(g.address)}">Household report</a>`
      : `<h2>No household yet</h2><span class="muted small">Set an address on a dog to group it with its housemates.</span>`}
    </div>
    <div class="list">${g.dogs.map((d) => dogCard(d, need)).join("")}</div>
  </section>`;
}

function dogCard(d, need) {
  return `<div class="dogcard" style="--c:${d.color}" data-dog="${d.id}">
    <input type="color" value="${d.color}" data-color="${d.id}" aria-label="Color for ${esc(d.name)}" title="Color used for ${esc(d.name)} everywhere">
    <div>
      <input class="name" value="${esc(d.name)}" data-field="name" data-id="${d.id}" aria-label="Name">
      <div class="small muted">${d.labelled_barks} labelled, ${d.predicted_barks} matched automatically</div>
      <div class="meter" title="${Math.min(d.labelled_barks, need)} of ${need} labels needed to start matching"><b style="width:${Math.min(100, (100 * d.labelled_barks) / need)}%"></b></div>
      <div class="dogfields">
        <label>Size <select data-field="size" data-id="${d.id}">${[["", "Not sure"], ["small", "Small"], ["medium", "Medium"], ["large", "Large"]].map(([v, t]) => `<option value="${v}" ${(d.size || "") === v ? "selected" : ""}>${t}</option>`).join("")}</select></label>
        <label>Household <input type="text" list="addrList" value="${esc(d.address)}" data-field="address" data-id="${d.id}" placeholder="Address"></label>
        <label>Owner <input type="text" value="${esc(d.owner)}" data-field="owner" data-id="${d.id}" placeholder="optional"></label>
        <label>Breed <input type="text" value="${esc(d.breed || "")}" data-field="breed" data-id="${d.id}" placeholder="if you know it"></label>
        <label class="wide2">Description <input type="text" value="${esc(d.description)}" data-field="description" data-id="${d.id}" placeholder="Color, markings, size"></label>
      </div>
      <div class="dogtraits">${voiceBlock(d)}${dirBlock(d)}${rhythmBlock(d)}</div>
    </div>
    <div class="row dogactions">
      <a class="btn primary" href="#/dogs/${d.id}">Details</a>
      <a class="btn" href="#/clips" data-filter="${d.id}">Clips</a>
      <a class="btn" href="#/report" data-report="${d.id}">Report</a>
      <button class="btn danger" data-del="${d.id}">Remove</button>
    </div>
  </div>`;
}

function wireDogs(r) {
  $("#addDog").onsubmit = async (ev) => {
    ev.preventDefault();
    const addr = $("#newAddr").value.trim();
    const owner = $("#newOwner").value.trim() || r.dogs.find((d) => (d.address || "").trim().toLowerCase() === addr.toLowerCase() && d.owner)?.owner || "";
    try {
      await api("/api/dogs", { method: "POST", body: { name: $("#newName").value, size: $("#newSize").value, owner, address: addr } });
      toast("Dog added"); renderDogs();
    } catch (e) { toast(e.message.includes("UNIQUE") ? "There's already a dog with that name" : e.message); }
  };
  const ud = $("#useDir");
  if (ud) ud.onchange = async () => {
    await api("/api/settings", { method: "PUT", body: { values: { "identify.use_direction": ud.checked } } });
    toast(ud.checked ? "Direction now counts when matching; re-matching barks" : "Matching by voice only");
  };
  view.querySelectorAll("[data-field]").forEach((i) => i.addEventListener("change", async () => {
    const body = { [i.dataset.field]: i.value };
    if (i.dataset.field === "address" && i.value.trim()) {          // joining a household takes its owner
      const mate = r.dogs.find((d) => String(d.id) !== i.dataset.id && (d.address || "").trim().toLowerCase() === i.value.trim().toLowerCase() && d.owner);
      if (mate && !S.dogs[i.dataset.id]?.owner) body.owner = mate.owner;
    }
    try { await api(`/api/dogs/${i.dataset.id}`, { method: "PATCH", body }); } catch (e) { toast(e.message); return; }
    toast(`${{ name: "Name", size: "Size", address: "Household", owner: "Owner", description: "Description", breed: "Breed" }[i.dataset.field]} saved`);
    if (i.dataset.field === "address" || i.dataset.field === "size") renderDogs(); else loadDogs();
  }));
  view.querySelectorAll("[data-color]").forEach((i) => i.addEventListener("change", async () => {
    await api(`/api/dogs/${i.dataset.color}`, { method: "PATCH", body: { color: i.value } }); renderDogs();
  }));
  view.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async () => {
    const d = S.dogs[b.dataset.del];
    if (!confirm(`Remove ${d.name}? Their barks stay in the log as unidentified.`)) return;
    await api(`/api/dogs/${d.id}`, { method: "DELETE" }); toast(`${d.name} removed`); renderDogs();
  }));
  view.querySelectorAll("[data-edithome]").forEach((b) => b.onclick = () => {     // edit in place (no pop-ups)
    const sec = b.closest(".household"), old = sec.dataset.home, head = sec.querySelector(".home-head");
    if (sec.querySelector(".home-edit")) return;
    const dogs = r.dogs.filter((d) => (d.address || "").trim() === old);
    head.insertAdjacentHTML("afterend", `<div class="row inline-edit home-edit">
      <label class="small muted">Address <input type="text" class="he-addr" value="${esc(old)}" maxlength="120"></label>
      <label class="small muted">Owner <input type="text" class="he-owner" value="${esc(dogs.find((d) => d.owner)?.owner || "")}" maxlength="80" placeholder="optional"></label>
      <button type="button" class="btn primary he-save">Save</button><button type="button" class="btn he-no">Cancel</button></div>`);
    const box = sec.querySelector(".home-edit");
    box.querySelector(".he-addr").focus();
    box.querySelector(".he-no").onclick = () => box.remove();
    box.querySelector(".he-save").onclick = async () => {
      const addr = box.querySelector(".he-addr").value.trim(), owner = box.querySelector(".he-owner").value.trim();
      await Promise.all(dogs.map((d) => api(`/api/dogs/${d.id}`, { method: "PATCH", body: { address: addr, owner } })));
      toast("Household saved"); renderDogs();
    };
  });
  view.querySelectorAll("[data-filter]").forEach((a) => a.addEventListener("click", () => { Object.assign(S.clipFilters, { dog: a.dataset.filter, offset: 0, tag: "" }); }));
  view.querySelectorAll("[data-report]").forEach((a) => a.addEventListener("click", () => { S.report = { ...(S.report || {}), subject: `dog:${a.dataset.report}` }; }));
  view.querySelectorAll("[data-reporthome]").forEach((a) => a.addEventListener("click", () => { S.report = { ...(S.report || {}), subject: `addr:${a.dataset.reporthome}` }; }));
  const rn = $("#reviewNb"); if (rn) rn.onclick = () => { S.clipFilters.hideNb = false; S.clipFilters.tag = "nb"; try { localStorage.setItem("bt.hideNb", "0"); } catch (_) { /* */ } };
}

// ---- dogs the Pi keeps hearing that nobody has named --------------------------------------
async function loadDiscovered(r) {
  let d;
  try { d = await api("/api/dogs/discovered"); } catch (_) { return; }
  const box = $("#discovered"); if (!box || route.name !== "dogs") return;
  $("#discCount").textContent = d.groups.length || "";
  if (!d.groups.length) {
    box.innerHTML = `<h2>Dogs you haven't named</h2><p class="small muted">Nothing new: ${r.unidentified ? `the ${r.unidentified} unidentified bark${r.unidentified === 1 ? "" : "s"} don't form a clear group yet` : "every recent bark is matched to a dog"}. When the Pi keeps hearing a voice it can't match, on several days, it shows up here to listen to and name.</p>`;
    return;
  }
  const opts = Object.values(S.dogs).map((x) => `<option value="${x.id}">${esc(x.name)}</option>`).join("");
  box.innerHTML = `<h2>Dogs you haven't named <span class="count">${d.groups.length}</span></h2>
    <p class="small muted sec-sub">Unidentified barks that sound alike${S.status ? "" : ""} and keep coming back. Listen to the examples; if it's one dog, name it (or say which dog it is). Only the examples are labelled; the rest follow when the Pi re-matches.</p>
    <div class="disc-grid">${d.groups.map((g, i) => `
      <div class="panel disc" data-g="${i}">
        <div class="row spread"><strong>Voice ${String.fromCharCode(65 + i)}</strong><span class="small muted">${g.barks} barks · ${g.episodes} clips · ${g.days} day${g.days === 1 ? "" : "s"}</span></div>
        ${g.profile ? `<div class="voice">${shapeSvg(g.profile.shape, "var(--muted)")}<dl class="voicefacts"><div><dt>Pitch</dt><dd>${fmtHz(g.profile.pitch_hz)}</dd></div><div><dt>Sounds</dt><dd>${SIZE_SOUND[g.profile.sounds_like]}</dd></div>${g.bearing != null ? `<div><dt>From</dt><dd>${Math.round(g.bearing)}°</dd></div>` : ""}</dl></div>` : ""}
        <p class="small muted" style="margin:0">Last heard ${fmtDay(g.last_ts)}, ${fmtTime(g.last_ts)}.</p>
        <div class="row samples">${g.samples.map((s, j) => s.audio ? `<button type="button" class="btn sample" data-s="${j}" title="${fmtDay(s.ts)} ${fmtTimeS(s.ts)}">▶ ${j + 1}</button>` : "").join("")}</div>
        <div class="row disc-act">
          <input type="text" placeholder="Name this dog" data-name maxlength="40" aria-label="Name for this dog">
          <button type="button" class="btn primary" data-new>Add</button>
          ${opts ? `<select data-which aria-label="Or it's one of your dogs"><option value="">…or it's</option>${opts}</select>` : ""}
          <button type="button" class="btn" data-nb title="Mark the examples as “Not a bark”">Not a dog</button>
        </div>
      </div>`).join("")}</div>`;
  box.querySelectorAll(".disc").forEach((el) => {
    const g = d.groups[Number(el.dataset.g)], ids = g.samples.map((s) => s.bark_id);
    el.querySelectorAll(".sample").forEach((b) => b.onclick = () => { const s = g.samples[Number(b.dataset.s)]; playSegment(b, s.episode_id, s.offset, s.duration); });
    const assign = async (body, msg) => {
      try { await api(`/api/dogs/discovered/${g.key}`, { method: "POST", body: { bark_ids: ids, ...body } }); toast(msg); renderDogs(); }
      catch (e) { toast(e.message); }
    };
    el.querySelector("[data-new]").onclick = () => {
      const n = el.querySelector("[data-name]").value.trim();
      if (!n) { el.querySelector("[data-name]").focus(); return toast("Type a name first"); }
      assign({ new_name: n }, `Added ${n} with ${ids.length} example barks`);
    };
    el.querySelector("[data-name]").onkeydown = (e) => { if (e.key === "Enter") el.querySelector("[data-new]").click(); };
    const w = el.querySelector("[data-which]");
    if (w) w.onchange = () => { if (w.value) assign({ dog_id: Number(w.value) }, `Labelled ${ids.length} barks as ${S.dogs[w.value].name}`); };
    el.querySelector("[data-nb]").onclick = () => assign({ dog_id: 0 }, "Marked as not a bark");
  });
}

// play one bark out of a clip, with a little audio either side
function playSegment(btn, eid, offset, dur) {
  player.pause(); resetBtn();
  player.src = `/api/episodes/${eid}/audio`;
  const stopAt = offset + dur + 0.35;
  player.onloadedmetadata = () => { player.currentTime = Math.max(0, offset - 0.25); player.play().catch(() => toast("Couldn't play that")); };
  player.ontimeupdate = () => { if (player.currentTime >= stopAt) { player.pause(); player.ontimeupdate = null; btn.classList.remove("playing"); } };
  btn.classList.add("playing");
}

// ---- traits ------------------------------------------------------------------------------------
function shapeSvg(shape, color) {
  if (!shape?.length) return "";
  const W = 120, H = 28, n = shape.length, lo = -30;
  const pts = shape.map((v, i) => `${((i / (n - 1)) * W).toFixed(1)},${(H - ((Math.max(lo, v) - lo) / -lo) * (H - 2) - 1).toFixed(1)}`).join(" ");
  return `<svg class="shape" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="Spectral shape of the bark, low to high pitch"><polyline points="0,${H} ${pts} ${W},${H}" fill="${color}" fill-opacity=".18" stroke="${color}" stroke-width="1.5"/></svg>`;
}
function voiceBlock(d) {
  const p = d.profile;
  if (!p) return `<div class="trait"><h4>Voice</h4><p class="small muted">Label a few of ${esc(d.name)}'s barks to build a voice profile.</p></div>`;
  const sep = d.separation;
  const mismatch = d.size && p.sounds_like !== d.size && p.sounds_like_share >= 0.6
    ? ` Sounds more like ${SIZE_WORD[p.sounds_like]} than the size you set; that's common, and labels decide matching.` : "";
  return `<div class="trait"><h4>Voice</h4>
    ${shapeSvg(p.shape, d.color)}
    <dl class="voicefacts">
      <div><dt>Pitch</dt><dd>${fmtHz(p.pitch_hz)}</dd></div>
      <div><dt>Tone</dt><dd>${fmtHz(p.brightness_hz)}</dd></div>
      <div><dt>Bark length</dt><dd>${p.length_s.toFixed(2)} s</dd></div>
      <div><dt>Sounds</dt><dd>${SIZE_SOUND[p.sounds_like]}</dd></div>
    </dl>
    <p class="small muted">From ${p.barks} confirmed bark${p.barks === 1 ? "" : "s"}.${sep ? ` Told apart from your other dogs ${Math.round(sep.accuracy * 100)}% of the time${sep.confused_with && sep.accuracy < 0.9 ? `, sometimes mixed up with ${esc(S.dogs[sep.confused_with]?.name || "another dog")}` : ""}.` : ""}${mismatch}</p>
  </div>`;
}
function dirBlock(d) {
  const r = d.direction;
  if (!r) return "";
  const W = 120, H = 64, cx = 60, cy = 60, R = 54;
  const pt = (deg, rr) => [cx - Math.cos((deg * Math.PI) / 180) * rr, cy - Math.sin((deg * Math.PI) / 180) * rr];
  const mx = Math.max(1, ...r.hist);
  let wedges = "";
  r.hist.forEach((n, i) => {
    if (!n) return;
    const rr = 14 + (40 * n) / mx, [x0, y0] = pt(i * 10, rr), [x1, y1] = pt(i * 10 + 10, rr);
    wedges += `<path d="M${cx},${cy} L${x0.toFixed(1)},${y0.toFixed(1)} A${rr},${rr} 0 0 1 ${x1.toFixed(1)},${y1.toFixed(1)} Z" fill="${d.color}" fill-opacity=".55"/>`;
  });
  const [mx2, my2] = pt(r.bearing, R);
  return `<div class="trait"><h4>Where it barks from</h4>
    <svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="Directions ${esc(d.name)}'s barks came from">
      <path d="M${cx - R},${cy} A${R},${R} 0 0 1 ${cx + R},${cy}" fill="none" stroke="var(--faint)"/>${wedges}
      <line x1="${cx}" y1="${cy}" x2="${mx2.toFixed(1)}" y2="${my2.toFixed(1)}" stroke="var(--ink)" stroke-width="1.5"/></svg>
    <p class="small muted">Usually about <strong>${Math.round(r.bearing)}°</strong> (${r.spread < 1 ? "tightly grouped" : `most within ±${Math.round(r.spread)}°`}), from ${r.n} bark${r.n === 1 ? "" : "s"} with a clear direction${r.labelled ? `, ${r.labelled} confirmed` : ""}.</p>
  </div>`;
}
function rhythmBlock(d) {
  const r = d.rhythm;
  if (!r) return "";
  const W = 120, H = 34, n = r.hist.length, mx = Math.max(1, ...r.hist), bw = W / n;
  const bars = r.hist.map((v, i) => `<rect x="${(i * bw + 1).toFixed(1)}" y="${(H - 10 - ((H - 12) * v) / mx).toFixed(1)}" width="${(bw - 2).toFixed(1)}" height="${(((H - 12) * v) / mx).toFixed(1)}" fill="${d.color}" fill-opacity=".7"><title>${r.bins[i]}–${r.bins[i + 1]} s: ${v}</title></rect>`).join("");
  const style = r.singles >= 0.6 ? "mostly single woofs" : r.burst >= 4 ? `bursts of about ${Math.round(r.burst)}` : "short runs";
  return `<div class="trait"><h4>Rhythm</h4>
    <svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="How long ${esc(d.name)} pauses between barks">${bars}<text x="0" y="${H - 1}" style="font-size:9px">0</text><text x="${W}" y="${H - 1}" text-anchor="end" style="font-size:9px">8 s</text></svg>
    <p class="small muted">Usually ${r.pause_s < 1 ? `${Math.round(r.pause_s * 1000)} ms` : `${r.pause_s.toFixed(1)} s`} between barks${r.per_min ? `, about ${Math.round(r.per_min)} a minute while going` : ""}; ${style}.</p>
  </div>`;
}

// ---- one dog in detail -------------------------------------------------------------------------
async function renderDog(id) {
  const days = S.dogDays || 90;
  let r;
  try { [r] = await Promise.all([api(`/api/dogs/${id}/detail?days=${days}`), loadDogs()]); }
  catch (e) { view.innerHTML = `<section><a href="#/dogs" class="small">All dogs</a><div class="notice err">${esc(e.message)}</div></section>`; return; }
  if (route.name !== "dogs" || Number(route.arg) !== id) return;
  const d = r.dog, t = r.totals, full = S.dogs[id] || d, v = r.voice;
  const homeTxt = [d.address, d.owner].filter(Boolean).join(" · ");
  view.innerHTML = `
    <section class="dog-head" style="--c:${d.color}">
      <a href="#/dogs" class="small">All dogs</a>
      <div class="row spread">
        <h1><i class="dogdot"></i>${esc(d.name)}</h1>
        <span class="seg" role="radiogroup" aria-label="Period">${[30, 90, 365].map((n) => `<button type="button" role="radio" data-days="${n}" aria-checked="${days === n}">${n === 365 ? "Year" : `${n} days`}</button>`).join("")}</span>
      </div>
      <p class="sub">${[homeTxt || "No household set", d.breed || "", d.size ? `${d.size} (your setting)` : ""].filter(Boolean).map(esc).join(" · ")}</p>
      <dl class="facts">
        <div><dt>Barking, last ${days === 365 ? "year" : `${days} days`}</dt><dd>${fmtDur(t.recent_seconds)}</dd></div>
        <div><dt>Barks in that time</dt><dd>${t.recent_barks}</dd></div>
        <div><dt>All barks</dt><dd>${t.barks} <span class="small muted">${t.labelled} labelled</span></dd></div>
        <div><dt>First heard</dt><dd class="small">${t.first_ts ? fmtDay(t.first_ts) : "–"}</dd></div>
        <div><dt>Last heard</dt><dd class="small">${t.last_ts ? `${fmtDay(t.last_ts)}, ${fmtTime(t.last_ts)}` : "–"}</dd></div>
      </dl>
    </section>
    <section class="panel"><h2>Timeline</h2>${dogDaily(r.daily, d.color)}<p class="small muted" style="margin:6px 0 0">Minutes of barking per day (this dog's share of each clip).</p></section>
    <section class="grid2">
      <div class="panel"><h2>Time of day</h2>${dogHours(r.hours, d.color)}<p class="small muted" style="margin:6px 0 0">Barks by hour, over the period.</p></div>
      <div class="panel"><h2>Density</h2>${dogWeek(r.week, d.color)}<p class="small muted" style="margin:6px 0 0">Barks by weekday and hour. Darker means more.</p></div>
    </section>
    <section class="panel"><h2>Voice</h2>
      ${v ? `<div class="dogtraits"><div class="solo-trait">${voiceBlock(full)}</div>
        ${histBlock("Pitch", v.pitch, (x) => fmtHz(x), d.color)}${histBlock("Tone (brightness)", v.tone, (x) => fmtHz(x), d.color)}${histBlock("Bark length", v.length, (x) => `${x.toFixed(2)} s`, d.color)}</div>
        <p class="small muted">${v.confirmed ? `From the ${v.from} barks you labelled.` : `From ${v.from} automatically matched barks; label some to make this firmer.`} Breed can't be told from sound reliably, so it's yours to fill in.</p>`
        : `<p class="small muted">No barks with a voice measurement yet.</p>`}
      <h3>Typical barks</h3>
      <div class="typical">${r.typical.length ? r.typical.map((b, i) => `
        <figure class="tbark" data-b="${b.bark_id}">
          <canvas class="tb-spec" width="300" height="64"></canvas><canvas class="tb-wave" height="44"></canvas>
          <figcaption><button type="button" class="btn sample" data-s="${i}">▶ Play</button> <span class="small muted">${fmtDay(b.ts)} ${fmtTime(b.ts)} · ${Math.round(b.prob * 100)}% bark</span> <a class="small" href="#/clips/${b.episode_id}">clip</a></figcaption>
        </figure>`).join("") : `<p class="small muted">No recordings of this dog are left (clips are removed by the storage rules).</p>`}</div>
      <p class="small muted">The barks closest to this dog's usual sound: spectrogram on top (low pitch at the bottom), waveform below, the bark itself between the marks.</p>
    </section>
    <section class="grid2">
      <div class="panel solo-trait"><h2>Where it barks from</h2>${r.direction ? dirBlock({ ...full, direction: r.direction }) : `<p class="small muted">No directions yet: they need two microphones (Settings → Microphone and detection).</p>`}</div>
      <div class="panel solo-trait"><h2>Rhythm</h2>${r.rhythm ? rhythmBlock({ ...full, rhythm: r.rhythm }) : `<p class="small muted">Not enough barks yet.</p>`}</div>
    </section>
    <section><h2>Longest clips</h2>
      <div class="list">${r.longest.length ? r.longest.map((e) => `<a class="ep" href="#/clips/${e.id}"><span></span><span class="when">${fmtTime(e.start_ts)}<span class="day">${fmtDay(e.start_ts)}</span></span><span class="dur">${fmtDur(e.duration)}</span><span class="n">${e.bark_count} barks</span><span></span><span></span></a>`).join("") : `<div class="empty">None in this period.</div>`}</div>
    </section>`;
  view.querySelectorAll("[data-days]").forEach((b) => b.onclick = () => { S.dogDays = Number(b.dataset.days); renderDog(id); });
  view.querySelectorAll(".tbark").forEach((fig, i) => {
    const b = r.typical[i];
    fig.querySelector(".sample").onclick = (e) => playSegment(e.currentTarget, b.episode_id, b.offset, b.duration);
    api(`/api/barks/${b.bark_id}/wave?width=300`).then((w) => drawBark(fig, w, d.color)).catch(() => {});
  });
}

function drawBark(fig, w, color) {
  const sc = fig.querySelector(".tb-spec"); paintSpec(sc, w.spec);
  const cv = fig.querySelector(".tb-wave"), dpr = window.devicePixelRatio || 1, W = cv.clientWidth || 300, H = cv.clientHeight || 44;
  cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
  const g = cv.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0);
  const n = w.min.length, x = (i) => (i / n) * W, y = (v) => H / 2 - v * (H / 2 - 2);
  g.fillStyle = cssVar("--faint"); g.fillRect((w.bark_start / w.seconds) * W, 0, ((w.bark_end - w.bark_start) / w.seconds) * W, H);
  g.fillStyle = color;
  for (let i = 0; i < n; i++) g.fillRect(x(i), y(w.max[i]), Math.max(1, W / n), Math.max(1, y(w.min[i]) - y(w.max[i])));
  g.fillStyle = cssVar("--ink");
  for (const t of [w.bark_start, w.bark_end]) g.fillRect((t / w.seconds) * W, 0, 1, H);
}
function dogDaily(daily, color) {
  const W = 1000, H = 120, n = daily.length, mx = Math.max(60, ...daily.map((x) => x.seconds)), bw = W / n;
  let g = "";
  daily.forEach((x, i) => {
    const hh = ((H - 20) * x.seconds) / mx;
    g += `<rect x="${(i * bw + bw * 0.12).toFixed(1)}" y="${(H - 18 - hh).toFixed(1)}" width="${(bw * 0.76).toFixed(1)}" height="${Math.max(0, hh).toFixed(1)}" fill="${color}"><title>${prettyIso(x.date)}: ${fmtDur(x.seconds)}, ${x.barks} barks</title></rect>`;
  });
  const step = Math.ceil(n / 8);
  daily.forEach((x, i) => { if (i % step === 0) g += `<text x="${(i * bw).toFixed(1)}" y="${H - 4}">${new Date(x.date + "T12:00:00Z").toLocaleDateString([], { month: "short", day: "numeric", timeZone: "UTC" })}</text>`; });
  g += `<text x="0" y="10">top: ${fmtDur(mx)} a day</text>`;
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Barking per day">${g}</svg>`;
}
function dogHours(hours, color) {
  const W = 480, H = 120, mx = Math.max(1, ...hours), bw = W / 24;
  let g = "";
  hours.forEach((v, i) => {
    const hh = ((H - 20) * v) / mx;
    g += `<rect x="${(i * bw + 1).toFixed(1)}" y="${(H - 18 - hh).toFixed(1)}" width="${(bw - 2).toFixed(1)}" height="${hh.toFixed(1)}" fill="${color}"><title>${hourName(i)}: ${v} barks</title></rect>`;
  });
  for (const hr of [0, 6, 12, 18]) g += `<text x="${hr * bw}" y="${H - 4}">${hr === 0 ? "12a" : hr === 12 ? "12p" : hr > 12 ? hr - 12 + "p" : hr + "a"}</text>`;
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Barks by hour">${g}</svg>`;
}
function dogWeek(week, color) {
  const names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], lx = 34, cw = 18, ch = 16, W = lx + 24 * cw, H = 7 * ch + 18;
  const mx = Math.max(1, ...week.flat());
  let g = "";
  week.forEach((row, r) => {
    g += `<text x="0" y="${r * ch + 12}">${names[r]}</text>`;
    row.forEach((v, c) => { g += `<rect x="${lx + c * cw}" y="${r * ch + 1}" width="${cw - 2}" height="${ch - 3}" rx="2" fill="${v ? color : "var(--faint)"}" fill-opacity="${v ? (0.15 + 0.85 * Math.sqrt(v / mx)).toFixed(2) : 1}"><title>${names[r]} ${hourName(c)}: ${v} barks</title></rect>`; });
  });
  for (const hr of [0, 6, 12, 18]) g += `<text x="${lx + hr * cw}" y="${H - 2}">${hr === 0 ? "12a" : hr === 12 ? "12p" : hr > 12 ? hr - 12 + "p" : hr + "a"}</text>`;
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Barks by weekday and hour">${g}</svg>`;
}
function histBlock(title, h, fmt, color) {
  if (!h?.hist?.length) return "";
  const W = 200, H = 52, n = h.hist.length, mx = Math.max(1, ...h.hist), bw = W / n;
  const bars = h.hist.map((v, i) => `<rect x="${(i * bw + 1).toFixed(1)}" y="${(H - 12 - ((H - 14) * v) / mx).toFixed(1)}" width="${(bw - 2).toFixed(1)}" height="${(((H - 14) * v) / mx).toFixed(1)}" fill="${color}" fill-opacity=".7"/>`).join("");
  return `<div class="trait"><h4>${title}</h4><svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="${title} spread">${bars}<text x="0" y="${H - 1}" style="font-size:9px">${fmt(h.lo)}</text><text x="${W}" y="${H - 1}" text-anchor="end" style="font-size:9px">${fmt(h.hi)}</text></svg></div>`;
}

PAGES.dogs = renderDogs;
