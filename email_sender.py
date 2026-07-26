"""
Email Sender for 2-Source UrbanPiper Reports
"""

import base64
import os
import tempfile
import logging
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders
import email.policy

import config

log = logging.getLogger(__name__)

_SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
_GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/drive.file",
]

# Gmail API has a 25MB limit on the total request size including base64 encoding.
SIZE_LIMIT_BYTES = 15 * 1024 * 1024   # 15 MB

def _get_credentials():
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
    except ImportError:
        raise ImportError("Run: pip install google-auth google-auth-oauthlib google-api-python-client")

    creds_json_str = os.environ.get("GMAIL_CREDENTIALS", "")
    token_json_str = os.environ.get("GMAIL_TOKEN", "")

    if creds_json_str and token_json_str:
        log.info("Loading credentials from environment variables.")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tf:
            tf.write(token_json_str)
            token_tmp = tf.name
        try:
            creds = Credentials.from_authorized_user_file(token_tmp, _GMAIL_SCOPES)
            if creds.expired and creds.refresh_token:
                log.info("Refreshing expired token...")
                creds.refresh(Request())
        finally:
            os.unlink(token_tmp)
    else:
        token_path = os.path.join(_SCRIPT_DIR, "token.json")
        if not os.path.exists(token_path):
            raise FileNotFoundError("Gmail not authorized. Run: python authorize_gmail.py (or copy token.json from original repo)")
        log.info("Loading credentials from local token.json.")
        creds = Credentials.from_authorized_user_file(token_path, _GMAIL_SCOPES)
        if creds.expired and creds.refresh_token:
            log.info("Refreshing expired token...")
            creds.refresh(Request())
            with open(token_path, "w") as f:
                f.write(creds.to_json())

    return creds


def _get_or_create_drive_folder(drive, folder_name):
    q = (
        f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder'"
        " and trashed=false"
    )
    results = drive.files().list(q=q, fields="files(id,name)").execute()
    files = results.get("files", [])
    if files:
        return files[0]["id"]
    folder = drive.files().create(
        body={"name": folder_name, "mimeType": "application/vnd.google-apps.folder"},
        fields="id"
    ).execute()
    log.info(f"Created Drive folder: {folder_name}")
    return folder["id"]

def _cleanup_old_drive_files(drive, folder_id, days_to_keep=90):
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days_to_keep)).isoformat()
    q = f"'{folder_id}' in parents and trashed=false and createdTime < '{cutoff}'"
    results = drive.files().list(q=q, fields="files(id,name,createdTime)").execute()
    old_files = results.get("files", [])
    for f in old_files:
        drive.files().delete(fileId=f["id"]).execute()
        log.info(f"Deleted old Drive file: {f['name']} (created {f['createdTime']})")

def _upload_to_drive(creds, csv_bytes, filename):
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseUpload
    import io

    drive     = build("drive", "v3", credentials=creds)
    folder_id = _get_or_create_drive_folder(drive, "UrbanPiper Reports")
    _cleanup_old_drive_files(drive, folder_id, days_to_keep=90)

    media = MediaIoBaseUpload(
        io.BytesIO(csv_bytes),
        mimetype="text/csv",
        resumable=True,
    )
    uploaded = drive.files().create(
        body={"name": filename, "parents": [folder_id]},
        media_body=media,
        fields="id,webViewLink"
    ).execute()

    drive.permissions().create(
        fileId=uploaded["id"],
        body={"type": "anyone", "role": "reader"},
    ).execute()

    link = uploaded["webViewLink"]
    log.info(f"Uploaded to Drive: {filename} -> {link}")
    return link


def send_reports_email(reports_data, date_str, is_test=False):
    """
    reports_data: list of tuples (filename, csv_bytes)
    """
    to_emails = config.REPORT_EMAILS
    if not to_emails:
        log.error("No recipient emails configured in REPORT_EMAILS.")
        return

    from googleapiclient.discovery import build
    creds = _get_credentials()
    gmail = build("gmail", "v1", credentials=creds)

    msg = MIMEMultipart()
    to_header = ", ".join(to_emails)
    msg["To"] = to_header
    
    subject_prefix = "[TEST] " if is_test else ""
    msg["Subject"] = f"{subject_prefix}UrbanPiper Daily Reports - {date_str}"

    body_html = f"""
    <html><body style='margin:0;padding:16px;background:#fff'>
    <p style='font-family:Arial;font-size:14px'>Hi Team,</p>
    <p style='font-family:Arial;font-size:14px'>
    Please find the attached UrbanPiper Reports for <b>{date_str}</b>.
    </p>
    """

    for filename, csv_bytes in reports_data:
        size = len(csv_bytes)
        if size >= SIZE_LIMIT_BYTES:
            log.info(f"{filename} exceeds {SIZE_LIMIT_BYTES} bytes. Uploading to Drive...")
            link = _upload_to_drive(creds, csv_bytes, filename)
            body_html += f"""
            <p style='font-family:Arial;font-size:14px'>
            <b>{filename}</b> ({size / 1024 / 1024:.1f} MB) is too large for email.<br>
            Download it from Google Drive: <a href='{link}'>{link}</a>
            </p>
            """
        else:
            log.info(f"Attaching {filename} directly ({size / 1024 / 1024:.1f} MB).")
            attachment = MIMEBase("text", "csv")
            attachment.set_payload(csv_bytes)
            encoders.encode_base64(attachment)
            attachment.add_header("Content-Disposition", f'attachment; filename="{filename}"')
            msg.attach(attachment)

    body_html += """
    <p style='font-family:Arial;font-size:12px;color:#888;margin-top:32px'>
    This is an automated report. Do not reply to this email.
    </p>
    </body></html>
    """
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    raw = base64.urlsafe_b64encode(msg.as_bytes(policy=email.policy.SMTP)).decode("utf-8")
    result = gmail.users().messages().send(userId="me", body={"raw": raw}).execute()
    
    log.info(f"Email sent to {len(to_emails)} recipient(s) | ID: {result.get('id')}")
    return result.get("id")
