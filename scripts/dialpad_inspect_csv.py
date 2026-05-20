#!/usr/bin/env python3
"""One-off: inspect what columns Dialpad's stats CSV returns for stat_type=calls."""

import csv
import io
import os
import sys
import time
from datetime import date

import requests


API_KEY = os.environ["DIALPAD_API_KEY"]
BASE = "https://dialpad.com/api/v2"
H = {"Authorization": f"Bearer {API_KEY}", "Accept": "application/json"}


# Pick one busy center
r = requests.get(f"{BASE}/callcenters", headers=H, params={"limit": 100}, timeout=30)
centers = r.json().get("items", [])
target = next((c for c in centers if "*Dallas - WTG Main" in c.get("name", "")), centers[0])
print(f"Inspecting: {target['name']} (id={target['id']})", flush=True)

# Request 7 days of stats
payload = {
    "days_ago_start": 7,
    "days_ago_end":   0,
    "target_id":      str(target["id"]),
    "target_type":    "callcenter",
    "stat_type":      "calls",
    "export_type":    "stats",
    "is_today":       False,
    "timezone":       "America/Chicago",
    "coaching_group": False,
}
r = requests.post(f"{BASE}/stats", headers={**H, "Content-Type": "application/json"},
                  json=payload, timeout=30)
print(f"POST /stats: {r.status_code} {r.text[:200]}", flush=True)
rid = r.json().get("request_id")
if not rid: sys.exit(1)

# Poll — dump every response so we can see what status values Dialpad uses
print(f"\n=== Polling /stats/{rid} ===", flush=True)
import json as _json
last_body = None
for i in range(60):
    time.sleep(2)
    raw = requests.get(f"{BASE}/stats/{rid}", headers=H, timeout=30)
    print(f"  poll {i+1}: HTTP {raw.status_code}  body={raw.text[:300]}", flush=True)
    if raw.status_code == 200:
        last_body = raw.json()
        # Stop when we have a download URL OR see status that looks final
        s = last_body.get("status", "")
        if last_body.get("file_url") or last_body.get("download_url") or last_body.get("url"):
            break
        if s in ("complete","completed","done","ready","failed","error"):
            break

print(f"\nFinal poll body:")
print(_json.dumps(last_body, indent=2) if last_body else "(none)")
url = (last_body or {}).get("file_url") or (last_body or {}).get("download_url") or (last_body or {}).get("url")
if not url:
    print("\nNo file URL found in poll response. Listing all keys:")
    for k in (last_body or {}).keys(): print(f"  - {k}")
    sys.exit(1)

print(f"\nDownloading CSV from {url[:80]}…", flush=True)
csv_text = requests.get(url, headers=H, timeout=60).text
print(f"CSV size: {len(csv_text)} chars", flush=True)
print(f"\n=== First 3000 chars of CSV ===")
print(csv_text[:3000])
print(f"\n=== Headers detected ===")
reader = csv.DictReader(io.StringIO(csv_text))
print(reader.fieldnames)
print(f"\n=== Sample row ===")
for row in reader:
    for k, v in row.items():
        print(f"  {k}: {v}")
    break

# Also: try the "Stats" API valid enum discovery via a deliberately bogus stat_type
print(f"\n=== Probe for valid stat_type enum values ===")
r = requests.post(f"{BASE}/stats", headers={**H, "Content-Type": "application/json"},
                  json={**payload, "stat_type": "_invalid_value_here_"}, timeout=30)
print(f"Bogus stat_type response ({r.status_code}):")
print(r.text[:2000])
