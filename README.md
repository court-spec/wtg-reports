# WTG Reports

Weekly pipeline reporting for Wisdom Teeth Guys.

## How it works

```
HubSpot API → sync script → Google Sheet → Looker Studio → wtg-reporting.com
                  ↑
        GitHub Actions runs this weekly (Mondays 6 AM CT)
```

## Setup (one-time)

Required GitHub Secrets (Settings → Secrets and variables → Actions):

| Secret | What it is |
|---|---|
| `HUBSPOT_TOKEN` | HubSpot Private App access token with deals/companies/owners read scope |
| `GOOGLE_SHEET_ID` | ID from the Google Sheet URL (the long string between `/d/` and `/edit`) |
| `GOOGLE_SA_JSON` | Full JSON of the Google Service Account key (paste contents as-is) |

The Service Account email must be **shared on the Google Sheet as Editor**.

## Manual trigger

Go to **Actions → Sync HubSpot to Google Sheets → Run workflow**.

## Run locally for testing

```bash
pip install -r requirements.txt
export HUBSPOT_TOKEN=...
export GOOGLE_SHEET_ID=...
export GOOGLE_SA_JSON="$(cat path/to/service-account.json)"
python scripts/sync_hubspot_deals.py
```
