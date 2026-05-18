"""
Refresh Meta long-lived access token using app credentials.
Long-lived tokens last ~60 days; refresh proactively if older than 30 days.

Outputs the new token to STDOUT (single line) so the workflow can capture
it and update the GitHub secret via gh CLI.
"""
import os, sys, json
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError

def main():
    token = os.environ["META_ACCESS_TOKEN"]
    app_id = os.environ["META_APP_ID"]
    app_secret = os.environ["META_APP_SECRET"]
    params = {
        "grant_type": "fb_exchange_token",
        "client_id": app_id,
        "client_secret": app_secret,
        "fb_exchange_token": token,
    }
    url = "https://graph.facebook.com/v21.0/oauth/access_token?" + urlencode(params)
    try:
        with urlopen(Request(url), timeout=20) as resp:
            data = json.loads(resp.read())
    except HTTPError as e:
        err = e.read().decode("utf-8", errors="ignore")
        print(f"ERROR refreshing token: {err}", file=sys.stderr)
        sys.exit(1)

    new_token = data.get("access_token", "")
    if not new_token or not new_token.startswith("EAA"):
        print(f"Unexpected response: {data}", file=sys.stderr)
        sys.exit(1)

    expires_in = data.get("expires_in", 0)
    print(f"[Meta] Token refreshed. Expires in {expires_in} seconds (~{expires_in/86400:.0f} days)", file=sys.stderr)
    # Print just the token to stdout so gh secret set can capture it
    print(new_token)

if __name__ == "__main__":
    main()
