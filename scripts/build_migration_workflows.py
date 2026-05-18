#!/usr/bin/env python3
"""
Build 6 HubSpot migration workflows (one per city) that move deals from the
Pipedrive-suffixed pipelines into the clean pipelines. All workflows are
created PAUSED (isEnabled=false) so they don't run until manually activated.

Run once:
    python3 scripts/build_migration_workflows.py

Re-running creates duplicate workflows — use --replace to delete existing
ones with matching names first.

Required env var:
    HUBSPOT_TOKEN  — HubSpot Private App token with workflow write access
"""

import json
import os
import sys
from pathlib import Path

import requests


HUBSPOT_TOKEN = os.environ.get("HUBSPOT_TOKEN")
if not HUBSPOT_TOKEN:
    print("Set HUBSPOT_TOKEN env var.", file=sys.stderr)
    sys.exit(1)

BASE = "https://api.hubapi.com"
H = {"Authorization": f"Bearer {HUBSPOT_TOKEN}", "Content-Type": "application/json"}

MAPPING_FILE = Path(__file__).resolve().parent.parent / "PIPELINE_CONSOLIDATION_MAPPING.json"
WORKFLOW_NAME_FMT = "[PAUSED] Migrate {city} Pipedrive → Clean Pipeline"


def build_flow(pair: dict) -> dict:
    """Construct the HubSpot flow JSON for a single city pair."""
    city = pair["city"]
    src_pipeline = pair["src_pipeline_id"]
    stages = pair["stages"]

    # actionId 1 is the LIST_BRANCH; subsequent IDs are the SET-property actions
    branches = []
    set_actions = []
    next_id = 2
    for s in stages:
        aid = str(next_id)
        next_id += 1
        branches.append({
            "filterBranch": {
                "filterBranches": [{
                    "filterBranches": [],
                    "filters": [{
                        "property": "dealstage",
                        "operation": {
                            "operator": "IS_ANY_OF",
                            "includeObjectsWithNoValueSet": False,
                            "values": [s["src_stage_id"]],
                            "operationType": "ENUMERATION",
                        },
                        "filterType": "PROPERTY",
                    }],
                    "filterBranchType": "AND",
                    "filterBranchOperator": "AND",
                }],
                "filters": [],
                "filterBranchType": "OR",
                "filterBranchOperator": "OR",
            },
            "branchName": s["src_stage_label"],
            "connection": {"edgeType": "STANDARD", "nextActionId": aid},
        })
        set_actions.append({
            "actionId": aid,
            "actionTypeVersion": 0,
            "actionTypeId": "0-5",
            "fields": {
                "property_name": "dealstage",
                "value": {"staticValue": s["dst_stage_id"], "type": "STATIC_VALUE"},
            },
            "type": "SINGLE_CONNECTION",
        })

    return {
        "name": WORKFLOW_NAME_FMT.format(city=city),
        "description": (
            f"Daily migration: when a deal lands in the {city} - Wisdom Teeth Guys "
            f"Pipedrive pipeline ({src_pipeline}), route it to the corresponding stage "
            f"in the clean {city} - Wisdom Teeth Guys pipeline ({pair['dst_pipeline_id']}). "
            f"Re-enrolls on pipeline change. Built {city.lower()}-{src_pipeline} via API; "
            f"keep paused until ready to cut over."
        ),
        "isEnabled": False,
        "type": "PLATFORM_FLOW",
        "objectTypeId": "0-3",
        "flowType": "WORKFLOW",
        "startActionId": "1",
        "nextAvailableActionId": str(next_id),
        "timeWindows": [],
        "blockedDates": [],
        "customProperties": {},
        "dataSources": [],
        "crmObjectCreationStatus": "ACTIVE",
        "enrollmentCriteria": {
            "shouldReEnroll": True,
            "listFilterBranch": {
                "filterBranches": [{
                    "filterBranches": [],
                    "filters": [{
                        "property": "pipeline",
                        "operation": {
                            "operator": "IS_ANY_OF",
                            "includeObjectsWithNoValueSet": False,
                            "values": [src_pipeline],
                            "operationType": "ENUMERATION",
                        },
                        "filterType": "PROPERTY",
                    }],
                    "filterBranchType": "AND",
                    "filterBranchOperator": "AND",
                }],
                "filters": [],
                "filterBranchType": "OR",
                "filterBranchOperator": "OR",
            },
            "unEnrollObjectsNotMeetingCriteria": False,
            "reEnrollmentTriggersFilterBranches": [{
                "filterBranches": [],
                "filters": [
                    {"property": "hs_name", "operation": {
                        "operator": "IS_EQUAL_TO", "includeObjectsWithNoValueSet": False,
                        "value": "pipeline", "operationType": "STRING"}, "filterType": "PROPERTY"},
                    {"property": "hs_value", "operation": {
                        "operator": "IS_ANY_OF", "includeObjectsWithNoValueSet": False,
                        "values": [src_pipeline], "operationType": "ENUMERATION"}, "filterType": "PROPERTY"},
                ],
                "filterBranchType": "AND",
                "filterBranchOperator": "AND",
            }],
            "type": "LIST_BASED",
        },
        "actions": [{
            "actionId": "1",
            "listBranches": branches,
            "type": "LIST_BRANCH",
        }] + set_actions,
    }


def find_existing_by_name(name: str):
    """Return existing flow ID if a workflow with this name already exists."""
    after = None
    while True:
        params = {"limit": 100}
        if after:
            params["after"] = after
        r = requests.get(f"{BASE}/automation/v4/flows", headers=H, params=params)
        r.raise_for_status()
        body = r.json()
        for f in body.get("results", []):
            if f.get("name") == name:
                return f["id"]
        paging = body.get("paging", {}).get("next")
        if not paging:
            return None
        after = paging.get("after")


def main():
    mapping = json.loads(MAPPING_FILE.read_text())
    replace = "--replace" in sys.argv

    print(f"Building {len(mapping)} migration workflows (PAUSED)…\n")
    for pair in mapping:
        name = WORKFLOW_NAME_FMT.format(city=pair["city"])
        existing = find_existing_by_name(name)
        if existing:
            if replace:
                print(f"  Deleting existing '{name}' (id={existing})…")
                requests.delete(f"{BASE}/automation/v4/flows/{existing}", headers=H)
            else:
                print(f"  ⊘ Skipping '{name}' — already exists (id={existing}). Use --replace to recreate.")
                continue

        flow = build_flow(pair)
        r = requests.post(f"{BASE}/automation/v4/flows", headers=H, json=flow)
        if r.status_code in (200, 201):
            data = r.json()
            print(f"  ✓ Created '{name}' (id={data['id']}, enabled={data['isEnabled']})")
        else:
            print(f"  ✗ Failed '{name}': {r.status_code}  {r.text[:300]}")

    print(f"\nAll workflows are PAUSED. Review in HubSpot → Automation → Workflows.")


if __name__ == "__main__":
    main()
