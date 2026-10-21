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
        """Parse grid rows from Empower catalog HTML into structured course objects."""
        if not html_content:
            return []

        soup = BeautifulSoup(html_content, "html.parser")
        rows = soup.find_all("div", class_="ui-grid-row")
        courses: List[Dict[str, Any]] = []

        for row in rows:
            cols = row.find_all("div", recursive=False)
            if len(cols) < 8:
                continue

            # Skip header row
            col1_raw = cols[1].get_text(strip=True)
            if col1_raw.lower() in ["course", ""]:
                continue

            course_full_text = cols[1].get_text(separator=" ", strip=True).replace("\xa0", " ")
            
            # Extract dept, code, section, title
            # Typical formats:
            # "ARTS 101 A Intro. Art & Design"
            # "CSCS 201 B Data Structures"
            # "MGMT 605 01 Business Strategy"
            course_match = re.match(
                r"^([A-Z]{2,6})\s+([0-9]{3}[A-Z]?)\s+([A-Za-z0-9]+)\s+(.+)$",
                course_full_text
            )
            if course_match:
                dept, num, section, title = course_match.groups()
            else:
                parts = course_full_text.split(maxsplit=3)
                dept = parts[0] if len(parts) > 0 else "GEN"
                num = parts[1] if len(parts) > 1 else ""
                section = parts[2] if len(parts) > 2 else "A"
                title = parts[3] if len(parts) > 3 else course_full_text

            # Filter out postgraduate courses (500+, 600+, etc.), pharmacy, and intensive english
            if not is_undergraduate_course(dept, num, title):
                continue

            location = cols[0].get_text(strip=True)
            credit = cols[2].get_text(strip=True)
            classroom = cols[3].get_text(strip=True).replace("CLASS ROOM", "").strip()
            schedule_raw = cols[4].get_text(separator=" ", strip=True)
            instructor = cols[5].get_text(strip=True)
            offer_raw = cols[6].get_text(strip=True)
            avail_raw = cols[7].get_text(strip=True)

            # Parse schedule (start date, days, time)
            # Example: "start:02/01/2027 M W F 11:00 - 11:50"
            start_date = ""
            days_list = []
            time_range = ""

            start_match = re.search(r"start:(\d{2}/\d{2}/\d{4})", schedule_raw)
            if start_match:
                start_date = start_match.group(1)

            # Extract time range e.g. "11:00 - 11:50"
            time_match = re.search(r"(\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2})", schedule_raw)
            if time_match:
                time_range = time_match.group(1).replace(" ", "")
                # normalize to e.g. 11:00 - 11:50
                time_range = time_range.replace("-", " - ")

            # Extract days (M, T, W, R, F, S, U)
            # Days in Empower are usually uppercase letters like "M W F" or "T R"
            sched_without_date = re.sub(r"start:\d{2}/\d{2}/\d{4}", "", schedule_raw)
            if time_match:
                sched_without_date = sched_without_date.replace(time_match.group(0), "")
            
            day_tokens = re.findall(r"\b([MTWRFSU])\b", sched_without_date)
            days_list = day_tokens if day_tokens else []

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

            # Popover identifier
            cid = ""
            for a in row.find_all("a"):
                if a.get("data-cid"):
                    cid = a["data-cid"]
                    break

            uniqueness = f"{term_id}_{dept}_{num}_{section}"

            courses.append({
                "uniqueness": uniqueness,
                "term": term_id,
                "dept": dept,
                "code": num,
                "course_code": f"{dept} {num}",
                "section": section,
                "title": title,
                "credit": credit,
                "classroom": classroom,
                "schedule_raw": schedule_raw,
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
