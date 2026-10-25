import logging
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from app.database import init_db
from app.tracker import tracker_manager
from app.api import router as api_router
from app.config import BASE_DIR, SERVER_HOST, SERVER_PORT

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("fccu.main")

@asynccontextmanager
async def lifespan(app: FastAPI):
    import os
    # Startup:
    logger.info("Initializing database...")
    init_db()
    if not os.environ.get("VERCEL"):
        logger.info("Starting background tracker...")
        tracker_manager.start()
    yield
    # Shutdown:
    if not os.environ.get("VERCEL"):
        logger.info("Stopping background tracker...")
        tracker_manager.stop()

app = FastAPI(
    title="FCCU Course & Seat Tracker",
    description="Real-time seat availability and alert system for FCCU Spring 2027 Course Registration",
    version="1.0.0",
    lifespan=lifespan
)

# Enable CORS for local dev / client flexibility
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API endpoints
app.include_router(api_router)

# Mount static folder
static_dir = BASE_DIR / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

@app.get("/ping")
@app.head("/ping")
async def health_ping():
    """Ultra-lightweight ping endpoint for uptime monitors and cron-job.org."""
    return {"status": "ok"}

@app.get("/")

async def serve_index():
    index_file = static_dir / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return {"message": "FCCU Course Tracker API is live. Place index.html in /static."}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=SERVER_HOST, port=SERVER_PORT, reload=True)
