import re
import secrets
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, Header, Request, status
from sqlalchemy.orm import Session
from app.database import get_db, get_setting_int
from app.config import settings
from app.models import Payment, PaymentIntent, Wallet, Ledger, Pairing
from app.schemas import SePayWebhookPayload
from app.deps import raise_api_error
from app.routers.event_routes import broadcast_user_event

router = APIRouter(prefix="/api/v1/webhooks", tags=["SePay Webhook"])

CODE_PATTERN = re.compile(r"PB[ID][23456789ABCDEFGHJKMNPQRSTUVWXYZ]{8}")

@router.post("/sepay")
async def sepay_webhook(
    request: Request,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    # 1. Flexible API Key verification (supports 'Apikey <key>', 'Bearer <key>', raw '<key>', and 'X-API-KEY' header)
    auth_val = authorization or request.headers.get("x-api-key") or ""
    token = auth_val.strip()
    if token.lower().startswith("apikey "):
        token = token[7:].strip()
    elif token.lower().startswith("bearer "):
        token = token[7:].strip()
        
    expected_token = settings.SEPAY_API_KEY.strip()
    valid_auth = False
    if token and secrets.compare_digest(token, expected_token):
        valid_auth = True
    elif auth_val and secrets.compare_digest(auth_val.strip(), f"Apikey {expected_token}"):
        valid_auth = True

    if not valid_auth:
        raise_api_error(status_code=status.HTTP_401_UNAUTHORIZED, code="unauthorized", message="API Key SePay không chính xác")
        
    try:
        raw_body = await request.json()
        payload = SePayWebhookPayload(**raw_body)
    except Exception:
        raise_api_error(status_code=400, code="invalid_request", message="Payload webhook không đúng định dạng")
        
    # 2. Only process incoming transfers
    if payload.transferType != "in":
        return {"success": True, "message": "Ignored transferType out"}
        
    # 3. Anti-duplicate by SePay ID (provider_tx_id)
    tx_id_str = str(payload.id)
    existing_payment = db.query(Payment).filter(Payment.provider_tx_id == tx_id_str).first()
    if existing_payment:
        return {"success": True, "message": "Already processed"}
        
    amount = payload.transferAmount
    now = datetime.now(timezone.utc)
    
    # 4. Extract code
    code_match = None
    if payload.code:
        c = payload.code.strip().upper()
        if CODE_PATTERN.match(c):
            code_match = c
            
    if not code_match:
        text_to_search = f"{payload.content or ''} {payload.description or ''}".upper()
        clean_content = re.sub(r"[\s\-_]", "", text_to_search)
        search = CODE_PATTERN.search(clean_content)
        if search:
            code_match = search.group(0)
            
    # If no valid code found: save as unmatched
    if not code_match:
        unmatched_pay = Payment(
            provider="sepay",
            provider_tx_id=tx_id_str,
            reference_code=payload.referenceCode,
            amount_vnd=amount,
            content=payload.content,
            matched_kind=None,
            wallet_id=None,
            intent_id=None,
            status="unmatched",
            raw_json=raw_body,
            received_at=now
        )
        db.add(unmatched_pay)
        db.commit()
        return {"success": True, "matched": False}
        
    # 5. Process matched code in database transaction
    code_type = code_match[2] # 'I' (topup app) or 'D' (direct pay at booth)
    
    if code_type == "I":
        # App topup intent: PBI + 8 chars
        intent = db.query(PaymentIntent).filter(
            PaymentIntent.ref == code_match,
            PaymentIntent.kind == "topup"
        ).first()
        if intent:
            wallet = intent.wallet
            wallet.balance_vnd += amount
            intent.credited_vnd += amount
            
            # Accept payment even if intent was cancelled/expired — money arrived, must credit
            if intent.status in ("cancelled", "expired"):
                intent.status = "paid"
                intent.paid_at = now
            elif intent.credited_vnd >= intent.expected_vnd:
                intent.status = "paid"
                intent.paid_at = now
            else:
                intent.status = "partial"
                
            pay_rec = Payment(
                provider="sepay",
                provider_tx_id=tx_id_str,
                reference_code=payload.referenceCode,
                amount_vnd=amount,
                content=payload.content,
                matched_kind="intent",
                wallet_id=wallet.id,
                intent_id=intent.id,
                status="credited",
                raw_json=raw_body,
                received_at=now
            )
            db.add(pay_rec)
            
            led = Ledger(
                wallet_id=wallet.id,
                delta_balance_vnd=amount,
                delta_held_vnd=0,
                balance_after_vnd=wallet.balance_vnd,
                held_after_vnd=wallet.held_vnd,
                reason="topup",
                ref_id=intent.id,
                actor="sepay",
                created_at=now
            )
            db.add(led)
            
            # Check if linked to pairing in awaiting_payment
            if intent.user_id:
                price = get_setting_int(db, "price_per_session_vnd", settings.DEFAULT_PRICE_PER_SESSION_VND)
                avail = wallet.balance_vnd - wallet.held_vnd
                
                pairing = db.query(Pairing).filter(
                    Pairing.user_id == intent.user_id,
                    Pairing.topup_id == intent.id,
                    Pairing.status == "awaiting_payment"
                ).first()
                if pairing:
                    needed = (pairing.planned_sessions or 1) * price
                    if avail >= needed:
                        pairing.status = "ready"
                        broadcast_user_event(intent.user_id, "pairing.updated", {
                            "id": pairing.id,
                            "status": "ready",
                            "remaining_sessions": pairing.planned_sessions
                        })
                        
                broadcast_user_event(intent.user_id, "topup.updated", {
                    "id": intent.id,
                    "status": intent.status,
                    "credited_vnd": intent.credited_vnd
                }, event_id=payload.id if isinstance(payload.id, int) else None)
                
                broadcast_user_event(intent.user_id, "wallet.updated", {
                    "available_vnd": avail,
                    "sessions_available": avail // price
                })
                
            db.commit()
            return {"success": True, "matched": "topup_intent", "intent_id": intent.id}
            
    elif code_type == "D":
        # Direct payment at booth: PBD + 8 chars
        intent = db.query(PaymentIntent).filter(
            PaymentIntent.ref == code_match,
            PaymentIntent.kind == "direct_pay"
        ).first()
        if intent:
            wallet = intent.wallet
            wallet.balance_vnd += amount
            intent.credited_vnd += amount
            intent.status = "paid"
            intent.paid_at = now
            
            pay_rec = Payment(
                provider="sepay",
                provider_tx_id=tx_id_str,
                reference_code=payload.referenceCode,
                amount_vnd=amount,
                content=payload.content,
                matched_kind="intent",
                wallet_id=wallet.id,
                intent_id=intent.id,
                status="credited",
                raw_json=raw_body,
                received_at=now
            )
            db.add(pay_rec)
            
            led = Ledger(
                wallet_id=wallet.id,
                delta_balance_vnd=amount,
                delta_held_vnd=0,
                balance_after_vnd=wallet.balance_vnd,
                held_after_vnd=wallet.held_vnd,
                reason="topup",
                ref_id=intent.id,
                actor="sepay",
                created_at=now
            )
            db.add(led)
            db.commit()
            return {"success": True, "matched": "direct_pay", "intent_id": intent.id}

    # If code was present but not found in database: save as unmatched for manual admin audit
    unmatched_pay = Payment(
        provider="sepay",
        provider_tx_id=tx_id_str,
        reference_code=payload.referenceCode,
        amount_vnd=amount,
        content=payload.content,
        matched_kind=None,
        wallet_id=None,
        intent_id=None,
        status="unmatched",
        raw_json=raw_body,
        received_at=now
    )
    db.add(unmatched_pay)
    db.commit()
    return {"success": True, "matched": False, "note": "Code not found in DB"}
