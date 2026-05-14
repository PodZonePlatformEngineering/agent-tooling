from __future__ import annotations

"""OAuth2 Gmail wrapper for the onboarding toolkit.

Credentials:
  Client secret : ~/.config/podzone/gmail-client-secret.json
  Refresh token : ~/.config/podzone/gmail-token.json  (written after first consent)
  Scopes        : gmail.compose + gmail.readonly

Run `onboard auth-gmail` once to perform the browser consent flow.
Subsequent calls refresh the token transparently.
"""

import base64
import mimetypes
import pathlib
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.readonly",
]
_CLIENT_SECRET = pathlib.Path.home() / ".config" / "podzone" / "gmail-client-secret.json"
_TOKEN_PATH = pathlib.Path.home() / ".config" / "podzone" / "gmail-token.json"


def _get_service():
    creds = None
    if _TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(_TOKEN_PATH), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            _save_token(creds)
        else:
            raise RuntimeError(
                "Gmail credentials missing or invalid. "
                "Run `onboard auth-gmail` to complete the OAuth consent flow."
            )
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _save_token(creds: Credentials) -> None:
    _TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    _TOKEN_PATH.write_text(creds.to_json())


def run_auth_flow() -> None:
    """Run the one-time browser OAuth consent flow and persist the token."""
    if not _CLIENT_SECRET.exists():
        raise FileNotFoundError(
            f"Gmail client secret not found: {_CLIENT_SECRET}\n"
            "Place the OAuth Desktop App credentials file at that path and retry."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(_CLIENT_SECRET), SCOPES)
    creds = flow.run_local_server(port=0)
    _save_token(creds)
    print(f"Gmail OAuth complete. Token saved to {_TOKEN_PATH}")


def _build_raw_message(
    to: str,
    subject: str,
    body: str,
    attachments: list[tuple[pathlib.Path, str]] | None = None,
) -> dict:
    """Build a MIME multipart message and return the Gmail API raw payload dict."""
    msg = MIMEMultipart("mixed")
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    for path, display_name in (attachments or []):
        content_type, _ = mimetypes.guess_type(str(path))
        maintype, subtype = (content_type or "application/octet-stream").split("/", 1)
        with path.open("rb") as f:
            part = MIMEBase(maintype, subtype)
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", "attachment", filename=display_name)
        msg.attach(part)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    return {"raw": raw}


def create_draft(
    to: str,
    subject: str,
    body: str,
    attachments: list[tuple[pathlib.Path, str]] | None = None,
) -> str:
    """Create a Gmail draft and return its draft ID."""
    service = _get_service()
    message = _build_raw_message(to, subject, body, attachments)
    draft = service.users().drafts().create(
        userId="me", body={"message": message}
    ).execute()
    return draft["id"]


def search_messages(query: str, max_results: int = 10) -> list[dict]:
    """Return a list of message stubs matching the Gmail search query."""
    service = _get_service()
    result = service.users().messages().list(
        userId="me", q=query, maxResults=max_results
    ).execute()
    return result.get("messages", [])


def get_message(msg_id: str) -> dict:
    """Return the full Gmail API message object for the given message ID."""
    service = _get_service()
    return service.users().messages().get(
        userId="me", id=msg_id, format="full"
    ).execute()


def list_labels(max_results: int = 1) -> list[dict]:
    """List Gmail labels — used by auth-check to verify OAuth scope."""
    service = _get_service()
    result = service.users().labels().list(userId="me").execute()
    labels = result.get("labels", [])
    return labels[:max_results]


def extract_body_text(msg: dict) -> str:
    """Recursively extract plain-text body from a Gmail API message."""

    def _collect(parts: list) -> str:
        text = ""
        for part in parts:
            if part.get("mimeType") == "text/plain":
                data = part.get("body", {}).get("data", "")
                if data:
                    # Gmail base64url may be missing padding
                    text += base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
            if "parts" in part:
                text += _collect(part["parts"])
        return text

    payload = msg.get("payload", {})
    if payload.get("mimeType") == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
    return _collect(payload.get("parts", []))
