// Front end for the Flask API in app.py. No build step: plain JS, Plotly, marked and DOMPurify.
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

// Everything the page needs to remember lives here; views re-render from it.
const state = {
  overview: null,
  dataVersion: 0, // bumped after an upload/reset so every view reloads
  loaded: {},     // view name -> dataVersion it last rendered
  chart: { metric: "calories", order: "highest" },
  explore: { dataset: "drinks", sort: null, desc: false },
  focus: "overview",
  summaries: {},  // "focus:version" -> markdown, so switching focus doesn't call the LLM again
  history: [],
  view: "overview",
};

// Helpers

// fetch() wrapper: parses JSON or text and turns API errors into thrown Errors with the server's message.
async function api(url, options = {}) {
  const res = await fetch(url, options);
  const isJson = res.headers.get("content-type")?.includes("json");
  const body = isJson ? await res.json() : await res.text();
  if (!res.ok) {
    const err = new Error(body.error || "Something went wrong. Try again.");
    err.details = body.problems;
    throw err;
  }
  return body;
}

function toast(message) {
  const el = $("#toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (el.hidden = true), 4000);
}

const fmt = (n, digits = 1) =>
  n === null || n === undefined ? "–" : Number(n).toLocaleString(undefined, { maximumFractionDigits: digits });

const escapeHtml = (s) =>
  String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const markdown = (text) => DOMPurify.sanitize(marked.parse(text || ""));

const metricLabel = (m) => {
  const spec = state.overview.metrics[m];
  return `${spec.label} (${spec.unit})`;
};

// Animate numbers up from zero (once, on first render of a value).
function countUp(el) {
  const target = parseFloat(el.dataset.value);
  const digits = Number(el.dataset.digits ?? 1);
  if (Number.isNaN(target) || matchMedia("(prefers-reduced-motion: reduce)").matches) {
    el.textContent = fmt(target, digits);
    return;
  }
  const start = performance.now();
  const step = (now) => {
    const t = Math.min(Math.max((now - start) / 900, 0), 1);
    el.textContent = fmt(target * (1 - Math.pow(1 - t, 3)), digits);
    if (t < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

// Theme

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  $("#theme-toggle").setAttribute("aria-label", theme === "dark" ? "Switch to light mode" : "Switch to dark mode");
  try { localStorage.setItem("theme", theme); } catch (_) { /* storage unavailable */ }
  $$(".chart > div").forEach((el) => el.data && Plotly.relayout(el, themeLayout(el)));
}

function initTheme() {
  let saved = null;
  try { saved = localStorage.getItem("theme"); } catch (_) { /* storage unavailable */ }
  const prefersDark = matchMedia("(prefers-color-scheme: dark)").matches;
  applyTheme(saved || (prefersDark ? "dark" : "light"));
  $("#theme-toggle").addEventListener("click", () =>
    applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));
}

// Routing

const views = ["overview", "charts", "explore", "summary", "ask", "data"];
const loaders = {};

// Hash-based routing (#overview, #charts, ...): show one section and load its data the first time,
// or again after the data changed (tracked by dataVersion).
function showView() {
  const name = views.includes(location.hash.slice(1)) ? location.hash.slice(1) : "overview";
  views.forEach((v) => ($(`#view-${v}`).hidden = v !== name));
  $$(".tabs-list a").forEach((a) => {
    if (a.dataset.view === name) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  moveTabIndicator(name);
  if (loaders[name] && state.loaded[name] !== state.dataVersion) {
    state.loaded[name] = state.dataVersion;
    loaders[name]();
  } else if (name === "charts") {
    $$(".chart > div").forEach((el) => el.data && Plotly.Plots.resize(el));
  }
  window.scrollTo({ top: 0 });
}

// The active tab is a second copy of the tab list, clipped to the current tab's box.
// Changing the clip slides one continuous highlight between tabs.
function moveTabIndicator(name = state.view) {
  state.view = name;
  const link = $(`.tabs-list a[data-view="${name}"]`);
  const track = $(".tabs-track");
  if (!link || !track) return;
  const left = link.offsetLeft;
  const right = track.offsetWidth - (link.offsetLeft + link.offsetWidth);
  const overlay = $(".tabs-active");
  overlay.style.setProperty("--clip-left", `${left}px`);
  overlay.style.setProperty("--clip-right", `${right}px`);
  if (window.innerWidth < 860) link.scrollIntoView({ block: "nearest", inline: "center" });
}

// Overview

async function loadOverview() {
  state.overview = await api("/api/overview");
  const note = $("#source-note");
  note.textContent = state.overview.using_defaults ? "Starbucks sample data" : "Your uploaded data";
  note.classList.toggle("custom", !state.overview.using_defaults);
  renderAverage("drinks");
  renderAverage("food");
  renderRatio();
  renderCaffeine();
  renderDuel();
  renderDatasetCard("drinks");
  renderDatasetCard("food");
}

const LEVEL_WORD = { low: "Low", medium: "Medium", high: "High" };

// Traffic-light level for a single value, using the same thresholds as the server.
function levelOf(metric, value) {
  const { reference_intake: ri, levels } = state.overview;
  if (value === null || value === undefined || !ri[metric] || !levels.limit.includes(metric)) return null;
  const pct = (value / ri[metric]) * 100;
  return pct <= levels.thresholds.low ? "low" : pct <= levels.thresholds.high ? "medium" : "high";
}

// Reveal a group on the next frame so its CSS transition runs from the start state.
const reveal = (el) => requestAnimationFrame(() => requestAnimationFrame(() => el?.classList.add("on")));

// Average item as a colour block: giant kcal number, share of a day, and one chip per nutrient.
function renderAverage(kind) {
  const { stats } = state.overview.datasets[kind];
  const pm = stats.per_metric;
  const order = ["fat", stats.sweetness_metric, "sodium", "caffeine", "protein", "fiber"]
    .filter((m, i, a) => m && pm[m]?.intake && a.indexOf(m) === i);
  const chips = order.map((m, i) => {
    const { pct, level } = pm[m].intake;
    const spec = state.overview.metrics[m];
    const title = level ? `${LEVEL_WORD[level]}: ${fmt(pct, 0)}% of a day` : `${fmt(pct, 0)}% of a day (not rated)`;
    return `<li class="${level || ""}" title="${title}" style="transition-delay:${i * 50}ms">
      <i></i>${spec.label} ${fmt(pm[m].mean)}${spec.unit} <span>${fmt(pct, 0)}%</span></li>`;
  }).join("");
  const energy = pm.calories;
  const sweetNote = stats.sweetness_metric === "carbs" ? "Carbs stand in for sugar; the file has no sugar column." : "";
  const el = $(`#avg-${kind}`);
  el.innerHTML = `
    <h2>${kind === "drinks" ? "Average drink" : "Average food item"} <span class="count">${stats.items} items</span></h2>
    ${energy ? `<div class="big"><span data-value="${energy.mean}" data-digits="0">0</span><small>kcal</small></div>
      <span class="tag">${fmt(energy.intake.pct, 0)}% of a 2,000 kcal day</span>` : ""}
    <ul class="chips" aria-label="Nutrients as a share of a day">${chips}</ul>
    ${sweetNote ? `<p class="block-note">${sweetNote}</p>` : ""}`;
  $$("[data-value]", el).forEach(countUp);
  reveal($(".chips", el));
}

function renderRatio() {
  const cal = state.overview.comparison.find((r) => r.metric === "calories");
  const el = $("#ratio-card");
  if (!cal || !cal.ratio) { el.hidden = true; return; }
  el.hidden = false;
  const foodHigher = cal.higher === "food";
  const ratio = foodHigher ? cal.ratio : cal.drinks / cal.food;
  el.innerHTML = `
    <h2>${foodHigher ? "Food vs drink" : "Drink vs food"}</h2>
    <div class="big"><span data-value="${ratio}" data-digits="1">0</span><span class="times">×</span></div>
    <span class="tag">more calories in ${foodHigher ? "food" : "drinks"}</span>
    <div class="mini-bars" aria-hidden="true">
      <div><span>Drink</span><i class="d" style="--w:${(cal.drinks / Math.max(cal.drinks, cal.food)).toFixed(3)}"></i><b>${fmt(cal.drinks, 0)}</b></div>
      <div><span>Food</span><i class="f" style="--w:${(cal.food / Math.max(cal.drinks, cal.food)).toFixed(3)}"></i><b>${fmt(cal.food, 0)}</b></div>
    </div>
    <p class="block-note">Average kcal per item.</p>`;
  reveal($(".mini-bars", el));
  $$("[data-value]", el).forEach(countUp);
}

function renderCaffeine() {
  const { stats } = state.overview.datasets.drinks;
  const share = stats.items ? stats.caffeinated_items / stats.items : 0;
  const estimated = stats.caffeine_source !== "column";
  const el = $("#caffeine-card");
  el.innerHTML = `
    <h2>Caffeinated drinks</h2>
    <div class="big"><span data-value="${stats.caffeinated_items}" data-digits="0">0</span><small>/ ${stats.items}</small></div>
    <div class="meterline" aria-hidden="true"><i></i></div>
    <dl class="split-stat">
      <div><dt>Caffeinated</dt><dd>${stats.caffeinated_items}</dd></div>
      <div><dt>No caffeine</dt><dd>${stats.items - stats.caffeinated_items}</dd></div>
    </dl>
    <span class="tag">${estimated ? "Estimated from item names" : "From the caffeine column"}</span>
    <p class="block-note">${estimated
      ? "Coffee, espresso, tea and Refreshers count as caffeinated; decaf, herbal and crème drinks do not."
      : `${plural(stats.items - stats.caffeinated_items, "drink")} ${stats.items - stats.caffeinated_items === 1 ? "has" : "have"} no caffeine.`}</p>`;
  $$("[data-value]", el).forEach(countUp);
  requestAnimationFrame(() => requestAnimationFrame(() => ($(".meterline i", el).style.transform = `scaleX(${share.toFixed(3)})`)));
}

function renderDuel() {
  const rows = state.overview.comparison;
  const el = $("#duel");
  el.innerHTML = `
    <div class="duel-legend"><span class="d">Drinks</span><span class="f">Food</span></div>
    ${rows.map((r) => {
      const max = Math.max(r.drinks, r.food) || 1;
      return `<div class="duel-row winner-${r.higher}">
        <div class="bar left"><b>${fmt(r.drinks)}</b><i style="--w:${r.drinks / max}"></i></div>
        <div class="label">${r.label}<small>${r.unit}</small></div>
        <div class="bar right"><i style="--w:${r.food / max}"></i><b>${fmt(r.food)}</b></div>
      </div>`;
    }).join("")}
    <p class="duel-note">${duelNote(rows)}</p>`;
  // Next frame, so the bars grow from their baseline.
  el.classList.remove("on");
  reveal(el);
}

function duelNote(rows) {
  const cal = rows.find((r) => r.metric === "calories");
  if (!cal || !cal.ratio) return "";
  return cal.higher === "food"
    ? `A food item averages <strong>${fmt(cal.ratio)}×</strong> the calories of a drink.`
    : `A drink averages <strong>${fmt(cal.drinks / cal.food)}×</strong> the calories of a food item.`;
}

function renderDatasetCard(kind) {
  const { stats } = state.overview.datasets[kind];
  const pm = stats.per_metric;
  const leaders = ["calories", stats.sweetness_metric, "protein"].filter((m, i, a) => m && pm[m] && a.indexOf(m) === i);
  $(`#stats-${kind}`).innerHTML = `
    <h2>${kind === "drinks" ? "Drinks" : "Food"} in detail <span class="badge">${stats.items} items</span></h2>
    <div class="ratio-line">
      ${stats.fat_protein_ratio != null ? `<span class="tag">Fat-to-protein ${fmt(stats.fat_protein_ratio, 2)} : 1</span>` : ""}
      ${pm.calories ? `<span class="tag">${fmt(pm.calories.total, 0)} kcal across the menu</span>` : ""}
    </div>
    <table class="stat-table">
      <thead><tr><th>Per item</th><th>Mean</th><th>Median</th><th>Min</th><th>Max</th><th>Total</th></tr></thead>
      <tbody>${stats.metrics.map((m) => `<tr><td>${metricLabel(m)}</td><td>${fmt(pm[m].mean)}</td>
        <td>${fmt(pm[m].median)}</td><td>${fmt(pm[m].min)}</td><td>${fmt(pm[m].max)}</td><td>${fmt(pm[m].total, 0)}</td></tr>`).join("")}</tbody>
    </table>
    <ul class="leaders">${leaders.map((m) => `<li><span>Most ${state.overview.metrics[m].label.toLowerCase()}</span>
      <div>${escapeHtml(pm[m].highest.name)} <b>${fmt(pm[m].highest.value)} ${state.overview.metrics[m].unit}</b></div></li>`).join("")}</ul>`;
}

loaders.overview = loadOverview;

// Charts

// Colours come from the CSS variables so charts follow the light/dark theme.
function themeLayout(el) {
  const css = getComputedStyle(document.documentElement);
  const ink = css.getPropertyValue("--ink").trim();
  const grid = css.getPropertyValue("--sunken").trim();
  const edge = css.getPropertyValue("--edge").trim();
  const update = {
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    font: { family: "Archivo, sans-serif", color: ink, size: 13 },
  };
  if (el._fullLayout?.xaxis) {
    Object.assign(update, { "xaxis.gridcolor": grid, "yaxis.gridcolor": grid, "xaxis.zerolinecolor": edge, "yaxis.zerolinecolor": edge, "xaxis.zerolinewidth": 2, "yaxis.zerolinewidth": 2 });
  }
  return update;
}

// Draw a figure, then play a short CSS entrance: a left-to-right wipe for axis charts,
// a zoom-in for pies and sunbursts.
async function drawChart(el, fig) {
  const layout = { ...fig.layout, autosize: true };
  delete layout.template;
  const config = { responsive: true, displaylogo: false, modeBarButtonsToRemove: ["select2d", "lasso2d"] };
  await Plotly.newPlot(el, fig.data, layout, config);
  await Plotly.relayout(el, themeLayout(el));
  const round = fig.data.every((t) => t.type === "pie" || t.type === "sunburst");
  el.classList.remove("wipe", "zoom");
  void el.offsetWidth; // restart the animation when the chart is redrawn
  el.classList.add(round ? "zoom" : "wipe");
}

async function loadCharts() {
  // Plotly comes from a CDN; if it was blocked, say so instead of leaving empty boxes.
  if (!window.Plotly) {
    $$(".chart-grid figure > div").forEach((el) => {
      el.innerHTML = `<p class="empty">The chart library couldn't load. Check your internet connection and reload the page.</p>`;
    });
    return;
  }
  const { metric, order } = state.chart;
  const figs = await api(`/api/charts?metric=${metric}&order=${order}&n=10`);
  const label = state.overview.metrics[metric].label.toLowerCase();
  $("#cap-top").textContent = `${order === "highest" ? "Highest" : "Lowest"} ${label} items`;
  $("#cap-distribution").textContent = `Spread of ${label} across items`;
  $("#cap-categories").textContent = `Average ${label} by category`;
  for (const [name, fig] of Object.entries(figs)) {
    await drawChart($(`#chart-${name}`), fig);
  }
}

function initCharts() {
  const select = $("#chart-metric");
  select.innerHTML = Object.entries(window.METRICS).map(([k, m]) => `<option value="${k}">${m.label}</option>`).join("");
  select.addEventListener("change", () => { state.chart.metric = select.value; loadCharts().catch((e) => toast(e.message)); });
  $$("#view-charts [data-order]").forEach((b) => b.addEventListener("click", () => {
    $$("#view-charts [data-order]").forEach((o) => o.classList.toggle("on", o === b));
    state.chart.order = b.dataset.order;
    loadCharts().catch((e) => toast(e.message));
  }));
}

// Only offer metrics that exist in at least one loaded dataset.
function refreshMetricOptions() {
  const available = new Set(Object.values(state.overview.datasets).flatMap((d) => d.stats.metrics));
  $$("#chart-metric option").forEach((o) => (o.hidden = o.disabled = !available.has(o.value)));
  if (!available.has(state.chart.metric)) state.chart.metric = "calories";
  $("#chart-metric").value = state.chart.metric;
}

loaders.charts = async () => { await ensureOverview(); refreshMetricOptions(); await loadCharts(); };

// Explore

const PRESETS = {
  caffeine: { dataset: "drinks", caffeine: "yes" },
  "light-food": { dataset: "food", calories_below: 500 },
  protein: { dataset: "food", protein_min: 15 },
  clear: {},
};

function exploreQuery() {
  const params = new URLSearchParams({ dataset: state.explore.dataset });
  new FormData($("#filters")).forEach((v, k) => v !== "" && v !== "all" && params.set(k, v));
  if (state.explore.dataset === "food") params.delete("caffeine");
  if (state.explore.sort) { params.set("sort", state.explore.sort); params.set("desc", state.explore.desc ? "1" : "0"); }
  return params.toString();
}

async function loadItems() {
  const query = exploreQuery();
  const data = await api(`/api/items?${query}`);
  $("#download-csv").href = `/api/items.csv?${query}`;
  $("#result-count").innerHTML = `<strong>${data.count}</strong> of ${data.total} ${data.dataset} match`;
  const showCaffeine = data.dataset === "drinks" || data.caffeine_source === "column";
  $(".caffeine-field").hidden = !showCaffeine;
  $("#caffeine-hint").hidden = !(showCaffeine && data.caffeine_source === "name");
  // With real caffeine values the mg column is enough; otherwise show the estimated yes/no flag.
  const cols = data.columns.filter((c) => c !== "caffeinated" || (showCaffeine && data.caffeine_source === "name"));
  const head = (c) => (c === "name" ? "Item" : c === "category" ? "Category" : c === "caffeinated" ? "Caffeine" : metricLabel(c));
  const sortAttr = (c) => (state.explore.sort === c ? ` aria-sort="${state.explore.desc ? "descending" : "ascending"}"` : "");
  const cell = (row, c) => {
    if (c === "caffeinated") return `<span class="caf${row[c] ? " on" : ""}">${row[c] ? "Yes" : "No"}</span>`;
    if (c === "category") return `<span class="tag-cat">${escapeHtml(row[c] ?? "")}</span>`;
    if (c === "name") return escapeHtml(row[c] ?? "");
    const level = levelOf(c, row[c]);
    return level ? `<span class="cell ${level}" title="${LEVEL_WORD[level]} for one item">${fmt(row[c])}</span>` : fmt(row[c]);
  };
  $("#items-table").className = `${data.dataset}-table`;
  $("#items-table").innerHTML = `
    <thead><tr>${cols.map((c) => `<th data-col="${c}" tabindex="0"${sortAttr(c)}>${head(c)}</th>`).join("")}</tr></thead>
    <tbody>${data.rows.length
      ? data.rows.map((r, i) => `<tr style="animation-delay:${Math.min(i, 20) * 18}ms">${cols.map((c) => `<td>${cell(r, c)}</td>`).join("")}</tr>`).join("")
      : `<tr><td colspan="${cols.length}" class="empty">No items match these filters. Loosen a limit or clear filters.</td></tr>`}</tbody>`;
}

function setCategories() {
  const select = $("#filters [name=category]");
  const cats = state.overview.datasets[state.explore.dataset].categories;
  const current = select.value;
  select.innerHTML = `<option value="all">All</option>` + cats.map((c) => `<option>${escapeHtml(c)}</option>`).join("");
  select.value = cats.includes(current) ? current : "all";
}

function setDataset(dataset) {
  state.explore.dataset = dataset;
  $$("#filters [data-dataset]").forEach((b) => b.classList.toggle("on", b.dataset.dataset === dataset));
  setCategories();
}

function initExplore() {
  let timer;
  const refresh = () => { clearTimeout(timer); timer = setTimeout(() => loadItems().catch((e) => toast(e.message)), 220); };
  $("#filters").addEventListener("input", refresh);
  $("#filters").addEventListener("submit", (e) => e.preventDefault());
  $$("#filters [data-dataset]").forEach((b) => b.addEventListener("click", () => { setDataset(b.dataset.dataset); refresh(); }));
  $$("[data-preset]").forEach((b) => b.addEventListener("click", () => {
    const preset = PRESETS[b.dataset.preset];
    $("#filters").reset();
    if (preset.dataset) setDataset(preset.dataset); else setCategories();
    Object.entries(preset).forEach(([k, v]) => { const f = $(`#filters [name=${k}]`); if (f) f.value = v; });
    state.explore.sort = preset.calories_below ? "calories" : preset.protein_min ? "protein" : null;
    state.explore.desc = Boolean(preset.protein_min);
    refresh();
  }));
  const sortBy = (th) => {
    const col = th.dataset.col;
    state.explore.desc = state.explore.sort === col ? !state.explore.desc : col !== "name" && col !== "category";
    state.explore.sort = col;
    loadItems().catch((e) => toast(e.message));
  };
  $("#items-table").addEventListener("click", (e) => { const th = e.target.closest("th"); if (th) sortBy(th); });
  $("#items-table").addEventListener("keydown", (e) => { const th = e.target.closest("th"); if (th && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); sortBy(th); } });
}

loaders.explore = async () => { await ensureOverview(); setCategories(); await loadItems(); };

// AI summary

async function writeSummary(force = false) {
  const out = $("#summary-output");
  const btn = $("#summary-btn");
  const key = `${state.focus}:${state.dataVersion}`;
  if (!force && state.summaries[key]) {
    out.innerHTML = markdown(state.summaries[key]);
    btn.textContent = "Rewrite summary";
    return;
  }
  btn.disabled = true;
  out.innerHTML = `<div class="skeleton"><span></span><span></span><span></span><span></span></div>`;
  try {
    const res = await fetch("/api/summary", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ focus: state.focus }),
    });
    if (!res.ok) throw new Error((await res.json()).error);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let text = "";
    let frame = null;
    out.classList.add("streaming");
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      text += decoder.decode(value, { stream: true });
      // Re-render at most once per frame while text streams in.
      frame ??= requestAnimationFrame(() => { out.innerHTML = markdown(text); frame = null; });
    }
    cancelAnimationFrame(frame);
    out.innerHTML = markdown(text);
    state.summaries[key] = text;
    btn.textContent = "Rewrite summary";
  } catch (err) {
    out.innerHTML = `<p class="empty">${escapeHtml(err.message)}</p>`;
  } finally {
    out.classList.remove("streaming");
    btn.disabled = false;
  }
}

function emptySummary(focus) {
  focus = focus === "overview" ? "the whole menu" : focus;
  return `<div class="empty-state"><p><strong>No summary yet.</strong></p>
    <p>Select <strong>Write summary</strong> and Groq will summarise the current statistics, focused on ${escapeHtml(focus)}.</p></div>`;
}

function initSummary() {
  $("#summary-output").innerHTML = emptySummary("overview");
  $$("#focus button").forEach((b) => b.addEventListener("click", () => {
    $$("#focus button").forEach((o) => o.classList.toggle("on", o === b));
    state.focus = b.dataset.focus;
    const cached = state.summaries[`${state.focus}:${state.dataVersion}`];
    $("#summary-btn").textContent = cached ? "Rewrite summary" : "Write summary";
    $("#summary-output").innerHTML = cached ? markdown(cached) : emptySummary(b.textContent.toLowerCase());
  }));
  $("#summary-btn").addEventListener("click", () => {
    const cached = state.summaries[`${state.focus}:${state.dataVersion}`];
    writeSummary(Boolean(cached));
  });
}

// Ask

function addMessage(role, html, extraClass = "") {
  const el = document.createElement("div");
  el.className = `msg ${role} ${extraClass}`.trim();
  el.innerHTML = html;
  $("#chat").append(el);
  el.scrollIntoView({ behavior: "smooth", block: "end" });
  return el;
}

function stepsHtml(steps) {
  if (!steps?.length) return "";
  const lines = steps.map((s) => `${s.tool}(${JSON.stringify(s.args)})\n→ ${JSON.stringify(s.result)}`).join("\n\n");
  return `<details><summary>How this was calculated</summary><pre>${escapeHtml(lines)}</pre></details>`;
}

async function ask(question) {
  $("#suggestions").hidden = true;
  addMessage("user", escapeHtml(question));
  const pending = addMessage("bot", `<div class="thinking"><i></i><i></i><i></i></div>`);
  const input = $("#ask-input");
  input.disabled = true;
  try {
    const data = await api("/api/ask", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, history: state.history }),
    });
    pending.innerHTML = markdown(data.answer) + stepsHtml(data.steps);
    state.history.push({ role: "user", content: question }, { role: "assistant", content: data.answer });
  } catch (err) {
    pending.classList.add("error");
    pending.textContent = err.message;
  } finally {
    input.disabled = false;
    input.focus();
    pending.scrollIntoView({ behavior: "smooth", block: "end" });
  }
}

function initAsk() {
  $("#ask-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const input = $("#ask-input");
    const q = input.value.trim();
    if (!q) return;
    input.value = "";
    ask(q);
  });
  $$("#suggestions .chip").forEach((c) => c.addEventListener("click", () => ask(c.textContent)));
}

// Upload + data quality

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

function qualityCard(kind, r) {
  const all = Object.keys(state.overview.metrics);
  const missing = all.filter((m) => !r.metrics.includes(m)).map((m) => state.overview.metrics[m].label);
  const items = [
    `${plural(r.rows_read, "row")} read, <strong>${r.rows_kept}</strong> kept`,
    r.dropped_no_data && `${plural(r.dropped_no_data, "row")} with no nutrition values (“-” or blank) removed`,
    r.duplicates_removed && `${plural(r.duplicates_removed, "duplicate row")} removed`,
    r.conflicting_duplicates && `${plural(r.conflicting_duplicates, "item")} listed twice with different values; first kept`,
    r.partial_rows && `${plural(r.partial_rows, "row")} with some missing values; skipped only for those nutrients`,
    `Read as ${r.encoding.toUpperCase()}, separated by “${r.delimiter}”`,
    `Nutrients found: ${r.metrics.map((m) => state.overview.metrics[m].label).join(", ")}`,
    missing.length && `Not in file: ${missing.join(", ")}`,
    r.ignored_columns.length && `Ignored columns: ${r.ignored_columns.map(escapeHtml).join(", ")}`,
  ].filter(Boolean);
  return `<article class="quality ${kind}"><h3><i></i>${kind === "drinks" ? "Drinks" : "Food"}</h3>
    <p class="file">${escapeHtml(r.filename)}</p><ul>${items.map((i) => `<li>${i}</li>`).join("")}</ul></article>`;
}

async function loadData() {
  await ensureOverview();
  $("#quality").innerHTML = ["drinks", "food"].map((k) => qualityCard(k, state.overview.datasets[k].report)).join("");
}

async function dataChanged(message) {
  state.dataVersion += 1;
  state.loaded = {};
  state.history = [];
  state.overview = null;
  await loadData();
  state.loaded.data = state.dataVersion;
  toast(message);
}

function initUpload() {
  const form = $("#upload-form");
  const msg = $("#upload-message");
  $$(".drop", form).forEach((zone) => {
    const input = $("input", zone);
    const name = $(".file-name", zone);
    const update = () => {
      zone.classList.toggle("ready", input.files.length > 0);
      name.textContent = input.files[0]?.name || "Drop a file here or click to choose";
    };
    input.addEventListener("change", update);
    ["dragenter", "dragover"].forEach((ev) => zone.addEventListener(ev, () => zone.classList.add("over")));
    ["dragleave", "drop"].forEach((ev) => zone.addEventListener(ev, () => zone.classList.remove("over")));
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = new FormData(form);
    if (![...data.values()].some((f) => f.name)) {
      msg.className = "form-message bad";
      msg.textContent = "Choose a drinks or food CSV first.";
      return;
    }
    const btn = $("button[type=submit]", form);
    btn.disabled = true;
    msg.className = "form-message";
    msg.textContent = "Uploading and cleaning…";
    try {
      const res = await api("/api/upload", { method: "POST", body: data });
      form.reset();
      $$(".drop", form).forEach((z) => { z.classList.remove("ready"); $(".file-name", z).textContent = "Drop a file here or click to choose"; });
      msg.className = "form-message good";
      msg.textContent = `Loaded ${Object.keys(res.loaded).join(" and ")}. Every page now uses your data.`;
      await dataChanged("Data updated");
    } catch (err) {
      msg.className = "form-message bad";
      msg.innerHTML = escapeHtml(err.message) + (err.details
        ? "<br>" + Object.entries(err.details).map(([k, v]) => `${k}: ${escapeHtml(v)}`).join("<br>") : "");
    } finally {
      btn.disabled = false;
    }
  });

  $("#reset-btn").addEventListener("click", async () => {
    await api("/api/reset", { method: "POST" });
    msg.className = "form-message";
    msg.textContent = "";
    await dataChanged("Back to the Starbucks sample data");
  });
}

loaders.data = loadData;

// Start

async function ensureOverview() {
  if (!state.overview) await loadOverview();
}

window.addEventListener("DOMContentLoaded", () => {
  initTheme();
  initCharts();
  initExplore();
  initSummary();
  initAsk();
  initUpload();
  window.addEventListener("hashchange", () => showView());
  window.addEventListener("resize", () => moveTabIndicator());
  document.fonts?.ready.then(() => moveTabIndicator());
  // Every loader goes through here so failures show a message instead of failing silently.
  Object.keys(loaders).forEach((name) => {
    const run = loaders[name];
    loaders[name] = () => run().catch((err) => { state.loaded[name] = -1; toast(err.message); });
  });
  showView();
});
