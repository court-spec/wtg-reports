#!/usr/bin/env python3
"""
One-time: generate a Google Ads OAuth2 refresh token.

Run this ONCE on your Mac. It will:
  1. Open your browser to a Google authorization page
  2. After you approve, capture the redirect via localhost
  3. Print the refresh token — copy/paste into GitHub Secret GOOGLE_ADS_REFRESH_TOKEN

Usage:
  python3 scripts/generate_google_ads_refresh_token.py

It prompts for client_id + client_secret if not in your local .env.
"""

import http.server
import json
import os
import secrets
import socketserver
import sys
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path


SCOPES = ["https://www.googleapis.com/auth/adwords"]
AUTH_URL = "https://accounts.google.com/o/oauth2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REDIRECT_PORT = 8765
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/oauth2callback"


def load_env():
    """Try .env then prompt."""
    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    parent_env = Path.cwd() / ".env"
    for p in (env_path, parent_env, Path(".env")):
        if p.exists():
            for line in p.read_text().splitlines():
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    cid = os.environ.get("GOOGLE_ADS_CLIENT_ID") or input("Paste GOOGLE_ADS_CLIENT_ID: ").strip()
    sec = os.environ.get("GOOGLE_ADS_CLIENT_SECRET") or input("Paste GOOGLE_ADS_CLIENT_SECRET: ").strip()
    return cid, sec


def main():
    client_id, client_secret = load_env()
    state = secrets.token_urlsafe(16)

    # Build auth URL
    params = {
        "client_id":     client_id,
        "redirect_uri":  REDIRECT_URI,
        "response_type": "code",
        "scope":         " ".join(SCOPES),
        "access_type":   "offline",
        "prompt":        "consent",
        "state":         state,
    }
    auth_url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

    print("\nOpening browser for Google authorization…")
    print("If it doesn't open automatically, paste this URL into your browser:")
    print(f"  {auth_url}\n")
    webbrowser.open(auth_url)

    # Tiny HTTP server to catch the redirect
    code_holder = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args, **kwargs): pass  # quiet
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            if parsed.path == "/oauth2callback":
                if qs.get("state", [""])[0] != state:
                    self.send_response(400); self.end_headers()
                    self.wfile.write(b"State mismatch.")
                    return
                if "code" in qs:
                    code_holder["code"] = qs["code"][0]
                    self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
                    self.wfile.write(b"<h2>Success! You can close this tab.</h2>")
                else:
                    self.send_response(400); self.end_headers()
                    self.wfile.write(f"Error: {qs.get('error', ['unknown'])[0]}".encode())

    with socketserver.TCPServer(("localhost", REDIRECT_PORT), Handler) as httpd:
        print(f"Listening on {REDIRECT_URI} …")
        while "code" not in code_holder:
            httpd.handle_request()

    code = code_holder["code"]
    print("Got authorization code. Exchanging for refresh token…")

    # Exchange code → tokens
    data = urllib.parse.urlencode({
        "code":          code,
        "client_id":     client_id,
        "client_secret": client_secret,
        "redirect_uri":  REDIRECT_URI,
        "grant_type":    "authorization_code",
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=data,
                                  headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req) as r:
        body = json.loads(r.read())

    refresh = body.get("refresh_token")
    if not refresh:
        print("\nERROR: no refresh_token in response. Full body:")
        print(json.dumps(body, indent=2))
        sys.exit(1)

    print("\n" + "=" * 60)
    print("REFRESH TOKEN — copy this into GitHub Secret `GOOGLE_ADS_REFRESH_TOKEN`:")
    print("=" * 60)
    print(refresh)
    print("=" * 60)


if __name__ == "__main__":
    main()
