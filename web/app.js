const els = {
  startBtn: document.querySelector("#startBtn"),
  stopBtn: document.querySelector("#stopBtn"),
  closeAllBtn: document.querySelector("#closeAllBtn"),
  resetBtn: document.querySelector("#resetBtn"),
  syncBtn: document.querySelector("#syncBtn"),
  scanBtn: document.querySelector("#scanBtn"),
  form: document.querySelector("#settingsForm"),
  runningText: document.querySelector("#runningText"),
  scanText: document.querySelector("#scanText"),
  sourceText: document.querySelector("#sourceText"),
  exchangeBadge: document.querySelector("#exchangeBadge"),
  equityText: document.querySelector("#equityText"),
  availableText: document.querySelector("#availableText"),
  tpCount: document.querySelector("#tpCount"),
  slCount: document.querySelector("#slCount"),
  pnlText: document.querySelector("#pnlText"),
  exposureText: document.querySelector("#exposureText"),
  exposureMeter: document.querySelector("#exposureMeter"),
  drawdownText: document.querySelector("#drawdownText"),
  drawdownMeter: document.querySelector("#drawdownMeter"),
  winRateText: document.querySelector("#winRateText"),
  profitFactorText: document.querySelector("#profitFactorText"),
  dailyPnlText: document.querySelector("#dailyPnlText"),
  scanDurationText: document.querySelector("#scanDurationText"),
  candidateRows: document.querySelector("#candidateRows"),
  cooldownRows: document.querySelector("#cooldownRows"),
  positionRows: document.querySelector("#positionRows"),
  historyRows: document.querySelector("#historyRows"),
  eventLog: document.querySelector("#eventLog"),
  momentumChart: document.querySelector("#momentumChart"),
  fundingChart: document.querySelector("#fundingChart"),
  riskChart: document.querySelector("#riskChart"),
  toast: document.querySelector("#toast"),
  chartTooltip: document.querySelector("#chartTooltip"),
};

let currentState = null;
let toastTimer = null;
const chartHitAreas = new WeakMap();

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "\"": "&quot;",
    "'": "&#039;",
  })[char]);
}

function showToast(message, type = "info") {
  if (!els.toast) return;
  clearTimeout(toastTimer);
  els.toast.textContent = message;
  els.toast.className = `toast show ${type}`;
  toastTimer = setTimeout(() => {
    els.toast.className = "toast";
  }, type === "error" ? 5200 : 3200);
}

function normalizeLiveTradingToggle() {
  const input = els.form?.elements?.live_trading_enabled;
  const label = input?.closest("label");
  if (!input || !label) return;
  label.classList.add("settings-check", "live-toggle");
  for (const node of [...label.childNodes]) {
    if (node.nodeType === Node.TEXT_NODE && node.textContent.trim()) {
      node.textContent = "";
    }
  }
  let text = label.querySelector("span");
  if (!text) {
    text = document.createElement("span");
    label.appendChild(text);
  }
  text.textContent = "启动实盘交易";
  label.title = "默认关闭。开启后会使用 BINANCE_LIVE_API_KEY 在币安实盘合约账户真实下单。";
}

function normalizeSettingsLabels() {
  normalizeLiveTradingToggle();
  const labelTextMap = {
    margin_per_trade_usdt: "基础开仓保证金 (U)",
    leverage: "最高杠杆上限",
    take_profit_roi_pct: "初始止盈 ROI (%)",
    stop_loss_roi_pct: "初始止损 ROI (%)",
    max_candidates_per_scan: "候选币数量",
    market_confirm_min_score: "市场确认分阈值",
    short_market_confirm_min_score: "做空确认分阈值",
    long_structure_min_score: "做多结构最低分",
    market_regime_min_score: "市场环境最低分",
  };
  for (const [name, textValue] of Object.entries(labelTextMap)) {
    const input = els.form?.elements?.[name];
    const label = input?.closest("label");
    if (!label) continue;
    for (const node of [...label.childNodes]) {
      if (node.nodeType === Node.TEXT_NODE && node.textContent.trim()) node.textContent = `${textValue} `;
    }
  }
  const toggles = {
    analyzer_realtime_before_open: "启用分析器二次确认",
    equity_take_profit_enabled: "启用总资产止盈全平",
    take_profit_cooldown_enabled: "启用止盈冷静期",
    market_regime_filter_enabled: "启用市场环境过滤",
    long_structure_confirmation_enabled: "启用做多结构确认",
  };
  for (const [name, textValue] of Object.entries(toggles)) {
    const input = els.form?.elements?.[name];
    const label = input?.closest("label");
    if (!label) continue;
    label.classList.add("settings-check");
    let text = label.querySelector("span");
    if (!text) {
      text = document.createElement("span");
      label.appendChild(text);
    }
    text.textContent = textValue;
  }
  const realtimeLabel = els.form?.elements?.analyzer_realtime_before_open?.closest("label");
  if (realtimeLabel) {
    realtimeLabel.title = "开启后，候选通过后下单前会再次实时跑分析器；关闭后，只使用候选阶段已经通过的分析器结果，开仓更快。";
  }
  const confirmationModeLabel = els.form?.elements?.confirmation_mode?.closest("label");
  if (confirmationModeLabel) {
    confirmationModeLabel.title = "市场确认分使用趋势、动量、量能、OI、资金费率、多空比和主动成交替代黑盒置信度；分析器置信度则切回原来的脚本确认。";
  }
  const marketConfirmLabel = els.form?.elements?.market_confirm_min_score?.closest("label");
  if (marketConfirmLabel) {
    marketConfirmLabel.title = "妖币强波动池做多的第三关阈值，满分10分。分数越高越保守，默认7.0。";
  }
  const shortMarketConfirmLabel = els.form?.elements?.short_market_confirm_min_score?.closest("label");
  if (shortMarketConfirmLabel) {
    shortMarketConfirmLabel.title = "妖币强波动池做空的第三关阈值。做空更看重结构破坏，建议比做多略高，例如7.5。";
  }
  const longStructureLabel = els.form?.elements?.long_structure_confirmation_enabled?.closest("label");
  if (longStructureLabel) {
    longStructureLabel.title = "开启后，做多候选还要通过上涨结构确认；只过滤明显跌破结构、放量滞涨、主动买入不足等坏结构，不惩罚妖币常见强势过热。";
  }
  const longStructureScoreLabel = els.form?.elements?.long_structure_min_score?.closest("label");
  if (longStructureScoreLabel) {
    longStructureScoreLabel.title = "做多结构确认满分按4分理解，默认2.5；分数越低代表上涨结构越可能已经被破坏。";
  }
  const marketRegimeLabel = els.form?.elements?.market_regime_filter_enabled?.closest("label");
  if (marketRegimeLabel) {
    marketRegimeLabel.title = "开启后，系统会结合BTC/ETH短线方向和全市场涨跌家数判断顺风/逆风；明显逆风时跳过对应方向开仓。";
  }
  const marketRegimeMinLabel = els.form?.elements?.market_regime_min_score?.closest("label");
  if (marketRegimeMinLabel) {
    marketRegimeMinLabel.title = "市场环境分低于该值时，认为当前方向逆风，系统不新增对应方向仓位。默认4.5。";
  }
  const equityTakeProfitLabel = els.form?.elements?.equity_take_profit_enabled?.closest("label");
  if (equityTakeProfitLabel) {
    equityTakeProfitLabel.title = "开启后，当合约总资产 - 昨日合约总账户余额 达到总资产止盈收益阈值时，系统停止新增开仓并提交全部平仓。";
  }
  const takeProfitCooldownLabel = els.form?.elements?.take_profit_cooldown_enabled?.closest("label");
  if (takeProfitCooldownLabel) {
    takeProfitCooldownLabel.title = "关闭后，止盈/盈利平仓不会进入冷静期；止损/亏损平仓仍然一定进入冷静期。";
  }
  const cooldownMinutesLabel = els.form?.elements?.stop_loss_cooldown_minutes?.closest("label");
  if (cooldownMinutesLabel) {
    cooldownMinutesLabel.title = "同币种平仓后的冷却时长，单位分钟；止损/亏损始终使用这个时间，止盈/盈利在启用止盈冷静期时使用这个时间。默认60分钟。";
  }
}

async function runAction(button, pendingText, successText, fn) {
  const originalText = button ? button.innerHTML : "";
  try {
    if (button) button.disabled = true;
    showToast(pendingText, "info");
    const result = await fn();
    if (result && result.ok === false) {
      showToast(result.error || "操作失败", "error");
      return result;
    }
    showToast(successText, "success");
    return result;
  } catch (error) {
    showToast(error.message || "操作失败", "error");
    throw error;
  } finally {
    if (button) {
      button.disabled = false;
      button.innerHTML = originalText;
    }
  }
}

function money(value) {
  return `${Number(value || 0).toFixed(2)} U`;
}

function pct(value) {
  return `${Number(value || 0).toFixed(2)}%`;
}

function signedPct(value) {
  const n = Number(value || 0);
  return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
}

function compactUsd(value) {
  const n = Number(value || 0);
  if (n >= 1_000_000_000) return `$${(n / 1_000_000_000).toFixed(2)}B`;
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `$${(n / 1_000).toFixed(1)}K`;
  return `$${n.toFixed(0)}`;
}

function ratio(value) {
  return Number(value || 0).toFixed(2);
}

function price(value) {
  const n = Number(value || 0);
  if (n >= 100) return n.toFixed(2);
  if (n >= 1) return n.toFixed(4);
  return n.toFixed(6);
}

function priceOrDash(value) {
  const n = Number(value || 0);
  return n > 0 ? `${price(n)} U` : "-";
}

function titleText(value) {
  return escapeHtml(value || "-").replace(/\n/g, "&#10;");
}

function sidePill(side) {
  const label = side === "LONG" ? "多" : "空";
  const cls = side === "LONG" ? "long" : "short";
  return `<span class="pill ${cls}">${label}</span>`;
}

function signedClass(value) {
  const n = Number(value || 0);
  if (n > 0) return "positive";
  if (n < 0) return "negative";
  return "";
}

function accountModeLabel(config = currentState?.config) {
  return config?.live_trading_enabled ? "实盘" : "模拟仓";
}

function clampPct(value) {
  return Math.max(0, Math.min(100, Number(value || 0)));
}

function formatDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

async function refresh() {
  try {
    currentState = await api("/api/state");
    normalizeSettingsLabels();
    render(currentState);
  } catch (error) {
    els.scanText.textContent = "连接服务失败";
    els.sourceText.textContent = error.message;
  }
}

function render(payload) {
  const { state, metrics, config } = payload;
  const modeLabel = accountModeLabel(config);
  if (els.syncBtn && !els.syncBtn.disabled) {
    els.syncBtn.innerHTML = `<span class="button-icon">↻</span>同步${modeLabel}`;
  }
  els.runningText.textContent = state.running ? "运行中" : "已停止";
  els.runningText.className = state.running ? "positive" : "danger";

  if (state.running && state.scan_in_progress) {
    els.scanText.textContent = "扫描中";
  } else if (state.running && state.next_scan_at) {
    const remain = Math.max(0, Math.round(state.next_scan_at - Date.now() / 1000));
    els.scanText.textContent = `倒计时 ${remain}s`;
  } else if (state.running) {
    els.scanText.textContent = state.last_scan_at ? "等待下轮" : "准备扫描";
  } else {
    els.scanText.textContent = state.last_scan_at ? "已停止" : "尚未扫描";
  }
  els.sourceText.textContent = `行情源：${state.market_source || "-"}`;
  if (state.exchange_status && state.exchange_status !== "unknown") {
    els.sourceText.textContent += ` / ${modeLabel}：${state.exchange_status}`;
  }
  const exchangeStatus = state.exchange_status || "unknown";
  els.exchangeBadge.textContent = exchangeStatus === "connected" ? `${modeLabel}已连接` : `${modeLabel}待同步`;
  els.exchangeBadge.className = `version ${exchangeStatus === "connected" ? "connected" : "offline"}`;

  els.equityText.textContent = money(metrics.equity_usdt);
  els.availableText.textContent = money(metrics.available_margin_usdt);
  els.tpCount.textContent = `${state.take_profit_count || 0} 次`;
  els.slCount.textContent = `${state.stop_loss_count || 0} 次`;
  els.pnlText.textContent = `${Number(metrics.total_pnl_usdt || 0) >= 0 ? "+" : ""}${money(metrics.total_pnl_usdt)}`;
  els.pnlText.className = signedClass(metrics.total_pnl_usdt);
  els.exposureText.textContent = pct(metrics.exposure_pct);
  els.exposureText.className = Number(metrics.exposure_pct || 0) > Number(config.max_exposure_pct || 65) * 0.8 ? "danger" : "positive";
  els.exposureMeter.style.width = `${clampPct(metrics.exposure_pct)}%`;
  els.drawdownText.textContent = pct(metrics.max_drawdown_pct);
  els.drawdownMeter.style.width = `${clampPct(metrics.max_drawdown_pct)}%`;
  els.winRateText.textContent = pct(metrics.win_rate_pct);
  els.profitFactorText.textContent = Number(metrics.profit_factor || 0).toFixed(2);
  els.dailyPnlText.textContent = `${Number(metrics.daily_realized_pnl_usdt || 0) >= 0 ? "+" : ""}${money(metrics.daily_realized_pnl_usdt)}`;
  els.dailyPnlText.className = signedClass(metrics.daily_realized_pnl_usdt);
  els.scanDurationText.textContent = `${Number(state.last_scan_duration_sec || 0).toFixed(2)} s`;

  for (const [key, value] of Object.entries(config)) {
    const input = els.form.elements[key];
    if (input && document.activeElement !== input) {
      if (input.type === "checkbox") {
        input.checked = Boolean(value);
      } else {
        input.value = Array.isArray(value) ? value.join(", ") : value;
      }
    }
  }

  renderCandidates(state.candidates || []);
  renderCooldowns(state.stop_loss_cooldowns || []);
  renderPositions(state.positions || []);
  renderHistory(state.trade_history || []);
  renderEvents(state.events || []);
  renderCharts(state.candidates || []);
}

function setupCanvas(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.max(1, Math.round(rect.width * dpr));
  canvas.height = Math.max(1, Math.round(rect.height * dpr));
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, width: rect.width, height: rect.height };
}

function clearChart(ctx, width, height) {
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "rgba(5, 17, 34, 0.14)";
  ctx.fillRect(0, 0, width, height);
}

function drawGrid(ctx, x, y, width, height) {
  ctx.strokeStyle = "rgba(120, 170, 230, 0.13)";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i += 1) {
    const yy = y + (height / 4) * i;
    ctx.beginPath();
    ctx.moveTo(x, yy);
    ctx.lineTo(x + width, yy);
    ctx.stroke();
  }
}

function drawLegend(ctx, items, x, y) {
  let offset = 0;
  ctx.font = "12px Microsoft YaHei, Arial";
  for (const item of items) {
    ctx.fillStyle = item.color;
    ctx.beginPath();
    ctx.roundRect(x + offset, y - 8, 18, 6, 3);
    ctx.fill();
    ctx.fillStyle = "#8ea5c8";
    ctx.fillText(item.label, x + offset + 25, y);
    offset += ctx.measureText(item.label).width + 58;
  }
}

function setChartHits(canvas, hits) {
  if (!canvas) return;
  chartHitAreas.set(canvas, hits);
}

function showChartTooltip(event, html) {
  if (!els.chartTooltip) return;
  els.chartTooltip.innerHTML = html;
  els.chartTooltip.classList.add("show");
  els.chartTooltip.setAttribute("aria-hidden", "false");
  const tooltipRect = els.chartTooltip.getBoundingClientRect();
  const gap = 14;
  let left = event.clientX + gap;
  let top = event.clientY + gap;
  if (left + tooltipRect.width > window.innerWidth - 10) {
    left = event.clientX - tooltipRect.width - gap;
  }
  if (top + tooltipRect.height > window.innerHeight - 10) {
    top = event.clientY - tooltipRect.height - gap;
  }
  els.chartTooltip.style.left = `${Math.max(10, left)}px`;
  els.chartTooltip.style.top = `${Math.max(10, top)}px`;
}

function hideChartTooltip() {
  if (!els.chartTooltip) return;
  els.chartTooltip.classList.remove("show");
  els.chartTooltip.setAttribute("aria-hidden", "true");
}

function bindChartTooltip(canvas) {
  if (!canvas) return;
  canvas.addEventListener("mousemove", (event) => {
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    const hits = chartHitAreas.get(canvas) || [];
    const hit = hits.find((item) => x >= item.x && x <= item.x + item.w && y >= item.y && y <= item.y + item.h);
    if (hit) {
      canvas.style.cursor = "crosshair";
      showChartTooltip(event, hit.html);
    } else {
      canvas.style.cursor = "";
      hideChartTooltip();
    }
  });
  canvas.addEventListener("mouseleave", () => {
    canvas.style.cursor = "";
    hideChartTooltip();
  });
}

function tooltipRows(title, rows) {
  const body = rows
    .map(([label, value, cls = ""]) => `<div><span>${escapeHtml(label)}</span><strong class="${cls}">${escapeHtml(value)}</strong></div>`)
    .join("");
  return `<b>${escapeHtml(title)}</b>${body}`;
}

function chartRows(rows) {
  return rows.slice(0, 8).map((row) => ({
    symbol: String(row.symbol || "").replace("USDT", ""),
    fullSymbol: row.symbol || "",
    side: row.side || "",
    score: Number(row.score || 0),
    confidence: Number(row.analyzer_confidence_pct || 0),
    price: Number(row.change_24h_pct || 0),
    price4h: Number(row.change_4h_pct || 0),
    price1h: Number(row.change_1h_pct || 0),
    oi: Number(row.open_interest_24h_pct || 0),
    oiValue: Number(row.open_interest_usdt || 0),
    oi4h: Number(row.open_interest_4h_pct || 0),
    oi1h: Number(row.open_interest_1h_pct || 0),
    volumeRatio: Number(row.volume_4h_ratio || row.volume_ratio || 0),
    funding: Number(row.funding_rate_pct || 0),
    ls: Number(row.long_short_ratio || 0),
    taker: Number(row.taker_long_short_ratio || 0),
    oiVolume: Number(row.oi_volume_ratio || 0),
    risk: row.risk_level || "正常",
  }));
}

function scaleValue(value, maxAbs, height) {
  if (!maxAbs) return height / 2;
  return height / 2 - (value / maxAbs) * (height * 0.42);
}

function drawMomentumChart(rows) {
  if (!els.momentumChart) return;
  const data = chartRows(rows);
  const { ctx, width, height } = setupCanvas(els.momentumChart);
  clearChart(ctx, width, height);
  setChartHits(els.momentumChart, []);
  const pad = { left: 38, right: 18, top: 16, bottom: 28 };
  const chartW = width - pad.left - pad.right;
  const chartH = height - pad.top - pad.bottom;
  drawGrid(ctx, pad.left, pad.top, chartW, chartH);
  drawLegend(ctx, [{ label: "24h涨跌", color: "#31e981" }, { label: "OI 24h", color: "#3aa8ff" }], pad.left, 12);
  if (!data.length) return;
  const maxAbs = Math.max(8, ...data.flatMap((d) => [Math.abs(d.price), Math.abs(d.oi)]));
  const step = chartW / data.length;
  const zeroY = pad.top + chartH / 2;
  const hits = [];
  ctx.strokeStyle = "rgba(180, 205, 240, 0.24)";
  ctx.beginPath();
  ctx.moveTo(pad.left, zeroY);
  ctx.lineTo(pad.left + chartW, zeroY);
  ctx.stroke();
  ctx.fillStyle = "#6f86aa";
  ctx.font = "10px Microsoft YaHei, Arial";
  ctx.textAlign = "left";
  ctx.fillText("0", 8, zeroY + 3);
  ctx.fillText(`+/-${maxAbs.toFixed(1)}%`, 8, pad.top + 4);

  data.forEach((d, i) => {
    const cx = pad.left + step * i + step / 2;
    const barW = Math.max(8, step * 0.26);
    const y = pad.top + scaleValue(d.price, maxAbs, chartH);
    const top = Math.min(y, zeroY);
    const barH = Math.max(2, Math.abs(zeroY - y));
    const grad = ctx.createLinearGradient(0, top, 0, top + barH);
    grad.addColorStop(0, d.price >= 0 ? "rgba(49, 233, 129, 0.92)" : "rgba(255, 82, 104, 0.92)");
    grad.addColorStop(1, d.price >= 0 ? "rgba(49, 233, 129, 0.12)" : "rgba(255, 82, 104, 0.12)");
    ctx.fillStyle = grad;
    ctx.beginPath();
    ctx.roundRect(cx - barW / 2, top, barW, barH, 4);
    ctx.fill();
    ctx.fillStyle = "#8ea5c8";
    ctx.font = "11px Microsoft YaHei, Arial";
    ctx.textAlign = "center";
    ctx.fillText(d.symbol.slice(0, 6), cx, height - 8);
    hits.push({
      x: Math.max(0, cx - step / 2),
      y: pad.top,
      w: step,
      h: chartH,
      html: tooltipRows(d.fullSymbol || d.symbol, [
        ["方向/总分", `${d.side || "-"} / ${d.score.toFixed(1)}`],
        ["分析置信度", `${d.confidence.toFixed(1)}%`, d.confidence > 90 ? "positive" : "negative"],
        ["24h涨跌", signedPct(d.price), signedClass(d.price)],
        ["4h / 1h", `${signedPct(d.price4h)} / ${signedPct(d.price1h)}`],
        ["OI规模", compactUsd(d.oiValue)],
        ["OI 24h", signedPct(d.oi), signedClass(d.oi)],
        ["OI 4h / 1h", `${signedPct(d.oi4h)} / ${signedPct(d.oi1h)}`],
        ["4h量能", `${d.volumeRatio.toFixed(1)}x`],
      ]),
    });
  });

  ctx.strokeStyle = "#3aa8ff";
  ctx.lineWidth = 2;
  ctx.beginPath();
  data.forEach((d, i) => {
    const cx = pad.left + step * i + step / 2;
    const y = pad.top + scaleValue(d.oi, maxAbs, chartH);
    if (i === 0) ctx.moveTo(cx, y);
    else ctx.lineTo(cx, y);
  });
  ctx.stroke();
  data.forEach((d, i) => {
    const cx = pad.left + step * i + step / 2;
    const y = pad.top + scaleValue(d.oi, maxAbs, chartH);
    ctx.fillStyle = "#3aa8ff";
    ctx.beginPath();
    ctx.arc(cx, y, 3, 0, Math.PI * 2);
    ctx.fill();
  });
  setChartHits(els.momentumChart, hits);
}

function drawFundingChart(rows) {
  if (!els.fundingChart) return;
  const data = chartRows(rows).slice(0, 6);
  const { ctx, width, height } = setupCanvas(els.fundingChart);
  clearChart(ctx, width, height);
  setChartHits(els.fundingChart, []);
  const pad = { left: 38, right: 18, top: 18, bottom: 28 };
  const chartW = width - pad.left - pad.right;
  const chartH = height - pad.top - pad.bottom;
  drawGrid(ctx, pad.left, pad.top, chartW, chartH);
  drawLegend(ctx, [{ label: "资金费率", color: "#ffc64b" }, { label: "多空比", color: "#54e7ff" }], pad.left, 12);
  if (!data.length) return;
  const maxFunding = Math.max(0.02, ...data.map((d) => Math.abs(d.funding)));
  const step = chartW / data.length;
  const hits = [];
  const zeroY = pad.top + chartH / 2;
  ctx.strokeStyle = "rgba(180, 205, 240, 0.24)";
  ctx.beginPath();
  ctx.moveTo(pad.left, zeroY);
  ctx.lineTo(pad.left + chartW, zeroY);
  ctx.stroke();
  ctx.fillStyle = "#6f86aa";
  ctx.font = "10px Microsoft YaHei, Arial";
  ctx.textAlign = "left";
  ctx.fillText("FR 0", 5, zeroY + 3);
  data.forEach((d, i) => {
    const cx = pad.left + step * i + step / 2;
    const barH = Math.min(chartH * 0.86, Math.abs(d.funding) / maxFunding * chartH * 0.72);
    ctx.fillStyle = d.funding >= 0 ? "rgba(49, 233, 129, 0.78)" : "rgba(255, 82, 104, 0.78)";
    ctx.beginPath();
    ctx.roundRect(cx - 8, d.funding >= 0 ? zeroY - barH : zeroY, 16, barH, 5);
    ctx.fill();
    const ratioY = pad.top + chartH - Math.min(chartH, d.ls / 3 * chartH);
    ctx.fillStyle = "#54e7ff";
    ctx.beginPath();
    ctx.arc(cx, ratioY, 4, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#8ea5c8";
    ctx.font = "11px Microsoft YaHei, Arial";
    ctx.textAlign = "center";
    ctx.fillText(d.symbol.slice(0, 5), cx, height - 8);
    hits.push({
      x: Math.max(0, cx - step / 2),
      y: pad.top,
      w: step,
      h: chartH,
      html: tooltipRows(d.fullSymbol || d.symbol, [
        ["方向/风险", `${d.side || "-"} / ${d.risk}`],
        ["资金费率 8h", signedPct(d.funding), signedClass(d.funding)],
        ["账户多空比", ratio(d.ls)],
        ["主动买卖比", ratio(d.taker)],
        ["OI/成交额", ratio(d.oiVolume)],
        ["24h涨跌", signedPct(d.price), signedClass(d.price)],
        ["OI 24h", signedPct(d.oi), signedClass(d.oi)],
      ]),
    });
  });
  setChartHits(els.fundingChart, hits);
}

function drawRiskChart(rows) {
  if (!els.riskChart) return;
  const data = chartRows(rows);
  const { ctx, width, height } = setupCanvas(els.riskChart);
  clearChart(ctx, width, height);
  setChartHits(els.riskChart, []);
  const counts = { 正常: 0, 注意: 0, 危险: 0 };
  data.forEach((row) => {
    counts[row.risk] = (counts[row.risk] || 0) + 1;
  });
  const total = Math.max(1, Object.values(counts).reduce((sum, n) => sum + n, 0));
  const colors = { 正常: "#31e981", 注意: "#ffc64b", 危险: "#ff5268" };
  const cx = width * 0.42;
  const cy = height * 0.5;
  const radius = Math.min(width, height) * 0.31;
  let start = -Math.PI / 2;
  const hits = [];
  for (const key of ["正常", "注意", "危险"]) {
    const angle = (counts[key] / total) * Math.PI * 2;
    ctx.strokeStyle = colors[key];
    ctx.lineWidth = 18;
    ctx.beginPath();
    ctx.arc(cx, cy, radius, start, start + angle);
    ctx.stroke();
    hits.push({
      x: cx - radius - 16,
      y: cy - radius - 16,
      w: radius * 2 + 32,
      h: radius * 2 + 32,
      html: tooltipRows("风险分布", [
        ["正常", `${counts["正常"]} 个`],
        ["注意", `${counts["注意"]} 个`],
        ["危险", `${counts["危险"]} 个`],
        ["来源", "当前候选表前 8"],
      ]),
    });
    start += angle;
  }
  ctx.fillStyle = "#f1f7ff";
  ctx.font = "700 24px Microsoft YaHei, Arial";
  ctx.textAlign = "center";
  ctx.fillText(`${total}`, cx, cy + 8);
  ctx.font = "12px Microsoft YaHei, Arial";
  ctx.fillStyle = "#8ea5c8";
  ctx.fillText("候选", cx, cy + 28);
  let y = 68;
  ctx.textAlign = "left";
  for (const key of ["正常", "注意", "危险"]) {
    ctx.fillStyle = colors[key];
    ctx.beginPath();
    ctx.roundRect(width * 0.68, y - 10, 18, 7, 3);
    ctx.fill();
    ctx.fillStyle = "#b8c9e7";
    ctx.font = "13px Microsoft YaHei, Arial";
    ctx.fillText(`${key} ${counts[key]}`, width * 0.68 + 26, y);
    hits.push({
      x: width * 0.68 - 8,
      y: y - 20,
      w: width * 0.3,
      h: 28,
      html: tooltipRows(`${key}风险`, [
        ["数量", `${counts[key]} 个`],
        ["占比", `${((counts[key] / total) * 100).toFixed(1)}%`],
        ["判断", key === "危险" ? "资金费率/OI/量能过热" : key === "注意" ? "信号较强但需观察" : "资金面相对稳定"],
      ]),
    });
    y += 28;
  }
  setChartHits(els.riskChart, hits);
}

function renderCharts(rows) {
  drawMomentumChart(rows);
  drawFundingChart(rows);
  drawRiskChart(rows);
}

function candidateNextAction(row, confidenceThreshold) {
  const reject = String(row.reject_reason || "");
  const summary = String(row.decision_summary || "");
  const decisionText = `${reject} ${summary}`;
  const confidence = Number(row.analyzer_confidence_pct || 0);
  const analyzerDirection = row.analyzer_direction || "";

  if (summary.includes("已提交") || reject.includes("已提交")) return "已提交";
  if (analyzerDirection === "市场确认" && !reject) return "风控下单";
  if (reject.includes("等待分析器")) return "等待分析器";
  if (reject.includes("已有持仓")) return "已有持仓";
  if (reject.includes("禁止开仓")) return "禁开跳过";
  if (reject.includes("冷静期")) return "冷静期";
  if (decisionText.includes("今日") || decisionText.includes("敞口") || decisionText.includes("持仓上限")) return "风控拦截";
  if (decisionText.includes("结构破坏")) return "结构过滤";
  if (reject.includes("市场确认分")) return "市场确认不足";
  if (reject.includes("方向") || reject.includes("观望") || reject.includes("置信度") || reject.includes("分析器")) return "分析器拒绝";
  if (!reject && analyzerDirection && confidence > confidenceThreshold) return "风控下单";
  if (reject === "待开仓" && analyzerDirection && confidence > confidenceThreshold) return "风控下单";
  if (!analyzerDirection || confidence <= 0) return "等待分析器";
  return "继续观察";
}

function renderCooldowns(rows) {
  if (!els.cooldownRows) return;
  if (!rows.length) {
    els.cooldownRows.innerHTML = `<tr><td colspan="7" class="empty">暂无平仓冷静期</td></tr>`;
    return;
  }
  els.cooldownRows.innerHTML = rows
    .map((row) => {
      const symbol = escapeHtml(row.symbol || "-");
      const reason = row.close_reason || "最近平仓";
      return `
        <tr>
          <td><strong>${symbol}</strong></td>
          <td><span class="score-badge">${Number(row.remaining_minutes || 0)} 分钟</span></td>
          <td>${formatDate(row.closed_at)}</td>
          <td class="${signedClass(row.pnl_usdt)}">${money(row.pnl_usdt)}</td>
          <td class="${signedClass(row.roi_pct)}">${pct(row.roi_pct)}</td>
          <td><span class="decision-chain" title="${titleText(reason)}">${escapeHtml(reason)}</span></td>
          <td><button class="mini-action" type="button" data-clear-cooldown="${symbol}">解除</button></td>
        </tr>
      `;
    })
    .join("");
}

function renderCandidates(rows) {
  if (!els.candidateRows) return;
  if (!rows.length) {
    els.candidateRows.innerHTML = `<tr><td colspan="25" class="empty">候选表空</td></tr>`;
    return;
  }
  els.candidateRows.innerHTML = rows
    .map((row) => {
      const reject = row.reject_reason || "待开仓";
      const decisionSteps = Array.isArray(row.decision_steps) ? row.decision_steps : [];
      const decisionTitle = decisionSteps.length ? decisionSteps.join("\n") : row.decision_summary || reject;
      const decisionText = row.decision_summary || reject;
      const riskCls = row.risk_level === "危险" ? "risk-high" : row.risk_level === "注意" ? "risk-watch" : "risk-ok";
      const modeText = "强波动";
      const confidenceThreshold = Number(currentState?.config?.analyzer_min_confidence_pct || 90);
      const isMarketConfirm = row.analyzer_direction === "市场确认";
      const confirmValue = Number(row.analyzer_confidence_pct || 0);
      const confirmThreshold = row.side === "SHORT"
        ? Number(currentState?.config?.short_market_confirm_min_score || 7.5)
        : Number(currentState?.config?.market_confirm_min_score || 7);
      const confirmPassed = isMarketConfirm ? confirmValue >= confirmThreshold * 10 : confirmValue > confidenceThreshold;
      const confirmText = isMarketConfirm ? `${(confirmValue / 10).toFixed(1)}/10` : `${confirmValue.toFixed(1)}%`;
      const regimeScore = Number(row.market_regime_score || 0);
      const regimeText = row.market_regime_label ? `${row.market_regime_label} ${regimeScore.toFixed(1)}` : "-";
      const nextAction = candidateNextAction(row, confidenceThreshold);
      return `
        <tr>
          <td>${row.symbol}</td>
          <td><span class="score-badge">${modeText}</span></td>
          <td><span class="score-badge">${Number(row.score || 0).toFixed(1)}</span></td>
          <td>${sidePill(row.side)}</td>
          <td>${row.analyzer_direction || "-"}</td>
          <td class="${confirmPassed ? "positive" : "danger"}">${confirmText}</td>
          <td title="${titleText(row.market_regime_reason || "-")}"><span class="score-badge">${escapeHtml(regimeText)}</span></td>
          <td><span class="next-action" title="${titleText(decisionTitle)}">${escapeHtml(nextAction)}</span></td>
          <td>${reject}</td>
          <td><span class="decision-chain" title="${titleText(decisionTitle)}">${escapeHtml(decisionText)}</span></td>
          <td>${price(row.price)} U</td>
          <td class="${signedClass(row.change_24h_pct)}">${signedPct(row.change_24h_pct)}</td>
          <td class="${signedClass(row.change_4h_pct)}">${signedPct(row.change_4h_pct)}</td>
          <td class="${signedClass(row.change_1h_pct)}">${signedPct(row.change_1h_pct)}</td>
          <td>${compactUsd(row.open_interest_usdt)}</td>
          <td class="${signedClass(row.open_interest_24h_pct)}">${signedPct(row.open_interest_24h_pct)}</td>
          <td class="${signedClass(row.open_interest_4h_pct)}">${signedPct(row.open_interest_4h_pct)}</td>
          <td class="${signedClass(row.open_interest_1h_pct)}">${signedPct(row.open_interest_1h_pct)}</td>
          <td>${Number(row.volume_4h_ratio || row.volume_ratio || 0).toFixed(1)}x</td>
          <td class="${signedClass(row.funding_rate_pct)}">${signedPct(row.funding_rate_pct)}</td>
          <td>${Number(row.long_short_ratio || 0).toFixed(2)}</td>
          <td>${Number(row.taker_long_short_ratio || 0).toFixed(2)}</td>
          <td>${Number(row.oi_volume_ratio || 0).toFixed(1)}</td>
          <td><span class="risk-chip ${riskCls}">${row.risk_level || "正常"}</span></td>
          <td class="priority">${row.priority}</td>
        </tr>
      `;
    })
    .join("");
}

function renderHistory(rows) {
  if (!els.historyRows) return;
  if (!rows.length) {
    els.historyRows.innerHTML = `<tr><td colspan="10" class="empty">暂无历史</td></tr>`;
    return;
  }
  els.historyRows.innerHTML = rows
    .slice(0, 60)
    .map((row) => {
      const strategyParts = [];
      if (Number(row.strategy_score || 0) > 0) strategyParts.push(`分 ${Number(row.strategy_score || 0).toFixed(1)}`);
      if (row.analyzer_direction) strategyParts.push(`${row.analyzer_direction} ${Number(row.analyzer_confidence_pct || 0).toFixed(1)}%`);
      if (row.decision_summary) strategyParts.push(row.decision_summary);
      const strategyText = strategyParts.join(" / ") || row.trigger_reason || "-";
      const strategyTitle = Array.isArray(row.decision_steps) && row.decision_steps.length ? row.decision_steps.join("\n") : strategyText;
      return `
        <tr>
          <td>${row.symbol}</td>
          <td>${sidePill(row.side)}</td>
          <td>${formatDate(row.opened_at)}</td>
          <td>${formatDate(row.closed_at)}</td>
          <td>${priceOrDash(row.entry_price)}</td>
          <td>${priceOrDash(row.exit_price)}</td>
          <td>${row.close_reason}</td>
          <td><span class="decision-chain" title="${titleText(strategyTitle)}">${escapeHtml(strategyText)}</span></td>
          <td class="${signedClass(row.pnl_usdt)}">${money(row.pnl_usdt)}</td>
          <td class="${signedClass(row.roi_pct)}">${pct(row.roi_pct)}</td>
        </tr>
      `;
    })
    .join("");
}

function renderPositions(rows) {
  if (!els.positionRows) return;
  if (!rows.length) {
    els.positionRows.innerHTML = `<tr><td colspan="9" class="empty">暂无持仓</td></tr>`;
    return;
  }
  els.positionRows.innerHTML = rows
    .map((row) => {
      return `
        <tr>
          <td>${row.symbol}</td>
          <td>${row.trigger_reason}</td>
          <td>${sidePill(row.side)}</td>
          <td>${formatDate(row.opened_at)}</td>
          <td><strong>${price(row.last_price)} U</strong></td>
          <td>${Number(row.quantity || 0).toFixed(4)}</td>
          <td>${money(row.margin_usdt)}</td>
          <td class="${signedClass(row.unrealized_pnl_usdt)}">${money(row.unrealized_pnl_usdt)}</td>
          <td class="${signedClass(row.roi_pct)}">${pct(row.roi_pct)}</td>
        </tr>
      `;
    })
    .join("");
}

function renderEvents(events) {
  if (!events.length) {
    els.eventLog.innerHTML = `<div class="event"><span>暂无日志</span><time>-</time></div>`;
    return;
  }
  els.eventLog.innerHTML = events
    .slice(0, 14)
    .map((event) => {
      return `<div class="event"><span>${event.message}</span><time>${formatDate(event.time)}</time></div>`;
    })
    .join("");
}

els.startBtn.addEventListener("click", async () => {
  await runAction(els.startBtn, "正在启动机器人...", "机器人已启动", async () => {
    currentState = await api("/api/start", { method: "POST" });
    render(currentState);
    return currentState;
  });
});

els.stopBtn.addEventListener("click", async () => {
  await runAction(els.stopBtn, "正在停止机器人...", "机器人已停止", async () => {
    currentState = await api("/api/stop", { method: "POST" });
    render(currentState);
    return currentState;
  });
});

els.closeAllBtn.addEventListener("click", async () => {
  await runAction(els.closeAllBtn, "正在提交全部平仓...", "全部平仓请求已提交", async () => {
    currentState = await api("/api/close-all", { method: "POST" });
    render(currentState);
    return currentState;
  });
});

els.resetBtn.addEventListener("click", async () => {
  const ok = window.confirm("确认清空所有历史记录并重新开始测试？请先确保 Binance Testnet 已经没有持仓。");
  if (!ok) return;
  await runAction(els.resetBtn, "正在重置测试数据...", "测试数据已清空", async () => {
    const payload = await api("/api/reset-test", { method: "POST" });
    currentState = payload;
    render(currentState);
    if (payload.ok === false) {
      els.scanText.textContent = "重置失败";
      els.sourceText.textContent = payload.error || "请先确认模拟仓没有持仓";
    }
    return payload;
  });
});

els.syncBtn.addEventListener("click", async () => {
  const modeLabel = accountModeLabel();
  await runAction(els.syncBtn, `正在同步${modeLabel}...`, `${modeLabel}同步完成`, async () => {
    currentState = await api("/api/sync-exchange", { method: "POST" });
    render(currentState);
    return currentState;
  });
});

els.scanBtn.addEventListener("click", async () => {
  await runAction(els.scanBtn, "正在刷新候选...", "刷新任务已提交", async () => {
    const result = await api("/api/scan", { method: "POST" });
    setTimeout(refresh, 400);
    return result;
  });
});

els.cooldownRows?.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-clear-cooldown]");
  if (!button) return;
  const symbol = button.dataset.clearCooldown || "";
  const ok = window.confirm(`确认解除 ${symbol} 本次平仓冷静期？如果后续再次平仓，冷静期会重新生效。`);
  if (!ok) return;
  await runAction(button, `正在解除 ${symbol} 冷静期...`, `${symbol} 冷静期已解除`, async () => {
    currentState = await api("/api/clear-cooldown", {
      method: "POST",
      body: JSON.stringify({ symbol }),
    });
    render(currentState);
    return currentState;
  });
});

els.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  await runAction(els.form.querySelector("button[type='submit']"), "正在保存参数...", "参数已保存", async () => {
    const data = Object.fromEntries(new FormData(els.form).entries());
    const previousLiveTradingEnabled = Boolean(currentState?.config?.live_trading_enabled);
    for (const input of els.form.querySelectorAll("input[type='checkbox']")) {
      data[input.name] = input.checked ? "true" : "false";
    }
    if (data.live_trading_enabled === "true" && !previousLiveTradingEnabled) {
      const ok = window.confirm("确认启动实盘交易？开启后机器人会使用 BINANCE_LIVE_API_KEY 在币安实盘合约账户真实下单。");
      if (!ok) return currentState;
    }
    currentState = await api("/api/settings", {
      method: "POST",
      body: JSON.stringify(data),
    });
    if (Boolean(currentState?.config?.live_trading_enabled) !== previousLiveTradingEnabled) {
      try {
        currentState = await api("/api/sync-exchange", { method: "POST" });
      } catch (error) {
        showToast(`${accountModeLabel(currentState?.config)}账户同步失败：${error.message}`, "error");
      }
    }
    normalizeSettingsLabels();
    render(currentState);
    return currentState;
  });
});

bindChartTooltip(els.momentumChart);
bindChartTooltip(els.fundingChart);
bindChartTooltip(els.riskChart);

refresh();
setInterval(refresh, 2000);
