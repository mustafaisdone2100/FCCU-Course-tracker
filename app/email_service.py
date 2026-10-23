import smtplib
import logging
import asyncio
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Dict, Any, Optional, List
import httpx

from app.config import (
    SMTP_HOST,
    SMTP_PORT,
    SMTP_USER,
    SMTP_PASSWORD,
    SMTP_FROM_NAME,
    RESEND_API_KEY,
    RESEND_FROM_EMAIL,
    EMPOWER_CATALOG_URL,
)

logger = logging.getLogger("fccu.email")

def is_email_configured() -> bool:
    """Returns True if either SMTP or Resend credentials are configured."""
    has_smtp = bool(SMTP_USER and SMTP_PASSWORD)
    has_resend = bool(RESEND_API_KEY)
    return has_smtp or has_resend

def get_email_provider() -> str:
    if SMTP_USER and SMTP_PASSWORD:
        return "gmail_smtp"
    elif RESEND_API_KEY:
        return "resend_api"
    return "simulation"

def build_alert_html(
    alert_type: str,
    course_code: str,
    section: str,
    title: str,
    message: str,
    available: int,
    capacity: int,
    instructor: str = "TBA",
    schedule: str = "",
) -> str:
    """Creates a responsive, beautiful HTML email template for FCCU course alerts."""
    is_seat_open = (alert_type == "SEAT_OPENED")
    header_color = "#10b981" if is_seat_open else "#6366f1"
    badge_text = "SEAT AVAILABLE" if is_seat_open else "NEW SECTION ADDED"
    badge_bg = "rgba(16, 185, 129, 0.15)" if is_seat_open else "rgba(99, 102, 241, 0.15)"
    badge_color = "#10b981" if is_seat_open else "#818cf8"
    icon = "⚡" if is_seat_open else "🆕"

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>{badge_text}: {course_code} {section}</title>
      <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #0b0f19; margin: 0; padding: 24px 12px; color: #e2e8f0; }}
        .card {{ max-width: 540px; margin: 0 auto; background: #131b2e; border: 1px solid #1e293b; border-radius: 16px; overflow: hidden; box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.5); }}
        .header {{ background: linear-gradient(135deg, {header_color}22 0%, #1e1b4b 100%); padding: 28px 24px; border-bottom: 1px solid #1e293b; text-align: center; }}
        .badge {{ display: inline-block; background: {badge_bg}; color: {badge_color}; border: 1px solid {badge_color}44; padding: 6px 14px; border-radius: 9999px; font-size: 12px; font-weight: 700; letter-spacing: 0.05em; text-transform: uppercase; margin-bottom: 12px; }}
        .title {{ font-size: 24px; font-weight: 800; color: #ffffff; margin: 0 0 6px 0; }}
        .subtitle {{ font-size: 14px; color: #94a3b8; margin: 0; }}
        .body {{ padding: 28px 24px; }}
        .alert-box {{ background: rgba(16, 185, 129, 0.1); border-left: 4px solid {header_color}; padding: 14px 16px; border-radius: 8px; font-size: 15px; color: #f1f5f9; margin-bottom: 24px; line-height: 1.5; }}
        .details-table {{ width: 100%; border-collapse: collapse; margin-bottom: 28px; }}
        .details-table td {{ padding: 10px 12px; border-bottom: 1px solid #1e293b; font-size: 14px; }}
        .details-table tr:last-child td {{ border-bottom: none; }}
        .label {{ color: #64748b; font-weight: 600; width: 38%; }}
        .value {{ color: #ffffff; font-weight: 600; text-align: right; }}
        .seat-highlight {{ color: #34d399; font-weight: 800; font-size: 16px; }}
        .cta-button {{ display: block; text-align: center; background: linear-gradient(135deg, #2563eb, #3b82f6); color: #ffffff !important; text-decoration: none; font-weight: 700; font-size: 15px; padding: 14px 24px; border-radius: 10px; margin-bottom: 16px; }}
        .footer {{ text-align: center; padding: 20px 24px; border-top: 1px solid #1e293b; font-size: 12px; color: #64748b; background: #0f172a; }}
      </style>
    </head>
    <body>
      <div class="card">
        <div class="header">
          <div class="badge">{icon} {badge_text}</div>
          <h1 class="title">{course_code} - Sec {section}</h1>
          <p class="subtitle">{title}</p>
        </div>
        <div class="body">
          <div class="alert-box">
            <strong>{message}</strong>
            <br>
            <span style="font-size: 13px; color: #94a3b8;">Log into Empower SIS immediately before other students take it!</span>
          </div>

          <table class="details-table">
            <tr>
              <td class="label">Course</td>
              <td class="value">{course_code} (Section {section})</td>
            </tr>
            <tr>
              <td class="label">Available Seats</td>
              <td class="value seat-highlight">{available} / {capacity} seats</td>
            </tr>
            <tr>
              <td class="label">Instructor</td>
              <td class="value">{instructor}</td>
            </tr>
            {f'<tr><td class="label">Schedule</td><td class="value">{schedule}</td></tr>' if schedule else ''}
            <tr>
              <td class="label">Status</td>
              <td class="value" style="color: {header_color};">🟢 Ready to register</td>
            </tr>
          </table>

          <a href="{EMPOWER_CATALOG_URL}" class="cta-button" target="_blank">
            👉 Open Empower SIS & Register Now
          </a>
        </div>
        <div class="footer">
          FCCU Spring 2027 Course Tracker • Real-time Empower Monitor<br>
          You received this because you subscribed to email alerts for this course.
        </div>
      </div>
    </body>
    </html>
    """
    return html

def _send_smtp(to_email: str, subject: str, html_content: str) -> bool:
    """Send email via SMTP (synchronous helper for asyncio.to_thread)."""
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{SMTP_FROM_NAME} <{SMTP_USER}>"
        msg["To"] = to_email

        # Attach HTML
        part = MIMEText(html_content, "html", "utf-8")
        msg.attach(part)

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=12) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, [to_email], msg.as_string())

        logger.info(f"Successfully sent alert email to {to_email} via SMTP.")
        return True
    except Exception as e:
        logger.error(f"Failed to send email to {to_email} via SMTP: {e}")
        return False

async def _send_resend(to_email: str, subject: str, html_content: str) -> bool:
    """Send email via Resend API."""
    try:
        headers = {
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "from": f"{SMTP_FROM_NAME} <{RESEND_FROM_EMAIL}>",
            "to": [to_email],
            "subject": subject,
            "html": html_content
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post("https://api.resend.com/emails", json=payload, headers=headers)
            if resp.status_code in [200, 201]:
                logger.info(f"Successfully sent alert email to {to_email} via Resend.")
                return True
            else:
                logger.error(f"Resend API error: {resp.status_code} - {resp.text}")
                return False
    except Exception as e:
        logger.error(f"Failed to send email to {to_email} via Resend: {e}")
        return False

async def dispatch_email_alert(
    to_email: str,
    alert_type: str,
    course_code: str,
    section: str,
    title: str,
    message: str,
    available: int,
    capacity: int,
    instructor: str = "TBA",
    schedule: str = "",
) -> bool:
    """Dispatch an alert email to a subscriber via the best available provider."""
    is_seat_open = (alert_type == "SEAT_OPENED")
    subject = (
        f"⚡ Seat Opened! {course_code} Sec {section} ({available} seats available)"
        if is_seat_open
        else f"🆕 New Section Added! {course_code} Sec {section}"
    )

    html_content = build_alert_html(
        alert_type=alert_type,
        course_code=course_code,
        section=section,
        title=title,
        message=message,
        available=available,
        capacity=capacity,
        instructor=instructor,
        schedule=schedule,
    )

    if RESEND_API_KEY:
        return await _send_resend(to_email, subject, html_content)
    elif SMTP_USER and SMTP_PASSWORD:
        return await asyncio.to_thread(_send_smtp, to_email, subject, html_content)
    else:
        logger.info(
            f"[EMAIL SIMULATION] Would dispatch email to '{to_email}' with subject: '{subject}'. "
            "(To send real emails, set SMTP_USER and SMTP_PASSWORD in environment variables)."
        )
        return True

async def send_welcome_email(to_email: str, course_code: str, section: str, title: str) -> bool:
    """Sends confirmation email immediately when a student subscribes."""
    subject = f"🔔 Alert Activated: {course_code} {section} - FCCU Course Tracker"
    sec_display = f"Section {section}" if section and section != "ALL" else "All Sections"
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"><style>body{{font-family:sans-serif;background:#0b0f19;color:#e2e8f0;padding:24px;}}.card{{max-width:500px;margin:0 auto;background:#131b2e;border:1px solid #1e293b;border-radius:14px;padding:24px;text-align:center;}}</style></head>
    <body>
      <div class="card">
        <h2 style="color:#10b981;margin-top:0;">🔔 Alert Confirmed!</h2>
        <p style="font-size:16px;color:#ffffff;font-weight:bold;">{course_code} ({sec_display})</p>
        <p style="color:#94a3b8;font-size:14px;">{title}</p>
        <div style="background:#0f172a;padding:16px;border-radius:10px;text-align:left;font-size:14px;margin:20px 0;line-height:1.6;">
          ✅ We will automatically email you the <strong>exact second</strong> a seat opens up or a new section is added on Empower SIS.
        </div>
        <p style="font-size:12px;color:#64748b;">FCCU Spring 2027 Course Tracker</p>
      </div>
    </body>
    </html>
    """

    if RESEND_API_KEY:
        return await _send_resend(to_email, subject, html)
    elif SMTP_USER and SMTP_PASSWORD:
        return await asyncio.to_thread(_send_smtp, to_email, subject, html)
    else:
        logger.info(f"[EMAIL SIMULATION] Welcome email to {to_email} for {course_code} {section}")
        return True
