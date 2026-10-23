import sqlite3
import json
import logging
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Tuple
from app.config import DB_PATH

logger = logging.getLogger("fccu.database")

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Create database tables and indices if they do not exist."""
    with get_connection() as conn:
        cursor = conn.cursor()
        
        # Courses snapshot table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS courses (
                uniqueness TEXT PRIMARY KEY,
                term TEXT NOT NULL,
                dept TEXT NOT NULL,
                code TEXT NOT NULL,
                course_code TEXT NOT NULL,
                section TEXT NOT NULL,
                title TEXT NOT NULL,
                credit TEXT,
                classroom TEXT,
                schedule_raw TEXT,
                start_date TEXT,
                days TEXT,
                time_range TEXT,
                instructor TEXT,
                capacity INTEGER NOT NULL DEFAULT 0,
                available INTEGER NOT NULL DEFAULT 0,
                enrolled INTEGER NOT NULL DEFAULT 0,
                seat_status TEXT NOT NULL,
                is_full INTEGER NOT NULL DEFAULT 0,
                location TEXT,
                cid TEXT,
                last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Seat history log
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS seat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uniqueness TEXT NOT NULL,
                course_code TEXT NOT NULL,
                section TEXT NOT NULL,
                title TEXT NOT NULL,
                term TEXT NOT NULL,
                old_available INTEGER NOT NULL,
                new_available INTEGER NOT NULL,
                capacity INTEGER NOT NULL,
                change_type TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Alerts table (for opened seats, low seats, etc.)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uniqueness TEXT NOT NULL,
                course_code TEXT NOT NULL,
                section TEXT NOT NULL,
                title TEXT NOT NULL,
                term TEXT NOT NULL,
                alert_type TEXT NOT NULL,
                message TEXT NOT NULL,
                old_available INTEGER NOT NULL,
                new_available INTEGER NOT NULL,
                capacity INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_read INTEGER DEFAULT 0
            )
        """)

        # Watchlist table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS watchlist (
                uniqueness TEXT PRIMARY KEY,
                course_code TEXT NOT NULL,
                section TEXT NOT NULL,
                title TEXT NOT NULL,
                term TEXT NOT NULL,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Email Subscriptions table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS email_subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL,
                course_code TEXT NOT NULL,
                section TEXT DEFAULT 'ALL',
                uniqueness TEXT,
                term TEXT NOT NULL,
                notify_seat_open INTEGER DEFAULT 1,
                notify_new_section INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_notified_at TIMESTAMP DEFAULT NULL,
                is_active INTEGER DEFAULT 1
            )
        """)

        # Indices for speedy queries
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_courses_term ON courses(term)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_courses_dept ON courses(dept)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_courses_seat_status ON courses(seat_status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_courses_is_full ON courses(is_full)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_term ON alerts(term)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_created_at ON alerts(created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_history_created_at ON seat_history(created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_subs_email ON email_subscriptions(email)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_subs_course_code ON email_subscriptions(course_code)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_subs_uniqueness ON email_subscriptions(uniqueness)")

        
        # Schema migration checks
        cursor.execute("PRAGMA table_info(courses)")
        col_names = [col["name"] for col in cursor.fetchall()]
        if "first_seen_at" not in col_names:
            cursor.execute("ALTER TABLE courses ADD COLUMN first_seen_at TIMESTAMP DEFAULT NULL")
        if "is_new" not in col_names:
            cursor.execute("ALTER TABLE courses ADD COLUMN is_new INTEGER DEFAULT 0")

        conn.commit()
        logger.info("Database initialized successfully.")
    
    # Automatically clean any non-undergraduate (500+) courses
    purge_non_undergraduate_courses()

def purge_non_undergraduate_courses():
    """Remove any postgraduate (500+), pharmacy, and intensive english courses from database."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT uniqueness, dept, code, title, term FROM courses")
        rows = cursor.fetchall()
        to_delete = []
        for r in rows:
            uniq = r["uniqueness"]
            dept = (r["dept"] or "").strip().upper()
            code = (r["code"] or "").strip().upper()
            title = (r["title"] or "").strip().lower()
            term = (r["term"] or "").strip().upper()

            # Remove pharmacy
            if (term == "PH27SP" or dept.startswith("PHAR") or dept in ["PHRM", "PHARM"] 
                or "pharmacy" in title or "pharmd" in title or "pharm-d" in title):
                to_delete.append(uniq)
                continue

            # Remove intensive english
            if (term == "IE27SP" or dept in ["IE", "IEP", "IEAP"] or dept.startswith("IEP") 
                or "intensive english" in title):
                to_delete.append(uniq)
                continue

            # Math 095 exception
            if "095" in code or code == "95":
                continue

            # Check 100-400 level
            m = re.search(r"(\d+)", code)
            if not m or not (100 <= int(m.group(1)) < 500):
                to_delete.append(uniq)

        if to_delete:
            cursor.executemany("DELETE FROM courses WHERE uniqueness = ?", [(u,) for u in to_delete])
            cursor.executemany("DELETE FROM watchlist WHERE uniqueness = ?", [(u,) for u in to_delete])
            cursor.executemany("DELETE FROM alerts WHERE uniqueness = ?", [(u,) for u in to_delete])
            cursor.executemany("DELETE FROM seat_history WHERE uniqueness = ?", [(u,) for u in to_delete])
            conn.commit()
            logger.info(f"Purged {len(to_delete)} non-undergraduate/pharmacy/IE course sections from database.")

def save_courses_and_detect_changes(
    courses: List[Dict[str, Any]],
    term: str
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Saves or updates course records.
    Detects seat changes compared to previous record:
    - Generates SEAT_OPENED alert if old_available == 0 and new_available > 0.
    - Generates SEAT_TAKEN or COURSE_FULL event.
    Returns (seat_changes, new_alerts).
    """
    if not courses:
        return [], []

    now_iso = datetime.now(timezone.utc).isoformat()
    seat_changes = []
    new_alerts = []

    with get_connection() as conn:
        cursor = conn.cursor()

        # Load existing courses for this term to compare
        cursor.execute(
            "SELECT uniqueness, course_code, section, title, capacity, available, is_full FROM courses WHERE term = ?",
            (term,)
        )
        existing_map = {row["uniqueness"]: dict(row) for row in cursor.fetchall()}

        # Load current watchlist
        cursor.execute("SELECT uniqueness, course_code FROM watchlist")
        watchlist_rows = cursor.fetchall()
        watchlist_set = {row["uniqueness"] for row in watchlist_rows}
        watchlist_courses = {row["course_code"] for row in watchlist_rows}

        for c in courses:
            uniq = c["uniqueness"]
            old = existing_map.get(uniq)
            old_avail = old["available"] if old else None
            old_cap = old["capacity"] if old else None
            new_avail = c["available"]
            new_cap = c["capacity"]
            is_new_section = False

            # Check if this is a newly added section (course didn't exist previously in DB)
            if old is None and len(existing_map) > 0:
                is_new_section = True
                change_type = "NEW_SECTION"
                msg = f"🆕 New Section Added! {c['course_code']} (Sec {c['section']}) - {c['title']} added with {c['available']}/{c['capacity']} seats!"
                
                is_watched = (uniq in watchlist_set) or (c["course_code"] in watchlist_courses)
                alert_dict = {
                    "uniqueness": uniq,
                    "course_code": c["course_code"],
                    "section": c["section"],
                    "title": c["title"],
                    "term": term,
                    "alert_type": "NEW_SECTION",
                    "message": msg,
                    "old_available": 0,
                    "new_available": new_avail,
                    "capacity": new_cap,
                    "created_at": now_iso,
                    "is_watched": is_watched
                }
                new_alerts.append(alert_dict)

                cursor.execute("""
                    INSERT INTO alerts (uniqueness, course_code, section, title, term, alert_type, message, old_available, new_available, capacity, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (uniq, c["course_code"], c["section"], c["title"], term, "NEW_SECTION", msg, 0, new_avail, new_cap, now_iso))

                cursor.execute("""
                    INSERT INTO seat_history (uniqueness, course_code, section, title, term, old_available, new_available, capacity, change_type, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (uniq, c["course_code"], c["section"], c["title"], term, 0, new_avail, new_cap, "NEW_SECTION", now_iso))

            # Check for changes if course previously existed in DB
            elif old is not None:
                if old_avail != new_avail or old_cap != new_cap:
                    # Determine change type
                    if old_avail == 0 and new_avail > 0:
                        change_type = "SEAT_OPENED"
                        diff = new_avail - old_avail
                        msg = f"🎉 Seat Opened! {c['course_code']} (Sec {c['section']}) has {new_avail} seat(s) available now (was Full 0/{new_cap})."
                        
                        alert_dict = {
                            "uniqueness": uniq,
                            "course_code": c["course_code"],
                            "section": c["section"],
                            "title": c["title"],
                            "term": term,
                            "alert_type": "SEAT_OPENED",
                            "message": msg,
                            "old_available": old_avail,
                            "new_available": new_avail,
                            "capacity": new_cap,
                            "created_at": now_iso,
                            "is_watched": uniq in watchlist_set
                        }
                        new_alerts.append(alert_dict)

                        cursor.execute("""
                            INSERT INTO alerts (uniqueness, course_code, section, title, term, alert_type, message, old_available, new_available, capacity, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (uniq, c["course_code"], c["section"], c["title"], term, "SEAT_OPENED", msg, old_avail, new_avail, new_cap, now_iso))

                    elif new_avail == 0 and old_avail > 0:
                        change_type = "COURSE_FULL"
                    elif new_avail > old_avail:
                        change_type = "SEATS_INCREASED"
                    else:
                        change_type = "SEAT_TAKEN"

                    change_record = {
                        "uniqueness": uniq,
                        "course_code": c["course_code"],
                        "section": c["section"],
                        "title": c["title"],
                        "term": term,
                        "old_available": old_avail,
                        "new_available": new_avail,
                        "capacity": new_cap,
                        "change_type": change_type,
                        "created_at": now_iso
                    }
                    seat_changes.append(change_record)

                    cursor.execute("""
                        INSERT INTO seat_history (uniqueness, course_code, section, title, term, old_available, new_available, capacity, change_type, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (uniq, c["course_code"], c["section"], c["title"], term, old_avail, new_avail, new_cap, change_type, now_iso))

            # Upsert into courses table
            days_json = json.dumps(c.get("days", []))
            cursor.execute("""
                INSERT INTO courses (
                    uniqueness, term, dept, code, course_code, section, title, credit,
                    classroom, schedule_raw, start_date, days, time_range, instructor,
                    capacity, available, enrolled, seat_status, is_full, location, cid, last_updated_at, is_new
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(uniqueness) DO UPDATE SET
                    term=excluded.term,
                    dept=excluded.dept,
                    code=excluded.code,
                    course_code=excluded.course_code,
                    section=excluded.section,
                    title=excluded.title,
                    credit=excluded.credit,
                    classroom=excluded.classroom,
                    schedule_raw=excluded.schedule_raw,
                    start_date=excluded.start_date,
                    days=excluded.days,
                    time_range=excluded.time_range,
                    instructor=excluded.instructor,
                    capacity=excluded.capacity,
                    available=excluded.available,
                    enrolled=excluded.enrolled,
                    seat_status=excluded.seat_status,
                    is_full=excluded.is_full,
                    location=excluded.location,
                    cid=excluded.cid,
                    last_updated_at=excluded.last_updated_at,
                    is_new=CASE WHEN excluded.is_new = 1 THEN 1 ELSE courses.is_new END
            """, (
                c["uniqueness"], c["term"], c["dept"], c["code"], c["course_code"],
                c["section"], c["title"], c.get("credit", ""), c.get("classroom", ""),
                c.get("schedule_raw", ""), c.get("start_date", ""), days_json,
                c.get("time_range", ""), c.get("instructor", "TBA"), c["capacity"],
                c["available"], c["enrolled"], c["seat_status"], 1 if c["is_full"] else 0,
                c.get("location", ""), c.get("cid", ""), now_iso, 1 if is_new_section else 0
            ))

        # Remove sections that are no longer listed on Empower (keep only currently active Empower courses)
        current_uniques = {c["uniqueness"] for c in courses}
        removed_uniques = set(existing_map.keys()) - current_uniques
        for rem_uniq in removed_uniques:
            old_item = existing_map[rem_uniq]
            logger.info(f"Section removed from Empower: {old_item['course_code']} Sec {old_item['section']}")
            cursor.execute("""
                INSERT INTO seat_history (uniqueness, course_code, section, title, term, old_available, new_available, capacity, change_type, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (rem_uniq, old_item["course_code"], old_item["section"], old_item["title"], term, old_item["available"], 0, old_item["capacity"], "SECTION_REMOVED", now_iso))
            cursor.execute("DELETE FROM courses WHERE uniqueness = ?", (rem_uniq,))

        conn.commit()

    return seat_changes, new_alerts

def get_courses(
    term: str,
    search: Optional[str] = None,
    dept: Optional[str] = None,
    status: Optional[str] = None,
    level: Optional[str] = None,
    watched_only: bool = False,
    sort_by: str = "course_code"
) -> List[Dict[str, Any]]:
    """Query courses with optional filtering and watchlist status."""
    with get_connection() as conn:
        cursor = conn.cursor()

        query = """
            SELECT c.*, 
                   (w.uniqueness IS NOT NULL) AS is_watched
            FROM courses c
            LEFT JOIN watchlist w ON c.uniqueness = w.uniqueness
            WHERE c.term = ?
              AND (c.code LIKE '%095%' OR c.code = '95' OR (CAST(c.code AS INTEGER) >= 100 AND CAST(c.code AS INTEGER) < 500))
              AND c.dept NOT IN ('PHAR', 'PHRM', 'PHARM', 'IE', 'IEP', 'IEAP')
              AND lower(c.dept) NOT LIKE 'phar%'
              AND lower(c.dept) NOT LIKE 'iep%'
              AND lower(c.title) NOT LIKE '%pharmacy%'
              AND lower(c.title) NOT LIKE '%pharmd%'
              AND lower(c.title) NOT LIKE '%intensive english%'
        """
        params: List[Any] = [term]

        if watched_only:
            query += " AND w.uniqueness IS NOT NULL"

        if dept and dept.strip() and dept.upper() != "ALL":
            query += " AND c.dept = ?"
            params.append(dept.upper().strip())

        if level and level.strip() and level.upper() != "ALL":
            lvl = level.strip()
            if lvl == "095":
                query += " AND (c.code LIKE '%095%' OR c.code = '95')"
            elif lvl in ["100", "200", "300", "400"]:
                prefix = lvl[0]
                query += f" AND (c.code LIKE '{prefix}%' AND length(c.code) >= 3)"

        if status and status.strip() and status.lower() != "all":
            st = status.lower().strip()
            if st == "open":
                query += " AND c.available > 3"
            elif st == "low":
                query += " AND c.available > 0 AND c.available <= 3"
            elif st == "full":
                query += " AND c.available <= 0"
            elif st == "new":
                query += " AND c.is_new = 1"

        if search and search.strip():
            terms = search.strip().split()
            for term_word in terms:
                query += " AND (c.course_code LIKE ? OR c.title LIKE ? OR c.instructor LIKE ? OR c.classroom LIKE ?)"
                kw = f"%{term_word}%"
                params.extend([kw, kw, kw, kw])

        # Order by
        if sort_by == "available_asc":
            query += " ORDER BY c.available ASC, c.course_code ASC, c.section ASC"
        elif sort_by == "available_desc":
            query += " ORDER BY c.available DESC, c.course_code ASC, c.section ASC"
        elif sort_by == "dept":
            query += " ORDER BY c.dept ASC, c.code ASC, c.section ASC"
        elif sort_by == "title":
            query += " ORDER BY c.title ASC, c.section ASC"
        else:
            query += " ORDER BY c.dept ASC, c.code ASC, c.section ASC"

        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["days"] = json.loads(d["days"]) if d["days"] else []
            except Exception:
                d["days"] = []
            d["is_watched"] = bool(d["is_watched"])
            d["is_full"] = bool(d["is_full"])
            d["is_new"] = bool(d.get("is_new", 0))
            result.append(d)

        return result

def get_stats(term: str) -> Dict[str, Any]:
    """Compute aggregate stats for the term dashboard."""
    with get_connection() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT 
                COUNT(*) as total_sections,
                COUNT(DISTINCT course_code) as total_courses,
                COALESCE(SUM(capacity), 0) as total_capacity,
                COALESCE(SUM(available), 0) as total_available,
                COALESCE(SUM(enrolled), 0) as total_enrolled,
                COALESCE(SUM(CASE WHEN available <= 0 THEN 1 ELSE 0 END), 0) as full_sections,
                COALESCE(SUM(CASE WHEN available > 0 AND available <= 3 THEN 1 ELSE 0 END), 0) as low_sections,
                COALESCE(SUM(CASE WHEN available > 3 THEN 1 ELSE 0 END), 0) as open_sections,
                COALESCE(SUM(CASE WHEN is_new = 1 THEN 1 ELSE 0 END), 0) as new_sections,
                MAX(last_updated_at) as last_updated
            FROM courses
            WHERE term = ?
              AND (code LIKE '%095%' OR code = '95' OR (CAST(code AS INTEGER) >= 100 AND CAST(code AS INTEGER) < 500))
              AND dept NOT IN ('PHAR', 'PHRM', 'PHARM', 'IE', 'IEP', 'IEAP')
              AND lower(dept) NOT LIKE 'phar%'
              AND lower(dept) NOT LIKE 'iep%'
              AND lower(title) NOT LIKE '%pharmacy%'
              AND lower(title) NOT LIKE '%pharmd%'
              AND lower(title) NOT LIKE '%intensive english%'
        """, (term,))
        row = dict(cursor.fetchone())

        cursor.execute("""
            SELECT COUNT(*) as watched_count 
            FROM watchlist w
            JOIN courses c ON w.uniqueness = c.uniqueness
            WHERE c.term = ?
              AND (c.code LIKE '%095%' OR c.code = '95' OR (CAST(c.code AS INTEGER) >= 100 AND CAST(c.code AS INTEGER) < 500))
              AND c.dept NOT IN ('PHAR', 'PHRM', 'PHARM', 'IE', 'IEP', 'IEAP')
              AND lower(c.dept) NOT LIKE 'phar%'
              AND lower(c.dept) NOT LIKE 'iep%'
              AND lower(c.title) NOT LIKE '%pharmacy%'
              AND lower(c.title) NOT LIKE '%pharmd%'
              AND lower(c.title) NOT LIKE '%intensive english%'
        """, (term,))
        row["watched_count"] = cursor.fetchone()["watched_count"]

        cursor.execute("""
            SELECT COUNT(*) as alerts_count 
            FROM alerts 
            WHERE term = ?
        """, (term,))
        row["alerts_count"] = cursor.fetchone()["alerts_count"]

        return row

def get_departments(term: str) -> List[Dict[str, Any]]:
    """Return distinct departments and count of sections in the term."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT dept, COUNT(*) as count,
                   SUM(CASE WHEN available <= 0 THEN 1 ELSE 0 END) as full_count,
                   SUM(available) as total_available
            FROM courses
            WHERE term = ?
              AND (code LIKE '%095%' OR code = '95' OR (CAST(code AS INTEGER) >= 100 AND CAST(code AS INTEGER) < 500))
              AND dept NOT IN ('PHAR', 'PHRM', 'PHARM', 'IE', 'IEP', 'IEAP')
              AND lower(dept) NOT LIKE 'phar%'
              AND lower(dept) NOT LIKE 'iep%'
              AND lower(title) NOT LIKE '%pharmacy%'
              AND lower(title) NOT LIKE '%pharmd%'
              AND lower(title) NOT LIKE '%intensive english%'
            GROUP BY dept
            ORDER BY dept ASC
        """, (term,))
        return [dict(r) for r in cursor.fetchall()]

def get_alerts(term: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """Retrieve recent alerts."""
    with get_connection() as conn:
        cursor = conn.cursor()
        if term:
            cursor.execute("""
                SELECT * FROM alerts 
                WHERE term = ? 
                ORDER BY created_at DESC 
                LIMIT ?
            """, (term, limit))
        else:
            cursor.execute("""
                SELECT * FROM alerts 
                ORDER BY created_at DESC 
                LIMIT ?
            """, (limit,))
        return [dict(r) for r in cursor.fetchall()]

def toggle_watchlist(uniqueness: str, term: str) -> bool:
    """Toggle watchlist entry for a course section. Returns True if now watched, False if unwatched."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT uniqueness FROM watchlist WHERE uniqueness = ?", (uniqueness,))
        existing = cursor.fetchone()

        if existing:
            cursor.execute("DELETE FROM watchlist WHERE uniqueness = ?", (uniqueness,))
            conn.commit()
            return False
        else:
            # Get course info
            cursor.execute("SELECT course_code, section, title FROM courses WHERE uniqueness = ?", (uniqueness,))
            c = cursor.fetchone()
            if c:
                cursor.execute("""
                    INSERT INTO watchlist (uniqueness, course_code, section, title, term)
                    VALUES (?, ?, ?, ?, ?)
                """, (uniqueness, c["course_code"], c["section"], c["title"], term))
                conn.commit()
            return True

def simulate_seat_opened(uniqueness: Optional[str] = None, term: str = "2027SP") -> Optional[Dict[str, Any]]:
    """
    Simulation feature:
    Forces a course to transition from 0 seats to 1 seat, or creates a demo alert,
    allowing the user to verify audio chime, desktop notification, and UI banner immediately!
    """
    with get_connection() as conn:
        cursor = conn.cursor()

        if uniqueness:
            cursor.execute("SELECT * FROM courses WHERE uniqueness = ?", (uniqueness,))
            course = cursor.fetchone()
        else:
            # Pick a sample course (e.g. ARTS 101 or CSCS or BUSN)
            cursor.execute("SELECT * FROM courses WHERE term = ? ORDER BY course_code ASC LIMIT 1", (term,))
            course = cursor.fetchone()

        if not course:
            return None

        c = dict(course)
        now_iso = datetime.now(timezone.utc).isoformat()
        old_avail = 0
        new_avail = 1
        cap = c["capacity"] if c["capacity"] > 0 else 35

        msg = f"🎉 [DEMO ALERT] Seat Opened! {c['course_code']} (Sec {c['section']}) has 1 seat available now (was 0/{cap})."

        cursor.execute("""
            INSERT INTO alerts (uniqueness, course_code, section, title, term, alert_type, message, old_available, new_available, capacity, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (c["uniqueness"], c["course_code"], c["section"], c["title"], term, "SEAT_OPENED", msg, old_avail, new_avail, cap, now_iso))

        cursor.execute("""
            INSERT INTO seat_history (uniqueness, course_code, section, title, term, old_available, new_available, capacity, change_type, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (c["uniqueness"], c["course_code"], c["section"], c["title"], term, old_avail, new_avail, cap, "SEAT_OPENED", now_iso))

        # Also temporarily update course to 1 seat available so UI reflects it
        cursor.execute("""
            UPDATE courses 
            SET available = ?, enrolled = ?, seat_status = 'low', is_full = 0, last_updated_at = ?
            WHERE uniqueness = ?
        """, (new_avail, max(0, cap - new_avail), now_iso, c["uniqueness"]))

        conn.commit()

        return {
            "uniqueness": c["uniqueness"],
            "course_code": c["course_code"],
            "section": c["section"],
            "title": c["title"],
            "term": term,
            "alert_type": "SEAT_OPENED",
            "message": msg,
            "old_available": old_avail,
            "new_available": new_avail,
            "capacity": cap,
            "created_at": now_iso
        }

def simulate_new_section(term: str = "2027SP") -> Optional[Dict[str, Any]]:
    """
    Simulation feature for testing new section additions:
    Simulates a department adding a brand-new section to Empower (e.g. CSCS 101 or ARTS 101),
    verifying the new section banner, audio chime, and desktop notification!
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        now_iso = datetime.now(timezone.utc).isoformat()

        # Find a popular undergrad course to base the new section on (e.g. ARTS 101 or CSCS or BUSN)
        cursor.execute("""
            SELECT * FROM courses 
            WHERE term = ? 
            ORDER BY course_code ASC 
            LIMIT 1
        """, (term,))
        base = cursor.fetchone()
        if not base:
            return None

        b = dict(base)
        # Determine a new section letter
        new_sec_letter = "C" if b["section"] != "C" else "D"
        new_uniq = f"{term}_{b['dept']}_{b['code']}_{new_sec_letter}"
        new_title = b["title"]
        cap = 35
        avail = 35

        cursor.execute("""
            INSERT INTO courses (
                uniqueness, term, dept, code, course_code, section, title, credit,
                classroom, schedule_raw, start_date, days, time_range, instructor,
                capacity, available, enrolled, seat_status, is_full, location, cid, last_updated_at, is_new
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(uniqueness) DO UPDATE SET
                available=excluded.available, capacity=excluded.capacity, is_new=1, last_updated_at=excluded.last_updated_at
        """, (
            new_uniq, term, b["dept"], b["code"], b["course_code"], new_sec_letter,
            new_title, b.get("credit", "3.00"), b.get("classroom", "TBA"),
            b.get("schedule_raw", "M W F 14:00 - 14:50"), b.get("start_date", "02/01/2027"),
            b.get("days", "[\"M\", \"W\", \"F\"]"), b.get("time_range", "14:00 - 14:50"),
            b.get("instructor", "Faculty Member"), cap, avail, 0, "open", 0,
            b.get("location", ""), b.get("cid", ""), now_iso
        ))

        msg = f"🆕 [DEMO ALERT] New Section Added! {b['course_code']} (Sec {new_sec_letter} - {new_title}) added with {avail}/{cap} seats!"

        cursor.execute("""
            INSERT INTO alerts (uniqueness, course_code, section, title, term, alert_type, message, old_available, new_available, capacity, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (new_uniq, b["course_code"], new_sec_letter, new_title, term, "NEW_SECTION", msg, 0, avail, cap, now_iso))

        cursor.execute("""
            INSERT INTO seat_history (uniqueness, course_code, section, title, term, old_available, new_available, capacity, change_type, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (new_uniq, b["course_code"], new_sec_letter, new_title, term, 0, avail, cap, "NEW_SECTION", now_iso))

        conn.commit()

        return {
            "uniqueness": new_uniq,
            "course_code": b["course_code"],
            "section": new_sec_letter,
            "title": new_title,
            "term": term,
            "alert_type": "NEW_SECTION",
            "message": msg,
            "old_available": 0,
            "new_available": avail,
            "capacity": cap,
            "created_at": now_iso
        }

def add_email_subscription(
    email: str,
    course_code: str,
    section: str = "ALL",
    uniqueness: Optional[str] = None,
    term: str = "2027SP",
    notify_seat_open: bool = True,
    notify_new_section: bool = True
) -> Dict[str, Any]:
    """Register or update an email alert subscription."""
    email_clean = email.strip().lower()
    course_code_clean = course_code.strip().upper()
    sec_clean = (section or "ALL").strip().upper()

    with get_connection() as conn:
        cursor = conn.cursor()
        # Check if already subscribed to this exact combination
        cursor.execute("""
            SELECT id FROM email_subscriptions
            WHERE email = ? AND course_code = ? AND section = ? AND term = ?
        """, (email_clean, course_code_clean, sec_clean, term))
        existing = cursor.fetchone()

        if existing:
            sub_id = existing["id"]
            cursor.execute("""
                UPDATE email_subscriptions
                SET notify_seat_open = ?, notify_new_section = ?, is_active = 1, uniqueness = ?
                WHERE id = ?
            """, (int(notify_seat_open), int(notify_new_section), uniqueness, sub_id))
        else:
            cursor.execute("""
                INSERT INTO email_subscriptions (
                    email, course_code, section, uniqueness, term, notify_seat_open, notify_new_section, is_active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
            """, (
                email_clean, course_code_clean, sec_clean, uniqueness, term,
                int(notify_seat_open), int(notify_new_section)
            ))
            sub_id = cursor.lastrowid

        conn.commit()
        return {
            "id": sub_id,
            "email": email_clean,
            "course_code": course_code_clean,
            "section": sec_clean,
            "term": term,
            "notify_seat_open": bool(notify_seat_open),
            "notify_new_section": bool(notify_new_section),
        }

def get_subscriptions_for_alert(alert_type: str, course_code: str, uniqueness: str, section: str, term: str) -> List[Dict[str, Any]]:
    """Find all matching active email subscriptions for a triggered alert."""
    with get_connection() as conn:
        cursor = conn.cursor()
        course_clean = course_code.strip().upper()
        sec_clean = section.strip().upper()

        if alert_type == "SEAT_OPENED":
            cursor.execute("""
                SELECT * FROM email_subscriptions
                WHERE term = ? 
                  AND is_active = 1 
                  AND notify_seat_open = 1
                  AND (
                      uniqueness = ? 
                      OR (course_code = ? AND (section = ? OR section = 'ALL'))
                  )
            """, (term, uniqueness, course_clean, sec_clean))
        elif alert_type == "NEW_SECTION":
            cursor.execute("""
                SELECT * FROM email_subscriptions
                WHERE term = ? 
                  AND is_active = 1 
                  AND notify_new_section = 1
                  AND course_code = ?
            """, (term, course_clean))
        else:
            return []

        return [dict(r) for r in cursor.fetchall()]

def get_user_subscriptions(email: str) -> List[Dict[str, Any]]:
    """Retrieve all active subscriptions for a given email."""
    email_clean = email.strip().lower()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT s.*, c.title, c.available, c.capacity, c.seat_status
            FROM email_subscriptions s
            LEFT JOIN courses c ON s.uniqueness = c.uniqueness
            WHERE s.email = ? AND s.is_active = 1
            ORDER BY s.created_at DESC
        """, (email_clean,))
        return [dict(r) for r in cursor.fetchall()]

def remove_email_subscription(subscription_id: int, email: Optional[str] = None) -> bool:
    """Deactivate an email subscription."""
    with get_connection() as conn:
        cursor = conn.cursor()
        if email:
            cursor.execute("UPDATE email_subscriptions SET is_active = 0 WHERE id = ? AND email = ?", (subscription_id, email.strip().lower()))
        else:
            cursor.execute("UPDATE email_subscriptions SET is_active = 0 WHERE id = ?", (subscription_id,))
        conn.commit()
        return cursor.rowcount > 0

def record_subscription_notified(subscription_ids: List[int]):
    """Update last_notified_at timestamp for subscribers who received an email."""
    if not subscription_ids:
        return
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.executemany(
            "UPDATE email_subscriptions SET last_notified_at = ? WHERE id = ?",
            [(now_iso, sid) for sid in subscription_ids]
        )
        conn.commit()

