#!/usr/bin/env python3
"""
Executive Dashboard — Deals Created + Deals Closed by week, filterable by
Primary Lead Source, Pipeline, and Territory.

NO org-level drill-down. Focused on top-line numbers and weekly trend.

Env: GOOGLE_SHEET_ID, GOOGLE_SA_JSON
Output: out/executive_dashboard.html
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import gspread
import pandas as pd
from google.oauth2.service_account import Credentials


OUT_DIR = Path(__file__).resolve().parent.parent / "out"
OUT_DIR.mkdir(exist_ok=True)
OUTPUT = OUT_DIR / "executive_dashboard.html"

GOOGLE_SHEET_ID = os.environ["GOOGLE_SHEET_ID"]
GOOGLE_SA_JSON = os.environ["GOOGLE_SA_JSON"]


# ─────────────────────────────────────────────────────────────────────────────
# Load + prep
# ─────────────────────────────────────────────────────────────────────────────

def load_sheet():
    creds = Credentials.from_service_account_info(
        json.loads(GOOGLE_SA_JSON),
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    client = gspread.authorize(creds)
    sh = client.open_by_key(GOOGLE_SHEET_ID)
    deals = pd.DataFrame(sh.worksheet("deals_raw").get_all_records())
    co = pd.DataFrame(sh.worksheet("companies_raw").get_all_records())
    return deals, co


print("Loading sheet…")
deals, co = load_sheet()
print(f"  → {len(deals)} deals, {len(co)} companies")

deals["company_id"] = deals["company_id"].astype(str)
co["company_id"] = co["company_id"].astype(str)
co = co.rename(columns={
    "name": "co_name", "zip": "co_zip",
    "market": "co_market", "territory": "co_territory",
})
df = deals.merge(co, on="company_id", how="left")

# Dates
df["create_dt"] = pd.to_datetime(df["create_date"], errors="coerce", utc=True).dt.tz_localize(None)
df["won_dt"]    = pd.to_datetime(df["won_time"],    errors="coerce", utc=True).dt.tz_localize(None)
df = df[df["create_dt"].notna()].copy()

# Lead source / pipeline / territory (use the best available)
df["lead_source"] = df["primary_lead_source"].fillna("").astype(str).str.strip()
df.loc[df["lead_source"] == "", "lead_source"] = "(none)"

df["pipeline_label"] = df["pipeline_name"].fillna("").astype(str).str.strip()
df.loc[df["pipeline_label"] == "", "pipeline_label"] = "(no pipeline)"

# Territory: prefer fine company territory, fall back to broad market, then deal-level
def first_nonblank(*vals):
    for v in vals:
        s = str(v).strip()
        if s and s.lower() not in ("none", "nan", "unknown", "unassigned"):
            return s
    return "Unassigned"

df["territory_label"] = [first_nonblank(t, m, d)
                          for t, m, d in zip(df["co_territory"], df["co_market"], df["territory"])]


# ─────────────────────────────────────────────────────────────────────────────
# Compute per-deal week labels (ISO)
# ─────────────────────────────────────────────────────────────────────────────

def iso_label(ts):
    if pd.isna(ts): return None
    iy, iw, _ = ts.isocalendar()
    return f"{iy}-W{iw:02d}"

df["cw"] = df["create_dt"].apply(iso_label)
df["ww"] = df["won_dt"].apply(iso_label)

# Restrict to deals created since 2024-01-01 for size; the dashboard windows are recent anyway
df = df[df["create_dt"] >= "2024-01-01"].copy()


# ─────────────────────────────────────────────────────────────────────────────
# Compact encoding: assign integer codes to lead_source/pipeline/territory
# (keeps the JSON payload small for ~150K deals)
# ─────────────────────────────────────────────────────────────────────────────

def codes(series):
    uniq = sorted(series.unique())
    code_map = {v: i for i, v in enumerate(uniq)}
    return uniq, [code_map[v] for v in series]

ls_labels, ls_codes  = codes(df["lead_source"])
pl_labels, pl_codes  = codes(df["pipeline_label"])
te_labels, te_codes  = codes(df["territory_label"])

# Deal-level array: [cw, ww or null, ls_code, pl_code, te_code]
records = list(zip(df["cw"], df["ww"], ls_codes, pl_codes, te_codes))
# Replace NaN ww with None
records = [[cw, (ww if isinstance(ww, str) else None), ls, pl, te] for cw, ww, ls, pl, te in records]

print(f"Deal records: {len(records):,}")
print(f"Lead sources: {len(ls_labels)}, Pipelines: {len(pl_labels)}, Territories: {len(te_labels)}")


# ─────────────────────────────────────────────────────────────────────────────
# Build week label index (only weeks that appear in data, sorted)
# ─────────────────────────────────────────────────────────────────────────────

all_weeks = sorted({r[0] for r in records} | {r[1] for r in records if r[1]})
print(f"Weeks covered: {len(all_weeks)} ({all_weeks[0]} → {all_weeks[-1]})")


# ─────────────────────────────────────────────────────────────────────────────
# Render HTML
# ─────────────────────────────────────────────────────────────────────────────

UPDATE_DATE = datetime.now(timezone.utc).strftime("%B %-d, %Y")

DATA = {
    "updateDate": UPDATE_DATE,
    "leadSources": ls_labels,
    "pipelines": pl_labels,
    "territories": te_labels,
    "weeks": all_weeks,
    "deals": records,  # [cw, ww, lsCode, plCode, teCode]
}

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Executive Dashboard — Deals Created &amp; Closed</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       background: #f6f7f9; color: #1a2e4a; }
.header { background: #fff; border-bottom: 1px solid #e2e8f0; padding: 16px 28px; display: flex; justify-content: space-between; align-items: center; }
.header h1 { font-size: 20px; color: #1e3a5f; }
.header .meta { font-size: 12px; color: #6b7280; }
.back { color: #1e3a5f; text-decoration: none; font-size: 13px; margin-right: 16px; }

.filters { background: #fff; border-bottom: 1px solid #e2e8f0; padding: 12px 28px; display: flex; gap: 16px; align-items: center; flex-wrap: wrap; }
.filter-group { display: flex; align-items: center; gap: 6px; position: relative; }
.filter-group label { font-size: 11px; font-weight: 700; color: #6b7280; text-transform: uppercase; letter-spacing: 0.5px; }
.dd-btn { font-size: 13px; padding: 6px 12px; border: 1px solid #d1d5db; border-radius: 6px; background: #fff; cursor: pointer; min-width: 180px; text-align: left; }
.dd-menu { position: absolute; top: 100%; left: 0; margin-top: 4px; background: #fff; border: 1px solid #d1d5db; border-radius: 6px; box-shadow: 0 4px 12px rgba(0,0,0,0.1); padding: 8px; max-height: 320px; overflow-y: auto; z-index: 50; min-width: 240px; display: none; }
.dd-menu.open { display: block; }
.dd-menu label { display: block; font-size: 13px; padding: 4px 6px; cursor: pointer; }
.dd-menu label:hover { background: #f3f4f6; border-radius: 4px; }
.dd-sticky { position: sticky; top: 0; background: #fff; z-index: 2; padding: 6px 4px; border-bottom: 1px solid #eee; margin-bottom: 4px; }
.dd-actions { display: flex; gap: 6px; margin-bottom: 6px; }
.dd-actions button { font-size: 11px; padding: 3px 10px; border: 1px solid #ccc; border-radius: 4px; background: #fff; cursor: pointer; }
.dd-actions button.primary { background: #1e3a5f; color: #fff; border-color: #1e3a5f; }
.dd-search { width: 100%; padding: 5px 8px; font-size: 12px; border: 1px solid #d1d5db; border-radius: 4px; box-sizing: border-box; }
.dd-search:focus { outline: none; border-color: #1e3a5f; }
.reset-btn { margin-left: auto; background: #fff; border: 1px solid #d1d5db; border-radius: 6px; padding: 6px 14px; font-size: 12px; color: #555; cursor: pointer; }

.content { padding: 24px 28px; max-width: 1600px; margin: 0 auto; }
.section { background: #fff; border-radius: 12px; padding: 20px 24px; box-shadow: 0 1px 4px rgba(0,0,0,.05); margin-bottom: 24px; }
.section h2 { font-size: 16px; color: #1e3a5f; margin-bottom: 14px; }
.kpi-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 16px; }
.kpi-card { background: #f8fafc; border-radius: 8px; padding: 14px 18px; border: 1px solid #e2e8f0; }
.kpi-card .label { font-size: 10px; font-weight: 700; color: #888; text-transform: uppercase; letter-spacing: 0.6px; margin-bottom: 6px; }
.kpi-card .value { font-size: 28px; font-weight: 700; color: #1a2e4a; }
.kpi-card .sub { font-size: 11px; color: #888; margin-top: 4px; }
.kpi-card.neg .value { color: #dc2626; }
.kpi-card.pos .value { color: #16a34a; }

.chart-card { background: #fff; padding: 12px; border-radius: 8px; }
.chart-controls { display: flex; gap: 10px; align-items: center; font-size: 12px; margin-bottom: 8px; color: #6b7280; }
.chart-controls input { width: 54px; padding: 3px 6px; font-size: 12px; }
.chart-controls button { font-size: 11px; padding: 3px 10px; border: 1px solid #d1d5db; background: #fff; border-radius: 4px; cursor: pointer; }
.chart-wrap { height: 220px; }
footer { text-align: center; font-size: 11px; color: #9ca3af; padding: 20px; }
</style>
</head>
<body>

<div class="header">
  <div>
    <h1>📊 Executive Dashboard</h1>
    <div class="meta">Deals Created (forward indicator) &amp; Deals Closed (current performance) · by week</div>
  </div>
  <div>
    <a class="back" href="index.html">← All reports</a>
    <span class="meta">Last build <span id="update-date">—</span></span>
  </div>
</div>

<div class="filters">
  <div class="filter-group">
    <label>Lead Source</label>
    <button class="dd-btn" id="ls-btn">All Lead Sources ▾</button>
    <div class="dd-menu" id="ls-menu"></div>
  </div>
  <div class="filter-group">
    <label>Pipeline</label>
    <button class="dd-btn" id="pl-btn">All Pipelines ▾</button>
    <div class="dd-menu" id="pl-menu"></div>
  </div>
  <div class="filter-group">
    <label>Territory</label>
    <button class="dd-btn" id="te-btn">All Territories ▾</button>
    <div class="dd-menu" id="te-menu"></div>
  </div>
  <button class="reset-btn" onclick="resetFilters()">↺ Reset</button>
</div>

<div class="content">

  <!-- Deals Created -->
  <div class="section">
    <h2>📈 Deals Created — Forward Indicator</h2>
    <div class="kpi-row">
      <div class="kpi-card"><div class="label">This Week</div><div class="value" id="c-tw">—</div><div class="sub" id="c-tw-sub">current ISO week</div></div>
      <div class="kpi-card"><div class="label">Last Week</div><div class="value" id="c-lw">—</div><div class="sub" id="c-lw-sub">previous ISO week</div></div>
      <div class="kpi-card" id="c-90-card"><div class="label">Last 90 Days vs Prev 90</div><div class="value" id="c-90">—</div><div class="sub" id="c-90-sub">—</div></div>
      <div class="kpi-card" id="c-mtd-card"><div class="label">MTD vs LY</div><div class="value" id="c-mtd">—</div><div class="sub" id="c-mtd-sub">—</div></div>
    </div>
    <div class="chart-card">
      <div class="chart-controls">
        <span>Weekly trend · </span>
        <label>Start Week <input type="number" id="c-start" min="1" max="53" value="1"></label>
        <label>End Week <input type="number" id="c-end" min="1" max="53" value="20"></label>
        <button onclick="applyZoom('c')">Apply</button>
        <button onclick="setRange('c',1,17)">Wk 1–17</button>
        <button onclick="setRange('c',1,13)">Q1</button>
        <button onclick="setRange('c',14,26)">Q2</button>
        <span id="c-summary" style="margin-left:auto;color:#16a34a;font-weight:600"></span>
      </div>
      <div class="chart-wrap"><canvas id="c-chart"></canvas></div>
    </div>
  </div>

  <!-- Deals Closed -->
  <div class="section">
    <h2>🏆 Deals Closed (Won) — Current Performance</h2>
    <div class="kpi-row">
      <div class="kpi-card"><div class="label">Wins This Week</div><div class="value" id="w-tw">—</div><div class="sub">current ISO week</div></div>
      <div class="kpi-card"><div class="label">Wins Last Week</div><div class="value" id="w-lw">—</div><div class="sub">previous ISO week</div></div>
      <div class="kpi-card" id="w-90-card"><div class="label">Trailing 90 Days vs Prev 90</div><div class="value" id="w-90">—</div><div class="sub" id="w-90-sub">—</div></div>
      <div class="kpi-card" id="w-ytd-card"><div class="label">YTD vs LY</div><div class="value" id="w-ytd">—</div><div class="sub" id="w-ytd-sub">—</div></div>
    </div>
    <div class="chart-card">
      <div class="chart-controls">
        <span>Weekly wins · </span>
        <label>Start Week <input type="number" id="w-start" min="1" max="53" value="1"></label>
        <label>End Week <input type="number" id="w-end" min="1" max="53" value="20"></label>
        <button onclick="applyZoom('w')">Apply</button>
        <button onclick="setRange('w',1,17)">Wk 1–17</button>
        <button onclick="setRange('w',1,13)">Q1</button>
        <button onclick="setRange('w',14,26)">Q2</button>
        <span id="w-summary" style="margin-left:auto;color:#16a34a;font-weight:600"></span>
      </div>
      <div class="chart-wrap"><canvas id="w-chart"></canvas></div>
    </div>
  </div>

  <footer>Auto-updated daily · Lead source, pipeline, and territory filters compound</footer>
</div>

<script>
const DATA = __DATA_JSON__;
document.getElementById('update-date').textContent = DATA.updateDate;

// Filter state — Sets of selected codes (empty = all selected)
const filters = { ls: new Set(), pl: new Set(), te: new Set() };

// ── Build filter dropdowns ──────────────────────────────────────────────────
function buildDropdown(prefix, labels) {
  const menu = document.getElementById(prefix+'-menu');
  menu.innerHTML = '';
  // Sticky header at top: actions + search box
  const sticky = document.createElement('div'); sticky.className = 'dd-sticky';
  const actions = document.createElement('div'); actions.className = 'dd-actions';
  actions.innerHTML = `<button onclick="ddSelectAll('${prefix}')">All</button>
    <button onclick="ddSelectNone('${prefix}')">None</button>
    <button class="primary" onclick="ddApply('${prefix}')">Apply</button>`;
  sticky.appendChild(actions);
  const search = document.createElement('input');
  search.type = 'search'; search.className = 'dd-search';
  search.placeholder = 'Search…';
  search.addEventListener('input', e => {
    const q = e.target.value.toLowerCase();
    menu.querySelectorAll('label').forEach(lab => {
      const txt = lab.textContent.toLowerCase();
      lab.style.display = (q === '' || txt.includes(q)) ? '' : 'none';
    });
  });
  search.addEventListener('click', e => e.stopPropagation());
  sticky.appendChild(search);
  menu.appendChild(sticky);
  labels.forEach((lbl, idx) => {
    const lab = document.createElement('label');
    const cb = document.createElement('input');
    cb.type = 'checkbox'; cb.value = idx; cb.checked = true; cb.dataset.code = idx;
    lab.appendChild(cb);
    lab.appendChild(document.createTextNode(' ' + lbl));
    menu.appendChild(lab);
  });
  const btn = document.getElementById(prefix+'-btn');
  btn.onclick = e => { e.stopPropagation();
    document.querySelectorAll('.dd-menu').forEach(m => { if (m !== menu) m.classList.remove('open'); });
    menu.classList.toggle('open');
  };
}
function ddSelectAll(prefix){
  // Only toggle items currently visible (respects search filter)
  document.querySelectorAll(`#${prefix}-menu label`).forEach(lab => {
    if (lab.style.display !== 'none') {
      const cb = lab.querySelector('input[type=checkbox]');
      if (cb) cb.checked = true;
    }
  });
}
function ddSelectNone(prefix){
  document.querySelectorAll(`#${prefix}-menu label`).forEach(lab => {
    if (lab.style.display !== 'none') {
      const cb = lab.querySelector('input[type=checkbox]');
      if (cb) cb.checked = false;
    }
  });
}
function ddApply(prefix){
  const all = [...document.querySelectorAll(`#${prefix}-menu input[type=checkbox]`)];
  const checked = all.filter(c => c.checked).map(c => parseInt(c.dataset.code));
  // If all are checked or none, treat as "All"
  if (checked.length === 0 || checked.length === all.length) {
    filters[prefix] = new Set();
    document.getElementById(prefix+'-btn').textContent = `All ${{ls:'Lead Sources',pl:'Pipelines',te:'Territories'}[prefix]} ▾`;
  } else {
    filters[prefix] = new Set(checked);
    const labels = {ls:DATA.leadSources, pl:DATA.pipelines, te:DATA.territories}[prefix];
    const display = checked.length === 1 ? labels[checked[0]] : `${checked.length} selected`;
    document.getElementById(prefix+'-btn').textContent = display + ' ▾';
  }
  document.getElementById(prefix+'-menu').classList.remove('open');
  refresh();
}
document.addEventListener('click', e => {
  document.querySelectorAll('.dd-menu').forEach(m => {
    if (!m.contains(e.target) && !document.getElementById(m.id.replace('-menu','-btn')).contains(e.target)) {
      m.classList.remove('open');
    }
  });
});

function resetFilters(){
  filters.ls.clear(); filters.pl.clear(); filters.te.clear();
  ['ls','pl','te'].forEach(p => {
    ddSelectAll(p);
    const labels = {ls:'Lead Sources',pl:'Pipelines',te:'Territories'}[p];
    document.getElementById(p+'-btn').textContent = 'All ' + labels + ' ▾';
  });
  refresh();
}

buildDropdown('ls', DATA.leadSources);
buildDropdown('pl', DATA.pipelines);
buildDropdown('te', DATA.territories);

// ── Data slicing ────────────────────────────────────────────────────────────
function dealMatches(d){
  // d = [cw, ww, ls, pl, te]
  if (filters.ls.size > 0 && !filters.ls.has(d[2])) return false;
  if (filters.pl.size > 0 && !filters.pl.has(d[3])) return false;
  if (filters.te.size > 0 && !filters.te.has(d[4])) return false;
  return true;
}

function todayISO(){
  const d = new Date();
  return { year: d.getUTCFullYear(), week: getISOWeek(d) };
}
function getISOWeek(d){
  const target = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
  const dayNum = target.getUTCDay() || 7;
  target.setUTCDate(target.getUTCDate() + 4 - dayNum);
  const yearStart = new Date(Date.UTC(target.getUTCFullYear(), 0, 1));
  return Math.ceil((((target - yearStart) / 86400000) + 1) / 7);
}
function isoFromOffset(year, week, weekOffset){
  let w = week + weekOffset;
  let y = year;
  while (w < 1) { y--; w += 52; }
  while (w > 52) { y++; w -= 52; }
  return `${y}-W${String(w).padStart(2,'0')}`;
}

// ── Compute aggregates ─────────────────────────────────────────────────────
function computeAggregates(){
  const filtered = DATA.deals.filter(dealMatches);
  const createdByWeek = {}, wonByWeek = {};
  for (const d of filtered) {
    if (d[0]) createdByWeek[d[0]] = (createdByWeek[d[0]]||0) + 1;
    if (d[1]) wonByWeek[d[1]]    = (wonByWeek[d[1]]||0) + 1;
  }
  return { filtered, createdByWeek, wonByWeek };
}

// Today + relative weeks
function relativeWeeks() {
  const { year, week } = todayISO();
  return {
    thisWk: `${year}-W${String(week).padStart(2,'0')}`,
    lastWk: isoFromOffset(year, week, -1),
    year, week,
  };
}

// Date-range filter for deals: 90 days, MTD, etc. — operates on the create_dt or won_dt
// but we only stored week labels. For 90d/MTD comparisons, sum the relevant week ranges.

function weeksBetween(yearWeekStart, yearWeekEnd){
  // Returns inclusive list of ISO week labels between two YYYY-Www strings
  const out = [];
  let [ys, ws] = yearWeekStart.split('-W').map(Number);
  let [ye, we] = yearWeekEnd.split('-W').map(Number);
  while (ys < ye || (ys === ye && ws <= we)) {
    out.push(`${ys}-W${String(ws).padStart(2,'0')}`);
    ws++;
    if (ws > 52) { ws = 1; ys++; }
  }
  return out;
}

function sumWeeks(map, weeks){ return weeks.reduce((s,w) => s + (map[w]||0), 0); }

// 13 weeks = ~90 days. YTD = weeks since W1 of current year.
function refresh(){
  const { createdByWeek, wonByWeek } = computeAggregates();
  const { year, week, thisWk, lastWk } = relativeWeeks();

  const thisWkCreated = createdByWeek[thisWk] || 0;
  const lastWkCreated = createdByWeek[lastWk] || 0;
  const thisWkWon = wonByWeek[thisWk] || 0;
  const lastWkWon = wonByWeek[lastWk] || 0;

  // 90d (13 weeks) — current vs previous
  const last13Start = isoFromOffset(year, week, -13);
  const last13End   = isoFromOffset(year, week, -1);
  const prev13Start = isoFromOffset(year, week, -26);
  const prev13End   = isoFromOffset(year, week, -14);
  const c90 = sumWeeks(createdByWeek, weeksBetween(last13Start, last13End));
  const c90p = sumWeeks(createdByWeek, weeksBetween(prev13Start, prev13End));
  const w90 = sumWeeks(wonByWeek, weeksBetween(last13Start, last13End));
  const w90p = sumWeeks(wonByWeek, weeksBetween(prev13Start, prev13End));

  // MTD (created): all weeks in current year up to current week, restricted to weeks
  // that fall in the current month. Simplification: use last 4 weeks (≈ month) for MTD vs LY.
  // Better: count weeks since the start of current month.
  // For now: MTD ≈ last 4 weeks vs same 4 weeks last year (52 weeks back)
  const mtdStart = isoFromOffset(year, week, -3);  // last 4 weeks including current
  const mtdEnd   = thisWk;
  const cMtd  = sumWeeks(createdByWeek, weeksBetween(mtdStart, mtdEnd));
  const cMtdLY = sumWeeks(createdByWeek, weeksBetween(isoFromOffset(year, week, -55), isoFromOffset(year, week, -52)));

  // YTD wins = all weeks from W01 current year through last week
  const ytdStart = `${year}-W01`;
  const wYtd  = sumWeeks(wonByWeek, weeksBetween(ytdStart, lastWk));
  const wYtdLY = sumWeeks(wonByWeek, weeksBetween(`${year-1}-W01`, isoFromOffset(year, week, -53)));

  // Render
  setKpi('c-tw', thisWkCreated);
  setKpi('c-lw', lastWkCreated);
  setKpiPct('c-90', c90, c90p, 'prior 90d');
  setKpiPct('c-mtd', cMtd, cMtdLY, 'last 4 wks LY');

  setKpi('w-tw', thisWkWon);
  setKpi('w-lw', lastWkWon);
  setKpiPct('w-90', w90, w90p, 'prior 90d');
  setKpiPct('w-ytd', wYtd, wYtdLY, 'same period LY');

  renderChart('c', createdByWeek, '#1e40af');
  renderChart('w', wonByWeek, '#16a34a');
}

function setKpi(id, v){ document.getElementById(id).textContent = (v||0).toLocaleString(); }
function setKpiPct(id, cur, prev, subText){
  document.getElementById(id).textContent = (cur||0).toLocaleString();
  const sub = document.getElementById(id+'-sub');
  const card = document.getElementById(id+'-card');
  card.classList.remove('neg','pos');
  if (prev > 0) {
    const pct = Math.round((cur/prev - 1) * 1000) / 10;
    sub.textContent = `vs ${prev.toLocaleString()} ${subText} (${pct>=0?'+':''}${pct}%)`;
    card.classList.add(pct < 0 ? 'neg' : 'pos');
  } else {
    sub.textContent = `vs 0 ${subText}`;
  }
}

// ── Charts (weekly bar with zoom-range picker) ─────────────────────────────
const charts = {};
function renderChart(prefix, byWeek, color){
  const start = parseInt(document.getElementById(prefix+'-start').value) || 1;
  const end   = parseInt(document.getElementById(prefix+'-end').value) || 20;
  const { year } = relativeWeeks();
  const curLabels = [], curData = [], prevData = [];
  for (let w = start; w <= end; w++) {
    const wk = `${year}-W${String(w).padStart(2,'0')}`;
    const wkPrev = `${year-1}-W${String(w).padStart(2,'0')}`;
    curLabels.push(`Wk ${w}`);
    curData.push(byWeek[wk] || 0);
    prevData.push(byWeek[wkPrev] || 0);
  }
  const curSum = curData.reduce((a,b)=>a+b,0);
  const prevSum = prevData.reduce((a,b)=>a+b,0);
  document.getElementById(prefix+'-summary').textContent =
    `${curSum.toLocaleString()} this year · ${prevSum.toLocaleString()} same wks LY` +
    (prevSum>0 ? ` (${curSum>=prevSum?'+':''}${Math.round((curSum/prevSum-1)*100)}%)` : '');

  if (charts[prefix]) {
    charts[prefix].data.labels = curLabels;
    charts[prefix].data.datasets[0].data = curData;
    charts[prefix].data.datasets[1].data = prevData;
    charts[prefix].update();
  } else {
    charts[prefix] = new Chart(document.getElementById(prefix+'-chart'), {
      type: 'bar',
      data: {
        labels: curLabels,
        datasets: [
          { label: 'This Year', data: curData, backgroundColor: color },
          { label: 'Prior Year', data: prevData, backgroundColor: color+'40' },
        ]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { position: 'bottom', labels: { font: { size: 11 } } } },
        scales: {
          x: { grid: { display: false }, ticks: { font: { size: 10 } } },
          y: { grid: { color: '#f0f0f0' }, ticks: { font: { size: 10 } } },
        }
      }
    });
  }
}

function applyZoom(prefix){
  const data = computeAggregates();
  renderChart(prefix, prefix==='c' ? data.createdByWeek : data.wonByWeek, prefix==='c' ? '#1e40af':'#16a34a');
}
function setRange(prefix, s, e){
  document.getElementById(prefix+'-start').value = s;
  document.getElementById(prefix+'-end').value = e;
  applyZoom(prefix);
}

refresh();
</script>
</body>
</html>
"""

# Inject the data JSON
html = HTML.replace("__DATA_JSON__", json.dumps(DATA, default=str))
OUTPUT.write_text(html, encoding="utf-8")
print(f"  ✓ Written: {OUTPUT}  ({len(html)//1024} KB)")
