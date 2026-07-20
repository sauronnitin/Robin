import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# NOTE: We authenticate as a real Google user via OAuth2, not a service account.
# Service accounts on personal (non-Workspace) Google accounts have a hard 0-byte
# Drive storage quota and cannot create any new Doc, Sheet, or uploaded file.
# Files created under a real user's OAuth token count against that user's own
# quota, which is what this pipeline needs (a new resume/cover letter doc per job).
# Full drive scope is required to place files into a user-chosen folder
# (GOOGLE_DRIVE_FOLDER_ID). drive.file alone cannot see/write arbitrary folders.
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive",
]

_TOKEN_PATH = Path(__file__).resolve().parents[3] / "google-oauth-token.json"


def get_credentials():
    client_secret_path = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")
    if not client_secret_path:
        raise EnvironmentError("GOOGLE_OAUTH_CLIENT_SECRET is not set")

    creds = None
    if _TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(_TOKEN_PATH), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(client_secret_path, SCOPES)
            creds = flow.run_local_server(port=0)
        _TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")

    return creds
