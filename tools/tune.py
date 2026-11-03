import cv2
import numpy as np
import subprocess
import os

CAM = "/dev/video0"
W, H = 320, 240
FLIP = 1   # 1 = lật gương trái/phải, None = không lật
SAVE_FILE = os.path.expanduser("~/test/color_params.txt")

# key, nhãn, giá trị, mặc định, bước, min, max, điều khiển webcam (None = phần mềm)
P = [
    ["gain_r", "R gain",   1.00, 1.00, 0.02, 0.5, 1.5, None],
    ["gain_g", "G gain",   1.00, 1.00, 0.02, 0.5, 1.5, None],
    ["gain_b", "B gain",   1.00, 1.00, 0.02, 0.5, 1.5, None],
    ["gamma",  "Gamma",    1.00, 1.00, 0.05, 0.5, 2.0, None],
    ["black",  "Black pt", 0,    0,    2,    0,   40,  None],
    ["sat",    "Cam Sat",  24,   24,   2,    0,   32,  "saturation"],
    ["con",    "Cam Con",  145,  145,  5,    0,   255, "contrast"],
    ["bri",    "Cam Bri",  128,  128,  5,    0,   255, "brightness"],
    ["wb",     "Cam WB",   0,    0,    20,   0,   511, "wb"],   # 0 = tự động
]

def v4l(name, val):
    subprocess.run(["v4l2-ctl", "-d", CAM, "-c", f"{name}={val}"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def apply_cam(p):
    if p[7] is None:
        return
    if p[7] == "wb":
        if p[2] == 0:
            v4l("white_balance_automatic", 1)
        else:
            v4l("white_balance_automatic", 0)
            v4l("white_balance_temperature", int(p[2]))
    else:
        v4l(p[7], int(p[2]))

def fmt(p):
    if p[0] == "wb" and p[2] == 0:
        return "AUTO"
    return f"{p[2]:.2f}" if isinstance(p[4], float) else str(int(p[2]))

def get(key):
    return next(p[2] for p in P if p[0] == key)

def build_lut():
    x = np.arange(256, dtype=np.float32)
    bp = get("black")
    x = np.clip((x - bp) / (255 - bp), 0, 1) ** get("gamma") * 255
    mk = lambda g: np.clip(x * g, 0, 255).astype(np.uint8)
    return mk(get("gain_b")), mk(get("gain_g")), mk(get("gain_r"))

def print_result():
    lines = [
        f"GAIN_B, GAIN_G, GAIN_R = {get('gain_b'):.2f}, {get('gain_g'):.2f}, {get('gain_r'):.2f}",
        f"GAMMA = {get('gamma'):.2f}",
        f"BLACK_POINT = {int(get('black'))}",
        f"# webcam: saturation={int(get('sat'))} contrast={int(get('con'))} "
        f"brightness={int(get('bri'))} wb={'AUTO' if get('wb') == 0 else int(get('wb'))}",
    ]
    text = "\n".join(lines)
    print("\n===== SO DA CHINH =====\n" + text + "\n=======================\n", flush=True)
    with open(SAVE_FILE, "w") as f:
        f.write(text + "\n")

# ---- thanh điều khiển nhỏ ở đáy ----
BAR_H = 28
BAR_Y = H - BAR_H
# tên: (x0, x1)
BTN = {
    "<": (2, 26), ">": (28, 52),
    "-": (168, 196), "+": (198, 226),
    "SAVE": (229, 266), "RST": (268, 292), "X": (294, 318),
}
state = {"show": True, "sel": 0, "lut": build_lut(), "quit": False}

def change(sign):
    p = P[state["sel"]]
    p[2] = round(min(p[6], max(p[5], p[2] + sign * p[4])), 2)
    apply_cam(p)
    state["lut"] = build_lut()
    print(f"{p[1]} = {fmt(p)}", flush=True)

def on_mouse(event, x, y, flags, param):
    if event != cv2.EVENT_LBUTTONDOWN:
        return
    if not state["show"]:
        state["show"] = True
        return
    if y < BAR_Y:                 # chạm vào vùng hình -> ẩn thanh
        state["show"] = False
        return
    for name, (x0, x1) in BTN.items():
        if x0 <= x <= x1:
            if name == "<":
                state["sel"] = (state["sel"] - 1) % len(P)
            elif name == ">":
                state["sel"] = (state["sel"] + 1) % len(P)
            elif name == "-":
                change(-1)
            elif name == "+":
                change(+1)
            elif name == "SAVE":
                print_result()
            elif name == "RST":
                for p in P:
                    p[2] = p[3]
                    apply_cam(p)
                state["lut"] = build_lut()
                print("RESET", flush=True)
            elif name == "X":
                state["quit"] = True

def button(img, x0, x1, text):
    y0, y1 = BAR_Y + 4, H - 4
    cv2.rectangle(img, (x0, y0), (x1, y1), (80, 80, 80), -1)
    cv2.rectangle(img, (x0, y0), (x1, y1), (190, 190, 190), 1)
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.4, 1)
    cv2.putText(img, text, (x0 + (x1 - x0 - tw) // 2, y0 + (y1 - y0 + th) // 2),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_AA)

def draw_ui(frame):
    if not state["show"]:
        return
    # nền thanh mờ, chỉ che đúng dải đáy
    bar = frame[BAR_Y:H]
    frame[BAR_Y:H] = (bar * 0.35).astype(np.uint8)
    p = P[state["sel"]]
    cv2.putText(frame, f"{p[1]} {fmt(p)}", (58, BAR_Y + 19),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1, cv2.LINE_AA)
    for name, (x0, x1) in BTN.items():
        button(frame, x0, x1, name)

# ---- khởi động ----
for p in P:
    apply_cam(p)

cap = cv2.VideoCapture(CAM, cv2.CAP_V4L2)
cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
if not cap.isOpened():
    raise SystemExit("Không mở được webcam")

cv2.namedWindow("tune", cv2.WINDOW_NORMAL)
cv2.setWindowProperty("tune", cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
cv2.setMouseCallback("tune", on_mouse)

while not state["quit"]:
    ok, frame = cap.read()
    if not ok:
        break
    if FLIP is not None:
        frame = cv2.flip(frame, FLIP)
    frame = cv2.resize(frame, (W, H))
    lb, lg, lr = state["lut"]
    b, g, r = cv2.split(frame)
    frame = cv2.merge([cv2.LUT(b, lb), cv2.LUT(g, lg), cv2.LUT(r, lr)])
    draw_ui(frame)
    cv2.imshow("tune", frame)
    if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
        break

cap.release()
cv2.destroyAllWindows()
print_result()