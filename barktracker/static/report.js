"use strict";
// Report: your local rules (ordinance text, quiet hours, the rules incidents are checked against)
// and the report options sit at the top; the printable report is rendered underneath.

function reportQuery() {
  const r = S.report;
  const q = new URLSearchParams({ from: r.from, to: r.to });
  if (r.subject.startsWith("dog:")) q.set("dog", r.subject.slice(4));
  if (r.subject.startsWith("addr:")) q.set("address", r.subject.slice(5));
  if (r.flagged) q.set("flagged", "true");
  if (r.confirmed) q.set("confirmed", "true");
  return q.toString();
}

async function renderReport() {
  const today = isoToday();
  S.report = { subject: "all", from: shiftIso(today, -29), to: today, flagged: false, confirmed: false, ...(S.report || {}) };
  const R = S.report;
  let subj, rep, ord;
  try { [subj, rep, ord] = await Promise.all([api("/api/subjects"), api(`/api/report?${reportQuery()}`), api("/api/ordinance")]); }
  catch (e) { view.innerHTML = `<div class="notice err">${esc(e.message)}</div>`; return; }
  if (route.name !== "report") return;
  RULE_LABELS = ord.labels;
  const opt = (v, label) => `<option value="${esc(v)}" ${R.subject === v ? "selected" : ""}>${esc(label)}</option>`;
  const placeholder = ord.ordinance.preset === "example";
  const q = reportQuery();
  view.innerHTML = `
    <section class="no-print">
      <h1>Report</h1>
      <p class="sub" style="max-width:66ch">A log of barking for one dog or one household, checked against your local rules, ready to print or download for a complaint.</p>
    </section>
    <section class="no-print">
      <details class="panel rulespanel" ${placeholder || S.rulesOpen ? "open" : ""} id="rulesBox">
        <summary><h2>Local rules</h2><span class="small muted">${placeholder ? "Placeholder wording: set these up for where you live." : esc(ord.ordinance.jurisdiction)} · quiet hours ${clock12(ord.ordinance.thresholds.quiet_start)} to ${clock12(ord.ordinance.thresholds.quiet_end)} · ${ord.ordinance.rules.filter((r) => r.enabled).length} rules</span></summary>
        <div id="ordForm"></div>
      </details>
    </section>
    <section class="panel no-print reportctl">
      <label>Report on<select id="rSubj">
        ${opt("all", "All barking")}
        ${subj.dogs.length ? `<optgroup label="Dog">${subj.dogs.map((d) => opt(`dog:${d.id}`, d.name + (d.address ? ` (${d.address})` : ""))).join("")}</optgroup>` : ""}
        ${subj.neighbors.length ? `<optgroup label="Household (all dogs at an address)">${subj.neighbors.map((n) => opt(`addr:${n.address}`, n.address + (n.owner ? ` (${n.owner})` : ""))).join("")}</optgroup>` : ""}
      </select></label>
      <label>From<input type="date" id="rFrom" value="${R.from}" max="${today}"></label>
      <label>To<input type="date" id="rTo" value="${R.to}" max="${today}"></label>
      <div class="quick">
        <button class="btn" data-range="7">Last 7 days</button><button class="btn" data-range="30">Last 30 days</button><button class="btn" data-range="90">Last 90 days</button>
      </div>
      <label class="check"><input type="checkbox" id="rFlag" ${R.flagged ? "checked" : ""}> Only incidents that meet a rule</label>
      <label class="check"><input type="checkbox" id="rConf" ${R.confirmed ? "checked" : ""} ${R.subject === "all" ? "disabled" : ""}> Only barks I've confirmed are this dog</label>
      <div class="actions">
        <button class="btn primary" id="rPrint">Print or save as PDF</button>
        <span class="dl-group" role="group" aria-label="Download">
          <span class="small muted">Download</span>
          <a class="btn" href="/api/report.csv?${q}" title="One row per incident">Incidents (.csv)</a>
          <a class="btn" href="/api/report.csv?${q}&kind=daily" title="One row per day">Daily totals (.csv)</a>
          <a class="btn" href="/api/report/barks.csv?${q}" title="One row per bark: time, dog, how it was identified, direction">Every bark (.csv)</a>
          <a class="btn" href="/api/report/audio.zip?${q}" title="The recordings of every incident">Audio (.zip)</a>
          <a class="btn" href="/api/report/everything.zip?${q}" title="Recordings, every table, the rule text and the report data">Everything (.zip)</a>
        </span>
      </div>
      ${rep.reporter.name ? "" : `<p class="small muted" style="margin:0;grid-column:1/-1">Add your name and contact details under Local rules → Your details so they print on the report.</p>`}
      ${R.subject !== "all" && rep.subject.kind === "dog" && !rep.subject.address ? `<p class="small muted" style="margin:0;grid-column:1/-1">Add this dog's household on the <a href="#/dogs">Dogs</a> page so the owner and address print on the report.</p>` : ""}
    </section>
    ${reportDoc(rep)}`;
  drawOrdinance(ord);
  $("#rulesBox").addEventListener("toggle", (e) => { S.rulesOpen = e.target.open; });
  const set = (k, v) => { S.report[k] = v; renderReport(); };
  $("#rSubj").onchange = (e) => { S.report.confirmed = S.report.confirmed && e.target.value !== "all"; set("subject", e.target.value); };
  $("#rFrom").onchange = (e) => set("from", e.target.value);
  $("#rTo").onchange = (e) => set("to", e.target.value);
  $("#rFlag").onchange = (e) => set("flagged", e.target.checked);
  $("#rConf").onchange = (e) => set("confirmed", e.target.checked);
  view.querySelectorAll("[data-range]").forEach((b) => b.onclick = () => { S.report.to = today; set("from", shiftIso(today, -(Number(b.dataset.range) - 1))); });
  $("#rPrint").onclick = () => window.print();
}

// ---- local rules editor ------------------------------------------------------------------
const PARAM_UNITS = { minutes: "min", window_hours: "hours", days: "days", window_days: "days" };
const PARAM_NAMES = { minutes: "At least", window_hours: "within", days: "At least", window_days: "within" };

function drawOrdinance(r) {
  const o = r.ordinance, th = o.thresholds, types = r.rule_types;
  const rules = o.rules.map((x) => ({ ...x, params: { ...x.params } }));
  const contacts = o.contacts.map((c) => ({ ...c }));
  const box = $("#ordForm");
  const ruleRow = (x, i) => {
    const t = types[x.type];
    return `<div class="rule ${x.enabled ? "" : "off"}" data-i="${i}">
      <label class="check rule-on" title="Use this rule"><input type="checkbox" data-k="enabled" ${x.enabled ? "checked" : ""}><span class="sr-only">Use this rule</span></label>
      <input type="text" class="rule-name" data-k="label" value="${esc(x.label)}" aria-label="Rule name" maxlength="60">
      <span class="rule-params">${Object.keys(t.params).map((k) => `<label class="unit">${PARAM_NAMES[k]}<input type="number" min="0" step="1" data-p="${k}" value="${x.params[k]}">${PARAM_UNITS[k]}</label>`).join("")}</span>
      <span class="rule-scope small muted">${t.scope === "day" ? "per day" : "per incident"}</span>
      <button type="button" class="btn icon" data-del aria-label="Remove ${esc(x.label)}">×</button>
      <span class="rule-help small muted">${esc(t.help)}${x.type === "quiet_hours" ? `, between ${clock12(th.quiet_start)} and ${clock12(th.quiet_end)}` : ""}${x.type === "sustained" ? `, no break over ${th.max_pause_seconds} s` : ""}.</span>
    </div>`;
  };
  const draw = () => {
    box.innerHTML = `
      <div class="ord-grid">
        <div class="form">
          <label class="inline">Start from <select id="preset">${r.presets.map((p) => `<option value="${p.id}" ${p.id === o.preset ? "selected" : ""}>${esc(p.label)}</option>`).join("")}<option value="custom" ${r.presets.some((p) => p.id === o.preset) ? "" : "selected"}>My own (custom)</option></select></label>
          <label>Jurisdiction<input type="text" name="jurisdiction" value="${esc(o.jurisdiction)}" placeholder="City of Anytown, USA"></label>
          <label>Code section<input type="text" name="code_section" value="${esc(o.code_section)}" placeholder="Municipal Code § 0-0-0, Animal Noise"></label>
          <label>What the rule says, in plain words<textarea name="rule_text" rows="3">${esc(o.rule_text)}</textarea></label>
          <label>Notes<textarea name="rule_note" rows="2">${esc(o.rule_note)}</textarea></label>
          <div class="two"><label>Link to the code<input type="url" name="source_url" value="${esc(o.source_url)}" placeholder="https://"></label><label>Penalty<input type="text" name="penalty" value="${esc(o.penalty)}"></label></div>
        </div>
        <div class="form">
          <h3>Quiet hours</h3>
          <div class="grid3"><label>Start<input type="time" name="quiet_start" value="${th.quiet_start}" required></label>
            <label>End<input type="time" name="quiet_end" value="${th.quiet_end}" required></label>
            <label>Breaks that end an incident<span class="unit"><input type="number" name="max_pause_seconds" value="${th.max_pause_seconds}" min="0" step="5">s</span></label></div>
          <p class="help" style="margin:-4px 0 0">Shown striped on the dashboard. An incident is barking with no break longer than the time on the right.</p>
          <h3>Where to report</h3>
          <div class="contacts-ed">${contacts.map((c, i) => `<div class="contact" data-c="${i}"><input type="text" data-ck="name" value="${esc(c.name)}" placeholder="Anytown Animal Control" aria-label="Name"><input type="text" data-ck="phone" value="${esc(c.phone)}" placeholder="555-555-0101 or a web address" aria-label="Phone or web address"><button type="button" class="btn icon" data-cdel aria-label="Remove">×</button></div>`).join("")}</div>
          <button type="button" class="btn" id="addContact">+ Add a contact</button>
        </div>
      </div>
      <h3 class="rules-h">Rules <span class="small muted">Incidents and days that meet one are flagged on the dashboard and in reports. Most codes say “habitually or frequently” without numbers, so these are your documentation criteria, not legal limits.</span></h3>
      <div class="rules">${rules.map(ruleRow).join("") || `<p class="small muted">No rules: reports will list barking without flagging anything.</p>`}</div>
      <div class="addrule">
        <button type="button" class="btn" id="addRule" aria-haspopup="true" aria-expanded="false">+ Add rule</button>
        <div class="menu" id="ruleMenu" hidden role="menu">${Object.entries(types).map(([k, t]) => `<button type="button" role="menuitem" data-type="${k}"><strong>${esc(t.label)}</strong><span class="small muted">${esc(t.help)} (${t.scope === "day" ? "per day" : "per incident"})</span></button>`).join("")}</div>
      </div>
      <details class="reporter"><summary><h3>Your details</h3> <span class="small muted">printed on reports as the person submitting the log; stored only on this Pi</span></summary>
        <div class="two">${["name", "address", "phone", "email"].map((k) => `<label>${k[0].toUpperCase() + k.slice(1)}<input type="${k === "email" ? "email" : "text"}" name="reporter.${k}" value="${esc(r.reporter[k])}"></label>`).join("")}</div>
      </details>
      <div class="row spread savebar">
        <span class="row"><button type="button" class="btn primary" id="saveOrd">Save rules</button><span class="small muted" id="ordDirty"></span></span>
        <button type="button" class="btn" id="resetOrd">Reset to the preset</button>
      </div>`;
    wire();
  };
  const dirty = () => { const d = $("#ordDirty"); if (d) d.textContent = "Unsaved changes"; };
  const wire = () => {
    box.querySelectorAll(".rule").forEach((el) => {
      const x = rules[Number(el.dataset.i)];
      el.querySelector('[data-k="enabled"]').onchange = (e) => { x.enabled = e.target.checked; el.classList.toggle("off", !x.enabled); dirty(); };
      el.querySelector('[data-k="label"]').oninput = (e) => { x.label = e.target.value; dirty(); };
      el.querySelectorAll("[data-p]").forEach((inp) => inp.oninput = () => { x.params[inp.dataset.p] = Number(inp.value); dirty(); });
      el.querySelector("[data-del]").onclick = () => { rules.splice(Number(el.dataset.i), 1); draw(); dirty(); };
    });
    box.querySelectorAll(".contact").forEach((el) => {
      const c = contacts[Number(el.dataset.c)];
      el.querySelectorAll("[data-ck]").forEach((inp) => inp.oninput = () => { c[inp.dataset.ck] = inp.value; dirty(); });
      el.querySelector("[data-cdel]").onclick = () => { contacts.splice(Number(el.dataset.c), 1); keep(); draw(); dirty(); };
    });
    box.querySelectorAll("input[name], textarea[name]").forEach((i) => i.addEventListener("input", dirty));
    $("#addContact").onclick = () => { keep(); contacts.push({ name: "", phone: "" }); draw(); dirty(); box.querySelector(".contact:last-child input")?.focus(); };
    const menu = $("#ruleMenu"), add = $("#addRule");
    add.onclick = () => { menu.hidden = !menu.hidden; add.setAttribute("aria-expanded", String(!menu.hidden)); };
    menu.querySelectorAll("[data-type]").forEach((b) => b.onclick = () => {
      const t = types[b.dataset.type];
      keep(); rules.push({ type: b.dataset.type, label: t.label, enabled: true, params: { ...t.params } });
      draw(); dirty();
      box.querySelector(".rule:last-child .rule-name")?.focus();
    });
    $("#preset").onchange = async (e) => {
      const p = e.target.value; if (p === "custom") return;
      const label = r.presets.find((x) => x.id === p)?.label || p;
      if (!confirm(`Load the ${label} wording, rules and contacts? Your details are kept.`)) { e.target.value = o.preset; return; }
      await api("/api/ordinance", { method: "PUT", body: { reset: true, preset: p } });
      toast(`Loaded ${label}`); renderReport();
    };
    $("#resetOrd").onclick = async () => {
      const p = $("#preset").value === "custom" ? "example" : $("#preset").value;
      if (!confirm("Replace the wording, quiet hours, rules and contacts with the preset? Your details are kept.")) return;
      await api("/api/ordinance", { method: "PUT", body: { reset: true, preset: p } }); toast("Reset to the preset"); renderReport();
    };
    $("#saveOrd").onclick = save;
  };
  // carry typed-in text across redraws (adding a rule or contact redraws the form)
  const keep = () => {
    box.querySelectorAll("input[name], textarea[name]").forEach((i) => {
      if (i.name.startsWith("reporter.")) r.reporter[i.name.slice(9)] = i.value;
      else if (i.name in th) th[i.name] = i.name === "max_pause_seconds" ? Number(i.value) : i.value;
      else o[i.name] = i.value;
    });
  };
  async function save() {
    keep();
    const body = { rules, contacts: contacts.filter((c) => c.name.trim() || c.phone.trim()),
                   thresholds: { quiet_start: th.quiet_start, quiet_end: th.quiet_end, max_pause_seconds: th.max_pause_seconds } };
    for (const k of ["jurisdiction", "code_section", "rule_text", "rule_note", "source_url", "penalty"]) body[k] = o[k];
    const pv = $("#preset").value, base = r.presets.find((p) => p.id === pv);
    body.preset = pv === "custom" || ["jurisdiction", "code_section", "rule_text"].some((k) => body[k] !== r.ordinance[k]) ? "custom" : pv;
    if (!base && pv !== "custom") body.preset = "custom";
    try {
      await Promise.all([api("/api/ordinance", { method: "PUT", body }), api("/api/reporter", { method: "PUT", body: r.reporter })]);
      toast("Rules saved"); S.rulesOpen = true; renderReport();
    } catch (e) { toast(e.message); }
  }
  draw();
}

function ruleSentence(rep) {
  const sm = rep.summary, parts = [];
  for (const [k, n] of Object.entries(sm.flag_counts)) parts.push(`${n} incident${n === 1 ? "" : "s"} met “${rep.flag_labels[k] || k}”`);
  for (const [k, n] of Object.entries(sm.days_flagged)) if (n) parts.push(`${n} day${n === 1 ? "" : "s"} met “${rep.flag_labels[k] || k}”`);
  return parts.length ? parts.join(", ").replace(/, ([^,]*)$/, " and $1") + "." : "";
}
function reportDoc(rep) {
  const o = rep.ordinance, sm = rep.summary, sub = rep.subject, rp = rep.reporter;
  const subjectLine = sub.kind === "all" ? "All dogs heard at the recording location"
    : sub.kind === "dog" ? [sub.title, sub.owner && `owned by ${sub.owner}`, sub.address].filter(Boolean).join(", ")
    : `Dogs kept at ${sub.title}${sub.owner ? ` (${sub.owner})` : ""}`;
  const dogsLine = sub.dogs?.length ? sub.dogs.map((d) => d.name + (d.description ? ` (${d.description})` : "")).join("; ") : "";
  const flagTxt = (fl) => fl.map((f) => rep.flag_labels[f] || f).join(", ");
  const activeDays = rep.days.filter((d) => d.incidents);
  const generated = new Date(rep.generated_ts * 1000).toLocaleString([], { dateStyle: "long", timeStyle: "short", ...timeOpts() });
  const clockOk = S.status?.clock?.ntp_synced;
  return `
  <article class="doc">
    <header class="doc-head">
      <h1>Barking complaint log</h1>
      <dl class="doc-meta">
        <div><dt>Subject</dt><dd>${esc(subjectLine)}${dogsLine && sub.kind !== "dog" ? `<br><span class="muted">${esc(dogsLine)}</span>` : sub.dogs?.[0]?.description ? `<br><span class="muted">${esc(sub.dogs[0].description)}</span>` : ""}</dd></div>
        <div><dt>Period</dt><dd>${longDate(rep.period.from)} to ${longDate(rep.period.to)}</dd></div>
        <div><dt>Prepared by</dt><dd>${rp.name ? esc([rp.name, rp.address].filter(Boolean).join(", ")) + (rp.phone || rp.email ? `<br>${esc([rp.phone, rp.email].filter(Boolean).join(", "))}` : "") : `<span class="blank">Name and address</span>`}</dd></div>
        <div><dt>Generated</dt><dd>${esc(generated)} (times in ${esc(rep.timezone)})</dd></div>
      </dl>
    </header>

    <section class="doc-sec">
      <h2>Ordinance</h2>
      <p><strong>${esc(o.code_section)}</strong>, ${esc(o.jurisdiction)}</p>
      <p>${esc(o.rule_text)}</p>
      ${o.rule_note ? `<p class="muted">${esc(o.rule_note)}</p>` : ""}
      ${o.source_url ? `<p class="small muted">Source: ${esc(o.source_url)}</p>` : ""}
    </section>

    <section class="doc-sec">
      <h2>Summary</h2>
      ${sm.incidents ? `<p class="lead">Barking was recorded on <strong>${sm.days_with_barking} of ${sm.days_in_period} days</strong>: ${sm.incidents} incident${sm.incidents === 1 ? "" : "s"} totalling <strong>${fmtDur(sm.total_seconds)}</strong>${sub.kind !== "all" ? ` and ${sm.barks} barks` : ""}. ${ruleSentence(rep)} The longest lasted ${fmtDur(sm.longest.duration)}, on ${fmtDay(sm.longest.start_ts)} at ${fmtTime(sm.longest.start_ts)}.</p>`
        : `<p class="lead">No barking${rep.flagged_only ? " meeting the criteria" : ""} was recorded for this subject in the period.</p>`}
      <table class="crit">
        <thead><tr><th>Rule</th><th>Definition used</th><th class="num">Incidents</th><th class="num">Days</th></tr></thead>
        <tbody>${Object.entries(rep.criteria).map(([k, v]) => `<tr><td>${esc(rep.flag_labels[k])}</td><td>${esc(v)}</td><td class="num">${k in sm.flag_counts ? sm.flag_counts[k] : "–"}</td><td class="num">${k in sm.flag_counts ? new Set(rep.incidents.filter((i) => i.flags.includes(k)).map((i) => i.date)).size : sm.days_flagged[k]}</td></tr>`).join("")}</tbody>
      </table>
    </section>

    ${activeDays.length ? `<section class="doc-sec">
      <h2>Daily totals</h2>
      <table><thead><tr><th>Date</th><th class="num">Incidents</th><th class="num">Total time</th>${sub.kind !== "all" ? `<th class="num">Barks</th>` : ""}<th class="num">Quiet hours</th><th>Criteria met</th></tr></thead>
      <tbody>${activeDays.map((d) => `<tr><td class="nw">${longDate(d.date)}</td><td class="num">${d.incidents}</td><td class="num">${fmtClock(d.seconds)}</td>${sub.kind !== "all" ? `<td class="num">${d.barks}</td>` : ""}<td class="num">${d.quiet_seconds ? fmtClock(d.quiet_seconds) : "–"}</td><td>${esc(flagTxt([...new Set([...d.flags, ...rep.incidents.filter((i) => i.date === d.date).flatMap((i) => i.flags)])]))}</td></tr>`).join("")}</tbody></table>
    </section>` : ""}

    ${rep.incidents.length ? `<section class="doc-sec">
      <h2>Incident log</h2>
      <table class="log"><thead><tr><th class="num">#</th><th>Date</th><th>Start</th><th>End</th><th class="num">Length</th><th class="num">Barks</th>${sub.kind === "all" ? `<th>Dogs heard</th>` : `<th>Identified</th>`}<th>Criteria met</th><th>Notes</th></tr></thead>
      <tbody>${rep.incidents.map((i) => `<tr class="${i.flags.length ? "hit" : ""}"><td class="num">${i.number}</td><td class="nw">${shortDate(i.date)}</td><td class="nw">${esc(i.start)}</td><td class="nw">${esc(i.end)}</td><td class="num">${fmtClock(i.duration)}</td><td class="num">${sub.kind === "all" ? i.barks : i.subject_barks}</td><td>${sub.kind === "all" ? esc(Object.entries(i.dogs).map(([k, v]) => `${k} ×${v}`).join(", ")) : ({ confirmed: "Confirmed", automatic: "Automatic", mixed: "Partly confirmed" }[i.identified] || "")}</td><td>${esc(flagTxt(i.flags))}</td><td>${esc(i.notes)}</td></tr>`).join("")}</tbody></table>
      <p class="small muted">An incident is continuous barking with no break longer than ${o.thresholds.max_pause_seconds} seconds. ${sub.kind !== "all" ? "“Confirmed” means a person listened to the recording and identified the dog; “Automatic” means it was matched by voice to barks that were confirmed earlier." : ""}</p>
    </section>` : ""}

    <section class="doc-sec">
      <h2>How this was recorded</h2>
      <p>Sound was recorded continuously by a Raspberry Pi 4 with a USB microphone${S.status?.audio_device ? ` (${esc(S.status.audio_device.split(":")[0])})` : ""} at the preparer's home. Each loud sound was checked by an audio classification model trained to recognize dog barking, and barks less than ${S.status?.settings?.episode_gap ?? 8} seconds apart were grouped. Times come from the device clock${clockOk === true ? ", which is synchronized to internet time" : clockOk === false ? " (not confirmed synchronized at the time of this report)" : ""}. An audio clip was saved for each recorded episode and can be provided on request.</p>
    </section>

    ${o.contacts?.length ? `<section class="doc-sec"><h2>Where to report</h2><ul class="contacts">${o.contacts.map((c) => `<li>${esc(c.name)}${c.phone ? `: ${esc(c.phone)}` : ""}</li>`).join("")}</ul></section>` : ""}

    <section class="doc-sec sign">
      <p>I declare that this log was produced from recordings made at my residence and is accurate to the best of my knowledge.</p>
      <div class="sigline"><span>Signature</span><span>Date</span></div>
    </section>
  </article>`;
}
PAGES.report = renderReport;
