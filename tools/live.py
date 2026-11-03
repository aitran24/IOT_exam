# import cv2
# import subprocess

# CAM = "/dev/video0"
# W, H = 320, 240
# FLIP = 1    # 1 = lật gương trái/phải, None = không lật

# for c in ["white_balance_automatic=1", "saturation=24", "contrast=145",
#           "brightness=128", "gamma=5", "sharpness=8"]:
#     subprocess.run(["v4l2-ctl", "-d", CAM, "-c", c],
#                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# cap = cv2.VideoCapture(CAM, cv2.CAP_V4L2)
# cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
# cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
# cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
# if not cap.isOpened():
#     raise SystemExit("Không mở được webcam")

# cv2.namedWindow("live", cv2.WINDOW_NORMAL)
# cv2.setWindowProperty("live", cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

# while True:
#     ok, frame = cap.read()
#     if not ok:
#         break
#     if FLIP is not None:
#         frame = cv2.flip(frame, FLIP)
#     frame = cv2.resize(frame, (W, H))
#     cv2.imshow("live", frame)
#     if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
#         break

# cap.release()
# cv2.destroyAllWindows()
import cv2

CAM = "/dev/video0"
W, H = 320, 240
FLIP = 1    # 1 = lật gương trái/phải, None = không lật

cap = cv2.VideoCapture(CAM, cv2.CAP_V4L2)
cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
if not cap.isOpened():
    raise SystemExit("Không mở được webcam")

cv2.namedWindow("live", cv2.WINDOW_NORMAL)
cv2.setWindowProperty("live", cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

while True:
    ok, frame = cap.read()
    if not ok:
        break
    if FLIP is not None:
        frame = cv2.flip(frame, FLIP)
    frame = cv2.resize(frame, (W, H))
    cv2.imshow("live", frame)
    if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
        break

cap.release()
cv2.destroyAllWindows()