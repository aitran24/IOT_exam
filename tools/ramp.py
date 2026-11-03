import cv2, numpy as np

W, H = 320, 240
img = np.zeros((H, W, 3), np.uint8)
g = np.linspace(0, 255, W).astype(np.uint8)
rows = [
    lambda v: (v, v, v),          # xám
    lambda v: (0, 0, v),          # đỏ (BGR)
    lambda v: (0, v, 0),          # xanh lá
    lambda v: (v, 0, 0),          # xanh dương
    lambda v: (0, v, v),          # vàng
]
h = H // len(rows)
for i, f in enumerate(rows):
    b, gg, r = f(g)
    img[i*h:(i+1)*h, :, 0] = b
    img[i*h:(i+1)*h, :, 1] = gg
    img[i*h:(i+1)*h, :, 2] = r

cv2.namedWindow("ramp", cv2.WINDOW_NORMAL)
cv2.setWindowProperty("ramp", cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
cv2.imshow("ramp", img)
cv2.waitKey(0)