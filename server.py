"""Server nhỏ trên Pi: lưu phiên chụp và phục vụ trang tải ảnh /d/<token>.
Tuần sau chuyển sang VPS chỉ cần mang file này (hoặc phần route) sang."""
import json
import logging
import os
import re
import secrets
import shutil
import threading
import time

import cv2
from flask import Flask, abort, render_template_string, send_from_directory
from werkzeug.serving import make_server

import config

app = Flask(__name__)
logging.getLogger("werkzeug").setLevel(logging.WARNING)
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{10,64}$")

PAGE = """<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ảnh photobooth</title>
<style>
:root{--bg:#fafafa;--fg:#222;--card:#fff;--mut:#666;--ac:#e91e63}
@media(prefers-color-scheme:dark){:root{--bg:#16161a;--fg:#f2f2f2;--card:#222228;--mut:#aaa}}
body{margin:0;font-family:system-ui,sans-serif;background:var(--bg);color:var(--fg);text-align:center}
main{max-width:520px;margin:0 auto;padding:16px}
img,video{max-width:100%;border-radius:8px;box-shadow:0 2px 10px #0003}
.btn{display:block;margin:14px 0;padding:14px;border-radius:10px;background:var(--ac);color:#fff;text-decoration:none;font-weight:700}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.card{background:var(--card);border-radius:8px;padding:6px}
.card a{font-size:14px;color:var(--ac)}
small{color:var(--mut)}
</style></head><body><main>
<h2>Ảnh của bạn 📸</h2>
<img src="/f/{{t}}/{{strip}}" alt="Ảnh ghép">
<a class="btn" href="/f/{{t}}/{{strip}}?dl=1" download="photobooth.jpg">Tải ảnh ghép</a>
{% if video %}<h3>Video lúc chụp 🎬</h3>
<video controls playsinline preload="metadata" src="/f/{{t}}/{{video}}"></video>
<a class="btn" href="/f/{{t}}/{{video}}" download="photobooth.mp4">Tải video</a>{% endif %}
<h3>Từng tấm</h3>
<div class="grid">{% for s in shots %}
<div class="card"><img src="/f/{{t}}/{{s}}" alt=""><br><a href="/f/{{t}}/{{s}}?dl=1" download>Tải tấm {{loop.index}}</a></div>
{% endfor %}</div>
<p><small>Ảnh tự xóa sau {{hours}} giờ ({{left}} còn lại). Hãy tải về sớm nhé!</small></p>
</main></body></html>"""


def _meta(token):
    if not TOKEN_RE.match(token):
        abort(404)
    p = config.DATA_DIR / token / "meta.json"
    if not p.exists():
        abort(404)
    m = json.loads(p.read_text())
    if m["expires"] < time.time():
        abort(410)
    return m


@app.get("/d/<token>")
def page(token):
    m = _meta(token)
    left = max(0, int((m["expires"] - time.time()) // 3600))
    return render_template_string(PAGE, t=token, strip=m["strip"], shots=m["shots"], video=m.get("video"),
                                  hours=config.EXPIRE_HOURS, left=f"{left} giờ")


@app.get("/f/<token>/<name>")
def file(token, name):
    _meta(token)
    return send_from_directory(config.DATA_DIR / token, name)


@app.get("/healthz")
def health():
    return "ok"


def save_session(strip_img, tiles_bgr, video_path=None) -> str:
    """Lưu ảnh ghép + từng tấm, trả về token để tạo link/QR."""
    token = secrets.token_urlsafe(16)
    d = config.DATA_DIR / token
    d.mkdir(parents=True, exist_ok=True)
    strip_img.save(d / "strip.jpg", quality=config.JPEG_QUALITY)
    names = []
    for i, t in enumerate(tiles_bgr, 1):
        n = f"shot_{i}.jpg"
        cv2.imwrite(str(d / n), t, [cv2.IMWRITE_JPEG_QUALITY, config.JPEG_QUALITY])
        names.append(n)
    video = None
    if video_path and os.path.exists(video_path):
        shutil.move(str(video_path), str(d / "video.mp4"))
        video = "video.mp4"
    now = time.time()
    (d / "meta.json").write_text(json.dumps(
        {"created": now, "expires": now + config.EXPIRE_HOURS * 3600, "strip": "strip.jpg",
         "shots": names, "video": video}))
    return token


def link_for(token) -> str:
    return f"{config.PUBLIC_URL}/d/{token}"


def _cleanup_loop():
    while True:
        try:
            for d in config.DATA_DIR.glob("*"):
                mp = d / "meta.json"
                if mp.exists() and json.loads(mp.read_text())["expires"] < time.time():
                    shutil.rmtree(d, ignore_errors=True)
        except Exception:
            pass
        time.sleep(600)


def start():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    srv = make_server("0.0.0.0", config.PORT, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    threading.Thread(target=_cleanup_loop, daemon=True).start()
    return srv
