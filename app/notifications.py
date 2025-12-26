"""Email notification module for open alerts."""
import os
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

logger = logging.getLogger(__name__)


def format_time_elapsed(sent_at: datetime, opened_at: datetime) -> str:
    """Format the time elapsed between sending and opening in a human-readable way."""
    delta = opened_at - sent_at
    total_seconds = int(delta.total_seconds())

    if total_seconds < 0:
        return "immediately"

    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60

    parts = []

    if days > 0:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours > 0:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes > 0 and days == 0:  # Only show minutes if less than a day
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    if seconds > 0 and days == 0 and hours == 0:  # Only show seconds if less than an hour
        parts.append(f"{seconds} second{'s' if seconds != 1 else ''}")

    if not parts:
        return "immediately"

    return ", ".join(parts)

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
    track_id: str,
    sent_at: datetime | None = None
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

    # Calculate time elapsed
    recipient_name = recipient.split('@')[0] if recipient else 'Someone'
    elapsed = format_time_elapsed(sent_at, opened_at) if sent_at else None

    # Format the email
    if elapsed:
        email_subject = f"{recipient_name} read your message {elapsed} after you sent it"
    else:
        email_subject = f"{recipient_name} read your message: {subject or '(no subject)'}"

    html_body = f"""
    <html>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; padding: 20px;">
        <h2 style="color: #27ae60;">{recipient_name} read your message!</h2>
        {f'<p style="font-size: 18px; color: #333;"><strong>{elapsed}</strong> after you sent it</p>' if elapsed else ''}
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
{recipient_name} read your message!
{f"{elapsed} after you sent it" if elapsed else ""}

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


def send_followup_reminder(
    recipient: str,
    subject: str,
    sent_at: datetime,
    days_ago: int,
    track_id: str
) -> bool:
    """
    Send email notification reminding to follow up on an unopened email.

    Returns True if email was sent successfully, False otherwise.
    """
    if not is_email_notifications_enabled():
        logger.warning("Email notifications not configured - skipping followup reminder")
        return False

    # Format the email
    email_subject = f"Follow-up Reminder: {subject or '(no subject)'}"

    html_body = f"""
    <html>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; padding: 20px;">
        <h2 style="color: #e67e22;">Time to follow up?</h2>
        <p style="color: #555; font-size: 16px;">
            Your email hasn't been opened in <strong>{days_ago} days</strong>. Consider sending a follow-up!
        </p>
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
                <td style="padding: 8px 15px 8px 0; color: #666; font-weight: bold;">Sent:</td>
                <td style="padding: 8px 0;">{sent_at.strftime('%B %d, %Y at %I:%M %p')}</td>
            </tr>
        </table>
        <p style="margin-top: 20px; color: #888; font-size: 12px;">
            This email has not been opened (excluding automated proxy prefetches).
        </p>
    </body>
    </html>
    """

    text_body = f"""
Time to follow up?

Your email hasn't been opened in {days_ago} days. Consider sending a follow-up!

To: {recipient or 'Unknown'}
Subject: {subject or '(no subject)'}
Sent: {sent_at.strftime('%B %d, %Y at %I:%M %p')}

This email has not been opened (excluding automated proxy prefetches).
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

        logger.info(f"Follow-up reminder sent for track {track_id}")
        return True

    except Exception as e:
        logger.error(f"Failed to send follow-up reminder: {e}")
        return False


def send_hot_conversation_notification(
    recipient: str,
    subject: str,
    open_count: int,
    track_id: str
) -> bool:
    """
    Send email notification when an email has 3+ opens in 24 hours.

    Returns True if email was sent successfully, False otherwise.
    """
    if not is_email_notifications_enabled():
        logger.warning("Email notifications not configured - skipping hot conversation notification")
        return False

    recipient_name = recipient.split('@')[0] if recipient else 'Someone'

    # Format the email
    email_subject = f"🔥 Hot conversation! {recipient_name} opened your email {open_count} times today"

    html_body = f"""
    <html>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; padding: 20px;">
        <h2 style="color: #e74c3c;">🔥 Hot conversation!</h2>
        <p style="font-size: 18px; color: #333;">
            <strong>{recipient_name}</strong> has opened your email <strong>{open_count} times</strong> in the last 24 hours.
        </p>
        <p style="color: #555;">They're clearly interested - this might be a good time to follow up!</p>
        <table style="border-collapse: collapse; margin-top: 15px;">
            <tr>
                <td style="padding: 8px 15px 8px 0; color: #666; font-weight: bold;">To:</td>
                <td style="padding: 8px 0;">{recipient or 'Unknown'}</td>
            </tr>
            <tr>
                <td style="padding: 8px 15px 8px 0; color: #666; font-weight: bold;">Subject:</td>
                <td style="padding: 8px 0;">{subject or '(no subject)'}</td>
            </tr>
        </table>
    </body>
    </html>
    """

    text_body = f"""
🔥 Hot conversation!

{recipient_name} has opened your email {open_count} times in the last 24 hours.
They're clearly interested - this might be a good time to follow up!

To: {recipient or 'Unknown'}
Subject: {subject or '(no subject)'}
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

        logger.info(f"Hot conversation notification sent for track {track_id}")
        return True

    except Exception as e:
        logger.error(f"Failed to send hot conversation notification: {e}")
        return False


def send_revived_conversation_notification(
    recipient: str,
    subject: str,
    days_since_first_open: int,
    track_id: str
) -> bool:
    """
    Send email notification when an old email is opened again after 2+ weeks.

    Returns True if email was sent successfully, False otherwise.
    """
    if not is_email_notifications_enabled():
        logger.warning("Email notifications not configured - skipping revived conversation notification")
        return False

    recipient_name = recipient.split('@')[0] if recipient else 'Someone'

    # Format the email
    email_subject = f"🔄 Old conversation revived! {recipient_name} re-opened your email after {days_since_first_open} days"

    html_body = f"""
    <html>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; padding: 20px;">
        <h2 style="color: #9b59b6;">🔄 Old conversation revived!</h2>
        <p style="font-size: 18px; color: #333;">
            <strong>{recipient_name}</strong> just re-opened your email from <strong>{days_since_first_open} days ago</strong>.
        </p>
        <p style="color: #555;">They're thinking about this again - might be worth reaching out!</p>
        <table style="border-collapse: collapse; margin-top: 15px;">
            <tr>
                <td style="padding: 8px 15px 8px 0; color: #666; font-weight: bold;">To:</td>
                <td style="padding: 8px 0;">{recipient or 'Unknown'}</td>
            </tr>
            <tr>
                <td style="padding: 8px 15px 8px 0; color: #666; font-weight: bold;">Subject:</td>
                <td style="padding: 8px 0;">{subject or '(no subject)'}</td>
            </tr>
        </table>
    </body>
    </html>
    """

    text_body = f"""
🔄 Old conversation revived!

{recipient_name} just re-opened your email from {days_since_first_open} days ago.
They're thinking about this again - might be worth reaching out!

To: {recipient or 'Unknown'}
Subject: {subject or '(no subject)'}
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

        logger.info(f"Revived conversation notification sent for track {track_id}")
        return True

    except Exception as e:
        logger.error(f"Failed to send revived conversation notification: {e}")
        return False
