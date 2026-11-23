import hashlib
import io
import json
import logging
import time
import requests

logger = logging.getLogger("api_client")

BASE_URL = "https://tinsmith-cattail-murmuring.ngrok-free.dev"
DEVICE_TOKEN = "dev_secret_token_st7789v_2026"
HEADERS = {
    "Authorization": f"Bearer {DEVICE_TOKEN}",
    "Content-Type": "application/json"
}

def get_headers():
    return {
        "Authorization": f"Bearer {DEVICE_TOKEN}",
        "Content-Type": "application/json"
    }

def heartbeat(state="idle", cpu_temp=45.0, disk_free_mb=10000, camera_ok=True, app_version="1.0.0"):
    try:
        res = requests.post(
            f"{BASE_URL}/api/v1/device/heartbeat",
            json={
                "state": state,
                "cpu_temp_c": cpu_temp,
                "disk_free_mb": disk_free_mb,
                "camera_ok": camera_ok,
                "app_version": app_version
            },
            headers=get_headers(),
            timeout=5
        )
        if res.ok:
            return res.json()
    except Exception as e:
        logger.warning(f"Heartbeat failed: {e}")
    return None

def create_direct_pay(sessions: int = 1):
    """Tạo mã thanh toán trực tiếp tại máy (1 lượt 2k, 2 lượt 4k, 3 lượt 6k)"""
    try:
        res = requests.post(
            f"{BASE_URL}/api/v1/device/direct-pay",
            json={"sessions": sessions},
            headers=get_headers(),
            timeout=5
        )
        if res.ok:
            return res.json()
    except Exception as e:
        logger.error(f"create_direct_pay error: {e}")
    return None

def check_direct_pay(dp_id: str, wait: int = 2):
    """Kiểm tra xem khách đã quét QR chuyển khoản 2.000đ chưa"""
    try:
        res = requests.get(
            f"{BASE_URL}/api/v1/device/direct-pay/{dp_id}?wait={wait}",
            headers=get_headers(),
            timeout=wait + 3
        )
        if res.ok:
            data = res.json()
            return data.get("status") == "paid"
    except Exception as e:
        pass
    return False

def simulate_direct_pay(dp_id: str):
    """Giả lập thanh toán thành công tức thì (Test mode không tốn tiền SePay)"""
    try:
        res = requests.post(
            f"{BASE_URL}/api/v1/device/direct-pay/{dp_id}/simulate",
            headers=get_headers(),
            timeout=5
        )
        return res.ok
    except Exception as e:
        logger.error(f"simulate_direct_pay error: {e}")
        return False

def verify_wallet_code(wallet_code: str):
    """Xác thực mã ví khách 8 ký tự (từ web /guest)"""
    try:
        res = requests.post(
            f"{BASE_URL}/api/v1/device/verify",
            json={"wallet_code": wallet_code.strip().upper()},
            headers=get_headers(),
            timeout=5
        )
        if res.ok:
            data = res.json()
            sessions_avail = data.get("sessions_available", 0)
            return True, sessions_avail, "Hợp lệ"
        elif res.status_code == 404:
            return False, 0, "Mã ví không tồn tại"
        elif res.status_code == 423:
            return False, 0, "Ví đang bị tạm khóa"
        else:
            err = res.json().get("error", {}).get("message", "Lỗi xác thực")
            return False, 0, err
    except Exception as e:
        return False, 0, f"Không kết nối được server: {e}"

def poll_commands(wait: int = 15):
    """Long-poll các lệnh điều khiển từ web (ví dụ chụp tự động)"""
    try:
        res = requests.get(
            f"{BASE_URL}/api/v1/device/commands?wait={wait}",
            headers=get_headers(),
            timeout=wait + 5
        )
        if res.ok:
            return res.json().get("commands", [])
    except Exception:
        pass
    return []

def ack_command(cmd_id: str, result: str = "ok"):
    try:
        requests.post(
            f"{BASE_URL}/api/v1/device/commands/{cmd_id}/ack",
            json={"result": result},
            headers=get_headers(),
            timeout=5
        )
    except Exception:
        pass

def open_session(source: dict, client_session_id: str, layout: str = "strip", filter_name: str = "cat"):
    """Tạo phiên chụp và giữ 2.000đ trên server"""
    try:
        res = requests.post(
            f"{BASE_URL}/api/v1/device/sessions",
            json={
                "client_session_id": client_session_id,
                "source": source,
                "layout": layout,
                "filter": filter_name
            },
            headers=get_headers(),
            timeout=10
        )
        if res.ok:
            return True, res.json()
        else:
            err = res.json().get("error", {})
            return False, err
    except Exception as e:
        return False, {"message": str(e)}

def upload_media_bytes(session_id: str, kind: str, data: bytes, content_type: str = "image/jpeg"):
    """Tải tệp ảnh hoặc video lên server"""
    sha256_hex = hashlib.sha256(data).hexdigest()
    headers = {
        "Authorization": f"Bearer {DEVICE_TOKEN}",
        "Content-Type": content_type,
        "X-Content-SHA256": sha256_hex
    }
    try:
        res = requests.put(
            f"{BASE_URL}/api/v1/device/sessions/{session_id}/media/{kind}",
            data=data,
            headers=headers,
            timeout=30
        )
        return res.ok
    except Exception as e:
        logger.error(f"Upload media {kind} failed: {e}")
        return False

def complete_session(session_id: str, layout: str, filter_name: str, frame_color: str, color_filter: str, shots: int, captured_at: str, duration_s: float, media_list: list):
    """Hoàn tất phiên chụp, trừ tiền và nhận share_url"""
    try:
        res = requests.post(
            f"{BASE_URL}/api/v1/device/sessions/{session_id}/complete",
            json={
                "layout": layout,
                "filter": filter_name,
                "frame_color": frame_color,
                "color_filter": color_filter,
                "shots": shots,
                "captured_at": captured_at,
                "duration_s": duration_s,
                "media": media_list
            },
            headers=get_headers(),
            timeout=15
        )
        if res.ok:
            return True, res.json()
        else:
            return False, res.json().get("error", {})
    except Exception as e:
        return False, {"message": str(e)}
