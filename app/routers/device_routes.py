import os
import asyncio
import hashlib
import time
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, Header, Request, Query, status, Body
from sqlalchemy.orm import Session
from app.database import get_db, get_setting_int
from app.config import settings
from app.models import (
    Device, DeviceCommand, PairingCode, Pairing, Wallet, PaymentIntent,
    Session as PhotoSession, Media, Ledger, User
)
from app.schemas import (
    HeartbeatBody, HeartbeatResponse, DeviceCommandAck,
    PairingCodeResponse, DeviceVerifyBody, DeviceSessionCreateBody,
    DeviceSessionCompleteBody, DeviceSessionCancelBody
)
from app.security import (
    gen_ulid, gen_code, hmac_hash, ensure_utc
)
from app.vietqr import build_vietqr_payload
from app.deps import raise_api_error, get_current_device
from app.routers.event_routes import broadcast_user_event

router = APIRouter(prefix="/api/v1/device", tags=["Device (Raspberry Pi)"])

@router.post("/heartbeat", response_model=HeartbeatResponse)
def device_heartbeat(
    req: HeartbeatBody,
    device: Device = Depends(get_current_device),
    db: Session = Depends(get_db)
):
    now = datetime.now(timezone.utc)
    device.last_seen = now
    device.cpu_temp = req.cpu_temp_c
    device.disk_free_mb = req.disk_free_mb
    device.camera_ok = req.camera_ok
    device.app_version = req.app_version
    db.commit()
    
    price = get_setting_int(db, "price_per_session_vnd", settings.DEFAULT_PRICE_PER_SESSION_VND)
    shots = get_setting_int(db, "shots", settings.DEFAULT_SHOTS)
    first_s = get_setting_int(db, "countdown_first_s", settings.DEFAULT_COUNTDOWN_FIRST_S)
    next_s = get_setting_int(db, "countdown_next_s", settings.DEFAULT_COUNTDOWN_NEXT_S)
    max_sess = get_setting_int(db, "max_sessions_per_pairing", settings.DEFAULT_MAX_SESSIONS_PER_PAIRING)
    
    return HeartbeatResponse(
        server_time=now.isoformat().replace("+00:00", "Z"),
        mode=device.mode,
        config={
            "price_per_session_vnd": price,
            "shots": shots,
            "countdown_first_s": first_s,
            "countdown_next_s": next_s,
            "max_sessions_per_pairing": max_sess
        }
    )

@router.get("/commands")
async def get_device_commands(
    wait: int = Query(0, ge=0, le=25),
    device: Device = Depends(get_current_device),
    db: Session = Depends(get_db)
):
    start_time = time.time()
    while True:
        cmds = db.query(DeviceCommand).filter(
            DeviceCommand.device_id == device.id,
            DeviceCommand.status == "pending"
        ).all()
        if cmds:
            result = [{"id": c.id, "type": c.type, "params": c.params} for c in cmds]
            return {"commands": result}
            
        elapsed = time.time() - start_time
        if elapsed >= wait or wait == 0:
            return {"commands": []}
            
        await asyncio.sleep(1.0)
        db.expire_all()

@router.post("/commands/{cmd_id}/ack", status_code=status.HTTP_204_NO_CONTENT)
def ack_device_command(
    cmd_id: str,
    body: DeviceCommandAck,
    device: Device = Depends(get_current_device),
    db: Session = Depends(get_db)
):
    cmd = db.query(DeviceCommand).filter(
        DeviceCommand.id == cmd_id,
        DeviceCommand.device_id == device.id
    ).first()
    if cmd:
        cmd.status = "acked"
        cmd.acked_at = datetime.now(timezone.utc)
        db.commit()
    return None

@router.post("/pairing-codes", response_model=PairingCodeResponse)
def create_pairing_code(
    device: Device = Depends(get_current_device),
    db: Session = Depends(get_db)
):
    # Expire old waiting codes for this device
    now = datetime.now(timezone.utc)
    db.query(PairingCode).filter(
        PairingCode.device_id == device.id,
        PairingCode.status == "waiting"
    ).update({PairingCode.status: "expired"})
    
    ttl_s = get_setting_int(db, "pairing_code_ttl_s", settings.DEFAULT_PAIRING_CODE_TTL_S)
    code = gen_code(6)
    token = f"pt_{secrets.token_hex(16)}"
    pc_id = gen_ulid("pc_")
    expires_at = now + timedelta(seconds=ttl_s)
    
    p_code = PairingCode(
        id=pc_id,
        device_id=device.id,
        code_hash=hmac_hash(code),
        token_hash=hmac_hash(token),
        status="waiting",
        expires_at=expires_at,
        created_at=now
    )
    db.add(p_code)
    db.commit()
    
    qr_url = f"{settings.BASE_URL}/m/{token}"
    return PairingCodeResponse(
        id=pc_id,
        code=code,
        token=token,
        qr_url=qr_url,
        expires_at=expires_at.isoformat().replace("+00:00", "Z"),
        ttl_s=ttl_s
    )

@router.get("/pairing-codes/{code_id}")
async def poll_pairing_code(
    code_id: str,
    wait: int = Query(0, ge=0, le=25),
    device: Device = Depends(get_current_device),
    db: Session = Depends(get_db)
):
    start = time.time()
    while True:
        pc = db.query(PairingCode).filter(
            PairingCode.id == code_id,
            PairingCode.device_id == device.id
        ).first()
        if not pc:
            raise_api_error(status_code=404, code="not_found", message="Mã ghép máy không tồn tại")
            
        now = datetime.now(timezone.utc)
        if pc.status == "waiting" and pc.expires_at and ensure_utc(pc.expires_at) < now:
            pc.status = "expired"
            db.commit()
            
        if pc.status != "waiting":
            pairing = db.query(Pairing).filter(Pairing.code_id == pc.id).first()
            user_display = None
            if pairing and pairing.user:
                u = pairing.user
                if u.display_name:
                    parts = u.display_name.split()
                    user_display = f"{parts[0]} {parts[-1][0]}." if len(parts) > 1 else u.display_name
                else:
                    user_display = "Khách hàng"
                    
            return {
                "status": pc.status,
                "pairing_id": pairing.id if pairing else None,
                "user": {"display_name": user_display} if user_display else None,
                "planned_sessions": pairing.planned_sessions if pairing else None,
                "remaining_sessions": (pairing.planned_sessions or 0) - (pairing.completed_sessions if pairing else 0)
            }
            
        elapsed = time.time() - start
        if elapsed >= wait or wait == 0:
            return {
                "status": "waiting",
                "pairing_id": None,
                "user": None,
                "planned_sessions": None,
                "remaining_sessions": 0
            }
            
        await asyncio.sleep(1.0)
        db.expire_all()

@router.post("/pairings/{pairing_id}/reject", status_code=status.HTTP_204_NO_CONTENT)
def reject_pairing(
    pairing_id: str,
    device: Device = Depends(get_current_device),
    db: Session = Depends(get_db)
):
    pairing = db.query(Pairing).filter(
        Pairing.id == pairing_id,
        Pairing.device_id == device.id
    ).first()
    if pairing:
        pairing.status = "cancelled"
        if pairing.pairing_code:
            pairing.pairing_code.status = "rejected"
        db.commit()
        broadcast_user_event(pairing.user_id, "pairing.updated", {"id": pairing.id, "status": "cancelled"})
    return None

@router.get("/pairings/{pairing_id}")
async def poll_device_pairing(
    pairing_id: str,
    wait: int = Query(0, ge=0, le=25),
    device: Device = Depends(get_current_device),
    db: Session = Depends(get_db)
):
    start = time.time()
    while True:
        pairing = db.query(Pairing).filter(
            Pairing.id == pairing_id,
            Pairing.device_id == device.id
        ).first()
        if not pairing:
            raise_api_error(status_code=404, code="not_found", message="Ghép máy không tồn tại")
            
        # If in a terminal state or ready/in_use
        remaining = max(0, (pairing.planned_sessions or 0) - pairing.completed_sessions)
        if wait == 0 or pairing.status in ("ready", "in_use", "finished", "cancelled", "expired"):
            return {
                "status": pairing.status,
                "remaining_sessions": remaining
            }
            
        elapsed = time.time() - start
        if elapsed >= wait:
            return {
                "status": pairing.status,
                "remaining_sessions": remaining
            }
            
        await asyncio.sleep(1.0)
        db.expire_all()

@router.post("/verify")
def device_verify_wallet(
    req: DeviceVerifyBody,
    request: Request,
    device: Device = Depends(get_current_device),
    db: Session = Depends(get_db)
):
    """User inputs 8-char wallet code on booth screen."""
    code = req.wallet_code.strip().upper()
    now = datetime.now(timezone.utc)
    
    wallet = db.query(Wallet).filter(
        (Wallet.code == code) | (Wallet.id == code)
    ).first()
    if not wallet:
        raise_api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code="not_found",
            message="Mã ví không tồn tại"
        )
        
    if wallet.locked_until and ensure_utc(wallet.locked_until) > now:
        raise_api_error(
            status_code=status.HTTP_423_LOCKED,
            code="locked",
            message="Ví đang bị tạm khóa do nhập sai quá nhiều lần",
            details={"locked_until": wallet.locked_until.isoformat().replace("+00:00", "Z")}
        )
        
    price = get_setting_int(db, "price_per_session_vnd", settings.DEFAULT_PRICE_PER_SESSION_VND)
    avail = wallet.balance_vnd - wallet.held_vnd
    sessions_avail = avail // price if price > 0 else 0
    
    return {
        "status": "valid",
        "wallet_code": code,
        "balance_vnd": wallet.balance_vnd,
        "available_vnd": avail,
        "held_vnd": wallet.held_vnd,
        "price_per_session_vnd": price,
        "sessions_available": sessions_avail,
        "max_sessions": min(sessions_avail, 3)
    }

@router.post("/direct-pay", status_code=status.HTTP_201_CREATED)
async def device_direct_pay(
    request: Request,
    sessions: int = Query(1, ge=1, le=10),
    device: Device = Depends(get_current_device),
    db: Session = Depends(get_db)
):
    """Direct payment at booth with selectable sessions count (1, 2, 3 sessions)."""
    sessions_count = sessions
    try:
        body = await request.json()
        if isinstance(body, dict) and "sessions" in body:
            sessions_count = int(body["sessions"])
    except Exception:
        pass
    sessions_count = max(1, min(10, sessions_count))

    price = get_setting_int(db, "price_per_session_vnd", settings.DEFAULT_PRICE_PER_SESSION_VND)
    total_amount = price * sessions_count
    direct_ttl_min = get_setting_int(db, "direct_pay_ttl_min", settings.DEFAULT_DIRECT_PAY_TTL_MIN)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=direct_ttl_min)
    
    # Create temporary wallet with 0 balance
    temp_wallet = Wallet(
        id=gen_ulid("wal_"),
        kind="guest",
        code=gen_code(8),
        temporary=True,
        expires_at=expires_at,
        balance_vnd=0,
        held_vnd=0,
        status="active",
        created_at=now
    )
    db.add(temp_wallet)
    db.flush()
    
    ref_code = "PBD" + gen_code(8)
    dp_id = gen_ulid("dp_")
    
    intent = PaymentIntent(
        id=dp_id,
        kind="direct_pay",
        ref=ref_code,
        wallet_id=temp_wallet.id,
        device_id=device.id,
        expected_vnd=total_amount,
        credited_vnd=0,
        status="pending",
        expires_at=expires_at,
        created_at=now
    )
    db.add(intent)
    db.commit()
    
    qr_payload = build_vietqr_payload(
        amount_vnd=total_amount,
        transfer_content=ref_code
    )
    
    return {
        "id": dp_id,
        "sessions": sessions_count,
        "amount_vnd": total_amount,
        "bank_name": settings.BANK_NAME,
        "bank_bin": settings.BANK_BIN,
        "account_number": settings.ACCOUNT_NUMBER,
        "account_name": settings.ACCOUNT_NAME,
        "transfer_content": ref_code,
        "qr_payload": qr_payload,
        "expires_at": expires_at.isoformat().replace("+00:00", "Z")
    }

@router.get("/direct-pay/{dp_id}")
async def poll_direct_pay(
    dp_id: str,
    wait: int = Query(0, ge=0, le=25),
    device: Device = Depends(get_current_device),
    db: Session = Depends(get_db)
):
    start = time.time()
    while True:
        intent = db.query(PaymentIntent).filter(
            PaymentIntent.id == dp_id,
            PaymentIntent.device_id == device.id
        ).first()
        if not intent:
            raise_api_error(status_code=404, code="not_found", message="Lệnh thanh toán không tồn tại")
            
        now = datetime.now(timezone.utc)
        if intent.status == "pending" and intent.expires_at and ensure_utc(intent.expires_at) < now:
            intent.status = "expired"
            db.commit()
            
        if intent.status != "pending" or wait == 0:
            return {"status": intent.status}
            
        elapsed = time.time() - start
        if elapsed >= wait:
            return {"status": intent.status}
            
        await asyncio.sleep(1.0)
        db.expire_all()

@router.post("/direct-pay/{dp_id}/simulate")
def simulate_direct_pay(
    dp_id: str,
    device: Device = Depends(get_current_device),
    db: Session = Depends(get_db)
):
    """Giả lập thanh toán thành công tức thì cho direct-pay (Test mode không tốn tiền)."""
    intent = db.query(PaymentIntent).filter(
        PaymentIntent.id == dp_id,
        PaymentIntent.device_id == device.id
    ).first()
    if not intent:
        raise_api_error(status_code=404, code="not_found", message="Lệnh thanh toán không tồn tại")

    now = datetime.now(timezone.utc)
    intent.wallet.balance_vnd += intent.expected_vnd
    intent.credited_vnd = intent.expected_vnd
    intent.status = "paid"
    intent.paid_at = now
    db.commit()
    return {"success": True, "status": "paid", "amount_vnd": intent.expected_vnd}

@router.post("/sessions", status_code=status.HTTP_201_CREATED)
def open_device_session(
    req: DeviceSessionCreateBody,
    device: Device = Depends(get_current_device),
    db: Session = Depends(get_db)
):
    if device.mode == "maintenance":
        raise_api_error(status_code=503, code="maintenance", message="Máy đang ở chế độ bảo trì")
        
    price = get_setting_int(db, "price_per_session_vnd", settings.DEFAULT_PRICE_PER_SESSION_VND)
    now = datetime.now(timezone.utc)
    hold_ttl_min = get_setting_int(db, "hold_ttl_min", settings.DEFAULT_HOLD_TTL_MIN)
    hold_expires = now + timedelta(minutes=hold_ttl_min)
    
    src = req.source
    src_type = src.get("type")
    
    wallet = None
    user_id = None
    pairing_id = None
    remaining_sessions = 1
    
    if src_type == "pairing":
        pr_id = src.get("id")
        pairing = db.query(Pairing).filter(
            Pairing.id == pr_id,
            Pairing.device_id == device.id
        ).first()
        if not pairing:
            raise_api_error(status_code=404, code="not_found", message="Ghép máy không tồn tại")
            
        if pairing.status not in ("ready", "in_use"):
            raise_api_error(
                status_code=status.HTTP_409_CONFLICT,
                code="pairing_state_invalid",
                message=f"Ghép máy chưa sẵn sàng (trạng thái: {pairing.status})"
            )
            
        remaining = (pairing.planned_sessions or 0) - pairing.completed_sessions
        if remaining <= 0:
            raise_api_error(status_code=409, code="no_sessions_left", message="Đã hết số lượt chụp của phiên ghép máy")
            
        user_id = pairing.user_id
        pairing_id = pairing.id
        wallet = db.query(Wallet).filter(Wallet.user_id == user_id).first()
        remaining_sessions = remaining
        
    elif src_type == "wallet_code":
        code = src.get("code", "").strip().upper()
        wallet = db.query(Wallet).filter(
            (Wallet.code == code) | (Wallet.id == code)
        ).first()
        if not wallet:
            raise_api_error(status_code=404, code="not_found", message="Mã ví không tồn tại")
        user_id = wallet.user_id
        avail = wallet.balance_vnd - wallet.held_vnd
        remaining_sessions = avail // price if price > 0 else 0
        
    elif src_type == "direct_pay":
        dp_id = src.get("id")
        intent = db.query(PaymentIntent).filter(PaymentIntent.id == dp_id, PaymentIntent.kind == "direct_pay").first()
        if not intent or intent.status != "paid":
            raise_api_error(status_code=409, code="pairing_state_invalid", message="Thanh toán trực tiếp chưa hoàn tất")
        wallet = intent.wallet
        avail = wallet.balance_vnd - wallet.held_vnd
        remaining_sessions = avail // price if price > 0 else 0
    else:
        raise_api_error(status_code=400, code="invalid_request", message="Source type không hợp lệ")
        
    if not wallet or wallet.status != "active":
        raise_api_error(status_code=423, code="locked", message="Ví không hợp lệ hoặc đang bị khóa")
        
    # ATOMIC HOLD TRANSACTION (Section 6 & 9)
    # Check if balance - held >= price
    if wallet.balance_vnd - wallet.held_vnd < price:
        raise_api_error(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            code="insufficient_balance",
            message="Số dư không đủ để mở phiên chụp",
            details={"shortfall_vnd": price - (wallet.balance_vnd - wallet.held_vnd)}
        )
        
    wallet.held_vnd += price
    
    # Record in ledger
    led = Ledger(
        wallet_id=wallet.id,
        delta_balance_vnd=0,
        delta_held_vnd=price,
        balance_after_vnd=wallet.balance_vnd,
        held_after_vnd=wallet.held_vnd,
        reason="hold",
        ref_id=req.client_session_id,
        actor=f"device:{device.id}",
        created_at=now
    )
    db.add(led)
    
    if pairing_id:
        pairing.status = "in_use"
        
    session_id = gen_ulid("ses_")
    sess = PhotoSession(
        id=session_id,
        wallet_id=wallet.id,
        user_id=user_id,
        pairing_id=pairing_id,
        device_id=device.id,
        layout=req.layout or "strip",
        filter=req.filter or "none",
        status="started",
        hold_expires_at=hold_expires,
        created_at=now
    )
    db.add(sess)
    db.commit()
    
    return {
        "session_id": session_id,
        "hold_vnd": price,
        "hold_expires_at": hold_expires.isoformat().replace("+00:00", "Z"),
        "remaining_sessions": remaining_sessions,
        "upload": {
            "max_bytes": {
                "image": settings.MAX_IMAGE_BYTES,
                "video": settings.MAX_VIDEO_BYTES
            },
            "kinds": ["strip", "shot_1", "shot_2", "shot_3", "shot_4", "video"]
        }
    }

@router.put("/sessions/{session_id}/media/{kind}")
async def upload_session_media(
    session_id: str,
    kind: str,
    request: Request,
    content_type: Optional[str] = Header(None),
    x_content_sha256: Optional[str] = Header(None, alias="X-Content-SHA256"),
    device: Device = Depends(get_current_device),
    db: Session = Depends(get_db)
):
    session = db.query(PhotoSession).filter(
        PhotoSession.id == session_id,
        PhotoSession.device_id == device.id
    ).first()
    if not session:
        raise_api_error(status_code=404, code="not_found", message="Phiên chụp không tồn tại")
        
    # Read raw body
    body_bytes = await request.body()
    body_len = len(body_bytes)
    
    max_allowed = settings.MAX_VIDEO_BYTES if kind == "video" else settings.MAX_IMAGE_BYTES
    if body_len > max_allowed:
        raise_api_error(status_code=413, code="too_large", message="Kích thước tệp vượt quá giới hạn cho phép")
        
    calc_sha = hashlib.sha256(body_bytes).hexdigest().lower()
    if x_content_sha256 and calc_sha != x_content_sha256.strip().lower():
        raise_api_error(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="hash_mismatch",
            message="Mã SHA256 gửi kèm không khớp với nội dung thực tế"
        )
        
    # Storage key path
    ext = "mp4" if kind == "video" else "jpg"
    filename = f"{session_id}_{kind}.{ext}"
    os.makedirs(settings.STORAGE_DIR, exist_ok=True)
    file_path = os.path.join(settings.STORAGE_DIR, filename)
    
    # Save file
    with open(file_path, "wb") as f:
        f.write(body_bytes)
        
    # Parse shot index if applicable
    idx = None
    if kind.startswith("shot_"):
        try:
            idx = int(kind.split("_")[1])
        except ValueError:
            pass
            
    now = datetime.now(timezone.utc)
    # Check if media record exists (Idempotent upload)
    media_rec = db.query(Media).filter(
        Media.session_id == session.id,
        Media.kind == ("shot" if idx is not None else kind),
        Media.idx == idx
    ).first()
    
    if media_rec:
        media_rec.bytes = body_len
        media_rec.sha256 = calc_sha
        media_rec.storage_key = file_path
        media_rec.content_type = content_type or ("video/mp4" if kind == "video" else "image/jpeg")
    else:
        media_rec = Media(
            id=gen_ulid("med_"),
            session_id=session.id,
            kind="shot" if idx is not None else kind,
            idx=idx,
            content_type=content_type or ("video/mp4" if kind == "video" else "image/jpeg"),
            bytes=body_len,
            sha256=calc_sha,
            storage_key=file_path,
            created_at=now
        )
        db.add(media_rec)
        
    db.commit()
    return {
        "kind": kind,
        "bytes": body_len,
        "sha256": calc_sha,
        "stored": True
    }

@router.post("/sessions/{session_id}/complete")
def complete_device_session(
    session_id: str,
    req: DeviceSessionCompleteBody,
    device: Device = Depends(get_current_device),
    db: Session = Depends(get_db)
):
    session = db.query(PhotoSession).filter(
        PhotoSession.id == session_id,
        PhotoSession.device_id == device.id
    ).first()
    if not session:
        raise_api_error(status_code=404, code="not_found", message="Phiên chụp không tồn tại")
        
    if session.status == "completed":
        # Already completed
        return {"status": "completed"}
        
    # Verify required media (strip + 4 shots; video is optional)
    uploaded_media = db.query(Media).filter(Media.session_id == session.id, Media.deleted_at == None).all()
    uploaded_kinds = {m.kind if m.idx is None else f"shot_{m.idx}": m for m in uploaded_media}
    
    missing = []
    if "strip" not in uploaded_kinds:
        missing.append("strip")
    for i in range(1, (req.shots or 4) + 1):
        shot_key = f"shot_{i}"
        if shot_key not in uploaded_kinds:
            missing.append(shot_key)
            
    if missing:
        raise_api_error(
            status_code=status.HTTP_409_CONFLICT,
            code="media_missing",
            message="Chưa tải lên đầy đủ các tệp bắt buộc",
            details={"missing": missing}
        )
        
    price = get_setting_int(db, "price_per_session_vnd", settings.DEFAULT_PRICE_PER_SESSION_VND)
    wallet = session.wallet
    now = datetime.now(timezone.utc)
    
    # ATOMIC DEDUCTION TRANSACTION (Section 6 & 9)
    if wallet.held_vnd < price:
        raise_api_error(
            status_code=409,
            code="pairing_state_invalid",
            message="Khoản tiền giữ không đủ hoặc đã bị hủy"
        )
        
    wallet.balance_vnd -= price
    wallet.held_vnd -= price
    
    led = Ledger(
        wallet_id=wallet.id,
        delta_balance_vnd=-price,
        delta_held_vnd=-price,
        balance_after_vnd=wallet.balance_vnd,
        held_after_vnd=wallet.held_vnd,
        reason="session",
        ref_id=session.id,
        actor=f"device:{device.id}",
        created_at=now
    )
    db.add(led)
    
    session.layout = req.layout or session.layout
    session.filter = req.filter or session.filter
    session.frame_color = req.frame_color
    session.color_filter = req.color_filter
    session.shots = req.shots or 4
    session.status = "completed"
    session.completed_at = now
    
    # Always generate share_token & share_url so booth screen can display the download QR code
    retention_hours = get_setting_int(db, "guest_retention_hours", settings.DEFAULT_GUEST_RETENTION_HOURS)
    share_token = secrets.token_urlsafe(24)
    share_expires = now + timedelta(hours=retention_hours)
    session.share_token = share_token
    session.share_expires_at = share_expires
    share_url = f"{settings.BASE_URL}/d/{share_token}"
    
    # Check if guest or user
    if session.user_id:
        # App user
        pairing = session.pairing
        if pairing:
            pairing.completed_sessions += 1
            planned = pairing.planned_sessions or 1
            remaining = max(0, planned - pairing.completed_sessions)
            if remaining <= 0:
                pairing.status = "finished"
                pairing.finished_at = now
            broadcast_user_event(session.user_id, "pairing.updated", {"id": pairing.id, "status": pairing.status, "remaining_sessions": remaining})
        else:
            remaining = (wallet.balance_vnd - wallet.held_vnd) // price if price > 0 else 0
            
        broadcast_user_event(session.user_id, "library.item_added", {"id": session.id})
        broadcast_user_event(session.user_id, "wallet.updated", {
            "available_vnd": wallet.balance_vnd - wallet.held_vnd,
            "sessions_available": (wallet.balance_vnd - wallet.held_vnd) // price
        })
        db.commit()
        
        return {
            "status": "completed",
            "charged_vnd": price,
            "owner": "user",
            "library_item_id": session.id,
            "share_url": share_url,
            "share_token": share_token,
            "share_expires_at": share_expires.isoformat().replace("+00:00", "Z"),
            "remaining_sessions": remaining
        }
    else:
        # Guest web
        db.commit()
        
        avail = wallet.balance_vnd - wallet.held_vnd
        return {
            "status": "completed",
            "charged_vnd": price,
            "owner": "guest",
            "share_url": share_url,
            "share_token": share_token,
            "share_expires_at": share_expires.isoformat().replace("+00:00", "Z"),
            "remaining_sessions": avail // price
        }

@router.post("/sessions/{session_id}/cancel")
def cancel_device_session(
    session_id: str,
    req: DeviceSessionCancelBody,
    device: Device = Depends(get_current_device),
    db: Session = Depends(get_db)
):
    session = db.query(PhotoSession).filter(
        PhotoSession.id == session_id,
        PhotoSession.device_id == device.id
    ).first()
    if not session:
        raise_api_error(status_code=404, code="not_found", message="Phiên chụp không tồn tại")
        
    if session.status == "completed":
        raise_api_error(status_code=409, code="pairing_state_invalid", message="Phiên chụp đã hoàn tất, không thể hủy")
        
    price = get_setting_int(db, "price_per_session_vnd", settings.DEFAULT_PRICE_PER_SESSION_VND)
    wallet = session.wallet
    now = datetime.now(timezone.utc)
    
    # Release held amount
    if wallet.held_vnd >= price:
        wallet.held_vnd -= price
        led = Ledger(
            wallet_id=wallet.id,
            delta_balance_vnd=0,
            delta_held_vnd=-price,
            balance_after_vnd=wallet.balance_vnd,
            held_after_vnd=wallet.held_vnd,
            reason="release",
            ref_id=f"cancel_{req.reason}",
            actor=f"device:{device.id}",
            created_at=now
        )
        db.add(led)
        
    session.status = "cancelled"
    db.commit()
    return {"status": "cancelled", "released_vnd": price}

@router.get("/sessions")
def list_open_device_sessions(
    status: str = Query("started"),
    device: Device = Depends(get_current_device),
    db: Session = Depends(get_db)
):
    sessions = db.query(PhotoSession).filter(
        PhotoSession.device_id == device.id,
        PhotoSession.status == status
    ).all()
    return [{"id": s.id, "created_at": s.created_at.isoformat().replace("+00:00", "Z")} for s in sessions]
