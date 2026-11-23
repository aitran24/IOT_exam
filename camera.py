"""Đọc webcam liên tục ở luồng riêng, luôn giữ khung hình mới nhất."""
import threading
import time

import cv2
import numpy as np

import config


class Camera:
    def __init__(self):
        self._frame = None
        self._lock = threading.Lock()
        self._running = True
        self.cap = None
        if not config.FAKE_CAM:
            self.cap = cv2.VideoCapture(config.CAM_DEVICE, cv2.CAP_V4L2)
            self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.CAP_W)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.CAP_H)
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            if not self.cap.isOpened():
                raise RuntimeError(f"Không mở được webcam {config.CAM_DEVICE}")
        threading.Thread(target=self._loop, daemon=True).start()

    def _fake(self, n):
        img = np.zeros((config.CAP_H, config.CAP_W, 3), np.uint8)
        img[:] = (60 + (n * 2) % 120, 90, 140)
        cv2.putText(img, "FAKE CAM", (80, 360), cv2.FONT_HERSHEY_SIMPLEX, 4, (255, 255, 255), 8)
        return img

    def _loop(self):
        n = 0
        while self._running:
            if self.cap is None:
                frame = self._fake(n)
                n += 1
                time.sleep(1 / 30)
            else:
                ok, frame = self.cap.read()
                if not ok:
                    time.sleep(0.05)
                    continue
            with self._lock:
                self._frame = frame

    def read(self):
        """Trả về khung hình mới nhất (chỉ đọc, đừng sửa trực tiếp), hoặc None nếu chưa có."""
        with self._lock:
            return self._frame

    def stop(self):
        self._running = False
        time.sleep(0.1)
        if self.cap is not None:
            self.cap.release()
