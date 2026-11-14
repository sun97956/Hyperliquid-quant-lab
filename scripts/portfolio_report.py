"""Rich single-file HTML report for portfolio/weight-based strategies.

The report is self-contained: inline CSS, inline JavaScript, embedded JSON,
canvas charts, and no external CDN.
"""
import json
import os

import pandas as pd

ASSETS = ["BTC", "ETH", "SOL", "BNB", "HYPE"]
ASSET_COLORS = ["#f59e0b", "#38bdf8", "#a78bfa", "#22c55e", "#ef4444", "#eab308", "#14b8a6", "#f472b6", "#94a3b8"]
CASH_COLOR = "#64748b"
CURVE_COLORS = ["#22c55e", "#f59e0b", "#38bdf8", "#a78bfa", "#ef4444"]


def _date_string(value):
    return str(value)[:10]


def _clean_number(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def _normalized(series):
    base = float(series.iloc[0])
    return [None if pd.isna(v) else round(float(v) / base * 100, 6) for v in series]


def _values(series):
    return [None if pd.isna(v) else round(float(v), 6) for v in series]


def _with_cash(weights):
    out = {key: float(value) for key, value in weights.items() if key != "Cash"}
    out["Cash"] = round(1.0 - sum(out.values()), 6)
    return out


def _trades_records(trades, assets):
    if trades is None or len(trades) == 0:
        return []
    records = []
    for _, row in trades.iterrows():
        target_sum = 0.0
        record = {
            "decision_date": _date_string(row["decision_date"]),
            "exec_date": _date_string(row["exec_date"]),
            "action": str(row["action"]),
            "turnover": round(float(row["turnover"]), 6),
            "fee": round(float(row["fee"]), 6),
        }
        for symbol in assets:
            value = round(float(row[f"target_{symbol}"]), 6)
            record[f"target_{symbol}"] = value
            target_sum += value
        cash = row.get("target_CASH")
        record["target_CASH"] = round(float(cash) if cash is not None else 1.0 - target_sum, 6)
        records.append(record)
    return records


def _weights_history(trades, assets):
    if trades is None or len(trades) == 0:
        return []
    history = []
    ordered = trades.sort_values("exec_date")
    for _, row in ordered.iterrows():
        target_sum = 0.0
        record = {"date": _date_string(row["exec_date"])}
        for symbol in assets:
            value = round(float(row[f"target_{symbol}"]), 6)
            record[symbol] = value
            target_sum += value
        cash = row.get("target_CASH")
        record["Cash"] = round(float(cash) if cash is not None else 1.0 - target_sum, 6)
        history.append(record)
    return history


def build_portfolio_payload(
    title,
    meta,
    strategy_label,
    results,
    strategy_curve,
    benchmarks,
    exposure,
    trades,
    first_weights,
    last_weights,
    rows_per_symbol,
    config_items,
    method_note,
    data_start,
    data_end,
    warmup_bars,
    assets=None,
    config=None,
):
    if assets is None:
        if trades is not None and len(trades) > 0:
            assets = [
                column[len("target_"):]
                for column in trades.columns
                if column.startswith("target_") and column != "target_CASH"
            ]
        elif first_weights:
            assets = [key for key in first_weights if key != "Cash"]
    asset_list = list(assets or ASSETS)
    asset_colors = [ASSET_COLORS[i % len(ASSET_COLORS)] for i in range(len(asset_list))]
    clean_results = {}
    for name, metrics in results.items():
        clean_results[name] = {key: _clean_number(value) for key, value in metrics.items()}

    curves = [
        {
            "label": strategy_label,
            "color": CURVE_COLORS[0],
            "values": _normalized(strategy_curve),
        }
    ]
    for index, (name, curve) in enumerate(benchmarks.items()):
        curves.append({
            "label": name,
            "color": CURVE_COLORS[(index + 1) % len(CURVE_COLORS)],
            "values": _normalized(curve),
        })

    drawdown = (strategy_curve / strategy_curve.cummax() - 1) * 100
    exposure_values = [None if pd.isna(v) else round(float(v) * 100, 6) for v in exposure]
    cash_values = [None if v is None else round(100.0 - v, 6) for v in exposure_values]
    dates = [int(t.timestamp() * 1000) for t in strategy_curve.index]

    payload = {
        "generated_at": pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M UTC"),
        "title": title,
        "meta": meta,
        "strategy_label": strategy_label,
        "strategy_metrics": clean_results[strategy_label],
        "results": clean_results,
        "curves": curves,
        "dates": dates,
        "drawdown": _values(drawdown),
        "exposure": exposure_values,
        "cash": cash_values,
        "assets": asset_list,
        "asset_colors": asset_colors,
        "cash_color": CASH_COLOR,
        "weights_history": _weights_history(trades, asset_list),
        "trades": _trades_records(trades, asset_list),
        "first_weights": _with_cash(first_weights),
        "last_weights": _with_cash(last_weights),
        "rows_per_symbol": rows_per_symbol,
        "data": {
            "start": data_start,
            "end": data_end,
            "warmup_bars": int(warmup_bars),
        },
        "config_items": config_items,
        "method_note": method_note,
        "config": config or {},
    }
    return payload


def render_portfolio_html(payload):
    data_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    data_json = data_json.replace("<", "\\u003c").replace(">", "\\u003e")
    return HTML_TEMPLATE.replace("__PAYLOAD__", data_json)


def write_portfolio_report(path, **kwargs):
    payload = build_portfolio_payload(**kwargs)
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(render_portfolio_html(payload))
    return payload


HTML_TEMPLATE = r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Portfolio Backtest</title>
<style>
:root{
  --bg:#0b1220; --bg2:#111a2e; --card:#151f35; --card2:#1a2540;
  --line:#243252; --text:#e8eefc; --muted:#8b9bb8; --accent:#3b82f6;
  --good:#22c55e; --bad:#ef4444; --warn:#f59e0b; --test:#a78bfa;
  --radius:14px; --shadow:0 8px 28px rgba(0,0,0,.35);
  font-family:"Segoe UI",system-ui,-apple-system,sans-serif;
}
*{box-sizing:border-box}
body{margin:0;background:radial-gradient(1200px 600px at 10% -10%,#1a2a4a 0%,var(--bg) 55%);color:var(--text);min-height:100vh}
.wrap{max-width:1180px;margin:0 auto;padding:24px 18px 48px}
.header{display:flex;flex-wrap:wrap;gap:14px;justify-content:space-between;align-items:flex-end;margin-bottom:18px}
.header h1{margin:0;font-size:1.45rem;letter-spacing:.02em}
.badge{display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border-radius:999px;background:var(--card2);border:1px solid var(--line);color:var(--muted);font-size:.78rem}
.meta{color:var(--muted);font-size:.85rem;line-height:1.6}
.kpi-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:14px}
.kpi{background:linear-gradient(180deg,var(--card),var(--bg2));border:1px solid var(--line);border-radius:var(--radius);padding:12px 14px;box-shadow:var(--shadow)}
.kpi .label{color:var(--muted);font-size:.72rem;text-transform:uppercase;letter-spacing:.06em}
.kpi .value{font-size:1.35rem;font-weight:700;margin-top:6px}
.kpi .sub{color:var(--muted);font-size:.76rem;margin-top:4px}
.grid{display:grid;gap:14px}
.grid-2{grid-template-columns:1.15fr .85fr}
@media(max-width:900px){.kpi-grid{grid-template-columns:repeat(2,1fr)}.grid-2{grid-template-columns:1fr}}
.card{background:linear-gradient(180deg,var(--card),var(--bg2));border:1px solid var(--line);border-radius:var(--radius);padding:16px 18px;box-shadow:var(--shadow)}
.card h2{margin:0 0 12px;font-size:1rem;font-weight:600;color:#c9d7f2}
canvas{width:100%;height:280px;background:#0a1020;border-radius:12px;border:1px solid var(--line)}
.legend{display:flex;flex-wrap:wrap;gap:12px;margin-top:8px;color:var(--muted);font-size:.78rem}
.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:4px}
.table-tools{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:8px}
.table-tools input{background:var(--bg);border:1px solid var(--line);color:var(--text);border-radius:8px;padding:7px 10px;min-width:240px}
.table-wrap{overflow:auto;max-height:380px;border:1px solid var(--line);border-radius:12px}
table{width:100%;border-collapse:collapse;font-size:.8rem}
th,td{padding:7px 8px;border-bottom:1px solid rgba(36,50,82,.7);text-align:right;white-space:nowrap}
th{position:sticky;top:0;background:#132038;color:var(--muted);font-weight:600;text-align:right}
td:first-child,th:first-child{text-align:left}
.pos{color:var(--good)}.neg{color:var(--bad)}
.weights-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:14px}
@media(max-width:600px){.weights-grid{grid-template-columns:1fr}}
.config-list{margin:0;padding-left:18px;color:var(--muted);font-size:.86rem;line-height:1.7}
.note{font-size:.78rem;color:var(--muted);margin-top:8px}
.foot{margin-top:18px;color:var(--muted);font-size:.75rem;line-height:1.5}
</style>
</head>
<body>
<div class="wrap">
  <div class="header">
    <div>
      <h1 id="title">Portfolio Backtest</h1>
      <div class="meta" id="meta"></div>
    </div>
    <div class="badge">Crypto Quant Lab</div>
  </div>

  <div class="kpi-grid" id="kpis"></div>

  <div class="grid grid-2" style="margin-bottom:14px">
    <div class="card">
      <h2>Equity vs benchmarks (start = 100)</h2>
      <canvas id="chartEquity" height="300"></canvas>
      <div class="legend" id="legendEquity"></div>
    </div>
    <div class="card">
      <h2>Strategy drawdown</h2>
      <canvas id="chartDrawdown" height="300"></canvas>
    </div>
  </div>

  <div class="grid grid-2" style="margin-bottom:14px">
    <div class="card">
      <h2>Exposure / cash</h2>
      <canvas id="chartExposure" height="260"></canvas>
      <div class="legend">
        <span><i class="dot" style="background:#22c55e"></i>Risky exposure</span>
        <span><i class="dot" style="background:#38bdf8"></i>Cash</span>
      </div>
      <p class="note" id="exposureNote"></p>
    </div>
    <div class="card">
      <h2>Target weights at rebalances</h2>
      <canvas id="chartWeights" height="260"></canvas>
      <div class="legend" id="legendWeights"></div>
    </div>
  </div>

  <div class="card" style="margin-bottom:14px">
    <h2>Metrics</h2>
    <div class="table-wrap" id="metricsTable"></div>
  </div>

  <div class="weights-grid">
    <div class="card"><h2>First scheduled rebalance</h2><div id="firstWeights"></div></div>
    <div class="card"><h2>Last scheduled rebalance</h2><div id="lastWeights"></div></div>
  </div>

  <div class="card" style="margin-bottom:14px">
    <h2>Rebalance history</h2>
    <div class="table-tools"><input id="q" type="text" placeholder="Search date or action…"/></div>
    <div class="table-wrap">
      <table>
        <thead><tr id="tradesHead"></tr></thead>
        <tbody id="tradesBody"></tbody>
      </table>
    </div>
  </div>

  <div class="grid grid-2">
    <div class="card">
      <h2>Configuration</h2>
      <ul class="config-list" id="configList"></ul>
      <p class="note" id="methodNote"></p>
    </div>
    <div class="card">
      <h2>Data</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Symbol</th><th>Fetched</th><th>Aligned</th><th>Used</th></tr></thead>
          <tbody id="dataBody"></tbody>
        </table>
      </div>
      <p class="note" id="dataNote"></p>
    </div>
  </div>

  <div class="foot" id="foot"></div>
</div>

<script id="payload" type="application/json">__PAYLOAD__</script>
<script>
(function(){
  const P = JSON.parse(document.getElementById('payload').textContent);
  const ASSETS = P.assets.concat(['Cash']);
  const ASSET_COLORS = P.asset_colors.concat([P.cash_color]);

  const fmtNum = (x, d=2) => (x===null||x===undefined||Number.isNaN(x)) ? '—' : Number(x).toFixed(d);
  const fmtPct = (x) => (x===null||x===undefined||Number.isNaN(x)) ? '—' : (x>=0?'+':'') + Number(x).toFixed(2) + '%';
  const fmtPctNoSign = (x) => (x===null||x===undefined||Number.isNaN(x)) ? '—' : Number(x).toFixed(2) + '%';
  const cls = (x) => x>0 ? 'pos' : (x<0 ? 'neg' : '');
  const fmtDate = (ms) => new Date(ms).toISOString().slice(0,10);

  function setupCanvas(cv){
    const dpr = window.devicePixelRatio || 1;
    const w = cv.clientWidth || 900;
    const h = cv.clientHeight || 280;
    cv.width = Math.max(1, Math.floor(w*dpr));
    cv.height = Math.max(1, Math.floor(h*dpr));
    const ctx = cv.getContext('2d');
    ctx.setTransform(dpr,0,0,dpr,0,0);
    return {ctx,w,h};
  }

  function drawGrid(ctx, pad, w, h, ymin, ymax, fmtY, x0, x1){
    ctx.font = '11px sans-serif';
    for(let i=0;i<=4;i++){
      const y = pad.t + i/4*(h-pad.t-pad.b);
      const value = ymax - i/4*(ymax-ymin);
      ctx.strokeStyle = 'rgba(36,50,82,.7)';
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(pad.l,y); ctx.lineTo(w-pad.r,y); ctx.stroke();
      ctx.fillStyle = '#8b9bb8';
      ctx.fillText(fmtY(value), 6, y+4);
    }
    ctx.fillStyle = '#8b9bb8';
    if(x0) ctx.fillText(x0, pad.l, h-8);
    if(x1){ const tw = ctx.measureText(x1).width; ctx.fillText(x1, w-pad.r-tw, h-8); }
  }

  function plotLine(ctx, values, color, lw, pad, w, h, ymin, ymax){
    ctx.beginPath();
    ctx.strokeStyle = color;
    ctx.lineWidth = lw;
    let started = false;
    for(let i=0;i<values.length;i++){
      const v = values[i];
      if(v===null || v===undefined){ started=false; continue; }
      const x = pad.l + (values.length<=1?0:i/(values.length-1))*(w-pad.l-pad.r);
      const y = pad.t + (1-(v-ymin)/(ymax-ymin))*(h-pad.t-pad.b);
      if(!started){ ctx.moveTo(x,y); started=true; } else { ctx.lineTo(x,y); }
    }
    ctx.stroke();
  }

  function drawLineChart(cv, series, opts){
    const {ctx,w,h} = setupCanvas(cv);
    ctx.clearRect(0,0,w,h);
    const pad = {l:52,r:12,t:14,b:28};
    let ymin = Infinity, ymax = -Infinity;
    for(const s of series) for(const v of s.values){ if(v!==null && v!==undefined){ ymin=Math.min(ymin,v); ymax=Math.max(ymax,v); } }
    if(opts.zero){ ymin = Math.min(ymin,0); ymax = Math.max(ymax,0); }
    if(opts.yMin!==undefined) ymin = opts.yMin;
    if(opts.yMax!==undefined) ymax = opts.yMax;
    if(!(ymax>ymin)){ ymin = 0; ymax = 1; }
    if(opts.pad!==false){ const r=(ymax-ymin)*0.06; if(opts.yMin===undefined) ymin-=r; if(opts.yMax===undefined) ymax+=r; }
    drawGrid(ctx,pad,w,h,ymin,ymax,opts.fmtY,(P.dates[0]?fmtDate(P.dates[0]):''),(P.dates.length?fmtDate(P.dates[P.dates.length-1]):''));
    for(const s of series) plotLine(ctx,s.values,s.color,1.6,pad,w,h,ymin,ymax);
  }

  function drawDrawdown(cv, values){
    const {ctx,w,h} = setupCanvas(cv);
    ctx.clearRect(0,0,w,h);
    const pad = {l:52,r:12,t:14,b:28};
    let ymin = 0;
    for(const v of values){ if(v!==null && v!==undefined) ymin = Math.min(ymin,v); }
    const ymax = 0;
    const range = (ymax-ymin) || 1;
    const y0 = pad.t + (1-(0-ymin)/range)*(h-pad.t-pad.b);
    drawGrid(ctx,pad,w,h,ymin,ymax,(v)=>v.toFixed(0)+'%',(P.dates[0]?fmtDate(P.dates[0]):''),(P.dates.length?fmtDate(P.dates[P.dates.length-1]):''));
    ctx.beginPath();
    ctx.moveTo(pad.l,y0);
    for(let i=0;i<values.length;i++){
      const v = values[i];
      if(v===null || v===undefined) continue;
      const x = pad.l + (values.length<=1?0:i/(values.length-1))*(w-pad.l-pad.r);
      const y = pad.t + (1-(v-ymin)/range)*(h-pad.t-pad.b);
      ctx.lineTo(x,y);
    }
    ctx.lineTo(w-pad.r,y0);
    ctx.closePath();
    ctx.fillStyle = 'rgba(239,68,68,.18)';
    ctx.fill();
    plotLine(ctx,values,'#ef4444',1.6,pad,w,h,ymin,ymax);
  }

  function drawWeights(cv, history){
    if(!history || !history.length) return;
    const {ctx,w,h} = setupCanvas(cv);
    ctx.clearRect(0,0,w,h);
    const pad = {l:52,r:12,t:14,b:28};
    const n = history.length;
    const xAt = (i) => pad.l + (n<=1?0:i/(n-1))*(w-pad.l-pad.r);
    const yAt = (v) => pad.t + (1-v/100)*(h-pad.t-pad.b);
    drawGrid(ctx,pad,w,h,0,100,(v)=>v.toFixed(0)+'%',history[0].date,history[n-1].date);
    let lower = new Array(n).fill(0);
    for(let j=0;j<ASSETS.length;j++){
      const asset = ASSETS[j];
      const upper = lower.map((v,i)=>v + Number(history[i][asset]||0)*100);
      ctx.beginPath();
      for(let i=0;i<n;i++){ const x=xAt(i), y=yAt(upper[i]); if(i===0) ctx.moveTo(x,y); else ctx.lineTo(x,y); }
      for(let i=n-1;i>=0;i--){ ctx.lineTo(xAt(i),yAt(lower[i])); }
      ctx.closePath();
      ctx.fillStyle = ASSET_COLORS[j] + 'cc';
      ctx.fill();
      lower = upper;
    }
  }

  function renderKpis(){
    const m = P.strategy_metrics;
    const items = [
      ['Total return', fmtPct(m.total_return_pct), cls(m.total_return_pct)],
      ['Sharpe', fmtNum(m.sharpe,2), cls(m.sharpe)],
      ['Max drawdown', fmtPct(m.max_drawdown_pct), 'neg'],
      ['Annual vol', fmtPctNoSign(m.annual_vol_pct), ''],
      ['Avg exposure', fmtPctNoSign(m.avg_exposure_pct), ''],
      ['Rebalances', `${m.rebalances} executed · ${m.skipped_rebalances} skipped`, ''],
      ['Fees', fmtNum(m.total_fees,4), ''],
      ['Turnover', fmtNum(m.total_turnover,2), ''],
    ];
    document.getElementById('kpis').innerHTML = items.map(([label,value,extra]) =>
      `<div class="kpi"><div class="label">${label}</div><div class="value ${extra}">${value}</div></div>`
    ).join('');
  }

  function renderMetrics(){
    const keys = [
      ['total_return_pct','Total return %'],
      ['annual_vol_pct','Annual vol %'],
      ['sharpe','Sharpe'],
      ['max_drawdown_pct','Max drawdown %'],
      ['avg_exposure_pct','Avg exposure %'],
      ['rebalances','Rebalances'],
      ['skipped_rebalances','Skipped'],
      ['total_fees','Fees'],
      ['total_turnover','Turnover'],
    ];
    const portfolios = Object.keys(P.results);
    let html = '<table><thead><tr><th>Portfolio</th>' + keys.map(([,label])=>`<th>${label}</th>`).join('') + '</tr></thead><tbody>';
    for(const name of portfolios){
      const m = P.results[name];
      html += `<tr><td>${name}</td>` + keys.map(([key]) => {
        if(key==='annual_vol_pct' || key==='avg_exposure_pct') return `<td>${fmtPctNoSign(m[key])}</td>`;
        if(key.endsWith('_pct')) return `<td>${fmtPct(m[key])}</td>`;
        if(key==='sharpe') return `<td>${fmtNum(m[key],2)}</td>`;
        if(key==='total_fees') return `<td>${fmtNum(m[key],4)}</td>`;
        if(key==='total_turnover') return `<td>${fmtNum(m[key],2)}</td>`;
        return `<td>${fmtNum(m[key],0)}</td>`;
      }).join('') + '</tr>';
    }
    html += '</tbody></table>';
    document.getElementById('metricsTable').innerHTML = html;
  }

  function weightsTable(weights){
    return '<table class="small"><thead><tr><th>Asset</th><th>Target</th></tr></thead><tbody>' +
      ASSETS.map(asset => `<tr><td>${asset}</td><td>${fmtPctNoSign((weights[asset]||0)*100)}</td></tr>`).join('') +
      '</tbody></table>';
  }

  function renderWeights(){
    document.getElementById('firstWeights').innerHTML = weightsTable(P.first_weights);
    document.getElementById('lastWeights').innerHTML = weightsTable(P.last_weights);
  }

  function renderTrades(){
    const q = (document.getElementById('q').value || '').trim().toLowerCase();
    const rows = P.trades.filter(t => !q || (t.decision_date+' '+t.exec_date+' '+t.action).toLowerCase().includes(q));
    document.getElementById('tradesHead').innerHTML =
      '<th>Decision</th><th>Execution</th><th>Action</th><th>Turnover %</th><th>Fee</th>' +
      ASSETS.map(asset => `<th>${asset}</th>`).join('');
    document.getElementById('tradesBody').innerHTML = rows.map(t =>
      `<tr><td>${t.decision_date}</td><td>${t.exec_date}</td><td>${t.action}</td><td>${fmtPctNoSign(t.turnover*100)}</td><td>${fmtNum(t.fee,4)}</td>` +
      ASSETS.map(asset => `<td>${fmtPctNoSign((t['target_'+asset]||0)*100)}</td>`).join('') +
      '</tr>'
    ).join('');
  }

  function renderData(){
    document.getElementById('dataBody').innerHTML = Object.entries(P.rows_per_symbol).map(([symbol, info]) =>
      `<tr><td>${symbol}</td><td>${info.fetched}</td><td>${info.aligned}</td><td>${info.used}</td></tr>`
    ).join('');
    document.getElementById('dataNote').textContent =
      `Backtest ${P.data.start} to ${P.data.end}; warmup ${P.data.warmup_bars} daily bars.`;
  }

  function renderConfig(){
    document.getElementById('configList').innerHTML = P.config_items;
    document.getElementById('methodNote').textContent = P.method_note;
  }

  function renderLegends(){
    document.getElementById('legendEquity').innerHTML = P.curves.map(c =>
      `<span><i class="dot" style="background:${c.color}"></i>${c.label}</span>`
    ).join('');
    document.getElementById('legendWeights').innerHTML = ASSETS.map((asset, i) =>
      `<span><i class="dot" style="background:${ASSET_COLORS[i]}"></i>${asset}</span>`
    ).join('');
  }

  function drawCharts(){
    drawLineChart(document.getElementById('chartEquity'),
      P.curves.map(c => ({values:c.values, color:c.color})),
      {fmtY:(v)=>v.toFixed(0)});
    drawDrawdown(document.getElementById('chartDrawdown'), P.drawdown);
    drawLineChart(document.getElementById('chartExposure'),
      [{values:P.exposure, color:'#22c55e'}, {values:P.cash, color:'#38bdf8'}],
      {fmtY:(v)=>v.toFixed(0)+'%', yMin:0, yMax:100, pad:false});
    drawWeights(document.getElementById('chartWeights'), P.weights_history);
  }

  document.title = P.title;
  document.getElementById('title').textContent = P.title;
  document.getElementById('meta').textContent = P.meta;
  document.getElementById('foot').textContent = `Generated ${P.generated_at} · Crypto Quant Lab · single-file local report.`;
  document.getElementById('q').addEventListener('input', renderTrades);
  document.getElementById('exposureNote').textContent =
    `Average risky exposure ${fmtPctNoSign(P.strategy_metrics.avg_exposure_pct)}; average cash ${fmtPctNoSign(100 - P.strategy_metrics.avg_exposure_pct)}.`;
  renderKpis();
  renderMetrics();
  renderWeights();
  renderTrades();
  renderData();
  renderConfig();
  renderLegends();
  drawCharts();
  window.addEventListener('resize', drawCharts);
})();
</script>
</body>
</html>
'''
