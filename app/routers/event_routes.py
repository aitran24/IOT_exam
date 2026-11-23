import asyncio
import json
import time
from typing import Optional, Dict, Set
from fastapi import APIRouter, Depends, Query, Request, Header
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User
from app.security import gen_ulid
from app.deps import get_current_user, raise_api_error

router = APIRouter(prefix="/api/v1/events", tags=["Real-time Events (SSE)"])

# Ticket store: ticket_id -> (user_id, expiry_ts)
_event_tickets: Dict[str, tuple[str, float]] = {}

# User event queues: user_id -> Set[asyncio.Queue]
_user_queues: Dict[str, Set[asyncio.Queue]] = {}

def broadcast_user_event(user_id: str, event_type: str, data: dict, event_id: Optional[int] = None):
    """Deliver an event to all open SSE connections of user_id."""
    queues = _user_queues.get(user_id)
    if not queues:
        return
    msg = {
        "event": event_type,
        "id": event_id or int(time.time()),
        "data": json.dumps(data)
    }
    for q in list(queues):
        try:
            q.put_nowait(msg)
        except Exception:
            pass

@router.post("/ticket")
def create_event_ticket(
    current_user: User = Depends(get_current_user)
):
    ticket_id = gen_ulid("ev_")
    _event_tickets[ticket_id] = (current_user.id, time.time() + 30.0)
    return {"ticket": ticket_id, "expires_in_s": 30}

@router.get("")
async def sse_events(
    request: Request,
    ticket: Optional[str] = Query(None),
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    user_id = None
    if ticket:
        record = _event_tickets.pop(ticket, None)
        if record and record[1] > time.time():
            user_id = record[0]
            
    if not user_id and authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ")[1].strip()
        try:
            from app.security import decode_access_token
            payload = decode_access_token(token)
            user_id = payload.get("sub")
        except Exception:
            pass
            
    if not user_id:
        raise_api_error(status_code=401, code="unauthorized", message="Cần ticket hoặc access token hợp lệ")
        
    queue: asyncio.Queue = asyncio.Queue()
    _user_queues.setdefault(user_id, set()).add(queue)
    
    async def event_generator():
        try:
            # Welcome comment
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    # Wait for next event or 25s timeout for ping comment
                    msg = await asyncio.wait_for(queue.get(), timeout=25.0)
                    evt = msg["event"]
                    eid = msg["id"]
                    dt = msg["data"]
                    yield f"event: {evt}\nid: {eid}\ndata: {dt}\n\n"
                except asyncio.TimeoutError:
                    # Server sends comment every 25s to keep connection alive (Section 10.7)
                    yield ": ping\n\n"
        finally:
            user_qs = _user_queues.get(user_id)
            if user_qs and queue in user_qs:
                user_qs.remove(queue)
                if not user_qs:
                    _user_queues.pop(user_id, None)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )
