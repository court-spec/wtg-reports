#!/usr/bin/env python3
"""Probe Dialpad API endpoints to find what lists historical calls."""

import os
import requests

H = {"Authorization": f"Bearer {os.environ['DIALPAD_API_KEY']}", "Accept": "application/json"}
BASE = "https://dialpad.com"

# Endpoints that might list calls / call records
paths = [
    "/api/v2/calls",
    "/api/v1/calls",
    "/api/v2/recordings",
    "/api/v2/callrecords",
    "/api/v2/cdr",
    "/api/v2/call-history",
    "/api/v2/events",
    "/api/v2/userevents",
    "/api/v2/user_events",
    "/api/v2/event_subscriptions",
    "/api/v2/calllogs",
    "/api/v2/call_logs",
    "/api/v2/transcripts",
    "/api/v2/call_recording",
    "/api/v2/stats",
    "/api/v2/userdetails",
    "/api/v2/users",
    "/api/v2/numbers",
    "/api/v2/sms",
]
for p in paths:
    r = requests.get(f"{BASE}{p}", headers=H, params={"limit": 1}, timeout=15)
    body = r.text[:200].replace("\n", " ")
    print(f"  {r.status_code}  GET {p:35s}  {body[:140]}")
