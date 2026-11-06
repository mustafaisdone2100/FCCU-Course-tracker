import re
import logging
from typing import Dict, List, Optional, Tuple, Any
import requests
from bs4 import BeautifulSoup

from app.config import (
    EMPOWER_CATALOG_URL,
    EMPOWER_GET_LIST_URL,
    REQUEST_TIMEOUT_SECONDS,
)

logger = logging.getLogger("fccu.scraper")

def is_undergraduate_course(dept: str, code: str, title: str = "") -> bool:
    """
    Keep regular undergraduate courses from 100-400 level and remedial courses like MATH 095.
    Explicitly exclude:
    - Postgraduate courses (500, 600, 700+ levels)
    - Pharmacy Program courses (PHAR, PHRM, or title containing pharmacy/pharmd)
    - Intensive English Program courses (IE, IEP, IEAP, or title containing intensive english)
    """
    dept_clean = dept.strip().upper()
    title_clean = title.strip().lower()

    # Exclude Pharmacy Program courses
    if (dept_clean.startswith("PHAR") or dept_clean in ["PHRM", "PHARM"] 
        or "pharmacy" in title_clean or "pharmd" in title_clean or "pharm-d" in title_clean):
        return False

    # Exclude Intensive English Program courses
    if (dept_clean in ["IE", "IEP", "IEAP"] or dept_clean.startswith("IEP")
        or "intensive english" in title_clean):
        return False

    code_clean = code.strip().upper()
    # Explicitly include MATH 095
    if "095" in code_clean or code_clean == "95":
        return True

    m = re.search(r"(\d+)", code_clean)
    if not m:
        return False
    num = int(m.group(1))
    return 100 <= num < 500


class EmpowerScraper:
    """Scrapes FCCU Empower SIS Course Catalog and parses seat and schedule details."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": EMPOWER_CATALOG_URL,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
        })
        self._cached_token: Optional[str] = None

    def get_token_and_session(self, force_refresh: bool = False) -> str:
        """Fetch search page to obtain fresh CSRF token and session cookies."""
        if self._cached_token and not force_refresh:
            return self._cached_token

        logger.info("Fetching search form to obtain CSRF token...")
        try:
            resp = self.session.get(
                EMPOWER_CATALOG_URL,
                timeout=REQUEST_TIMEOUT_SECONDS,
                verify=False
            )
            resp.raise_for_status()

            soup = BeautifulSoup(resp.text, "html.parser")
            token_elem = soup.find("input", {"name": "token"})
            if token_elem and token_elem.get("value"):
                self._cached_token = token_elem["value"]
                logger.info(f"Retrieved token successfully: {self._cached_token[:8]}...")
                return self._cached_token
            else:
                raise ValueError("Could not find 'token' input in catalog HTML")
        except Exception as e:
            logger.error(f"Failed to fetch Empower CSRF token: {e}")
            raise

    def fetch_catalog_html(self, term_id: str, dept_id: str = "") -> str:
        """Query the Empower CFC GetList endpoint for a given term."""
        token = self.get_token_and_session()

        payload = {
            "fuseaction": "CourseCatalog",
            "screen_width": "1920",
            "token": token,
            "empower_global_term_id": term_id,
            "empower_global_dept_id": dept_id,
            "empower_global_course_id": "",
            "cs_sess_id": "",
            "cs_loca_id": "",
            "cs_inst_id": "",
            "cs_classroom": "",
            "cs_emph_id": "",
            "cs_descr": "",
            "CS_time_start": "",
            "CS_time_end": "",
            "status": "1"
        }

        try:
            resp = self.session.post(
                EMPOWER_GET_LIST_URL,
                data=payload,
                timeout=REQUEST_TIMEOUT_SECONDS * 2,
                verify=False
            )
            resp.raise_for_status()
            data = resp.json()

            if not data.get("SUCCESS"):
                # Token might have expired, retry once with fresh token
                logger.warning(f"Empower returned SUCCESS=False: {data.get('MSG')}. Retrying with fresh token...")
                token = self.get_token_and_session(force_refresh=True)
                payload["token"] = token
                resp = self.session.post(
                    EMPOWER_GET_LIST_URL,
                    data=payload,
                    timeout=REQUEST_TIMEOUT_SECONDS * 2,
                    verify=False
                )
                data = resp.json()

            return data.get("html", "")
        except requests.exceptions.JSONDecodeError:
            logger.error("Empower response was not valid JSON")
            raise ValueError("Empower returned invalid non-JSON response")
        except Exception as e:
            logger.error(f"Error querying Empower catalog for term {term_id}: {e}")
            raise

    def parse_courses(self, html_content: str, term_id: str) -> List[Dict[str, Any]]:
        """
        Parse grid rows from Empower catalog HTML into structured course objects.
        Handles both single-row courses and multi-row courses (e.g. Science courses
        with separate lecture and laboratory/discussion sessions).
        """
        if not html_content:
            return []

        soup = BeautifulSoup(html_content, "html.parser")
        rows = soup.find_all("div", class_="ui-grid-row")
        courses: List[Dict[str, Any]] = []

        i = 0
        while i < len(rows):
            row = rows[i]
            cols = row.find_all("div", recursive=False)
            if not cols or len(cols) < 2:
                i += 1
                continue

            # Skip header row
            col1_raw = cols[1].get_text(separator=" ", strip=True).replace("\xa0", " ")
            if col1_raw.lower() in ["course", ""]:
                i += 1
                continue

            # Extract dept, code, section, title from primary course row
            course_match = re.match(
                r"^([A-Z]{2,6})\s+([0-9]{3}[A-Z]?)\s+([A-Za-z0-9]+)\s*(.*)$",
                col1_raw
            )
            if not course_match:
                parts = col1_raw.split(maxsplit=3)
                dept = parts[0] if len(parts) > 0 else ""
                num = parts[1] if len(parts) > 1 else ""
                section = parts[2] if len(parts) > 2 else "A"
                title = parts[3] if len(parts) > 3 else col1_raw
            else:
                dept, num, section, title = course_match.groups()

            # Must be valid dept and course code (e.g. BIOL 100, MATH 095)
            if not re.match(r"^[A-Z]{2,6}$", dept) or not re.match(r"^\d{2,4}", num):
                i += 1
                continue

            # Filter out postgraduate courses (500+, 600+, etc.), pharmacy, and intensive english
            if not is_undergraduate_course(dept, num, title):
                i += 1
                continue

            location = cols[0].get_text(strip=True) if len(cols) > 0 else ""
            credit = cols[2].get_text(strip=True) if len(cols) > 2 else ""
            classroom = cols[3].get_text(strip=True).replace("CLASS ROOM", "").strip() if len(cols) > 3 else ""
            schedule_raw = cols[4].get_text(separator=" ", strip=True) if len(cols) > 4 else ""
            instructor = cols[5].get_text(strip=True) if len(cols) > 5 else "TBA"
            if not instructor:
                instructor = "TBA"

            offer_raw = cols[6].get_text(strip=True) if len(cols) > 6 else ""
            avail_raw = cols[7].get_text(strip=True) if len(cols) > 7 else ""

            # Popover identifier
            cid = ""
            for a in row.find_all("a"):
                if a.get("data-cid"):
                    cid = a["data-cid"]
                    break

            # Collect multiple rooms and schedules for courses with lab/discussion sub-rows
            additional_rooms = [classroom] if classroom else []
            additional_schedules = [schedule_raw] if schedule_raw else []

            i += 1
            # Scan ahead for sub-rows (labs, discussion sessions, or seat numbers) belonging to this course
            while i < len(rows):
                next_row = rows[i]
                next_cols = next_row.find_all("div", recursive=False)
                if not next_cols or len(next_cols) < 2:
                    i += 1
                    continue

                next_c1 = next_cols[1].get_text(separator=" ", strip=True).replace("\xa0", " ")
                # If next row starts a new course header, stop scanning
                if re.match(r"^[A-Z]{2,6}\s+[0-9]{3}[A-Z]?\s+[A-Za-z0-9]+", next_c1):
                    break

                # Sub-session row (e.g. 4 cols or 7 cols with lab details)
                if len(next_cols) >= 3:
                    sub_room = next_cols[1].get_text(strip=True).replace("CLASS ROOM", "").strip()
                    sub_sched = next_cols[2].get_text(separator=" ", strip=True)
                    if sub_room and sub_room not in additional_rooms:
                        additional_rooms.append(sub_room)
                    if sub_sched and sub_sched not in additional_schedules:
                        additional_schedules.append(sub_sched)

                # Sub-session row with capacity & available (col 4 & col 5)
                if len(next_cols) >= 6:
                    sub_offer = next_cols[4].get_text(strip=True)
                    sub_avail = next_cols[5].get_text(strip=True)
                    if sub_offer.isdigit() and int(sub_offer) > 0:
                        offer_raw = sub_offer
                    if sub_avail.isdigit():
                        avail_raw = sub_avail

                i += 1

            # Parse composite schedule and classroom
            all_schedules_str = " | ".join(additional_schedules) if additional_schedules else schedule_raw
            combined_classroom = " / ".join(additional_rooms) if additional_rooms else classroom

            # Parse start date
            start_date = ""
            start_match = re.search(r"start:(\d{2}/\d{2}/\d{4})", all_schedules_str)
            if start_match:
                start_date = start_match.group(1)

            # Extract time ranges
            time_matches = re.findall(r"(\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2})", all_schedules_str)
            clean_times = [tm.replace(" ", "").replace("-", " - ") for tm in time_matches]
            time_range = " | ".join(clean_times) if clean_times else ""

            # Extract days (M, T, W, R, F, S, U)
            sched_without_date = re.sub(r"start:\d{2}/\d{2}/\d{4}", "", all_schedules_str)
            for tm in time_matches:
                sched_without_date = sched_without_date.replace(tm, "")
            day_tokens = re.findall(r"\b([MTWRFSU])\b", sched_without_date)
            days_order = ["M", "T", "W", "R", "F", "S", "U"]
            days_list = [d for d in days_order if d in set(day_tokens)]

            # Seats
            try:
                capacity = int(offer_raw)
            except ValueError:
                capacity = 0

            try:
                available = int(avail_raw)
            except ValueError:
                available = 0

            enrolled = max(0, capacity - available)

            # Determine seat status
            if available <= 0:
                seat_status = "full"
            elif available <= 3:
                seat_status = "low"
            else:
                seat_status = "open"

            uniqueness = f"{term_id}_{dept}_{num}_{section}"

            # Clean leading asterisk or extra spaces in title
            clean_title = re.sub(r"^\*\s*", "", title).strip()

            courses.append({
                "uniqueness": uniqueness,
                "term": term_id,
                "dept": dept,
                "code": num,
                "course_code": f"{dept} {num}",
                "section": section,
                "title": clean_title,
                "credit": credit,
                "classroom": combined_classroom,
                "schedule_raw": all_schedules_str,
                "start_date": start_date,
                "days": days_list,
                "time_range": time_range,
                "instructor": instructor if instructor else "TBA",
                "capacity": capacity,
                "available": available,
                "enrolled": enrolled,
                "seat_status": seat_status,
                "is_full": available <= 0,
                "location": location,
                "cid": cid,
            })

        return courses

    def fetch_and_parse(self, term_id: str) -> List[Dict[str, Any]]:
        """One-stop helper to fetch and parse catalog for a term."""
        html = self.fetch_catalog_html(term_id)
        return self.parse_courses(html, term_id)
