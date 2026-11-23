"""Quay video lúc chụp bằng ffmpeg (H.264, điện thoại xem được ngay).
Cần: sudo apt install -y ffmpeg. Không có ffmpeg thì bỏ qua video, phần còn lại vẫn chạy."""
import os
import shutil
import subprocess
import threading
import time

import numpy as np

import config


class VideoRecorder:
    def __init__(self, path):
        self.path = str(path)
        self.latest = None            # main đặt khung hình mới nhất vào đây (BGR, đúng VIDEO_SIZE)
        self.done = threading.Event()
        self._stop = False
        self.proc = None
        exe = shutil.which("ffmpeg")
        if not exe:
            print("CẢNH BÁO: chưa cài ffmpeg nên không quay video (sudo apt install -y ffmpeg)", flush=True)
            self.done.set()
            return
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        w, h = config.VIDEO_SIZE
        cmd = [exe, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}",
               "-r", str(config.VIDEO_FPS), "-i", "-", "-an", "-c:v", "libx264", "-preset", "ultrafast",
               "-crf", "28", "-pix_fmt", "yuv420p", "-movflags", "+faststart", self.path]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        threading.Thread(target=self._run, daemon=True).start()

    @property
    def ok(self):
        return self.proc is not None

    def _run(self):
        period = 1 / config.VIDEO_FPS
        nxt = time.monotonic()
        try:
            while not self._stop:
                f = self.latest
                if f is not None:     # luôn ghi đều đặn; nếu main chậm thì lặp khung cũ -> video đúng thời lượng
                    self.proc.stdin.write(np.ascontiguousarray(f))
                nxt += period
                time.sleep(max(0, nxt - time.monotonic()))
        except (BrokenPipeError, OSError):
            pass
        finally:
            try:
                self.proc.stdin.close()
            except OSError:
                pass
            self.proc.wait()
            self.done.set()

    def stop(self):
        """Kết thúc quay (không chặn). Gọi wait() để chờ file hoàn tất."""
        self._stop = True

    def wait(self, timeout=30):
        self.done.wait(timeout)
        return self.ok and os.path.exists(self.path) and os.path.getsize(self.path) > 0

    def abort(self):
        self._stop = True
        if self.proc is not None:
            self.proc.kill()
        self.done.wait(5)
        try:
            os.remove(self.path)
        except OSError:
            pass
