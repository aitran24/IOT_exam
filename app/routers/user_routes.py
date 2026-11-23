from datetime import datetime, timedelta, timezone
from typing import Optional, List
from fastapi import APIRouter, Depends, Header, Request, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User, UserIdentity, RefreshToken
from app.schemas import (
    UserProfileResponse, UserPatchBody, TermsAcceptBody,
    SessionItem, PushTokenBody, UserIdentityItem
)
from app.security import (
    verify_step_up_token, mask_phone, mask_email, decrypt_str, hmac_hash
)
from app.deps import raise_api_error, get_current_user

router = APIRouter(prefix="/api/v1/me", tags=["User Profile"])

@router.get("", response_model=UserProfileResponse)
def get_my_profile(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    identities_res = []
    phone_masked = None
    email_masked = None
    
    for ident in current_user.identities:
        identities_res.append(UserIdentityItem(
            provider=ident.provider,
            linked_at=ident.created_at.isoformat().replace("+00:00", "Z")
        ))
        if ident.provider == "phone":
            phone_dec = decrypt_str(ident.provider_uid_enc)
            phone_masked = mask_phone(phone_dec) if phone_dec and not phone_dec.startswith("phone_") else "+84•••••" + current_user.id[-4:]
        elif ident.provider == "google":
            dec_email = decrypt_str(ident.email_enc)
            email_masked = mask_email(dec_email) or "user••••@gmail.com"
            
    return UserProfileResponse(
        id=current_user.id,
        display_name=current_user.display_name,
        phone_masked=phone_masked,
        email_masked=email_masked,
        identities=identities_res,
        terms_accepted=bool(current_user.terms_accepted_at),
        created_at=current_user.created_at.isoformat().replace("+00:00", "Z")
    )

@router.patch("")
def update_profile(
    req: UserPatchBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    current_user.display_name = req.display_name.strip()
    db.commit()
    return {"id": current_user.id, "display_name": current_user.display_name}

@router.post("/terms")
def accept_terms(
    req: TermsAcceptBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    now = datetime.now(timezone.utc)
    current_user.terms_version = req.version
    current_user.terms_accepted_at = now
    db.commit()
    return {"status": "terms_accepted", "terms_version": req.version, "accepted_at": now.isoformat().replace("+00:00", "Z")}

@router.get("/sessions", response_model=dict)
def get_user_sessions(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    now = datetime.now(timezone.utc)
    active_tokens = db.query(RefreshToken).filter(
        RefreshToken.user_id == current_user.id,
        RefreshToken.revoked_at == None,
        RefreshToken.expires_at > now
    ).order_by(RefreshToken.created_at.desc()).all()
    
    items = []
    for t in active_tokens:
        items.append({
            "id": t.id,
            "device_name": t.device_name or "Unknown Device",
            "platform": t.platform or "web",
            "created_at": t.created_at.isoformat().replace("+00:00", "Z"),
            "last_used_at": t.last_used_at.isoformat().replace("+00:00", "Z") if t.last_used_at else None,
            "current": False
        })
    return {"items": items}

@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    t = db.query(RefreshToken).filter(
        RefreshToken.id == session_id,
        RefreshToken.user_id == current_user.id
    ).first()
    if t:
        t.revoked_at = datetime.now(timezone.utc)
        db.commit()
    return None

@router.post("/push-tokens")
def register_push_token(
    req: PushTokenBody,
    current_user: User = Depends(get_current_user)
):
    return {"status": "registered"}

@router.delete("", status_code=status.HTTP_202_ACCEPTED)
def request_account_deletion(
    x_step_up_token: Optional[str] = Header(None, alias="X-Step-Up-Token"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not x_step_up_token or not verify_step_up_token(x_step_up_token, current_user.id):
        raise_api_error(
            status_code=status.HTTP_403_FORBIDDEN,
            code="step_up_required",
            message="Thao tác này yêu cầu xác thực lại (Step-up authentication)"
        )
        
    now = datetime.now(timezone.utc)
    delete_after = now + timedelta(days=7)
    current_user.status = "pending_delete"
    current_user.delete_after = delete_after
    db.commit()
    
    return {
        "status": "pending_delete",
        "delete_after": delete_after.isoformat().replace("+00:00", "Z")
    }

@router.post("/delete/cancel")
def cancel_account_deletion(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    current_user.status = "active"
    current_user.delete_after = None
    db.commit()
    return {"status": "active", "message": "Đã hủy yêu cầu xóa tài khoản thành công"}
