"use strict";
// Clips: every recording in one list. A clip opens in place (spectrogram, player, who's barking,
// note) so you can work down the list without leaving it. ↑ and ↓ (or k and j) move between clips.

const TAGS = {
  unreviewed: ["Unreviewed", "Nobody has checked this clip, and something about it is uncertain: a low bark score, a dog it couldn't name, or a shaky match."],
  auto: ["Auto", "Found and matched automatically with good confidence. Nobody has checked it."],
  manual: ["Manual", "You labelled the barks or reviewed this clip."],
};
function tagBadge(t, fp) {
  if (fp) return `<span class="mark mark-nb" title="Marked “Not a bark”: left out of stats and reports">Not a bark</span>`;
  if (!TAGS[t]) return "";
  return `<span class="mark mark-${t}" title="${TAGS[t][1]}">${t === "manual" ? "✓ " : ""}${TAGS[t][0]}</span>`;
}
function loadClipPrefs() {
  if (S.clipPrefsLoaded) return;
  S.clipPrefsLoaded = true;
  try { S.clipFilters.hideNb = localStorage.getItem("bt.hideNb") !== "0"; } catch (_) { S.clipFilters.hideNb = true; }
}

async function renderClips() {
  loadClipPrefs();
  await loadDogs();
  const f = S.clipFilters, openId = route.arg ? Number(route.arg) : null;
  const qs = new URLSearchParams({ limit: 30, offset: f.offset || 0, include_fp: !f.hideNb });
  if (f.dog) qs.set("dog", f.dog);
  if (f.tag) qs.set("tag", f.tag);
  if (f.min) qs.set("min_duration", f.min);
  const [r, nb] = await Promise.all([api(`/api/episodes?${qs}`), api("/api/not-bark")]);
  if (route.name !== "clips") return;
  const items = r.items;
  try { localStorage.setItem("bt.clipsSeen", String(Date.now() / 1000)); } catch (_) { /* private mode */ }
  const opts = Object.values(S.dogs).map((d) => `<option value="${d.id}" ${f.dog == d.id ? "selected" : ""}>${esc(d.name)}</option>`).join("");
  view.innerHTML = `
    <section>
      <h1 style="margin-bottom:14px">Clips</h1>
      <div class="filters">
        <label class="check"><input type="checkbox" id="fHideNb" ${f.hideNb ? "checked" : ""}> Hide clips that aren't barking${nb.episodes ? ` <span class="muted">(${nb.episodes})</span>` : ""}</label>
        <select id="fDog" aria-label="Filter by dog"><option value="">All dogs</option>${opts}<option value="u" ${f.dog === "u" ? "selected" : ""}>Unidentified</option></select>
        <select id="fTag" aria-label="Filter by review"><option value="">Any status</option>
          ${Object.entries(TAGS).map(([k, [t]]) => `<option value="${k}" ${f.tag === k ? "selected" : ""}>${t}</option>`).join("")}
          ${f.hideNb ? "" : `<option value="nb" ${f.tag === "nb" ? "selected" : ""}>Not a bark</option>`}</select>
        <label class="row" style="gap:6px">At least <input type="number" id="fMin" min="0" step="5" value="${f.min || 0}" style="width:5em"> s</label>
        <span class="muted small" style="margin-left:auto">${r.total} clip${r.total === 1 ? "" : "s"}</span>
      </div>
      ${!f.hideNb && nb.with_audio ? `<div class="notice nb small">Clips marked “Not a bark” teach the Pi to ignore similar sounds${nb.filter_active ? " (it's doing so now)" : ""}. Their audio isn't needed for that. <button class="btn" id="nbBulk">Delete audio of all ${nb.with_audio} (${nb.audio_mb} MB)</button></div>` : ""}
      <div class="list cliplist" id="clipList">${items.length ? items.map(clipRow).join("") : `<div class="empty"><strong>No clips match</strong>${r.total ? "Try another filter." : "When the Pi hears barking it saves a clip here."}</div>`}</div>
      <div class="pager">
        <button class="btn" id="pPrev" ${!f.offset ? "disabled" : ""}>Newer</button>
        <span class="small muted">${r.total ? `${(f.offset || 0) + 1}–${Math.min((f.offset || 0) + 30, r.total)} of ${r.total}` : ""}</span>
        <button class="btn" id="pNext" ${(f.offset || 0) + 30 >= r.total ? "disabled" : ""}>Older</button>
      </div>
      <p class="small muted">Select a clip to open it here. While one is open, ↑ and ↓ move to the clip above or below.</p>
    </section>`;
  const reload = () => { f.offset = 0; location.hash = "#/clips"; renderClips(); };
  $("#fHideNb").onchange = (e) => { f.hideNb = e.target.checked; if (f.hideNb && f.tag === "nb") f.tag = ""; try { localStorage.setItem("bt.hideNb", f.hideNb ? "1" : "0"); } catch (_) { /* */ } reload(); };
  $("#fDog").onchange = (e) => { f.dog = e.target.value; reload(); };
  $("#fTag").onchange = (e) => { f.tag = e.target.value; reload(); };
  $("#fMin").onchange = (e) => { f.min = Number(e.target.value) || 0; reload(); };
  $("#pPrev").onclick = () => { f.offset = Math.max(0, (f.offset || 0) - 30); location.hash = "#/clips"; renderClips(); };
  $("#pNext").onclick = () => { f.offset = (f.offset || 0) + 30; location.hash = "#/clips"; renderClips(); };
  const bulk = $("#nbBulk");
  if (bulk) bulk.onclick = async () => {
    const res = await api("/api/not-bark/delete-audio", { method: "POST" });
    toast(`Deleted audio of ${res.deleted} clip${res.deleted === 1 ? "" : "s"}, freed ${res.freed_mb} MB`); renderClips();
  };
  const list = $("#clipList");
  list.addEventListener("click", (ev) => {
    if (ev.target.closest("[data-play]")) return;
    const row = ev.target.closest(".ep[data-id]");
    if (!row || ev.target.closest(".clipdetail")) return;
    toggleClip(Number(row.dataset.id));
  });
  list.addEventListener("keydown", (ev) => {
    const row = ev.target.closest?.(".ep[data-id]");
    if (row && (ev.key === "Enter" || ev.key === " ") && ev.target === row) { ev.preventDefault(); toggleClip(Number(row.dataset.id)); }
  });
  document.onkeydown = (ev) => {
    if (route.name !== "clips" || ev.target.closest?.("input, textarea, select, audio") || ev.metaKey || ev.ctrlKey || ev.altKey) return;
    const open = list.querySelector(".ep.open"); if (!open) return;
    const rows = [...list.querySelectorAll(".ep[data-id]")], i = rows.indexOf(open);
    const go = (j) => { if (rows[j]) { ev.preventDefault(); toggleClip(Number(rows[j].dataset.id), true); } };
    if (ev.key === "ArrowDown" || ev.key === "j") go(i + 1);
    else if (ev.key === "ArrowUp" || ev.key === "k") go(i - 1);
    else if (ev.key === "Escape") { ev.preventDefault(); toggleClip(Number(open.dataset.id)); }
  };
  if (openId) {
    if (!list.querySelector(`.ep[data-id="${openId}"]`)) {        // linked clip isn't on this page: show it first
      try {
        const e = await api(`/api/episodes/${openId}?all_kinds=true`);
        list.insertAdjacentHTML("afterbegin", clipRow(e));
      } catch (_) { toast("That clip no longer exists"); }
    }
    toggleClip(openId, true);
  }
}

function spanText(e) { return e.bark_count > 1 ? fmtDur(e.duration) : "single bark"; }

function clipRow(e) {
  return `<div class="ep ${e.live ? "live-ep" : ""}${e.false_positive ? " nbrow" : ""}" data-id="${e.id}" tabindex="0" role="button" aria-expanded="false">
      ${e.has_clip ? playBtn(e.id) : `<span></span>`}
      <span class="when">${fmtTime(e.start_ts)}<span class="day">${fmtDay(e.start_ts)}</span></span>
      <span class="dur" title="From the first bark to the last, pauses included">${spanText(e)}</span>
      <span class="n">${e.bark_count} ${e.false_positive ? "sound" : "bark"}${e.bark_count === 1 ? "" : "s"}</span>
      <span class="dogs">${e.false_positive ? "" : dogTags(e.dogs, e.match)}${confPill(e.max_prob, e.false_positive)}</span>
      <span class="state">${e.live ? `<span class="mark mark-live">Recording</span>` : tagBadge(e.tag, e.false_positive)}</span>
    </div>`;
}

async function toggleClip(id, scroll = false) {
  const list = $("#clipList"); if (!list) return;
  const row = list.querySelector(`.ep[data-id="${id}"]`); if (!row) return;
  const wasOpen = row.classList.contains("open");
  list.querySelectorAll(".ep.open").forEach((r) => { r.classList.remove("open"); r.setAttribute("aria-expanded", "false"); });
  list.querySelectorAll(".clipdetail").forEach((d) => d.remove());
  player.pause(); resetBtn();
  if (wasOpen) { history.replaceState(null, "", "#/clips"); return; }
  row.classList.add("open"); row.setAttribute("aria-expanded", "true");
  history.replaceState(null, "", `#/clips/${id}`);
  const box = h(`<div class="clipdetail" role="region" aria-label="Clip details"><p class="muted small">Loading…</p></div>`);
  row.after(box);
  if (scroll) row.scrollIntoView({ block: "nearest", behavior: "smooth" });
  try { await clipDetail(box, id, row); } catch (e) { box.innerHTML = `<div class="notice err">${esc(e.message)}</div>`; }
}

async function clipDetail(box, id, row) {
  const ep = await api(`/api/episodes/${id}?all_kinds=true`);
  if (!box.isConnected) return;
  const clipLen = ep.clip_seconds || (ep.duration + 4);
  const sel = new Set();
  box.innerHTML = `
    <dl class="facts">
      <div><dt>${ep.false_positive ? "Lasted" : "First bark to last"}</dt><dd>${fmtDur(ep.duration)}</dd></div>
      <div><dt>${ep.false_positive ? "Sounds" : "Barks"}</dt><dd>${ep.bark_count}</dd></div>
      <div><dt>Loudest</dt><dd>${ep.peak_db != null ? ep.peak_db.toFixed(0) + " dBFS" : "–"}</dd></div>
      <div><dt>Bark score</dt><dd>${ep.max_prob != null ? Math.round(ep.max_prob * 100) + "%" : "–"}</dd></div>
      ${ep.bearing != null ? `<div><dt>Direction</dt><dd>about ${Math.round(ep.bearing)}° <span class="small muted">${ep.bearings} of ${ep.barks.length} barks</span></dd></div>` : ""}
      <div><dt>Started</dt><dd class="small">${fmtTimeS(ep.start_ts)}</dd></div>
    </dl>
    ${ep.live ? `<div class="notice">Still recording. Open it again when the barking stops to hear the whole clip.</div>` : ""}
    ${ep.false_positive ? `<div class="notice nb">
      <p style="margin:0 0 8px"><strong>Marked “Not a bark”.</strong> It's left out of stats and reports, and its sounds teach the Pi to ignore similar ones.</p>
      <div class="row"><button class="btn" data-act="restore">Restore as barking</button>
        ${ep.has_clip ? `<button class="btn" data-act="audio">Delete audio, keep what it learned</button>` : ""}
        <button class="btn danger" data-act="forget">Delete and forget</button></div></div>` : ""}
    ${ep.has_clip ? `
      <div class="spec-wrap"><canvas width="900" height="80"></canvas><div class="spec-marks"></div><div class="playhead" style="left:0"></div></div>
      <div class="spec-time"><span>0 s</span><span>Select a bark to hear it alone and label it. Shift-select for several.</span><span>${clipLen.toFixed(1)} s</span></div>
      <audio controls preload="auto" src="/api/episodes/${id}/audio"></audio>`
      : `<div class="notice">${ep.false_positive ? "The audio was deleted. Its sounds are still used to recognize this false alarm." : "The audio was removed by the storage rules (Settings → Storage). The log entry is kept."}</div>`}
    <div class="labeler"></div>
    <div class="clipfoot">
      <textarea rows="1" placeholder="Note: mail carrier, squirrel, doorbell…" aria-label="Note">${esc(ep.note)}</textarea>
      <button class="btn" data-act="note">Save note</button>
      ${ep.false_positive ? "" : `<button class="btn danger" data-act="delete">Delete clip</button>`}
    </div>`;
  const q = (s) => box.querySelector(s);
  const aud = q("audio"), marks = q(".spec-marks"), lab = q(".labeler");
  let stopAt = null;
  const refreshRow = async () => {
    try { const e = await api(`/api/episodes/${id}?all_kinds=true`); const fresh = h(clipRow(e)); fresh.classList.add("open"); fresh.setAttribute("aria-expanded", "true"); row.replaceWith(fresh); row = fresh; } catch (_) { /* gone */ }
  };
  const moveOn = (msg) => {
    toast(msg);
    const next = box.nextElementSibling?.dataset?.id || row.previousElementSibling?.previousElementSibling?.dataset?.id;
    box.remove(); row.remove();
    if (next) toggleClip(Number(next), true); else history.replaceState(null, "", "#/clips");
  };
  if (ep.has_clip) {
    api(`/api/episodes/${id}/spectrogram?width=900&height=80`).then((sp) => paintSpec(q("canvas"), sp)).catch(() => {});
    const tick = () => {
      if (!box.isConnected) return;
      q(".playhead").style.left = `${(aud.currentTime / clipLen) * 100}%`;
      if (stopAt != null && aud.currentTime >= stopAt) { aud.pause(); stopAt = null; }
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
    q(".spec-wrap").addEventListener("click", (ev) => {
      if (ev.target.closest(".mark-bark")) return;
      const r = ev.currentTarget.getBoundingClientRect();
      aud.currentTime = ((ev.clientX - r.left) / r.width) * clipLen; stopAt = null; aud.play();
    });
  }
  function drawMarks() {
    if (!marks) return;
    marks.innerHTML = ep.barks.map((b) => {
      const k = b.dog_id === NOT_BARK ? null : b.dog_id == null ? "u" : String(b.dog_id);
      const color = b.dog_id === NOT_BARK ? "var(--danger)" : dogColor(k);
      const who = b.dog_id === NOT_BARK ? "Not a bark" : b.unknown ? "Unknown dog" : dogName(k);
      const how = b.unknown ? "marked by you" : b.label_dog_id != null ? "labelled by you" : b.pred_dog_id != null ? `guessed, ${Math.round((b.pred_conf || 0) * 100)}% sure` : "not identified yet";
      return `<button class="mark-bark${b.unknown ? " unknown" : ""}" data-b="${b.id}" aria-pressed="${sel.has(b.id)}" style="--c:${color};left:${(b.offset / clipLen) * 100}%;width:${Math.max(0.5, (b.duration / clipLen) * 100)}%" title="${esc(who)} (${how}) at ${b.offset.toFixed(1)} s${b.bearing != null ? `, from about ${Math.round(b.bearing)}°` : ""}" aria-label="Bark at ${b.offset.toFixed(1)} seconds: ${esc(who)}, ${how}"></button>`;
    }).join("");
    marks.querySelectorAll(".mark-bark").forEach((btn) => btn.addEventListener("click", (ev) => {
      const bid = Number(btn.dataset.b), b = ep.barks.find((x) => x.id === bid);
      if (!(ev.shiftKey || ev.metaKey || ev.ctrlKey)) { const had = sel.has(bid) && sel.size === 1; sel.clear(); if (!had) sel.add(bid); }
      else sel.has(bid) ? sel.delete(bid) : sel.add(bid);
      drawMarks(); drawLabeler();
      if (aud && sel.has(bid)) { aud.currentTime = Math.max(0, b.offset - 0.25); stopAt = b.offset + b.duration + 0.35; aud.play(); }
    }));
  }
  function drawLabeler() {
    if (ep.false_positive) { lab.innerHTML = ""; return; }
    const dogs = Object.values(S.dogs);
    const current = sel.size ? null : (() => { const ids = new Set(ep.barks.map((b) => b.label_dog_id)); return ids.size === 1 ? [...ids][0] : undefined; })();
    const snd = ep.sound;
    const suggest = new Set(snd && !sel.size ? dogs.filter((d) => d.size === snd.sounds_like && d.labelled_barks < (S.minLabels || 3)).map((d) => d.id) : []);
    lab.innerHTML = `
      <h3>${sel.size ? `Who made the ${sel.size} selected bark${sel.size === 1 ? "" : "s"}?` : "Who's barking in this clip?"}</h3>
      ${snd && !sel.size ? `<p class="small muted" style="margin:0">Sounds ${SIZE_SOUND[snd.sounds_like]} (around ${fmtHz(snd.pitch_hz)}), like ${SIZE_WORD[snd.sounds_like]}.${suggest.size ? ` Suggested: ${[...suggest].map((i) => esc(S.dogs[i].name)).join(" or ")}.` : ""}</p>` : ""}
      <div class="row">
        ${dogs.map((d) => `<button class="chip ${suggest.has(d.id) ? "suggest" : ""}" data-label="${d.id}" style="--c:${d.color}" aria-pressed="${current === d.id}"><i></i>${esc(d.name)}${suggest.has(d.id) ? ` <span class="sugg">suggested</span>` : ""}</button>`).join("")}
        <button class="chip" data-label="-1" style="--c:${UNKNOWN}" aria-pressed="${current === -1}" title="It's a bark, but you can't tell whose. Not credited to any dog, and the Pi doesn't learn from it."><i></i>Unknown dog</button>
        <button class="chip" data-label="0" style="--c:var(--danger)" aria-pressed="${current === 0}"><i></i>Not a bark</button>
        <button class="btn" data-label="clear">Clear labels</button>
        ${sel.size ? `<button class="btn" data-label="whole">Whole clip instead</button>` : ""}
      </div>
      ${dogs.length ? "" : `<p class="small muted" style="margin:0"><a href="#/dogs">Add your dogs</a> first, then label clips here to teach the Pi who's who.</p>`}`;
    lab.querySelectorAll("[data-label]").forEach((b) => b.addEventListener("click", async () => {
      if (b.dataset.label === "whole") { sel.clear(); drawMarks(); drawLabeler(); return; }
      const v = b.dataset.label === "clear" ? null : Number(b.dataset.label);
      try {
        const res = sel.size ? await api("/api/barks/label", { method: "POST", body: { bark_ids: [...sel], dog_id: v } })
          : await api(`/api/episodes/${id}/label`, { method: "POST", body: { dog_id: v } });
        if (v === 0 && !sel.size && S.clipFilters.hideNb) { moveOn("Marked as not a bark"); return; }
        Object.assign(ep, await api(`/api/episodes/${id}?all_kinds=true`)); sel.clear(); drawMarks(); drawLabeler();
        const who = v === null ? "Labels cleared" : v === 0 ? "Marked as not a bark" : v === -1 ? "Marked as an unknown dog" : `Labelled as ${S.dogs[v]?.name}`;
        const info = res.identifier || {};
        toast(info.trained ? `${who}. Retrained on ${Object.values(info.counts).reduce((a, c) => a + c, 0)} labelled barks.` : `${who}.`);
        refreshRow();
      } catch (e) { toast(e.message); }
    }));
  }
  box.querySelectorAll("[data-act]").forEach((b) => b.onclick = async () => {
    try {
      const act = b.dataset.act;
      if (act === "note") { await api(`/api/episodes/${id}`, { method: "PATCH", body: { note: q("textarea").value } }); toast("Note saved"); }
      else if (act === "delete") {
        if (!confirm("Delete this clip and its log entry? This can't be undone.")) return;
        await api(`/api/episodes/${id}`, { method: "DELETE" }); moveOn("Clip deleted");
      } else if (act === "restore") { await api(`/api/episodes/${id}/label`, { method: "POST", body: { dog_id: null } }); moveOn("Restored as barking"); }
      else if (act === "audio") { await api(`/api/episodes/${id}?keep_training=true`, { method: "DELETE" }); moveOn("Audio deleted; the filter still remembers this sound"); }
      else if (act === "forget") {
        if (!confirm("Delete this clip and stop using it to recognize false alarms? This can't be undone.")) return;
        await api(`/api/episodes/${id}`, { method: "DELETE" }); moveOn("Deleted and forgotten");
      }
    } catch (e) { toast(e.message); }
  });
  drawMarks(); drawLabeler();
  if (!ep.reviewed && !ep.live && !ep.false_positive) {          // opening a clip counts as reviewing it
    api(`/api/episodes/${id}`, { method: "PATCH", body: { reviewed: true } }).then(refreshRow).catch(() => {});
  }
}

const SIZE_WORD = { small: "a small dog", medium: "a medium dog", large: "a large dog" };
const SIZE_SOUND = { small: "high and bright", medium: "mid-range", large: "deep" };
function fmtHz(hz) { return hz >= 1000 ? `${(hz / 1000).toFixed(1)} kHz` : `${Math.round(hz)} Hz`; }

PAGES.clips = renderClips;
