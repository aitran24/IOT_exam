from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session
from app.config import settings
from app.models import Base, Setting

connect_args = {}
if settings.DATABASE_URL.startswith("sqlite"):
    connect_args["check_same_thread"] = False

engine = create_engine(
    settings.DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True
)

if settings.DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    """Create all tables and seed default settings if not exists."""
    Base.metadata.create_all(bind=engine)
    
    defaults = {
        "price_per_session_vnd": str(settings.DEFAULT_PRICE_PER_SESSION_VND),
        "shots": str(settings.DEFAULT_SHOTS),
        "countdown_first_s": str(settings.DEFAULT_COUNTDOWN_FIRST_S),
        "countdown_next_s": str(settings.DEFAULT_COUNTDOWN_NEXT_S),
        "max_sessions_per_pairing": str(settings.DEFAULT_MAX_SESSIONS_PER_PAIRING),
        "topup_min_vnd": str(settings.DEFAULT_TOPUP_MIN_VND),
        "topup_max_vnd": str(settings.DEFAULT_TOPUP_MAX_VND),
        "guest_retention_hours": str(settings.DEFAULT_GUEST_RETENTION_HOURS),
        "pairing_code_ttl_s": str(settings.DEFAULT_PAIRING_CODE_TTL_S),
        "pairing_idle_timeout_min": str(settings.DEFAULT_PAIRING_IDLE_TIMEOUT_MIN),
        "hold_ttl_min": str(settings.DEFAULT_HOLD_TTL_MIN),
        "otp_ttl_s": str(settings.DEFAULT_OTP_TTL_S),
        "direct_pay_ttl_min": str(settings.DEFAULT_DIRECT_PAY_TTL_MIN),
    }
    
    with SessionLocal() as db:
        for k, v in defaults.items():
            existing = db.query(Setting).filter(Setting.key == k).first()
            if not existing:
                db.add(Setting(key=k, value=v))
        db.commit()

def get_setting_int(db: Session, key: str, default: int) -> int:
    record = db.query(Setting).filter(Setting.key == key).first()
    if record:
        try:
            return int(record.value)
        except ValueError:
            pass
    return default

def get_setting_str(db: Session, key: str, default: str) -> str:
    record = db.query(Setting).filter(Setting.key == key).first()
    if record:
        return record.value
    return default
