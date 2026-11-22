import asyncio
import csv
import io
import json
import logging
from typing import Optional, Dict, Any
from fastapi import APIRouter, Query, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from app.config import SUPPORTED_TERMS, DEFAULT_TERM, IS_RENDER
from app.database import (
    get_courses,
    get_stats,
    get_departments,
    get_alerts,
    toggle_watchlist,
    add_email_subscription,
    get_user_subscriptions,
    remove_email_subscription,
    get_removed_sections,
)
from app.email_service import is_email_configured, get_email_provider, send_welcome_email, dispatch_email_alert
from app.tracker import tracker_manager


logger = logging.getLogger("fccu.api")
router = APIRouter(prefix="/api")

@router.get("/ping")
@router.head("/ping")
def api_ping():
    """Ultra-lightweight ping endpoint for uptime monitors."""
    return {"status": "ok"}

@router.get("/terms")
def list_terms():
    """Return available terms and primary default term."""
    return {
        "default": DEFAULT_TERM,
        "terms": SUPPORTED_TERMS
    }

@router.get("/status")
def get_tracker_status():
    """Get current status of scraper and background polling."""
    err = tracker_manager.last_sync_error
    if err and len(err) > 150:
        err = err[:150] + "..."
    return {
        "active_term": tracker_manager.active_term,
        "is_running": tracker_manager.is_running,
        "is_refreshing": tracker_manager.is_refreshing,
        "poll_interval_seconds": tracker_manager.poll_interval,
        "last_sync_time": tracker_manager.last_sync_time,
        "last_sync_error": err,
        "active_listeners": len(tracker_manager._listeners),
    }

@router.get("/courses")
async def query_courses(
    term: str = Query(DEFAULT_TERM),
    search: Optional[str] = Query(None),
    dept: Optional[str] = Query(None),
    status: Optional[str] = Query("all"),
    level: Optional[str] = Query(None),
    watched_only: bool = Query(False),
    sort_by: str = Query("course_code")
):
    """Retrieve filtered list of courses for a term."""
    courses = get_courses(
        term=term,
        search=search,
        dept=dept,
        status=status,
        level=level,
        watched_only=watched_only,
        sort_by=sort_by
    )

    # If database is empty (e.g. serverless cold start on Vercel), auto-sync from Empower
    if not courses and not search and not dept and (not status or status == "all") and (not level or level == "ALL") and not watched_only:
        logger.info(f"Database empty on request for {term}. Auto-syncing from Empower...")
        await tracker_manager.sync_term(term)
        courses = get_courses(
            term=term,
            search=search,
            dept=dept,
            status=status,
            level=level,
            watched_only=watched_only,
            sort_by=sort_by
        )

    return {
        "term": term,
        "count": len(courses),
        "courses": courses
    }

@router.get("/departments")
def query_departments(term: str = Query(DEFAULT_TERM)):
    """Retrieve list of departments and course numbers for the term."""
    depts = get_departments(term=term)
    return {
        "term": term,
        "departments": depts
    }

@router.get("/stats")
def query_stats(term: str = Query(DEFAULT_TERM)):
    """Get aggregate seat statistics for the term."""
    stats = get_stats(term=term)
    return {
        "term": term,
        "stats": stats
    }

@router.get("/alerts")
def query_alerts(term: Optional[str] = Query(None), limit: int = Query(50)):
    """Get recent seat opened / seat changed alerts."""
    alerts = get_alerts(term=term, limit=limit)
    return {
        "alerts": alerts
    }

@router.get("/removed-sections")
def query_removed_sections(
    term: str = Query(DEFAULT_TERM),
    search: Optional[str] = Query(None),
    dept: Optional[str] = Query(None),
    limit: int = Query(250)
):
    """Retrieve sections that were previously listed on Empower SIS but were removed or cancelled."""
    removed = get_removed_sections(term=term, search=search, dept=dept, limit=limit)
    return {
        "term": term,
        "count": len(removed),
        "removed_sections": removed
    }

@router.get("/removed-sections/csv")
def download_removed_sections_csv(
    term: str = Query(DEFAULT_TERM),
    search: Optional[str] = Query(None),
    dept: Optional[str] = Query(None)
):
    """Download a CSV file containing all cancelled/removed sections for the term."""
    removed = get_removed_sections(term=term, search=search, dept=dept, limit=None)

    output = io.StringIO()
    writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)

    # Headers
    writer.writerow([
        "Term",
        "Department",
        "Course Code",
        "Section",
        "Course Title",
        "Credit Hours",
        "Former Instructor",
        "Classroom",
        "Previous Schedule",
        "Capacity",
        "Enrolled",
        "Date Removed",
        "Status / Reason"
    ])

    for r in removed:
        writer.writerow([
            r.get("term", term),
            r.get("dept", ""),
            r.get("course_code", ""),
            r.get("section", ""),
            r.get("title", ""),
            r.get("credit", "3.00"),
            r.get("instructor", "TBA"),
            r.get("classroom", "TBA"),
            r.get("schedule_raw", "TBA"),
            r.get("capacity", 0),
            r.get("enrolled", 0),
            r.get("removed_at", ""),
            r.get("reason", "Cancelled / Dropped from Empower SIS")
        ])

    csv_bytes = output.getvalue().encode("utf-8-sig")
    filename = f"FCCU_Removed_Sections_{term}.csv"

    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-cache"
        }
    )

@router.post("/refresh")
async def trigger_refresh(term: str = Query(DEFAULT_TERM)):
    """Trigger an immediate scrape of Empower for the specified term."""
    if tracker_manager.is_refreshing:
        return {"status": "already_refreshing", "message": "A sync is already in progress"}
    
    tracker_manager.active_term = term
    result = await tracker_manager.sync_term(term)
    return result

@router.post("/watchlist/toggle")
def handle_toggle_watchlist(payload: Dict[str, str]):
    """Star or unstar a course."""
    uniqueness = payload.get("uniqueness")
    term = payload.get("term", DEFAULT_TERM)
    if not uniqueness:
        raise HTTPException(status_code=400, detail="Missing 'uniqueness'")
    
    is_watched = toggle_watchlist(uniqueness, term)
    return {
        "uniqueness": uniqueness,
        "is_watched": is_watched
    }

@router.post("/simulate")
async def trigger_simulate_alert(payload: Optional[Dict[str, Any]] = None):
    """Simulate a seat opening, filling, or new section addition for immediate alert testing."""
    uniqueness = payload.get("uniqueness") if payload else None
    term = payload.get("term", DEFAULT_TERM) if payload else DEFAULT_TERM
    sim_type = payload.get("type", "seat_opened") if payload else "seat_opened"
    seats = int(payload.get("seats", 1)) if payload else 1

    alert = await tracker_manager.trigger_simulation(
        uniqueness=uniqueness,
        term=term,
        sim_type=sim_type,
        seats=seats
    )
    if not alert:
        raise HTTPException(status_code=404, detail="No course found to simulate on")
    return {
        "success": True,
        "alert": alert
    }

@router.get("/email/status")
def email_status():
    """Return whether email sending is configured and active provider."""
    provider = get_email_provider()
    configured = is_email_configured()
    is_blocked = (IS_RENDER and provider == "gmail_smtp")
    return {
        "configured": configured,
        "provider": provider,
        "is_render": IS_RENDER,
        "is_smtp_blocked": is_blocked,
        "hint": (
            "Render Free Tier blocks Gmail SMTP ports (465/587). Please add RESEND_API_KEY in Render Environment Variables."
            if is_blocked else ""
        )
    }

@router.post("/email/test")
async def send_direct_test_email(payload: Dict[str, str]):
    """Send an immediate test seat opening email to verify delivery to an inbox."""
    email = payload.get("email", "").strip()
    if not email or "@" not in email or "." not in email:
        raise HTTPException(status_code=400, detail="Please enter a valid email address.")

    course_code = payload.get("course_code", "MCOM 101").strip().upper()
    section = payload.get("section", "A").strip().upper()
    title = payload.get("title", "Intro to Communication Studies")

    delivered, err_detail = await dispatch_email_alert(
        to_email=email,
        alert_type="SEAT_OPENED",
        course_code=course_code,
        section=section,
        title=title,
        message=f"🎉 [TEST ALERT] Seat Opened! {course_code} (Sec {section}) has 1 seat available now!",
        available=1,
        capacity=35,
        instructor="Dr. Test Professor",
        schedule="M W F 11:00 - 11:50"
    )

    configured = is_email_configured()
    provider = get_email_provider()

    if configured and delivered:
        msg = f"✅ Test email successfully sent to {email} via {provider}! Please check your Inbox and Spam folder."
    elif not configured:
        msg = f"ℹ️ Email simulated for {email}. (Note: Real delivery requires SMTP_USER & SMTP_PASSWORD or RESEND_API_KEY in Render environment variables)."
    else:
        msg = f"⚠️ Attempted to send via {provider}, but encountered an error: {err_detail}"

    return {
        "success": delivered,
        "configured": configured,
        "provider": provider,
        "recipient": email,
        "message": msg
    }

@router.post("/subscriptions")
async def create_subscription(payload: Dict[str, Any]):
    """Subscribe an email address to course seat openings and new sections."""
    email = payload.get("email", "").strip()
    course_code = payload.get("course_code", "").strip()
    section = payload.get("section", "ALL").strip()
    uniqueness = payload.get("uniqueness")
    title = payload.get("title", "")
    term = payload.get("term", DEFAULT_TERM)
    notify_seat_open = payload.get("notify_seat_open", True)
    notify_new_section = payload.get("notify_new_section", True)

    if not email or "@" not in email or "." not in email:
        raise HTTPException(status_code=400, detail="Please enter a valid email address.")
    if not course_code:
        raise HTTPException(status_code=400, detail="Course code is required.")

    sub = add_email_subscription(
        email=email,
        course_code=course_code,
        section=section,
        uniqueness=uniqueness,
        term=term,
        notify_seat_open=notify_seat_open,
        notify_new_section=notify_new_section,
    )

    # Send an instant confirmation email in background
    asyncio.create_task(send_welcome_email(email, course_code, section, title))

    return {
        "success": True,
        "message": f"Subscribed! You will receive an instant email alert when seats open or new sections are added for {course_code}.",
        "subscription": sub
    }

@router.get("/subscriptions")
def list_subscriptions(email: str = Query(...)):
    """Retrieve all active subscriptions for an email address."""
    email_clean = email.strip()
    if not email_clean:
        return {"subscriptions": []}
    subs = get_user_subscriptions(email_clean)
    return {"subscriptions": subs}

@router.delete("/subscriptions/{sub_id}")
def unsubscribe(sub_id: int, email: Optional[str] = Query(None)):
    """Unsubscribe from a course alert."""
    success = remove_email_subscription(sub_id, email)
    if not success:
        raise HTTPException(status_code=404, detail="Subscription not found or already cancelled.")
    return {"success": True, "message": "Successfully unsubscribed from alerts."}

@router.get("/stream")

async def event_stream(request: Request):
    """Server-Sent Events (SSE) stream for live alerts and updates."""
    queue = tracker_manager.add_listener()

    async def sse_event_generator():
        try:
            # Yield initial connection confirmation
            init_msg = json.dumps({"type": "connected", "data": {"term": tracker_manager.active_term}})
            yield f"data: {init_msg}\n\n"

            while True:
                # Check client disconnection
                if await request.is_disconnected():
                    break

                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    # Heartbeat keepalive
                    yield ": heartbeat\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            tracker_manager.remove_listener(queue)

    return StreamingResponse(
        sse_event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )
