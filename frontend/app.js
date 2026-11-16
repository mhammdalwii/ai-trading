"use strict";

const state = {
  symbols: [], selected: null, tf: "M15", summary: {}, analysis: null, ticks: {},
  config: null, token: localStorage.getItem("dashboardToken") || "",
};
const $ = (id) => document.getElementById(id);
const DIGITS = { XAUUSD: 2, BTCUSD: 2, ETHUSD: 2, EURUSD: 5, GBPUSD: 5, USDJPY: 3, AUDUSD: 5 };
const fmt = (sym, v) => (v == null ? "-" : Number(v).toFixed(DIGITS[sym] ?? 5));
const SIGNAL_LABEL = { BUY: "BUY", SELL: "SELL", WAIT_AND_SEE: "WAIT & SEE", NO_TRADE: "NO TRADE", ERROR: "ERROR" };
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  if (state.token) headers["X-Dashboard-Token"] = state.token;
  const res = await fetch(path, { ...opts, headers });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
  return body;
}

// ---------------------------------------------------------------- chart
let chart, candleSeries, ema50Series, ema200Series, priceLines = [];
function initChart() {
  if (!window.LightweightCharts) { $("chartFallback").classList.remove("hidden"); return; }
  const el = $("chart");
  chart = LightweightCharts.createChart(el, {
    layout: { background: { color: "#171b23" }, textColor: "#8b93a3" },
    grid: { vertLines: { color: "#20252f" }, horzLines: { color: "#20252f" } },
    timeScale: { timeVisible: true, borderColor: "#2a303c" },
    rightPriceScale: { borderColor: "#2a303c" },
    crosshair: { mode: 0 },
    autoSize: true,
  });
  candleSeries = chart.addCandlestickSeries({ upColor: "#26a69a", downColor: "#ef5350", borderVisible: false, wickUpColor: "#26a69a", wickDownColor: "#ef5350" });
  ema50Series = chart.addLineSeries({ color: "#4c8dff", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, title: "EMA50" });
  ema200Series = chart.addLineSeries({ color: "#f5a623", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, title: "EMA200" });
}

function ema(values, period) {
  const k = 2 / (period + 1); const out = []; let prev = null;
  values.forEach((v, i) => {
    prev = prev == null ? v : v * k + prev * (1 - k);
    out.push(i >= period - 1 ? prev : null);
  });
  return out;
}

async function loadChart() {
  if (!chart || !state.selected) return;
  const sym = state.selected;
  const data = await api(`/api/candles/${sym}?timeframe=${state.tf}&count=500`);
  const c = data.candles.map((x) => ({ time: Math.floor(new Date(x.time).getTime() / 1000), open: x.open, high: x.high, low: x.low, close: x.close }));
  const precision = DIGITS[sym] ?? 5;
  candleSeries.applyOptions({ priceFormat: { type: "price", precision, minMove: 1 / 10 ** precision } });
  candleSeries.setData(c);
  const closes = c.map((x) => x.close);
  const line = (arr) => arr.map((v, i) => (v == null ? null : { time: c[i].time, value: v })).filter(Boolean);
  ema50Series.setData(line(ema(closes, 50)));
  ema200Series.setData(line(ema(closes, 200)));
  drawLevels();
}

function drawLevels() {
  if (!candleSeries) return;
  priceLines.forEach((l) => candleSeries.removePriceLine(l)); priceLines = [];
  const a = state.analysis;
  if (!a || a.entry == null || a.symbol !== state.selected) return;
  const add = (price, color, title) => priceLines.push(candleSeries.createPriceLine({ price, color, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title }));
  add(a.entry, "#4c8dff", "Entry"); add(a.stop_loss, "#ef5350", "SL");
  add(a.take_profit_1, "#26a69a", "TP1"); add(a.take_profit_2, "#26a69a", "TP2");
}

// ---------------------------------------------------------------- pairs list
function renderPairs() {
  $("pairs").innerHTML = state.symbols.map((sym) => {
    const s = state.summary[sym] || {}; const t = state.ticks[sym];
    const conf = s.confidence ?? 0;
    return `<button class="pair ${sym === state.selected ? "active" : ""}" data-sym="${sym}">
      <div class="top"><span class="sym">${sym}</span><span class="badge ${esc(s.signal)}">${SIGNAL_LABEL[s.signal] || "…"}</span></div>
      <div class="top"><span class="px mono">${t ? fmt(sym, t.bid) : "-"}</span><span class="muted small">${s.confidence != null ? conf.toFixed(0) + "%" : ""}</span></div>
      <div class="mini"><div style="width:${conf}%"></div></div>
    </button>`;
  }).join("");
}
$("pairs").addEventListener("click", (e) => {
  const btn = e.target.closest(".pair"); if (!btn) return;
  selectSymbol(btn.dataset.sym);
});

async function selectSymbol(sym) {
  state.selected = sym; state.analysis = null;
  localStorage.setItem("selectedSymbol", sym);
  renderPairs(); renderAnalysis();
  $("symTitle").textContent = sym;
  await Promise.all([loadAnalysis(), loadChart().catch(showError)]);
}

// ---------------------------------------------------------------- analysis panel
async function loadAnalysis() {
  const sym = state.selected;
  try {
    const a = await api(`/api/analysis/${sym}`);
    if (sym !== state.selected) return;
    state.analysis = a;
    state.summary[sym] = { ...state.summary[sym], signal: a.signal, confidence: a.confidence };
    renderAnalysis(); renderPairs(); drawLevels();
  } catch (e) { showError(e); }
}

function renderAnalysis() {
  const a = state.analysis; const sym = state.selected;
  const badge = $("sigBadge");
  badge.className = "badge " + (a ? a.signal : "");
  badge.textContent = a ? SIGNAL_LABEL[a.signal] : "Memuat…";
  $("sigTime").textContent = a ? new Date(a.time).toLocaleTimeString("id-ID") : "";
  $("confVal").textContent = a ? `${a.confidence.toFixed(1)}%` : "-";
  $("confBar").style.width = a ? `${a.confidence}%` : "0";
  $("confBar").style.background = !a ? "" : a.confidence > 80 ? "var(--pos)" : a.confidence >= 60 ? "var(--accent)" : "var(--muted)";
  $("lvEntry").textContent = fmt(sym, a?.entry); $("lvSL").textContent = fmt(sym, a?.stop_loss);
  $("lvTP1").textContent = fmt(sym, a?.take_profit_1); $("lvTP2").textContent = fmt(sym, a?.take_profit_2);
  $("warnings").innerHTML = (a?.warnings || []).map((w) => `<li>${esc(w)}</li>`).join("");
  $("factors").innerHTML = (a?.factors || []).map((f) => `<tr>
      <td>${esc(f.name)}<div class="note">${esc(f.note)}</div></td>
      <td class="pts ${f.points > 0 ? "pos" : "muted"}">${f.points} / ${f.max_points}</td></tr>`).join("");
  const tradable = a && (a.signal === "BUY" || a.signal === "SELL");
  const btn = $("execBtn");
  btn.disabled = !tradable;
  btn.textContent = tradable ? `Execute ${a.signal} to MT5` : "Execute to MT5";
}

// ---------------------------------------------------------------- execute
$("execBtn").addEventListener("click", () => {
  const a = state.analysis; if (!a) return;
  const lot = parseFloat($("lot").value);
  const tpKey = $("tpChoice").value;
  const tp = tpKey === "tp1" ? a.take_profit_1 : a.take_profit_2;
  $("confirmText").innerHTML = `<strong>${a.signal} ${esc(a.symbol)}</strong> ${lot} lot<br>
    Entry ±${fmt(a.symbol, a.entry)} · SL ${fmt(a.symbol, a.stop_loss)} · TP ${fmt(a.symbol, tp)}<br>
    Confidence ${a.confidence.toFixed(1)}%${state.config?.data_source === "mock" ? "<br><em>Mode simulasi: order tidak dikirim ke broker.</em>" : ""}`;
  $("confirm").returnValue = "";
  $("confirm").showModal();
});
$("confirm").addEventListener("close", async () => {
  if ($("confirm").returnValue !== "ok") return;
  const a = state.analysis; const msg = $("execMsg");
  msg.className = "exec-msg muted"; msg.textContent = "Mengirim order…";
  $("execBtn").disabled = true;
  try {
    if (state.config?.token_required && !state.token) {
      state.token = prompt("Masukkan token dashboard") || "";
      localStorage.setItem("dashboardToken", state.token);
    }
    const res = await api(`/api/execute/${a.symbol}`, {
      method: "POST",
      body: JSON.stringify({ volume: parseFloat($("lot").value), take_profit: $("tpChoice").value, expected_signal: a.signal }),
    });
    msg.className = "exec-msg pos";
    msg.textContent = `Order terisi: ${res.side} ${res.volume} lot @ ${fmt(a.symbol, res.result.price)} (tiket ${res.result.order})`;
    loadPositions(); loadOrderLog();
  } catch (e) {
    msg.className = "exec-msg neg"; msg.textContent = `Gagal: ${e.message}`;
    if (/token/i.test(e.message)) { state.token = ""; localStorage.removeItem("dashboardToken"); }
    loadOrderLog(); loadAnalysis();
  } finally { renderAnalysis(); }
});

// ---------------------------------------------------------------- positions, news, log
async function loadPositions() {
  try {
    const rows = await api("/api/positions");
    $("positions").innerHTML = rows.length ? rows.map((p) => `<tr>
      <td>${esc(p.symbol)}</td><td class="${p.side === "BUY" ? "pos" : "neg"}">${p.side}</td><td>${p.volume}</td>
      <td class="mono">${fmt(p.symbol, p.price_open)}</td><td class="mono">${fmt(p.symbol, p.sl)}</td><td class="mono">${fmt(p.symbol, p.tp)}</td>
      <td class="mono ${p.profit >= 0 ? "pos" : "neg"}">${p.profit.toFixed(2)}</td>
      <td><button data-ticket="${p.ticket}">Tutup</button></td></tr>`).join("")
      : `<tr><td colspan="8" class="muted">Belum ada posisi</td></tr>`;
  } catch (e) { /* status koneksi sudah ditampilkan di header */ }
}
$("positions").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-ticket]"); if (!btn) return;
  if (!confirm(`Tutup posisi #${btn.dataset.ticket}?`)) return;
  btn.disabled = true;
  try { await api(`/api/positions/${btn.dataset.ticket}/close`, { method: "POST" }); }
  catch (err) { alert(`Gagal menutup posisi: ${err.message}`); }
  loadPositions(); loadOrderLog();
});

async function loadNews() {
  try {
    const n = await api("/api/news?hours=24");
    const pill = $("newsStatus");
    if (n.blocked_symbols.length) { pill.className = "pill warn"; pill.textContent = `WAIT & SEE: ${n.blocked_symbols.join(", ")}`; }
    else if (n.source === "disabled") { pill.className = "pill warn"; pill.textContent = "Filter berita nonaktif"; }
    else if (!n.source) { pill.className = "pill bad"; pill.textContent = "Kalender berita tidak tersedia"; }
    else { pill.className = "pill ok"; pill.textContent = `Berita: aman (${n.source})`; }
    $("news").innerHTML = n.events.length ? n.events.map((e) => `<li><span class="mono">${new Date(e.time).toLocaleString("id-ID", { weekday: "short", hour: "2-digit", minute: "2-digit" })}</span>
      <strong>${esc(e.currency)}</strong> ${esc(e.title)}</li>`).join("")
      : `<li class="muted">${n.source ? "Tidak ada berita high impact" : esc(n.error || "Tidak ada data")}</li>`;
  } catch (e) { /* abaikan */ }
}

async function loadOrderLog() {
  try {
    const rows = await api("/api/orders/log?limit=30");
    $("orderLog").innerHTML = rows.length ? rows.map((r) => {
      const t = new Date(r.logged_at).toLocaleTimeString("id-ID");
      const what = r.action === "close" ? `Tutup #${r.ticket}` : `${r.side || ""} ${r.symbol || ""} ${r.volume ?? ""}`;
      return `<li><span class="muted">${t}</span> ${esc(what)} <span class="${r.status === "rejected" ? "neg" : "pos"}">${esc(r.status)}</span>${r.reason ? `<div class="muted">${esc(r.reason)}</div>` : ""}</li>`;
    }).join("") : `<li class="muted">Belum ada order</li>`;
  } catch (e) { /* abaikan */ }
}

// ---------------------------------------------------------------- status & loops
async function loadHealth() {
  const conn = $("conn");
  try {
    const h = await api("/api/health");
    conn.className = "pill " + (h.connected ? "ok" : "bad");
    conn.textContent = h.connected ? (h.data_source === "mock" ? "Mode simulasi" : "MT5 terhubung") : "MT5 terputus";
    $("account").textContent = h.account ? `${h.account.server} · Balance ${h.account.balance.toFixed(2)} ${h.account.currency} · Equity ${h.account.equity.toFixed(2)}` : "";
  } catch (e) { conn.className = "pill bad"; conn.textContent = "Backend tidak merespons"; }
}

async function loadTicks() {
  try {
    const rows = await api("/api/ticks");
    rows.forEach((t) => (state.ticks[t.symbol] = t));
    const t = state.ticks[state.selected];
    if (t) $("symPrice").textContent = `Bid ${fmt(t.symbol, t.bid)} · Ask ${fmt(t.symbol, t.ask)} · Spread ${t.spread_points}`;
    renderPairs();
  } catch (e) { /* abaikan */ }
}

async function loadSummary() {
  try {
    const rows = await api("/api/analysis");
    rows.forEach((r) => (state.summary[r.symbol] = r));
    renderPairs();
  } catch (e) { /* abaikan */ }
}

function showError(e) { const m = $("execMsg"); m.className = "exec-msg neg"; m.textContent = e.message; }

document.querySelectorAll(".tf").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll(".tf").forEach((x) => x.classList.toggle("active", x === b));
  state.tf = b.dataset.tf; loadChart().catch(showError);
}));

setInterval(() => ($("clock").textContent = new Date().toLocaleTimeString("id-ID")), 1000);

async function boot() {
  initChart();
  state.config = await api("/api/config").catch(() => ({ symbols: Object.keys(DIGITS), default_lot: 0.01, max_lot: 1 }));
  state.symbols = state.config.symbols;
  $("lot").value = state.config.default_lot; $("lot").max = state.config.max_lot;
  const saved = localStorage.getItem("selectedSymbol");
  await Promise.all([loadHealth(), loadTicks()]);
  await selectSymbol(state.symbols.includes(saved) ? saved : state.symbols[0]);
  loadSummary(); loadPositions(); loadNews(); loadOrderLog();
  setInterval(loadTicks, 3000);
  setInterval(loadPositions, 5000);
  setInterval(loadHealth, 15000);
  setInterval(() => { loadAnalysis(); loadSummary(); }, 30000);
  setInterval(() => loadChart().catch(() => {}), 60000);
  setInterval(loadNews, 300000);
}
boot();
