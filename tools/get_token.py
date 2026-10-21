#!/usr/bin/env python3
r"""One-time YouTube sign-in for the Music-gen-daily upload workflow. Run on Carti's PC:

    pip install google-auth-oauthlib
    python get_token.py client_secret.json

It opens a browser to sign in to the YouTube channel, then stores three GitHub secrets on
ctemenes/Music-gen-daily with the GitHub CLI (gh must be installed and signed in):
YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN. Nothing secret is printed or written to disk.
Scopes: upload, read the channel, read analytics (so the channel tracker needs no second sign-in).
"""
import json, shutil, subprocess, sys

from google_auth_oauthlib.flow import InstalledAppFlow

REPO = "ctemenes/Music-gen-daily"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.readonly",
          "https://www.googleapis.com/auth/yt-analytics.readonly"]

if len(sys.argv) != 2:
    sys.exit("Usage: python get_token.py client_secret.json")
gh = shutil.which("gh")
if not gh:
    sys.exit("GitHub CLI not found. Install it with: winget install GitHub.cli   then run: gh auth login")
cfg = json.load(open(sys.argv[1], encoding="utf-8"))
app = cfg.get("installed") or cfg.get("web")
if not app:
    sys.exit("That file is not an OAuth client file. Download it from Google Cloud: Credentials, your Desktop client, Download JSON.")
flow = InstalledAppFlow.from_client_secrets_file(sys.argv[1], SCOPES)
creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
if not creds.refresh_token:
    sys.exit("Google did not return a refresh token. Remove the app's access at myaccount.google.com/permissions and run this again.")
for name, value in (("YT_CLIENT_ID", app["client_id"]), ("YT_CLIENT_SECRET", app["client_secret"]),
                    ("YT_REFRESH_TOKEN", creds.refresh_token)):
    r = subprocess.run([gh, "secret", "set", name, "-R", REPO], input=value, text=True, capture_output=True)
    if r.returncode != 0:
        sys.exit(f"Could not set {name}: {r.stderr.strip()[:300]}")
    print(f"Saved GitHub secret {name}")
print("Done. Delete client_secret.json from this folder if you like; the workflow no longer needs it.")
