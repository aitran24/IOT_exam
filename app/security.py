import hmac
import hashlib
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import ulid
import pyotp
from app.config import settings

# 31-character alphabet without 0, 1, I, L, O (Mục 11 & 15.2)
ALPHABET_31 = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"

ph = PasswordHasher()

def get_aes_key() -> bytes:
    """Returns 32-byte AES-256 key from settings."""
    raw = settings.AES_MASTER_KEY
    if len(raw) == 64:
        try:
            return bytes.fromhex(raw)
        except ValueError:
            pass
    return hashlib.sha256(raw.encode()).digest()

AES_KEY = get_aes_key()
PEPPER_KEY = settings.HMAC_PEPPER_KEY.encode()

def gen_ulid(prefix: str = "") -> str:
    """Generate prefixed ULID string (e.g., usr_01J9..., ses_01J9...)."""
    return f"{prefix}{ulid.ULID()}"

def gen_code(length: int = 8) -> str:
    """Generate cryptographically secure string from the 31-character alphabet."""
    return "".join(secrets.choice(ALPHABET_31) for _ in range(length))

def encrypt_bytes(data: bytes) -> bytes:
    """Encrypt raw bytes using AES-256-GCM (12-byte nonce prepended)."""
    if not data:
        return b""
    aesgcm = AESGCM(AES_KEY)
    nonce = secrets.token_bytes(12)
    ciphertext = aesgcm.encrypt(nonce, data, None)
    return nonce + ciphertext

def decrypt_bytes(encrypted_data: bytes) -> bytes:
    """Decrypt raw bytes using AES-256-GCM."""
    if not encrypted_data or len(encrypted_data) < 28: # 12 nonce + 16 tag
        return b""
    aesgcm = AESGCM(AES_KEY)
    nonce = encrypted_data[:12]
    ciphertext = encrypted_data[12:]
    return aesgcm.decrypt(nonce, ciphertext, None)

def encrypt_str(text: Optional[str]) -> Optional[bytes]:
    if text is None:
        return None
    return encrypt_bytes(text.encode("utf-8"))

def decrypt_str(enc: Optional[bytes]) -> Optional[str]:
    if enc is None or len(enc) == 0:
        return None
    try:
        dec = decrypt_bytes(enc)
        return dec.decode("utf-8")
    except Exception:
        return None

def hmac_hash(data: str | bytes) -> bytes:
    """HMAC-SHA256 with pepper key (for searchable column hashes & OTP)."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hmac.new(PEPPER_KEY, data, hashlib.sha256).digest()

def hmac_hash_hex(data: str | bytes) -> str:
    return hmac_hash(data).hex()

def hash_password(password: str) -> str:
    """Argon2 password hashing for admin passwords."""
    return ph.hash(password)

def verify_password(password: str, hashed: str) -> bool:
    """Verify password against Argon2 hash."""
    try:
        return ph.verify(hashed, password)
    except (VerifyMismatchError, InvalidHashError):
        return False

def generate_totp_secret() -> str:
    """Generate a random base32 TOTP secret."""
    return pyotp.random_base32()

def get_totp_uri(username: str, secret: str, issuer: str = "Photobooth QNV&AT") -> str:
    """Generate provisioning URI for authenticator apps."""
    totp = pyotp.TOTP(secret)
    return totp.provisioning_uri(name=username, issuer_name=issuer)

def verify_totp(secret: str, code: str) -> bool:
    """Verify 6-digit TOTP code (permits 1 window drift)."""
    if not secret or not code:
        return False
    totp = pyotp.TOTP(secret)
    return bool(totp.verify(code.strip(), valid_window=1))

def create_access_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """Create JWT access token (15 mins default)."""
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
        "aud": "photobooth_app"
    })
    return jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)

def decode_access_token(token: str) -> Dict[str, Any]:
    """Decode and validate JWT access token."""
    return jwt.decode(
        token, 
        settings.JWT_SECRET, 
        algorithms=[settings.JWT_ALGORITHM],
        audience="photobooth_app"
    )

def create_step_up_token(user_id: str, purpose: str = "step_up") -> str:
    """Create step-up authentication token valid for 5 minutes (Section 10.2)."""
    su_id = gen_ulid("su_")
    now = datetime.now(timezone.utc)
    expire = now + timedelta(seconds=settings.STEP_UP_TOKEN_EXPIRE_SECONDS)
    payload = {
        "jti": su_id,
        "sub": user_id,
        "purpose": purpose,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
        "aud": "photobooth_stepup"
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)

def verify_step_up_token(token: str, user_id: str) -> bool:
    """Verify step-up token."""
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
            audience="photobooth_stepup"
        )
        return payload.get("sub") == user_id
    except Exception:
        return False

def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Ensure datetime object is timezone-aware UTC (important for SQLite compatibility)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt

def sign_media_url(media_id: str, exp_timestamp: int) -> str:
    """HMAC signature for temporary media URLs (Section 10.6 & 15.4)."""
    msg = f"{media_id}:{exp_timestamp}".encode("utf-8")
    sig = hmac.new(PEPPER_KEY, msg, hashlib.sha256).hexdigest()
    return sig

def verify_media_sig(media_id: str, exp_timestamp: int, signature: str) -> bool:
    """Verify HMAC signature and check expiry."""
    now_ts = int(time.time())
    if exp_timestamp < now_ts:
        return False
    expected_sig = sign_media_url(media_id, exp_timestamp)
    return secrets.compare_digest(expected_sig, signature)

def mask_phone(phone: Optional[str]) -> Optional[str]:
    """Mask phone number according to privacy guidelines (e.g. +84•••••4567)."""
    if not phone:
        return None
    phone = phone.strip()
    if len(phone) >= 9:
        prefix = phone[:3] # e.g. +84
        suffix = phone[-4:] # e.g. 4567
        return f"{prefix}•••••{suffix}"
    return phone

def mask_email(email: Optional[str]) -> Optional[str]:
    """Mask email according to privacy guidelines (e.g. l•••@gmail.com)."""
    if not email or "@" not in email:
        return None
    parts = email.split("@")
    user, domain = parts[0], parts[1]
    if len(user) > 1:
        masked_user = user[0] + "•••"
    else:
        masked_user = user + "•••"
    return f"{masked_user}@{domain}"

# ----------------- VIETNAM TIMEZONE (UTC+7) -----------------
VN_TZ = timezone(timedelta(hours=7))

def now_vn() -> datetime:
    """Current datetime in Vietnam timezone (UTC+7)."""
    return datetime.now(VN_TZ)

def to_utc_iso(dt: Optional[datetime]) -> Optional[str]:
    """Convert any datetime to a valid ISO 8601 UTC string ending in 'Z'."""
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")

def to_vn_datetime(dt: Optional[datetime]) -> Optional[datetime]:
    """Convert any datetime to Vietnam timezone (UTC+7)."""
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(VN_TZ)

def to_vn_str(dt: Optional[datetime], fmt: str = "%H:%M:%S %d/%m/%Y") -> str:
    """Format datetime in Vietnam timezone (UTC+7)."""
    vn_dt = to_vn_datetime(dt)
    if not vn_dt:
        return ""
    return vn_dt.strftime(fmt)

