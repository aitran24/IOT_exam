from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import (
    Column,
    String,
    Integer,
    BigInteger,
    Float,
    Boolean,
    DateTime,
    ForeignKey,
    LargeBinary,
    JSON,
    Index,
    CheckConstraint,
    UniqueConstraint
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

def utcnow():
    return datetime.now(timezone.utc)

class User(Base):
    __tablename__ = "users"
    
    id = Column(String, primary_key=True) # usr_ + ULID
    display_name = Column(String, nullable=True)
    status = Column(String, nullable=False, default="active") # active | locked | pending_delete
    terms_version = Column(String, nullable=True)
    terms_accepted_at = Column(DateTime(timezone=True), nullable=True)
    delete_after = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    
    identities = relationship("UserIdentity", back_populates="user", cascade="all, delete-orphan")
    wallet = relationship("Wallet", back_populates="user", uselist=False)
    refresh_tokens = relationship("RefreshToken", back_populates="user", cascade="all, delete-orphan")
    sessions = relationship("Session", back_populates="user")

class UserIdentity(Base):
    __tablename__ = "user_identities"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    provider = Column(String, nullable=False) # phone | google
    provider_uid_hash = Column(LargeBinary, nullable=False)
    provider_uid_enc = Column(LargeBinary, nullable=False)
    email_enc = Column(LargeBinary, nullable=True)
    email_verified = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    
    user = relationship("User", back_populates="identities")
    __table_args__ = (
        UniqueConstraint("provider", "provider_uid_hash", name="uq_provider_uid_hash"),
    )

class OTPRequest(Base):
    __tablename__ = "otp_requests"
    
    id = Column(String, primary_key=True) # otp_ + ULID
    phone_hash = Column(LargeBinary, nullable=True)
    email_hash = Column(LargeBinary, nullable=True)
    email_enc = Column(LargeBinary, nullable=True)
    purpose = Column(String, nullable=False) # login | link_phone | step_up
    user_id = Column(String, nullable=True)
    code_hmac = Column(LargeBinary, nullable=False)
    attempts = Column(Integer, nullable=False, default=0)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    consumed_at = Column(DateTime(timezone=True), nullable=True)
    ip = Column(String, nullable=True)
    device_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    
    id = Column(String, primary_key=True) # rt_ + ULID
    family_id = Column(String, nullable=False)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash = Column(LargeBinary, nullable=False, unique=True)
    device_id = Column(String, nullable=True)
    device_name = Column(String, nullable=True)
    platform = Column(String, nullable=True)
    ip = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    rotated_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    
    user = relationship("User", back_populates="refresh_tokens")

class Wallet(Base):
    __tablename__ = "wallets"
    
    id = Column(String, primary_key=True) # wal_ + ULID
    kind = Column(String, nullable=False) # guest | user
    code = Column(String, unique=True, nullable=True) # 8 chars, guest only
    user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), unique=True, nullable=True)
    temporary = Column(Boolean, nullable=False, default=False)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    balance_vnd = Column(BigInteger, nullable=False, default=0)
    held_vnd = Column(BigInteger, nullable=False, default=0)
    status = Column(String, nullable=False, default="active") # active | locked
    failed_attempts = Column(Integer, nullable=False, default=0)
    locked_until = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    
    user = relationship("User", back_populates="wallet")
    ledger_entries = relationship("Ledger", back_populates="wallet", cascade="all, delete-orphan")
    intents = relationship("PaymentIntent", back_populates="wallet")
    sessions = relationship("Session", back_populates="wallet")
    
    __table_args__ = (
        CheckConstraint("balance_vnd >= 0", name="chk_wallet_balance_positive"),
        CheckConstraint("held_vnd >= 0 AND held_vnd <= balance_vnd", name="chk_wallet_held_range"),
    )

class PaymentIntent(Base):
    __tablename__ = "payment_intents"
    
    id = Column(String, primary_key=True) # tp_ | dp_ + ULID
    kind = Column(String, nullable=False) # topup | direct_pay
    ref = Column(String, unique=True, nullable=False) # PBI... or PBD...
    wallet_id = Column(String, ForeignKey("wallets.id"), nullable=False)
    user_id = Column(String, nullable=True)
    device_id = Column(String, nullable=True)
    expected_vnd = Column(BigInteger, nullable=False)
    credited_vnd = Column(BigInteger, nullable=False, default=0)
    status = Column(String, nullable=False, default="pending") # pending | partial | paid | expired | cancelled
    idempotency_key = Column(String, nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    paid_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    
    wallet = relationship("Wallet", back_populates="intents")
    payments = relationship("Payment", back_populates="intent")

class Payment(Base):
    __tablename__ = "payments"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    provider = Column(String, nullable=False, default="sepay")
    provider_tx_id = Column(String, unique=True, nullable=False)
    reference_code = Column(String, nullable=True)
    amount_vnd = Column(BigInteger, nullable=False)
    content = Column(String, nullable=True)
    matched_kind = Column(String, nullable=True) # guest_wallet | intent | NULL
    wallet_id = Column(String, ForeignKey("wallets.id"), nullable=True)
    intent_id = Column(String, ForeignKey("payment_intents.id"), nullable=True)
    status = Column(String, nullable=False, default="credited") # credited | unmatched | ignored
    raw_json = Column(JSON, nullable=True)
    received_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    
    wallet = relationship("Wallet")
    intent = relationship("PaymentIntent", back_populates="payments")

class Ledger(Base):
    __tablename__ = "ledger"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    wallet_id = Column(String, ForeignKey("wallets.id"), nullable=False)
    delta_balance_vnd = Column(BigInteger, nullable=False)
    delta_held_vnd = Column(BigInteger, nullable=False, default=0)
    balance_after_vnd = Column(BigInteger, nullable=False)
    held_after_vnd = Column(BigInteger, nullable=False)
    reason = Column(String, nullable=False) # topup | hold | release | session | refund | admin_adjust
    ref_id = Column(String, nullable=True)
    actor = Column(String, nullable=True) # system | admin:<name> | device:<id> | user:<id>
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    
    wallet = relationship("Wallet", back_populates="ledger_entries")

class Device(Base):
    __tablename__ = "devices"
    
    id = Column(String, primary_key=True) # e.g. dev_01
    name = Column(String, nullable=True)
    token_hash = Column(LargeBinary, nullable=False)
    mode = Column(String, nullable=False, default="online") # online | maintenance
    last_seen = Column(DateTime(timezone=True), nullable=True)
    cpu_temp = Column(Float, nullable=True)
    disk_free_mb = Column(Integer, nullable=True)
    camera_ok = Column(Boolean, nullable=True)
    app_version = Column(String, nullable=True)
    
    pairing_codes = relationship("PairingCode", back_populates="device")
    pairings = relationship("Pairing", back_populates="device")
    sessions = relationship("Session", back_populates="device")
    commands = relationship("DeviceCommand", back_populates="device")

class PairingCode(Base):
    __tablename__ = "pairing_codes"
    
    id = Column(String, primary_key=True) # pc_ + ULID
    device_id = Column(String, ForeignKey("devices.id"), nullable=False)
    code_hash = Column(LargeBinary, nullable=False)
    token_hash = Column(LargeBinary, nullable=False)
    status = Column(String, nullable=False, default="waiting") # waiting | claimed | rejected | expired
    claimed_by = Column(String, ForeignKey("users.id"), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    
    device = relationship("Device", back_populates="pairing_codes")

class Pairing(Base):
    __tablename__ = "pairings"
    
    id = Column(String, primary_key=True) # pr_ + ULID
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    device_id = Column(String, ForeignKey("devices.id"), nullable=False)
    code_id = Column(String, ForeignKey("pairing_codes.id"), nullable=True)
    status = Column(String, nullable=False, default="claimed") # claimed | awaiting_payment | ready | in_use | finished | cancelled | expired
    planned_sessions = Column(Integer, nullable=True)
    completed_sessions = Column(Integer, nullable=False, default=0)
    topup_id = Column(String, ForeignKey("payment_intents.id"), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    
    user = relationship("User")
    device = relationship("Device", back_populates="pairings")
    pairing_code = relationship("PairingCode")
    topup = relationship("PaymentIntent")
    sessions = relationship("Session", back_populates="pairing")

class Session(Base):
    __tablename__ = "sessions"
    
    id = Column(String, primary_key=True) # ses_ + ULID
    wallet_id = Column(String, ForeignKey("wallets.id"), nullable=False)
    user_id = Column(String, ForeignKey("users.id"), nullable=True)
    pairing_id = Column(String, ForeignKey("pairings.id"), nullable=True)
    device_id = Column(String, ForeignKey("devices.id"), nullable=False)
    layout = Column(String, nullable=True)
    filter = Column(String, nullable=True)
    frame_color = Column(String, nullable=True)
    color_filter = Column(String, nullable=True)
    shots = Column(Integer, nullable=True)
    status = Column(String, nullable=False, default="started") # started | completed | cancelled | failed | expired
    hold_expires_at = Column(DateTime(timezone=True), nullable=True)
    share_token = Column(String, unique=True, nullable=True) # guest download token
    share_expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    
    wallet = relationship("Wallet", back_populates="sessions")
    user = relationship("User", back_populates="sessions")
    pairing = relationship("Pairing", back_populates="sessions")
    device = relationship("Device", back_populates="sessions")
    media = relationship("Media", back_populates="session", cascade="all, delete-orphan")

class Media(Base):
    __tablename__ = "media"
    
    id = Column(String, primary_key=True) # med_ + ULID
    session_id = Column(String, ForeignKey("sessions.id"), nullable=False)
    kind = Column(String, nullable=False) # strip | shot | video
    idx = Column(Integer, nullable=True)
    content_type = Column(String, nullable=False)
    bytes = Column(BigInteger, nullable=False)
    sha256 = Column(String, nullable=False)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    storage_key = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    
    session = relationship("Session", back_populates="media")

class Admin(Base):
    __tablename__ = "admins"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False) # argon2
    totp_secret_enc = Column(LargeBinary, nullable=False)
    role = Column(String, nullable=False) # owner | staff

class AuditLog(Base):
    __tablename__ = "audit_log"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    actor = Column(String, nullable=False) # username or system
    action = Column(String, nullable=False)
    target = Column(String, nullable=True)
    detail = Column(JSON, nullable=True)
    ip = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

class Setting(Base):
    __tablename__ = "settings"
    
    key = Column(String, primary_key=True)
    value = Column(String, nullable=False)

class DeviceCommand(Base):
    __tablename__ = "device_commands"
    
    id = Column(String, primary_key=True) # cmd_ + ULID
    device_id = Column(String, ForeignKey("devices.id"), nullable=False)
    type = Column(String, nullable=False) # lock | unlock | restart_app | reboot | update_config
    params = Column(JSON, nullable=True)
    status = Column(String, nullable=False, default="pending") # pending | acked | expired
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    acked_at = Column(DateTime(timezone=True), nullable=True)
    
    device = relationship("Device", back_populates="commands")

class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    
    key = Column(String, primary_key=True)
    scope = Column(String, nullable=False)
    request_hash = Column(String, nullable=False)
    status_code = Column(Integer, nullable=False)
    response_json = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    expires_at = Column(DateTime(timezone=True), nullable=False)
