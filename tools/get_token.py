#!/usr/bin/env python3
r"""One-time YouTube sign-in for the Music-gen-daily upload workflow. Run on Carti's PC:

    pip install google-auth-oauthlib
    python get_token.py                      (asks for the client ID and pastes the secret hidden)
    python get_token.py client_secret.json   (or reads a downloaded client file)

It opens a browser to sign in to the YouTube channel, then stores three GitHub secrets on
ctemenes/Music-gen-daily with the GitHub CLI (gh must be installed and signed in):
YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN. Nothing secret is printed or written to disk.
Scopes: upload, manage videos (edit titles, descriptions, tags, privacy and schedules), read the channel,
read analytics (so the channel tracker needs no second sign-in).
"""
import getpass, json, shutil, subprocess, sys

from google_auth_oauthlib.flow import InstalledAppFlow

REPO = "ctemenes/Music-gen-daily"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.force-ssl",
          "https://www.googleapis.com/auth/youtube.readonly",
          "https://www.googleapis.com/auth/yt-analytics.readonly"]

gh = shutil.which("gh")
if not gh:
    sys.exit("GitHub CLI not found. Install it with: winget install GitHub.cli   then run: gh auth login")
if len(sys.argv) == 2:
    cfg = json.load(open(sys.argv[1], encoding="utf-8"))
else:
    print("Google Cloud > Google Auth Platform > Clients > your Desktop client.")
    cid = input("Client ID: ").strip()
    secret = getpass.getpass("Client secret (paste it, nothing will show): ").strip()
    if not cid or not secret:
        sys.exit("Need both the client ID and the client secret.")
    cfg = {"installed": {"client_id": cid, "client_secret": secret,
                         "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                         "token_uri": "https://oauth2.googleapis.com/token",
                         "redirect_uris": ["http://localhost"]}}
app = cfg.get("installed") or cfg.get("web")
if not app:
    sys.exit("That file is not an OAuth client file.")
flow = InstalledAppFlow.from_client_config(cfg, SCOPES)
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
