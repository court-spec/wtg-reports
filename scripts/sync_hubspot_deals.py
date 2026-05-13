#!/usr/bin/env python3
"""
Pull all deals + companies from HubSpot and write to a Google Sheet.
Runs weekly via GitHub Actions.

Required env vars (set as GitHub Secrets):
  HUBSPOT_TOKEN        — HubSpot Private App access token
  GOOGLE_SHEET_ID      — the ID portion of the Google Sheet URL
  GOOGLE_SA_JSON       — full JSON of the Google Service Account key (as a string)
"""

import os
import json
import sys
import time
from datetime import datetime, timezone

import requests
import gspread
from google.oauth2.service_account import Credentials


HUBSPOT_TOKEN   = os.environ["HUBSPOT_TOKEN"]
GOOGLE_SHEET_ID = os.environ["GOOGLE_SHEET_ID"]
GOOGLE_SA_JSON  = os.environ["GOOGLE_SA_JSON"]

HS_BASE = "https://api.hubapi.com"
HEADERS = {
    "Authorization": f"Bearer {HUBSPOT_TOKEN}",
    "Content-Type": "application/json",
}

# Deal properties to pull. Update these names if your HubSpot uses different
# internal property names — find them under Settings → Properties.
DEAL_PROPERTIES = [
    "dealname",
    "createdate",
    "closedate",
    "dealstage",
    "pipeline",
    "amount",
    "hubspot_owner_id",
    # Custom properties — adjust to match your HubSpot field internal names
    "territory",
    "market",
    "marketer_assigned",
    "organization_name",
    "zip_code",
    "general_dentist",
]

COMPANY_PROPERTIES = [
    "name",
    "zip",
    "city",
    "state",
    "territory",
    "market",
]

OWNER_FIELDS_TO_KEEP = ["id", "email", "firstName", "lastName"]


# ─────────────────────────────────────────────────────────────────────────────
# HubSpot fetchers
# ─────────────────────────────────────────────────────────────────────────────

def hs_get_all(endpoint: str, properties: list, associations: list = None):
    """Generic paginator for HubSpot v3 objects (deals, companies, etc)."""
    url = f"{HS_BASE}{endpoint}"
    after = None
    results = []
    while True:
        params = {
            "limit": 100,
            "properties": ",".join(properties),
        }
        if associations:
            params["associations"] = ",".join(associations)
        if after:
            params["after"] = after

        r = requests.get(url, headers=HEADERS, params=params, timeout=60)
        if r.status_code == 429:
            time.sleep(2)
            continue
        r.raise_for_status()
        body = r.json()
        results.extend(body.get("results", []))

        paging = body.get("paging", {}).get("next")
        if not paging:
            break
        after = paging.get("after")
    return results


def fetch_owners():
    """Get all HubSpot owners (users) so we can resolve hubspot_owner_id → name/email."""
    url = f"{HS_BASE}/crm/v3/owners"
    after = None
    out = {}
    while True:
        params = {"limit": 100}
        if after:
            params["after"] = after
        r = requests.get(url, headers=HEADERS, params=params, timeout=60)
        r.raise_for_status()
        body = r.json()
        for o in body.get("results", []):
            out[str(o["id"])] = {
                "email":      o.get("email", ""),
                "first_name": o.get("firstName", ""),
                "last_name":  o.get("lastName", ""),
            }
        paging = body.get("paging", {}).get("next")
        if not paging:
            break
        after = paging.get("after")
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Flatteners
# ─────────────────────────────────────────────────────────────────────────────

def flatten_deals(deals: list, companies_by_id: dict, owners: dict):
    """Flatten deal objects into rows suitable for a sheet."""
    rows = []
    for d in deals:
        props = d.get("properties", {})
        # Find first associated company (if any)
        assoc = d.get("associations", {}).get("companies", {}).get("results", [])
        company_id = assoc[0]["id"] if assoc else None
        company = companies_by_id.get(company_id, {}) if company_id else {}

        owner_id = props.get("hubspot_owner_id", "")
        owner = owners.get(owner_id, {})

        rows.append({
            "deal_id":           d.get("id"),
            "deal_name":         props.get("dealname", ""),
            "create_date":       props.get("createdate", ""),
            "close_date":        props.get("closedate", ""),
            "deal_stage":        props.get("dealstage", ""),
            "pipeline":          props.get("pipeline", ""),
            "amount":            props.get("amount", ""),
            "owner_email":       owner.get("email", ""),
            "owner_name":        f"{owner.get('first_name','')} {owner.get('last_name','')}".strip(),
            "deal_territory":    props.get("territory", ""),
            "deal_market":       props.get("market", ""),
            "marketer_assigned": props.get("marketer_assigned", ""),
            "deal_org_name":     props.get("organization_name", ""),
            "deal_zip":          props.get("zip_code", ""),
            "general_dentist":   props.get("general_dentist", ""),
            "company_id":        company_id or "",
            "company_name":      company.get("name", ""),
            "company_zip":       company.get("zip", ""),
            "company_city":      company.get("city", ""),
            "company_state":     company.get("state", ""),
            "company_territory": company.get("territory", ""),
            "company_market":    company.get("market", ""),
        })
    return rows


def companies_to_rows(companies: list):
    out = []
    for c in companies:
        p = c.get("properties", {})
        out.append({
            "company_id":  c.get("id"),
            "name":        p.get("name", ""),
            "zip":         p.get("zip", ""),
            "city":        p.get("city", ""),
            "state":       p.get("state", ""),
            "territory":   p.get("territory", ""),
            "market":      p.get("market", ""),
        })
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Google Sheets writer
# ─────────────────────────────────────────────────────────────────────────────

def write_tab(sheet, tab_name: str, rows: list):
    """Overwrite a tab with the given rows (list of dicts). Creates the tab if missing."""
    if not rows:
        print(f"  ! no rows for {tab_name}, skipping")
        return

    headers = list(rows[0].keys())
    data = [headers] + [[r.get(h, "") for h in headers] for r in rows]

    try:
        ws = sheet.worksheet(tab_name)
        ws.clear()
    except gspread.WorksheetNotFound:
        ws = sheet.add_worksheet(title=tab_name, rows=len(data) + 10, cols=len(headers))

    ws.update(data, value_input_option="RAW")
    print(f"  ✓ wrote {len(rows)} rows to '{tab_name}'")


def get_sheet():
    creds_dict = json.loads(GOOGLE_SA_JSON)
    creds = Credentials.from_service_account_info(
        creds_dict,
        scopes=["https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"],
    )
    client = gspread.authorize(creds)
    return client.open_by_key(GOOGLE_SHEET_ID)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("Fetching HubSpot owners…")
    owners = fetch_owners()
    print(f"  → {len(owners)} owners")

    print("Fetching HubSpot companies…")
    companies = hs_get_all("/crm/v3/objects/companies", COMPANY_PROPERTIES)
    companies_by_id = {c["id"]: c.get("properties", {}) for c in companies}
    print(f"  → {len(companies)} companies")

    print("Fetching HubSpot deals (with company associations)…")
    deals = hs_get_all(
        "/crm/v3/objects/deals",
        DEAL_PROPERTIES,
        associations=["companies"],
    )
    print(f"  → {len(deals)} deals")

    print("Flattening…")
    deal_rows    = flatten_deals(deals, companies_by_id, owners)
    company_rows = companies_to_rows(companies)
    meta_rows = [{
        "key": "last_synced_utc",
        "value": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }, {
        "key": "deal_count",
        "value": str(len(deal_rows)),
    }, {
        "key": "company_count",
        "value": str(len(company_rows)),
    }]

    print("Writing to Google Sheet…")
    sheet = get_sheet()
    write_tab(sheet, "deals_raw",     deal_rows)
    write_tab(sheet, "companies_raw", company_rows)
    write_tab(sheet, "_meta",         meta_rows)

    print("Done.")


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as e:
        print(f"HTTP error: {e.response.status_code} {e.response.text}", file=sys.stderr)
        sys.exit(1)
