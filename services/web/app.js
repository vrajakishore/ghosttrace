"use strict";
// GhostTrace prototype web app. Plain JavaScript, no libraries, no build step.

const $ = (id) => document.getElementById(id);
function el(tag, props, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (k === "class") e.className = v;
    else if (k === "text") e.textContent = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (typeof v === "boolean") { if (v) e.setAttribute(k, ""); else e.removeAttribute(k); }
    else e.setAttribute(k, v);
  }
  for (const kid of kids) if (kid != null) e.append(kid);
  return e;
}
async function api(path, opts) {
  const r = await fetch(path, opts);
  const body = await r.json();
  if (!r.ok) throw new Error(body.error || String(r.status));
  return body;
}
const pct = (x, d = 1) => (x == null ? "n/a" : (100 * x).toFixed(d) + "%");
const num = (x) => Number(x).toLocaleString("en-IN");
const words = (s) => String(s).replace(/_/g, " ");
const LABEL = { recommend: "named for review", review: "several candidates", unresolved: "unresolved",
                confirm: "confirmed", reject: "rejected", need_more: "needs more evidence" };
const tag = (d) => el("span", { class: "tag " + d, text: LABEL[d] || d });

let SUMMARY = null;
const started = {};

// ---------- tabs ----------
function showTab(name) {
  document.querySelectorAll("nav button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
  document.querySelectorAll(".tab").forEach((s) => (s.hidden = s.id !== name));
  if (name !== "replay") pause();
  if (!started[name]) { started[name] = true; ({ replay: initReplay, cases: loadCases, dial: initDial, desk: loadDesk })[name](); }
  if (name === "replay") { sizeCanvas(); draw(); }
}
document.querySelectorAll("nav button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));

// ---------- 1. corridor replay ----------
const R = { data: null, t: 0, playing: false, last: 0, crossPtr: 0, evPtr: 0, fx: [], counts: null, raf: 0 };
const canvas = $("road"), ctx = canvas.getContext("2d");
const COL = { grey: "#9aa3b2", red: "#f1707b", green: "#54b877", amber: "#f2b24d", lane: "rgba(255,255,255,0.06)", ink: "#e8ebf1" };

function sizeCanvas() {
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(320, canvas.clientWidth) * dpr;
  canvas.height = 250 * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}
function xOf(km) {
  const n = R.data.nodes_km, lo = Math.min(...n), hi = Math.max(...n), w = canvas.clientWidth;
  return 40 + ((km - lo) / (hi - lo)) * (w - 80);
}
async function initReplay() {
  for (let h = 0; h < 22; h += 2) $("rHour").append(el("option", { value: h, text: String(h).padStart(2, "0") + ":00" }));
  $("rHour").value = "8";
  $("rPlay").addEventListener("click", () => (R.playing ? pause() : play()));
  $("rReset").addEventListener("click", loadReplay);
  $("rDay").addEventListener("change", loadReplay);
  $("rHour").addEventListener("change", loadReplay);
  window.addEventListener("resize", () => { if (R.data) { sizeCanvas(); draw(); } });
  await loadReplay();
}
async function loadReplay() {
  pause();
  $("rClock").textContent = "Loading…";
  const d = await api(`/api/replay?day=${$("rDay").value}&start_hour=${$("rHour").value}&minutes=120`);
  const mlff = new Set(d.plazas.filter((p) => p.type === "mlff").map((p) => p.km));
  const crossTimes = [], events = [];
  d.trips.forEach((trip, i) => {
    trip.i = 0;
    trip.y = ((i * 7919) % 100) / 100;                       // fixed position inside the lane
    trip.ghostAt = trip.e.length ? trip.e[0][0] : Infinity;
    for (let k = 1; k < trip.w.length - 1; k++) if (mlff.has(trip.w[k][1]) && trip.w[k][0] >= 0) crossTimes.push(trip.w[k][0]);
    for (const e of trip.e) if (e[0] >= 0 && e[0] <= d.seconds) events.push({ t: e[0], plaza: e[1], decision: e[2], top: e[3], id: e[4], cls: trip.c });
  });
  crossTimes.sort((a, b) => a - b);
  events.sort((a, b) => a.t - b.t);
  Object.assign(R, { data: d, crossTimes, events, t: 0, crossPtr: 0, evPtr: 0, fx: [],
                     counts: { cross: 0, ghost: 0, recommend: 0, review: 0, unresolved: 0 } });
  $("feed").replaceChildren();
  sizeCanvas(); step(0); draw();
}
function play() { R.playing = true; R.last = performance.now(); $("rPlay").textContent = "Pause"; R.raf = requestAnimationFrame(frame); }
function pause() { R.playing = false; cancelAnimationFrame(R.raf); if ($("rPlay")) $("rPlay").textContent = "Play"; }
function frame(now) {
  if (!R.playing) return;
  const dt = Math.min(0.1, (now - R.last) / 1000);
  R.last = now;
  step(dt * Number($("rSpeed").value));
  draw();
  if (R.t >= R.data.seconds) return pause();
  R.raf = requestAnimationFrame(frame);
}
function clockLabel() {
  const s = Number($("rHour").value) * 3600 + R.t;
  return `Day ${$("rDay").value}, ${String(Math.floor(s / 3600) % 24).padStart(2, "0")}:${String(Math.floor(s / 60) % 60).padStart(2, "0")}`;
}
function step(simSeconds) {
  R.t = Math.min(R.data.seconds, R.t + simSeconds);
  const c = R.counts, km = Object.fromEntries(R.data.plazas.map((p) => [p.id, p.km]));
  while (R.crossPtr < R.crossTimes.length && R.crossTimes[R.crossPtr] <= R.t) { R.crossPtr++; c.cross++; }
  while (R.evPtr < R.events.length && R.events[R.evPtr].t <= R.t) {
    const e = R.events[R.evPtr++];
    c.ghost++; c[e.decision || "unresolved"]++;
    R.fx.push({ born: performance.now(), x: xOf(km[e.plaza]), x2: e.top ? xOf(km[e.top]) : null, decision: e.decision || "unresolved" });
    const row = el("div", { class: "row", tabindex: "0", role: "button" },
      el("span", { text: `${e.plaza} · ${clockAt(e.t)}` }), tag(e.decision || "unresolved"));
    const open = () => { showTab("cases"); openCase(e.id, "cDetail"); };
    row.addEventListener("click", open);
    row.addEventListener("keydown", (k) => { if (k.key === "Enter" || k.key === " ") { k.preventDefault(); open(); } });
    $("feed").prepend(row);
    while ($("feed").children.length > 12) $("feed").lastChild.remove();
  }
  $("cCross").textContent = num(c.cross); $("cGhost").textContent = num(c.ghost);
  $("cRec").textContent = num(c.recommend); $("cRev").textContent = num(c.review); $("cUnr").textContent = num(c.unresolved);
  $("rClock").textContent = clockLabel();
}
function clockAt(t) {
  const s = Number($("rHour").value) * 3600 + t;
  return `${String(Math.floor(s / 3600) % 24).padStart(2, "0")}:${String(Math.floor(s / 60) % 60).padStart(2, "0")}`;
}
function draw() {
  if (!R.data) return;
  const w = canvas.clientWidth, top = { 1: 96, "-1": 166 }, laneH = 42;
  ctx.clearRect(0, 0, w, 250);
  ctx.fillStyle = COL.lane;
  ctx.fillRect(20, top[1] - 4, w - 40, laneH + 8); ctx.fillRect(20, top[-1] - 4, w - 40, laneH + 8);
  ctx.font = "12px Segoe UI, system-ui, sans-serif"; ctx.textAlign = "center";
  ctx.fillStyle = "rgba(255,255,255,0.45)";
  ctx.fillText("→ direction of travel", w / 2, top[1] - 12); ctx.fillText("← direction of travel", w / 2, top[-1] + laneH + 22);
  for (const p of R.data.plazas) {
    const x = xOf(p.km), legacy = p.type === "legacy";
    ctx.strokeStyle = legacy ? COL.amber : "#7fb4ff"; ctx.lineWidth = legacy ? 5 : 2;
    ctx.setLineDash(legacy ? [] : [6, 4]);
    ctx.beginPath(); ctx.moveTo(x, top[1] - 8); ctx.lineTo(x, top[-1] + laneH + 8); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle = COL.ink; ctx.font = "600 13px Segoe UI, system-ui, sans-serif";
    ctx.fillText(p.id, x, 34);
    ctx.font = "11px Segoe UI, system-ui, sans-serif"; ctx.fillStyle = "rgba(255,255,255,0.6)";
    if (w >= 600) ctx.fillText(legacy ? "boom barrier" : "MLFF gantry", x, 50);
    ctx.fillText(`km ${p.km}`, x, w >= 600 ? 64 : 50);
  }
  const ghosts = [];
  ctx.fillStyle = COL.grey;
  for (const trip of R.data.trips) {
    const wp = trip.w;
    if (R.t < wp[0][0] || R.t > wp[wp.length - 1][0]) continue;
    while (trip.i < wp.length - 2 && wp[trip.i + 1][0] <= R.t) trip.i++;
    while (trip.i > 0 && wp[trip.i][0] > R.t) trip.i--;
    const a = wp[trip.i], b = wp[trip.i + 1];
    const f = b[0] === a[0] ? 0 : (R.t - a[0]) / (b[0] - a[0]);
    const x = xOf(a[1] + (b[1] - a[1]) * f), y = top[trip.d] + trip.y * laneH;
    if (trip.ghostAt <= R.t) ghosts.push([x, y]); else ctx.fillRect(x - 1.2, y - 1.2, 2.4, 2.4);
  }
  ctx.fillStyle = COL.red;
  for (const [x, y] of ghosts) { ctx.beginPath(); ctx.arc(x, y, 3.6, 0, 6.283); ctx.fill(); }
  const now = performance.now();
  R.fx = R.fx.filter((f) => now - f.born < 2600);
  for (const f of R.fx) {
    const age = (now - f.born) / 2600, colour = f.decision === "recommend" ? COL.green : f.decision === "review" ? COL.amber : COL.red;
    ctx.globalAlpha = 1 - age; ctx.strokeStyle = colour; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(f.x, 150, 8 + age * 26, 0, 6.283); ctx.stroke();
    if (f.decision === "recommend" && f.x2 != null) {
      ctx.beginPath(); ctx.moveTo(f.x, 84); ctx.quadraticCurveTo((f.x + f.x2) / 2, 60 - Math.min(40, Math.abs(f.x2 - f.x) / 8), f.x2, 84); ctx.stroke();
    }
    ctx.globalAlpha = 1;
  }
}

// ---------- 2. case file ----------
const HEX = { white: "#f2f3f5", silver: "#c3c8d0", grey: "#7d848f", black: "#22252b", red: "#c0392b", blue: "#2e6fb8",
              brown: "#7a5236", green: "#3d7f4f", orange: "#e07b22", yellow: "#e6c229" };
function vehicleSvg(o, status) {
  const tall = /truck|trailer|bus|van/.test(o.body), y = tall ? 8 : 34, h = tall ? 92 : 66;
  const ns = "http://www.w3.org/2000/svg", svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 160 124"); svg.setAttribute("width", "160");
  svg.setAttribute("role", "img"); svg.setAttribute("aria-label", `Schematic rear view of a ${o.colour} ${words(o.body)}`);
  const add = (name, attrs) => { const n = document.createElementNS(ns, name); for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v); svg.append(n); return n; };
  add("rect", { x: 34, y: 100, width: 20, height: 16, rx: 3, fill: "#15171b" }); add("rect", { x: 106, y: 100, width: 20, height: 16, rx: 3, fill: "#15171b" });
  add("rect", { x: 22, y, width: 116, height: h, rx: tall ? 6 : 14, fill: HEX[o.colour] || "#999", stroke: "#4a5160", "stroke-width": 2 });
  if (!tall) add("rect", { x: 38, y: y + 8, width: 84, height: 22, rx: 6, fill: "#2b3442" });
  add("rect", { x: 27, y: y + h - 28, width: 16, height: 9, rx: 2, fill: "#d8443b" }); add("rect", { x: 117, y: y + h - 28, width: 16, height: 9, rx: 2, fill: "#d8443b" });
  const py = y + h - 22;
  if (status === "missing") add("rect", { x: 58, y: py, width: 44, height: 15, fill: "none", stroke: "#4a5160", "stroke-dasharray": "3 3" });
  else {
    add("rect", { x: 58, y: py, width: 44, height: 15, rx: 2, fill: "#fff", stroke: "#4a5160" });
    if (status === "obstructed_deliberate") { add("rect", { x: 56, y: py + 2, width: 48, height: 11, fill: "#6b5a3a" }); }
    else if (status === "damaged") add("path", { d: `M60 ${py + 13} L74 ${py + 2} L82 ${py + 12} L98 ${py + 3}`, stroke: "#4a5160", fill: "none", "stroke-width": 2 });
    else if (status === "glare_weather") add("ellipse", { cx: 80, cy: py + 7, rx: 30, ry: 12, fill: "#fff", opacity: 0.85 });
  }
  return svg;
}
async function loadCases() {
  if (!$("cFilter").dataset.bound) { $("cFilter").dataset.bound = "1"; $("cFilter").addEventListener("change", loadCases); }
  const f = $("cFilter").value, d = await api(`/api/cases?limit=60${f ? "&decision=" + f : ""}`);
  $("cTotal").textContent = `${num(d.total)} cases, newest 60 shown`;
  $("cList").replaceChildren(...d.cases.map((c) => caseRow(c, "cDetail")));
}
function caseRow(c, target) {
  const row = el("div", { class: "row", tabindex: "0", role: "button", "data-id": c.case_id },
    el("span", { text: `${c.plaza_id} · ${c.time}` }), tag(c.reviewer_decision || c.decision),
    el("small", { text: `${c.observed} · plate ${words(c.plate_status)}` }));
  const open = () => openCase(c.case_id, target);
  row.addEventListener("click", open);
  row.addEventListener("keydown", (k) => { if (k.key === "Enter" || k.key === " ") { k.preventDefault(); open(); } });
  return row;
}
async function openCase(id, target) {
  const c = await api(`/api/cases/${id}`), g = c.ghost, o = g.observed, s = c.settings, box = $(target);
  const aiStatus = await api("/api/health");
  document.querySelectorAll(`#${target === "cDetail" ? "cList" : "kList"} .row`).forEach((r) => r.classList.toggle("sel", r.dataset.id === String(id)));
  const rows = c.candidates.map((k) => el("tr", {},
    el("td", { text: k.vrn }), el("td", { text: `${k.registered.colour} ${k.registered.make} ${k.registered.model}` }),
    el("td", { text: `${k.plaza_id} · ${k.time}` }),
    el("td", { class: "num", text: `${k.offset_minutes > 0 ? "+" : ""}${k.offset_minutes} min` }),
    el("td", { class: "num" }, simBar(k.image_similarity, s.threshold, k.passes), k.image_similarity.toFixed(2)),
    el("td", { text: k.passes ? "passes" : k.in_window ? "below threshold" : "outside window" })));
  const p = c.evidence_pack;
  const aiOutput = el("div", { "aria-live": "polite" });
  const aiButton = el("button", { type: "button", class: "primary", text: "Generate AI explanation", disabled: !aiStatus.foundry_agent_enabled });
  aiButton.addEventListener("click", async () => {
    aiButton.disabled = true;
    aiOutput.replaceChildren(el("p", { class: "muted", text: "Waiting for Foundry…" }));
    try {
      const pack = await api("/v1/evidence-pack", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(c),
      });
      aiOutput.replaceChildren(
        el("p", { class: pack.grounding_ok ? "ok" : "bad", text: `${pack.source} · ${pack.grounding_ok ? "grounding checks passed" : "grounding check failed"}` }),
        el("div", { class: "pack", text: pack.text }),
      );
    } catch (error) {
      aiOutput.replaceChildren(el("p", { class: "bad", text: `Foundry request failed: ${error.message}` }));
    } finally {
      aiButton.disabled = !aiStatus.foundry_agent_enabled;
    }
  });
  const aiPanel = el("section", { class: "ai-explanation" },
    el("h3", { text: "Foundry AI explanation · decision unchanged" }),
    el("p", { class: "muted", text: aiStatus.foundry_agent_enabled
      ? "Optional model call. The engine's decision remains authoritative."
      : "AI is off in this run. Enable Foundry mode on the host to request an explanation." }),
    aiButton,
    aiOutput);
  const visionPanel = visionSection(c, aiStatus);
  box.replaceChildren(
    el("h2", {}, `Case ${c.case_id} `, tag(c.decision)),
    el("div", { class: "facts" }, vehicleSvg(o, g.plate_status),
      el("dl", {}, el("dt", { text: "Gantry" }), el("dd", { text: `${g.plaza_id}, ${g.direction}` }),
        el("dt", { text: "Time" }), el("dd", { text: g.time }),
        el("dt", { text: "Plate status" }), el("dd", { text: words(g.plate_status) }),
        el("dt", { text: "Camera profile" }), el("dd", { text: `${o.colour} ${o.make} ${o.model} (${words(o.body)})` }),
        el("dt", { text: "Rule applied" }), el("dd", { text: `${s.window}-minute window, similarity ≥ ${s.threshold}, margin ${s.margin}` }))),
    el("p", { class: "muted", text: "Schematic drawn from the camera profile. It is not a camera image." }),
    el("h3", { text: `Candidate line-up (${c.candidates.length} registered vehicles of this description near the expected time)` }),
    c.candidates.length ? el("table", {}, el("thead", {}, el("tr", {}, ...["Registration", "Registered as", "Identified at", "Against expected time", "Image similarity", "Result"].map((t) => el("th", { text: t })))), el("tbody", {}, ...rows))
      : el("p", { class: "muted", text: "No registered vehicle of this description was identified at a legacy plaza near the expected time." }),
    el("h3", { text: "Evidence pack" }), el("div", { class: "pack", text: p.text }),
    el("p", {}, el("span", { class: p.grounding_ok ? "ok" : "bad", text: p.grounding_ok ? "Grounding check passed. " : `Grounding check FAILED: ${p.ungrounded_items.join(", ")}. ` }),
      el("span", { class: "muted", text: `Every registration number, toll point and time in the text exists in the case data. Text source: ${p.source}.` })),
    visionPanel,
    aiPanel);
  if (target === "kDetail") {
    const note = el("input", { type: "text", placeholder: "Reviewer note (optional)", "aria-label": "Reviewer note", style: "flex:1;min-width:180px;padding:6px 8px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--ink)" });
    const act = (decision) => async () => {
      await api(`/api/cases/${id}/review`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ decision, note: note.value }) });
      box.replaceChildren(el("p", { class: "ok", text: `Case ${id}: ${LABEL[decision]}. Recorded in the audit log.` }));
      loadDesk();
    };
    box.append(el("div", { class: "actions" }, note,
      el("button", { type: "button", class: "primary", onclick: act("confirm"), text: "Confirm" }),
      el("button", { type: "button", onclick: act("reject"), text: "Reject" }),
      el("button", { type: "button", onclick: act("need_more"), text: "Need more evidence" })));
  } else {
    const out = el("div", {});
    box.append(el("div", { class: "actions" }, el("button", { type: "button", text: "Reveal simulated truth", onclick: async () => {
      const t = await api(`/api/cases/${id}/truth`);
      const verdict = c.decision !== "recommend" ? "The engine did not name a vehicle." : c.top_vrn === t.vrn ? "The engine named the right vehicle." : "The engine named the WRONG vehicle.";
      out.replaceChildren(el("div", { class: "truth" }, el("b", { text: "Simulation only. " }),
        `The vehicle was ${t.vrn} (${t.vehicle}). ${t.kind === "honest" ? "Its plate was unreadable by accident." : "It hid its plate on purpose."} ` +
        `${t.identified_at.length ? "It was identified at " + t.identified_at.join(" and ") + "." : "It was never identified at a legacy plaza."} ${verdict}`));
    } })), out);
  }
}
// ---------- AI vision match ----------
function frameTile(caseId, n, caption) {
  return el("figure", { class: "frame", "data-n": n },
    el("img", { src: `/api/cases/${caseId}/frames/${n}.png`, alt: `Simulated camera frame: ${caption}`, width: "320", height: "240", loading: "lazy" }),
    el("figcaption", { text: caption }));
}
function visionSection(c, status) {
  const enabled = !!status.vision_ai_enabled, out = el("div", { "aria-live": "polite" });
  const grid = el("div", { class: "frames" }, frameTile(c.case_id, 0, `GHOST · ${c.ghost.plaza_id} · ${c.ghost.time}`));
  const button = el("button", { type: "button", class: "primary", text: "Run AI vision match", disabled: !enabled });
  button.addEventListener("click", async () => {
    button.disabled = true;
    out.replaceChildren(el("p", { class: "muted", text: "The vision model is reading every frame…" }));
    try {
      const r = await api("/v1/vision-match", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ case_id: c.case_id }) });
      grid.replaceChildren(grid.firstChild, ...r.lineup.map((k) => frameTile(c.case_id, k.index, `CAND ${k.index} · ${k.vrn} · ${k.plaza_id} · ${k.offset_minutes > 0 ? "+" : ""}${k.offset_minutes} min`)));
      if (r.status !== "ok") { out.replaceChildren(el("p", { class: "bad", text: `AI vision unavailable: ${r.reason}` })); return; }
      const score = Object.fromEntries(r.candidates.map((k) => [k.index, k]));
      grid.querySelectorAll("figure[data-n]").forEach((f) => {
        const k = score[f.dataset.n];
        if (!k) return;
        f.classList.toggle("named", k.vrn === r.named_vrn);
        f.append(el("div", { class: "fp" }, simBar(k.score, 0.6, k.vrn === r.named_vrn), ` ${k.score.toFixed(2)}`));
      });
      const verdict = r.decision === "recommend" ? `AI names ${r.named_vrn} for human review.` : r.decision === "review" ? "AI sees several possible vehicles. A reviewer must look." : "AI cannot name a vehicle from these frames.";
      const agree = c.decision === r.decision && (r.decision !== "recommend" || c.top_vrn === r.named_vrn);
      out.replaceChildren(
        el("p", {}, el("b", { text: verdict }), " ", tag(r.decision),
          el("span", { class: agree ? "ok" : "bad", text: agree ? " Agrees with the engine." : ` Differs from the engine (${LABEL[c.decision]}${c.top_vrn ? " " + c.top_vrn : ""}).` })),
        r.ghost ? el("p", { class: "muted", text: `What the AI read on the ghost frame: ${r.ghost.colour} ${r.ghost.body}, plate ${r.ghost.plate}; marks: ${r.ghost.marks.join(", ") || "none seen"}.` +
          (r.ignored_marks && r.ignored_marks.length ? ` Ignored because read on every frame: ${r.ignored_marks.join(", ")}.` : "") }) : null,
        el("table", {}, el("thead", {}, el("tr", {}, ...["Candidate", "Registration", "Marks the AI read", "Matching", "Conflicting", "Score"].map((t) => el("th", { text: t })))),
          el("tbody", {}, ...r.candidates.map((k) => el("tr", {},
            el("td", { text: `CAND ${k.index}` }), el("td", { text: k.vrn }), el("td", { text: k.fingerprint.marks.join(", ") || "none seen" }),
            el("td", { text: k.matching.join(", ") || "–" }), el("td", { text: k.conflicting.join("; ") || "–" }),
            el("td", { class: "num", text: k.score.toFixed(2) }))))),
        el("p", { class: "muted", text: `Model ${r.model} · ${r.latency_ms} ms · AI notes: ${r.notes}` }));
    } catch (error) {
      out.replaceChildren(el("p", { class: "bad", text: `AI vision request failed: ${error.message}` }));
    } finally {
      button.disabled = !enabled;
    }
  });
  return el("section", { class: "ai-explanation" },
    el("h3", { text: "AI vision match · simulated camera frames" }),
    el("p", { class: "muted", text: enabled
      ? "A Foundry vision model reads the ghost frame and every time-feasible candidate frame. A fixed rule compares what it read. Synthetic renders, not real images. A human reviewer decides."
      : "AI vision is off in this run. Set MODEL_BASE_URL and MODEL_NAME on the host to enable it. Synthetic renders, not real images." }),
    grid, button, out);
}

function simBar(v, thr, pass) {
  const b = el("span", { class: "simbar" + (pass ? " pass" : "") });
  b.append(el("i", { style: `width:${Math.max(0, Math.min(1, v)) * 100}%` }), el("u", { style: `left:${thr * 100}%` }));
  return b;
}

// ---------- 3. reliability dial ----------
const D = { timer: 0 };
async function initDial() {
  SUMMARY = SUMMARY || (await api("/api/summary"));
  const S = SUMMARY;
  $("dWindow").max = S.windows.length - 1;
  for (const p of S.presets) $("dPreset").append(el("option", { value: p, text: p[0].toUpperCase() + p.slice(1) }));
  $("dPreset").value = "central";
  ["dWindow", "dThr", "dPreset"].forEach((id) => $(id).addEventListener("input", refreshDial));
  document.querySelectorAll("input[name=method]").forEach((r) => r.addEventListener("change", () => {
    if (r.checked && r.value === "reported") $("dWindow").value = S.windows.indexOf(10);
    refreshDial();
  }));
  $("dTuned").addEventListener("click", useTuned);
  useTuned();
}
function useTuned() {
  const t = SUMMARY.tuned[$("dPreset").value];
  document.querySelector("input[name=method][value=full]").checked = true;
  if (t) { $("dWindow").value = SUMMARY.windows.indexOf(t.window); $("dThr").value = t.threshold; }
  refreshDial();
}
function refreshDial() { clearTimeout(D.timer); D.timer = setTimeout(drawDial, 60); }
async function drawDial() {
  const S = SUMMARY, method = document.querySelector("input[name=method]:checked").value;
  const window_ = S.windows[Number($("dWindow").value)], thr = Number($("dThr").value), preset = $("dPreset").value;
  $("dWindowV").textContent = window_; $("dThrV").textContent = method === "full" ? thr.toFixed(2) : "not used";
  $("dThr").disabled = $("dPreset").disabled = method !== "full";
  const p = S.camera.presets[preset], same = 1 / (1 + p.noise * p.noise);
  $("dPresetNote").textContent = `Assumption: two photos of the same vehicle score about ${same.toFixed(2)}; two different vehicles of the same model and colour score about ${(p.lookalike_cos * same).toFixed(2)}. No real images were measured.`;
  const q = `method=${method}&window=${window_}&threshold=${thr}&preset=${preset}`;
  const [m, curve] = await Promise.all([api("/api/metrics?" + q), api("/api/curve?" + q)]);
  $("dPrec").textContent = pct(m.precision); $("dPrec").className = m.precision >= 0.99 ? "green" : m.precision >= 0.9 ? "amber" : "red";
  $("dCi").textContent = m.precision_ci ? `95% interval ${pct(m.precision_ci[0])} to ${pct(m.precision_ci[1])} · ${num(m.wrong)} wrong of ${num(m.recommend)} named` : "No vehicle was named at this setting.";
  $("dRes").textContent = pct(m.resolution);
  $("dResN").textContent = `${num(m.correct)} of ${num(m.reachable)} · ${num(m.ghosts)} ghost crossings tested`;
  const parts = [["correct", m.correct, "var(--green)", "Named, correct"], ["wrong", m.wrong, "var(--red)", "Named, wrong"],
                 ["review", m.review, "var(--amber)", "Several candidates"], ["unresolved", m.unresolved, "#9aa3b2", "Unresolved"]];
  $("dBar").replaceChildren(...parts.map(([, n, c]) => el("div", { style: `width:${(100 * n) / m.ghosts}%;background:${c}` })));
  $("dBar").setAttribute("aria-label", parts.map(([, n, , l]) => `${l}: ${n}`).join(", "));
  $("dBarKey").replaceChildren(...parts.map(([, n, c, l]) => el("span", {}, el("i", { class: "sw", style: `background:${c}` }), `${l}: ${num(n)}`)));
  drawChart(curve, method, method === "full" ? thr : window_);
}
function drawChart(curve, method, current) {
  const svg = $("dChart"), ns = "http://www.w3.org/2000/svg", L = 44, T = 14, W = 500, H = 160;
  const xs = curve.map((r) => (method === "full" ? r.threshold : r.window)), lo = Math.min(...xs), hi = Math.max(...xs);
  const X = (v) => L + ((v - lo) / (hi - lo || 1)) * W, Y = (v) => T + (1 - v) * H;
  svg.replaceChildren();
  const add = (name, attrs, text) => { const n = document.createElementNS(ns, name); for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v); if (text != null) n.textContent = text; svg.append(n); };
  for (const v of [0, 0.25, 0.5, 0.75, 1]) { add("line", { x1: L, x2: L + W, y1: Y(v), y2: Y(v), stroke: "var(--line)" }); add("text", { x: L - 6, y: Y(v) + 4, "text-anchor": "end" }, `${v * 100}%`); }
  add("line", { x1: L, x2: L + W, y1: Y(0.99), y2: Y(0.99), stroke: "var(--muted)", "stroke-dasharray": "4 4" });
  add("text", { x: L + W, y: Y(0.99) - 4, "text-anchor": "end" }, "99% line");
  for (const v of xs) add("text", { x: X(v), y: T + H + 16, "text-anchor": "middle" }, method === "full" ? v.toFixed(2) : v);
  add("text", { x: L + W / 2, y: T + H + 34, "text-anchor": "middle" }, method === "full" ? "image similarity threshold" : "time window (minutes)");
  const path = (key) => curve.filter((r) => r[key] != null).map((r, i) => `${i ? "L" : "M"}${X(method === "full" ? r.threshold : r.window)},${Y(r[key])}`).join(" ");
  add("path", { d: path("precision"), fill: "none", stroke: "var(--accent)", "stroke-width": 2.5 });
  add("path", { d: path("resolution"), fill: "none", stroke: "var(--green)", "stroke-width": 2.5 });
  add("line", { x1: X(current), x2: X(current), y1: T, y2: T + H, stroke: "var(--ink)", "stroke-width": 1.5 });
  add("text", { x: L, y: T + H + 54, style: "fill:var(--accent);font-weight:600" }, "— precision: share of named vehicles that are the right vehicle");
  add("text", { x: L, y: T + H + 70, style: "fill:var(--green);font-weight:600" }, "— resolution: share of reachable ghosts that are correctly named");
}

// ---------- 4. reviewer desk ----------
async function loadDesk() {
  if (!$("kRefresh").dataset.bound) { $("kRefresh").dataset.bound = "1"; $("kRefresh").addEventListener("click", loadDesk); }
  const [d, audit] = await Promise.all([api("/api/cases?decision=recommend&limit=60"), api("/api/audit")]);
  const open = d.cases.filter((c) => !c.reviewer_decision).slice(0, 30);
  $("kList").replaceChildren(...(open.length ? open.map((c) => caseRow(c, "kDetail")) : [el("p", { class: "muted", text: "The queue is empty." })]));
  $("kAudit").replaceChildren(...(audit.length ? audit.map((a) => el("div", { class: "row" },
    el("span", {}, `${a.at} · case ${a.case_id} · ${a.actor} `, tag(a.action), a.note ? ` · ${a.note}` : "")))
    : [el("p", { class: "muted", text: "No reviews recorded yet." })]));
}

showTab("replay");
