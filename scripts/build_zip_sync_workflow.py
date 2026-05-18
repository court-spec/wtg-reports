#!/usr/bin/env python3
"""
Build a paused HubSpot workflow that syncs zip between a deal and its newly
associated company.

Trigger: list-based on `number_of_associated_companies IS_KNOWN`, with
re-enrollment when that property changes (HubSpot's proven pattern, same as
the existing 'Attach company name from association to deal' workflow).

Action: custom JS that:
  1. Reads deal.postal_code (and falls back to migrated_zip_code / zip_code)
  2. Looks up the associated company's `zip`
  3. Bidirectional fill — whichever side is missing gets the other side's value
  4. Outputs `zip` which the next action writes to deal.postal_code

Created PAUSED so we can test before activating.
"""

import json
import os
import sys
import requests
from pathlib import Path

HUBSPOT_TOKEN = os.environ.get("HUBSPOT_TOKEN")
if not HUBSPOT_TOKEN:
    print("Set HUBSPOT_TOKEN", file=sys.stderr); sys.exit(1)

BASE = "https://api.hubapi.com"
H = {"Authorization": f"Bearer {HUBSPOT_TOKEN}", "Content-Type": "application/json"}
NAME = "[PAUSED] Sync Zip on Company Association (Deal ↔ Company)"

SOURCE_CODE = r"""const hubspot = require('@hubspot/api-client');

exports.main = async (event, callback) => {
  // Pick the best deal-side zip we already have
  const dealPostal = (event.inputFields['postal_code'] || '').trim();
  const migrated  = (event.inputFields['migrated_zip_code'] || '').trim();
  const zipCode   = (event.inputFields['zip_code'] || '').trim();
  let dealZip = dealPostal || migrated || zipCode || '';

  let resultZip = dealZip;

  try {
    const client = new hubspot.Client({ accessToken: process.env.HUBSPOT_ACCESS_TOKEN });
    const dealId = event.object.objectId;

    const assocResp = await client.crm.associations.v4.basicApi.getPage(
      'deals', dealId, 'companies'
    );
    const results = assocResp.results || [];

    for (const assoc of results) {
      const companyId = assoc.toObjectId;
      const co = await client.crm.companies.basicApi.getById(companyId, ['zip']);
      const companyZip = (co.properties.zip || '').trim();

      if (!dealZip && companyZip) {
        // Deal is empty, company has zip → pull company.zip onto deal
        resultZip = companyZip;
      } else if (dealZip && !companyZip) {
        // Deal has zip, company is empty → push deal.zip onto company
        await client.crm.companies.basicApi.update(companyId, {
          properties: { zip: dealZip }
        });
      }
      // If both have values, preserve both (no change either direction)
      // Only process the first associated company (deals can have multiple)
      break;
    }
  } catch (e) {
    // Non-fatal — return whatever we have
  }

  callback({ outputFields: { zip: resultZip } });
};
"""


def build_flow():
    src_filter = lambda prop: {
        "filterBranches": [],
        "filters": [{
            "property": prop,
            "operation": {"operator": "IS_KNOWN", "includeObjectsWithNoValueSet": False, "operationType": "ALL_PROPERTY"},
            "filterType": "PROPERTY",
        }],
        "filterBranchType": "AND", "filterBranchOperator": "AND",
    }
    return {
        "name": NAME,
        "description": (
            "Triggers when a deal gains an associated company (or loses/changes one). "
            "Bidirectionally syncs zip: if the deal has a zip and the company doesn't, "
            "the deal's zip is copied to company.zip. If the company has a zip and the "
            "deal doesn't, the company's zip is copied to deal.postal_code. If both have "
            "values, neither is overwritten. Built via API and kept paused for testing."
        ),
        "isEnabled": False,
        "type": "PLATFORM_FLOW",
        "objectTypeId": "0-3",
        "flowType": "WORKFLOW",
        "startActionId": "1",
        "nextAvailableActionId": "3",
        "timeWindows": [],
        "blockedDates": [],
        "customProperties": {},
        "dataSources": [],
        "crmObjectCreationStatus": "ACTIVE",
        "enrollmentCriteria": {
            "shouldReEnroll": True,
            "listFilterBranch": {
                "filterBranches": [src_filter("number_of_associated_companies")],
                "filters": [],
                "filterBranchType": "OR", "filterBranchOperator": "OR",
            },
            "unEnrollObjectsNotMeetingCriteria": False,
            "reEnrollmentTriggersFilterBranches": [{
                "filterBranches": [],
                "filters": [
                    {"property": "hs_name", "operation": {
                        "operator": "IS_EQUAL_TO", "includeObjectsWithNoValueSet": False,
                        "value": "number_of_associated_companies", "operationType": "STRING"}, "filterType": "PROPERTY"},
                    {"property": "hs_value", "operation": {
                        "operator": "IS_KNOWN", "includeObjectsWithNoValueSet": False,
                        "operationType": "ALL_PROPERTY"}, "filterType": "PROPERTY"},
                ],
                "filterBranchType": "AND", "filterBranchOperator": "AND",
            }],
            "type": "LIST_BASED",
        },
        "actions": [
            {
                "actionId": "1",
                "secretNames": [],
                "sourceCode": SOURCE_CODE,
                "runtime": "NODE20X",
                "inputFields": [
                    {"name": "postal_code", "value": {"propertyName": "postal_code", "type": "OBJECT_PROPERTY"}},
                    {"name": "migrated_zip_code", "value": {"propertyName": "migrated_zip_code", "type": "OBJECT_PROPERTY"}},
                    {"name": "zip_code", "value": {"propertyName": "zip_code", "type": "OBJECT_PROPERTY"}},
                ],
                "outputFields": [{"name": "zip", "type": "STRING"}],
                "connection": {"edgeType": "STANDARD", "nextActionId": "2"},
                "type": "CUSTOM_CODE",
            },
            {
                "actionId": "2",
                "actionTypeVersion": 0,
                "actionTypeId": "0-5",
                "fields": {
                    "property_name": "postal_code",
                    "value": {"actionId": "1", "dataKey": "zip", "type": "FIELD_DATA"},
                },
                "type": "SINGLE_CONNECTION",
            },
        ],
    }


def find_existing(name):
    after = None
    while True:
        p = {"limit": 100}
        if after: p["after"] = after
        r = requests.get(f"{BASE}/automation/v4/flows", headers=H, params=p); r.raise_for_status()
        body = r.json()
        for f in body.get("results", []):
            if f.get("name") == name:
                return f["id"]
        pg = body.get("paging", {}).get("next")
        if not pg: return None
        after = pg.get("after")


def main():
    existing = find_existing(NAME)
    if existing:
        if "--replace" in sys.argv:
            print(f"Deleting existing {existing}…")
            requests.delete(f"{BASE}/automation/v4/flows/{existing}", headers=H)
        else:
            print(f"⊘ Already exists (id={existing}). Use --replace to recreate.")
            return

    flow = build_flow()
    r = requests.post(f"{BASE}/automation/v4/flows", headers=H, json=flow)
    if r.status_code in (200, 201):
        d = r.json()
        print(f"✓ Created '{d['name']}' (id={d['id']}, enabled={d['isEnabled']})")
    else:
        print(f"✗ {r.status_code}  {r.text[:500]}")


if __name__ == "__main__":
    main()
