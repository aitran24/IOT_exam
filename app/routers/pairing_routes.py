from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import APIRouter, Depends, Header, status
from sqlalchemy.orm import Session
from sqlalchemy import or_
from app.database import get_db, get_setting_int
from app.config import settings
from app.models import User, Wallet, Device, PairingCode, Pairing, PaymentIntent
from app.schemas import (
    PairingCreateBody, PairingCreateResponse, PairingPlanBody,
    PairingPlanResponse, PairingDetailResponse, MachineBrief
)
from app.security import gen_ulid, gen_code, hmac_hash, ensure_utc
from app.vietqr import build_vietqr_payload, generate_qr_data_url
from app.deps import raise_api_error, get_current_user
from app.idempotency import check_or_save_idempotency

router = APIRouter(prefix="/api/v1/pairings", tags=["Pairings"])

@router.post("", status_code=status.HTTP_201_CREATED)
def claim_pairing(
    req: PairingCreateBody,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not current_user.terms_accepted_at:
        raise_api_error(
            status_code=status.HTTP_403_FORBIDDEN,
            code="terms_not_accepted",
            message="Bạn cần đồng ý điều khoản trước khi ghép nối máy"
        )
        
    def _execute():
        now = datetime.now(timezone.utc)
        p_code = None
        if req.method == "qr" and req.token:
            tok_hash = hmac_hash(req.token.strip())
            p_code = db.query(PairingCode).filter(
                PairingCode.token_hash == tok_hash,
                PairingCode.status == "waiting",
                PairingCode.expires_at > now
            ).first()
        elif req.method == "code" and req.code:
            code_hash = hmac_hash(req.code.strip().upper())
            p_code = db.query(PairingCode).filter(
                PairingCode.code_hash == code_hash,
                PairingCode.status == "waiting",
                PairingCode.expires_at > now
            ).first()
        else:
            raise_api_error(status_code=400, code="invalid_request", message="Cần cung cấp method 'qr' hoặc 'code'")
            
        if not p_code:
            raise_api_error(
                status_code=status.HTTP_404_NOT_FOUND,
                code="not_found",
                message="Mã ghép máy không hợp lệ hoặc đã hết hạn"
            )
            
        # Check active pairing on this device
        active_pairing = db.query(Pairing).filter(
            Pairing.device_id == p_code.device_id,
            Pairing.status.in_(["claimed", "awaiting_payment", "ready", "in_use"])
        ).first()
        if active_pairing:
            raise_api_error(
                status_code=status.HTTP_409_CONFLICT,
                code="machine_busy",
                message="Máy đang có người khác sử dụng, vui lòng thử lại sau"
            )
            
        # Claim code
        p_code.status = "claimed"
        p_code.claimed_by = current_user.id
        
        idle_timeout_min = get_setting_int(db, "pairing_idle_timeout_min", settings.DEFAULT_PAIRING_IDLE_TIMEOUT_MIN)
        pairing_expires = now + timedelta(minutes=idle_timeout_min)
        pairing_id = gen_ulid("pr_")
        
        pairing = Pairing(
            id=pairing_id,
            user_id=current_user.id,
            device_id=p_code.device_id,
            code_id=p_code.id,
            status="claimed",
            planned_sessions=None,
            completed_sessions=0,
            expires_at=pairing_expires,
            created_at=now
        )
        db.add(pairing)
        db.commit()
        
        device = db.query(Device).filter(Device.id == p_code.device_id).first()
        wallet = db.query(Wallet).filter(Wallet.user_id == current_user.id).first()
        avail = (wallet.balance_vnd - wallet.held_vnd) if wallet else 0
        price = get_setting_int(db, "price_per_session_vnd", settings.DEFAULT_PRICE_PER_SESSION_VND)
        max_sess = get_setting_int(db, "max_sessions_per_pairing", settings.DEFAULT_MAX_SESSIONS_PER_PAIRING)
        
        resp_data = {
            "id": pairing.id,
            "status": "claimed",
            "machine": {
                "id": device.id if device else p_code.device_id,
                "name": device.name if device and device.name else "Photobooth QNV&AT"
            },
            "max_sessions": max_sess,
            "price_per_session_vnd": price,
            "expires_at": pairing_expires.isoformat().replace("+00:00", "Z"),
            "wallet": {
                "available_vnd": avail,
                "sessions_available": avail // price
            }
        }
        return 201, resp_data

    code, data = check_or_save_idempotency(
        db=db,
        key=idempotency_key,
        scope=f"pair_{current_user.id}",
        payload_dict=req.model_dump(),
        execute_fn=_execute
    )
    return data

@router.post("/{pairing_id}/plan")
def set_pairing_plan(
    pairing_id: str,
    req: PairingPlanBody,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if req.sessions < 1 or req.sessions > 3:
        raise_api_error(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="sessions_out_of_range",
            message="Chỉ được chọn từ 1 đến 3 lượt chụp"
        )
        
    def _execute():
        now = datetime.now(timezone.utc)
        pairing = db.query(Pairing).filter(
            Pairing.id == pairing_id,
            Pairing.user_id == current_user.id
        ).first()
        if not pairing:
            raise_api_error(status_code=404, code="not_found", message="Ghép máy không tồn tại")
            
        if pairing.status not in ("claimed", "awaiting_payment"):
            raise_api_error(
                status_code=status.HTTP_409_CONFLICT,
                code="pairing_state_invalid",
                message=f"Không thể đổi số lượt khi ghép máy ở trạng thái '{pairing.status}'"
            )
            
        wallet = db.query(Wallet).filter(Wallet.user_id == current_user.id).first()
        price = get_setting_int(db, "price_per_session_vnd", settings.DEFAULT_PRICE_PER_SESSION_VND)
        total_vnd = req.sessions * price
        avail_vnd = (wallet.balance_vnd - wallet.held_vnd) if wallet else 0
        
        pairing.planned_sessions = req.sessions
        
        if avail_vnd >= total_vnd:
            pairing.status = "ready"
            pairing.topup_id = None
            db.commit()
            
            resp = {
                "id": pairing.id,
                "status": "ready",
                "planned_sessions": req.sessions,
                "remaining_sessions": req.sessions - pairing.completed_sessions,
                "total_vnd": total_vnd,
                "available_vnd": avail_vnd,
                "shortfall_vnd": 0,
                "topup": None
            }
            return 200, resp
        else:
            shortfall = total_vnd - avail_vnd
            pairing.status = "awaiting_payment"
            
            # Cancel old pending topup if any
            if pairing.topup_id:
                old_tp = db.query(PaymentIntent).filter(PaymentIntent.id == pairing.topup_id).first()
                if old_tp and old_tp.credited_vnd == 0:
                    old_tp.status = "cancelled"
                    
            ref_code = "PBI" + gen_code(8)
            tp_id = gen_ulid("tp_")
            tp_expires = now + timedelta(minutes=15)
            
            topup = PaymentIntent(
                id=tp_id,
                kind="topup",
                ref=ref_code,
                wallet_id=wallet.id,
                user_id=current_user.id,
                expected_vnd=shortfall,
                credited_vnd=0,
                status="pending",
                expires_at=tp_expires,
                created_at=now
            )
            db.add(topup)
            pairing.topup_id = tp_id
            db.commit()
            
            qr_payload = build_vietqr_payload(
                amount_vnd=shortfall,
                transfer_content=ref_code
            )
            qr_image = generate_qr_data_url(qr_payload)
            
            resp = {
                "id": pairing.id,
                "status": "awaiting_payment",
                "planned_sessions": req.sessions,
                "remaining_sessions": req.sessions - pairing.completed_sessions,
                "total_vnd": total_vnd,
                "available_vnd": avail_vnd,
                "shortfall_vnd": shortfall,
                "topup": {
                    "id": tp_id,
                    "amount_vnd": shortfall,
                    "expires_at": tp_expires.isoformat().replace("+00:00", "Z"),
                    "payment": {
                        "bank_name": settings.BANK_NAME,
                        "account_number": settings.ACCOUNT_NUMBER,
                        "account_name": settings.ACCOUNT_NAME,
                        "amount_vnd": shortfall,
                        "transfer_content": ref_code,
                        "qr_payload": qr_payload,
                        "qr_image_url": qr_image
                    }
                }
            }
            return 200, resp

    code, data = check_or_save_idempotency(
        db=db,
        key=idempotency_key,
        scope=f"plan_{pairing_id}",
        payload_dict=req.model_dump(),
        execute_fn=_execute
    )
    return data

@router.get("/{pairing_id}")
def get_pairing(
    pairing_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    pairing = db.query(Pairing).filter(
        Pairing.id == pairing_id,
        Pairing.user_id == current_user.id
    ).first()
    if not pairing:
        raise_api_error(status_code=404, code="not_found", message="Ghép máy không tồn tại")
        
    device = pairing.device
    remaining = (pairing.planned_sessions or 0) - pairing.completed_sessions
    
    return {
        "id": pairing.id,
        "status": pairing.status,
        "planned_sessions": pairing.planned_sessions,
        "completed_sessions": pairing.completed_sessions,
        "remaining_sessions": max(0, remaining),
        "shortfall_vnd": 0,
        "topup_id": pairing.topup_id,
        "machine": {
            "id": device.id if device else pairing.device_id,
            "name": device.name if device and device.name else "Photobooth QNV&AT"
        },
        "expires_at": pairing.expires_at.isoformat().replace("+00:00", "Z"),
        "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    }

@router.post("/{pairing_id}/finish")
def finish_pairing(
    pairing_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    pairing = db.query(Pairing).filter(
        Pairing.id == pairing_id,
        Pairing.user_id == current_user.id
    ).first()
    if not pairing:
        raise_api_error(status_code=404, code="not_found", message="Ghép máy không tồn tại")
        
    pairing.status = "finished"
    pairing.finished_at = datetime.now(timezone.utc)
    db.commit()
    return {"status": "finished", "completed_sessions": pairing.completed_sessions}

@router.post("/{pairing_id}/cancel")
def cancel_pairing(
    pairing_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    pairing = db.query(Pairing).filter(
        Pairing.id == pairing_id,
        Pairing.user_id == current_user.id
    ).first()
    if not pairing:
        raise_api_error(status_code=404, code="not_found", message="Ghép máy không tồn tại")
        
    if pairing.completed_sessions > 0:
        raise_api_error(status_code=409, code="pairing_state_invalid", message="Không thể hủy phiên đã bắt đầu chụp")
        
    pairing.status = "cancelled"
    db.commit()
    return {"status": "cancelled"}
