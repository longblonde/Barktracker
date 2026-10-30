"use strict";
// Dogs: the dogs you've named, grouped by household; dogs the Pi keeps hearing that you haven't
// named yet; and for each dog its voice, where it barks from and its rhythm.

async function renderDogs() {
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
        <label class="wide">Description <input type="text" value="${esc(d.description)}" data-field="description" data-id="${d.id}" placeholder="Breed, color, size"></label>
      </div>
      <div class="dogtraits">${voiceBlock(d)}${dirBlock(d)}${rhythmBlock(d)}</div>
    </div>
    <div class="row dogactions">
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
    toast(`${{ name: "Name", size: "Size", address: "Household", owner: "Owner", description: "Description" }[i.dataset.field]} saved`);
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
  view.querySelectorAll("[data-edithome]").forEach((b) => b.onclick = async () => {
    const sec = b.closest(".household"), old = sec.dataset.home;
    const dogs = r.dogs.filter((d) => (d.address || "").trim() === old);
    const addr = prompt("Household address", old);
    if (addr === null) return;
    const owner = prompt("Owner (optional)", dogs.find((d) => d.owner)?.owner || "");
    if (owner === null) return;
    await Promise.all(dogs.map((d) => api(`/api/dogs/${d.id}`, { method: "PATCH", body: { address: addr.trim(), owner: owner.trim() } })));
    toast("Household saved"); renderDogs();
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

PAGES.dogs = renderDogs;
