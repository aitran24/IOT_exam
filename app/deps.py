import time
import secrets
from typing import Optional, Dict, Any
from fastapi import Request, Depends, HTTPException, Header, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from app.database import get_db
from app.config import settings
from app.security import (
    decode_access_token,
    hmac_hash,
    gen_ulid,
    PEPPER_KEY
)
from app.models import User, Device, Admin

# Rate limiter tracking in-memory: key -> list of timestamps
_rate_limit_buckets: Dict[str, list] = {}

def raise_api_error(
    status_code: int,
    code: str,
    message: str,
    details: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None
):
    request_id = gen_ulid("req_")
    body = {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "request_id": request_id
        }
    }
    raise HTTPException(status_code=status_code, detail=body, headers=headers)

def check_rate_limit(key: str, max_calls: int, period_seconds: int):
    """
    In-memory rate limiter with sliding window.
    Throws 429 rate_limited with Retry-After if exceeded.
    """
    now = time.time()
    timestamps = _rate_limit_buckets.setdefault(key, [])
    # purge old timestamps
    timestamps = [ts for ts in timestamps if now - ts < period_seconds]
    _rate_limit_buckets[key] = timestamps
    
    if len(timestamps) >= max_calls:
        oldest = timestamps[0]
        retry_after = int(period_seconds - (now - oldest)) + 1
        raise_api_error(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            code="rate_limited",
            message="Quá nhiều yêu cầu. Vui lòng thử lại sau.",
            details={"retry_after_s": retry_after},
            headers={"Retry-After": str(retry_after)}
        )
    timestamps.append(now)

def get_current_user(
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
) -> User:
    """Validate Bearer access_token and return active user."""
    if not authorization or not authorization.startswith("Bearer "):
        raise_api_error(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="unauthorized",
            message="Thiếu hoặc sai access token"
        )
    token = authorization.split(" ")[1].strip()
    try:
        payload = decode_access_token(token)
    except Exception as e:
        # Check if expired
        err_msg = str(e).lower()
        if "expired" in err_msg:
            raise_api_error(
                status_code=status.HTTP_401_UNAUTHORIZED,
                code="token_expired",
                message="Access token đã hết hạn, vui lòng dùng refresh token"
            )
        raise_api_error(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="unauthorized",
            message="Token không hợp lệ"
        )
        
    user_id = payload.get("sub")
    if not user_id:
        raise_api_error(status_code=401, code="unauthorized", message="Token không chứa user_id")
        
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise_api_error(status_code=404, code="not_found", message="Người dùng không tồn tại")
        
    if user.status == "locked":
        raise_api_error(status_code=423, code="locked", message="Tài khoản đang bị khóa")
        
    return user

def get_current_device(
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
) -> Device:
    """Validate Bearer device_token for Raspberry Pi (/device/* endpoints)."""
    if not authorization or not authorization.startswith("Bearer "):
        raise_api_error(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="unauthorized",
            message="Thiếu hoặc sai device token"
        )
    token = authorization.split(" ")[1].strip()
    tok_hash = hmac_hash(token)
    
    device = db.query(Device).filter(Device.token_hash == tok_hash).first()
    if not device:
        raise_api_error(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="unauthorized",
            message="Device token không hợp lệ"
        )
    return device

def get_current_admin(
    request: Request,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
) -> Admin:
    """Admin auth via Authorization header or session cookie."""
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ")[1].strip()
    elif "admin_session" in request.cookies:
        token = request.cookies.get("admin_session")
        
    if not token:
        raise_api_error(status_code=401, code="unauthorized", message="Cần đăng nhập quản trị")
        
    try:
        # We store admin JWT with aud='photobooth_admin'
        import jwt
        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
            audience="photobooth_admin"
        )
    except Exception:
        raise_api_error(status_code=401, code="unauthorized", message="Phiên đăng nhập quản trị không hợp lệ hoặc đã hết hạn")
        
    admin_id = payload.get("sub")
    if admin_id is None:
        raise_api_error(status_code=401, code="unauthorized", message="Token quản trị không hợp lệ")
    try:
        admin_id_int = int(admin_id)
    except (ValueError, TypeError):
        admin_id_int = admin_id
        
    admin = db.query(Admin).filter(Admin.id == admin_id_int).first()
    if not admin:
        raise_api_error(status_code=401, code="unauthorized", message="Tài khoản quản trị không tồn tại")
    return admin

def require_admin_owner(
    current_admin: Admin = Depends(get_current_admin)
) -> Admin:
    if current_admin.role != "owner":
        raise_api_error(
            status_code=status.HTTP_403_FORBIDDEN,
            code="forbidden",
            message="Chỉ owner mới có quyền thực hiện thao tác này"
        )
    return current_admin
