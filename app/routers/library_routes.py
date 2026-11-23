import os
import time
from datetime import datetime, timezone
from typing import Optional, List
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.database import get_db
from app.config import settings
from app.models import User, Session as PhotoSession, Media
from app.schemas import (
    LibraryListResponse, LibraryItemBrief, MachineBrief,
    LibraryDetailResponse, MediaFile, DeleteBatchBody
)
from app.security import sign_media_url
from app.deps import raise_api_error, get_current_user

router = APIRouter(prefix="/api/v1/library", tags=["Photo Library"])

def build_signed_media_url(media: Media, download: bool = False) -> tuple[str, str]:
    now_ts = int(time.time())
    exp_ts = now_ts + (15 * 60) # 15 mins expiry
    sig = sign_media_url(media.id, exp_ts)
    dl_param = "&download=1" if download else ""
    url = f"{settings.MEDIA_BASE_URL}/media/m/{media.id}?exp={exp_ts}&sig={sig}{dl_param}"
    exp_iso = datetime.fromtimestamp(exp_ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    return url, exp_iso

@router.get("", response_model=LibraryListResponse)
def list_library(
    limit: int = Query(20, ge=1, le=50),
    cursor: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    query = db.query(PhotoSession).filter(
        PhotoSession.user_id == current_user.id,
        PhotoSession.status == "completed",
        PhotoSession.deleted_at == None
    )
    
    if cursor:
        cursor_sess = db.query(PhotoSession).filter(PhotoSession.id == cursor).first()
        if cursor_sess:
            query = query.filter(PhotoSession.created_at < cursor_sess.created_at)
            
    sessions = query.order_by(PhotoSession.created_at.desc()).limit(limit + 1).all()
    has_more = len(sessions) > limit
    rows = sessions[:limit]
    next_cursor = rows[-1].id if has_more and rows else None
    
    # Calculate usage
    total_items = db.query(PhotoSession).filter(
        PhotoSession.user_id == current_user.id,
        PhotoSession.status == "completed",
        PhotoSession.deleted_at == None
    ).count()
    
    total_bytes = db.query(func.coalesce(func.sum(Media.bytes), 0)).join(PhotoSession).filter(
        PhotoSession.user_id == current_user.id,
        PhotoSession.status == "completed",
        PhotoSession.deleted_at == None
    ).scalar() or 0
    
    items = []
    for s in rows:
        media_list = db.query(Media).filter(Media.session_id == s.id, Media.deleted_at == None).all()
        strip_media = next((m for m in media_list if m.kind == "strip"), None)
        has_video = any(m.kind == "video" for m in media_list)
        session_bytes = sum(m.bytes for m in media_list)
        
        thumb_url = None
        if strip_media:
            thumb_url, _ = build_signed_media_url(strip_media)
            
        device = s.device
        items.append(LibraryItemBrief(
            id=s.id,
            created_at=s.created_at.isoformat().replace("+00:00", "Z"),
            layout=s.layout,
            filter=s.filter,
            frame_color=s.frame_color,
            color_filter=s.color_filter,
            machine=MachineBrief(
                id=device.id if device else s.device_id,
                name=device.name if device and device.name else "Photobooth QNV&AT"
            ),
            has_video=has_video,
            bytes=session_bytes,
            thumbnail_url=thumb_url
        ))
        
    return LibraryListResponse(
        items=items,
        next_cursor=next_cursor,
        usage={"items": total_items, "bytes": int(total_bytes)}
    )

@router.get("/{session_id}", response_model=LibraryDetailResponse)
def get_library_item(
    session_id: str,
    download: bool = Query(False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    session = db.query(PhotoSession).filter(
        PhotoSession.id == session_id,
        PhotoSession.user_id == current_user.id,
        PhotoSession.deleted_at == None
    ).first()
    
    if not session or session.status != "completed":
        raise_api_error(status_code=404, code="not_found", message="Không tìm thấy mục thư viện này")
        
    media_list = db.query(Media).filter(
        Media.session_id == session.id,
        Media.deleted_at == None
    ).all()
    
    media_files = []
    for m in media_list:
        url, exp_iso = build_signed_media_url(m, download=download)
        duration_s = (m.duration_ms / 1000.0) if m.duration_ms else None
        media_files.append(MediaFile(
            id=m.id,
            kind=m.kind,
            index=m.idx,
            content_type=m.content_type,
            bytes=m.bytes,
            width=m.width,
            height=m.height,
            duration_s=duration_s,
            url=url,
            url_expires_at=exp_iso
        ))
        
    return LibraryDetailResponse(
        id=session.id,
        created_at=session.created_at.isoformat().replace("+00:00", "Z"),
        layout=session.layout,
        filter=session.filter,
        frame_color=session.frame_color,
        color_filter=session.color_filter,
        media=media_files
    )

@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_library_item(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    session = db.query(PhotoSession).filter(
        PhotoSession.id == session_id,
        PhotoSession.user_id == current_user.id,
        PhotoSession.deleted_at == None
    ).first()
    
    if session:
        now = datetime.now(timezone.utc)
        session.deleted_at = now
        # Also clean up files on disk if present
        media_list = db.query(Media).filter(Media.session_id == session.id).all()
        for m in media_list:
            m.deleted_at = now
            if m.storage_key and os.path.exists(m.storage_key):
                try:
                    os.remove(m.storage_key)
                except Exception:
                    pass
        db.commit()
    return None

@router.post("/delete-batch")
def delete_batch(
    req: DeleteBatchBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if len(req.ids) > 50:
        raise_api_error(status_code=400, code="too_many_ids", message="Tối đa 50 mục mỗi lần xóa")
        
    now = datetime.now(timezone.utc)
    deleted = []
    not_found = []
    
    for sid in req.ids:
        session = db.query(PhotoSession).filter(
            PhotoSession.id == sid,
            PhotoSession.user_id == current_user.id,
            PhotoSession.deleted_at == None
        ).first()
        if session:
            session.deleted_at = now
            media_list = db.query(Media).filter(Media.session_id == session.id).all()
            for m in media_list:
                m.deleted_at = now
                if m.storage_key and os.path.exists(m.storage_key):
                    try:
                        os.remove(m.storage_key)
                    except Exception:
                        pass
            deleted.append(sid)
        else:
            not_found.append(sid)
            
    db.commit()
    return {"deleted": deleted, "not_found": not_found}
