#!/usr/bin/env python3
"""
Google Ads Dashboard — absolutes only.

Per-city view:
  - Google Ads spend (from google_ads_raw)
  - HubSpot deals created where primary_lead_source = "Google Adwords PPC"
  - HubSpot deals won (subset)
  - Raw "effective CPA" = spend / won deals (city-level)

No campaign-level attribution attempted — Pipedrive migration stripped GCLID
so we work in absolutes by city.

Env: GOOGLE_SHEET_ID, GOOGLE_SA_JSON
Output: out/google_ads_dashboard.html
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import gspread
import pandas as pd
from google.oauth2.service_account import Credentials


OUT_DIR = Path(__file__).resolve().parent.parent / "out"
OUT_DIR.mkdir(exist_ok=True)
OUTPUT = OUT_DIR / "google_ads_dashboard.html"

CITIES = ["Dallas", "Houston", "San Antonio", "Austin", "Phoenix", "Utah", "Tucson"]


def load_sheet():
    creds = Credentials.from_service_account_info(
        json.loads(os.environ["GOOGLE_SA_JSON"]),
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    sh = gspread.authorize(creds).open_by_key(os.environ["GOOGLE_SHEET_ID"])
    ga = pd.DataFrame(sh.worksheet("google_ads_raw").get_all_records())
    deals = pd.DataFrame(sh.worksheet("deals_raw").get_all_records())
    return ga, deals


print("Loading sheet…")
ga, deals = load_sheet()
print(f"  → {len(ga)} Google Ads rows, {len(deals)} deals")


# ─── Google Ads side: parse city from campaign name ───
def city_from_campaign(name):
    s = (name or "").lower()
    for c in CITIES:
        # Match "Dallas", "Dallas - Branded", "Performance Max - Dallas", etc.
        if re.search(rf"\b{c.lower()}\b", s):
            return c
    return None

ga["date"] = pd.to_datetime(ga["date"], errors="coerce")
ga = ga.dropna(subset=["date"]).copy()
ga["cost_usd"] = pd.to_numeric(ga["cost_usd"], errors="coerce").fillna(0.0)
ga["clicks"] = pd.to_numeric(ga["clicks"], errors="coerce").fillna(0).astype(int)
ga["impressions"] = pd.to_numeric(ga["impressions"], errors="coerce").fillna(0).astype(int)
ga["conversions"] = pd.to_numeric(ga["conversions"], errors="coerce").fillna(0.0)
ga["city"] = ga["campaign_name"].apply(city_from_campaign)
ga["cw"] = ga["date"].dt.isocalendar().apply(lambda r: f"{int(r['year'])}-W{int(r['week']):02d}", axis=1) if False else ga["date"].apply(lambda d: f"{d.isocalendar().year}-W{d.isocalendar().week:02d}")

ga_unmapped = ga[ga["city"].isna()]
if len(ga_unmapped):
    print(f"  ! {len(ga_unmapped)} Google Ads rows with unmapped campaign name:")
    print("    " + ", ".join(sorted(ga_unmapped["campaign_name"].unique())[:10]))
ga = ga[ga["city"].notna()].copy()


# ─── HubSpot deals side: city from pipeline, filter to Google Ads PPC ───
_CITY_FOR_PIPELINE = {
    "dallas - wisdom teeth guys":              "Dallas",
    "dallas - wisdom teeth guys pipedrive":    "Dallas",
    "houston - wisdom teeth guys":             "Houston",
    "houston - wisdom teeth guys pipedrive":   "Houston",
    "san antonio - wisdom teeth guys":         "San Antonio",
    "san antonio - wisdom teeth guys pipedrive":"San Antonio",
    "austin - wisdom teeth guys":              "Austin",
    "austin - wisdom teeth guys pipedrive":    "Austin",
    "utah - wisdom teeth guys":                "Utah",
    "utah - wisdom teeth guys pipedrive":      "Utah",
    "phoenix - wisdom teeth guys":             "Phoenix",
    "phoenix - wisdom teeth guys pipedrive":   "Phoenix",
    "tucson - wisdom teeth guys":              "Tucson",
}
deals["pipeline_label"] = deals["pipeline_name"].fillna("").astype(str).str.strip()
deals["city"] = deals["pipeline_label"].str.lower().map(_CITY_FOR_PIPELINE)
deals = deals[deals["city"].notna()].copy()

deals["create_dt"] = pd.to_datetime(deals["create_date"], errors="coerce", utc=True).dt.tz_localize(None)
deals["won_dt"]    = pd.to_datetime(deals["won_time"],    errors="coerce", utc=True).dt.tz_localize(None)
deals["lead_source"] = deals["primary_lead_source"].fillna("").astype(str).str.strip().str.lower()
# Lock to Google Ads PPC deals
ads_deals = deals[deals["lead_source"] == "google adwords ppc"].copy()
print(f"  Google-Ads-PPC deals in HubSpot: {len(ads_deals):,}")

def iso_label(ts):
    if pd.isna(ts): return None
    iy, iw, _ = ts.isocalendar()
    return f"{iy}-W{iw:02d}"

ads_deals["cw"] = ads_deals["create_dt"].apply(iso_label)
ads_deals["ww"] = ads_deals["won_dt"].apply(iso_label)


# ─── Build per-city week-keyed aggregations ───
# Limit to current date range covered by Google Ads (last ~30 days)
min_date = ga["date"].min().date()
max_date = ga["date"].max().date()
print(f"  Google Ads coverage: {min_date} → {max_date}")

# Restrict deals to the same period for fair comparison
ads_deals_in_window = ads_deals[
    (ads_deals["create_dt"] >= pd.Timestamp(min_date)) &
    (ads_deals["create_dt"] <= pd.Timestamp(max_date) + pd.Timedelta(days=1))
].copy()

# Aggregate per city per week
spend_by_city_week = ga.groupby(["city", "cw"])["cost_usd"].sum().to_dict()
clicks_by_city_week = ga.groupby(["city", "cw"])["clicks"].sum().to_dict()
impr_by_city_week  = ga.groupby(["city", "cw"])["impressions"].sum().to_dict()

created_by_city_week = ads_deals_in_window.groupby(["city","cw"]).size().to_dict()
# Won = any deal where won_dt falls in window (regardless of creation date — booking view)
won_view = ads_deals[(ads_deals["won_dt"] >= pd.Timestamp(min_date)) &
                     (ads_deals["won_dt"] <= pd.Timestamp(max_date) + pd.Timedelta(days=1))]
won_by_city_week = won_view.groupby(["city","ww"]).size().to_dict()


# All weeks in the range, sorted
all_weeks = sorted(set([w for (_, w) in spend_by_city_week.keys()]))


# Build per-city series
def series(city, byCityWeek):
    return [byCityWeek.get((city, w), 0) for w in all_weeks]

city_data = {}
for c in CITIES:
    city_data[c] = {
        "spend":   [round(spend_by_city_week.get((c,w), 0), 2) for w in all_weeks],
        "clicks":  [int(clicks_by_city_week.get((c,w), 0)) for w in all_weeks],
        "impr":    [int(impr_by_city_week.get((c,w), 0)) for w in all_weeks],
        "created": [int(created_by_city_week.get((c,w), 0)) for w in all_weeks],
        "won":     [int(won_by_city_week.get((c,w), 0)) for w in all_weeks],
    }

# Overall totals
overall = {
    "spend":   [sum(city_data[c]["spend"][i] for c in CITIES) for i in range(len(all_weeks))],
    "clicks":  [sum(city_data[c]["clicks"][i] for c in CITIES) for i in range(len(all_weeks))],
    "impr":    [sum(city_data[c]["impr"][i] for c in CITIES) for i in range(len(all_weeks))],
    "created": [sum(city_data[c]["created"][i] for c in CITIES) for i in range(len(all_weeks))],
    "won":     [sum(city_data[c]["won"][i] for c in CITIES) for i in range(len(all_weeks))],
}
city_data = {"Overall": overall, **city_data}


UPDATE_DATE = datetime.now(timezone.utc).strftime("%B %-d, %Y")
DATA = {
    "updateDate": UPDATE_DATE,
    "cities":     ["Overall"] + CITIES,
    "weeks":      all_weeks,
    "perCity":    city_data,
    "windowStart": str(min_date),
    "windowEnd":   str(max_date),
}


HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Google Ads Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       background: #f6f7f9; color: #1a2e4a; padding-bottom: 40px; }
.header { background: #fff; border-bottom: 1px solid #e2e8f0; padding: 16px 28px; display: flex; justify-content: space-between; align-items: center; }
.header h1 { font-size: 20px; color: #1e3a5f; }
.header .meta { font-size: 12px; color: #6b7280; }
.back { color: #1e3a5f; text-decoration: none; font-size: 13px; margin-right: 16px; }

.banner { background: #fffbeb; border-bottom: 1px solid #fde68a; padding: 10px 28px; font-size: 12px; color: #92400e; }
.banner b { font-weight: 700; }

.summary { padding: 20px 28px; display: grid; grid-template-columns: repeat(5, 1fr); gap: 14px; max-width: 1800px; margin: 0 auto; }
.kpi { background: #fff; border-radius: 10px; padding: 14px 18px; box-shadow: 0 1px 4px rgba(0,0,0,0.05); }
.kpi .label { font-size: 10px; font-weight: 700; color: #888; text-transform: uppercase; letter-spacing: 0.6px; margin-bottom: 6px; }
.kpi .value { font-size: 24px; font-weight: 700; color: #1a2e4a; }
.kpi .sub { font-size: 11px; color: #888; margin-top: 4px; }

.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; padding: 0 28px 24px; max-width: 1800px; margin: 0 auto; }
@media (max-width: 1100px) { .grid { grid-template-columns: 1fr; } }
.panel { background: #fff; border-radius: 12px; padding: 18px 20px; box-shadow: 0 1px 4px rgba(0,0,0,0.05); }
.panel h3 { font-size: 16px; color: #1e3a5f; margin-bottom: 8px; display: flex; align-items: center; gap: 10px; }
.panel h3 .city-tag { background: #e0f2fe; color: #075985; font-size: 11px; padding: 2px 8px; border-radius: 12px; font-weight: 600; }
.row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 8px; font-size: 12px; }
.row .stat { background: #f8fafc; border-radius: 6px; padding: 8px 10px; }
.row .stat .l { font-size: 10px; color: #888; text-transform: uppercase; letter-spacing: 0.4px; }
.row .stat .v { font-size: 16px; font-weight: 700; color: #1e3a5f; margin-top: 2px; }
.chart-row { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-top: 6px; }
.chart-block .chart-label { font-size: 11px; font-weight: 700; color: #6b7280; text-transform: uppercase; letter-spacing: 0.4px; margin-bottom: 4px; }
.chart-block .chart-wrap { height: 130px; }
footer { text-align: center; font-size: 11px; color: #9ca3af; padding: 20px; }
</style>
</head>
<body>

<div class="header">
  <div>
    <h1>💰 Google Ads Dashboard</h1>
    <div class="meta">Spend, clicks, and HubSpot-attributed deals (lead source = Google Adwords PPC), by city</div>
  </div>
  <div>
    <a class="back" href="index.html">← All reports</a>
    <span class="meta">Last build <span id="update-date">—</span></span>
  </div>
</div>

<div class="banner">
  <b>Reading note:</b> "Created" and "Won" come from HubSpot deals where Primary Lead Source = "Google Adwords PPC".
  These are <i>absolute counts</i> — Pipedrive migration stripped GCLID, so we can't attribute deals to specific campaigns yet.
  CPA shown is city-level: total city spend ÷ city won. Window: <span id="window-range">—</span>.
</div>

<div class="summary" id="summary"></div>
<div class="grid" id="grid"></div>
<footer>Auto-updated daily · Google Ads (campaign-day) + HubSpot deals filtered by lead source</footer>

<script>
const DATA = __DATA_JSON__;
document.getElementById('update-date').textContent = DATA.updateDate;
document.getElementById('window-range').textContent = `${DATA.windowStart} → ${DATA.windowEnd}`;

const fmt = n => (n || 0).toLocaleString();
const fmt$ = n => '$' + (Math.round((n||0) * 100)/100).toLocaleString(undefined, {minimumFractionDigits:2, maximumFractionDigits:2});
const sum = arr => arr.reduce((a,b)=>a+b, 0);

const cityColor = {
  "Overall":      "#1e3a5f",
  "Dallas":       "#1e40af",
  "Houston":      "#0891b2",
  "San Antonio":  "#0d9488",
  "Austin":       "#65a30d",
  "Phoenix":      "#ea580c",
  "Utah":         "#7c3aed",
  "Tucson":       "#db2777",
};

// Summary KPIs from Overall
const o = DATA.perCity['Overall'];
const totalSpend = sum(o.spend);
const totalClicks = sum(o.clicks);
const totalCreated = sum(o.created);
const totalWon = sum(o.won);
const overallCpa = totalWon > 0 ? totalSpend / totalWon : null;
document.getElementById('summary').innerHTML = `
  <div class="kpi"><div class="label">Total Spend</div><div class="value">${fmt$(totalSpend)}</div><div class="sub">across all cities</div></div>
  <div class="kpi"><div class="label">Total Clicks</div><div class="value">${fmt(totalClicks)}</div><div class="sub">Google Ads</div></div>
  <div class="kpi"><div class="label">Deals Created</div><div class="value">${fmt(totalCreated)}</div><div class="sub">Lead Source = Google Adwords PPC</div></div>
  <div class="kpi"><div class="label">Deals Won</div><div class="value">${fmt(totalWon)}</div><div class="sub">in window</div></div>
  <div class="kpi"><div class="label">Effective CPA</div><div class="value">${overallCpa !== null ? fmt$(overallCpa) : '—'}</div><div class="sub">spend ÷ won</div></div>
`;

// Build per-city panels
const grid = document.getElementById('grid');
DATA.cities.forEach(city => {
  const d = DATA.perCity[city];
  const ttlSpend = sum(d.spend), ttlClicks = sum(d.clicks), ttlCreated = sum(d.created), ttlWon = sum(d.won);
  const cpa = ttlWon > 0 ? ttlSpend / ttlWon : null;
  const cpc = ttlClicks > 0 ? ttlSpend / ttlClicks : null;

  const panel = document.createElement('div'); panel.className = 'panel';
  const safeId = city.replace(/\\s+/g,'-').toLowerCase();
  panel.innerHTML = `
    <h3>${city} <span class="city-tag">${city.toUpperCase()}</span></h3>
    <div class="row">
      <div class="stat"><div class="l">Spend</div><div class="v">${fmt$(ttlSpend)}</div></div>
      <div class="stat"><div class="l">Clicks</div><div class="v">${fmt(ttlClicks)}</div></div>
      <div class="stat"><div class="l">Created (PPC)</div><div class="v">${fmt(ttlCreated)}</div></div>
      <div class="stat"><div class="l">Won (PPC)</div><div class="v">${fmt(ttlWon)}</div></div>
    </div>
    <div class="row" style="grid-template-columns: 1fr 1fr">
      <div class="stat"><div class="l">Avg CPC</div><div class="v">${cpc !== null ? fmt$(cpc) : '—'}</div></div>
      <div class="stat"><div class="l">Effective CPA (spend ÷ won)</div><div class="v">${cpa !== null ? fmt$(cpa) : '—'}</div></div>
    </div>
    <div class="chart-row">
      <div class="chart-block"><div class="chart-label">Weekly Spend</div><div class="chart-wrap"><canvas id="${safeId}-spend"></canvas></div></div>
      <div class="chart-block"><div class="chart-label">Created vs Won (PPC deals)</div><div class="chart-wrap"><canvas id="${safeId}-deals"></canvas></div></div>
    </div>`;
  grid.appendChild(panel);

  const color = cityColor[city] || '#666';
  new Chart(document.getElementById(`${safeId}-spend`), {
    type: 'bar',
    data: { labels: DATA.weeks, datasets: [{ label: 'Spend ($)', data: d.spend, backgroundColor: color }] },
    options: { responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: { x: { grid: { display: false }, ticks: { font: { size: 9 } } },
                y: { grid: { color: '#f0f0f0' }, ticks: { font: { size: 9 }, callback: v => '$' + v } } } }
  });
  new Chart(document.getElementById(`${safeId}-deals`), {
    type: 'bar',
    data: { labels: DATA.weeks, datasets: [
      { label: 'Created', data: d.created, backgroundColor: color + '70' },
      { label: 'Won',     data: d.won,     backgroundColor: color },
    ]},
    options: { responsive: true, maintainAspectRatio: false,
      plugins: { legend: { position: 'bottom', labels: { font: { size: 9 }, boxWidth: 10 } } },
      scales: { x: { grid: { display: false }, ticks: { font: { size: 9 } } },
                y: { grid: { color: '#f0f0f0' }, ticks: { font: { size: 9 } } } } }
  });
});
</script>
</body>
</html>
"""

html = HTML.replace("__DATA_JSON__", json.dumps(DATA, default=str))
OUTPUT.write_text(html, encoding="utf-8")
print(f"  ✓ Written: {OUTPUT}  ({len(html)//1024} KB)")
