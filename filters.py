"""Filter vui AR: Tai mèo anime + Má hồng (brush), Tai thỏ, Vương miện 3D, Kính râm 3D, Nơ hồng, Mũ...
Nạp từ file PNG trong suốt tại assets/stickers/
Hỗ trợ:
- Nhiều layer cùng lúc (ví dụ vừa tai mèo vừa má hồng).
- Tự động xoay sticker theo góc nghiêng của đầu (khi nghiêng đầu sang trái hoặc phải).
- Sử dụng mô hình Deep CNN YuNet (OpenCV FaceDetectorYN) với 5 điểm landmark cực nhạy và mượt mà.
"""
import os
import cv2
import numpy as np

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STICKERS_DIR = os.path.join(BASE_DIR, "assets", "stickers")

def _load(filename):
    p = os.path.join(STICKERS_DIR, filename)
    if not os.path.exists(p):
        return None
    img = cv2.imread(p, cv2.IMREAD_UNCHANGED)
    if img is None:
        return None
    if img.ndim == 3 and img.shape[2] == 4:
        return img
    if img.ndim == 3 and img.shape[2] == 3:
        # Tự động tách nền nếu ảnh 3 kênh có nền trắng / ca rô
        corner = img[0, 0]
        if corner[0] > 220 and corner[1] > 220 and corner[2] > 220:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            alpha = np.clip((245 - gray.astype(np.float32)) * 4.0, 0, 255).astype(np.uint8)
            return np.dstack([img, alpha])
    return None

def _crop(img):
    """Cắt bỏ phần viền trong suốt thừa (alpha = 0) quanh hình sticker."""
    if img is None or img.ndim != 3 or img.shape[2] != 4:
        return img
    pts = cv2.findNonZero(img[:, :, 3])
    if pts is None:
        return img
    cx, cy, cw, ch = cv2.boundingRect(pts)
    return img[cy:cy+ch, cx:cx+cw]

# Cấu trúc mỗi filter: list các tuple layer [ (sprite_img, width_ratio, y_offset, anchor), ... ]
# anchor: "top" (đáy sprite khớp toạ độ tựa) | "eyes"/"cheeks"/"center" (căn giữa tâm sprite)
SPRITES = {}

cat_img = _load("cat_ears.png")
brush_img = _load("brush.png")

if cat_img is not None:
    cat_layers = [(cat_img, 1.4, 0.5, "top")]
    if brush_img is not None:
        # Xuất hiện cùng lúc tai mèo + má hồng brush!
        cat_layers.append((brush_img, 0.90, 0.5, "cheeks"))
    SPRITES["cat"] = cat_layers

bunny_img = _load("bunny_ears.png")
if bunny_img is not None:
    SPRITES["bunny"] = [(bunny_img, 1.30, 0.07, "top")]

crown_img = _crop(_load("crown.png"))
if crown_img is not None:
    SPRITES["crown"] = [(crown_img, 1, 0.05, "top")]

glasses_img = _load("glasses.png")
if glasses_img is None:
    glasses_img = _load("sunglasses.png")
if glasses_img is not None:
    SPRITES["glasses"] = [(glasses_img, 1.2, 0.50, "eyes")]

ribbon_img = _load("ribbon.png")
if ribbon_img is not None:
    # Nơ cài bên tóc trái: off_x=0.80 (khi lật gương sẽ nằm bên trái), off_y=0.00, kích thước 0.52 vừa vặn
    SPRITES["ribbon"] = [(ribbon_img, 0.8, -0.02, "center", 0.80)]

heart_img = _crop(_load("heart_glasses.png"))
if heart_img is not None:
    SPRITES["heart"] = [(heart_img, 1.10, 0.42, "eyes")]

party_img = _crop(_load("party.png"))
if party_img is not None:
    SPRITES["party"] = [(party_img, 0.75, 0.08, "top")]

hat_img = _crop(_load("top_hat.png"))
if hat_img is not None:
    SPRITES["hat"] = [(hat_img, 0.95, 0.08, "top")]

angry_img = _crop(_load("angry.png"))
if angry_img is not None:
    SPRITES["angry"] = [(angry_img, 0.40, 0.20, "top")]

if brush_img is not None:
    SPRITES["brush"] = [(brush_img, 0.90, 0.60, "cheeks")]

# --- CÁC FILTER KẾT HỢP (COMBOS SÁNG TẠO) ---
# 1. Công chúa: Vương miện + Đôi má ửng hồng
if crown_img is not None:
    p_layers = [(crown_img, 1, 0.05, "top")]
    if brush_img is not None:
        p_layers.append((brush_img, 0.90, 0.60, "cheeks"))
    SPRITES["princess"] = p_layers

# 2. Mèo ngầu: Tai mèo anime + Kính râm đen cực chất
if cat_img is not None and glasses_img is not None:
    SPRITES["cool_cat"] = [
        (cat_img, 1.4, 0.5, "top"),
        (glasses_img, 1.15, 0.50, "eyes")
    ]

# 3. Thỏ đáng yêu: Tai thỏ + Má hồng
if bunny_img is not None:
    b_layers = [(bunny_img, 1.30, 0.07, "top")]
    if brush_img is not None:
        b_layers.append((brush_img, 0.90, 0.60, "cheeks"))
    SPRITES["bunny_cute"] = b_layers

# 4. Tình yêu / Valentine: Kính trái tim + Má hồng + Nơ hồng tóc
love_layers = []
if heart_img is not None:
    love_layers.append((heart_img, 1.10, 0.42, "eyes"))
if brush_img is not None:
    love_layers.append((brush_img, 0.85, 0.60, "cheeks"))
if ribbon_img is not None:
    love_layers.append((ribbon_img, 0.75, -0.02, "center", 0.80))
if love_layers:
    SPRITES["love"] = love_layers

# 5. Quý ông / Ảo thuật gia: Mũ quý tộc + Kính râm đen
if hat_img is not None and glasses_img is not None:
    SPRITES["gentleman"] = [
        (hat_img, 0.95, 0.08, "top"),
        (glasses_img, 1.15, 0.50, "eyes")
    ]

# 6. Tiệc vui: Mũ sinh nhật + Kính trái tim
if party_img is not None:
    party_layers = [(party_img, 0.75, 0.08, "top")]
    if heart_img is not None:
        party_layers.append((heart_img, 1.10, 0.42, "eyes"))
    elif glasses_img is not None:
        party_layers.append((glasses_img, 1.15, 0.50, "eyes"))
    SPRITES["party_combo"] = party_layers


def crop43(frame: np.ndarray) -> np.ndarray:
    """Cắt chính giữa tỉ lệ 4:3 (giống khung ảnh cuối)."""
    h, w = frame.shape[:2]
    tw = int(h * 4 / 3)
    if w >= tw:
        x0 = (w - tw) // 2
        return frame[:, x0:x0 + tw]
    th = int(w * 3 / 4)
    y0 = (h - th) // 2
    return frame[y0:y0 + th, :]


def _rotate_sprite(sp: np.ndarray, angle_deg: float) -> np.ndarray:
    """Xoay sprite theo góc nghiêng (độ) với nền trong suốt không bị cắt góc."""
    if abs(angle_deg) < 0.5:
        return sp
    h, w = sp.shape[:2]
    # Trong OpenCV: góc dương là quay ngược chiều kim đồng hồ
    # angle_deg > 0 là nghiêng thuận chiều kim đồng hồ, nên truyền -angle_deg
    M = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), -angle_deg, 1.0)
    cos = np.abs(M[0, 0])
    sin = np.abs(M[0, 1])
    new_w = int((h * sin) + (w * cos))
    new_h = int((h * cos) + (w * sin))
    M[0, 2] += (new_w / 2.0) - (w / 2.0)
    M[1, 2] += (new_h / 2.0) - (h / 2.0)
    return cv2.warpAffine(
        sp, M, (new_w, new_h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0, 0)
    )


def _overlay(img, sprite, cx, cy, width, anchor="top", angle=0.0, face_center=None):
    w = int(width)
    if w < 8:
        return
    h = int(w * sprite.shape[0] / sprite.shape[1])
    sp = cv2.resize(sprite, (w, h), interpolation=cv2.INTER_AREA if w < sprite.shape[1] else cv2.INTER_LINEAR)
    
    # Tính tâm (sx, sy) của sticker khi chưa xoay
    if anchor in ("eyes", "center", "cheeks"):
        sx, sy = cx, cy
    else: # "top"
        sx, sy = cx, cy - h / 2.0

    # Nếu đầu nghiêng (angle != 0) và có tâm khuôn mặt, xoay tâm sticker quanh tâm khuôn mặt
    if abs(angle) >= 0.5 and face_center is not None:
        fcx, fcy = face_center
        rad = np.radians(angle)
        dx = sx - fcx
        dy = sy - fcy
        sx_prime = fcx + (dx * np.cos(rad) - dy * np.sin(rad))
        sy_prime = fcy + (dx * np.sin(rad) + dy * np.cos(rad))
    else:
        sx_prime, sy_prime = sx, sy

    # Xoay chính sticker theo góc nghiêng
    sp_rot = _rotate_sprite(sp, angle)
    rw, rh = sp_rot.shape[1], sp_rot.shape[0]
    
    x0 = int(round(sx_prime - rw / 2.0))
    y0 = int(round(sy_prime - rh / 2.0))

    ix0, iy0 = max(x0, 0), max(y0, 0)
    ix1, iy1 = min(x0 + rw, img.shape[1]), min(y0 + rh, img.shape[0])
    if ix0 >= ix1 or iy0 >= iy1:
        return

    sp_crop = sp_rot[iy0 - y0:iy1 - y0, ix0 - x0:ix1 - x0]
    a = sp_crop[..., 3:4].astype(np.float32) / 255.0
    roi = img[iy0:iy1, ix0:ix1].astype(np.float32)
    img[iy0:iy1, ix0:ix1] = (sp_crop[..., :3] * a + roi * (1.0 - a)).astype(np.uint8)


def apply(img: np.ndarray, kind: str, face) -> np.ndarray:
    """Gắn filter lên img (sửa trực tiếp), cho một hoặc nhiều khuôn mặt, có xoay theo góc nghiêng đầu."""
    if kind not in SPRITES or face is None:
        return img
    layers = SPRITES[kind]
    if isinstance(layers, tuple):
        layers = [layers]

    if (len(face) in (4, 5, 7)) and np.isscalar(face[0]):
        faces = (face,)
    else:
        faces = face

    for item in faces:
        if len(item) >= 7:
            x, y, w, h, angle, px, py = item[:7]
            fc = (px, py)
        elif len(item) >= 5:
            x, y, w, h, angle = item[:5]
            fc = (x + w / 2.0, y + h / 2.0)
        else:
            x, y, w, h = item[:4]
            angle = 0.0
            fc = (x + w / 2.0, y + h / 2.0)

        for layer in layers:
            sprite = layer[0]
            wr = layer[1]
            off = layer[2]
            anchor = layer[3] if len(layer) > 3 else "top"
            off_x = layer[4] if len(layer) > 4 else 0.5
            _overlay(img, sprite, x + off_x * w, y + off * h, w * wr, anchor=anchor, angle=angle, face_center=fc)

    return img


class FaceTracker:
    """Nhận diện mặt & mắt (tính góc nghiêng đầu) bằng YuNet Deep CNN và Haar cascade fallback."""

    def __init__(self):
        self.yn = None
        # Kiểm tra mô hình YuNet
        model_paths = [
            os.path.join(BASE_DIR, "assets", "models", "face_detection_yunet.onnx"),
            "/home/ras4/IOT_exam/photobooth_3/assets/models/face_detection_yunet.onnx",
            "/home/ras4/IOT/tuan4/lab1/face_detection_yunet_2023mar.onnx",
        ]
        for p in model_paths:
            if os.path.exists(p) and hasattr(cv2, "FaceDetectorYN"):
                try:
                    self.yn = cv2.FaceDetectorYN.create(
                        model=p, config="", input_size=(320, 240),
                        score_threshold=0.35, nms_threshold=0.3, top_k=5000
                    )
                    break
                except Exception:
                    pass

        # Fallback Haar cascade
        self.cc = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        self.rects = None
        self.angles = None
        self.pivots = None
        self.miss = 0

    def detect_all(self, img):
        """Tìm tất cả khuôn mặt và góc nghiêng, trả danh sách (x, y, w, h, angle, px, py)."""
        scale = 320 / img.shape[1] if img.shape[1] > 320 else 1.0
        small = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale != 1 else img
        
        results = []
        if self.yn is not None:
            sh, sw = small.shape[:2]
            self.yn.setInputSize((sw, sh))
            _, faces = self.yn.detect(small)
            if faces is not None and len(faces) > 0:
                for f in faces:
                    fx, fy, fw, fh = f[:4]
                    rex, rey = f[4], f[5]
                    lex, ley = f[6], f[7]
                    rmx, rmy = f[10], f[11]
                    lmx, lmy = f[12], f[13]

                    # 1. Góc nghiêng theo trục 2 mắt
                    dx = lex - rex
                    dy = ley - rey
                    eye_ang = float(np.degrees(np.arctan2(dy, dx)))

                    # 2. Góc nghiêng theo trục dọc khuôn mặt (tâm mắt xuống tâm khóe miệng)
                    em_x = (lex + rex) / 2.0
                    em_y = (ley + rey) / 2.0
                    mm_x = (lmx + rmx) / 2.0
                    mm_y = (lmy + rmy) / 2.0
                    v_dx = mm_x - em_x
                    v_dy = mm_y - em_y
                    vert_ang = float(np.degrees(np.arctan2(-v_dx, v_dy)))

                    # Kết hợp trục ngang + trục dọc theo tỉ lệ chuẩn xác 1:1 với góc nghiêng đầu
                    raw_ang = eye_ang * 0.40 + vert_ang * 0.60
                    angle = float(np.clip(raw_ang * 1.15, -60.0, 60.0))

                    # Tâm xoay thực của khuôn mặt
                    p_x = float((em_x + mm_x) / 2.0 / scale)
                    p_y = float((em_y + mm_y) / 2.0 / scale)

                    results.append((fx / scale, fy / scale, fw / scale, fh / scale, angle, p_x, p_y))
                return results

        # Fallback nếu không có YuNet
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        faces = self.cc.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=4, minSize=(30, 30))
        for (fx, fy, fw, fh) in faces:
            results.append((fx / scale, fy / scale, fw / scale, fh / scale, 0.0, (fx + fw / 2.0) / scale, (fy + fh / 2.0) / scale))
        return results

    def detect(self, img):
        """Tìm mặt lớn nhất, trả (x, y, w, h, angle, px, py) hoặc None."""
        faces = self.detect_all(img)
        return max(faces, key=lambda f: f[2] * f[3], default=None)

    def update(self, img):
        """Gọi trên khung xem trước: làm mượt vị trí và góc xoay nhạy bén."""
        detected = self.detect_all(img)
        H, W = img.shape[:2]
        if not detected:
            self.miss += 1
            if self.miss > 12:
                self.rects = None
                self.angles = None
                self.pivots = None
            return
        self.miss = 0
        detected = sorted(detected, key=lambda f: f[0])
        n_rects = np.array([[item[0] / W, item[1] / H, item[2] / W, item[3] / H] for item in detected], dtype=np.float32)
        n_angles = np.array([item[4] for item in detected], dtype=np.float32)
        n_pivots = np.array([[item[5] / W, item[6] / H] for item in detected], dtype=np.float32)

        if self.rects is not None and len(self.rects) == len(n_rects):
            # Làm mượt chuyển động mượt mà nhưng nhạy tức thì với góc xoay
            self.rects = self.rects * 0.40 + n_rects * 0.60
            self.angles = self.angles * 0.25 + n_angles * 0.75
            self.pivots = self.pivots * 0.40 + n_pivots * 0.60
        else:
            self.rects = n_rects
            self.angles = n_angles
            self.pivots = n_pivots

    def get(self, W, H):
        if self.rects is None or self.angles is None:
            return None
        if self.pivots is not None:
            return [(x * W, y * H, w * W, h * H, a, px * W, py * H) for (x, y, w, h), a, (px, py) in zip(self.rects, self.angles, self.pivots)]
        return [(x * W, y * H, w * W, h * H, a) for (x, y, w, h), a in zip(self.rects, self.angles)]
