import os
import time
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, Request, Query, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.database import get_db, get_setting_int
from app.config import settings
from app.models import Session as PhotoSession, Media, Wallet
from app.security import sign_media_url, ensure_utc, to_utc_iso, to_vn_str, gen_code
from app.vietqr import generate_qr_data_url
from app.deps import raise_api_error

router = APIRouter(tags=["Guest Web"])
TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

def render_template(request: Request, name: str, context: dict = None, status_code: int = 200):
    ctx = context or {}
    ctx.setdefault("request", request)
    try:
        return templates.TemplateResponse(request=request, name=name, context=ctx, status_code=status_code)
    except TypeError:
        return templates.TemplateResponse(name, ctx, status_code=status_code)

@router.get("/shares/{share_token}")
def get_shared_session_json(
    share_token: str,
    db: Session = Depends(get_db)
):
    now = datetime.now(timezone.utc)
    session = db.query(PhotoSession).filter(
        PhotoSession.share_token == share_token,
        PhotoSession.status == "completed",
        PhotoSession.deleted_at == None
    ).first()
    
    if not session or (session.share_expires_at and ensure_utc(session.share_expires_at) < now):
        raise_api_error(status_code=404, code="not_found", message="Liên kết tải ảnh không tồn tại hoặc đã hết hạn")
        
    media_list = db.query(Media).filter(Media.session_id == session.id, Media.deleted_at == None).all()
    
    media_items = []
    now_ts = int(time.time())
    exp_ts = now_ts + (15 * 60)
    for m in media_list:
        sig = sign_media_url(m.id, exp_ts)
        url = f"{settings.MEDIA_BASE_URL}/media/m/{m.id}?exp={exp_ts}&sig={sig}"
        dl_url = f"{url}&download=1"
        media_items.append({
            "id": m.id,
            "kind": m.kind,
            "index": m.idx,
            "content_type": m.content_type,
            "bytes": m.bytes,
            "url": url,
            "download_url": dl_url
        })
        
    remaining_seconds = int((ensure_utc(session.share_expires_at) - now).total_seconds()) if session.share_expires_at else 0
    
    return {
        "session_id": session.id,
        "layout": session.layout,
        "filter": session.filter,
        "frame_color": session.frame_color,
        "color_filter": session.color_filter,
        "created_at": ensure_utc(session.created_at).isoformat().replace("+00:00", "Z") if session.created_at else None,
        "expires_at": ensure_utc(session.share_expires_at).isoformat().replace("+00:00", "Z") if session.share_expires_at else None,
        "remaining_seconds": max(0, remaining_seconds),
        "media": media_items
    }

@router.get("/d/{share_token}", response_class=HTMLResponse)
def render_guest_share_page(
    share_token: str,
    request: Request,
    db: Session = Depends(get_db)
):
    now = datetime.now(timezone.utc)
    session = db.query(PhotoSession).filter(
        PhotoSession.share_token == share_token,
        PhotoSession.status == "completed",
        PhotoSession.deleted_at == None
    ).first()
    
    if not session or (session.share_expires_at and ensure_utc(session.share_expires_at) < now):
        return render_template(request, "guest_expired.html", status_code=404)
        
    media_list = db.query(Media).filter(Media.session_id == session.id, Media.deleted_at == None).all()
    
    now_ts = int(time.time())
    exp_ts = now_ts + (30 * 60) # 30 mins signed URL
    
    strip_media = None
    shots = []
    video_media = None
    
    for m in sorted(media_list, key=lambda x: (x.kind != "strip", x.idx or 0)):
        sig = sign_media_url(m.id, exp_ts)
        url = f"{settings.MEDIA_BASE_URL}/media/m/{m.id}?exp={exp_ts}&sig={sig}"
        dl_url = f"{url}&download=1"
        item = {
            "id": m.id,
            "kind": m.kind,
            "index": m.idx,
            "url": url,
            "download_url": dl_url,
            "bytes": m.bytes,
            "content_type": m.content_type
        }
        if m.kind == "strip":
            strip_media = item
        elif m.kind == "shot":
            shots.append(item)
        elif m.kind == "video":
            video_media = item
            
    remaining_seconds = int((ensure_utc(session.share_expires_at) - now).total_seconds()) if session.share_expires_at else 0
    
    return render_template(request, "guest_share.html", {
        "session": session,
        "strip": strip_media,
        "shots": shots,
        "video": video_media,
        "remaining_seconds": max(0, remaining_seconds),
        "expires_at_iso": to_vn_str(session.share_expires_at, "%H:%M %d/%m/%Y"),
        "terms_url": settings.TERMS_URL,
        "privacy_url": settings.PRIVACY_URL
    })

@router.get("/api/v1/wallets/{code}")
def get_guest_wallet(
    code: str,
    db: Session = Depends(get_db)
):
    code_clean = code.strip().upper()
    wallet = db.query(Wallet).filter(
        (Wallet.code == code_clean) | (Wallet.id == code_clean)
    ).first()
    
    if not wallet:
        raise_api_error(status_code=404, code="not_found", message="Mã ví không tồn tại")
        
    price = get_setting_int(db, "price_per_session_vnd", settings.DEFAULT_PRICE_PER_SESSION_VND)
    avail = wallet.balance_vnd - wallet.held_vnd
    
    # Check if there is any completed photo session for this wallet
    latest_sess = db.query(PhotoSession).filter(
        PhotoSession.wallet_id == wallet.id,
        PhotoSession.status == "completed",
        PhotoSession.deleted_at == None
    ).order_by(PhotoSession.completed_at.desc()).first()
    
    latest_session_info = None
    if latest_sess and latest_sess.share_token:
        latest_session_info = {
            "session_id": latest_sess.id,
            "share_token": latest_sess.share_token,
            "share_url": f"/d/{latest_sess.share_token}",
            "completed_at": to_utc_iso(latest_sess.completed_at)
        }
        
    display_code = wallet.code or wallet.id
    wallet_qr = generate_qr_data_url(display_code)
    
    return {
        "wallet_code": display_code,
        "balance_vnd": wallet.balance_vnd,
        "held_vnd": wallet.held_vnd,
        "available_vnd": avail,
        "sessions_available": avail // price if price > 0 else 0,
        "leftover_vnd": avail % price if price > 0 else 0,
        "latest_session": latest_session_info,
        "wallet_qr_url": wallet_qr
    }

@router.get("/guest", response_class=HTMLResponse)
def render_guest_portal(
    request: Request,
    db: Session = Depends(get_db)
):
    price = get_setting_int(db, "price_per_session_vnd", settings.DEFAULT_PRICE_PER_SESSION_VND)
    return render_template(request, "guest_wallet.html", {
        "price_per_session": price,
        "bank_name": settings.BANK_NAME,
        "account_number": settings.ACCOUNT_NUMBER,
        "account_name": settings.ACCOUNT_NAME
    })
