#!/usr/bin/env python3
"""
Sync Google Ads campaign performance into the existing Google Sheet.

Pulls daily campaign-level metrics (impressions, clicks, cost, conversions)
for the last 180 days and writes to a `google_ads_raw` tab.

Env vars (GitHub Secrets):
  GOOGLE_ADS_DEVELOPER_TOKEN
  GOOGLE_ADS_CLIENT_ID
  GOOGLE_ADS_CLIENT_SECRET
  GOOGLE_ADS_REFRESH_TOKEN
  GOOGLE_ADS_LOGIN_CUSTOMER_ID    (MCC, no dashes — e.g. 4327741431)
  GOOGLE_SHEET_ID
  GOOGLE_SA_JSON

Operating Customer ID (the WTG Google Ads account) is hardcoded below.
"""

import json
import os
import sys
from datetime import datetime, timezone

import gspread
from google.oauth2.service_account import Credentials

try:
    from google.ads.googleads.client import GoogleAdsClient
except ImportError:
    print("Installing google-ads library…", flush=True)
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "google-ads>=24"])
    from google.ads.googleads.client import GoogleAdsClient


# WTG operating account (hardcoded — not sensitive, just an account ID)
OPERATING_CUSTOMER_ID = "8784869120"

# Required secrets
REQUIRED = [
    "GOOGLE_ADS_DEVELOPER_TOKEN",
    "GOOGLE_ADS_CLIENT_ID",
    "GOOGLE_ADS_CLIENT_SECRET",
    "GOOGLE_ADS_REFRESH_TOKEN",
    "GOOGLE_ADS_LOGIN_CUSTOMER_ID",
    "GOOGLE_SHEET_ID",
    "GOOGLE_SA_JSON",
]
missing = [k for k in REQUIRED if not os.environ.get(k)]
if missing:
    print(f"ERROR: missing env vars: {missing}", file=sys.stderr)
    sys.exit(1)


# ─── Google Ads client (uses dict-based config; no yaml file needed) ───
ads_config = {
    "developer_token":     os.environ["GOOGLE_ADS_DEVELOPER_TOKEN"],
    "client_id":           os.environ["GOOGLE_ADS_CLIENT_ID"],
    "client_secret":       os.environ["GOOGLE_ADS_CLIENT_SECRET"],
    "refresh_token":       os.environ["GOOGLE_ADS_REFRESH_TOKEN"],
    "login_customer_id":   os.environ["GOOGLE_ADS_LOGIN_CUSTOMER_ID"],
    "use_proto_plus":      True,
}
print("Initializing Google Ads client…", flush=True)
client = GoogleAdsClient.load_from_dict(ads_config)
ga_service = client.get_service("GoogleAdsService")


# ─── Query: daily campaign performance for last ~2 years (for YoY comparisons) ───
from datetime import date, timedelta
_end = date.today()
_start = _end - timedelta(days=730)
QUERY = f"""
SELECT
    campaign.id,
    campaign.name,
    campaign.status,
    campaign.advertising_channel_type,
    segments.date,
    metrics.impressions,
    metrics.clicks,
    metrics.cost_micros,
    metrics.conversions,
    metrics.conversions_value,
    metrics.ctr,
    metrics.average_cpc
FROM campaign
WHERE segments.date BETWEEN '{_start.isoformat()}' AND '{_end.isoformat()}'
"""

print(f"Querying Google Ads (customer {OPERATING_CUSTOMER_ID})…", flush=True)
rows = []
stream = ga_service.search_stream(customer_id=OPERATING_CUSTOMER_ID, query=QUERY)
for batch in stream:
    for r in batch.results:
        rows.append({
            "date":            r.segments.date,
            "campaign_id":     r.campaign.id,
            "campaign_name":   r.campaign.name,
            "campaign_status": str(r.campaign.status).split(".")[-1],
            "channel_type":    str(r.campaign.advertising_channel_type).split(".")[-1],
            "impressions":     int(r.metrics.impressions),
            "clicks":          int(r.metrics.clicks),
            "cost_usd":        round(r.metrics.cost_micros / 1_000_000, 2),
            "conversions":     round(r.metrics.conversions, 2),
            "conv_value_usd":  round(r.metrics.conversions_value, 2),
            "ctr":             round(r.metrics.ctr * 100, 2),  # convert to %
            "avg_cpc_usd":     round(r.metrics.average_cpc / 1_000_000, 2),
        })
print(f"  → {len(rows)} campaign-day rows", flush=True)


# ─── Write to Google Sheet ───
print("Writing to Google Sheet…", flush=True)
sa = Credentials.from_service_account_info(
    json.loads(os.environ["GOOGLE_SA_JSON"]),
    scopes=["https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"],
)
sh = gspread.authorize(sa).open_by_key(os.environ["GOOGLE_SHEET_ID"])

tab_name = "google_ads_raw"
try:
    ws = sh.worksheet(tab_name)
    ws.clear()
except gspread.WorksheetNotFound:
    ws = sh.add_worksheet(title=tab_name, rows=max(len(rows) + 10, 100), cols=15)

if rows:
    headers = list(rows[0].keys())
    data = [headers] + [[r.get(h, "") for h in headers] for r in rows]
    ws.update(data, value_input_option="RAW")
    print(f"  ✓ wrote {len(rows)} rows to '{tab_name}'", flush=True)
else:
    ws.update([["date", "(no rows returned)"]], value_input_option="RAW")
    print("  ! no rows returned from Google Ads", flush=True)

# Update meta
try:
    meta_ws = sh.worksheet("_meta")
    meta = meta_ws.get_all_records()
    # Append a "google_ads_last_synced" row
    found = False
    for i, row in enumerate(meta):
        if row.get("key") == "google_ads_last_synced_utc":
            meta_ws.update_cell(i + 2, 2, datetime.now(timezone.utc).isoformat(timespec="seconds"))
            found = True
            break
    if not found:
        meta_ws.append_row(["google_ads_last_synced_utc",
                             datetime.now(timezone.utc).isoformat(timespec="seconds")])
except Exception as e:
    print(f"  (couldn't update _meta tab: {e})", flush=True)

print("Done.")
