import os
from dotenv import load_dotenv
from pydantic import BaseModel, Field

# Load .env file automatically
load_dotenv()

class Settings(BaseModel):
    # Environment
    ENV: str = Field(default_factory=lambda: os.getenv("ENV", "development"))
    DEBUG: bool = Field(default_factory=lambda: os.getenv("DEBUG", "False").lower() in ("true", "1"))
    
    # Base URL & Host
    BASE_URL: str = Field(default_factory=lambda: os.getenv("BASE_URL", "https://tinsmith-cattail-murmuring.ngrok-free.dev"))
    MEDIA_BASE_URL: str = Field(default_factory=lambda: os.getenv("MEDIA_BASE_URL", "https://tinsmith-cattail-murmuring.ngrok-free.dev"))
    
    # Database
    DATABASE_URL: str = Field(
        default_factory=lambda: os.getenv(
            "DATABASE_URL", 
            "sqlite:///./photobooth.db"
        )
    )
    
    # Cryptographic keys
    # Master key for AES-256-GCM encryption of sensitive database fields (must be 32 bytes)
    AES_MASTER_KEY: str = Field(
        default_factory=lambda: os.getenv(
            "AES_MASTER_KEY", 
            "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        )
    )
    # Pepper key for HMAC hashing (search index, OTP storage)
    HMAC_PEPPER_KEY: str = Field(
        default_factory=lambda: os.getenv(
            "HMAC_PEPPER_KEY", 
            "photobooth-super-secure-hmac-pepper-key-2026"
        )
    )
    # JWT signing secret
    JWT_SECRET: str = Field(
        default_factory=lambda: os.getenv(
            "JWT_SECRET", 
            "photobooth-jwt-super-secret-key-production-change-me"
        )
    )
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    STEP_UP_TOKEN_EXPIRE_SECONDS: int = 300
    
    # SePay Webhook
    SEPAY_API_KEY: str = Field(default_factory=lambda: os.getenv("SEPAY_API_KEY", "sepay_secret_key_123456"))
    BANK_NAME: str = Field(default_factory=lambda: os.getenv("BANK_NAME", "BIDV"))
    BANK_BIN: str = Field(default_factory=lambda: os.getenv("BANK_BIN", "970418")) # BIDV BIN
    ACCOUNT_NUMBER: str = Field(default_factory=lambda: os.getenv("ACCOUNT_NUMBER", "962472HDYO"))
    ACCOUNT_NAME: str = Field(default_factory=lambda: os.getenv("ACCOUNT_NAME", "VO THI QUYNH NGA"))
    
    # SMTP Email Configuration (Brevo / Sendinblue)
    SMTP_HOST: str = Field(default_factory=lambda: os.getenv("SMTP_HOST", "smtp-relay.brevo.com"))
    SMTP_PORT: int = Field(default_factory=lambda: int(os.getenv("SMTP_PORT", "587")))
    SMTP_USER: str = Field(default_factory=lambda: os.getenv("SMTP_USER", ""))
    SMTP_PASSWORD: str = Field(default_factory=lambda: os.getenv("SMTP_PASSWORD", ""))
    SMTP_FROM_NAME: str = Field(default_factory=lambda: os.getenv("SMTP_FROM_NAME", "ZenApp"))
    SMTP_FROM_EMAIL: str = Field(default_factory=lambda: os.getenv("SMTP_FROM_EMAIL", ""))
    SMTP_USE_TLS: bool = Field(default_factory=lambda: os.getenv("SMTP_USE_TLS", "True").lower() in ("true", "1"))
    
    # Storage
    STORAGE_DIR: str = Field(default_factory=lambda: os.getenv("STORAGE_DIR", "./storage"))
    MAX_IMAGE_BYTES: int = 8_000_000
    MAX_VIDEO_BYTES: int = 30_000_000
    
    # Business logic defaults (Mục 8 & 16)
    DEFAULT_PRICE_PER_SESSION_VND: int = 2000
    DEFAULT_SHOTS: int = 4
    DEFAULT_COUNTDOWN_FIRST_S: int = 4
    DEFAULT_COUNTDOWN_NEXT_S: int = 4
    DEFAULT_MAX_SESSIONS_PER_PAIRING: int = 3
    DEFAULT_TOPUP_MIN_VND: int = 2000
    DEFAULT_TOPUP_MAX_VND: int = 1_000_000
    DEFAULT_GUEST_RETENTION_HOURS: int = 24
    DEFAULT_PAIRING_CODE_TTL_S: int = 120
    DEFAULT_PAIRING_IDLE_TIMEOUT_MIN: int = 15
    DEFAULT_HOLD_TTL_MIN: int = 15
    DEFAULT_OTP_TTL_S: int = 300
    DEFAULT_DIRECT_PAY_TTL_MIN: int = 5
    TERMS_VERSION: str = "2026-11"
    TERMS_URL: str = "https://booth.tenclb.vn/terms"
    PRIVACY_URL: str = "https://booth.tenclb.vn/privacy"
    MIN_APP_VERSION: str = "1.0.0"

settings = Settings()
