#!/usr/bin/env python3
# Autorisation OAuth Google Drive (à lancer une fois en local)
#
# Prérequis :
# 1. Google Cloud Console → activer "Google Drive API"
# 2. Écran de consentement OAuth (mode test + ton email)
# 3. Identifiants → Client OAuth "Application de bureau"
#
# Usage :
#   python3 -m pip install --user google-auth-oauthlib google-api-python-client
#   export GOOGLE_CLIENT_ID="xxx.apps.googleusercontent.com"
#   export GOOGLE_CLIENT_SECRET="xxx"
#   python3 gdrive_auth.py
#
# Copie le refresh_token dans le secret Kubernetes gdrive-secrets.

import os

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


def main():
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise SystemExit("GOOGLE_CLIENT_ID et GOOGLE_CLIENT_SECRET requis")

    flow = InstalledAppFlow.from_client_config(
        {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        },
        SCOPES,
    )
    creds = flow.run_local_server(port=0, open_browser=True)
    print("\n=== À mettre dans gdrive-secrets ===")
    print(f"GOOGLE_CLIENT_ID={client_id}")
    print(f"GOOGLE_CLIENT_SECRET={client_secret}")
    print(f"GOOGLE_REFRESH_TOKEN={creds.refresh_token}")
    print("====================================\n")


if __name__ == "__main__":
    main()
