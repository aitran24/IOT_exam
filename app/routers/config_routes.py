from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db, get_setting_int, get_setting_str
from app.schemas import ConfigResponse, ConfigTopup, ConfigPairing, ConfigAuth
from app.config import settings

router = APIRouter(prefix="/api/v1", tags=["Config"])

@router.get("/config", response_model=ConfigResponse)
def get_public_config(db: Session = Depends(get_db)):
    price = get_setting_int(db, "price_per_session_vnd", settings.DEFAULT_PRICE_PER_SESSION_VND)
    max_sess = get_setting_int(db, "max_sessions_per_pairing", settings.DEFAULT_MAX_SESSIONS_PER_PAIRING)
    topup_min = get_setting_int(db, "topup_min_vnd", settings.DEFAULT_TOPUP_MIN_VND)
    topup_max = get_setting_int(db, "topup_max_vnd", settings.DEFAULT_TOPUP_MAX_VND)
    code_ttl = get_setting_int(db, "pairing_code_ttl_s", settings.DEFAULT_PAIRING_CODE_TTL_S)
    idle_timeout = get_setting_int(db, "pairing_idle_timeout_min", settings.DEFAULT_PAIRING_IDLE_TIMEOUT_MIN)
    otp_ttl = get_setting_int(db, "otp_ttl_s", settings.DEFAULT_OTP_TTL_S)
    
    return ConfigResponse(
        price_per_session_vnd=price,
        max_sessions_per_pairing=max_sess,
        topup=ConfigTopup(min_vnd=topup_min, max_vnd=topup_max),
        pairing=ConfigPairing(code_length=6, code_ttl_s=code_ttl, idle_timeout_min=idle_timeout),
        auth=ConfigAuth(providers=["phone", "google"], otp_length=6, otp_ttl_s=otp_ttl, resend_after_s=60),
        min_app_version=settings.MIN_APP_VERSION,
        terms_version=settings.TERMS_VERSION,
        terms_url=settings.TERMS_URL,
        privacy_url=settings.PRIVACY_URL
    )
