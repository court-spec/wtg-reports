# WTG Reports — Project Status & Architecture

*Last updated: May 14, 2026*

---

## TL;DR

We built a private subdomain (**wtg-reporting.com**) that hosts WTG's internal reports. It auto-pulls live data from HubSpot every day at 6 AM Central, regenerates the dashboards, and deploys them. Access is gated by Google login (only allowlisted WTG emails). Zero manual work — Court no longer has to email HTML files weekly.

**Live site:** https://wtg-reporting.com (Google login required)

---

## What Lives Where

| Component | Where | Purpose |
|---|---|---|
| **wtg-reporting.com** | Cloudflare (domain purchased through them) | Public-facing subdomain |
| **Cloudflare Pages project** | `referral-wins-by-insurance-type` | Hosts the deployed HTML reports |
| **Cloudflare Zero Trust Access** | Cloudflare dashboard → Zero Trust | Google login gate (allowlist of team emails) |
| **GitHub repo** | https://github.com/court-spec/wtg-reports | All code, build scripts, archived reports |
| **Google Sheet (data store)** | "WTG Reports Data" in Court's Google Drive | HubSpot data lands here daily |
| **GitHub Actions** | Repo → Actions tab | Runs the daily pipeline |

---

## Architecture (the pipeline)

```
┌──────────┐    ┌─────────────────┐    ┌────────────────┐    ┌────────────────────┐
│ HubSpot  │───▶│ Google Sheets   │───▶│ Python builds  │───▶│ Cloudflare Pages   │
│  (API)   │    │ (deals_raw,     │    │ HTML reports   │    │ (wtg-reporting.com)│
│          │    │  companies_raw) │    │                │    │                    │
└──────────┘    └─────────────────┘    └────────────────┘    └────────────────────┘
        ▲                                                              ▲
        │                                                              │
        └───── GitHub Actions cron: every day at 6 AM Central ─────────┘
                                                                       │
                                                          ┌──────────────────┐
                                                          │ Cloudflare Access│
                                                          │ (Google OAuth)   │
                                                          └──────────────────┘
                                                                       │
                                                          Only allowlisted
                                                          emails can view
```

**End-to-end runtime:** ~15 min from cron trigger to deployed site.

---

## Live Reports (currently deployed)

### 🔄 Auto-Updating (rebuilt daily)
1. **Pipeline Action Dashboard** — `/pipeline_dashboard.html`
   - Account-level performance across all 6 territories (AUS, DAL, HOU, PHX, SA, UT)
   - T12M Wins, YoY trends, At Risk / Watch / Stable / Momentum alerts
   - Tier classification (VIP/T1/T2/T3) calculated from annual wins
   - ~5K accounts in the filterable table

2. **Deal Won Time Dashboard** — `/deal_won_time_dashboard.html`
   - Same structure but filtered to won deals only
   - Uses `closed_won_time` as the date for cohorting

### 📁 Archived (point-in-time, preserved forever in `/archive/`)
3. **Slowdown Analysis** (May 4, 2026) — pipeline bottleneck deep dive
4. **Dental/Ortho Referrals (All)** — Apr 2025 – Mar 2026
5. **Dental/Ortho Referrals (Won)** — Apr 2025 – Mar 2026
6. **PPO Mapping Review** — Pipedrive → HubSpot insurance plan reconciliation tool

---

## Repo Structure

```
wtg-reports/
├── .github/workflows/
│   └── sync.yml              # GitHub Actions cron + deploy pipeline
├── archive/                  # Point-in-time reports (never auto-overwritten)
│   ├── slowdown_analysis_2026-05-04.html
│   ├── dental_ortho_referrals_apr2025_mar2026.html
│   ├── dental_ortho_referrals_won_apr2025_mar2026.html
│   └── ppo_mapping_review.html
├── scripts/
│   ├── build_pipeline_dashboard.py    # 1500-line: builds Pipeline Action HTML
│   ├── build_deal_won_dashboard.py    # 1500-line: builds Deal Won Time HTML
│   └── build_index.py                 # Landing page + copies archive/ to out/
├── sync_hubspot_deals.py     # Pulls HubSpot → Google Sheet
├── requirements.txt
├── README.md
└── PROJECT_STATUS.md         # This file
```

---

## Data Model

### HubSpot → Google Sheet
The sync pulls all deals created since **Jan 1, 2024** plus all companies. Data lands in two tabs:

**`deals_raw`** (one row per deal):
- `deal_id`, `pipeline`, `deal_stage`, `create_date`, `close_date`, `won_time`
- `territory` (HubSpot custom property `market`)
- `marketer_assigned` (currently `migrated_marketer_assigned__dentist_referral`)
- `general_dentist`, `deal_zip` (currently `migrated_zip_code`)
- `company_id` (foreign key)

**`companies_raw`** (one row per company):
- `company_id`, `name`, `zip`
- `market` (broad: "Dallas", "Utah")
- `territory` (fine: "Dallas SW", "Utah South" — from HubSpot's `market2` property)

### Why `migrated_*` prefixes?
Pipedrive migration put the data into `migrated_<fieldname>` properties. HubSpot's automated workflows will eventually populate the clean property names (`zip_code`, `marketer_assigned`, etc.) but as of May 2026 those workflows aren't fully running yet. **Issue tracked: https://github.com/court-spec/wtg-reports/issues/1** — switch to clean property names in June when workflows are active.

---

## Calculated Logic (in build scripts)

**Tier** — based on annual wins:
- 🏆 VIP = 20+ wins/year
- 🥇 Tier 1 = 11–20
- 🥈 Tier 2 = 5–10
- 🥉 Tier 3 = <5

**Alert** — rolling 2-month YoY (compares last 2 complete months vs same months prior year):
- 🔴 At Risk: down ≥25%
- 🟡 Watch: down 10–24%
- 🔵 Stable: -10% to +15%
- 🟢 Momentum: up ≥15%

**T12M** — trailing 12 months from today (dynamic)

---

## Secrets (stored in GitHub Actions Secrets)

| Secret name | What it is |
|---|---|
| `HUBSPOT_TOKEN` | HubSpot Private App token (read deals, companies, owners) |
| `GOOGLE_SHEET_ID` | ID of "WTG Reports Data" sheet |
| `GOOGLE_SA_JSON` | Google Service Account JSON for writing to the sheet |
| `CLOUDFLARE_API_TOKEN` | Cloudflare Pages: Edit |
| `CLOUDFLARE_ACCOUNT_ID` | Court@wisdomteethguys.com Cloudflare account ID |
| `SPOTIO_API` / `SPOTIO_API_SECRET` | Spotio API secret (for territory visit data) |
| `SPOTIO_CLIENT_ID` | Spotio client ID, paired with the API secret |
| `PIPEDRIVE_API` | Pipedrive API token (for cross-checking migration completeness) |

---

## How to Run Things

### Trigger an on-demand rebuild
1. Go to **https://github.com/court-spec/wtg-reports/actions**
2. Click **"Sync HubSpot to Google Sheets + Build Dashboards"** (left sidebar)
3. Click **Run workflow** (top right) → green Run workflow button
4. Wait ~15 min, then refresh wtg-reporting.com

### Add a new archived report (e.g., a one-off analysis)
1. Drop the HTML file into the `archive/` folder of the repo
2. Commit and push (or upload via GitHub web UI)
3. Next deploy will include it automatically with a landing page card
4. Add a label/description for it in `scripts/build_index.py` → `ARCHIVE_LABELS` dict

### Add a new user to wtg-reporting.com access
1. Cloudflare dashboard → **Zero Trust** → **Access** → **Applications**
2. Click on **WTG Reports** application
3. Edit the policy → add the new email
4. Save (takes effect immediately)

### Update HubSpot property mappings
- Edit `sync_hubspot_deals.py` → `DEAL_PROPERTIES` or `COMPANY_PROPERTIES` lists
- Update the corresponding column in `flatten_deals()` or `companies_to_rows()`
- Commit, push, manually trigger the workflow

---

## Spotio 2.0 API (auth working, endpoint discovery in progress)

**Base URL**: `https://api.spotio2.com`
**Docs**: https://developer.spotio2.com (Stoplight-hosted, SPA)

**Auth flow** (working — `scripts/spotio_auth.py`):
1. POST `https://api.spotio2.com/api/users/apitoken` with JSON `{"clientId": "...", "secret": "..."}`
2. Response: `{"accessToken": "<JWT>"}`
3. Use as `Authorization: Bearer <JWT>` on subsequent calls
4. JWT lifetime ≈ 30 days; the helper caches to `.spotio_token_cache.json` and auto-refreshes

**Endpoint inventory** (discovered May 15, 2026):
- `/api/users/apitoken` — auth ✅
- `/api/v2/activities` — POST, requires Bearer; **request body format TBD** (returns 404 referencing internal `spotio-dataobjects` route)
- `/api/auth/token` — alternate auth path (415 = wrong content-type); not used

**Use case**: pull last-visit timestamps per dental office to correlate with HubSpot referral drop-offs.

---

## In Progress (next up)

**Territory snapshot dashboards** (one per territory):
- Blend office visits (Spotio API) + marketer activity + weekly referral/close metrics
- One HTML per territory: AUS, DAL, HOU, PHX, SA, UT
- Linked from main landing page under "Territory Snapshots" section
- Same daily rebuild cycle
- **Blocker**: confirming Spotio API endpoint + auth method

---

## Known TODOs

1. **June 2026**: Switch from `migrated_*` HubSpot properties to clean ones — see GitHub issue #1
2. **Node.js 20 deprecation** — GitHub Actions will require Node 24 by Sept 16, 2026. Update workflow actions to support Node 24.
3. **Optimization**: 109K deals × 2 build scripts = some redundant Sheet reads. Could load once and pass to both.

---

## Quick Links

- **Site**: https://wtg-reporting.com
- **GitHub repo**: https://github.com/court-spec/wtg-reports
- **GitHub Actions**: https://github.com/court-spec/wtg-reports/actions
- **GitHub Issues**: https://github.com/court-spec/wtg-reports/issues
- **Google Sheet**: `WTG Reports Data` (in Court's Drive)
- **Cloudflare project**: `referral-wins-by-insurance-type`

---

## How to "Catch Up" Another Claude

> "Hey Claude — read `PROJECT_STATUS.md` at the root of the wtg-reports repo. Court (or her CEO) has a request about the WTG reporting pipeline."

That file is the source of truth for this project's architecture. Anything not described there is new ground.
