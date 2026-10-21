import uvicorn
from app.config import SERVER_HOST, SERVER_PORT

if __name__ == "__main__":
    print("=" * 60)
    print("   FCCU Spring 2027 Course & Seat Tracker")
    print(f"   Server running at: http://{SERVER_HOST}:{SERVER_PORT}")
    print("=" * 60)
    uvicorn.run("app.main:app", host=SERVER_HOST, port=SERVER_PORT, reload=False)
