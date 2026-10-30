"use strict";
// Settings: the set-and-forget things. Microphone and detection, storage and backup, alerts to other
// apps, the theme (which includes the live band), and how the Pi itself is doing. Watching the microphone live and
// adjusting detection levels happens on the Detection and tuning page.

const SETTINGS_SECTIONS = [["mic", "Microphone and detection"], ["storage", "Storage and backup"],
  ["alerts", "Alerts"], ["theme", "Theme"], ["system", "System"], ["advanced", "Advanced (debug)"]];

async function renderSettings() {
  const st = await api("/api/settings");
  if (route.name !== "settings") return;
  S.values = st.values;
  const card = (id, title, inner) => `<details class="panel card" id="card-${id}" data-card="${id}">
      <summary><h2>${title}</h2><span class="small muted card-sum" id="sum-${id}"></span></summary>
      <div class="card-body">${inner}</div></details>`;
  view.innerHTML = `
    <section><h1>Settings</h1><p class="sub">Changes take effect right away and are kept on this Pi. Open a section to change it.</p></section>
    <div class="row cards-ctl"><button type="button" class="linkish small" id="openAll">Open all</button><button type="button" class="linkish small" id="closeAll">Close all</button></div>
    <div class="cards">
      ${card("mic", "Microphone and detection", `<div id="sec-mic" class="solo"></div>`)}
      ${card("storage", "Storage and backup", `<div id="sec-storage"></div><div id="sec-backup"></div>`)}
      ${card("alerts", "Alerts", `<div id="sec-alerts" class="solo"></div>`)}
      ${card("theme", "Theme", `<div id="sec-theme" class="solo"></div>`)}
      ${card("system", "System", `<div id="sec-system" class="solo"></div>`)}
      ${card("advanced", "Advanced (debug)", `<div id="sec-advanced" class="solo"></div>`)}
    </div>
    <footer class="version">BarkTracker ${esc(st.build.version)}${st.build.commit ? `, build ${esc(st.build.commit)}${st.build.commit_date ? ` from ${esc(st.build.commit_date)}` : ""}` : ""}</footer>`;
  drawMic(st); drawStorage(st); drawBackup(); drawAlerts(); drawTheme(); drawSystem(); drawAdvanced();
  sumMic(st); sumStorage(st);
  view.querySelectorAll("details.card").forEach((d) => d.addEventListener("toggle", () => {
    if (d.open) history.replaceState(null, "", `#/settings/${d.dataset.card}`);
  }));
  $("#openAll").onclick = () => view.querySelectorAll("details.card").forEach((d) => { d.open = true; });
  $("#closeAll").onclick = () => view.querySelectorAll("details.card").forEach((d) => { d.open = false; });
  if (route.arg) openCard(route.arg);
}
function openCard(id) {
  if (id === "band") id = "theme";                // the live band moved into the theme
  const d = $(`#card-${id}`); if (!d) return;
  d.open = true;
  setTimeout(() => d.scrollIntoView({ behavior: "smooth", block: "start" }), 30);
}
function setSum(id, text) { const el = $(`#sum-${id}`); if (el) el.textContent = text; }
function sumMic(st) {
  const v = st.values, mode = !v["direction.enabled"] ? "mixed to mono" : v["direction.mode"] === "pair" ? `two microphones, ${v["direction.spacing_cm"]} cm` : `stereo, ${v["direction.spacing_cm"]} cm`;
  setSum("mic", `${st.info.audio_device ? st.info.audio_device.split(":")[0].split("(")[0].trim() : "no microphone"} · ${mode}`);
}
function sumStorage(st) {
  const d = st.disk, gb = (mb) => mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.round(mb)} MB`;
  setSum("storage", `${gb(d.clips_mb)} of clips · ${gb(d.free_mb)} free${st.values["backup.path"] ? " · backing up to USB" : " · no backup drive"}`);
}

// ---- microphone and detection ------------------------------------------------------------
function drawMic(st) {
  const v = st.values, mode = !v["direction.enabled"] ? "mono" : v["direction.mode"] === "pair" ? "pair" : "stereo";
  const el = $("#sec-mic");
  el.innerHTML = `<form class="panel form" id="f-mic">
    <h2>Microphone and detection</h2>
    <div id="micPick"></div>
    <h3>Channels</h3>
    <fieldset class="modes">
      <label class="check"><input type="radio" name="chmode" value="mono" ${mode === "mono" ? "checked" : ""}> Mix to mono <span class="help">One sound for detection. The usual choice for a single microphone or webcam.</span></label>
      <label class="check"><input type="radio" name="chmode" value="stereo" ${mode === "stereo" ? "checked" : ""}> Stereo: use the two channels for direction <span class="help">For a microphone with two capsules on one clock, like the Google AIY Voice HAT or a stereo webcam. Detection still hears the mix; each bark also gets a direction, shown on Detection and tuning, the dashboard and the Dogs page.</span></label>
      <label class="check"><input type="radio" name="chmode" value="pair" ${mode === "pair" ? "checked" : ""}> Two microphones <span class="help">The main microphone plus a second USB one, a metre or more apart, for a finer angle. They run on separate clocks: calibrate on Detection and tuning.</span></label>
    </fieldset>
    <div class="grid3 dirfields" ${mode === "mono" ? "hidden" : ""}>
      <label>Distance between the microphones<span class="unit"><input type="number" name="direction.spacing_cm" value="${v["direction.spacing_cm"]}" min="0.5" max="2000" step="0.1">cm</span>
        <span class="help">Measure between the mic holes. <button type="button" class="linkish" id="aiy">AIY Voice HAT: 7.1 cm</button></span></label>
      <label class="paironly" ${mode === "pair" ? "" : "hidden"}>Second microphone<select name="direction.device" id="dev2"><option value="">Choose…</option></select></label>
      <label class="check"><input type="checkbox" name="direction.swap" ${v["direction.swap"] ? "checked" : ""}> Mic A is on the other side <span class="help">Tick if sounds from the left land near 180°.</span></label>
    </div>
    <h3>Detection</h3>
    <p class="small muted intro">The loudness trigger, the quietest sound checked and the bark confidence are set on <a href="#/tune">Detection and tuning</a>, where you can see what they catch.</p>
    <div class="grid3">
      ${fieldNum("audio.gain_db", "Microphone boost", v["audio.gain_db"], { unit: "dB", min: -12, max: 30, help: "Webcams adjust their own gain; leave at 0 unless barks look quiet. The AIY Voice HAT has none: start at +18 dB (docs/MICROPHONES.md)." })}
      ${fieldNum("detection.episode_gap", "Quiet time that ends a clip", v["detection.episode_gap"], { unit: "seconds", min: 1, max: 120, step: 0.5 })}
      ${fieldNum("detection.min_episode_barks", "Smallest clip to keep", v["detection.min_episode_barks"], { unit: "barks", min: 1, max: 50, help: "2 skips single woofs." })}
      ${fieldNum("ui.day_nap_below_dbfs", "Calm means background noise below", v["ui.day_nap_below_dbfs"], { unit: "dBFS", min: -90, max: -10, help: `The live band's napping dog shows in the daytime when it's this calm (if the theme allows it). Depends on the microphone and its boost. <span id="ambNow">Background noise right now: measuring…</span>` })}
    </div>
    ${fieldCheck("detection.suppress_learned_false_positives", "Drop sounds that match clips marked “Not a bark”", v["detection.suppress_learned_false_positives"])}
    <h3>Telling dogs apart</h3>
    <div class="grid3">
      ${fieldNum("identify.min_labels_per_dog", "Labelled barks before matching a dog", v["identify.min_labels_per_dog"], { unit: "barks", min: 1, max: 100 })}
      <label>How sure a match must be <span class="unit"><input type="range" name="identify.min_confidence" min="0.3" max="0.95" step="0.05" value="${v["identify.min_confidence"]}" oninput="this.nextElementSibling.textContent=Math.round(this.value*100)+'%'"><output>${Math.round(v["identify.min_confidence"] * 100)}%</output></span><span class="help">Less sure matches stay “Unidentified”.</span></label>
      ${fieldCheck("identify.use_direction", "Use direction when matching", v["identify.use_direction"], "With two microphones: barks from where a dog usually barks count toward it.")}
    </div>
    <div class="row spread"><button class="btn primary" type="submit">Save</button><button type="button" class="btn" id="resetApp">Restore config.yaml values</button></div>
  </form>`;
  const f = $("#f-mic");
  const sync = () => {
    const m = f.querySelector('[name="chmode"]:checked').value;
    f.querySelector(".dirfields").hidden = m === "mono";
    f.querySelector(".paironly").hidden = m !== "pair";
  };
  f.querySelectorAll('[name="chmode"]').forEach((r) => r.onchange = sync);
  $("#aiy").onclick = () => { f.elements["direction.spacing_cm"].value = 7.1; f.querySelector('[name="chmode"][value="stereo"]').checked = true; sync(); toast("Set for the AIY Voice HAT; press Save"); };
  api("/api/audio/devices").then((r) => {
    const names = [...(r.devices || []).map((d) => d.short), ...(r.unlisted || [])], cur = v["direction.device"];
    $("#dev2").innerHTML = `<option value="">Choose…</option>` + names.map((n) => `<option ${n === cur ? "selected" : ""}>${esc(n)}</option>`).join("") + (cur && !names.includes(cur) ? `<option selected>${esc(cur)}</option>` : "");
  }).catch(() => {});
  f.onsubmit = async (ev) => {
    ev.preventDefault();
    const m = f.querySelector('[name="chmode"]:checked').value, vals = { "direction.enabled": m !== "mono" };
    if (m !== "mono") vals["direction.mode"] = m;
    for (const inp of f.querySelectorAll("input[name], select[name]")) {
      if (inp.name === "chmode" || inp.name === "direction.device" && m !== "pair") continue;
      vals[inp.name] = inp.type === "checkbox" ? inp.checked : inp.value;
    }
    try { const st2 = await saveApp(vals, m === "mono" ? "Saved: mixing to mono" : "Saved: direction is on; see Detection and tuning"); sumMic({ ...st, values: st2.values }); if (S.media) drawPictures(S.media); }
    catch (e) { toast(e.message); }
  };
  $("#resetApp").onclick = async () => {
    if (!confirm("Undo every change made here to microphone, detection, storage and matching settings, and go back to config.yaml?")) return;
    const st2 = await api("/api/settings", { method: "PUT", body: { reset: true } });
    toast("Restored config.yaml values"); S.values = st2.values; drawMic(st2); drawStorage(st2); if (S.media) drawPictures(S.media);
  };
  drawMics(v["audio.device"]);
  clearInterval(S.ambTimer);
  S.ambTimer = setInterval(() => {
    const a = $("#ambNow"); if (!a) { clearInterval(S.ambTimer); return; }
    const l = Beat.latest();
    a.textContent = l.ambient_db != null ? `Background noise right now: ${Math.round(l.ambient_db)} dBFS.` : "Background noise right now: not available (not listening).";
  }, 1000);
}

// ---- alerts to other apps ---------------------------------------------------------------------
async function drawAlerts() {
  const el = $("#sec-alerts"); if (!el) return;
  let r;
  try { [r] = await Promise.all([api("/api/alerts"), loadDogs()]); } catch (e) { el.innerHTML = `<div class="panel"><h2>Alerts</h2><p class="notice err">${esc(e.message)}</p></div>`; return; }
  const items = r.items.map((i) => ({ ...i, dogs: [...i.dogs] }));
  setSum("alerts", items.length ? `${items.filter((i) => i.enabled).length} of ${items.length} on` : "none set up");
  const PH = { webhook: "https://example.com/hook", ntfy: "https://ntfy.sh/your-topic", discord: "https://discord.com/api/webhooks/…", slack: "https://hooks.slack.com/services/…" };
  const draw = () => {
    el.innerHTML = `<div class="panel form">
      <h2>Alerts</h2>
      <p class="small muted intro">Like alarms on a phone: add as many as you like. Each one goes to one place (ntfy is the easiest for phone notifications; a plain webhook works with Home Assistant and Node-RED) when its condition is met: a clip ends, barking has gone on for a while, or a daily or weekly summary.</p>
      <div class="alerts">${items.map((a, i) => {
        const s = r.status[a.id];
        return `<div class="alert ${a.enabled ? "" : "off"}" data-i="${i}">
          <div class="alert-top">
            <label class="check"><input type="checkbox" data-k="enabled" ${a.enabled ? "checked" : ""}> On</label>
            <input type="text" data-k="name" value="${esc(a.name)}" aria-label="Name" placeholder="Name">
            <select data-k="kind" aria-label="Type">${Object.entries(r.kinds).map(([k, t]) => `<option value="${k}" ${a.kind === k ? "selected" : ""}>${t}</option>`).join("")}</select>
            <input type="url" data-k="url" value="${esc(a.url)}" placeholder="${PH[a.kind]}" aria-label="Address" class="alert-url">
            <button type="button" class="btn" data-test>Test</button>
            <button type="button" class="btn icon" data-del aria-label="Remove">×</button>
          </div>
          <div class="alert-cond">
            <select data-k="when" aria-label="When">${Object.entries(r.when).map(([k, t]) => `<option value="${k}" ${a.when === k ? "selected" : ""}>${t}</option>`).join("")}</select>
            ${a.when === "episode" || a.when === "criteria" ? `<label class="unit">lasting at least <input type="number" data-k="min_seconds" value="${a.min_seconds}" min="0" step="5"> s</label>` : ""}
            ${a.when === "ongoing" ? `<label class="unit">once barking has gone on for <input type="number" data-k="minutes" value="${a.minutes}" min="0.5" step="0.5"> min</label>` : ""}
            ${a.when === "weekly" ? `<label class="unit">on <select data-k="weekday">${["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"].map((d, i) => `<option value="${i}" ${a.weekday === i ? "selected" : ""}>${d}</option>`).join("")}</select></label>` : ""}
            ${a.when === "daily" || a.when === "weekly" ? `<label class="unit">at <input type="time" data-k="at" value="${a.at}"></label><span class="small muted">covering the last ${a.when === "weekly" ? "7 days" : "24 hours"}</span>` : ""}
            ${a.when === "daily" || a.when === "weekly" ? "" : `<label class="check"><input type="checkbox" data-k="quiet_hours_only" ${a.quiet_hours_only ? "checked" : ""}> only in quiet hours</label>`}
            ${Object.values(S.dogs).length ? `<span class="alert-dogs"><span class="small muted">Dogs:</span> ${Object.values(S.dogs).map((d) => `<button type="button" class="chip" data-dog="${d.id}" style="--c:${d.color}" aria-pressed="${a.dogs.includes(d.id)}"><i></i>${esc(d.name)}</button>`).join("")}<span class="small muted">${a.dogs.length ? "" : "(any)"}</span></span>` : ""}
          </div>
          ${s ? `<p class="small ${s.error ? "bad" : "muted"}">${s.error ? `Last send failed ${fmtWhen(s.ts)}: ${esc(s.error)}` : `Last sent ${fmtWhen(s.ts)}`}</p>` : ""}
        </div>`;
      }).join("") || `<p class="small muted">No alerts yet.</p>`}</div>
      <div class="row spread"><span class="row"><button type="button" class="btn" id="addAlert">+ Add an alert</button></span><button type="button" class="btn primary" id="saveAlerts">Save alerts</button></div>
    </div>`;
    el.querySelectorAll(".alert").forEach((row) => {
      const a = items[Number(row.dataset.i)];
      row.querySelectorAll("[data-k]").forEach((inp) => inp.onchange = inp.oninput = () => {
        a[inp.dataset.k] = inp.type === "checkbox" ? inp.checked : inp.type === "number" || inp.dataset.k === "weekday" ? Number(inp.value) : inp.value;
        if (inp.dataset.k === "kind") row.querySelector(".alert-url").placeholder = PH[a.kind];
        if (inp.dataset.k === "when" && inp.type !== "text") { draw(); return; }
        if (inp.dataset.k === "enabled") row.classList.toggle("off", !a.enabled);
      });
      row.querySelectorAll("[data-dog]").forEach((b) => b.onclick = () => {
        const id = Number(b.dataset.dog);
        a.dogs = a.dogs.includes(id) ? a.dogs.filter((x) => x !== id) : [...a.dogs, id]; draw();
      });
      row.querySelector("[data-del]").onclick = () => { items.splice(Number(row.dataset.i), 1); draw(); };
      row.querySelector("[data-test]").onclick = async () => {
        try { await api("/api/alerts/test", { method: "POST", body: a }); toast(`Sent a test to ${a.name || "it"}`); } catch (e) { toast(e.message); }
      };
    });
    $("#addAlert").onclick = () => { items.push({ name: "", kind: "ntfy", url: "", enabled: true, when: "episode", min_seconds: 30, quiet_hours_only: false, dogs: [], minutes: 5, at: "08:00", weekday: 0 }); draw(); el.querySelector(".alert:last-child .alert-url")?.focus(); };
    $("#saveAlerts").onclick = async () => {
      try { r = await api("/api/alerts", { method: "PUT", body: { items } }); toast("Alerts saved"); drawAlerts(); } catch (e) { toast(e.message); }
    };
  };
  draw();
}

// ---- theme ------------------------------------------------------------------------------------
async function drawTheme() {
  const el = $("#sec-theme"); if (!el) return;
  let r;
  try { r = await api("/api/themes"); } catch (e) { el.innerHTML = `<div class="panel"><h2>Theme</h2><p class="notice err">${esc(e.message)}</p></div>`; return; }
  const act = r.theme, edit = JSON.parse(JSON.stringify(act));
  setSum("theme", `${act.name}${act.pictures ? `, with ${act.pictures} pictures` : ""}`);
  let scheme = act.mode === "dark" ? "dark" : "light";
  const swatch = (t) => `<span class="sw" style="background:${t.colors.light.surface}"><i style="background:${t.colors.light.ink}"></i><i style="background:${t.colors.light.panel}"></i><i style="background:${t.alert_color}"></i><i style="background:${t.colors.dark.surface}"></i></span>`;
  const dis = act.builtin ? "disabled" : "";
  const lbv = edit.live_band, lbt = r.live_band;
  const draw = () => {
    el.innerHTML = `<div class="panel form">
      <h2>Theme</h2>
      <p class="small muted intro">Every color and display behavior in one place, the live band included. Built-in themes can't be changed: duplicate one to make it yours. Your themes are small files you can download, share and upload.</p>
      <div class="themes">${r.themes.map((t) => `<button type="button" class="theme-card" data-use="${t.id}" aria-pressed="${t.id === act.id}">${swatch(t)}<strong>${esc(t.name)}</strong><span class="small muted">${t.builtin ? "Built in" : "Yours"}${t.description ? ` · ${esc(t.description)}` : ""}</span></button>`).join("")}</div>
      <div class="row">
        <button type="button" class="btn" id="thDup">Duplicate “${esc(act.name)}”</button>
        <a class="btn" href="/api/themes/${act.id}.${act.pictures ? "zip" : "json"}" download>Download${act.pictures ? " (.zip with pictures)" : ""}</a>
        <label class="btn upload">Upload a theme<input type="file" accept=".json,.zip,application/json,application/zip" id="thUp" hidden></label>
        ${act.builtin ? "" : `<button type="button" class="btn danger" id="thDel">Delete</button>`}
      </div>
      <h3>${act.builtin ? `${esc(act.name)} <span class="small muted">(built in: duplicate it to change these)</span>` : `Edit “${esc(act.name)}”`}</h3>
      <fieldset class="theme-ed" ${dis}>
        <div class="grid3">
          <label>Name<input type="text" id="thName" value="${esc(edit.name)}" maxlength="40"></label>
          <label>Light or dark<select id="thMode">${[["auto", "Follow the device"], ["light", "Always light"], ["dark", "Always dark"]].map(([k, t]) => `<option value="${k}" ${edit.mode === k ? "selected" : ""}>${t}</option>`).join("")}</select></label>
          <label>Corner roundness<span class="unit"><input type="range" id="thRadius" min="0" max="16" value="${edit.radius}"><output>${edit.radius}px</output></span></label>
        </div>
        <label class="colorpick">Barking alert<span class="unit"><input type="color" id="thAlert" value="${edit.alert_color}"> the live graph, bearing chart and browser-tab dog turn this color while a dog is barking</span></label>
      </fieldset>
      <h3>Live band</h3>
      <p class="small muted intro">What the band at the top of every page shows, and when. These apply when you save.</p>
      <fieldset class="theme-ed" ${dis}>
        <label class="check"><input type="checkbox" id="lbNap" ${lbv.day_nap ? "checked" : ""}> ${esc(lbt.day_nap)}</label>
        <label class="check"><input type="checkbox" id="lbNotes" ${lbv.sound_notes ? "checked" : ""}> ${esc(lbt.sound_notes)}</label>
        <label>${esc(lbt.sound_note_min)}<span class="unit"><input type="number" id="lbNoteMin" value="${Math.round(lbv.sound_note_min * 100)}" min="30" max="99" step="1">% sure</span></label>
        <label class="check"><input type="checkbox" id="thWords" ${edit.behaviors.bark_words ? "checked" : ""}> ${esc(r.behaviors.bark_words)}</label>
      </fieldset>
      <div id="thPics"></div>
      ${act.builtin ? "" : `<div class="row">${act.pictures ? `<button type="button" class="btn" id="thPicsClear">Remove the theme's pictures</button><span class="small muted">The band then uses your own pictures (or the built-in drawings).</span>` : `<button type="button" class="btn" id="thPicsCopy">Copy the current pictures into this theme</button><span class="small muted">So they travel with it when you share it.</span>`}</div>`}
      <h3>Colors</h3>
      <fieldset class="theme-ed" ${dis}>
        <div class="row"><span class="small muted">Colors for</span><span class="seg" role="radiogroup">${["light", "dark"].map((s) => `<button type="button" role="radio" data-scheme="${s}" aria-checked="${scheme === s}">${s === "light" ? "Light" : "Dark"}</button>`).join("")}</span></div>
        <div class="colorgrid">${Object.entries(r.color_keys).map(([k, t]) => `<label class="colorpick"><input type="color" data-color="${k}" value="${edit.colors[scheme][k]}"> ${esc(t)}</label>`).join("")}</div>
        <h3>Behavior</h3>
        <label class="check"><input type="checkbox" id="thTab" ${edit.behaviors.tab_alert ? "checked" : ""}> ${esc(r.behaviors.tab_alert)}</label>
        <label class="check"><input type="checkbox" id="thCalm" ${edit.behaviors.motion === "reduced" ? "checked" : ""}> Calm motion: no bouncing bars or splashes, slower redraws (also kinder to an old screen)</label>
        <div class="row"><button type="button" class="btn primary" id="thSave">Save theme</button><span class="small muted">Colors preview as you make them.</span></div>
      </fieldset>
    </div>`;
    wire();
    if (S.media) drawPictures(S.media); else refreshBand();
  };
  const preview = () => {          // try it on the page without saving
    const css = (vals) => Object.entries(vals).map(([k, v]) => `--${k}:${v}`).join(";") + `;--alert:${edit.alert_color};--radius:${edit.radius}px;--radius-sm:${Math.floor(edit.radius / 2)}px`;
    let s = $("#themePreview");
    if (!s) { s = document.createElement("style"); s.id = "themePreview"; document.head.appendChild(s); }
    s.textContent = edit.mode === "light" ? `:root{${css(edit.colors.light)};color-scheme:light}`
      : edit.mode === "dark" ? `:root{${css(edit.colors.dark)};color-scheme:dark}`
      : `:root{${css(edit.colors.light)}}@media (prefers-color-scheme: dark){:root{${css(edit.colors.dark)}}}`;
    Beat.setPrefs({ alertColor: edit.alert_color, barkWords: edit.behaviors.bark_words, tabAlert: edit.behaviors.tab_alert, motion: edit.behaviors.motion });
  };
  const after = async (res, msg) => {
    $("#themePreview")?.remove();
    r = res; Theme.apply(res.theme); toast(msg); drawTheme(); Beat.loadMedia(); refreshBand();
  };
  const wire = () => {
    el.querySelectorAll("[data-use]").forEach((b) => b.onclick = async () => after(await api("/api/themes/active", { method: "PUT", body: { id: b.dataset.use } }), "Theme changed"));
    el.querySelectorAll("[data-scheme]").forEach((b) => b.onclick = () => { scheme = b.dataset.scheme; draw(); });
    $("#thDup").onclick = () => {                 // name the copy right here (no pop-up: some browsers block them)
      const btn = $("#thDup");
      if ($("#thDupRow")) { $("#thDupName").focus(); return; }
      btn.insertAdjacentHTML("afterend", `<span class="row inline-edit" id="thDupRow">
        <input type="text" id="thDupName" value="${esc(act.name)} (mine)" maxlength="40" aria-label="Name for your copy">
        <button type="button" class="btn primary" id="thDupGo">Make copy</button><button type="button" class="btn" id="thDupNo">Cancel</button></span>`);
      const inp = $("#thDupName"); inp.focus(); inp.select();
      const go = async () => {
        const name = inp.value.trim(); if (!name) { inp.focus(); return; }
        try { after(await api("/api/themes", { method: "POST", body: { ...edit, name, copy_pictures_from: act.id } }), `Made “${name}”: change it below`); }
        catch (e) { toast(e.message); }
      };
      $("#thDupGo").onclick = go;
      inp.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); go(); } else if (e.key === "Escape") $("#thDupRow").remove(); };
      $("#thDupNo").onclick = () => $("#thDupRow").remove();
    };
    const del = $("#thDel");
    if (del) del.onclick = async () => { if (confirm(`Delete “${act.name}”?`)) after(await api(`/api/themes/${act.id}`, { method: "DELETE" }), "Theme deleted"); };
    $("#thUp").onchange = async (e) => {
      const f = e.target.files[0]; if (!f) return;
      try {
        const r2 = await fetch("/api/theme-pack", { method: "PUT", body: f, headers: { "Content-Type": "application/octet-stream" } });
        if (!r2.ok) throw new Error((await r2.json().catch(() => ({}))).detail || r2.statusText);
        const res = await r2.json(); after(res, `Added “${res.theme.name}”${res.theme.pictures ? ` with ${res.theme.pictures} pictures` : ""}`); Beat.loadMedia();
      } catch (err) { toast(err.message); }
    };
    if (act.builtin) return;
    const pc = $("#thPicsCopy"), px = $("#thPicsClear");
    if (pc) pc.onclick = async () => { after(await api(`/api/themes/${act.id}/pictures`, { method: "POST", body: {} }), "Pictures copied into the theme"); Beat.loadMedia(); refreshBand(); };
    if (px) px.onclick = async () => { if (!confirm("Remove this theme's own pictures?")) return; after(await api(`/api/themes/${act.id}/pictures`, { method: "POST", body: { clear: true } }), "Theme pictures removed"); Beat.loadMedia(); refreshBand(); };
    const on = (id, ev, fn) => { const x = $(id); if (x) x.addEventListener(ev, () => { fn(x); preview(); }); };
    on("#thName", "input", (x) => { edit.name = x.value; });
    on("#thMode", "change", (x) => { edit.mode = x.value; });
    on("#thRadius", "input", (x) => { edit.radius = Number(x.value); x.nextElementSibling.textContent = `${x.value}px`; });
    on("#thAlert", "input", (x) => { edit.alert_color = x.value; });
    on("#thWords", "change", (x) => { edit.behaviors.bark_words = x.checked; });
    on("#lbNap", "change", (x) => { lbv.day_nap = x.checked; });
    on("#lbNotes", "change", (x) => { lbv.sound_notes = x.checked; });
    on("#lbNoteMin", "input", (x) => { if (x.value !== "") lbv.sound_note_min = Number(x.value) / 100; });
    on("#thTab", "change", (x) => { edit.behaviors.tab_alert = x.checked; });
    on("#thCalm", "change", (x) => { edit.behaviors.motion = x.checked ? "reduced" : "full"; });
    el.querySelectorAll("[data-color]").forEach((x) => x.addEventListener("input", () => { edit.colors[scheme][x.dataset.color] = x.value; preview(); }));
    $("#thSave").onclick = async () => {
      try { await api(`/api/themes/${act.id}`, { method: "PUT", body: edit }); after(await api("/api/themes"), "Theme saved"); } catch (e) { toast(e.message); }
    };
  };
  draw();
}

async function refreshBand() { try { drawPictures(await api("/api/media")); } catch (_) { /* */ } }

// ---- advanced (debug) ------------------------------------------------------------------------
async function drawAdvanced() {
  const el = $("#sec-advanced"); if (!el) return;
  let r;
  try { r = await api("/api/advanced"); } catch (e) { el.innerHTML = `<p class="notice err">${esc(e.message)}</p>`; return; }
  const changed = r.items.filter((i) => i.changed).length;
  setSum("advanced", changed ? `${changed} changed from the defaults` : "all defaults");
  const shown = (i, v) => i.kind === "pct" ? Math.round(v * 1000) / 10 : v;
  const groups = [...new Set(r.items.map((i) => i.group))];
  el.innerHTML = `<form class="panel form" id="advForm">
    <h2>Advanced (debug)</h2>
    <p class="small muted intro">Numbers that are normally fixed in the code, for experimenting and diagnosing. They apply straight away. The defaults are what BarkTracker was tested with; changed values are marked.</p>
    ${groups.map((g) => `<h3>${esc(g)}</h3><div class="adv-grid">${r.items.filter((i) => i.group === g).map((i) => `
      <label class="${i.changed ? "adv-changed" : ""}">${esc(i.label)}
        <span class="unit"><input type="number" name="${i.key}" value="${shown(i, i.value)}" step="any" min="${shown(i, i.lo)}" max="${shown(i, i.hi)}" data-kind="${i.kind}">${esc(i.unit)}</span>
        <span class="help">${i.help ? esc(i.help) + " " : ""}Default ${shown(i, i.default)}${i.unit === "%" ? "%" : ""}.</span></label>`).join("")}</div>`).join("")}
    <div class="row spread"><button class="btn primary" type="submit">Save</button><button type="button" class="btn" id="advReset">Back to the defaults</button></div>
  </form>`;
  $("#advForm").onsubmit = async (ev) => {
    ev.preventDefault();
    const values = {};
    for (const inp of ev.target.querySelectorAll("input[name]")) values[inp.name] = inp.dataset.kind === "pct" ? Number(inp.value) / 100 : Number(inp.value);
    try { await api("/api/advanced", { method: "PUT", body: { values } }); toast("Advanced settings saved"); drawAdvanced(); } catch (e) { toast(e.message); }
  };
  $("#advReset").onclick = async () => {
    if (!confirm("Put every advanced setting back to its default?")) return;
    await api("/api/advanced", { method: "PUT", body: { reset: true } }); toast("Back to the defaults"); drawAdvanced();
  };
}

// ---- system ----------------------------------------------------------------------------------
async function drawSystem() {
  const el = $("#sec-system"); if (!el) return;
  let s;
  try { s = await api("/api/system"); } catch (e) { el.innerHTML = `<div class="panel"><h2>System</h2><p class="notice err">${esc(e.message)}</p></div>`; return; }
  const hw = s.hardware, p = hw.power, d = s.direction;
  setSum("system", `${s.listening ? "listening" : "not listening"}${hw.temp_c != null ? ` · ${hw.temp_c} °C` : ""}${p && (p.under_voltage_now || p.under_voltage_since_boot) ? " · power warning" : ""} · ${s.clock?.ntp_synced ? "clock synced" : "clock not synced"}`);
  const warn = (c, t) => `<span class="${c ? "bad" : ""}">${t}</span>`;
  el.innerHTML = `<div class="panel form">
    <h2>System</h2>
    ${s.error ? `<div class="notice err">${esc(s.error)}</div>` : ""}
    <div class="two">
      <dl class="kv">
        <dt>Device</dt><dd>${esc(hw.model)}</dd>
        <dt>System</dt><dd>${esc(hw.os)}, Python ${esc(hw.python)}</dd>
        <dt>Temperature</dt><dd>${hw.temp_c != null ? warn(hw.temp_c >= 75, `${hw.temp_c} °C${hw.temp_c >= 80 ? " (hot: it slows itself down)" : hw.temp_c >= 75 ? " (warm)" : ""}`) : "–"}</dd>
        <dt>Power</dt><dd>${p ? (p.under_voltage_now ? warn(true, "Under-voltage now: use the official power supply") : p.under_voltage_since_boot ? warn(true, "Under-voltage since start-up") : p.throttled_since_boot ? warn(true, "Slowed down since start-up (heat or power)") : "OK") : "–"}</dd>
        <dt>Load</dt><dd>${hw.load ? `${hw.load[0]} (${hw.cpus} cores)` : "–"}</dd>
        <dt>Memory</dt><dd>${hw.mem_total_mb ? `${gbs(hw.mem_free_mb)} free of ${gbs(hw.mem_total_mb)}` : "–"}</dd>
        <dt>Disk</dt><dd>${gbs(hw.disk_free_mb)} free of ${gbs(hw.disk_total_mb)}</dd>
        <dt>Up for</dt><dd>${hw.uptime_s ? fmtDur(hw.uptime_s) : "–"}</dd>
      </dl>
      <dl class="kv">
        <dt>Listening</dt><dd>${s.listening ? `Yes, on ${esc(s.audio_device || "the default input")}` : warn(true, "No")}</dd>
        <dt>Direction</dt><dd>${d.enabled ? `${d.mode === "pair" ? "Two microphones" : "Stereo"}, ${d.spacing_cm} cm${d.error ? `: ${esc(d.error)}` : ""}` : "Off (mixed to mono)"}</dd>
        <dt>Detector</dt><dd>${s.model === "yamnet" ? "YAMNet sound model" : s.model ? "Basic (no model installed)" : "–"}</dd>
        <dt>Running for</dt><dd>${s.uptime_s != null ? fmtDur(s.uptime_s) : "–"}</dd>
        <dt>Since start</dt><dd>${s.stats ? `${s.stats.barks || 0} barks, ${s.stats.suppressed || 0} ignored as known false alarms` : "–"}</dd>
        <dt>Dog matching</dt><dd>${s.identifier.ready ? "On" : `Waiting for ${s.identifier.min_labels}+ labels per dog`}</dd>
        <dt>Clock</dt><dd>${s.clock?.ntp_synced === true ? "Synchronized to internet time" : s.clock?.ntp_synced === false ? warn(true, "Not synchronized: timestamps may drift") : "Unknown"}</dd>
        <dt>Time zone</dt><dd>${esc(s.timezone)}</dd>
        <dt>Logged</dt><dd>${s.counts.episodes} clips, ${s.counts.barks} barks</dd>
        <dt>Data folder</dt><dd><code>${esc(s.data_dir)}</code></dd>
      </dl>
    </div>
    <div class="row"><a class="btn" href="/api/export.csv">Download the whole log (.csv)</a><button type="button" class="btn" id="sysRefresh">Refresh</button></div>
  </div>`;
  $("#sysRefresh").onclick = drawSystem;
}

PAGES.settings = renderSettings;
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
  toast(msg); S.values = st.values; return st;
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
  settingsForm("storage", "Recordings on this Pi", "", `
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
      const st2 = await saveApp(vals, "Storage settings saved"); drawStorage(st2);
    });
}

// backup to a USB drive + restore ---------------------------------------------------
const fmtWhen = (ts) => ts ? `${fmtDay(ts)}, ${fmtTime(ts)}` : "never";
const gbs = (mb) => mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.round(mb)} MB`;

async function drawBackup() {
  const el = $("#sec-backup"); if (!el) return;
  let b;
  try { b = await api("/api/backup"); } catch (e) { el.innerHTML = `<div class="panel"><h2>Backup to a USB drive</h2><p class="notice">${esc(e.message)}</p></div>`; return; }
  const v = b.settings, last = b.last || {}, res = last.last_result;
  const run = b.running, rest = b.restore;
  let state;
  if (b.restart_pending || b.pending_restore) state = `<div class="notice">A restore is staged${b.pending_restore ? ` (${esc(b.pending_restore.snapshot)})` : ""}. ${b.restart_pending ? "BarkTracker is restarting to finish it…" : "Restart BarkTracker to finish: <code>sudo systemctl restart barktracker</code>"}</div>`;
  else if (run) state = `<p class="bk-state busy">Backing up: ${esc(run.step)}${run.total ? ` (${run.done} of ${run.total})` : ""}…</p>`;
  else if (!v["backup.path"]) state = `<p class="bk-state">No backup drive set yet. Your log, labels and recordings live only on the SD card.</p>`;
  else if (last.last_error) state = `<p class="bk-state bad"><strong>Last backup failed</strong> ${fmtWhen(last.last_try)}: ${esc(last.last_error)}</p>`;
  else if (last.last_ok) state = `<p class="bk-state good"><strong>Backed up</strong> ${fmtWhen(last.last_ok)}${res ? `: ${res.episodes} episodes, ${res.labelled_barks} labelled barks, ${res.clip_files} recordings (${gbs(res.clips_mb)})` : ""}.${b.enabled && b.next_due ? ` Next one ${fmtWhen(Math.max(b.next_due, Date.now() / 1000))}.` : ""}</p>`;
  else state = `<p class="bk-state">Not backed up yet.${b.target.ok ? " The first backup runs within a few minutes, or press Back up now." : ""}</p>`;
  const lr = b.last_restore;
  const drives = (b.drives || []).map((d) => `<li><button type="button" class="linkish" data-path="${esc(d.path)}">${esc(d.path)}</button> <span class="muted">${esc(d.label || d.device)}, ${esc(d.fs)}, ${gbs(d.free_mb)} free of ${gbs(d.total_mb)}${d.same_disk ? ", same disk as the SD card" : ""}${d.has_backup ? ", has a BarkTracker backup" : ""}</span></li>`).join("");

  el.innerHTML = `<div class="panel form">
    <h2>Backup to a USB drive</h2>
    <p class="small muted intro">Copies the log database (episodes, barks, dogs, your labels and all the training data behind them, settings, ordinance and contacts), the recordings and your pictures to a USB drive. If the SD card fails, restore the drive onto a fresh install and nothing is lost.</p>
    <div id="bkState">${state}</div>
    ${lr ? `<p class="small muted">Restored from ${esc(lr.snapshot)} on ${fmtWhen(lr.applied)}${lr.previous ? `; the database it replaced is kept in <code>${esc(lr.previous)}</code>` : ""}.</p>` : ""}
    <form id="bkForm" class="bk-form">
      <label>Backup folder on the USB drive
        <span class="bk-path"><input type="text" name="backup.path" value="${esc(v["backup.path"] || "")}" placeholder="/mnt/barktracker-backup" autocomplete="off" spellcheck="false"><button type="button" class="btn" id="bkCheck">Check</button></span>
        <span class="help" id="bkCheckOut">${v["backup.path"] ? (b.target.ok ? `Drive OK, ${gbs(b.target.free_mb)} free.` : esc(b.target.reason)) : "The drive's mount point. See the README for setting up a USB drive on the Pi."}</span></label>
      ${drives ? `<div class="small"><span class="muted">Drives plugged in:</span><ul class="bk-drives">${drives}</ul></div>` : `<p class="small muted">No USB drives are mounted right now.</p>`}
      <div class="grid3">
        ${fieldNum("backup.interval_hours", "Back up every", v["backup.interval_hours"], { unit: "hours", min: 1, max: 720 })}
        ${fieldNum("backup.keep", "Database copies to keep", v["backup.keep"], { unit: "copies", min: 1, max: 90, help: "Older copies let you go back before a mistake." })}
      </div>
      ${fieldCheck("backup.enabled", "Back up automatically", v["backup.enabled"])}
      ${fieldCheck("backup.include_clips", "Include the recordings", v["backup.include_clips"], "Recordings the clip retention rules delete are removed from the drive too.")}
      <div class="row spread"><button class="btn primary" type="submit">Save backup settings</button>
        <span class="row"><button class="btn" type="button" id="bkNow" ${(b.target.ok || b.target.unmounted) && !run ? "" : "disabled"}>Back up now</button>
        <button class="btn" type="button" id="bkEject" ${b.target.ok && !run ? "" : "disabled"}>Safely remove drive</button>
        <a class="btn" href="/api/backup/database.db" download>Download database</a></span></div>
    </form>

    <h3 class="bk-h">Restore</h3>
    <p class="small muted intro">On a new Pi: install BarkTracker, plug in the backup drive, then restore here. This replaces the current log with the backup's; the current database is kept in the data folder in case you need it.</p>
    <div class="bk-restore">
      <span class="bk-path"><input type="text" id="rsPath" value="${esc(v["backup.path"] || (b.drives || []).find((d) => d.has_backup)?.path || "")}" placeholder="/mnt/barktracker-backup" spellcheck="false"><button type="button" class="btn" id="rsFind">Look for backups</button></span>
      <div id="rsOut">${rest?.error ? `<p class="bk-state bad">${esc(rest.error)}</p>` : rest ? `<p class="bk-state busy">Restoring: ${esc(rest.step)}${rest.total ? ` (${rest.done} of ${rest.total})` : ""}…</p>` : ""}</div>
      <details class="bk-upload"><summary class="small">Restore from a downloaded database file instead (no recordings)</summary>
        <div class="row bk-confirm"><input type="file" id="rsFile" accept=".db,application/vnd.sqlite3"><input type="text" id="rsFileConfirm" placeholder="Type RESTORE" aria-label="Type RESTORE to confirm"><button type="button" class="btn" id="rsUpload">Restore file</button></div>
      </details>
    </div>
  </div>`;

  const form = $("#bkForm");
  form.onsubmit = async (ev) => {
    ev.preventDefault();
    const vals = {};
    for (const inp of form.querySelectorAll("input[name]")) vals[inp.name] = inp.type === "checkbox" ? inp.checked : inp.value;
    try { await api("/api/settings", { method: "PUT", body: { values: vals } }); toast("Backup settings saved"); drawBackup(); }
    catch (e) { toast(e.message); }
  };
  const check = async () => {
    const out = $("#bkCheckOut");
    const r = await api("/api/backup/check", { method: "POST", body: { path: form.elements["backup.path"].value } });
    out.textContent = r.ok ? `Drive OK, ${gbs(r.free_mb)} free. Save to use it.` : r.reason;
    out.classList.toggle("bad", !r.ok);
  };
  $("#bkCheck").onclick = () => check().catch((e) => toast(e.message));
  el.querySelectorAll(".bk-drives [data-path]").forEach((btn) => btn.onclick = () => {
    form.elements["backup.path"].value = btn.dataset.path;
    $("#rsPath").value = btn.dataset.path;
    check().catch(() => {});
  });
  $("#bkNow").onclick = async () => {
    try { await api("/api/backup/run", { method: "POST" }); toast("Backup started"); drawBackup(); } catch (e) { toast(e.message); }
  };
  $("#bkEject").onclick = async () => {
    try { const r = await api("/api/backup/eject", { method: "POST" }); toast(r.reason); drawBackup(); } catch (e) { toast(e.message); }
  };
  $("#rsFind").onclick = () => findBackups().catch((e) => { $("#rsOut").innerHTML = `<p class="bk-state bad">${esc(e.message)}</p>`; });
  $("#rsUpload").onclick = async () => {
    const f = $("#rsFile").files[0];
    if (!f) return toast("Choose a .db file first");
    if ($("#rsFileConfirm").value.trim() !== "RESTORE") return toast("Type RESTORE to confirm");
    try {
      const r = await fetch("/api/backup/restore-upload?confirm=RESTORE", { method: "PUT", body: f, headers: { "Content-Type": "application/octet-stream" } });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
      const out = await r.json();
      toast(`Staged ${out.episodes} episodes`);
      waitForRestart(out.restarting);
    } catch (e) { toast(e.message); }
  };
  if (run || (rest && !rest.error && !b.restart_pending)) pollBackup();
  else if (b.restart_pending) waitForRestart(true);
}

async function findBackups() {
  const path = $("#rsPath").value.trim();
  const info = await api("/api/backup/inspect", { method: "POST", body: { path } });
  const snaps = info.snapshots.map((s, i) => `<label class="check bk-snap"><input type="radio" name="rsSnap" value="${esc(s.name)}" ${i === 0 && !s.error ? "checked" : ""} ${s.error ? "disabled" : ""}>
      <span><strong>${fmtWhen(s.ts)}</strong> ${s.error ? `<span class="bad">${esc(s.error)}</span>` : `<span class="muted">${s.episodes} episodes${s.first_ts ? ` from ${fmtDay(s.first_ts)}` : ""}, ${s.dogs} dog${s.dogs === 1 ? "" : "s"}, ${s.labelled_barks} labelled barks, ${s.clips} recordings, ${s.mb} MB</span>`}</span></label>`).join("");
  $("#rsOut").innerHTML = `<div class="bk-found">
    <p class="small">Backup from <strong>${esc(info.hostname || "another Pi")}</strong>${info.version ? ` (BarkTracker ${esc(info.version)})` : ""}, ${info.clip_files} recordings (${gbs(info.clips_mb)}). Choose a copy:</p>
    ${snaps}
    <label class="check"><input type="checkbox" id="rsClips" ${info.clip_files ? "checked" : "disabled"}> Copy the recordings to this Pi too</label>
    <div class="row bk-confirm"><input type="text" id="rsConfirm" placeholder="Type RESTORE" aria-label="Type RESTORE to confirm"><button type="button" class="btn danger" id="rsGo">Restore and restart</button></div>
  </div>`;
  $("#rsGo").onclick = async () => {
    if ($("#rsConfirm").value.trim() !== "RESTORE") return toast("Type RESTORE to confirm");
    const snap = $('input[name="rsSnap"]:checked')?.value;
    try {
      await api("/api/backup/restore", { method: "POST", body: { path: info.root, snapshot: snap, include_clips: $("#rsClips").checked, confirm: "RESTORE" } });
      pollBackup();
    } catch (e) { toast(e.message); }
  };
}

function pollBackup() {
  clearTimeout(S.bkTimer);
  S.bkTimer = setTimeout(async () => {
    if (!$("#sec-backup")) return;
    try {
      const b = await api("/api/backup");
      if (b.restart_pending) { drawBackup(); return; }
      if (b.running || (b.restore && !b.restore.error)) {
        const msg = b.running ? `Backing up: ${b.running.step}${b.running.total ? ` (${b.running.done} of ${b.running.total})` : ""}…`
          : `Restoring: ${b.restore.step}${b.restore.total ? ` (${b.restore.done} of ${b.restore.total})` : ""}…`;
        const st = b.running ? $("#bkState") : $("#rsOut");
        if (st) st.innerHTML = `<p class="bk-state busy">${esc(msg)}</p>`;
        pollBackup();
      } else drawBackup();
    } catch (_) { drawBackup(); }
  }, 1000);
}

function waitForRestart(restarting) {
  const out = $("#rsOut");
  if (!restarting) { drawBackup(); return; }
  if (out) out.innerHTML = `<p class="bk-state busy">Restarting BarkTracker with the restored data…</p>`;
  let wentDown = false;
  const tick = async () => {
    try {
      await api("/api/status");
      if (wentDown) { location.hash = "#/settings/storage"; location.reload(); return; }
    } catch (_) { wentDown = true; }
    setTimeout(tick, 1500);
  };
  setTimeout(tick, 1500);
}

// ---- live band pictures, in the theme's Live band subsection ---------------------------------
function drawPictures(m) {
  S.media = m;
  const box = $("#thPics"); if (!box) return;
  const col = (kind, title, help) => {
    const items = m[kind], own = items.some((i) => i.own);
    return `<div class="piccol">
      <h3>${title}</h3><p class="small muted intro">${help}${own ? "" : " Using the built-in drawing until you add your own."}</p>
      <div class="thumbs">${items.map((i) => `<figure class="thumb">${i.video ? `<video src="${i.url}" muted loop autoplay playsinline></video>` : `<img src="${i.url}" alt="">`}
        <figcaption>${esc(i.name)}${i.own ? ` <button type="button" class="linkbtn" data-delpic="${i.folder}/${esc(i.name)}" aria-label="Remove ${esc(i.name)}">Remove</button>` : ""}</figcaption></figure>`).join("")}</div>
      <label class="btn upload">Add pictures<input type="file" accept="image/*,video/mp4,video/webm" multiple data-up="${kind}" hidden></label>
    </div>`;
  };
  box.innerHTML = `
    <p class="small muted">${m.source === "theme" ? `These pictures belong to the theme “${esc(m.theme)}”: adding or removing them changes the theme.`
      : `This theme has no pictures of its own, so it uses yours, shared by every theme without pictures. You can also copy files straight into <code>${esc(m.folder)}/quiet</code>, <code>/active</code> and <code>/bark</code> on the Pi.`}
      Photos, GIFs, SVGs and short MP4 or WebM loops all work; if a column has several, one is picked at random.</p>
    <div class="three">
      ${col("quiet", "Napping", `Quiet hours, or a calm daytime: background noise below <a href="#/settings/mic" data-napset>${esc(String(S.values?.["ui.day_nap_below_dbfs"] ?? -50))} dBFS</a> (a microphone setting).`)}
      ${col("active", "Sitting", "Daytime background noise, or louder sounds without barking.")}
      ${col("bark", "Barking", "While a bark is being detected.")}
    </div>`;
  const ns = box.querySelector("[data-napset]");
  if (ns) ns.onclick = (e) => {                   // jump to the setting and put the cursor in it
    e.preventDefault(); openCard("mic");
    const inp = $('#f-mic [name="ui.day_nap_below_dbfs"]');
    if (inp) setTimeout(() => { inp.focus(); inp.select(); }, 350);
  };
  box.querySelectorAll("[data-up]").forEach((inp) => inp.addEventListener("change", async () => {
    let r = m;
    for (const f of inp.files) {
      try { r = await fetch(`/api/media/${inp.dataset.up}/${encodeURIComponent(f.name)}`, { method: "PUT", body: f }).then(async (x) => { if (!x.ok) throw new Error((await x.json()).detail); return x.json(); }); }
      catch (e) { toast(`${f.name}: ${e.message}`); }
    }
    toast("Pictures added"); drawPictures(r); Beat.loadMedia();
  }));
  box.querySelectorAll("[data-delpic]").forEach((b) => b.addEventListener("click", async () => {
    const r = await api(`/api/media/${b.dataset.delpic.split("/").map(encodeURIComponent).join("/")}`, { method: "DELETE" });
    toast("Picture removed"); drawPictures(r); Beat.loadMedia();
  }));
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
    <h3>Which microphone</h3>
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
      S.values["audio.device"] = v;
    } catch (e) { toast(e.message); }
  };
  const ref = $("#micRefresh");
  if (ref) ref.onclick = () => drawMics(configured);
}