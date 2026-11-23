import os
import csv
import io
import json
import time
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, List
from fastapi import APIRouter, Depends, Request, Response, Query, status
from fastapi.responses import HTMLResponse, StreamingResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from app.database import get_db, get_setting_int, get_setting_str
from app.config import settings
from app.models import (
    Admin, AuditLog, Device, DeviceCommand, User, Wallet, Payment,
    PaymentIntent, Ledger, Session as PhotoSession, Media, Setting
)
from app.schemas import (
    AdminLoginBody, AdminAdjustBalanceBody, AdminMatchPaymentBody,
    AdminCreateCommandBody, AdminViewSessionMediaBody
)
from app.security import (
    verify_password, verify_totp, hash_password, gen_ulid,
    mask_phone, sign_media_url, decrypt_str, ensure_utc,
    now_vn, to_utc_iso, to_vn_str, hmac_hash
)
from app.deps import raise_api_error, get_current_admin, require_admin_owner
from app.routers.library_routes import build_signed_media_url

router = APIRouter(tags=["Admin Portal & API"])
TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

def render_template(request: Request, name: str, context: dict = None, status_code: int = 200):
    ctx = context or {}
    ctx.setdefault("request", request)
    try:
        return templates.TemplateResponse(request=request, name=name, context=ctx, status_code=status_code)
    except TypeError:
        return templates.TemplateResponse(name, ctx, status_code=status_code)

def log_audit(
    db: Session,
    actor: str,
    action: str,
    target: Optional[str] = None,
    detail: Optional[dict] = None,
    ip: Optional[str] = None
):
    """Immutable audit trail logging (Section 14 & 15.5)."""
    entry = AuditLog(
        actor=actor,
        action=action,
        target=target,
        detail=detail or {},
        ip=ip,
        created_at=datetime.now(timezone.utc)
    )
    db.add(entry)
    db.commit()

# ----------------- ADMIN AUTH -----------------
@router.post("/api/v1/admin/login")
def admin_login(
    req: AdminLoginBody,
    request: Request,
    response: Response,
    db: Session = Depends(get_db)
):
    admin = db.query(Admin).filter(Admin.username == req.username.strip()).first()
    if not admin or not verify_password(req.password, admin.password_hash):
        log_audit(db, actor=req.username, action="login_failed", detail={"reason": "bad_password"}, ip=request.client.host)
        raise_api_error(status_code=401, code="invalid_credentials", message="Tên đăng nhập hoặc mật khẩu không chính xác")
        
    # Verify TOTP 2FA (mandatory)
    totp_secret = decrypt_str(admin.totp_secret_enc)
    if not totp_secret or not verify_totp(totp_secret, req.totp_code):
        log_audit(db, actor=admin.username, action="login_failed", detail={"reason": "bad_totp"}, ip=request.client.host)
        raise_api_error(status_code=401, code="invalid_totp", message="Mã xác thực 2FA (OTP) không chính xác")
        
    # Create Admin JWT
    import jwt
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=30) # 30 mins inactivity expiry per Section 15.5
    token = jwt.encode({
        "sub": str(admin.id),
        "username": admin.username,
        "role": admin.role,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        "aud": "photobooth_admin"
    }, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    
    # Set HttpOnly, Secure, SameSite Cookie
    response.set_cookie(
        key="admin_session",
        value=token,
        httponly=True,
        secure=not settings.DEBUG,
        samesite="lax",
        max_age=1800
    )
    
    log_audit(db, actor=admin.username, action="login_success", detail={"role": admin.role}, ip=request.client.host)
    return {
        "success": True,
        "token": token,
        "admin": {"id": admin.id, "username": admin.username, "role": admin.role}
    }

@router.post("/api/v1/admin/logout")
def admin_logout(
    response: Response,
    current_admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    response.delete_cookie("admin_session")
    log_audit(db, actor=current_admin.username, action="logout")
    return {"success": True}

# ----------------- ADMIN DASHBOARD STATS -----------------
@router.get("/api/v1/admin/stats")
def get_admin_stats(
    current_admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    # Calculate today in Vietnam timezone (UTC+7)
    vn_now = now_vn()
    vn_today_start = vn_now.replace(hour=0, minute=0, second=0, microsecond=0)
    utc_today_start = vn_today_start.astimezone(timezone.utc)
    utc_week_start = utc_today_start - timedelta(days=7)
    utc_month_start = utc_today_start - timedelta(days=30)
    
    # Total sessions
    total_sessions = db.query(PhotoSession).filter(PhotoSession.status == "completed").count()
    sessions_today = db.query(PhotoSession).filter(
        PhotoSession.status == "completed",
        PhotoSession.completed_at >= utc_today_start
    ).count()
    
    # Liability / Customer deposit balance
    total_liability = db.query(func.coalesce(func.sum(Wallet.balance_vnd), 0)).scalar() or 0
    guest_liability = db.query(func.coalesce(func.sum(Wallet.balance_vnd), 0)).filter(Wallet.kind == "guest").scalar() or 0
    user_liability = db.query(func.coalesce(func.sum(Wallet.balance_vnd), 0)).filter(Wallet.kind == "user").scalar() or 0
    
    # Unmatched SePay payments
    unmatched_count = db.query(Payment).filter(Payment.status == "unmatched").count()
    
    revenue_data = {}
    if current_admin.role == "owner":
        # Owner views full revenue statistics
        rev_today = db.query(func.coalesce(func.sum(Payment.amount_vnd), 0)).filter(
            Payment.status == "credited",
            Payment.received_at >= utc_today_start
        ).scalar() or 0
        
        rev_week = db.query(func.coalesce(func.sum(Payment.amount_vnd), 0)).filter(
            Payment.status == "credited",
            Payment.received_at >= utc_week_start
        ).scalar() or 0
        
        rev_month = db.query(func.coalesce(func.sum(Payment.amount_vnd), 0)).filter(
            Payment.status == "credited",
            Payment.received_at >= utc_month_start
        ).scalar() or 0
        
        total_topup = db.query(func.coalesce(func.sum(Payment.amount_vnd), 0)).filter(
            Payment.status == "credited"
        ).scalar() or 0
        
        # Hourly stats for today (Vietnam 00:00 to 23:00)
        hourly = []
        for h in range(24):
            vn_h_start = vn_today_start + timedelta(hours=h)
            vn_h_end = vn_h_start + timedelta(hours=1)
            utc_h_start = vn_h_start.astimezone(timezone.utc)
            utc_h_end = vn_h_end.astimezone(timezone.utc)
            h_rev = db.query(func.coalesce(func.sum(Payment.amount_vnd), 0)).filter(
                Payment.status == "credited",
                Payment.received_at >= utc_h_start,
                Payment.received_at < utc_h_end
            ).scalar() or 0
            hourly.append({"hour": f"{h:02d}:00", "amount_vnd": int(h_rev)})
            
        revenue_data = {
            "revenue_today_vnd": int(rev_today),
            "revenue_week_vnd": int(rev_week),
            "revenue_month_vnd": int(rev_month),
            "total_topup_vnd": int(total_topup),
            "hourly": hourly
        }
    else:
        # Staff: revenue is hidden (Section 14: "staff... không xem doanh thu")
        revenue_data = {
            "revenue_today_vnd": None,
            "revenue_week_vnd": None,
            "revenue_month_vnd": None,
            "total_topup_vnd": None,
            "hourly": []
        }
        
    return {
        "role": current_admin.role,
        "revenue": revenue_data,
        "sessions": {
            "total": total_sessions,
            "today": sessions_today
        },
        "liability": {
            "total_vnd": int(total_liability),
            "guest_vnd": int(guest_liability),
            "user_vnd": int(user_liability)
        },
        "unmatched_payments_count": unmatched_count
    }

# ----------------- CSV EXPORT -----------------
@router.get("/api/v1/admin/export/csv")
def export_financial_csv(
    current_admin: Admin = Depends(require_admin_owner),
    db: Session = Depends(get_db)
):
    """Owner exports financial ledger to CSV."""
    rows = db.query(Ledger).order_by(Ledger.id.desc()).limit(2000).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Wallet ID", "Delta VND", "Delta Held VND", "Balance After VND", "Held After VND", "Reason", "Ref ID", "Actor", "Created At"])
    
    for r in rows:
        writer.writerow([
            r.id, r.wallet_id, r.delta_balance_vnd, r.delta_held_vnd,
            r.balance_after_vnd, r.held_after_vnd, r.reason, r.ref_id, r.actor,
            r.created_at.isoformat()
        ])
        
    output.seek(0)
    log_audit(db, actor=current_admin.username, action="export_csv")
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8-sig")),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=photobooth_ledger_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"}
    )

# ----------------- DEVICES MANAGEMENT -----------------
@router.get("/api/v1/admin/devices")
def list_admin_devices(
    current_admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    devices = db.query(Device).all()
    now = datetime.now(timezone.utc)
    res = []
    for d in devices:
        is_online = bool(d.last_seen and (now - ensure_utc(d.last_seen)).total_seconds() < 90)
        res.append({
            "id": d.id,
            "name": d.name or d.id,
            "mode": d.mode,
            "is_online": is_online,
            "last_seen": to_utc_iso(d.last_seen),
            "cpu_temp": d.cpu_temp,
            "disk_free_mb": d.disk_free_mb,
            "camera_ok": d.camera_ok,
            "app_version": d.app_version
        })
    return {"devices": res}

@router.post("/api/v1/admin/devices/{device_id}/mode")
def set_device_mode(
    device_id: str,
    mode: str = Query(..., pattern="^(online|maintenance)$"),
    current_admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    dev = db.query(Device).filter(Device.id == device_id).first()
    if not dev:
        raise_api_error(status_code=404, code="not_found", message="Máy không tồn tại")
    dev.mode = mode
    db.commit()
    log_audit(db, actor=current_admin.username, action="set_device_mode", target=device_id, detail={"mode": mode})
    return {"id": dev.id, "mode": dev.mode}

@router.post("/api/v1/admin/devices/{device_id}/commands")
def send_device_command(
    device_id: str,
    req: AdminCreateCommandBody,
    current_admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    dev = db.query(Device).filter(Device.id == device_id).first()
    if not dev:
        raise_api_error(status_code=404, code="not_found", message="Máy không tồn tại")
        
    cmd = DeviceCommand(
        id=gen_ulid("cmd_"),
        device_id=dev.id,
        type=req.command_type,
        params=req.params or {},
        status="pending",
        created_at=datetime.now(timezone.utc)
    )
    db.add(cmd)
    db.commit()
    log_audit(db, actor=current_admin.username, action="send_command", target=device_id, detail={"command": req.command_type})
    return {"id": cmd.id, "type": cmd.type, "status": "pending"}

# ----------------- SETTINGS MANAGEMENT -----------------
@router.get("/api/v1/admin/settings")
def get_all_settings(
    current_admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    settings_rows = db.query(Setting).all()
    return {s.key: s.value for s in settings_rows}

@router.patch("/api/v1/admin/settings")
def update_settings(
    updates: dict,
    current_admin: Admin = Depends(require_admin_owner),
    db: Session = Depends(get_db)
):
    for k, v in updates.items():
        rec = db.query(Setting).filter(Setting.key == k).first()
        if rec:
            rec.value = str(v)
        else:
            db.add(Setting(key=k, value=str(v)))
    db.commit()
    log_audit(db, actor=current_admin.username, action="update_settings", detail=updates)
    return {"success": True, "updated": updates}

# ----------------- USER MANAGEMENT -----------------
@router.get("/api/v1/admin/users")
def list_admin_users(
    query: Optional[str] = None,
    limit: int = Query(20, ge=1, le=100),
    current_admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    q = db.query(User)
    if query:
        q = q.filter(User.id.ilike(f"%{query}%") | User.display_name.ilike(f"%{query}%"))
    users = q.order_by(User.created_at.desc()).limit(limit).all()
    
    items = []
    for u in users:
        wallet = u.wallet
        bal = wallet.balance_vnd if wallet else 0
        items.append({
            "id": u.id,
            "display_name": u.display_name,
            "status": u.status,
            "phone_masked": "+84•••••" + u.id[-4:],
            "balance_vnd": bal,
            "created_at": to_utc_iso(u.created_at),
            "last_login_at": to_utc_iso(u.last_login_at)
        })
    return {"users": items}

@router.post("/api/v1/admin/users/{user_id}/adjust")
def admin_adjust_user_balance(
    user_id: str,
    req: AdminAdjustBalanceBody,
    current_admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Manual wallet balance adjustment (requires mandatory reason)."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise_api_error(status_code=404, code="not_found", message="Người dùng không tồn tại")
        
    wallet = user.wallet
    if not wallet:
        raise_api_error(status_code=404, code="not_found", message="Ví không tồn tại")
        
    if not req.reason.strip():
        raise_api_error(status_code=400, code="reason_required", message="Bắt buộc phải ghi rõ lý do điều chỉnh số dư")
        
    now = datetime.now(timezone.utc)
    new_bal = wallet.balance_vnd + req.delta_vnd
    if new_bal < 0:
        raise_api_error(status_code=400, code="invalid_balance", message="Số dư sau điều chỉnh không thể âm")
        
    wallet.balance_vnd = new_bal
    
    led = Ledger(
        wallet_id=wallet.id,
        delta_balance_vnd=req.delta_vnd,
        delta_held_vnd=0,
        balance_after_vnd=wallet.balance_vnd,
        held_after_vnd=wallet.held_vnd,
        reason="admin_adjust",
        ref_id=f"adj_{current_admin.username}",
        actor=f"admin:{current_admin.username}",
        created_at=now
    )
    db.add(led)
    db.commit()
    
    log_audit(
        db,
        actor=current_admin.username,
        action="adjust_balance",
        target=user.id,
        detail={"delta_vnd": req.delta_vnd, "new_balance": new_bal, "reason": req.reason}
    )
    return {"success": True, "new_balance_vnd": new_bal}

@router.post("/api/v1/admin/users/{user_id}/status")
def admin_set_user_status(
    user_id: str,
    status_val: str = Query(..., pattern="^(active|locked)$"),
    current_admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise_api_error(status_code=404, code="not_found", message="Người dùng không tồn tại")
    user.status = status_val
    db.commit()
    log_audit(db, actor=current_admin.username, action="set_user_status", target=user_id, detail={"status": status_val})
    return {"id": user.id, "status": user.status}

# ----------------- UNMATCHED PAYMENTS -----------------
@router.get("/api/v1/admin/payments/unmatched")
def list_unmatched_payments(
    current_admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    pays = db.query(Payment).filter(Payment.status == "unmatched").order_by(Payment.id.desc()).all()
    items = []
    for p in pays:
        items.append({
            "id": p.id,
            "provider_tx_id": p.provider_tx_id,
            "amount_vnd": p.amount_vnd,
            "content": p.content,
            "reference_code": p.reference_code,
            "received_at": to_utc_iso(p.received_at)
        })
    return {"unmatched": items}

@router.post("/api/v1/admin/payments/{payment_id}/match")
def match_unmatched_payment(
    payment_id: int,
    req: AdminMatchPaymentBody,
    current_admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    pay = db.query(Payment).filter(Payment.id == payment_id, Payment.status == "unmatched").first()
    if not pay:
        raise_api_error(status_code=404, code="not_found", message="Giao dịch không tồn tại hoặc đã được xử lý")
        
    wallet = None
    target = (req.user_id or req.wallet_code or "").strip()
    if target:
        if "@" in target:
            # Match by email
            user = db.query(User).filter(User.email_h == hmac_hash(target.lower())).first()
            if user and user.wallet:
                wallet = user.wallet
        elif target.startswith("wal_"):
            wallet = db.query(Wallet).filter(Wallet.id == target).first()
        elif target.startswith("usr_"):
            user = db.query(User).filter(User.id == target).first()
            if user and user.wallet:
                wallet = user.wallet
        else:
            # Fallback checks
            wallet = db.query(Wallet).filter(Wallet.user_id == target).first()
            if not wallet:
                wallet = db.query(Wallet).filter(Wallet.id == target).first()
            if not wallet and req.wallet_code:
                wallet = db.query(Wallet).filter(Wallet.code == req.wallet_code.strip().upper()).first()
        
    if not wallet:
        raise_api_error(status_code=404, code="not_found", message="Không tìm thấy ví tương ứng (hãy nhập đúng Email hoặc User ID)")
        
    now = datetime.now(timezone.utc)
    wallet.balance_vnd += pay.amount_vnd
    pay.status = "credited"
    pay.wallet_id = wallet.id
    pay.matched_kind = "manual_admin"
    
    led = Ledger(
        wallet_id=wallet.id,
        delta_balance_vnd=pay.amount_vnd,
        delta_held_vnd=0,
        balance_after_vnd=wallet.balance_vnd,
        held_after_vnd=wallet.held_vnd,
        reason="topup",
        ref_id=f"manual_pay_{pay.id}",
        actor=f"admin:{current_admin.username}",
        created_at=now
    )
    db.add(led)
    db.commit()
    
    log_audit(
        db,
        actor=current_admin.username,
        action="manual_match_payment",
        target=str(pay.id),
        detail={"wallet_id": wallet.id, "amount_vnd": pay.amount_vnd, "note": req.note}
    )
    return {"success": True, "wallet_id": wallet.id, "amount_credited_vnd": pay.amount_vnd}

# ----------------- TRANSACTIONS LIST -----------------
@router.get("/api/v1/admin/transactions")
def list_transactions(
    limit: int = Query(50, ge=1, le=200),
    current_admin: Admin = Depends(require_admin_owner),
    db: Session = Depends(get_db)
):
    """Owner views all payment transactions with type annotation."""
    pays = db.query(Payment).order_by(Payment.id.desc()).limit(limit).all()
    items = []
    for p in pays:
        # Determine transaction type for display
        if p.matched_kind == "guest_wallet":
            tx_type = "topup_guest"
            tx_label = "Nạp ví khách (QR tại máy)"
        elif p.matched_kind == "intent":
            # Look up the intent to know if it's direct_pay or topup
            intent = p.intent
            if intent and intent.kind == "direct_pay":
                tx_type = "direct_pay"
                tx_label = "Thanh toán trực tiếp (CK thẳng)"
            else:
                tx_type = "topup_user"
                tx_label = "Nạp ví tài khoản App"
        elif p.status == "unmatched":
            tx_type = "unmatched"
            tx_label = "Chuyển khoản không khớp mã"
        elif p.status == "ignored":
            tx_type = "ignored"
            tx_label = "Bỏ qua (không xử lý)"
        else:
            tx_type = "credited"
            tx_label = "Đã ghi có"

        items.append({
            "id": p.id,
            "provider_tx_id": p.provider_tx_id,
            "reference_code": p.reference_code,
            "amount_vnd": p.amount_vnd,
            "content": p.content,
            "status": p.status,
            "matched_kind": p.matched_kind,
            "tx_type": tx_type,
            "tx_label": tx_label,
            "wallet_id": p.wallet_id,
            "received_at": to_utc_iso(p.received_at)
        })
    return {"transactions": items, "total": len(items)}

# ----------------- PHOTOS & AUDIT TRAIL -----------------
@router.get("/api/v1/admin/sessions")
def list_admin_sessions(
    limit: int = Query(50, ge=1, le=200),
    current_admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    sessions = db.query(PhotoSession).order_by(PhotoSession.created_at.desc()).limit(limit).all()
    items = []
    for s in sessions:
        media_cnt = db.query(Media).filter(Media.session_id == s.id, Media.deleted_at == None).count()
        bytes_total = db.query(func.coalesce(func.sum(Media.bytes), 0)).filter(Media.session_id == s.id, Media.deleted_at == None).scalar() or 0
        share_url = f"{settings.BASE_URL}/d/{s.share_token}" if s.share_token else None
        
        user_name = s.user.display_name if s.user else None
        user_email = None
        if s.user:
            for ident in s.user.identities:
                if ident.email_enc:
                    try:
                        user_email = decrypt_str(ident.email_enc)
                        break
                    except Exception:
                        pass
                elif ident.provider == "email" and ident.provider_uid_enc:
                    try:
                        user_email = decrypt_str(ident.provider_uid_enc)
                        break
                    except Exception:
                        pass

        items.append({
            "id": s.id,
            "device_id": s.device_id,
            "status": s.status,
            "owner_type": "user" if s.user_id else "guest",
            "user_id": s.user_id,
            "user_name": user_name,
            "user_email": user_email,
            "media_count": media_cnt,
            "bytes": int(bytes_total),
            "created_at": to_utc_iso(s.created_at),
            "deleted": bool(s.deleted_at),
            "share_token": s.share_token,
            "share_url": share_url,
            "share_expires_at": to_utc_iso(s.share_expires_at) if s.share_expires_at else None
        })
    return {"sessions": items}

@router.post("/api/v1/admin/sessions/{session_id}/view")
def view_admin_session_media(
    session_id: str,
    req: AdminViewSessionMediaBody,
    request: Request,
    current_admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    session = db.query(PhotoSession).filter(PhotoSession.id == session_id).first()
    if not session:
        raise_api_error(status_code=404, code="not_found", message="Phiên chụp không tồn tại")
        
    reason_clean = (req.reason or "").strip()
    if not reason_clean:
        raise_api_error(status_code=400, code="invalid_request", message="Cần nhập lý do nghiệp vụ theo Luật Bảo vệ dữ liệu cá nhân")
        
    # Ghi nhận vào nhật ký kiểm toán (Audit Trail)
    log_audit(
        db,
        actor=current_admin.username,
        action="view_customer_photos",
        target=session_id,
        detail={"reason": reason_clean},
        ip=request.client.host if request.client else None
    )
    
    media_items = db.query(Media).filter(Media.session_id == session.id, Media.deleted_at == None).all()
    
    now_ts = int(time.time())
    exp_ts = now_ts + (60 * 60) # 60 phút xem ảnh dành cho admin
    
    items = []
    for m in sorted(media_items, key=lambda x: (x.kind != "strip", x.idx or 0)):
        sig = sign_media_url(m.id, exp_ts)
        url = f"{settings.MEDIA_BASE_URL}/media/m/{m.id}?exp={exp_ts}&sig={sig}"
        dl_url = f"{url}&download=1"
        items.append({
            "id": m.id,
            "kind": m.kind,
            "index": m.idx,
            "content_type": m.content_type,
            "bytes": m.bytes,
            "url": url,
            "download_url": dl_url
        })
        
    share_url = f"{settings.BASE_URL}/d/{session.share_token}" if session.share_token else None
    
    return {
        "session_id": session.id,
        "device_id": session.device_id,
        "status": session.status,
        "owner_type": "user" if session.user_id else "guest",
        "created_at": to_utc_iso(session.created_at),
        "share_token": session.share_token,
        "share_url": share_url,
        "share_expires_at": to_utc_iso(session.share_expires_at) if session.share_expires_at else None,
        "media": items
    }

@router.post("/api/v1/admin/sessions/{session_id}/extend-link")
def extend_admin_session_link(
    session_id: str,
    current_admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    session = db.query(PhotoSession).filter(PhotoSession.id == session_id).first()
    if not session:
        raise_api_error(status_code=404, code="not_found", message="Phiên chụp không tồn tại")
        
    now = datetime.now(timezone.utc)
    if not session.share_token:
        session.share_token = secrets.token_urlsafe(24)
        
    session.share_expires_at = now + timedelta(hours=24)
    session.deleted_at = None
    db.commit()
    
    log_audit(
        db,
        actor=current_admin.username,
        action="extend_share_link",
        target=session_id,
        detail={"new_share_token": session.share_token, "expires_at": to_utc_iso(session.share_expires_at)}
    )
    
    share_url = f"{settings.BASE_URL}/d/{session.share_token}"
    return {
        "success": True,
        "share_url": share_url,
        "expires_at": to_utc_iso(session.share_expires_at)
    }


@router.get("/api/v1/admin/audit-logs")
def list_audit_logs(
    limit: int = Query(50, ge=1, le=200),
    current_admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    logs = db.query(AuditLog).order_by(AuditLog.id.desc()).limit(limit).all()
    items = []
    for l in logs:
        items.append({
            "id": l.id,
            "actor": l.actor,
            "action": l.action,
            "target": l.target,
            "detail": l.detail,
            "ip": l.ip,
            "created_at": to_utc_iso(l.created_at)
        })
    return {"logs": items}

# ----------------- HTML PAGES -----------------
@router.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(request: Request):
    return render_template(request, "admin_login.html")

@router.get("/admin", response_class=HTMLResponse)
def admin_dashboard_page(request: Request):
    token = request.cookies.get("admin_session")
    if not token:
        return RedirectResponse("/admin/login", status_code=303)
    try:
        import jwt
        jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM], audience="photobooth_admin")
    except Exception:
        return RedirectResponse("/admin/login", status_code=303)
    return render_template(request, "admin_dash.html")
