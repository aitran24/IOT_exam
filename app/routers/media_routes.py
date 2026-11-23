import os
from fastapi import APIRouter, Query, status
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Media
from app.security import verify_media_sig
from app.deps import raise_api_error

router = APIRouter(prefix="/media", tags=["Media Serving"])

@router.get("/m/{media_id}")
def serve_signed_media(
    media_id: str,
    exp: int = Query(...),
    sig: str = Query(...),
    download: int = Query(0)
):
    if not verify_media_sig(media_id, exp, sig):
        raise_api_error(
            status_code=status.HTTP_403_FORBIDDEN,
            code="signature_invalid",
            message="Đường dẫn xem ảnh/video không hợp lệ hoặc đã hết hạn"
        )
        
    from app.database import SessionLocal
    with SessionLocal() as db:
        media = db.query(Media).filter(Media.id == media_id, Media.deleted_at == None).first()
        file_path = media.storage_key
        if not os.path.exists(file_path):
            server_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            alt_path = os.path.join(server_dir, file_path.lstrip("./"))
            if os.path.exists(alt_path):
                file_path = alt_path
            else:
                raise_api_error(status_code=404, code="not_found", message="Tệp đa phương tiện không tồn tại hoặc đã bị xóa")
            
        filename = os.path.basename(file_path)
        headers = {
            "Cache-Control": "private, max-age=900",
            "X-Content-Type-Options": "nosniff"
        }
        if download == 1:
            headers["Content-Disposition"] = f'attachment; filename="{filename}"'
            
        return FileResponse(
            path=file_path,
            media_type=media.content_type,
            headers=headers
        )
