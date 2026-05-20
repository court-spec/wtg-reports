#!/usr/bin/env python3
"""Test if Dialpad's stats endpoint supports export_type=records for individual call records."""

import os, time, requests

H = {"Authorization": f"Bearer {os.environ['DIALPAD_API_KEY']}", "Accept": "application/json"}
BASE = "https://dialpad.com/api/v2"

# Get a center
centers = requests.get(f"{BASE}/callcenters", headers=H, params={"limit": 5}, timeout=20).json().get("items", [])
target = next((c for c in centers if "*Dallas - WTG Main" in c.get("name", "")), centers[0])
cid = str(target["id"])
print(f"Using center: {target['name']} (id={cid})\n", flush=True)

# Try different export_type values
for et in ("records", "calls", "raw", "detail", "details", "individual", "events"):
    payload = {
        "days_ago_start": 3, "days_ago_end": 0,
        "target_id": cid, "target_type": "callcenter",
        "stat_type": "calls", "export_type": et,
        "is_today": False, "timezone": "America/Chicago",
        "coaching_group": False,
    }
    r = requests.post(f"{BASE}/stats", headers={**H, "Content-Type":"application/json"}, json=payload, timeout=20)
    if r.status_code == 200:
        rid = r.json().get("request_id")
        print(f"  ✓ export_type='{et}' accepted, request_id={rid}", flush=True)
        # Poll once
        for _ in range(30):
            time.sleep(2)
            poll = requests.get(f"{BASE}/stats/{rid}", headers=H, timeout=20).json()
            if poll.get("status") == "complete":
                url = poll.get("download_url")
                csv_text = requests.get(url, headers=H, timeout=30).text
                lines = csv_text.split("\n")
                print(f"    First 2 lines:")
                for line in lines[:2]:
                    print(f"      {line[:200]}", flush=True)
                print(f"    Total lines: {len(lines)}", flush=True)
                # Look for caller-number columns
                if lines:
                    header = lines[0].lower()
                    interesting = [c for c in header.split(",") if any(k in c for k in ("caller","number","external","from","phone"))]
                    if interesting:
                        print(f"    🎯 Caller-related columns: {interesting}", flush=True)
                break
    else:
        msg = r.text[:150].replace("\n", " ")
        print(f"  ✗ export_type='{et}': {r.status_code} {msg}", flush=True)
