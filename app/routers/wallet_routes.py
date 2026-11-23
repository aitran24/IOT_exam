from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import APIRouter, Depends, Header, Query, Request, status
from sqlalchemy.orm import Session
from app.database import get_db, get_setting_int
from app.config import settings
from app.models import User, Wallet, PaymentIntent, Ledger
from app.schemas import (
    WalletResponse, TransactionsResponse, TransactionItem,
    TopupCreateBody, TopupResponse, TopupStatusResponse, PaymentDetails
)
from app.security import gen_ulid, gen_code
from app.vietqr import build_vietqr_payload, generate_qr_data_url
from app.deps import raise_api_error, get_current_user
from app.idempotency import check_or_save_idempotency

router = APIRouter(prefix="/api/v1", tags=["Wallets & Topups"])

@router.get("/wallet", response_model=WalletResponse)
def get_user_wallet(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    wallet = db.query(Wallet).filter(Wallet.user_id == current_user.id).first()
    if not wallet:
        # Create wallet if missing
        wallet = Wallet(
            id=gen_ulid("wal_"),
            kind="user",
            code=gen_code(8),
            user_id=current_user.id,
            balance_vnd=0,
            held_vnd=0,
            status="active"
        )
        db.add(wallet)
        db.commit()
        db.refresh(wallet)
    elif not wallet.code:
        wallet.code = gen_code(8)
        db.commit()
        db.refresh(wallet)
        
    price = get_setting_int(db, "price_per_session_vnd", settings.DEFAULT_PRICE_PER_SESSION_VND)
    available = wallet.balance_vnd - wallet.held_vnd
    sessions_avail = available // price if price > 0 else 0
    leftover = available % price if price > 0 else 0
    
    # Return 8-character code for display on mobile app
    display_code = wallet.code or wallet.id
    
    return WalletResponse(
        wallet_id=display_code,
        wallet_code=display_code,
        balance_vnd=wallet.balance_vnd,
        held_vnd=wallet.held_vnd,
        available_vnd=available,
        price_per_session_vnd=price,
        sessions_available=sessions_avail,
        leftover_vnd=leftover,
        updated_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )

@router.get("/wallet/transactions", response_model=TransactionsResponse)
def get_wallet_transactions(
    limit: int = Query(20, ge=1, le=50),
    cursor: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    wallet = db.query(Wallet).filter(Wallet.user_id == current_user.id).first()
    if not wallet:
        return TransactionsResponse(items=[], next_cursor=None)
        
    query = db.query(Ledger).filter(Ledger.wallet_id == wallet.id)
    if cursor:
        try:
            cursor_id = int(cursor)
            query = query.filter(Ledger.id < cursor_id)
        except ValueError:
            pass
            
    ledger_rows = query.order_by(Ledger.id.desc()).limit(limit + 1).all()
    
    has_more = len(ledger_rows) > limit
    rows = ledger_rows[:limit]
    next_cursor = str(rows[-1].id) if has_more and rows else None
    
    items = []
    desc_map = {
        "topup": "Nạp tiền qua VietQR",
        "session": "Thanh toán phiên chụp ảnh",
        "hold": "Tạm giữ cho phiên chụp",
        "release": "Hoàn trả khoản giữ phiên chụp",
        "refund": "Hoàn tiền",
        "admin_adjust": "Điều chỉnh số dư bởi quản trị viên"
    }
    
    for row in rows:
        items.append(TransactionItem(
            id=f"led_{row.id}",
            type=row.reason,
            delta_vnd=row.delta_balance_vnd,
            balance_after_vnd=row.balance_after_vnd,
            description=desc_map.get(row.reason, row.reason),
            ref=row.ref_id,
            created_at=row.created_at.isoformat().replace("+00:00", "Z")
        ))
        
    return TransactionsResponse(items=items, next_cursor=next_cursor)

@router.post("/topups", status_code=status.HTTP_201_CREATED)
def create_topup(
    req: TopupCreateBody,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not current_user.terms_accepted_at:
        raise_api_error(
            status_code=status.HTTP_403_FORBIDDEN,
            code="terms_not_accepted",
            message="Bạn cần đồng ý điều khoản trước khi sử dụng ví và nạp tiền"
        )
        
    price = get_setting_int(db, "price_per_session_vnd", settings.DEFAULT_PRICE_PER_SESSION_VND)
    min_vnd = get_setting_int(db, "topup_min_vnd", settings.DEFAULT_TOPUP_MIN_VND)
    max_vnd = get_setting_int(db, "topup_max_vnd", settings.DEFAULT_TOPUP_MAX_VND)
    
    if req.mode == "sessions":
        if not req.sessions or req.sessions < 1 or req.sessions > 3:
            raise_api_error(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                code="sessions_out_of_range",
                message="Chỉ được chọn từ 1 đến 3 lượt"
            )
        amount_vnd = req.sessions * price
        sessions_cnt = req.sessions
    elif req.mode == "amount":
        if not req.amount_vnd or req.amount_vnd < min_vnd or req.amount_vnd > max_vnd:
            raise_api_error(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                code="amount_out_of_range",
                message=f"Số tiền nạp phải từ {min_vnd:,}đ đến {max_vnd:,}đ"
            )
        amount_vnd = req.amount_vnd
        sessions_cnt = amount_vnd // price
    else:
        raise_api_error(status_code=400, code="invalid_request", message="Chế độ nạp không hợp lệ (cần 'sessions' hoặc 'amount')")
        
    def _execute():
        wallet = db.query(Wallet).filter(Wallet.user_id == current_user.id).first()
        if not wallet:
            wallet = Wallet(id=gen_ulid("wal_"), kind="user", user_id=current_user.id)
            db.add(wallet)
            db.flush()
            
        now = datetime.now(timezone.utc)
        # Check active pending topups
        pending_count = db.query(PaymentIntent).filter(
            PaymentIntent.user_id == current_user.id,
            PaymentIntent.status == "pending",
            PaymentIntent.expires_at > now
        ).count()
        if pending_count >= 5:
            raise_api_error(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                code="too_many_pending_topups",
                message="Bạn đang có quá nhiều lệnh nạp chờ xử lý (tối đa 5 lệnh)"
            )
            
        ref_code = "PBI" + gen_code(8)
        topup_id = gen_ulid("tp_")
        expires_at = now + timedelta(minutes=15)
        
        intent = PaymentIntent(
            id=topup_id,
            kind="topup",
            ref=ref_code,
            wallet_id=wallet.id,
            user_id=current_user.id,
            expected_vnd=amount_vnd,
            credited_vnd=0,
            status="pending",
            idempotency_key=idempotency_key,
            expires_at=expires_at,
            created_at=now
        )
        db.add(intent)
        db.commit()
        
        qr_payload = build_vietqr_payload(
            amount_vnd=amount_vnd,
            transfer_content=ref_code
        )
        qr_image = generate_qr_data_url(qr_payload)
        
        resp_data = {
            "id": topup_id,
            "status": "pending",
            "amount_vnd": amount_vnd,
            "sessions": sessions_cnt,
            "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
            "payment": {
                "bank_name": settings.BANK_NAME,
                "account_number": settings.ACCOUNT_NUMBER,
                "account_name": settings.ACCOUNT_NAME,
                "amount_vnd": amount_vnd,
                "transfer_content": ref_code,
                "qr_payload": qr_payload,
                "qr_image_url": qr_image
            }
        }
        return 201, resp_data

    code, data = check_or_save_idempotency(
        db=db,
        key=idempotency_key,
        scope=f"topup_{current_user.id}",
        payload_dict=req.model_dump(),
        execute_fn=_execute
    )
    return data

@router.get("/topups/{topup_id}")
def get_topup_status(
    topup_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    intent = db.query(PaymentIntent).filter(
        PaymentIntent.id == topup_id,
        PaymentIntent.user_id == current_user.id
    ).first()
    if not intent:
        raise_api_error(status_code=404, code="not_found", message="Lệnh nạp không tồn tại")
        
    now = datetime.now(timezone.utc)
    if intent.status == "pending" and intent.expires_at < now:
        intent.status = "expired"
        db.commit()
        
    wallet = intent.wallet
    price = get_setting_int(db, "price_per_session_vnd", settings.DEFAULT_PRICE_PER_SESSION_VND)
    avail = wallet.balance_vnd - wallet.held_vnd
    
    return {
        "id": intent.id,
        "status": intent.status,
        "amount_vnd": intent.expected_vnd,
        "credited_vnd": intent.credited_vnd,
        "remaining_vnd": max(0, intent.expected_vnd - intent.credited_vnd),
        "expires_at": intent.expires_at.isoformat().replace("+00:00", "Z"),
        "paid_at": intent.paid_at.isoformat().replace("+00:00", "Z") if intent.paid_at else None,
        "wallet": {
            "available_vnd": avail,
            "sessions_available": avail // price
        }
    }

@router.post("/topups/{topup_id}/cancel")
def cancel_topup(
    topup_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    intent = db.query(PaymentIntent).filter(
        PaymentIntent.id == topup_id,
        PaymentIntent.user_id == current_user.id
    ).first()
    if not intent:
        raise_api_error(status_code=404, code="not_found", message="Lệnh nạp không tồn tại")
        
    if intent.credited_vnd > 0 or intent.status == "paid":
        raise_api_error(
            status_code=status.HTTP_409_CONFLICT,
            code="topup_not_cancellable",
            message="Không thể hủy lệnh nạp tiền đã có giao dịch ghi nhận"
        )
        
    intent.status = "cancelled"
    db.commit()
    return {"status": "cancelled"}

@router.get("/topups")
def list_topups(
    limit: int = Query(20, ge=1, le=50),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    intents = db.query(PaymentIntent).filter(
        PaymentIntent.user_id == current_user.id
    ).order_by(PaymentIntent.created_at.desc()).limit(limit).all()
    
    items = []
    for it in intents:
        items.append({
            "id": it.id,
            "status": it.status,
            "amount_vnd": it.expected_vnd,
            "credited_vnd": it.credited_vnd,
            "ref": it.ref,
            "created_at": it.created_at.isoformat().replace("+00:00", "Z")
        })
    return {"items": items}
