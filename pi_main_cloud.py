"""Photobooth CLB - Client hoàn chỉnh trên Raspberry Pi (ST7789V 320x240 LCD):
- Kết nối Cloud Server VPS (FastAPI)
- Quét VietQR trực tiếp tại màn hình máy (Direct Pay 2.000đ)
- Nhập mã ví khách 8 ký tự (từ web /guest)
- Nhận lệnh tự động chụp khi khách thanh toán qua Web
- Tải ảnh và video lên Cloud Server, sinh QR tải ảnh toàn cầu (4G/Wi-Fi)
"""
import hashlib
import io
import math
import os
import sys
import threading
import time
import uuid

if sys.platform.startswith("linux"):
    os.environ.setdefault("DISPLAY", ":0")
    os.environ.setdefault("XAUTHORITY", os.path.expanduser("~/.Xauthority"))

import cv2
import pygame
import qrcode
from PIL import Image

import compose
import config
import filters
from camera import Camera
from video import VideoRecorder
import api_client

W, H = config.SCREEN_W, config.SCREEN_H
BG, FG, MUTED = (24, 24, 30), (245, 245, 245), (170, 170, 180)
ACCENT, OK, DANGER, INFO = (233, 30, 99), (46, 160, 90), (200, 60, 60), (2, 132, 199)
FILTERS = [
    ("none", "Không"),
    ("cat", "Tai mèo"),
    ("bunny", "Tai thỏ"),
    ("crown", "Vương\nmiện"),
    ("glasses", "Kính râm"),
    ("ribbon", "Nơ hồng"),
    ("heart", "Kính tim"),
    ("brush", "Má hồng"),
    ("party", "Mũ tiệc"),
    ("hat", "Mũ nỉ"),
    ("princess", "Công\nchúa"),
    ("cool_cat", "Mèo\nngầu"),
    ("bunny_cute", "Thỏ\ncute"),
    ("love", "Tình\nyêu"),
    ("gentleman", "Quý\nông"),
]
FRAMES = [("black", "Đen"), ("white", "Trắng"), ("red", "Đỏ"), ("blue", "Xanh")]
COLORFX = [("orig", "Gốc"), ("bw", "Đen trắng"), ("pink", "Trắng hồng"), ("vintage", "Cổ điển")]

_fonts = {}

def font(size, bold=True):
    k = (size, bold)
    if k not in _fonts:
        path = pygame.font.match_font("dejavusans,notosans,liberationsans,freesans", bold=bold)
        _fonts[k] = pygame.font.Font(path, size)
    return _fonts[k]

def text(surf, s, size, color=FG, center=None, topleft=None, bold=True):
    img = font(size, bold).render(s, True, color)
    r = img.get_rect()
    if center:
        r.center = center
    elif topleft:
        r.topleft = topleft
    surf.blit(img, r)
    return r

def to_surface(bgr):
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    return pygame.image.frombuffer(rgb.tobytes(), (rgb.shape[1], rgb.shape[0]), "RGB")

def make_qr_surf(data: str, size: int = 150):
    qr = qrcode.QRCode(border=1, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    img = img.resize((size, size), Image.Resampling.NEAREST)
    return pygame.image.frombuffer(img.tobytes(), img.size, "RGB")

class Button:
    def __init__(self, rect, label, cb, size=20, bg=(50, 50, 60), fg=FG):
        self.rect, self.label, self.cb, self.size, self.bg, self.fg = pygame.Rect(rect), label, cb, size, bg, fg

    def draw(self, surf, active=False):
        pygame.draw.rect(surf, ACCENT if active else self.bg, self.rect, border_radius=8)
        pygame.draw.rect(surf, (230, 230, 230), self.rect, 1, border_radius=8)
        lines = self.label.split("\n")
        if len(lines) == 1:
            text(surf, self.label, self.size, self.fg, center=self.rect.center)
        else:
            line_height = self.size + 4
            total_h = len(lines) * line_height
            start_y = self.rect.centery - total_h // 2 + line_height // 2
            for i, line in enumerate(lines):
                text(surf, line, self.size, self.fg, center=(self.rect.centerx, start_y + i * line_height))

class App:
    def __init__(self):
        pygame.init()
        flags = pygame.SCALED | (0 if config.WINDOWED else pygame.FULLSCREEN)
        self.screen = pygame.display.set_mode((W, H), flags)
        pygame.display.set_caption("Photobooth CLB")
        if not config.WINDOWED:
            pygame.mouse.set_visible(False)
        self.clock = pygame.time.Clock()
        self.cam = Camera()
        self.tracker = filters.FaceTracker()
        self.layout, self.filt = "strip", "cat"
        self.frame, self.fx = "white", "orig"
        self.frame_n = 0
        self.filter_page = 0
        self.prev = None
        self.exit_taps = []
        self.rec = None
        self.running = True

        # Payment & Multi-Session State
        self.source = None # {"type": "direct_pay", "id": ...} or {"type": "wallet_code", "code": ...}
        self.planned_sessions = 1
        self.sessions_left = 1
        self.current_session_num = 1
        self.direct_pay_amount = 2000
        self.done_min_seconds = 120 # Minimum 2 minutes for QR photo download
        self.direct_pay_id = None
        self.direct_pay_qr = None
        self.direct_pay_content = ""
        self.direct_pay_account = "962472HDYO"
        self.direct_pay_bank = "BIDV"
        self.direct_pay_status = ""
        self.input_code = ""
        self.code_error = ""
        self.wallet_code = ""
        self.wallet_sessions_avail = 0
        self.cloud_share_url = None
        self.stop_bg_threads = False
        self.state = "idle"

        self.go("idle")

        # Start Background Daemon (Heartbeat & Remote Command Polling)
        threading.Thread(target=self._bg_daemon, daemon=True).start()

    # ---------- Background Polling ----------
    def _bg_daemon(self):
        last_hb = 0
        while self.running and not self.stop_bg_threads:
            now = time.time()
            if now - last_hb > 30:
                api_client.heartbeat(state=self.state)
                last_hb = now

            # If waiting at idle, check if someone paid on web (/guest?booth=dev_01)
            if self.state == "idle":
                try:
                    cmds = api_client.poll_commands(wait=5)
                    for cmd in cmds:
                        cmd_type = cmd.get("type")
                        params = cmd.get("params", {})
                        if cmd_type in ("start_guest_session", "start_session"):
                            wallet_code = params.get("wallet_code")
                            if wallet_code:
                                self.source = {"type": "wallet_code", "code": wallet_code}
                                self.planned_sessions = 1
                                self.sessions_left = 1
                                self.current_session_num = 1
                                api_client.ack_command(cmd["id"], "ok")
                                self.go("layout")
                                break
                except Exception:
                    pass
            time.sleep(2)

    # ---------- Chuyển Màn Hình ----------
    def go(self, state):
        self.state = state
        self.state_t = time.monotonic()
        b = []

        if state == "idle":
            self.source = None
            self.planned_sessions = 1
            self.sessions_left = 1
            self.current_session_num = 1
            self.wallet_code = ""
            self.wallet_sessions_avail = 0
            # 3 Options for Sessions: 1 lượt (2k), 2 lượt (4k), 3 lượt (6k) + TEST bypass
            b = [
                Button((10, 85, 94, 65), "1 LƯỢT\n2.000đ", lambda: self.choose_direct_pay(1), 14, bg=OK),
                Button((113, 85, 94, 65), "2 LƯỢT\n4.000đ", lambda: self.choose_direct_pay(2), 14, bg=OK),
                Button((216, 85, 94, 65), "3 LƯỢT\n6.000đ", lambda: self.choose_direct_pay(3), 14, bg=OK),
                Button((10, 168, 300, 52), "NHẬP MÃ VÍ (TỪ APP / WEB)", lambda: self.go("enter_code"), 15, bg=INFO),
                Button((246, 8, 68, 28), "TEST ⚡", self.quick_test, 12, bg=(234, 88, 12))
            ]

        elif state == "direct_pay":
            self.direct_pay_qr = None
            self.direct_pay_status = "Đang tạo mã VietQR..."
            b = [
                Button((4, 4, 66, 30), "◀ HỦY", lambda: self.go("idle"), 13),
                Button((156, 182, 154, 45), "TEST: VÀO CHỤP ⚡", self.test_bypass_pay, 13, bg=(234, 88, 12))
            ]
            threading.Thread(target=self._start_direct_pay, daemon=True).start()

        elif state == "enter_code":
            self.input_code = ""
            self.code_error = ""
            b = self._build_keyboard_buttons()

        elif state == "wallet_plan":
            max_c = min(3, self.wallet_sessions_avail)
            if max_c == 1:
                b = [
                    Button((W // 2 - 60, 100, 120, 60), "1 LƯỢT", lambda: self.choose_wallet_sessions(1), 16, bg=OK),
                ]
            elif max_c == 2:
                b = [
                    Button((35, 100, 115, 60), "1 LƯỢT", lambda: self.choose_wallet_sessions(1), 16, bg=OK),
                    Button((170, 100, 115, 60), "2 LƯỢT", lambda: self.choose_wallet_sessions(2), 16, bg=OK),
                ]
            else:
                b = [
                    Button((10, 100, 94, 60), "1 LƯỢT", lambda: self.choose_wallet_sessions(1), 16, bg=OK),
                    Button((113, 100, 94, 60), "2 LƯỢT", lambda: self.choose_wallet_sessions(2), 16, bg=OK),
                    Button((216, 100, 94, 60), "3 LƯỢT", lambda: self.choose_wallet_sessions(3), 16, bg=OK),
                ]
            b.append(Button((10, 185, 80, 42), "◀ HỦY", lambda: self.go("enter_code"), 13, bg=(70, 70, 80)))

        elif state == "layout":
            b = [
                Button((15, 60, 140, 150), "", lambda: self.pick_layout("strip")),
                Button((165, 60, 140, 150), "", lambda: self.pick_layout("grid")),
                Button((4, 4, 60, 32), "◀ HỦY", lambda: self.go("idle"), 14)
            ]

        elif state == "filter":
            total_pages = (len(FILTERS) + 2) // 3
            if self.filter_page >= total_pages:
                self.filter_page = 0
            b = [
                Button((0, 0, 40, 180), "◀", lambda: self.go("layout"), 22),
                Button((280, 0, 40, 180), "▶", lambda: self.go("ready"), 22, bg=OK),
                Button((7, 184, 36, 52), "◀", lambda: self.change_filter_page(-1), 20, bg=(51, 65, 85))
            ]
            start_idx = self.filter_page * 3
            page_filters = FILTERS[start_idx : start_idx + 3]
            for i, (k, label) in enumerate(page_filters):
                b.append(Button((47 + i * 76, 184, 72, 52), label, lambda k=k: self.pick_filter(k), 13))
            b.append(Button((275, 184, 36, 52), "▶", lambda: self.change_filter_page(1), 20, bg=(51, 65, 85)))

        elif state == "ready":
            b = [
                Button((0, 0, 40, 180), "◀", lambda: self.go("filter"), 22),
                Button((6, 184, 308, 52), "CHỤP NGAY", self.start_capture, 26, bg=ACCENT)
            ]

        elif state == "style":
            for i, (k, label) in enumerate(FRAMES):
                b.append(Button((174 + (i % 2) * 74, 18 + (i // 2) * 38, 70, 34), label, lambda k=k: self.pick_style("frame", k), 12))
            for i, (k, label) in enumerate(COLORFX):
                b.append(Button((174 + (i % 2) * 74, 106 + (i // 2) * 38, 70, 34), label, lambda k=k: self.pick_style("fx", k), 11))
            b.append(Button((174, 188, 144, 48), "XONG", self.start_processing, 22, bg=OK))

        elif state == "done":
            if self.sessions_left > 1:
                lbl = f"CHỤP TIẾP\n(Còn {self.sessions_left - 1} lượt)"
                btn_cb = self.next_session
                btn_bg = OK
            else:
                lbl = "XONG"
                btn_cb = self.finish_done
                btn_bg = INFO
            b = [Button((170, 166, 142, 64), lbl, btn_cb, 14, bg=btn_bg)]

        self.buttons = b

    def choose_direct_pay(self, count: int):
        self.planned_sessions = count
        self.sessions_left = count
        self.current_session_num = 1
        self.direct_pay_amount = count * 2000
        self.go("direct_pay")

    def choose_wallet_sessions(self, count: int):
        self.source = {"type": "wallet_code", "code": self.wallet_code}
        self.planned_sessions = count
        self.sessions_left = count
        self.current_session_num = 1
        self.go("layout")

    def next_session(self):
        self.sessions_left -= 1
        self.current_session_num += 1
        self.go("layout")

    def finish_done(self):
        self.sessions_left = 0
        self.source = None
        self.go("idle")

    def quick_test(self):
        """Bấm nút TEST ở màn hình chờ: Vào thẳng app chụp ngay không cần quét QR"""
        dp = api_client.create_direct_pay(sessions=1)
        if dp:
            self.direct_pay_id = dp.get("id")
            self.planned_sessions = 1
            self.sessions_left = 1
            self.current_session_num = 1
            self.direct_pay_amount = 2000
            api_client.simulate_direct_pay(self.direct_pay_id)
            self.source = {"type": "direct_pay", "id": self.direct_pay_id}
            self.go("layout")

    def test_bypass_pay(self):
        """Bấm nút TEST ở màn hình QR: Bỏ qua thanh toán và vào chụp gói đã chọn ngay"""
        if self.direct_pay_id:
            api_client.simulate_direct_pay(self.direct_pay_id)
            self.source = {"type": "direct_pay", "id": self.direct_pay_id}
            self.direct_pay_status = f"Đã duyệt TEST {self.direct_pay_amount:,}đ! Bắt đầu..."
            time.sleep(0.5)
            self.go("layout")

    # ---------- Direct Pay Logic ----------
    def _start_direct_pay(self):
        dp = api_client.create_direct_pay(sessions=self.planned_sessions)
        if not dp:
            self.direct_pay_status = "Lỗi kết nối server!"
            return

        self.direct_pay_id = dp.get("id")
        self.direct_pay_amount = dp.get("amount_vnd", self.planned_sessions * 2000)
        self.direct_pay_content = dp.get("transfer_content", "")
        self.direct_pay_account = dp.get("account_number", "962472HDYO")
        self.direct_pay_bank = dp.get("bank_name", "BIDV")
        payload = dp.get("qr_payload", "")
        self.direct_pay_qr = make_qr_surf(payload, size=140)
        self.direct_pay_status = "Chờ chuyển khoản..."

        # Poll status every 1.5 seconds
        while self.state == "direct_pay":
            if api_client.check_direct_pay(self.direct_pay_id, wait=2):
                self.source = {"type": "direct_pay", "id": self.direct_pay_id}
                self.direct_pay_status = f"Đã nhận {self.direct_pay_amount:,}đ! Bắt đầu..."
                time.sleep(1.0)
                self.go("layout")
                break
            time.sleep(1.0)

    # ---------- Enter Wallet Code Logic ----------
    def _build_keyboard_buttons(self):
        btns = []
        chars = [
            list("23456789"),
            list("ABCDEFGH"),
            list("JKMNPQRS"),
            list("TUVWXYZ⌫")
        ]
        start_y = 66
        btn_w, btn_h = 36, 28
        spacing_x = 39
        spacing_y = 31

        for r_idx, row in enumerate(chars):
            y = start_y + r_idx * spacing_y
            for c_idx, ch in enumerate(row):
                x = 6 + c_idx * spacing_x
                if ch == "⌫":
                    btns.append(Button((x, y, btn_w, btn_h), "⌫", self._kb_backspace, 15, bg=DANGER))
                else:
                    btns.append(Button((x, y, btn_w, btn_h), ch, lambda c=ch: self._kb_type(c), 15))

        # Bottom row
        btns.append(Button((6, 196, 75, 38), "◀ HỦY", lambda: self.go("idle"), 13))
        btns.append(Button((200, 196, 114, 38), "XÁC NHẬN", self._verify_code, 15, bg=OK))
        return btns

    def _kb_type(self, ch):
        if len(self.input_code) < 8:
            self.input_code += ch
            self.code_error = ""

    def _kb_backspace(self):
        if self.input_code:
            self.input_code = self.input_code[:-1]
            self.code_error = ""

    def _verify_code(self):
        if len(self.input_code) < 8:
            self.code_error = "Mã ví gồm 8 ký tự!"
            return

        self.code_error = "Đang kiểm tra..."
        ok, sessions, msg = api_client.verify_wallet_code(self.input_code)
        if ok and sessions > 0:
            self.wallet_code = self.input_code.strip().upper()
            self.wallet_sessions_avail = sessions
            self.go("wallet_plan")
        else:
            self.code_error = msg if not ok else "Ví đã hết lượt chụp!"

    # ---------- Chọn Tùy Chọn ----------
    def pick_layout(self, k):
        self.layout = k
        self.go("filter")

    def pick_filter(self, k):
        self.filt = k

    def change_filter_page(self, delta):
        total_pages = (len(FILTERS) + 2) // 3
        self.filter_page = (self.filter_page + delta) % total_pages
        self.go("filter")

    def toggle_filter_page(self):
        self.change_filter_page(1)

    def pick_style(self, what, k):
        if what == "frame":
            self.frame = k
        else:
            self.fx = k
        self.refresh_style()

    def enter_style(self):
        self.frame, self.fx = "white", "orig"
        self.go("style")
        self.refresh_style()

    def refresh_style(self):
        cw, ch = compose.canvas_size(self.layout)
        img = compose.make(self.shots, self.layout, self.frame, self.fx, scale=min(166 / cw, 232 / ch))
        self.style_surf = pygame.image.frombuffer(img.tobytes(), img.size, "RGB")

    # ---------- Vòng Chụp ----------
    def start_capture(self):
        self.shots = []
        os.makedirs(config.DATA_DIR.parent / "tmp", exist_ok=True)
        self.rec_path = config.DATA_DIR.parent / "tmp" / f"{int(time.time())}.mp4"
        self.rec = VideoRecorder(self.rec_path) if config.RECORD_VIDEO else None
        self.begin_countdown(config.FIRST_COUNTDOWN)
        self.go("countdown")

    def begin_countdown(self, secs):
        self.cd_end = time.monotonic() + secs
        self.flash_until = 0

    def cancel(self):
        if self.rec:
            self.rec.abort()
            self.rec = None
        self.go("ready")

    def take_shot(self):
        raw = self.cam.read()
        if raw is None:
            return
        crop = filters.crop43(raw).copy()
        faces = self.tracker.detect_all(crop) or self.tracker.get(crop.shape[1], crop.shape[0])
        if self.filt != "none":
            filters.apply(crop, self.filt, faces)
        if config.MIRROR_SAVED:
            crop = cv2.flip(crop, 1)
        self.shots.append(crop)
        self.flash_until = time.monotonic() + config.FLASH_SECONDS
        if len(self.shots) >= config.SHOTS:
            if self.rec:
                self.rec.stop()
            self.enter_style()
        else:
            self.cd_end = time.monotonic() + config.NEXT_COUNTDOWN

    def start_processing(self):
        self.go("processing")
        self.result = None
        threading.Thread(target=self._process, daemon=True).start()

    # ---------- Ghép ảnh & Upload Cloud ----------
    def _process(self):
        try:
            strip = compose.make(self.shots, self.layout, self.frame, self.fx)
            processed_shots = [compose.color_fx(t, self.fx) for t in self.shots]
            
            # Encode strip to JPEG bytes
            strip_buf = io.BytesIO()
            strip.save(strip_buf, format="JPEG", quality=config.JPEG_QUALITY)
            strip_bytes = strip_buf.getvalue()

            # Encode individual shots
            shots_bytes_list = []
            for t in processed_shots:
                _, buf = cv2.imencode(".jpg", t, [cv2.IMWRITE_JPEG_QUALITY, config.JPEG_QUALITY])
                shots_bytes_list.append(buf.tobytes())

            # Video bytes
            vpath = self.rec_path if (self.rec and self.rec.wait(25) and os.path.exists(self.rec_path)) else None
            v_bytes = None
            if vpath and os.path.exists(vpath):
                with open(vpath, "rb") as f:
                    v_bytes = f.read()

            # 1. Open Session on Cloud Server (Holds 2.000đ)
            client_sid = str(uuid.uuid4())
            ok, sess_info = api_client.open_session(
                source=self.source or {"type": "direct_pay", "id": "unknown"},
                client_session_id=client_sid,
                layout=self.layout,
                filter_name=self.filt
            )
            if not ok:
                raise RuntimeError(sess_info.get("message", "Không thể mở phiên trên máy chủ"))

            session_id = sess_info["session_id"]

            # 2. Upload media
            api_client.upload_media_bytes(session_id, "strip", strip_bytes, "image/jpeg")
            for idx, s_data in enumerate(shots_bytes_list, 1):
                api_client.upload_media_bytes(session_id, f"shot_{idx}", s_data, "image/jpeg")
            if v_bytes:
                api_client.upload_media_bytes(session_id, "video", v_bytes, "video/mp4")

            # 3. Complete Session
            now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            media_meta = [{"kind": "strip", "bytes": len(strip_bytes), "sha256": hashlib.sha256(strip_bytes).hexdigest()}]
            for idx, s_data in enumerate(shots_bytes_list, 1):
                media_meta.append({"kind": f"shot_{idx}", "bytes": len(s_data), "sha256": hashlib.sha256(s_data).hexdigest()})
            if v_bytes:
                media_meta.append({"kind": "video", "bytes": len(v_bytes), "sha256": hashlib.sha256(v_bytes).hexdigest()})

            ok, comp_info = api_client.complete_session(
                session_id=session_id,
                layout=self.layout,
                filter_name=self.filt,
                frame_color=self.frame,
                color_filter=self.fx,
                shots=config.SHOTS,
                captured_at=now_iso,
                duration_s=16.0,
                media_list=media_meta
            )
            if not ok:
                raise RuntimeError(comp_info.get("message", "Lỗi hoàn tất phiên chụp"))

            share_url = comp_info.get("share_url", "")
            if "localhost:8000" in share_url or "127.0.0.1:8000" in share_url:
                share_url = share_url.replace("http://localhost:8000", api_client.BASE_URL).replace("http://127.0.0.1:8000", api_client.BASE_URL)
            if not share_url:
                share_url = f"{api_client.BASE_URL}/guest"
            self.rec = None
            self.result = (strip, share_url)
        except Exception as e:
            print("LỖI xử lý & tải lên VPS:", e, flush=True)
            self.result = ("error", str(e))

    def build_done(self):
        strip, url = self.result
        print(f"LINK TẢI ẢNH CLOUD: {url}", flush=True)
        self.qr_surf = make_qr_surf(url, size=150)
        t = strip.copy()
        t.thumbnail((100, 140))
        self.thumb = pygame.image.frombuffer(t.tobytes(), t.size, "RGB")
        self.go("done")

    # ---------- Xử Lý Sự Kiện ----------
    def on_click(self, pos):
        if self.state == "countdown":
            self.cancel()
            return
        if self.state == "idle" and pos[0] < 50 and pos[1] < 50:
            now = time.monotonic()
            self.exit_taps = [t for t in self.exit_taps if now - t < 3] + [now]
            if len(self.exit_taps) >= 5:
                self.running = False
        for b in self.buttons:
            if b.rect.collidepoint(pos):
                b.cb()
                return

    # ---------- Vẽ Màn Hình (320x240) ----------
    def update_preview(self):
        if self.state not in ("idle", "filter", "ready", "countdown"):
            return
        use_filter = self.filt != "none" and self.state != "idle"
        raw = self.cam.read()
        if raw is None:
            return
        small = cv2.resize(filters.crop43(raw), (W, H), interpolation=cv2.INTER_AREA)
        self.frame_n += 1
        if use_filter and self.frame_n % 2 == 0:
            self.tracker.update(small)
        if use_filter:
            filters.apply(small, self.filt, self.tracker.get(W, H))
        self.prev = cv2.flip(small, 1) if config.MIRROR_PREVIEW else small

    def draw_preview(self, size=(W, H), pos=(0, 0)):
        if self.prev is None:
            return
        img = self.prev if size == (W, H) else cv2.resize(self.prev, size, interpolation=cv2.INTER_AREA)
        self.screen.blit(to_surface(img), pos)

    def draw(self):
        s = self.screen
        s.fill(BG)
        st = self.state

        if st == "idle":
            self.draw_preview()
            dim = pygame.Surface((W, H), pygame.SRCALPHA)
            dim.fill((0, 0, 0, 110))
            s.blit(dim, (0, 0))
            self.outlined(s, "PHOTOBOOTH CLB", 26, (W // 2, 24))
            text(s, "Chọn số lượt chụp (2.000đ / lượt)", 12, (254, 240, 138), center=(W // 2, 52))
            for b in self.buttons:
                b.draw(s, True)

        elif st == "direct_pay":
            # Direct Pay Screen (QR on left, info on right)
            if self.direct_pay_qr:
                s.blit(self.direct_pay_qr, (8, 48))
            else:
                pygame.draw.rect(s, (40, 40, 50), (8, 48, 140, 140), border_radius=6)
                text(s, "Đang tải...", 14, MUTED, center=(78, 118))

            text(s, "QUÉT QR THANH TOÁN", 14, FG, center=(W // 2 + 35, 18))
            # Right side info
            rx = 156
            text(s, f"Gói: {self.planned_sessions} lượt ({self.direct_pay_amount:,}đ)", 13, (254, 240, 138), topleft=(rx, 42), bold=True)
            text(s, f"Ngân hàng: {self.direct_pay_bank or 'BIDV'}", 11, MUTED, topleft=(rx, 66))
            text(s, f"STK: {self.direct_pay_account or '962472HDYO'}", 12, (254, 240, 138), topleft=(rx, 84), bold=True)
            text(s, f"Số tiền: {self.direct_pay_amount:,}đ", 13, OK, topleft=(rx, 106), bold=True)
            text(s, f"Nội dung: {self.direct_pay_content}", 12, ACCENT, topleft=(rx, 128), bold=True)
            text(s, self.direct_pay_status, 11, (200, 200, 200), topleft=(rx, 152), bold=False)

            for b in self.buttons:
                b.draw(s)

        elif st == "enter_code":
            # Enter 8-char wallet code screen
            text(s, "NHẬP MÃ VÍ (TỪ WEB /GUEST)", 15, FG, center=(W // 2, 16))
            # Code input box
            disp_code = self.input_code.ljust(8, "_")
            code_surf = font(22, bold=True).render(" ".join(disp_code), True, (56, 189, 248))
            box_r = pygame.Rect(W // 2 - 100, 32, 200, 30)
            pygame.draw.rect(s, (30, 41, 59), box_r, border_radius=6)
            pygame.draw.rect(s, ACCENT, box_r, 1, border_radius=6)
            s.blit(code_surf, code_surf.get_rect(center=box_r.center))

            if self.code_error:
                text(s, self.code_error, 11, DANGER, center=(W // 2, 188))

            for b in self.buttons:
                b.draw(s)

        elif st == "wallet_plan":
            text(s, "XÁC THỰC VÍ THÀNH CÔNG", 15, FG, center=(W // 2, 18), bold=True)
            text(s, f"Mã ví: {self.wallet_code}", 13, (56, 189, 248), center=(W // 2, 40), bold=True)
            text(s, f"Trong ví còn: {self.wallet_sessions_avail} lượt chụp", 13, OK, center=(W // 2, 60), bold=True)
            text(s, "Chọn số lượt bạn muốn chụp lần này:", 12, (254, 240, 138), center=(W // 2, 80))
            for b in self.buttons:
                b.draw(s)

        elif st == "layout":
            text(s, "Chọn bố cục", 20, center=(W // 2 + 20, 22))
            if self.planned_sessions > 1:
                text(s, f"Lượt {self.current_session_num}/{self.planned_sessions}", 12, (254, 240, 138), topleft=(8, 8), bold=True)
            for b, kind in zip(self.buttons[:2], ("strip", "grid")):
                b.draw(s, self.layout == kind)
                self.draw_layout_icon(kind, b.rect)
            self.buttons[2].draw(s)

        elif st in ("filter", "ready"):
            self.draw_preview((240, 180), (40, 0))
            if self.planned_sessions > 1:
                text(s, f"Lượt {self.current_session_num}/{self.planned_sessions}", 12, (254, 240, 138), topleft=(8, 8), bold=True)
            if st == "filter":
                total_pages = (len(FILTERS) + 2) // 3
                text(s, f"Chọn filter ({self.filter_page + 1}/{total_pages})", 13, FG, center=(W // 2, 12))
            for b in self.buttons:
                b.draw(s, st == "filter" and b.label == dict(FILTERS).get(self.filt, ""))

        elif st == "countdown":
            self.draw_countdown()

        elif st == "style":
            s.blit(self.style_surf, self.style_surf.get_rect(center=(85, 120)))
            text(s, "Khung", 12, MUTED, topleft=(174, 2))
            text(s, "Bộ lọc màu", 12, MUTED, topleft=(174, 90))
            for b, (k, _) in zip(self.buttons[:4], FRAMES):
                rgb = compose.FRAME_COLORS[k]
                pygame.draw.rect(s, rgb, b.rect, border_radius=8)
                pygame.draw.rect(s, ACCENT if self.frame == k else (120, 120, 120), b.rect, 4 if self.frame == k else 1, border_radius=8)
                text(s, b.label, 12, (255, 255, 255) if sum(rgb) < 450 else (30, 30, 30), center=b.rect.center)
            for b, (k, _) in zip(self.buttons[4:8], COLORFX):
                b.draw(s, self.fx == k)
            self.buttons[8].draw(s)

        elif st == "processing":
            dots = "." * (int(time.monotonic() * 2) % 4)
            text(s, "Đang tải ảnh lên Cloud" + dots, 20, center=(W // 2, H // 2))
            if self.result is not None:
                if self.result[0] == "error":
                    self.go("ready")
                else:
                    self.build_done()

        elif st == "done":
            elapsed = time.monotonic() - self.state_t
            remaining_s = max(0, int(self.done_min_seconds - elapsed))

            # Left: QR code to download photos
            if self.qr_surf:
                s.blit(self.qr_surf, (8, 10))
            text(s, "QUÉT QR ĐỂ TẢI ẢNH", 12, FG, center=(83, 170))
            text(s, f"Tự động chuyển sau: {remaining_s}s", 11, (254, 240, 138), center=(83, 190), bold=True)
            text(s, "(4G hoặc Wi-Fi đều được)", 10, (134, 239, 172), center=(83, 210), bold=False)

            # Right: Strip thumbnail
            if self.thumb:
                s.blit(self.thumb, self.thumb.get_rect(midtop=(241, 10)))

            # Right bottom: Action button ("XONG" / "CHỤP TIẾP")
            for b in self.buttons:
                b.draw(s)

            # Auto transition after 120s (2 minutes minimum display)
            if elapsed >= self.done_min_seconds:
                if self.sessions_left > 1:
                    self.next_session()
                else:
                    self.finish_done()

        pygame.display.flip()

    def outlined(self, surf, s, size, center):
        for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2), (-2, -2), (2, 2), (-2, 2), (2, -2)):
            text(surf, s, size, (0, 0, 0), center=(center[0] + dx, center[1] + dy))
        text(surf, s, size, FG, center=center)

    def feed_video(self):
        if not (self.rec and self.rec.ok and self.state == "countdown") or self.frame_n % 2:
            return
        raw = self.cam.read()
        if raw is None:
            return
        vw, vh = config.VIDEO_SIZE
        f = cv2.resize(filters.crop43(raw), (vw, vh), interpolation=cv2.INTER_AREA)
        if self.filt != "none":
            filters.apply(f, self.filt, self.tracker.get(vw, vh))
        self.rec.latest = cv2.flip(f, 1) if config.MIRROR_SAVED else f

    def draw_layout_icon(self, kind, r):
        c = (230, 230, 230)
        cx, top = r.centerx, r.y + 14
        if kind == "strip":
            for i in range(4):
                pygame.draw.rect(self.screen, c, (cx - 18, top + i * 24, 36, 20), border_radius=2)
            text(self.screen, "Dọc 4 tấm", 13, center=(cx, r.bottom - 14))
        else:
            for i in range(4):
                pygame.draw.rect(self.screen, c, (cx - 40 + (i % 2) * 42, top + 4 + (i // 2) * 48, 38, 42), border_radius=2)
            text(self.screen, "Lưới 2x2", 13, center=(cx, r.bottom - 14))

    def draw_countdown(self):
        s = self.screen
        now = time.monotonic()
        self.draw_preview()
        if now < self.flash_until:
            s.fill((255, 255, 255))
            return
        remain = max(0, math.ceil(self.cd_end - now))
        text(s, str(remain), 110, (0, 0, 0), center=(W // 2 + 3, H // 2 + 3))
        text(s, str(remain), 110, (255, 255, 255), center=(W // 2, H // 2))
        text(s, f"{len(self.shots) + 1}/{config.SHOTS}", 20, (255, 255, 255), topleft=(8, 6))
        # text(s, "Chạm màn hình để hủy", 12, (255, 255, 255), center=(W // 2, H - 12), bold=False)
        if now >= self.cd_end:
            self.take_shot()

    def run(self):
        try:
            while self.running:
                for e in pygame.event.get():
                    if e.type == pygame.QUIT or (e.type == pygame.KEYDOWN and e.key in (pygame.K_ESCAPE, pygame.K_q)):
                        self.running = False
                    elif e.type == pygame.MOUSEBUTTONDOWN:
                        self.on_click(e.pos)
                self.update_preview()
                self.feed_video()
                self.draw()
                self.clock.tick(30)
        finally:
            self.stop_bg_threads = True
            if self.rec:
                self.rec.abort()
            self.cam.stop()
            pygame.quit()

if __name__ == "__main__":
    print("Photobooth CLB Cloud Client starting...", flush=True)
    App().run()
