"""Email notification module for open alerts."""
import os
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

logger = logging.getLogger(__name__)

# SMTP configuration from environment
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
NOTIFICATION_EMAIL = os.getenv("NOTIFICATION_EMAIL", "")


def is_email_notifications_enabled() -> bool:
    """Check if email notifications are configured."""
    return bool(SMTP_USERNAME and SMTP_PASSWORD and NOTIFICATION_EMAIL)


def send_open_notification(
    recipient: str,
    subject: str,
    opened_at: datetime,
    country: str | None,
    city: str | None,
    track_id: str
) -> bool:
    """
    Send email notification when an email is opened.

    Returns True if email was sent successfully, False otherwise.
    """
    if not is_email_notifications_enabled():
        logger.warning("Email notifications not configured - skipping")
        return False

    # Build location string
    location_parts = []
    if city:
        location_parts.append(city)
    if country:
        location_parts.append(country)
    location = ", ".join(location_parts) if location_parts else "Unknown location"

    # Format the email
    email_subject = f"Email Opened: {subject or '(no subject)'}"

    html_body = f"""
    <html>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; padding: 20px;">
        <h2 style="color: #27ae60;">Your email was opened!</h2>
        <table style="border-collapse: collapse; margin-top: 15px;">
            <tr>
                <td style="padding: 8px 15px 8px 0; color: #666; font-weight: bold;">To:</td>
                <td style="padding: 8px 0;">{recipient or 'Unknown'}</td>
            </tr>
            <tr>
                <td style="padding: 8px 15px 8px 0; color: #666; font-weight: bold;">Subject:</td>
                <td style="padding: 8px 0;">{subject or '(no subject)'}</td>
            </tr>
            <tr>
                <td style="padding: 8px 15px 8px 0; color: #666; font-weight: bold;">Opened:</td>
                <td style="padding: 8px 0;">{opened_at.strftime('%B %d, %Y at %I:%M %p')}</td>
            </tr>
            <tr>
                <td style="padding: 8px 15px 8px 0; color: #666; font-weight: bold;">Location:</td>
                <td style="padding: 8px 0;">{location}</td>
            </tr>
        </table>
        <p style="margin-top: 20px; color: #888; font-size: 12px;">
            This is the first real open (excluding email privacy proxies).
        </p>
    </body>
    </html>
    """

    text_body = f"""
Your email was opened!

To: {recipient or 'Unknown'}
Subject: {subject or '(no subject)'}
Opened: {opened_at.strftime('%B %d, %Y at %I:%M %p')}
Location: {location}

This is the first real open (excluding email privacy proxies).
    """

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = email_subject
        msg["From"] = SMTP_USERNAME
        msg["To"] = NOTIFICATION_EMAIL

        msg.attach(MIMEText(text_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(SMTP_USERNAME, NOTIFICATION_EMAIL, msg.as_string())

        logger.info(f"Open notification sent for track {track_id}")
        return True

    except Exception as e:
        logger.error(f"Failed to send open notification: {e}")
        return False
