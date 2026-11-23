"""Cấu hình chung. Sửa số ở đây, không cần đụng vào code khác."""
import os
import socket
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA_DIR = BASE / "data" / "sessions"

# --- màn hình ---
SCREEN_W, SCREEN_H = 320, 240
WINDOWED = os.environ.get("PB_WINDOW") == "1"      # PB_WINDOW=1: chạy cửa sổ trên máy tính để thử
FAKE_CAM = os.environ.get("PB_FAKE_CAM") == "1"    # PB_FAKE_CAM=1: không cần webcam (để thử giao diện)
FAST = os.environ.get("PB_FAST") == "1"            # PB_FAST=1: đếm ngược 1 giây (để thử nhanh)

# --- camera ---
_cam = os.environ.get("PB_CAM", "/dev/video0")
CAM_DEVICE = int(_cam) if _cam.isdigit() else _cam
CAP_W, CAP_H = 1280, 720      # độ phân giải chụp (webcam của bạn hỗ trợ MJPG 1280x720 và 1920x1080)
MIRROR_PREVIEW = True         # xem trước dạng gương cho khách dễ căn
MIRROR_SAVED = True           # ảnh lưu có lật gương hay không (khớp 100% màn hình)

# --- vòng chụp ---
SHOTS = 4
FIRST_COUNTDOWN = 1 if FAST else 4
NEXT_COUNTDOWN = 1 if FAST else 4
FLASH_SECONDS = 0.15
DONE_TIMEOUT = 60             # giây: màn QR tự quay về màn chờ

# --- video lúc chụp (cần ffmpeg) ---
RECORD_VIDEO = True
VIDEO_SIZE = (640, 480)
VIDEO_FPS = 15

# --- ảnh ---
TILE_W, TILE_H = 540, 405     # mỗi tấm tỉ lệ 4:3
JPEG_QUALITY = 90
FOOTER_TEXT = "PHOTOBOOTH"

# --- server tải ảnh ---
PORT = 8000
EXPIRE_HOURS = 24


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


# Điện thoại phải vào được địa chỉ này (cùng Wi-Fi với Pi).
# Sau này có tên miền/Cloudflare Tunnel thì đặt: export PB_PUBLIC_URL=https://booth.xxx.vn
PUBLIC_URL = os.environ.get("PB_PUBLIC_URL") or f"http://{lan_ip()}:{PORT}"
