"""One-time Google OAuth consent for the Drive + Gmail + Calendar connectors.

Preferred path: the **Connect Google** button in the app's Connections panel
(/api/integrations/google/connect) — no restart needed. This CLI script is the
fallback for headless environments and for minting the prod Secret Manager
token. Read-only scopes; the refresh token is a secret — never commit it:

  local:  add GOOGLE_OAUTH_REFRESH_TOKEN=<token> to .env
  prod:   gcloud secrets create GOOGLE_OAUTH_REFRESH_TOKEN --data-file=-

Prereq: an OAuth client (Desktop app) in the GCP project — put its values in
GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET (.env).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.google_oauth import SCOPES  # noqa: E402


def main() -> None:
    client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
    if not client_id or not client_secret:
        sys.exit("Set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET first "
                 "(OAuth client of type 'Desktop app' in your GCP project).")

    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_config(
        {"installed": {"client_id": client_id, "client_secret": client_secret,
                       "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                       "token_uri": "https://oauth2.googleapis.com/token",
                       "redirect_uris": ["http://localhost"]}},
        scopes=SCOPES,
    )
    creds = flow.run_local_server(port=8099, prompt="consent")
    print("\nScopes granted:", ", ".join(creds.scopes or SCOPES))
    print("\nGOOGLE_OAUTH_REFRESH_TOKEN=" + (creds.refresh_token or ""))
    print("\nStore it in .env (local) or Secret Manager (prod). "
          "The app mints short-lived access tokens from it at execution time.")


if __name__ == "__main__":
    main()
