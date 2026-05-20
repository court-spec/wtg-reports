#!/usr/bin/env python3
"""
Backfill migrated Pipedrive deals so deal owner = marketer.

For each migrated deal (has `migrated_record_id`):
  1. Read `migrated_marketer_assigned__dentist_referral` (text name)
  2. Look up that marketer's HubSpot owner ID
  3. If the deal's current `hubspot_owner_id` ≠ that marketer's owner_id:
       a. Copy current owner to `patient_coordinator` property
       b. Set `hubspot_owner_id` to the marketer's owner_id

Usage:
  python3 scripts/backfill_migrated_deal_owners.py            # dry-run, writes CSV
  python3 scripts/backfill_migrated_deal_owners.py --apply    # actually update HubSpot

Env:
  HUBSPOT_TOKEN
"""

import csv
import os
import sys
import time
from collections import Counter
from pathlib import Path

import requests

# Local .env
for line in Path(__file__).resolve().parent.parent.parent.joinpath(".env").read_text().splitlines() \
        if (Path(__file__).resolve().parent.parent.parent / ".env").exists() \
        else Path(".env").read_text().splitlines() if Path(".env").exists() else []:
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

HEADERS = {"Authorization": f"Bearer {os.environ['HUBSPOT_TOKEN']}",
            "Content-Type": "application/json"}
APPLY = "--apply" in sys.argv
OUT_CSV = Path("/Users/courtiorg/Desktop/migrated_deal_owner_backfill.csv")


# ─── Step 1: build marketer name → owner_id map from HubSpot Owners ───
print("Fetching HubSpot owners…")
owners = []
after = None
while True:
    params = {"limit": 100}
    if after: params["after"] = after
    r = requests.get("https://api.hubapi.com/crm/v3/owners", headers=HEADERS, params=params)
    body = r.json()
    owners.extend(body.get("results", []))
    nxt = body.get("paging", {}).get("next")
    if not nxt: break
    after = nxt.get("after")

# Manual overrides for names that don't match cleanly
NAME_OVERRIDES = {
    "Holly Chandler":   "163523470",  # Holly Rose
    "Patricia Kriner":  "163523642",  # Trish Kriner
    "Val Zuniga":       "163523645",  # Valeria Zuniga
}

name_to_owner = {}
for o in owners:
    fn = (o.get("firstName") or "").strip()
    ln = (o.get("lastName") or "").strip()
    em = (o.get("email") or "").strip()
    full = f"{fn} {ln}".strip()
    if full:
        name_to_owner[full.lower()] = o["id"]
    if em:
        name_to_owner[em.lower()] = o["id"]
# Apply overrides
for nm, oid in NAME_OVERRIDES.items():
    name_to_owner[nm.lower()] = oid

import re
def resolve_owner(marketer_text):
    if not marketer_text: return None
    raw = marketer_text.strip()
    # Strip "x-Don't Use-" / "x- Don't Use -" prefixes (deprecated marketer entries)
    cleaned = re.sub(r"^x[-\s]*don[’']?t\s*use[-\s]*", "", raw, flags=re.IGNORECASE).strip()
    if cleaned.lower() == "orphan":
        return None  # genuinely orphaned — skip
    # Exact match
    owner = name_to_owner.get(cleaned.lower())
    if owner: return owner
    # First-name-only fallback (HubSpot owners sometimes lack last name)
    first = cleaned.split()[0].lower() if cleaned else ""
    if first:
        for k, v in name_to_owner.items():
            if k == first or k.startswith(first + " "):
                return v
    return None


# ─── Step 2: pull all migrated deals ───
def pull_migrated_deals():
    """Yield migrated dental/ortho referral deals, chunked by createdate month to bypass the 10K search cap."""
    from datetime import datetime, timezone
    import calendar
    url = "https://api.hubapi.com/crm/v3/objects/deals/search"
    total = 0

    # Build month windows from 2018 → now (Pipedrive history)
    windows = []
    y, m = 2018, 1
    now = datetime.now(timezone.utc)
    while (y, m) <= (now.year, now.month):
        next_y, next_m = (y, m + 1) if m < 12 else (y + 1, 1)
        windows.append((datetime(y, m, 1, tzinfo=timezone.utc),
                         datetime(next_y, next_m, 1, tzinfo=timezone.utc)))
        y, m = next_y, next_m

    def to_ms(dt): return str(int(dt.timestamp() * 1000))

    for w_idx, (start, end) in enumerate(windows, 1):
        after = None
        win_count = 0
        while True:
            payload = {
                "filterGroups": [{"filters": [
                    {"propertyName": "migrated_record_id", "operator": "HAS_PROPERTY"},
                    {"propertyName": "migrated_primary_lead_source",
                     "operator": "IN",
                     "values": ["Dentist Referral", "Orthodontist Referral"]},
                    {"propertyName": "createdate", "operator": "GTE", "value": to_ms(start)},
                    {"propertyName": "createdate", "operator": "LT",  "value": to_ms(end)},
                ]}],
                "properties": ["hubspot_owner_id",
                               "migrated_marketer_assigned__dentist_referral",
                               "patient_coordinator", "migrated_record_id",
                               "migrated_primary_lead_source"],
                "limit": 100,
                "sorts": [{"propertyName": "createdate", "direction": "ASCENDING"}],
            }
            if after: payload["after"] = after
            r = requests.post(url, headers=HEADERS, json=payload, timeout=30)
            if r.status_code == 429:
                time.sleep(2); continue
            body = r.json()
            results = body.get("results", [])
            if not results: break
            total += len(results); win_count += len(results)
            for d in results: yield d
            nxt = body.get("paging", {}).get("next")
            if not nxt: break
            after = nxt.get("after")
            if win_count >= 10000:
                print(f"  ! window {start:%Y-%m} hit 10K cap — may miss some deals in this month")
                break
        if win_count > 0:
            print(f"  window {w_idx:>3d}/{len(windows)} ({start:%Y-%m}): +{win_count} (total {total})")


# ─── Step 3: process + emit CSV ───
print(f"\nScanning migrated deals (dry-run={'NO' if APPLY else 'YES'})…")
rows_out = []
stats = Counter()
unknown_marketers = Counter()
to_update = []  # list of {id, properties} batches

for d in pull_migrated_deals():
    p = d.get("properties", {})
    deal_id = d["id"]
    current_owner = (p.get("hubspot_owner_id") or "").strip()
    marketer_text = (p.get("migrated_marketer_assigned__dentist_referral") or "").strip()
    current_pc = (p.get("patient_coordinator") or "").strip()
    target_owner = resolve_owner(marketer_text)

    # Simple rule: if migrated marketer is known and resolves to a HubSpot owner,
    # set hubspot_owner_id to that. Don't touch patient_coordinator.
    if not marketer_text:
        stats["no_marketer_text"] += 1
        decision = "SKIP (no marketer text)"
    elif not target_owner:
        stats["unknown_marketer"] += 1
        unknown_marketers[marketer_text] += 1
        decision = f"SKIP (no owner_id for '{marketer_text}')"
    elif current_owner == target_owner:
        stats["already_correct"] += 1
        decision = "SKIP (already correct)"
    else:
        stats["set_owner"] += 1
        decision = f"SET owner = {marketer_text}"
        to_update.append({
            "id": deal_id,
            "properties": {"hubspot_owner_id": target_owner}
        })

    rows_out.append({
        "deal_id": deal_id,
        "current_owner_id": current_owner,
        "current_patient_coord": current_pc,
        "marketer_text": marketer_text,
        "target_owner_id": target_owner or "",
        "decision": decision,
    })


# ─── Step 4: write CSV + print summary ───
with open(OUT_CSV, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()) if rows_out else
                                       ["deal_id","current_owner_id","current_patient_coord","marketer_text","target_owner_id","decision"])
    w.writeheader()
    w.writerows(rows_out)
print(f"\nCSV written: {OUT_CSV}  ({len(rows_out):,} rows)")
print(f"\nStats:")
for k, n in stats.most_common():
    print(f"  {k:30s} {n:,}")
print(f"\nTotal proposed updates: {len(to_update):,}")

if unknown_marketers:
    print(f"\nUnknown marketer names (need NAME_OVERRIDES additions):")
    for nm, n in unknown_marketers.most_common(20):
        print(f"  {n:>5}  {nm!r}")


# ─── Step 5: apply via batch update if --apply ───
if APPLY and to_update:
    print(f"\n=== APPLYING {len(to_update)} updates ===")
    url = "https://api.hubapi.com/crm/v3/objects/deals/batch/update"
    for i in range(0, len(to_update), 100):
        chunk = to_update[i:i+100]
        r = requests.post(url, headers=HEADERS, json={"inputs": chunk}, timeout=60)
        if r.status_code in (200, 207):
            print(f"  ✓ batch {i//100 + 1}: {len(chunk)} deals updated")
        else:
            print(f"  ! batch {i//100 + 1} failed: {r.status_code} {r.text[:200]}")
        time.sleep(0.5)  # polite throttle
    print("Done.")
else:
    print(f"\n(Dry-run. Re-run with --apply to push changes to HubSpot.)")
