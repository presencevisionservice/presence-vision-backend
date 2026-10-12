import base64

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from django.conf import settings

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.send"


def get_gmail_service():

    credentials = Credentials(
        token=None,
        refresh_token=settings.GMAIL_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
        scopes=[GMAIL_SCOPE],
    )

    credentials.refresh(Request())

    service = build(
        "gmail",
        "v1",
        credentials=credentials
    )

    return service