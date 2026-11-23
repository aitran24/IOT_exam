import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Optional, Callable, Any
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from app.models import IdempotencyRecord
from app.deps import raise_api_error

def check_or_save_idempotency(
    db: Session,
    key: Optional[str],
    scope: str,
    payload_dict: Any,
    execute_fn: Callable[[], tuple[int, dict]]
) -> tuple[int, dict]:
    """
    Handles Idempotency-Key caching (24h TTL) per Section 9.
    If key is None, simply executes execute_fn.
    """
    if not key:
        return execute_fn()
        
    req_str = json.dumps(payload_dict, sort_keys=True, default=str)
    req_hash = hashlib.sha256(req_str.encode("utf-8")).hexdigest()
    
    now = datetime.now(timezone.utc)
    rec = db.query(IdempotencyRecord).filter(IdempotencyRecord.key == key).first()
    
    if rec:
        rec_exp = rec.expires_at.replace(tzinfo=timezone.utc) if rec.expires_at.tzinfo is None else rec.expires_at
        if rec_exp < now:
            # Expired record, remove and recreate
            db.delete(rec)
            db.commit()
        else:
            if rec.request_hash != req_hash or rec.scope != scope:
                raise_api_error(
                    status_code=409,
                    code="idempotency_conflict",
                    message="Cùng Idempotency-Key nhưng nội dung yêu cầu khác nhau"
                )
            return rec.status_code, rec.response_json
            
    # Not found or expired, execute
    status_code, resp_data = execute_fn()
    
    # Save record
    new_rec = IdempotencyRecord(
        key=key,
        scope=scope,
        request_hash=req_hash,
        status_code=status_code,
        response_json=resp_data,
        created_at=now,
        expires_at=now + timedelta(hours=24)
    )
    db.add(new_rec)
    db.commit()
    return status_code, resp_data
