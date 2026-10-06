"use strict";
// Backup Hub — dashboard. Nessuna dipendenza esterna; tutto il testo dinamico passa da textContent.

const $ = (id) => document.getElementById(id);
const state = { status: null, runs: [], filter: "", tz: undefined, nextIso: null, busy: false };

// ---------- utilità DOM -------------------------------------------------------
function el(tag, props = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v == null || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) if (kid != null) n.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  return n;
}

// ---------- formattazione -----------------------------------------------------
function fmtBytes(n) {
  n = Number(n) || 0;
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return (i === 0 ? n.toFixed(0) : n.toFixed(n >= 100 ? 0 : 1)) + " " + u[i];
}
function fmtDur(sec) {
  if (sec == null || !isFinite(sec)) return "–";
  sec = Math.round(sec);
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  if (h) return `${h}h ${String(m).padStart(2, "0")}m`;
  if (m) return `${m}m ${String(s).padStart(2, "0")}s`;
  return `${s}s`;
}
function fmtApprox(sec) {
  if (sec == null || !isFinite(sec)) return "–";
  if (sec < 60) return "meno di 1 min";
  const h = Math.floor(sec / 3600), m = Math.round((sec % 3600) / 60);
  if (h) return `circa ${h}h ${String(m).padStart(2, "0")}m`;
  return `circa ${m} min`;
}
function fmtDate(d) {
  d = d instanceof Date ? d : new Date(d);
  return new Intl.DateTimeFormat("it-IT", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: state.tz }).format(d);
}
function fmtAgo(epoch) {
  const s = Math.max(0, Date.now() / 1000 - epoch);
  if (s < 60) return "adesso";
  if (s < 3600) return `${Math.floor(s / 60)} min fa`;
  if (s < 86400) return `${Math.floor(s / 3600)} h fa`;
  const d = Math.floor(s / 86400);
  return d === 1 ? "ieri" : `${d} giorni fa`;
}
function fmtIn(ms) {
  if (ms <= 0) return "in partenza…";
  const s = Math.round(ms / 1000);
  if (s < 90) return `tra ${s} s`;
  const m = Math.floor(s / 60);
  if (m < 90) return `tra ${m} min`;
  const h = Math.floor(m / 60);
  if (h < 48) return `tra ${h}h ${String(m % 60).padStart(2, "0")}m`;
  return `tra ${Math.round(h / 24)} giorni`;
}
const STATUS_IT = { ok: "Riuscito", failed: "Fallito", cancelled: "Annullato", interrupted: "Interrotto", running: "In corso" };

// ---------- rete ----------------------------------------------------------------
async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (_) { /* ignore */ }
    throw new Error(msg);
  }
  return r.json();
}
function toast(msg, bad = false) {
  const t = el("div", { class: "toast" + (bad ? " bad" : ""), role: "status", text: msg });
  $("toasts").append(t);
  setTimeout(() => t.remove(), 4500);
}

// ---------- render ----------------------------------------------------------------
function badge(status, dry) {
  const frag = [el("span", { class: "badge " + status, text: STATUS_IT[status] || status })];
  if (dry) frag.push(el("span", { class: "badge dry", text: "prova" }));
  return frag;
}

function sparkline(values) {
  const box = el("span", { class: "spark", "aria-hidden": "true" });
  if (!values || !values.length) return el("span", { class: "muted", text: "–" });
  const max = Math.max(...values);
  for (const v of values) box.append(el("i", { style: `height:${Math.max(8, (v / max) * 100)}%`, title: fmtDur(v) }));
  return box;
}

function renderHeader() {
  const s = state.status;
  const m = s.manager;
  $("sub").textContent = `${s.jobs.length} job · ${s.timezone} · v${s.version}`;
  const pill = $("pill");
  const last = s.last_run;
  if (m.running) { pill.className = "pill run"; pill.textContent = m.dry_run ? "Prova in corso" : "Backup in corso"; }
  else if (!last) { pill.className = "pill"; pill.textContent = "Nessun backup ancora"; }
  else if (last.status === "ok") { pill.className = "pill ok"; pill.textContent = "Tutto a posto"; }
  else { pill.className = "pill bad"; pill.textContent = "Ultimo backup: " + (STATUS_IT[last.status] || last.status).toLowerCase(); }

  const banner = $("banner");
  banner.hidden = true;
  if (!m.running && last && last.status !== "ok") {
    banner.className = "banner bad";
    banner.textContent = `${labelOf(last.job)}: ${STATUS_IT[last.status].toLowerCase()} ${fmtAgo(last.started_at)}.` + (last.last_error ? ` ${last.last_error}` : "");
    banner.hidden = false;
  } else if (!s.auth) {
    banner.className = "banner warn";
    banner.textContent = "Nessuna password impostata: chiunque sulla rete può avviare backup. Imposta auth.password nel file di configurazione.";
    banner.hidden = false;
  }
}
const labelOf = (name) => (state.status.jobs.find((j) => j.name === name) || {}).label || name;

function renderCards() {
  const s = state.status;
  const m = s.manager;
  // prossimo
  state.nextIso = s.schedule.enabled ? s.schedule.next_run : null;
  if (!s.schedule.enabled) {
    $("next-big").textContent = "Disattivato";
    $("next-when").textContent = "Il backup automatico è spento";
  } else {
    $("next-when").textContent = fmtDate(s.schedule.next_run);
  }
  $("next-est").textContent = s.next_estimate != null
    ? `Durata stimata: ${fmtApprox(s.next_estimate)}`
    : "Durata stimata dopo il primo backup completato";
  tickCountdown();

  // ultimo
  const last = s.last_run;
  if (!last) {
    $("last-big").textContent = "–";
    $("last-big").style.color = "";
    $("last-when").textContent = "Nessun backup eseguito";
    $("last-meta").textContent = "";
  } else {
    const ok = last.status === "ok";
    $("last-big").textContent = STATUS_IT[last.status] || last.status;
    $("last-big").style.color = ok ? "var(--ok)" : "var(--bad)";
    $("last-when").textContent = `${fmtAgo(last.started_at)} · ${fmtDate(last.started_at * 1000)}`;
    const per = s.jobs.filter((j) => j.last_run).map((j) => `${j.label}: ${STATUS_IT[j.last_run.status].toLowerCase()}`).join(" · ");
    $("last-meta").textContent = per;
  }

  // azioni
  $("btn-run").disabled = m.running;
  $("btn-run").lastChild.textContent = m.running ? " In corso…" : " Esegui ora";
  $("btn-cancel").hidden = !m.running;
  $("btn-cancel").disabled = !!m.cancelling;
  $("dry").disabled = m.running;
}

function tickCountdown() {
  const s = state.status;
  if (!s || !s.schedule.enabled || !state.nextIso) return;
  if (s.manager.running) { $("next-big").textContent = "Backup in corso"; return; }
  $("next-big").textContent = fmtIn(new Date(state.nextIso) - Date.now());
}

function td(label, props, ...kids) {
  return el("td", { ...props, "data-l": label }, ...kids);
}

function stat(value, label) {
  return el("div", { class: "stat" }, el("b", { text: value }), el("span", { text: label }));
}

function renderLive() {
  const m = state.status.manager;
  $("live").hidden = !m.running;
  if (!m.running) return;
  const metaBits = [`Avviato ${fmtAgo(m.started_at)}`, m.trigger];
  if (m.total_eta != null) metaBits.push(`fine stimata: ${fmtApprox(m.total_eta).replace("circa ", "tra ")}`);
  if (m.cancelling) metaBits.push("annullamento…");
  $("live-meta").textContent = metaBits.join(" · ");

  const box = $("live-jobs");
  box.replaceChildren();
  for (const d of m.done) {
    box.append(el("div", { class: "jobrow" }, el("div", { class: "jobrow-head" }, el("span", { class: "jobname", text: d.label }), badge(d.status, false))));
  }
  const c = m.current;
  if (c) {
    const p = c.progress || {};
    const hasPct = c.percent != null && p.bytes > 0;
    const bar = el("div", { class: "bar" + (hasPct ? "" : " indet"), role: "progressbar", "aria-label": `Avanzamento ${c.label}`, "aria-valuemin": 0, "aria-valuemax": 100, "aria-valuenow": hasPct ? Math.round(c.percent) : null }, el("span", { style: hasPct ? `width:${c.percent.toFixed(1)}%` : "" }));
    const etaLabel = c.eta != null ? fmtApprox(c.eta) : "calcolo in corso…";
    const etaSrc = c.eta != null && c.eta_source === "storico" ? "stima dai backup precedenti" : "tempo rimanente";
    const stats = el("div", { class: "stats" },
      stat(hasPct ? `${c.percent.toFixed(1)}%` : c.phase, hasPct ? "completato" : "fase"),
      stat(`${fmtBytes(p.bytes)} / ${fmtBytes(p.total_bytes)}`, "dati"),
      stat(p.speed ? `${fmtBytes(p.speed)}/s` : "–", "velocità"),
      stat(`${p.transfers || 0} / ${p.total_transfers || 0}`, "file copiati"),
      stat(`${p.checks || 0} / ${p.total_checks || 0}`, "file controllati"),
      stat(fmtDur(c.elapsed), "trascorso"),
      stat(etaLabel, etaSrc),
      p.errors ? stat(String(p.errors), "errori") : null,
    );
    const files = el("ul", { class: "files" });
    for (const t of (p.transferring || []).slice(0, 4)) files.append(el("li", { text: `${t.percentage}%  ${t.name}` }));
    box.append(el("div", { class: "jobrow" },
      el("div", { class: "jobrow-head" }, el("span", { class: "jobname", text: c.label }), badge("running", m.dry_run)),
      bar, stats, files));
  }
  for (const q of m.queue) {
    box.append(el("div", { class: "jobrow queued" }, el("div", { class: "jobrow-head" }, el("span", { class: "jobname", text: q.label }), el("span", { text: "in coda" }))));
  }
  $("live-log").textContent = (m.log_tail || []).join("\n") || "Nessun messaggio.";
}

function renderJobs() {
  const body = document.querySelector("#jobs-table tbody");
  body.replaceChildren();
  const running = state.status.manager.running;
  for (const j of state.status.jobs) {
    const lr = j.last_run;
    body.append(el("tr", {},
      td("Job", {}, el("b", { text: j.label }), el("div", { class: "sub2", text: j.mode === "sync" ? "sincronizza" : "copia" })),
      td("Percorso", {}, el("span", { class: "path", text: `${j.source} → ${j.dest}` })),
      td("Ultimo esito", {}, lr ? [badge(lr.status, false), el("div", { class: "sub2", text: fmtAgo(lr.started_at) })] : el("span", { class: "muted", text: "mai eseguito" })),
      td("Durata tipica", {}, j.estimate != null ? fmtApprox(j.estimate).replace("circa ", "~") : "–"),
      td("Ultime durate", {}, sparkline(j.history)),
      td("", { class: "num act" }, el("button", { class: "btn small", disabled: running, text: "Esegui", onclick: () => startRun([j.name]) })),
    ));
  }
}

function renderRuns() {
  const body = document.querySelector("#runs-table tbody");
  body.replaceChildren();
  $("runs-empty").hidden = state.runs.length > 0;
  document.querySelector("#runs-table").hidden = state.runs.length === 0;
  for (const r of state.runs) {
    body.append(el("tr", {},
      td("Quando", {}, el("div", { text: fmtDate(r.started_at * 1000) }), el("div", { class: "sub2", text: fmtAgo(r.started_at) })),
      td("Job", { class: "jobcell" }, labelOf(r.job)),
      td("Esito", {}, badge(r.status, r.dry_run), r.last_error && r.status !== "ok" ? el("div", { class: "sub2", text: r.last_error.slice(0, 90) }) : null),
      td("Durata", { class: "num" }, r.status === "running" ? "…" : fmtDur(r.duration)),
      td("Dati", { class: "num" }, fmtBytes(r.bytes)),
      td("File", { class: "num hide-sm" }, String(r.transfers)),
      td("Avvio", { class: "hide-sm" }, r.trigger),
      td("", { class: "num act" }, el("button", { class: "btn small", text: "Log", onclick: () => openLog(r) })),
    ));
  }
}

function renderFilter() {
  const sel = $("filter");
  if (sel.options.length === state.status.jobs.length + 1) return;
  sel.replaceChildren(el("option", { value: "", text: "Tutti i job" }));
  for (const j of state.status.jobs) sel.append(el("option", { value: j.name, text: j.label }));
  sel.value = state.filter;
}

function render() {
  renderHeader(); renderCards(); renderLive(); renderJobs(); renderFilter(); renderRuns();
  $("foot").textContent = `Aggiornato ${new Date().toLocaleTimeString("it-IT")} · notifiche ${state.status.notifications ? "attive" : "non configurate"}`;
}

// ---------- azioni ----------------------------------------------------------------
async function startRun(jobs) {
  try {
    await api("/api/run", { method: "POST", body: JSON.stringify({ jobs, dry_run: $("dry").checked }) });
    toast($("dry").checked ? "Prova avviata (nessun file verrà toccato)" : "Backup avviato");
    await refresh();
  } catch (e) { toast(e.message, true); }
}

async function openLog(run) {
  $("log-title").textContent = `${labelOf(run.job)} · ${fmtDate(run.started_at * 1000)}`;
  $("log-body").textContent = "Caricamento…";
  $("dlg-log").showModal();
  try {
    const d = await api(`/api/runs/${run.id}/log?tail=800`);
    $("log-body").textContent = (d.truncated ? "[… log accorciato, ultime 800 righe …]\n" : "") + d.lines.join("\n");
    $("log-body").scrollTop = $("log-body").scrollHeight;
  } catch (e) { $("log-body").textContent = "Impossibile leggere il log: " + e.message; }
}

const PRESETS = [
  ["Ogni notte 03:30", "30 3 * * *"],
  ["Ogni 6 ore", "0 */6 * * *"],
  ["Ogni giorno 13:00", "0 13 * * *"],
  ["Ogni domenica 04:00", "0 4 * * 0"],
];
let previewTimer = null;
function schedPreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(async () => {
    const box = $("sch-preview");
    const cron = $("sch-cron").value.trim();
    try {
      const d = await api("/api/schedule/preview?cron=" + encodeURIComponent(cron));
      box.className = "preview";
      box.textContent = "Prossime esecuzioni: " + d.next.map((x) => fmtDate(x)).join(" · ");
      $("sch-save").disabled = false;
    } catch (e) {
      box.className = "preview bad";
      box.textContent = "Espressione non valida";
      $("sch-save").disabled = true;
    }
  }, 250);
}
function openSchedule() {
  const s = state.status.schedule;
  $("sch-enabled").checked = s.enabled;
  $("sch-cron").value = s.cron;
  $("sch-test").hidden = !state.status.notifications;
  const pr = $("sch-presets");
  pr.replaceChildren();
  for (const [label, cron] of PRESETS) pr.append(el("button", { class: "btn small", type: "button", text: label, onclick: () => { $("sch-cron").value = cron; schedPreview(); } }));
  schedPreview();
  $("dlg-sched").showModal();
}

// ---------- ciclo di aggiornamento --------------------------------------------------
async function refresh() {
  try {
    const q = "/api/runs?limit=50" + (state.filter ? "&job=" + encodeURIComponent(state.filter) : "");
    const [status, runs] = await Promise.all([api("/api/status"), api(q)]);
    state.status = status; state.runs = runs; state.tz = status.timezone;
    render();
  } catch (e) {
    $("pill").className = "pill bad";
    $("pill").textContent = "Server non raggiungibile";
  }
}
function loop() {
  refresh().finally(() => {
    const running = state.status && state.status.manager.running;
    setTimeout(loop, running ? 1500 : 5000);
  });
}

$("btn-run").addEventListener("click", () => startRun(null));
$("btn-cancel").addEventListener("click", async () => {
  if (!confirm("Fermare il backup in corso? Quello che è già stato copiato resta su Drive.")) return;
  try { await api("/api/cancel", { method: "POST" }); toast("Annullamento richiesto"); refresh(); } catch (e) { toast(e.message, true); }
});
$("btn-schedule").addEventListener("click", openSchedule);
$("sch-cron").addEventListener("input", schedPreview);
$("sch-save").addEventListener("click", async (ev) => {
  ev.preventDefault();
  try {
    await api("/api/schedule", { method: "POST", body: JSON.stringify({ cron: $("sch-cron").value.trim(), enabled: $("sch-enabled").checked }) });
    $("dlg-sched").close(); toast("Orario salvato"); refresh();
  } catch (e) { toast(e.message, true); }
});
$("sch-test").addEventListener("click", async () => {
  try { await api("/api/test-notify", { method: "POST" }); toast("Notifica di prova inviata"); } catch (e) { toast(e.message, true); }
});
$("filter").addEventListener("change", (e) => { state.filter = e.target.value; refresh(); });
setInterval(tickCountdown, 1000);
loop();
