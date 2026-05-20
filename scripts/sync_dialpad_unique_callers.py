#!/usr/bin/env python3
"""
Sync Dialpad UNIQUE CALLERS (not total calls) by contact center by ISO week.

Writes to the WTG Dialpad Data sheet (separate from the main reporting sheet)
as a `unique_callers_raw` tab plus a `unique_callers_by_market_week` pivot.

Env:
  DIALPAD_API_KEY
  GOOGLE_SA_JSON
  DIALPAD_SHEET_ID   (target sheet — falls back to GOOGLE_SHEET_ID)
"""

import csv
import io
import json
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timezone

import gspread
import requests
from google.oauth2.service_account import Credentials


REQUIRED = ["DIALPAD_API_KEY", "GOOGLE_SA_JSON"]
missing = [k for k in REQUIRED if not os.environ.get(k)]
if missing:
    print(f"ERROR: missing env vars: {missing}", file=sys.stderr)
    sys.exit(1)

SHEET_ID = os.environ.get("DIALPAD_SHEET_ID") or os.environ.get("GOOGLE_SHEET_ID")
if not SHEET_ID:
    print("ERROR: set DIALPAD_SHEET_ID or GOOGLE_SHEET_ID", file=sys.stderr)
    sys.exit(1)

API_KEY = os.environ["DIALPAD_API_KEY"]
BASE = "https://dialpad.com/api/v2"
H = {"Authorization": f"Bearer {API_KEY}", "Accept": "application/json"}

YEAR_START = date(date.today().year, 1, 1)
YEAR_END   = date.today()


def get_paginated(path, params=None):
    params = dict(params or {})
    out = []
    while True:
        r = requests.get(f"{BASE}{path}", headers=H, params=params, timeout=30)
        r.raise_for_status()
        body = r.json()
        out.extend(body.get("items") or body.get("data") or [])
        cur = body.get("cursor")
        if not cur: break
        params["cursor"] = cur
    return out


# 1) List contact centers
print("Fetching contact centers…", flush=True)
centers = get_paginated("/callcenters")
print(f"  → {len(centers)} centers", flush=True)


# 2) Request unique_callers stat per center
def request_stats(target_id, stat_type):
    payload = {
        "days_ago_start": (YEAR_END - YEAR_START).days,
        "days_ago_end":   0,
        "target_id":      str(target_id),
        "target_type":    "callcenter",
        "stat_type":      stat_type,
        "export_type":    "stats",
        "is_today":       False,
        "timezone":       "America/Chicago",
        "coaching_group": False,
    }
    r = requests.post(f"{BASE}/stats", headers={**H, "Content-Type":"application/json"},
                      json=payload, timeout=30)
    if r.status_code >= 400:
        return None, r.text[:500]
    return r.json().get("request_id"), None


def poll_stats(request_id, max_wait_s=120):
    for _ in range(max_wait_s // 2):
        r = requests.get(f"{BASE}/stats/{request_id}", headers=H, timeout=30)
        if r.status_code == 200:
            body = r.json()
            if body.get("status") in ("complete","completed","done"):
                return body
            if body.get("status") == "failed":
                return None
        time.sleep(2)
    return None


def fetch_csv(url):
    r = requests.get(url, headers=H, timeout=60)
    r.raise_for_status()
    return r.text


# Try different stat_type candidates on the first center to find the right one
candidates = ["unique_callers", "unique_caller_count", "unique_users", "unique_caller", "callers_unique"]
print(f"\nTesting stat_type candidates on first center ({centers[0].get('name')})…", flush=True)
working_stat = None
first_id = centers[0]["id"]
for st in candidates:
    rid, err = request_stats(first_id, st)
    if rid:
        body = poll_stats(rid, max_wait_s=60)
        if body and (body.get("file_url") or body.get("download_url")):
            print(f"  ✓ '{st}' works", flush=True)
            working_stat = st
            break
    if err:
        msg = err.replace("\n"," ")[:160]
        print(f"  ✗ '{st}': {msg}", flush=True)

if not working_stat:
    print("\nNo unique-caller stat_type works. Falling back to scanning call records.", flush=True)
    # Could implement /calls endpoint pull here. For now, exit.
    sys.exit(1)


# 3) Collect weekly rows
weekly = []  # {contact_center, iso_year, iso_week, unique_callers}
print(f"\nPulling '{working_stat}' for all {len(centers)} centers…", flush=True)
for c in centers:
    name = c.get("name") or f"Center {c.get('id')}"
    cid = c.get("id")
    if not cid: continue
    rid, err = request_stats(cid, working_stat)
    if not rid:
        print(f"  ! {name}: {err[:120] if err else 'failed'}", flush=True)
        continue
    body = poll_stats(rid)
    if not body: continue
    url = body.get("file_url") or body.get("download_url")
    if not url: continue
    csv_text = fetch_csv(url)
    lines = csv_text.split("\n", 1); csv_content = lines[1] if lines[0].startswith("sep=") else csv_text
    reader = csv.DictReader(io.StringIO(csv_content))
    bucket = Counter()
    for row in reader:
        date_str = row.get("date") or row.get("Date") or row.get("day")
        if not date_str: continue
        try:
            d = datetime.strptime(date_str[:10], "%Y-%m-%d").date()
        except: continue
        val = None
        for k in row:
            if "unique" in k.lower() and ("call" in k.lower() or "user" in k.lower()):
                val = row[k]; break
        if val is None:
            for k in ("calls","count","total"):
                if k in row: val = row[k]; break
        try: n = int(float(val or 0))
        except: n = 0
        iy, iw, _ = d.isocalendar()
        bucket[(iy, iw)] += n
    for (iy, iw), n in sorted(bucket.items()):
        weekly.append({"contact_center": name, "iso_year": iy,
                       "iso_week": iw, "unique_callers": n})
    print(f"  ✓ {name}: {sum(bucket.values())} (weeks: {len(bucket)})", flush=True)


# 4) Write to new sheet
print(f"\nWriting to sheet {SHEET_ID[:12]}…", flush=True)
sa = Credentials.from_service_account_info(
    json.loads(os.environ["GOOGLE_SA_JSON"]),
    scopes=["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"])
sh = gspread.authorize(sa).open_by_key(SHEET_ID)

tab = "unique_callers_raw"
try:
    ws = sh.worksheet(tab); ws.clear()
except gspread.WorksheetNotFound:
    ws = sh.add_worksheet(title=tab, rows=max(len(weekly)+10, 100), cols=6)
if weekly:
    headers = list(weekly[0].keys())
    data = [headers] + [[r.get(h, "") for h in headers] for r in weekly]
    ws.update(data, value_input_option="RAW")
    print(f"  ✓ Wrote {len(weekly)} rows to '{tab}'", flush=True)


# 5) Build market pivot
def to_market(name):
    n = name.lower()
    if '*dallas' in n: return 'Dallas'
    if '*houston' in n: return 'Houston'
    if '*san antonio' in n: return 'San Antonio'
    if '*austin' in n: return 'Austin'
    if '*phoenix' in n: return 'Phoenix'
    if '*utah' in n: return 'Utah'
    if '*tucson' in n: return 'Tucson'
    if 'scheduling office - spanish' in n: return 'Scheduling (Spanish)'
    return None

market_week = defaultdict(int)
for r in weekly:
    m = to_market(r["contact_center"])
    if m:
        market_week[(m, r["iso_week"])] += r["unique_callers"]

if market_week:
    weeks = sorted({w for _, w in market_week})
    ORDER = ['Dallas','Houston','Phoenix','Utah','San Antonio','Austin','Tucson','Scheduling (Spanish)']
    markets = [m for m in ORDER if any((m, w) in market_week for w in weeks)]
    rows = [["Market"] + [f"W{w}" for w in weeks] + ["Total"]]
    totals_col = Counter()
    for m in markets:
        row = [m]; total = 0
        for w in weeks:
            v = market_week.get((m, w), 0)
            row.append(v); total += v; totals_col[w] += v
        row.append(total); rows.append(row)
    grand_total = sum(totals_col.values())
    rows.append(["TOTAL"] + [totals_col[w] for w in weeks] + [grand_total])

    tab2 = "unique_callers_by_market_week"
    try:
        ws2 = sh.worksheet(tab2); ws2.clear()
    except gspread.WorksheetNotFound:
        ws2 = sh.add_worksheet(title=tab2, rows=len(rows)+5, cols=len(rows[0])+1)
    ws2.update(rows, value_input_option="RAW")
    print(f"  ✓ Wrote market pivot to '{tab2}'", flush=True)

print(f"\nSheet: https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit")
print("Done.")
