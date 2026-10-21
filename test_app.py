import sys
from app.config import DEFAULT_TERM
from app.database import init_db, get_courses, get_stats, get_departments
from app.scraper import EmpowerScraper

print("Testing database initialization...")
init_db()
print("Database initialized successfully.")

stats = get_stats(DEFAULT_TERM)
print("Initial stats for", DEFAULT_TERM, ":", stats)

depts = get_departments(DEFAULT_TERM)
print(f"Departments found in DB: {len(depts)}")

courses = get_courses(DEFAULT_TERM)
print(f"Courses found in DB: {len(courses)}")

print("All basic module tests passed!")
