/* HANF dashboard - plain JavaScript, no build step. Every button calls the real backend API. */
"use strict";

// ------------------------------------------------------------------ helpers
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const SERIES = () => ["--s1", "--s2", "--s3", "--s4", "--s5", "--s6", "--s7", "--s8"].map(css);
const fmt = (v, d = 1) => (v === null || v === undefined || Number.isNaN(v)) ? "—" :
  (typeof v === "number" ? (Math.abs(v) >= 1000 ? v.toLocaleString(undefined, { maximumFractionDigits: 0 }) : v.toFixed(d)) : esc(v));
const pct = (v, d = 1) => (v === null || v === undefined) ? "—" : `${(v * 100).toFixed(d)}%`;

function toast(msg, kind = "ok") {
  const t = $("#toast");
  t.className = `toast show ${kind}`;
  t.innerHTML = msg;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.className = "toast"), kind === "err" ? 8000 : 3500);
}

async function api(path, opts = {}) {
  const init = { method: opts.method || (opts.body ? "POST" : "GET"), headers: {} };
  if (opts.body !== undefined) {
    init.headers["content-type"] = "application/json";
    init.body = JSON.stringify(opts.body);
  }
  const res = await fetch(path, init);
  const text = await res.text();
  let data;
  try { data = text ? JSON.parse(text) : {}; } catch { data = { detail: text }; }
  if (!res.ok) {
    let d = data.detail;
    if (Array.isArray(d)) d = d.map((x) => x.msg).join("; ");
    throw new Error(`${res.status}: ${d || res.statusText}`);
  }
  return data;
}

async function busy(btn, fn) {
  const label = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = "Working…";
  try { return await fn(); }
  catch (e) { toast(esc(e.message), "err"); }
  finally { btn.disabled = false; btn.innerHTML = label; }
}

const pill = (text, kind) => `<span class="pill ${kind}">${esc(text)}</span>`;
const statusKind = (s) => ({ connected: "good", emulated: "warn", ok: "good", degraded: "warn", connecting: "info2",
  unavailable: "bad", disabled: "", error: "bad", not_found: "warn", denied: "bad", rejected: "bad",
  completed: "good", partial: "warn", failed: "bad", anomaly: "bad", normal: "good", alert: "bad", pass: "good",
  done: "good", running: "info2", queued: "", completed_with_failures: "warn" }[s] ?? "");

function table(el, cols, rows, opts = {}) {
  const head = `<tr>${cols.map((c) => `<th class="${c.num ? "num" : ""}">${esc(c.label)}</th>`).join("")}</tr>`;
  const body = rows.length ? rows.map((r) => `<tr class="${opts.rowClass ? opts.rowClass(r) : ""}">${cols.map((c) => {
    const v = c.render ? c.render(r) : esc(r[c.key]);
    return `<td class="${c.num ? "num" : ""}">${v ?? ""}</td>`;
  }).join("")}</tr>`).join("") : `<tr><td colspan="${cols.length}" class="muted">No rows.</td></tr>`;
  el.innerHTML = `<thead>${head}</thead><tbody>${body}</tbody>`;
}

// ------------------------------------------------------------------ charts
const charts = {};
function chartDefaults() {
  Chart.defaults.color = css("--ink-2");
  Chart.defaults.borderColor = css("--grid");
  Chart.defaults.font.family = 'system-ui, -apple-system, "Segoe UI", sans-serif';
  Chart.defaults.font.size = 11;
}
function barChart(id, labels, datasets, opts = {}) {
  chartDefaults();
  const s = SERIES();
  const ds = datasets.map((d, i) => ({
    label: d.label, data: d.data, backgroundColor: d.color || s[i % s.length],
    borderRadius: { topLeft: 4, topRight: 4 }, borderSkipped: "bottom", maxBarThickness: 24,
    categoryPercentage: 0.7, barPercentage: 0.85,
  }));
  if (charts[id]) { charts[id].data.labels = labels; charts[id].data.datasets = ds; charts[id].update("none"); return; }
  charts[id] = new Chart($(`#${id}`), {
    type: "bar", data: { labels, datasets: ds },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      plugins: { legend: { display: datasets.length > 1, position: "bottom", labels: { boxWidth: 10 } },
        tooltip: { callbacks: opts.tooltip ? { label: opts.tooltip } : {} } },
      scales: { x: { grid: { display: false } },
        y: { beginAtZero: true, title: { display: !!opts.ylabel, text: opts.ylabel || "" } } },
    },
  });
}
function lineChart(id, labels, datasets, opts = {}) {
  chartDefaults();
  const s = SERIES();
  const ds = datasets.map((d, i) => ({ label: d.label, data: d.data, borderColor: d.color || s[i % s.length],
    backgroundColor: d.color || s[i % s.length], borderWidth: 2, pointRadius: 4, tension: 0, yAxisID: d.axis || "y",
    stepped: d.stepped || false }));
  if (charts[id]) charts[id].destroy();
  charts[id] = new Chart($(`#${id}`), {
    type: "line", data: { labels, datasets: ds },
    options: { responsive: true, maintainAspectRatio: false, animation: false, interaction: { mode: "index", intersect: false },
      plugins: { legend: { position: "bottom", labels: { boxWidth: 10 } },
        tooltip: { callbacks: opts.tooltip ? { afterBody: opts.tooltip } : {} } },
      scales: { x: { title: { display: !!opts.xlabel, text: opts.xlabel || "" }, grid: { display: false } },
        y: { beginAtZero: true, title: { display: true, text: opts.ylabel || "" } } } },
  });
}

// ------------------------------------------------------------------ navigation
let current = "dashboard";
const loaders = {};
function show(view) {
  current = view;
  $$("#nav button").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${view}`));
  try { localStorage.setItem("hanf-view", view); } catch { /* storage may be blocked */ }
  if (loaders[view]) loaders[view]();
}
$$("#nav button").forEach((b) => b.addEventListener("click", () => show(b.dataset.view)));
$("#theme-toggle").addEventListener("click", () => {
  const dark = document.documentElement.dataset.theme === "dark" ||
    (!document.documentElement.dataset.theme && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "light" : "dark";
  try { localStorage.setItem("hanf-theme", document.documentElement.dataset.theme); } catch { /* ignore */ }
  Object.values(charts).forEach((c) => c.destroy());
  Object.keys(charts).forEach((k) => delete charts[k]);
  if (loaders[current]) loaders[current]();
});

// ------------------------------------------------------------------ dashboard
let lastStatus = null;
loaders.dashboard = async () => {
  try {
    const st = await api("/api/status");
    lastStatus = st;
    const tel = st.telemetry;
    $("#mode-note").textContent = st.redis_mode === "live" ? "Redis: live servers" : "Redis: IN-PROCESS EMULATION";
    const strip = st.adapters.map((a) => pill(`${a.engine}: ${a.status}${a.version ? " " + a.version : ""}`, statusKind(a.status)));
    strip.push(pill(st.seeded ? "dataset seeded" : "dataset not seeded", st.seeded ? "good" : "warn"));
    strip.push(pill(`security model: ${st.security.model ? "ready" : "loading"}`, st.security.model ? "good" : "info2"));
    strip.push(pill(`topology epoch ${st.topology.epoch}: ${st.topology.active.length} active shards${st.topology.migrating ? " (migrating)" : ""}`, st.topology.migrating ? "warn" : "info2"));
    if (st.redis_mode !== "live") strip.push(pill("emulated Redis - not live measurements", "warn"));
    $("#health-strip").innerHTML = strip.join("");
    const src = tel.metric_sources;
    const tile = (label, value, note, key) => `<div class="card tile"><div class="label">${esc(label)}${key ? ` <span class="info" title="${esc(src[key] || "")}">i</span>` : ""}</div><div class="value">${value}</div><div class="note">${note}</div></div>`;
    $("#tiles").innerHTML = [
      tile("Requests", fmt(tel.total_requests, 0), `${fmt(tel.errors, 0)} errors (${pct(tel.error_rate)})`, "total_requests"),
      tile("Throughput, last 10 s", `${fmt(tel.throughput_10s)}/s`, `${fmt(tel.throughput_60s)}/s over 60 s`, "throughput_10s"),
      tile("Latency p50 / p99", `${fmt(tel.latency_ms.p50, 2)} / ${fmt(tel.latency_ms.p99, 2)} ms`, `${tel.latency_ms.samples} recent requests`, "latency_ms"),
      tile("Security windows", fmt(tel.security.windows_scored, 0), `${tel.security.alerts} anomaly verdicts`, "security"),
      tile("Consistency profile", esc(st.consistency.profile), `W=${esc(st.consistency.write_level)}, R=${esc(st.consistency.read_level)} at ${st.consistency.congestion_estimate}× congestion`, "consistency"),
      tile("Active shards", st.topology.active.length, `spare: ${esc(st.topology.spare.join(", ") || "none")}`, null),
      tile("Monitor queue", st.security.queue_length, `mode ${esc(st.security.mode)}, ${st.security.stats.windows_scored || 0} scored`, null),
      tile("Uptime", `${Math.round(st.uptime_s)} s`, `${tel.events_retained}/${tel.retention_max_events} events retained`, null),
    ].join("");
    const eng = Object.entries(tel.by_engine);
    barChart("ch-engine", eng.map((e) => e[0]), [{ label: "requests", data: eng.map((e) => e[1]) }]);
    const sl = Object.entries(tel.shard_load).sort();
    barChart("ch-shardload", sl.map((e) => e[0]), [{ label: "requests", data: sl.map((e) => e[1]) }]);
    const ev = await api("/api/telemetry?limit=40");
    table($("#events-table"), [
      { label: "#", key: "id", num: true },
      { label: "Time", render: (r) => new Date(r.ts * 1000).toLocaleTimeString() },
      { label: "Type", key: "type" }, { label: "Source", key: "source" },
      { label: "Data", render: (r) => `<span class="mono">${esc(JSON.stringify(r.data).slice(0, 180))}</span>` },
    ], ev.events);
  } catch (e) {
    $("#health-strip").innerHTML = pill(`API unreachable: ${e.message}`, "bad");
  }
};
$("#btn-seed").addEventListener("click", (e) => busy(e.target, async () => {
  const r = await api("/api/seed", { body: { reset: $("#seed-reset").checked } });
  toast(`Seeded ${r.redis.records} Redis records + ${r.redis.login_users} login users; MongoDB: ${esc(JSON.stringify(r.mongodb))}; Cassandra: ${esc(JSON.stringify(r.cassandra))} (${r.elapsed_s}s)`);
  loaders.dashboard();
}));
$("#btn-telemetry-reset").addEventListener("click", (e) => busy(e.target, async () => {
  await api("/api/telemetry/reset", { method: "POST" }); loaders.dashboard();
}));

// ------------------------------------------------------------------ playground
const PRESETS = {
  "KV read": { op: "read", key: "product:42" },
  "KV write": { op: "write", key: "product:9001", value: "limited edition mug, 350 ml" },
  "Document write": { op: "write", key: "order:777", value: { customer: "customer:3", status: "new", items: [{ sku: "product:12", qty: 2 }], total: 41.5 } },
  "Document query": { op: "query", key: "", filter: { status: "paid" } },
  "Wide-column write": { op: "write", key: "sensor:3", value: { ts: "2026-10-18T12:00:00", temp_c: 23.4, humidity: 41 } },
  "Wide-column read": { op: "read", key: "sensor:3" },
  "Login (valid)": { op: "login", key: "user7", value: "pass-7" },
  "Login (wrong)": { op: "login", key: "user7", value: "guess" },
  "Scan": { op: "scan", key: "product:1*" },
  "Injection attempt": { op: "query", key: "", filter: { status: { $ne: null } } },
};
$("#presets").innerHTML = Object.keys(PRESETS).map((k) => `<button class="btn" data-preset="${esc(k)}">${esc(k)}</button>`).join("");
$$("#presets .btn").forEach((b) => b.addEventListener("click", () => {
  const p = PRESETS[b.dataset.preset];
  $("#pg-op").value = p.op; $("#pg-key").value = p.key ?? "";
  $("#pg-value").value = p.value === undefined ? "" : (typeof p.value === "string" ? p.value : JSON.stringify(p.value, null, 2));
  $("#pg-filter").value = p.filter ? JSON.stringify(p.filter) : "";
  $("#pg-hint").value = ""; $("#pg-engine").value = "";
}));
function readRequest() {
  const body = { operation: $("#pg-op").value };
  const key = $("#pg-key").value.trim(); if (key) body.key = key;
  const v = $("#pg-value").value.trim();
  if (v) { try { body.value = JSON.parse(v); } catch { body.value = v; } }
  const f = $("#pg-filter").value.trim();
  if (f) body.filter = JSON.parse(f);
  if ($("#pg-hint").value) body.hint = $("#pg-hint").value;
  if ($("#pg-engine").value) body.engine = $("#pg-engine").value;
  if ($("#pg-client").value.trim()) body.client_id = $("#pg-client").value.trim();
  return body;
}
function renderClassification(c, decision) {
  const max = Math.max(...Object.values(c.scores), 1);
  const s = SERIES();
  return Object.entries(c.scores).map(([k, v], i) => `<div class="scorebar"><span class="name">${esc(k)}</span><span class="track"><span class="fill" style="width:${(v / max) * 100}%;background:${s[i]}"></span></span><span class="mono">${v}</span></div>`).join("") +
    `<div class="small" style="margin-top:6px">Winner: <b>${esc(c.workload)}</b> (confidence ${(c.confidence * 100).toFixed(0)}%)</div>
     <ul class="reasons">${c.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>` +
    (decision ? `<div class="small" style="margin-top:6px">Preferred engine <b>${esc(decision.preferred_engine)}</b> → selected <b>${esc(decision.engine)}</b> ${decision.fallback_used ? pill("fallback", "warn") : ""}<br><span class="muted">${esc(decision.reason)}</span></div>` : "");
}
function ringSvg(tokens, shardIds, marker) {
  const s = SERIES(); const cx = 110, cy = 110, r = 88;
  const color = (n) => s[shardIds.indexOf(n) % s.length];
  const ticks = tokens.map((t) => {
    const a = t.position * 2 * Math.PI - Math.PI / 2;
    return `<line x1="${cx + (r - 9) * Math.cos(a)}" y1="${cy + (r - 9) * Math.sin(a)}" x2="${cx + (r + 9) * Math.cos(a)}" y2="${cy + (r + 9) * Math.sin(a)}" stroke="${color(t.node)}" stroke-width="2"/>`;
  }).join("");
  let m = "";
  if (marker !== undefined && marker !== null) {
    const a = marker * 2 * Math.PI - Math.PI / 2;
    m = `<circle cx="${cx + r * Math.cos(a)}" cy="${cy + r * Math.sin(a)}" r="7" fill="${css("--ink")}" stroke="${css("--surface")}" stroke-width="2"/>`;
  }
  return `<svg viewBox="0 0 220 220" width="100%" style="max-width:260px;display:block;margin:auto" role="img" aria-label="hash ring">
    <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${css("--grid")}" stroke-width="1"/>${ticks}${m}
    <text x="${cx}" y="${cy}" text-anchor="middle" font-size="11">${marker !== undefined && marker !== null ? "● = key position" : ""}</text></svg>`;
}
let topoCache = null;
async function topology() { topoCache = (await api("/api/shards")).topology; return topoCache; }

$("#btn-exec").addEventListener("click", (e) => busy(e.target, async () => {
  let body;
  try { body = readRequest(); } catch (err) { throw new Error(`Invalid JSON: ${err.message}`); }
  const r = await api("/api/requests/execute", { body });
  $("#pg-status").innerHTML = pill(r.status, statusKind(r.status));
  $("#pg-outcome").classList.remove("muted");
  $("#pg-outcome").innerHTML = `<dl class="kv">
    <dt>Request id</dt><dd>${r.request_id}</dd>
    <dt>Status</dt><dd>${pill(r.status, statusKind(r.status))} ${r.error ? `<span class="muted">${esc(r.error)}</span>` : ""}</dd>
    <dt>Engine</dt><dd><b>${esc(r.engine)}</b> ${r.routing.fallback_used ? pill("fallback", "warn") : ""}</dd>
    <dt>Latency</dt><dd>${r.latency_ms.toFixed(3)} ms (end-to-end through the pipeline)</dd>
    <dt>Consistency</dt><dd>${esc(r.consistency.level)} (profile ${esc(r.consistency.profile)}) — ${esc(r.consistency.applied)}</dd>
    <dt>Security</dt><dd>${r.security.injection_signal ? pill("injection-style pattern", "warn") : "no injection pattern"} · ${esc(r.security.note)}</dd>
    <dt>Engine meta</dt><dd class="mono">${esc(JSON.stringify(r.meta))}</dd></dl>
    <details style="margin-top:8px"><summary class="small">Submitted request JSON</summary><pre class="json">${esc(JSON.stringify(body, null, 2))}</pre></details>`;
  $("#pg-class").classList.remove("muted");
  $("#pg-class").innerHTML = renderClassification(r.routing.classification, r.routing);
  $("#pg-data").textContent = JSON.stringify(r.data, null, 2);
  if (r.shard) {
    const t = await topology();
    $("#pg-shard").classList.remove("muted");
    $("#pg-shard").innerHTML = ringSvg(t.ring_tokens, t.active, r.shard.ring_position) +
      `<dl class="kv"><dt>Shard</dt><dd><b>${esc(r.shard.id)}</b> (port ${r.shard.port})</dd><dt>Key hash position</dt><dd>${r.shard.ring_position}</dd>
       <dt>Owning token</dt><dd>${r.shard.token_position}</dd><dt>Migration in progress</dt><dd>${r.shard.migration_in_progress}</dd></dl>`;
  } else {
    $("#pg-shard").innerHTML = `<span class="muted">No shard: executed on ${esc(r.engine)}${r.meta && r.meta.shards_scanned ? " (scan touched " + Object.keys(r.meta.shards_scanned).length + " shards)" : ""}.</span>`;
  }
}));
$("#btn-classify").addEventListener("click", (e) => busy(e.target, async () => {
  const d = await api("/api/classify", { body: readRequest() });
  $("#pg-class").classList.remove("muted");
  $("#pg-class").innerHTML = renderClassification(d.classification, d);
  toast("Classification only — nothing was executed.");
}));

// ------------------------------------------------------------------ engines
loaders.engines = async (deep = false) => {
  const d = await api(`/api/adapters${deep ? "?deep=true" : ""}`);
  $("#adapter-cards").innerHTML = d.adapters.map((a) => `<div class="card"><h3>${esc(a.engine)} ${pill(a.status, statusKind(a.status))}</h3>
    <dl class="kv"><dt>Data model</dt><dd>${esc(a.data_model)}</dd><dt>Version</dt><dd>${esc(a.version || "—")}</dd>
    <dt>Verified round trip</dt><dd>${a.verified_round_trip ? pill("yes", "good") : pill("no", "")}</dd>
    <dt>Ping</dt><dd>${a.latency_ms !== null ? a.latency_ms + " ms" : "—"}</dd>
    <dt>Capabilities</dt><dd>${esc(a.capabilities.join(", "))}</dd><dt>Detail</dt><dd class="small">${esc(a.detail)}</dd></dl>
    <div class="btns"><button class="btn" data-reconnect="${esc(a.name)}">Reconnect / re-check</button></div></div>`).join("");
  $$("[data-reconnect]").forEach((b) => b.addEventListener("click", () => busy(b, async () => {
    const h = await api(`/api/adapters/${b.dataset.reconnect}/reconnect`, { method: "POST" });
    toast(`${esc(h.engine)}: ${esc(h.status)} — ${esc(h.detail)}`, h.status === "connected" ? "ok" : "err");
    loaders.engines();
  })));
  const r = d.routing_rules;
  $("#rules").innerHTML = `<table><thead><tr><th>Rule</th><th class="num">Weight</th></tr></thead><tbody>${Object.entries(r.weights).map(([k, v]) => `<tr><td>${esc(k)}</td><td class="num">${v}</td></tr>`).join("")}</tbody></table>
    <h3 style="margin-top:12px">Key prefixes</h3>${Object.entries(r.key_prefixes).map(([k, v]) => `<div class="small"><b>${esc(k)}</b>: ${esc(v.join(", "))}</div>`).join("")}`;
  $("#mapping").innerHTML = `<table><thead><tr><th>Workload class</th><th>Engine</th></tr></thead><tbody>${Object.entries(r.classes).map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join("")}</tbody></table>
    <p class="small">Fallback to the key-value engine when the preferred engine is unavailable: <b>${d.fallback_to_kv ? "enabled" : "disabled"}</b>. Fallback decisions are flagged in every response; fallback writes stay in Redis.</p>`;
};
$("#btn-deep-health").addEventListener("click", (e) => busy(e.target, () => loaders.engines(true)));

// ------------------------------------------------------------------ shards
function renderMigration(m) {
  if (!m) return "No migration yet in this session.";
  const shards = Object.keys(m.keys_before);
  return `<div class="grid g2"><div><dl class="kv">
    <dt>Action</dt><dd>${esc(m.action)} ${pill(m.status, statusKind(m.status))}</dd>
    <dt>Keys before → after</dt><dd>${m.total_before.toLocaleString()} → ${m.total_after.toLocaleString()} ${m.count_reconciled ? pill("count reconciled", "good") : pill("count mismatch", "bad")}</dd>
    <dt>Keys moved</dt><dd>${m.moved.toLocaleString()} of ${m.to_move.toLocaleString()} (${m.remapped_pct}% of all keys)</dd>
    <dt>Verified at destination</dt><dd>${m.verified.toLocaleString()} (byte-identical DUMP)</dd>
    <dt>Failed</dt><dd>${m.failed} ${m.failed ? esc(m.failed_keys.slice(0, 5).join(", ")) : ""}</dd>
    <dt>Kept newer destination value</dt><dd>${m.kept_newer_destination}</dd>
    <dt>Elapsed</dt><dd>${m.elapsed_s} s</dd>
    <dt>Routes</dt><dd class="small">${esc(Object.entries(m.moves_by_route).map(([k, v]) => `${k}: ${v}`).join(", "))}</dd></dl></div>
    <div><table><thead><tr><th>Shard</th><th class="num">Before</th><th class="num">After</th></tr></thead><tbody>${shards.map((s) => `<tr><td>${esc(s)}</td><td class="num">${m.keys_before[s]}</td><td class="num">${m.keys_after[s]}</td></tr>`).join("")}</tbody></table>
    <div class="small" style="margin-top:8px"><b>Sample checks</b>: ${esc(m.sample_checks.map((c) => `${c.key} ${c.from}→${c.to} ✓`).join("; ")) || "—"}</div>
    <div class="log" style="margin-top:8px">${m.log.map(esc).join("<br>")}</div></div></div>`;
}
loaders.shards = async () => {
  const d = await api("/api/shards");
  const t = d.topology; topoCache = t;
  $("#topo-kv").innerHTML = `<dt>Epoch</dt><dd>${t.epoch}</dd><dt>Active</dt><dd>${esc(t.active.join(", "))}</dd>
    <dt>Spare</dt><dd>${esc(t.spare.join(", ") || "none")}</dd><dt>Virtual nodes</dt><dd class="small">${esc(Object.entries(t.vnodes).map(([k, v]) => `${k}: ${v}`).join(", "))}</dd>
    <dt>Ring ownership</dt><dd class="small">${esc(Object.entries(t.ownership).map(([k, v]) => `${k}: ${(v * 100).toFixed(1)}%`).join(", "))}</dd>
    <dt>Load since rebalance</dt><dd class="small">${esc(Object.entries(t.request_load_since_rebalance).map(([k, v]) => `${k}: ${v}`).join(", ") || "—")}</dd>
    <dt>Migration in progress</dt><dd>${t.migrating ? pill("yes - reads fall back to previous owner", "warn") : "no"}</dd>`;
  $("#ring").innerHTML = ringSvg(t.ring_tokens, t.active);
  const s = SERIES();
  $("#ring-legend").innerHTML = t.active.map((id, i) => `<span><i style="background:${s[i % s.length]}"></i>${esc(id)}</span>`).join("");
  const ids = d.health.map((h) => h.id);
  barChart("ch-keys", ids, [{ label: "keys", data: d.health.map((h) => h.keys ?? 0) }]);
  table($("#shard-table"), [
    { label: "Shard", key: "id" }, { label: "Endpoint", render: (r) => `${esc(r.host)}:${r.port}` },
    { label: "Status", render: (r) => pill(r.up ? "up" : "down", r.up ? "good" : "bad") },
    { label: "Active", render: (r) => (r.active ? "yes" : "spare") }, { label: "Virtual nodes", key: "vnodes", num: true },
    { label: "Keys", key: "keys", num: true }, { label: "Ping (ms)", key: "ping_ms", num: true },
  ], d.health);
  $("#migration-out").innerHTML = renderMigration(t.last_migration);
};
async function shardAction(btn, body, label) {
  await busy(btn, async () => {
    const r = await api("/api/shards/rebalance", { body });
    const m = r.migration || r;
    if (m.status) toast(`${label}: ${esc(m.status)} — moved ${m.moved ?? 0} keys, failed ${m.failed ?? 0}`, m.failed ? "err" : "ok");
    else toast(esc(JSON.stringify(r)));
    await loaders.shards();
  });
}
$("#btn-add-shard").addEventListener("click", (e) => shardAction(e.target, { action: "add_shard" }, "Add shard"));
$("#btn-load-rebalance").addEventListener("click", (e) => shardAction(e.target, { action: "rebalance_by_load" }, "Load rebalance"));
$("#btn-reset-shards").addEventListener("click", (e) => shardAction(e.target, { action: "reset" }, "Reset"));
$("#btn-retry").addEventListener("click", (e) => shardAction(e.target, { action: "retry" }, "Retry"));
$("#btn-locate").addEventListener("click", (e) => busy(e.target, async () => {
  const l = await api(`/api/shards/locate?key=${encodeURIComponent($("#locate-key").value)}`);
  $("#locate-out").innerHTML = `<b>${esc(l.key)}</b> → <b>${esc(l.node)}</b> (port ${l.port}); hash position ${l.ring_position.toFixed(6)}, owning token ${l.token_position.toFixed(6)}`;
  const t = topoCache || await topology();
  $("#ring").innerHTML = ringSvg(t.ring_tokens, t.active, l.ring_position);
}));

// ------------------------------------------------------------------ security
function metricRow(name, m) {
  return `<tr><td>${esc(name)}</td><td class="num">${fmt(m.precision, 3)}</td><td class="num">${fmt(m.recall, 3)}</td><td class="num">${fmt(m.f1, 3)}</td><td class="num">${pct(m.fpr)}</td><td class="num">${m.tp}/${m.fp}/${m.tn}/${m.fn}</td></tr>`;
}
function renderEval(d) {
  const e = d.synthetic_evaluation;
  if (!e) return `<span class="muted">No scored synthetic windows yet. Generate traffic first.</span>`;
  const per = Object.keys(e.isolation_forest.per_attack_recall);
  return `<div class="small">${e.windows} scored synthetic windows${d.flushed_windows !== undefined ? ` · ${d.flushed_windows} scored by this click (${d.flush_ms} ms)` : ""}</div>
    <div class="tablewrap"><table><thead><tr><th>Method</th><th class="num">Prec.</th><th class="num">Recall</th><th class="num">F1</th><th class="num">FPR</th><th class="num">TP/FP/TN/FN</th></tr></thead>
    <tbody>${metricRow("Isolation Forest", e.isolation_forest)}${metricRow("Static rules", e.static_rules)}</tbody></table></div>
    <table style="margin-top:6px"><thead><tr><th>Attack recall</th>${per.map((k) => `<th class="num">${esc(k)}</th>`).join("")}</tr></thead>
    <tbody><tr><td>Isolation Forest</td>${per.map((k) => `<td class="num">${pct(e.isolation_forest.per_attack_recall[k], 0)}</td>`).join("")}</tr>
    <tr><td>Static rules</td>${per.map((k) => `<td class="num">${pct(e.static_rules.per_attack_recall[k], 0)}</td>`).join("")}</tr></tbody></table>
    <div class="small muted" style="margin-top:6px">${esc(e.note)}</div>`;
}
loaders.security = async () => {
  const m = await api("/api/security/metrics");
  $("#sec-kv").innerHTML = `<dt>Mode</dt><dd>${esc(m.mode)}</dd><dt>Window / batch</dt><dd>${m.window} requests / ${m.batch} windows</dd>
    <dt>Queue</dt><dd>${m.queue_length} windows${Object.keys(m.partial_windows).length ? `, ${Object.keys(m.partial_windows).length} partial` : ""}</dd>
    <dt>Model</dt><dd>${m.model ? `IF, contamination ${m.model.contamination}, threshold ${m.model.threshold.toFixed(4)}, ${m.model.n_train} training windows` : "loading…"}</dd>
    <dt>Batch scoring</dt><dd>${m.batch_scoring_ms.mean !== null ? `${m.batch_scoring_ms.mean} ms mean over ${m.batch_scoring_ms.batches} batches` : "—"}</dd>
    <dt>Static rules</dt><dd class="small">${esc(Object.entries(m.static_rules).map(([k, v]) => `${k} > ${v}`).join(", "))}</dd>`;
  $("#sec-mode").value = m.mode;
  if (m.synthetic_evaluation) $("#sec-eval").innerHTML = renderEval(m);
  const ev = await api(`/api/security/events?limit=300${$("#sec-only-anom").checked ? "&only=anomaly" : ""}`);
  table($("#sec-table"), [
    { label: "#", key: "id", num: true }, { label: "Client", key: "client_id" },
    { label: "Synthetic label", render: (r) => esc(r.synthetic_label || "—") },
    { label: "Verdict", render: (r) => pill(r.verdict, statusKind(r.verdict)) },
    { label: "Score / thr.", render: (r) => `${r.anomaly_score.toFixed(3)} / ${r.threshold.toFixed(3)}`, num: true },
    { label: "Rules", render: (r) => pill(r.rule_verdict, statusKind(r.rule_verdict)) + (r.rule_reasons.length ? `<div class="small muted">${esc(r.rule_reasons.join("; "))}</div>` : "") },
    { label: "Most deviating features (|z|)", render: (r) => `<span class="small">${r.explanation.map((x) => `${esc(x.feature)}=${x.value} (z ${x.z > 0 ? "+" : ""}${x.z})`).join("<br>")}</span>` },
    { label: "Triage", render: (r) => r.verdict === "anomaly" ? `${esc(r.triage)}<br><button class="btn small" data-triage="${r.id}" data-d="confirmed">confirm</button> <button class="btn small" data-triage="${r.id}" data-d="dismissed">dismiss</button>` : "—" },
  ], ev.events, { rowClass: (r) => (r.verdict === "anomaly" ? "hl" : "") });
  $$("[data-triage]").forEach((b) => b.addEventListener("click", () => busy(b, async () => {
    await api(`/api/security/events/${b.dataset.triage}/triage`, { body: { decision: b.dataset.d } });
    loaders.security();
  })));
};
async function genTraffic(btn, normalOnly) {
  await busy(btn, async () => {
    const kinds = normalOnly ? [] : $$(".tr-kind").filter((c) => c.checked).map((c) => c.value);
    const r = await api("/api/security/generate-traffic", { body: {
      normal_windows: +$("#tr-normal").value, attack_windows: normalOnly ? 0 : +$("#tr-attack").value,
      attack_kinds: kinds.length ? kinds : ["injection"], subtle_fraction: +$("#tr-subtle").value } });
    $("#tr-out").innerHTML = `Executed <b>${r.requests}</b> requests in ${r.elapsed_s}s across ${r.windows.length} windows (seed ${r.seed}).<br>` +
      Object.entries(r.statuses_by_kind).map(([k, v]) => `${esc(k)}: ${esc(Object.entries(v).map(([s, n]) => `${s} ${n}`).join(", "))}`).join("<br>") +
      `<br><span class="muted">Queue now ${r.queue_length} windows (mode ${esc(r.monitor_mode)}).</span>`;
    loaders.security();
  });
}
$("#btn-traffic").addEventListener("click", (e) => genTraffic(e.target, false));
$("#btn-normal-only").addEventListener("click", (e) => genTraffic(e.target, true));
$("#btn-evaluate").addEventListener("click", (e) => busy(e.target, async () => {
  const d = await api("/api/security/evaluate", { method: "POST" });
  $("#sec-eval").innerHTML = renderEval(d);
  loaders.security();
}));
$("#btn-sec-clear").addEventListener("click", (e) => busy(e.target, async () => { await api("/api/security/clear", { method: "POST" }); $("#sec-eval").innerHTML = "Cleared."; loaders.security(); }));
$("#btn-sec-mode").addEventListener("click", (e) => busy(e.target, async () => { await api("/api/security/settings", { body: { mode: $("#sec-mode").value } }); toast(`Mode set to ${esc($("#sec-mode").value)}`); loaders.security(); }));
$("#btn-retrain").addEventListener("click", (e) => busy(e.target, async () => {
  const r = await api("/api/security/train", { body: { contamination: +$("#sec-cont").value } });
  toast(`Retrained on ${r.n_train} normal windows in ${r.train_seconds}s; threshold ${r.threshold}`); loaders.security();
}));
$("#sec-only-anom").addEventListener("change", () => loaders.security());

// ------------------------------------------------------------------ consistency
function renderPredictions(st) {
  table($("#pred-table"), [
    { label: "Profile (W/R)", key: "profile" },
    { label: "Pred. mean (ms)", render: (r) => r.mean_ms.toFixed(1), num: true },
    { label: "Pred. p95 (ms)", render: (r) => r.p95_ms.toFixed(1), num: true },
    { label: "Pred. stale", render: (r) => pct(r.stale_prob, 2), num: true },
    { label: "Meets SLO", render: (r) => (r.eligible ? pill("yes", "good") : pill("no", "bad")) },
  ], st.predictions, { rowClass: (r) => (r.profile === st.profile ? "hl" : "") });
}
loaders.consistency = async () => {
  const st = await api("/api/consistency");
  $("#cons-kv").innerHTML = `<dt>Selected profile</dt><dd><b>${esc(st.profile)}</b> ${st.fallback_used ? pill("fallback ALL/ALL", "warn") : ""}</dd>
    <dt>Congestion estimate</dt><dd>${st.congestion_estimate}×</dd><dt>Staleness SLO</dt><dd>${pct(st.slo, 2)}</dd>
    <dt>Monte-Carlo samples</dt><dd>${st.samples} per profile</dd><dt>Rule</dt><dd class="small">lowest predicted mean latency among profiles with predicted stale-read probability ≤ SLO; otherwise ALL/ALL</dd>`;
  renderPredictions(st);
};
$("#cong").addEventListener("input", () => ($("#cong-val").textContent = $("#cong").value));
$("#btn-cong").addEventListener("click", (e) => busy(e.target, async () => {
  const st = await api("/api/consistency/network", { body: { congestion: +$("#cong").value } });
  toast(`Controller selected ${esc(st.profile)} for ${st.congestion_estimate}× congestion`);
  loaders.consistency();
}));
$("#btn-sim").addEventListener("click", (e) => busy(e.target, async () => {
  const nums = (s) => s.split(",").map((x) => +x.trim()).filter((x) => !Number.isNaN(x));
  const body = { phases: nums($("#sim-phases").value), ops_per_phase: +$("#sim-ops").value, mc_samples: +$("#sim-mc").value,
    slo: +$("#sim-slo").value / 100, seed: +$("#sim-seed").value,
    network: { writer_rtt_ms: nums($("#sim-wrtt").value), reader_rtt_ms: nums($("#sim-rrtt").value),
      jitter_sigma: +$("#sim-jitter").value, read_delay_mean_ms: +$("#sim-delay").value } };
  const r = await api("/api/consistency/simulate", { body });
  const names = Object.keys(r.strategies);
  barChart("ch-sim-lat", names, [{ label: "mean latency (ms)", data: names.map((n) => r.strategies[n].mean_ms) }], { ylabel: "ms" });
  barChart("ch-sim-stale", names, [{ label: "stale reads (%)", data: names.map((n) => r.strategies[n].stale_pct) }], { ylabel: "% of reads" });
  const ad = r.phases.filter((p) => p.strategy === "HANF adaptive");
  const q = r.phases.filter((p) => p.strategy === "static QUORUM/QUORUM");
  lineChart("ch-sim-phase", ad.map((p) => `P${p.phase} (${p.congestion}×)`), [
    { label: "HANF adaptive - mean latency (ms)", data: ad.map((p) => p.observed_mean_ms) },
    { label: "static QUORUM/QUORUM - mean latency (ms)", data: q.map((p) => p.observed_mean_ms) },
  ], { ylabel: "ms", xlabel: "phase (congestion multiplier)", tooltip: (items) => `adaptive profile: ${ad[items[0].dataIndex].profile}` });
  table($("#sim-table"), [
    { label: "Phase", key: "phase", num: true }, { label: "Congestion", key: "congestion", num: true },
    { label: "Estimate used", key: "estimate_used", num: true }, { label: "Chosen profile", key: "profile" },
    { label: "Pred. mean (ms)", key: "predicted_mean_ms", num: true }, { label: "Obs. mean (ms)", key: "observed_mean_ms", num: true },
    { label: "Pred. stale (%)", key: "predicted_stale_pct", num: true }, { label: "Obs. stale (%)", key: "observed_stale_pct", num: true },
  ], ad);
  const s = r.strategies;
  toast(`Simulated in ${r.elapsed_s}s. Adaptive: ${s["HANF adaptive"].mean_ms} ms mean, ${s["HANF adaptive"].stale_pct}% stale; QUORUM: ${s["static QUORUM/QUORUM"].mean_ms} ms.`);
}));

// ------------------------------------------------------------------ benchmarks
const HIST = [
  ["E1", "Workload A — direct / routing / sync ML / batched ML (ops/s)", "16,004 / 16,820 / 7,489 / 14,012"],
  ["E1", "Workload B — direct / routing / sync ML / batched ML (ops/s)", "17,209 / 17,308 / 7,070 / 16,598"],
  ["E1", "Workload C — direct / routing / sync ML / batched ML (ops/s)", "22,341 / 18,877 / 8,183 / 17,183"],
  ["E2", "Keys remapped 4→5 / 10→11, consistent hashing v=128 (%)", "20.7 / 8.1 (modulo: 79.8 / 90.8)"],
  ["E2", "Adaptive rebalancing CV, round 0 → 5", "0.092 → 0.006"],
  ["E2", "Live migration 4→5 shards, 50,000 keys", "10,454 moved in 0.31 s, 0 mismatches"],
  ["E3", "Static rules — P / R / F1 / FPR", "1.000 / 0.653 / 0.790 / 0.0%"],
  ["E3", "Isolation Forest c=0.02 — P / R / F1 / FPR", "0.899 / 0.995 / 0.945 / 2.2%"],
  ["E3", "Single-window scoring latency", "6.4 ms mean"],
  ["E4", "Mean latency ONE / QUORUM / ALL / adaptive (ms)", "4.5 / 54.2 / 239.3 / 26.9"],
  ["E4", "Stale reads ONE / adaptive (%)", "69.8 / 0.009"],
];
table($("#hist-table"), [{ label: "Exp.", render: (r) => r[0] }, { label: "Metric", render: (r) => esc(r[1]) }, { label: "Review II value", render: (r) => esc(r[2]) }], HIST);

let jobPoll = null;
async function startBench(btn, experiment, profile) {
  await busy(btn, async () => {
    const j = await api("/api/benchmarks/run", { body: { experiment, profile } });
    $("#bm-status").innerHTML = `Job ${esc(j.id)} ${pill(j.status, statusKind(j.status))}`;
    clearInterval(jobPoll);
    jobPoll = setInterval(async () => {
      const s = await api(`/api/jobs/${j.id}`);
      $("#bm-status").innerHTML = `Job ${esc(s.id)} (${esc(s.kind)}, ${esc(s.params.profile)}) ${pill(s.status, statusKind(s.status))}${s.error ? ` <span class="muted">${esc(s.error)}</span>` : ""}`;
      $("#bm-log").innerHTML = s.progress.map((p) => `[${p.t}s] ${esc(p.msg)}`).join("<br>");
      $("#bm-log").scrollTop = 1e9;
      if (s.status === "done" || s.status === "failed") {
        clearInterval(jobPoll);
        if (s.result) toast(s.result.map((r) => `${esc(r.run_id)}: ${esc(r.status)}`).join("<br>"), s.status === "done" ? "ok" : "err");
        loaders.benchmarks();
      }
    }, 1500);
  });
}
$("#btn-bench").addEventListener("click", (e) => startBench(e.target, $("#bm-exp").value, $("#bm-profile").value));
$("#btn-full-suite").addEventListener("click", (e) => startBench(e.target, "all", "quick"));
loaders.benchmarks = async () => {
  const d = await api("/api/experiments");
  // Prefer the runs used in the generated IEEE report; otherwise the newest completed "default" run;
  // otherwise the newest completed run of any profile.
  const latest = {};
  const inReport = new Set();
  try {
    const rr = await api("/api/experiments/report-runs");
    Object.values(rr.runs).forEach((x) => inReport.add(x.run_id));
  } catch { /* report not built yet */ }
  const done = d.runs.filter((r) => r.status && r.status.startsWith("completed"));
  done.forEach((r) => { if (!latest[r.experiment] && inReport.has(r.run_id)) latest[r.experiment] = r; });
  done.forEach((r) => { if (!latest[r.experiment] && r.profile === "default") latest[r.experiment] = r; });
  done.forEach((r) => { if (!latest[r.experiment]) latest[r.experiment] = r; });
  $("#bm-latest").innerHTML = ["e1", "e2", "e3", "e4"].map((e) => latest[e]
    ? `<div style="margin-bottom:6px"><b>${e.toUpperCase()}</b> ${esc(d.experiments[e])} ${inReport.has(latest[e].run_id) ? pill("used in IEEE report", "info2") : ""}<br><span class="muted">${esc(latest[e].run_id)} · ${esc(latest[e].profile)} profile · ${new Date(latest[e].started_at * 1000).toLocaleString()} · ${esc(latest[e].evidence)}</span></div>`
    : `<div style="margin-bottom:6px"><b>${e.toUpperCase()}</b> <span class="muted">no completed run yet</span></div>`).join("");
  $("#bm-graphs").innerHTML = Object.values(latest).flatMap((r) => r.files.filter((f) => f.endsWith(".png")).map((f) =>
    `<figure style="margin:0"><img loading="lazy" src="/api/experiments/${encodeURIComponent(r.run_id)}/files/${encodeURIComponent(f)}" alt="${esc(f)}"><figcaption class="small muted">${esc(f)} · ${esc(r.run_id)} (${esc(r.profile)})</figcaption></figure>`)).join("") || `<span class="muted">No graphs yet — run an experiment.</span>`;
  try {
    const jobs = await api("/api/jobs");
    const running = jobs.jobs.find((j) => j.status === "running" || j.status === "queued");
    if (running && !jobPoll) $("#bm-status").innerHTML = `Job ${esc(running.id)} ${pill(running.status, "info2")} ${esc(running.last_progress || "")}`;
  } catch { /* ignore */ }
};

// ------------------------------------------------------------------ experiments
loaders.experiments = async () => {
  const d = await api("/api/experiments");
  table($("#exp-table"), [
    { label: "Run", render: (r) => `<a href="#" data-run="${esc(r.run_id)}">${esc(r.run_id)}</a>` },
    { label: "Experiment", render: (r) => esc(r.title) }, { label: "Profile", key: "profile" },
    { label: "Evidence", key: "evidence" }, { label: "Status", render: (r) => pill(r.status, statusKind(r.status)) },
    { label: "Started", render: (r) => new Date(r.started_at * 1000).toLocaleString() },
    { label: "Duration (s)", key: "duration_s", num: true },
  ], d.runs);
  $$("[data-run]").forEach((a) => a.addEventListener("click", async (ev) => {
    ev.preventDefault();
    const r = await api(`/api/experiments/${encodeURIComponent(a.dataset.run)}`);
    const base = `/api/experiments/${encodeURIComponent(r.run_id)}`;
    $("#exp-actions").innerHTML = `<a class="btn small" href="${base}/download">Download ZIP</a>`;
    const res = { ...r.results };
    delete res.runs; delete res.per_seed; delete res.phases_seed0;
    $("#exp-detail").classList.remove("muted");
    $("#exp-detail").innerHTML = `<dl class="kv"><dt>Status</dt><dd>${pill(r.status, statusKind(r.status))}</dd><dt>Evidence type</dt><dd>${esc(r.evidence)}</dd>
      <dt>Environment</dt><dd class="small">${esc(r.environment.os)} · ${esc(r.environment.cpu)} · ${r.environment.logical_cpus} logical CPUs · ${r.environment.memory_gb} GB · Python ${esc(r.environment.python)} · engines ${esc(JSON.stringify(r.environment.engines))}</dd>
      <dt>Failures</dt><dd>${r.failures.length ? esc(JSON.stringify(r.failures)) : "none"}</dd>
      <dt>Files</dt><dd>${r.files.map((f) => `<a href="${base}/files/${encodeURIComponent(f)}" target="_blank">${esc(f)}</a>`).join(" · ")}</dd></dl>
      <div class="grid g2 graphs" style="margin-top:10px">${r.files.filter((f) => f.endsWith(".png")).map((f) => `<img src="${base}/files/${encodeURIComponent(f)}" alt="${esc(f)}">`).join("")}</div>
      <details style="margin-top:10px"><summary>Configuration</summary><pre class="json">${esc(JSON.stringify(r.config, null, 2))}</pre></details>
      <details><summary>Results (summary)</summary><pre class="json">${esc(JSON.stringify(res, null, 2))}</pre></details>`;
  }));
};
$("#btn-refresh-exp").addEventListener("click", () => loaders.experiments());

// ------------------------------------------------------------------ boot
try {
  const th = localStorage.getItem("hanf-theme");
  if (th) document.documentElement.dataset.theme = th;
  const v = localStorage.getItem("hanf-view");
  if (v && loaders[v]) current = v;
} catch { /* storage unavailable */ }
show(current);
setInterval(() => {
  if (document.hidden) return;
  if (current === "dashboard") loaders.dashboard();
  if (current === "shards") loaders.shards().catch(() => {});
}, 3000);
