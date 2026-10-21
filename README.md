# FCCU Spring 2027 Course & Seat Tracker

A real-time course seat availability tracker and instant alert monitor for **Forman Christian College (A Chartered University) - Spring 2027 Course Registration**.

Directly connected to the **FCCU Empower SIS Course Catalog** (`https://mysis-fccollege.empower-xl.com/fusebox.cfm?fuseaction=CourseCatalog&rpt=1`).

---

## 🚀 Quick Start

### 1. Launch the Application
You can start the tracker in either of two ways:

- **Option A (Double-click)**: Run [`start.bat`](file:///b:/Course%20Tracker/start.bat)
- **Option B (Terminal)**:
  ```powershell
  python run.py
  ```

### 2. Open the Dashboard
Navigate to:
```
http://127.0.0.1:8000
```
(or `http://localhost:8000`)

---

## ✨ Features

- **Direct Empower SIS Integration**:
  - Automatically queries Empower's `/cfcs/courseCatalog.cfc?method=GetList` endpoint.
  - Automatically fetches the CSRF token and maintains session authentication.
  - **Undergraduate Scope & Strict Exclusions**:
    - Automatically filters to keep all undergraduate degree courses (100, 200, 300, 400 levels).
    - Explicitly includes remedial `MATH 095` when listed by Empower.
    - Excludes all postgraduate degree courses (500+, 600+, 700+ levels such as MS, MPhil, PhD, MBA).
    - Excludes **Pharmacy Program** (`PH27SP`, `PHAR`, `PHRM`, Pharm-D) and **Intensive English Program** (`IE27SP`, `IE`, `IEP`) courses.
    - Currently tracking **464 active undergraduate course sections across 215 unique courses** for **Spring 2027 (`2027SP`)**.

- **Instant Seat-Opening & New Section Alert System**:
  - **Seat Openings**: Whenever a full section (0 seats) opens up, instantly triggers audio chime, top glowing alert banner, and browser notification.
  - **New Sections Added on Empower**: During course registration (starting 23 Nov) and the second-phase **Add/Drop Period in January**, departments frequently open brand new sections (e.g. `CSCS 101 Sec C`, `MATH 101 Sec D`). The tracker immediately detects these newly added sections and dispatches instant `🆕 New Section Added!` alerts!
  - **Live Section Lifecycle Reconciliation**:
    - **Added Sections**: Automatically inserted into the catalog, tagged with a glowing `NEW` badge, and filtered via the `🆕 New Sections` chip.
    - **Removed / Cancelled Sections**: If a department drops or cancels a section on Empower, it is automatically removed from the active catalog so the dashboard always matches live Empower SIS data.

- **Pre-Registration Simulation Tests**:
  - Test both alert types before registration starts on November 23:
    - **🧪 Test Seat Alert**: Simulates a seat opening up in a full section.
    - **🆕 Test New Section**: Simulates a department adding a brand-new section.

- **Personal Course Watchlist**:
  - Click the **★ Star** icon on any course card to add it to your watchlist.
  - Filter by **⭐ Watched Only** to keep an eye on your required graduation courses.

- **Fast Instant Filtering & Search**:
  - Global search by Course Code (e.g., `CSCS 101`), Course Title, Instructor, or Classroom.
  - Department dropdown filter with section counts.
  - Quick status chips: **All**, **🟢 Available (>3)**, **🟡 Low Seats (1-3)**, **🔴 Full (0 seats)**, **⭐ Watched**.
  - Day filters: `M`, `T`, `W`, `R` (Thursday), `F`, `S`.
  - Sort by Course Code, Least Available Seats, Most Available Seats, Department, Title.
  - Switch between **Grid Cards View** and **Compact Table View**.
  - **Export to CSV**: Download the catalog and seat data with 1 click.

- **Auto-Sync & Manual Refresh**:
  - Automatically polls Empower in the background every 30 seconds.
  - Shows live countdown timer: `⏱️ 30s... 20s... 10s`.
  - Manual **"🔄 Sync Now"** button for instant on-demand polling.

---

## 🛠️ Project Structure

```
b:\Course Tracker\
  ├── app\
  │   ├── config.py           # Empower URLs, term definitions, server config
  │   ├── scraper.py          # Scrapes and parses Empower SIS catalog
  │   ├── database.py         # SQLite DB, seat change detection, alert history
  │   ├── tracker.py          # Background polling worker & Server-Sent Events (SSE) broadcaster
  │   ├── api.py              # FastAPI REST endpoints & SSE stream
  │   └── main.py             # FastAPI app with static file mounting
  ├── data\
  │   └── tracker.db          # SQLite persistent database (courses, history, alerts)
  ├── static\
  │   ├── index.html          # Modern, responsive single-page dashboard
  │   ├── app.js              # Real-time state management, audio synthesis, filters
  │   └── styles.css          # Glassmorphic dark theme styling
  ├── requirements.txt        # Python dependencies
  ├── run.py                  # Server entry point
  └── start.bat               # Windows double-click launcher
```

---

## 📡 REST & Real-time API Endpoints

- `GET /api/terms`: Available terms and default term (`2027SP`).
- `GET /api/status`: Scraper health, polling interval, last sync timestamp.
- `GET /api/courses`: Query courses with parameters (`term`, `search`, `dept`, `status`, `watched_only`, `sort_by`).
- `GET /api/stats`: Aggregate seat counts (total courses, capacity, available, enrolled, full).
- `GET /api/departments`: Department list with section counts.
- `GET /api/alerts`: Historical list of seat opened alerts.
- `POST /api/refresh`: Trigger instant live scrape from Empower.
- `POST /api/watchlist/toggle`: Star or unstar a course section.
- `POST /api/simulate`: Simulate a seat opening for alert verification.
- `GET /api/stream`: Server-Sent Events (SSE) stream for instant real-time event push without page refresh.
