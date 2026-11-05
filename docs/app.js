/* Postdoc Radar dashboard — vanilla JS, reads window.POSTDOC_DATA from data.js */
(function () {
  "use strict";

  const DATA = window.POSTDOC_DATA || { opportunities: [], pis: [], topics: [], runs: [] };
  const TODAY = new Date(new Date().toISOString().slice(0, 10) + "T00:00:00Z");
  const DAY = 86400000;
  const $ = (sel) => document.querySelector(sel);

  // ── per-browser stars / hidden ──
  const store = {
    get(key) { try { return JSON.parse(localStorage.getItem(key) || "[]"); } catch (e) { return []; } },
    set(key, val) { try { localStorage.setItem(key, JSON.stringify(val)); } catch (e) { /* storage unavailable */ } },
  };
  let starred = new Set(store.get("pr-starred"));
  let hidden = new Set(store.get("pr-hidden"));

  // ── helpers ──
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : "#");
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  function parseDate(s, endOfMonth) {
    if (!s) return null;
    let m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s);
    if (m) return new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
    m = /^(\d{4})-(\d{2})$/.exec(s);
    if (m) return endOfMonth ? new Date(Date.UTC(+m[1], +m[2], 0)) : new Date(Date.UTC(+m[1], +m[2] - 1, 1));
    return null;
  }
  const daysUntil = (d) => (d ? Math.round((d - TODAY) / DAY) : null);
  function fmtDate(s, estimated, endOfMonth) {
    const d = parseDate(s, endOfMonth);
    if (!d) return s || "";
    if (estimated || /^\d{4}-\d{2}$/.test(s)) return (estimated ? "≈ " : "") + MONTHS[d.getUTCMonth()] + " " + d.getUTCFullYear();
    return d.getUTCDate() + " " + MONTHS[d.getUTCMonth()] + " " + d.getUTCFullYear();
  }
  function ago(iso) {
    if (!iso) return "never";
    const days = Math.floor((Date.now() - new Date(iso)) / DAY);
    if (days <= 0) return "today";
    if (days === 1) return "yesterday";
    return days + " days ago";
  }
  const isNew = (o) => daysUntil(parseDate(o.first_seen)) > -7;

  function statusPill(o) {
    const dl = parseDate(o.deadline, true), op = parseDate(o.call_opens);
    const left = daysUntil(dl), toOpen = daysUntil(op);
    switch (o.status) {
      case "open":
        if (left != null) return `<span class="pill ${left <= 14 ? "soon" : "open"}">Open · ${o.dates_estimated ? "≈ " : ""}${left} day${left === 1 ? "" : "s"} left</span>`;
        return '<span class="pill open">Open</span>';
      case "rolling": return '<span class="pill rolling">Rolling · apply any time</span>';
      case "upcoming":
        return `<span class="pill upcoming">${o.dates_estimated ? "Opens " + fmtDate(o.call_opens, true) : "Opens in " + toOpen + " days"}</span>`;
      case "closed": return '<span class="pill closed">Closed</span>';
      default:
        return o.kind === "fellowship" ? '<span class="pill">Call dates not known yet</span>'
                                       : '<span class="pill">Open? · no deadline stated</span>';
    }
  }

  function callPeriod(o) {
    if (o.rolling && !o.deadline) return "Rolling — no fixed deadline";
    const parts = [];
    if (o.call_opens) parts.push("opens " + fmtDate(o.call_opens, o.dates_estimated));
    if (o.deadline) parts.push("deadline " + fmtDate(o.deadline, o.dates_estimated, true));
    if (!parts.length) return o.kind === "fellowship" ? "not known yet — checked automatically on the web" : "dates not stated";
    let s = parts.join(" → ");
    if (o.kind === "fellowship") s += o.dates_estimated ? " (estimated)" : (o.dates_checked ? ` (✓ checked ${o.dates_checked.slice(0, 10)})` : "");
    return s;
  }

  function relBlock(o) {
    return `<div class="rel" title="Relevance for you (0–100)"><b>${o.relevance}</b><div class="bar"><i style="width:${Math.max(0, Math.min(100, o.relevance))}%"></i></div></div>`;
  }

  function piChips(o) {
    const names = [];
    if (o.pi) names.push(o.pi);
    (o.matched_pis || []).forEach((p) => { if (!names.some((n) => n.toLowerCase() === p.toLowerCase())) names.push(p); });
    if (!names.length) return "";
    const listed = new Set((DATA.pis || []).map((p) => p.name));
    return `<div class="row"><span class="label">PI:</span> <span class="chips">${names.map((n) => `<span class="chip ${listed.has(n) ? "pi" : ""}" title="${esc((o.pi_notes || {})[n] || "")}">${listed.has(n) ? "★ " : ""}${esc(n)}</span>`).join("")}</span></div>`;
  }

  function conditions(o) {
    const bits = [];
    if (o.duration) bits.push("<span class='label'>Duration:</span> " + esc(o.duration));
    if (o.salary) bits.push("<span class='label'>Salary:</span> " + esc(o.salary));
    if (o.start_date) bits.push("<span class='label'>Start:</span> " + esc(o.start_date));
    return bits.length ? `<div class="row">${bits.join(" · ")}</div>` : "";
  }

  // ── positions ──
  function positionCard(o) {
    const where = [o.institution, o.location || o.country].filter(Boolean).map(esc).join(" · ");
    const topics = (o.topics || []).length ? ` <span class="chips">${o.topics.map((t) => `<span class="chip">${esc(t)}</span>`).join("")}</span>` : "";
    return `<article class="card ${o.kind} ${o.status === "closed" ? "closed" : ""} ${hidden.has(o.id) ? "hidden-item" : ""}" data-id="${esc(o.id)}">
      <div class="card-head">
        <div style="min-width:0">
          <div class="pills">${isNew(o) ? '<span class="pill new">NEW</span>' : ""}${o.kind === "fellowship" ? '<span class="pill kind upcoming">Fellowship</span>' : ""}${statusPill(o)}</div>
          <h3><a href="${esc(safeUrl(o.url))}" target="_blank" rel="noopener noreferrer">${esc(o.title)}</a></h3>
          <div class="meta">${where || "&nbsp;"}</div>
        </div>
        ${relBlock(o)}
      </div>
      ${piChips(o)}
      <div class="row"><span class="label">Call period:</span> ${esc(callPeriod(o))}${topics}</div>
      ${conditions(o)}
      ${o.summary ? `<div class="summary">${esc(o.summary)}</div>` : ""}
      ${o.eligibility ? `<details><summary>Eligibility</summary>${esc(o.eligibility)}</details>` : ""}
      ${o.relevance_reason ? `<div class="why">Why: ${esc(o.relevance_reason)}</div>` : ""}
      <div class="foot">
        <span class="src">${esc((o.sources || []).join(" · "))} · first seen ${esc(o.first_seen)}</span>
        <span class="actions">
          <button data-act="star" class="${starred.has(o.id) ? "on" : ""}">${starred.has(o.id) ? "★ Starred" : "☆ Star"}</button>
          <button data-act="hide">${hidden.has(o.id) ? "Unhide" : "Hide"}</button>
        </span>
      </div>
    </article>`;
  }

  function renderPositions() {
    const q = $("#q").value.trim().toLowerCase();
    const st = $("#f-status").value, pi = $("#f-pi").value, topic = $("#f-topic").value;
    const min = +$("#f-min").value, sort = $("#f-sort").value;
    const onlyStar = $("#f-star").checked, showHidden = $("#f-hidden").checked;
    let items = (DATA.opportunities || []).filter((o) => o.kind === "position");
    items = items.filter((o) => {
      if (st === "active" && o.status === "closed") return false;
      if (st === "closed" && o.status !== "closed") return false;
      if (o.relevance < min) return false;
      if (pi && !(o.matched_pis || []).includes(pi) && o.pi !== pi) return false;
      if (topic && !(o.topics || []).includes(topic)) return false;
      if (onlyStar && !starred.has(o.id)) return false;
      if (!showHidden && hidden.has(o.id)) return false;
      if (q && ![o.title, o.institution, o.location, o.country, o.pi, o.summary].join(" ").toLowerCase().includes(q)) return false;
      return true;
    });
    const dl = (o) => (parseDate(o.deadline, true) || new Date(8.64e15)).getTime();
    if (sort === "deadline") items.sort((a, b) => dl(a) - dl(b) || b.relevance - a.relevance);
    else if (sort === "new") items.sort((a, b) => (b.first_seen || "").localeCompare(a.first_seen || "") || b.relevance - a.relevance);
    else items.sort((a, b) => b.relevance - a.relevance || dl(a) - dl(b));
    $("#count").textContent = `${items.length} position${items.length === 1 ? "" : "s"} shown`;
    $("#list").innerHTML = items.length ? items.map(positionCard).join("") :
      '<div class="empty">Nothing matches these filters yet. Lower the minimum relevance or wait for the next daily run.</div>';
  }

  // ── fellowships ──
  function fellowshipCard(o) {
    const notes = o.pi_notes || {};
    const hostItems = Object.keys(notes).map((name) => {
      const n = notes[name];
      const cls = n.startsWith("✗") ? "no" : "";
      return `<li class="${cls}"><b>${esc(name)}</b> — ${esc(n)}</li>`;
    }).join("");
    return `<article class="card fellowship" data-id="${esc(o.id)}">
      <div class="card-head">
        <div style="min-width:0">
          <div class="pills">${statusPill(o)}</div>
          <h3><a href="${esc(safeUrl(o.url))}" target="_blank" rel="noopener noreferrer">${esc(o.title)}</a></h3>
          <div class="meta">${esc(o.institution || "")} · Host: ${esc(o.country || "")}</div>
        </div>
        ${relBlock(o)}
      </div>
      <div class="row"><span class="label">Call period:</span> ${esc(callPeriod(o))}</div>
      ${conditions(o)}
      ${o.summary ? `<div class="summary">${esc(o.summary)}</div>` : ""}
      ${o.eligibility ? `<details><summary>Eligibility</summary>${esc(o.eligibility)}</details>` : ""}
      ${hostItems ? `<div class="row"><span class="label">Possible host PIs from your list:</span></div><ul class="hosts">${hostItems}</ul>` :
        '<div class="why">None of your listed PIs is in an eligible host country/institution.</div>'}
    </article>`;
  }

  function renderFellowships() {
    const onlyHosted = $("#fl-hosted").checked;
    const order = { open: 0, rolling: 1, upcoming: 2, unknown: 3, closed: 4 };
    let items = (DATA.opportunities || []).filter((o) => o.kind === "fellowship");
    if (onlyHosted) items = items.filter((o) => (o.matched_pis || []).length);
    items.sort((a, b) => (order[a.status] - order[b.status]) || ((parseDate(a.deadline, true) || 8.64e15) - (parseDate(b.deadline, true) || 8.64e15)));
    $("#fellowship-list").innerHTML = items.length ? items.map(fellowshipCard).join("") :
      '<div class="empty">No fellowship matches a PI on your list yet. Add PIs with a country in inputs/pis.yaml, or untick the filter.</div>';
  }

  // ── by PI ──
  function renderPIs() {
    const opps = DATA.opportunities || [];
    const html = (DATA.pis || []).map((p) => {
      const pos = opps.filter((o) => o.kind === "position" && o.status !== "closed" && ((o.matched_pis || []).includes(p.name) || o.pi === p.name));
      const fel = opps.filter((o) => o.kind === "fellowship" && (o.matched_pis || []).includes(p.name));
      const li = (o) => `<li><a href="${esc(safeUrl(o.url))}" target="_blank" rel="noopener noreferrer">${esc(o.title)}</a> — ${esc(callPeriod(o))}${o.pi_notes && o.pi_notes[p.name] ? `<br><span class="muted small">${esc(o.pi_notes[p.name])}</span>` : ""}</li>`;
      return `<div class="pi-card">
        <h3>${p.website ? `<a href="${esc(safeUrl(p.website))}" target="_blank" rel="noopener noreferrer">${esc(p.name)}</a>` : esc(p.name)}</h3>
        <div class="meta">${esc([p.institution, p.country].filter(Boolean).join(" · "))} · priority ${esc(p.priority)} · searched ${esc(ago(p.last_searched))}</div>
        <h4>Positions (${pos.length})</h4>
        ${pos.length ? `<ul>${pos.map(li).join("")}</ul>` : '<div class="muted small">No open position found yet.</div>'}
        <h4>Fellowships you could apply for with ${esc(p.name.split(" ")[0])} (${fel.length})</h4>
        ${fel.length ? `<ul>${fel.map(li).join("")}</ul>` : '<div class="muted small">None matched (check the PI’s country and your eligibility data).</div>'}
      </div>`;
    }).join("");
    $("#pi-list").innerHTML = html || '<div class="empty">Add PIs to inputs/pis.yaml.</div>';
  }

  // ── activity ──
  function renderActivity() {
    const runs = (DATA.runs || []).slice().reverse();
    const last30 = runs.filter((r) => Date.now() - new Date(r.date) < 30 * DAY);
    const cost30 = last30.reduce((s, r) => s + ((r.ai && r.ai.backend !== "claude-code" && r.ai.cost_usd) || 0), 0);
    const rows = runs.map((r) => {
      const ai = r.ai || {};
      const tasks = (r.search_tasks || []).map((t) => `${t.task} (${t.found})`).join(", ");
      return `<tr><td>${esc((r.date || "").replace("T", " ").slice(0, 16))}</td><td class="num">${r.raw_items ?? ""}</td>
        <td class="num">${r.candidates ?? ""}</td><td class="num">${r.new_positions ?? ""}</td><td class="num">${r.emailed ?? 0}</td>
        <td class="num">${ai.web_searches ?? "–"}</td><td class="num" title="${ai.backend === "claude-code" ? "Claude subscription — list-price equivalent, not billed" : "API charge"}">${ai.cost_usd != null ? (ai.backend === "claude-code" ? "plan ≈$" : "$") + ai.cost_usd.toFixed(2) : "–"}</td>
        <td title="${esc(tasks)}">${esc(tasks.length > 70 ? tasks.slice(0, 70) + "…" : tasks)}</td>
        <td class="num" title="${esc((r.errors || []).join("\n"))}">${(r.errors || []).length}</td></tr>`;
    }).join("");
    const topics = (DATA.topics || []).map((t) => `<li>${esc(t.name)} — searched ${esc(ago(t.last_searched))}</li>`).join("");
    $("#activity-list").innerHTML = `
      <p>API charges over the last 30 days: <b>$${cost30.toFixed(2)}</b> (${last30.length} runs). Runs on the Claude Code backend use your subscription; their "plan ≈$" figure is the list-price equivalent, not a charge.</p>
      <div class="table-wrap"><table>
        <thead><tr><th>Run (UTC)</th><th class="num">Scanned</th><th class="num">Candidates</th><th class="num">New</th><th class="num">Emailed</th><th class="num">Web searches</th><th class="num">AI cost</th><th>Search tasks</th><th class="num">Warnings</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="9" class="muted">No runs yet.</td></tr>'}</tbody>
      </table></div>
      <h3 style="margin-top:22px;font-size:15px">Topics</h3><ul class="small">${topics}</ul>`;
  }

  // ── header ──
  function renderHeader() {
    const opps = DATA.opportunities || [];
    const pos = opps.filter((o) => o.kind === "position" && o.status !== "closed");
    const threshold = DATA.notify_threshold || 65;
    const strong = pos.filter((o) => o.relevance >= threshold).length;
    const newWeek = pos.filter(isNew).length;
    const hostedFel = opps.filter((o) => o.kind === "fellowship" && (o.matched_pis || []).length && o.status !== "closed").length;
    const soon = opps.filter((o) => o.status === "open" && o.relevance >= threshold && (daysUntil(parseDate(o.deadline, true)) ?? 999) <= 21).length;
    $("#title").textContent = DATA.title || "Postdoc Radar";
    document.title = DATA.title || "Postdoc Radar";
    $("#subtitle").textContent = DATA.generated_at ? `Updated ${ago(DATA.generated_at)} (${DATA.generated_at.slice(0, 16).replace("T", " ")} UTC)` : "No data yet — run the search once.";
    const stat = (n, label) => `<div class="stat"><b>${n}</b><span>${label}</span></div>`;
    $("#stats").innerHTML = stat(strong, "strong matches") + stat(newWeek, "new this week") + stat(hostedFel, "fellowships w/ host") + stat(soon, "deadlines ≤ 3 wk");
  }

  function fillSelects() {
    const piSel = $("#f-pi"), tSel = $("#f-topic");
    (DATA.pis || []).forEach((p) => piSel.insertAdjacentHTML("beforeend", `<option>${esc(p.name)}</option>`));
    (DATA.topics || []).forEach((t) => tSel.insertAdjacentHTML("beforeend", `<option>${esc(t.name)}</option>`));
  }

  // ── wiring ──
  function showTab(name) {
    document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
    document.querySelectorAll(".tab").forEach((s) => { s.hidden = s.id !== "tab-" + name; });
    try { localStorage.setItem("pr-tab", name); } catch (e) { /* ignore */ }
    try { history.replaceState(null, "", "#" + name); } catch (e) { /* file:// in some browsers */ }
  }
  document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
  ["#q", "#f-status", "#f-pi", "#f-topic", "#f-sort", "#f-min", "#f-star", "#f-hidden"].forEach((id) =>
    $(id).addEventListener("input", () => { $("#f-min-out").textContent = $("#f-min").value; renderPositions(); }));
  $("#fl-hosted").addEventListener("input", renderFellowships);
  $("#list").addEventListener("click", (ev) => {
    const btn = ev.target.closest("button[data-act]");
    if (!btn) return;
    const id = btn.closest(".card").dataset.id;
    const set = btn.dataset.act === "star" ? starred : hidden;
    set.has(id) ? set.delete(id) : set.add(id);
    store.set("pr-starred", [...starred]);
    store.set("pr-hidden", [...hidden]);
    renderPositions();
  });

  fillSelects();
  renderHeader();
  renderPositions();
  renderFellowships();
  renderPIs();
  renderActivity();
  let tab = (location.hash || "").slice(1);
  if (!document.getElementById("tab-" + tab)) {
    tab = "positions";
    try { tab = localStorage.getItem("pr-tab") || tab; } catch (e) { /* ignore */ }
  }
  if (document.getElementById("tab-" + tab)) showTab(tab);
})();
