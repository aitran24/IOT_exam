import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException, status
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.database import init_db
from app.worker import worker_loop
from app.security import gen_ulid

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("server")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize Database tables and settings
    logger.info("Initializing Database...")
    init_db()
    
    # Launch background worker
    worker_task = asyncio.create_task(worker_loop(interval_seconds=60))
    logger.info("Photobooth Server 2.0 started successfully.")
    yield
    # Shutdown
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass
    logger.info("Photobooth Server shut down cleanly.")

app = FastAPI(
    title="Photobooth Web Server API",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# ----------------- SECURITY & CORS MIDDLEWARE -----------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # In production, restrict to app domains
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    # Apply OWASP recommended security headers (Section 15.6)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if not settings.DEBUG:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self' https: data: 'unsafe-inline' 'unsafe-eval'; "
            "img-src 'self' data: https: blob:; "
            "media-src 'self' data: https: blob:;"
        )
    return response

# ----------------- UNIFORM ERROR ENVELOPE (SECTION 9) -----------------
@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    # If already formatted according to the spec
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail, headers=exc.headers)
    
    # Map common status codes to default codes
    code_map = {
        400: "invalid_request",
        401: "unauthorized",
        402: "insufficient_balance",
        403: "forbidden",
        404: "not_found",
        409: "conflict",
        410: "expired",
        413: "too_large",
        422: "invalid_data",
        423: "locked",
        429: "rate_limited",
        500: "internal_error",
        503: "maintenance"
    }
    status_code = exc.status_code
    code = code_map.get(status_code, "error")
    request_id = gen_ulid("req_")
    
    content = {
        "error": {
            "code": code,
            "message": str(exc.detail) if exc.detail else "Đã xảy ra lỗi",
            "details": {},
            "request_id": request_id
        }
    }
    return JSONResponse(status_code=status_code, content=content, headers=exc.headers)

@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.exception(f"Unhandled exception on {request.url.path}: {exc}")
    request_id = gen_ulid("req_")
    content = {
        "error": {
            "code": "internal_error",
            "message": "Lỗi máy chủ nội bộ. Vui lòng thử lại sau.",
            "details": {},
            "request_id": request_id
        }
    }
    return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content=content)

# ----------------- MOUNT STATIC & ROUTERS -----------------
import os
APP_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(APP_DIR, "static")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

from app.routers import (
    config_routes,
    auth_routes,
    user_routes,
    wallet_routes,
    pairing_routes,
    library_routes,
    event_routes,
    device_routes,
    sepay_routes,
    guest_routes,
    media_routes,
    admin_routes
)

app.include_router(config_routes.router)
app.include_router(auth_routes.router)
app.include_router(user_routes.router)
app.include_router(wallet_routes.router)
app.include_router(pairing_routes.router)
app.include_router(library_routes.router)
app.include_router(event_routes.router)
app.include_router(device_routes.router)
app.include_router(sepay_routes.router)
app.include_router(media_routes.router)
app.include_router(guest_routes.router)
app.include_router(admin_routes.router)

@app.get("/")
def root_redirect():
    return RedirectResponse(url="/admin")
