import os
import sys
import uvicorn

# Ensure server directory is in sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    reload = os.getenv("RELOAD", "True").lower() in ("true", "1")
    print(f"[*] Starting Photobooth Web Server on http://{host}:{port} ...")
    uvicorn.run("app.main:app", host=host, port=port, reload=reload)
