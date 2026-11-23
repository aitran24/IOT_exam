"""Ghép 4 tấm thành một bức + màu khung + bộ lọc màu.
Thêm bố cục mới = thêm nhánh trong geometry(). Thêm khung/bộ lọc = thêm vào FRAME_COLORS / color_fx()."""
import datetime

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

import config

TW, TH = config.TILE_W, config.TILE_H
M, G, FOOT = 30, 30, 90

FRAME_COLORS = {"black": (20, 20, 20), "white": (255, 255, 255),
                "red": (200, 40, 50), "blue": (40, 90, 200)}      # RGB

_SEPIA = np.array([[0.131, 0.534, 0.272],
                   [0.168, 0.686, 0.349],
                   [0.189, 0.769, 0.393]], np.float32)            # hàng = B,G,R ra; cột = B,G,R vào
_PINK_BGR = np.array([193, 182, 255], np.float32)
_BW_LUT = np.clip((np.arange(256) - 128) * 1.1 + 128, 0, 255).astype(np.uint8)


def color_fx(img, name):
    """Bộ lọc màu trên ảnh BGR: orig (gốc) | bw (đen trắng) | pink (trắng hồng) | vintage (cổ điển)."""
    if name == "bw":
        g = cv2.LUT(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), _BW_LUT)
        return cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)
    if name == "pink":
        f = img.astype(np.float32)
        f = f * 0.8 + f.mean(axis=2, keepdims=True) * 0.2        # bớt bão hòa nhẹ
        f = f * 0.78 + _PINK_BGR * 0.22                           # ánh hồng
        f = f * 0.92 + 22                                         # sáng, nhẹ nhàng
        return np.clip(f, 0, 255).astype(np.uint8)
    if name == "vintage":
        f = img.astype(np.float32)
        sep = cv2.transform(f, _SEPIA)
        f = sep * 0.85 + f * 0.15
        f = f * 0.88 + 18                                         # phai màu kiểu phim cũ
        return np.clip(f, 0, 255).astype(np.uint8)
    return img


def geometry(layout, s=1.0):
    """Trả về (kích thước canvas, kích thước 1 tấm, toạ độ các tấm, chiều cao chân ảnh) ở tỉ lệ s."""
    tw, th, m, g, foot = (max(1, round(v * s)) for v in (TW, TH, M, G, FOOT))
    if layout == "strip":
        gap, extra = max(1, round(20 * s)), round(30 * s)
        size = (tw + 2 * m, m + 4 * th + 3 * gap + extra + foot)
        pos = [(m, m + i * (th + gap)) for i in range(4)]
    else:
        size = (2 * tw + 3 * g, 2 * th + 3 * g + foot)
        pos = [(g, g), (2 * g + tw, g), (g, 2 * g + th), (2 * g + tw, 2 * g + th)]
    return size, (tw, th), pos, foot


def canvas_size(layout):
    return geometry(layout)[0]


def _font(size):
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"):
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


def make(tiles_bgr, layout="strip", frame="white", fx="orig", scale=1.0) -> Image.Image:
    (cw, ch), (tw, th), pos, foot = geometry(layout, scale)
    bg = FRAME_COLORS.get(frame, FRAME_COLORS["white"])
    canvas = Image.new("RGB", (cw, ch), bg)
    for tile, (x, y) in zip(tiles_bgr, pos):
        t = cv2.resize(tile, (tw, th), interpolation=cv2.INTER_AREA)
        t = color_fx(t, fx)
        canvas.paste(Image.fromarray(cv2.cvtColor(t, cv2.COLOR_BGR2RGB)), (x, y))
    if scale < 0.4:          # bản xem trước nhỏ: bỏ chữ chân ảnh cho khỏi bị cắt
        return canvas
    d = ImageDraw.Draw(canvas)
    fg = (255, 255, 255) if sum(bg) < 450 else (60, 60, 60)
    f = _font(max(6, round(34 * scale)))
    label = f"{config.FOOTER_TEXT}  {datetime.date.today():%d/%m/%Y}"
    d.text(((cw - d.textlength(label, font=f)) / 2, ch - foot + round(22 * scale)), label, font=f, fill=fg)
    return canvas
