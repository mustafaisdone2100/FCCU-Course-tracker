import asyncio
import json
import logging
from typing import Optional, Dict, Any
from fastapi import APIRouter, Query, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.config import SUPPORTED_TERMS, DEFAULT_TERM
from app.database import (
    get_courses,
    get_stats,
    get_departments,
    get_alerts,
    toggle_watchlist,
)
from app.tracker import tracker_manager

logger = logging.getLogger("fccu.api")
router = APIRouter(prefix="/api")

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
    return {
        "active_term": tracker_manager.active_term,
        "is_running": tracker_manager.is_running,
        "is_refreshing": tracker_manager.is_refreshing,
        "poll_interval_seconds": tracker_manager.poll_interval,
        "last_sync_time": tracker_manager.last_sync_time,
        "last_sync_error": tracker_manager.last_sync_error,
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
async def trigger_simulate_alert(payload: Optional[Dict[str, str]] = None):
    """Simulate a seat opening or new section addition for immediate alert testing."""
    uniqueness = payload.get("uniqueness") if payload else None
    term = payload.get("term", DEFAULT_TERM) if payload else DEFAULT_TERM
    sim_type = payload.get("type", "seat_opened") if payload else "seat_opened"

    alert = await tracker_manager.trigger_simulation(uniqueness=uniqueness, term=term, sim_type=sim_type)
    if not alert:
        raise HTTPException(status_code=404, detail="No course found to simulate on")
    return {
        "success": True,
        "alert": alert
    }

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
