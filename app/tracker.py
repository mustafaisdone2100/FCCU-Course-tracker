import asyncio
import logging
from typing import Set, Dict, Any, Optional
from datetime import datetime, timezone

from app.config import DEFAULT_TERM, DEFAULT_POLL_INTERVAL_SECONDS
from app.scraper import EmpowerScraper
from app.database import (
    save_courses_and_detect_changes,
    get_stats,
    simulate_seat_opened as db_simulate_seat_opened,
    simulate_new_section as db_simulate_new_section,
)

logger = logging.getLogger("fccu.tracker")

class CourseTrackerManager:
    """Manages background polling, seat delta detection, and SSE broadcasting."""

    def __init__(self):
        self.scraper = EmpowerScraper()
        self.active_term = DEFAULT_TERM
        self.poll_interval = DEFAULT_POLL_INTERVAL_SECONDS
        self.is_running = False
        self.is_refreshing = False
        self.last_sync_time: Optional[str] = None
        self.last_sync_error: Optional[str] = None
        self._listeners: Set[asyncio.Queue] = set()
        self._background_task: Optional[asyncio.Task] = None

    def add_listener(self) -> asyncio.Queue:
        """Register a new SSE client queue."""
        q = asyncio.Queue()
        self._listeners.add(q)
        logger.info(f"New SSE client connected. Active listeners: {len(self._listeners)}")
        return q

    def remove_listener(self, q: asyncio.Queue):
        """Deregister an SSE client queue."""
        self._listeners.discard(q)
        logger.info(f"SSE client disconnected. Active listeners: {len(self._listeners)}")

    async def broadcast(self, event_type: str, data: Dict[str, Any]):
        """Push an event to all connected SSE clients."""
        if not self._listeners:
            return

        payload = {
            "type": event_type,
            "data": data,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

        # Send to all connected queues without blocking
        dead_queues = []
        for q in self._listeners:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                dead_queues.append(q)
            except Exception:
                dead_queues.append(q)

        for q in dead_queues:
            self._listeners.discard(q)

    async def sync_term(self, term: str) -> Dict[str, Any]:
        """Scrape term from Empower and detect changes in background thread."""
        self.is_refreshing = True
        logger.info(f"Starting sync for term {term}...")

        try:
            # Run scraper in thread pool to avoid blocking asyncio event loop
            courses = await asyncio.to_thread(self.scraper.fetch_and_parse, term)
            
            # Detect changes and save to SQLite
            changes, alerts = await asyncio.to_thread(
                save_courses_and_detect_changes, courses, term
            )

            now_iso = datetime.now(timezone.utc).isoformat()
            self.last_sync_time = now_iso
            self.last_sync_error = None

            stats = await asyncio.to_thread(get_stats, term)

            logger.info(f"Sync complete for {term}. Courses: {len(courses)}, Changes: {len(changes)}, Alerts: {len(alerts)}")

            # Broadcast seat opened alerts
            for alert in alerts:
                await self.broadcast("seat_alert", alert)
                asyncio.create_task(send_external_notification(alert))
                asyncio.create_task(dispatch_email_alerts_for_event(alert))

            # Broadcast general update notification
            await self.broadcast("sync_completed", {
                "term": term,
                "courses_count": len(courses),
                "changes_count": len(changes),
                "alerts_count": len(alerts),
                "last_sync": now_iso,
                "stats": stats
            })

            return {
                "success": True,
                "term": term,
                "courses_count": len(courses),
                "changes": changes,
                "alerts": alerts,
                "last_sync": now_iso
            }

        except Exception as e:
            err_msg = str(e)
            logger.error(f"Failed to sync term {term}: {err_msg}")
            self.last_sync_error = err_msg
            await self.broadcast("sync_failed", {"term": term, "error": err_msg})
            return {"success": False, "term": term, "error": err_msg}

        finally:
            self.is_refreshing = False

    async def _polling_loop(self):
        """Continuous background worker that polls Empower."""
        logger.info(f"Starting background course tracker loop (interval: {self.poll_interval}s)...")
        # Run initial sync on startup
        await self.sync_term(self.active_term)

        while self.is_running:
            try:
                await asyncio.sleep(self.poll_interval)
                if self.is_running:
                    await self.sync_term(self.active_term)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Unexpected error in tracker loop: {e}")
                await asyncio.sleep(10)

    def start(self):
        """Start the background polling worker."""
        if self.is_running:
            return
        self.is_running = True
        self._background_task = asyncio.create_task(self._polling_loop())

    def stop(self):
        """Stop the background polling worker."""
        self.is_running = False
        if self._background_task:
            self._background_task.cancel()
            self._background_task = None

    async def trigger_simulation(self, uniqueness: Optional[str] = None, term: Optional[str] = None, sim_type: str = "seat_opened") -> Optional[Dict[str, Any]]:
        """Trigger a simulated alert for user testing (seat_opened or new_section)."""
        target_term = term or self.active_term
        if sim_type == "new_section":
            alert_data = await asyncio.to_thread(db_simulate_new_section, target_term)
        else:
            alert_data = await asyncio.to_thread(db_simulate_seat_opened, uniqueness, target_term)

        if alert_data:
            await self.broadcast("seat_alert", alert_data)
            asyncio.create_task(send_external_notification(alert_data))
            asyncio.create_task(dispatch_email_alerts_for_event(alert_data))
        return alert_data


async def dispatch_email_alerts_for_event(alert: Dict[str, Any]):
    """Finds matching email subscribers and sends alert emails."""
    try:
        from app.database import get_subscriptions_for_alert, record_subscription_notified
        from app.email_service import dispatch_email_alert

        alert_type = alert.get("alert_type", "SEAT_OPENED")
        course_code = alert.get("course_code", "")
        uniqueness = alert.get("uniqueness", "")
        section = alert.get("section", "")
        term = alert.get("term", "2027SP")

        subs = await asyncio.to_thread(
            get_subscriptions_for_alert, alert_type, course_code, uniqueness, section, term
        )

        if not subs:
            return

        logger.info(f"Dispatching email alerts to {len(subs)} subscriber(s) for {course_code} {section} ({alert_type})")
        notified_ids = []
        for sub in subs:
            to_email = sub["email"]
            success = await dispatch_email_alert(
                to_email=to_email,
                alert_type=alert_type,
                course_code=course_code,
                section=section,
                title=alert.get("title", ""),
                message=alert.get("message", ""),
                available=alert.get("new_available", 0),
                capacity=alert.get("capacity", 0),
            )
            if success:
                notified_ids.append(sub["id"])

        if notified_ids:
            await asyncio.to_thread(record_subscription_notified, notified_ids)
    except Exception as e:
        logger.error(f"Error dispatching email alerts: {e}")



async def send_external_notification(alert: Dict[str, Any]):
    """Send alert to Discord Webhook or Telegram Bot if configured."""
    from app.config import DISCORD_WEBHOOK_URL, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    import httpx

    msg = f"🚨 **FCCU Alert**: {alert.get('message', '')} ({alert.get('course_code')} {alert.get('section', '')})"
    
    if DISCORD_WEBHOOK_URL:
        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                await client.post(DISCORD_WEBHOOK_URL, json={"content": msg})
        except Exception as e:
            logger.warning(f"Discord alert webhook failed: {e}")

    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            async with httpx.AsyncClient(timeout=6.0) as client:
                await client.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"})
        except Exception as e:
            logger.warning(f"Telegram alert notification failed: {e}")


# Singleton tracker instance
tracker_manager = CourseTrackerManager()

