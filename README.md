# Photobooth Web Server & Backend VPS

Hệ thống Web Server & Backend cho hệ thống Photobooth tự phục vụ (phiên bản 2.0). Server chịu trách nhiệm quản lý toàn bộ logic tài chính, số dư ví, tài khoản, xác thực hai lớp (2FA), điều khiển thiết bị Raspberry Pi, tích hợp cổng thanh toán SePay (VietQR), quản lý kho ảnh/video và cung cấp giao diện quản trị (Admin Portal) cùng giao diện thân thiện cho khách hàng web.

---

## 1. Kiến Trúc & Tính Năng Nổi Bật

### 1.1 Vai Trò Của Server (Section 4 & 5)
- **Bảo toàn số dư & giao dịch:** Mọi dữ liệu về ví, tiền nạp, giữ/trừ lượt, sổ cái (`ledger`) đều nằm trên server với tính nguyên tử (Atomicity). Raspberry Pi bị tắt nguồn hay mất điện thì tiền của khách vẫn an toàn tuyệt đối.
- **Không mở cổng vào Pi:** Raspberry Pi kết nối ra ngoài tới server qua HTTPS bằng `Bearer <device_token>` và thăm dò dài (`?wait=25`).
- **Phân loại người dùng:**
  - **Người dùng App:** Tài khoản liên kết SĐT (OTP) hoặc Google; ví gắn với tài khoản; ảnh lưu trữ vĩnh viễn trong thư viện đến khi người dùng tự xóa.
  - **Khách Web (Không cần app):** Ví định danh bằng mã 8 ký tự; nạp tiền qua VietQR; ảnh và video lưu trữ trong **24 giờ** theo quy định về dữ liệu cá nhân.

### 1.2 Đáp Ứng Yêu Cầu Bảo Mật Cao (Section 15 & OWASP API Top 10)
- **Xác thực Admin:** Mật khẩu băm chuẩn **Argon2**, bắt buộc **2FA TOTP** (Google Authenticator), phiên làm việc tự hết hạn sau 30 phút. Phân quyền chặt chẽ giữa **Owner** (toàn quyền, xem doanh thu, đổi cấu hình) và **Staff** (vận hành máy, xem siêu dữ liệu, không xem doanh thu).
- **Quyền riêng tư dữ liệu cá nhân (Luật 91/2025/QH15):** 
  - Ảnh của khách web tự động bị Worker xóa vĩnh viễn sau 24 giờ.
  - Staff quản trị chỉ nhìn thấy siêu dữ liệu; nếu muốn xem ảnh của khách hàng phải nhập lý do nghiệp vụ và được ghi lại trong sổ cái kiểm toán (`audit_log`).
- **Mã hóa cột nhạy cảm:** Số điện thoại và bí mật TOTP được mã hóa bằng thuật toán **AES-256-GCM**; tra cứu bằng chỉ mục băm **HMAC-SHA256**.
- **Chống gian lận Refresh Token:** Refresh token dạng xoay vòng (rotating refresh tokens). Nếu phát hiện token cũ bị dùng lại (`token_reuse_detected`), toàn bộ chuỗi đăng nhập của thiết bị sẽ lập tức bị thu hồi.
- **Bảo mật truy cập ảnh:** Đường dẫn xem/tải ảnh trong thư viện được ký điện tử bằng **HMAC-SHA256** với thời hạn 10–15 phút. Can thiệp hoặc hết hạn đường dẫn sẽ bị từ chối với lỗi `403 signature_invalid`.
- **Cơ chế Giữ - Trừ - Nhả tiền (Hold / Deduct / Release):**
  - Mở phiên: Giữ tạm 2.000đ (`held_vnd += 2000`). Nếu 15 phút không chụp xong (treo máy, mất điện), Worker tự động nhả khoản giữ trả lại ví khách.
  - Hoàn tất: Chỉ trừ thật (`balance_vnd -= 2000`) khi server đã nhận đủ các tấm ảnh bắt buộc (`strip`, `shot_1`..`shot_4`) và kiểm tra khớp mã băm SHA256.

---

## 2. Cấu Trúc Thư Mục

```
server/
├── app/
│   ├── config.py             # Cấu hình hệ thống, biến môi trường, tham số mặc định
│   ├── database.py           # Kết nối CSDL (PostgreSQL / SQLite), khởi tạo bảng
│   ├── models.py             # SQLAlchemy models theo đúng thiết kế CSDL (Mục 8)
│   ├── schemas.py            # Pydantic schemas cho API & phản hồi lỗi chuẩn
│   ├── security.py           # Argon2, TOTP, AES-256-GCM, HMAC, JWT, ULID, mã ví 31 ký tự
│   ├── vietqr.py             # Sinh chuỗi VietQR chuẩn NAPAS EMVCo & ảnh QR base64
│   ├── deps.py               # Dependency injection, Rate limiter, Auth guards
│   ├── idempotency.py        # Xử lý Idempotency-Key lưu trữ 24 giờ
│   ├── worker.py             # Background task (dọn phiên hết hạn, xóa ảnh quá 24h, v.v.)
│   ├── main.py               # Ứng dụng FastAPI chính, lifespan, security headers middleware
│   ├── routers/
│   │   ├── config_routes.py  # GET /api/v1/config
│   │   ├── auth_routes.py    # OTP, Google, Refresh Token, Step-up
│   │   ├── user_routes.py    # Quản lý tài khoản cá nhân (/api/v1/me)
│   │   ├── wallet_routes.py  # Ví người dùng, nạp tiền VietQR, lịch sử giao dịch
│   │   ├── pairing_routes.py # Ghép nối máy photobooth bằng QR / mã 6 ký tự
│   │   ├── library_routes.py # Thư viện ảnh, URL ký HMAC, xóa theo yêu cầu
│   │   ├── event_routes.py   # Real-time SSE (/api/v1/events)
│   │   ├── device_routes.py  # API dành cho Raspberry Pi (/api/v1/device/*)
│   │   ├── sepay_routes.py   # Webhook SePay xử lý biến động số dư ngân hàng
│   │   ├── guest_routes.py   # Ví khách 8 ký tự, trang tải ảnh /d/{token}
│   │   ├── media_routes.py   # Phục vụ tệp ảnh/video với chữ ký HMAC
│   │   └── admin_routes.py   # API & Giao diện Quản trị (/admin)
│   ├── static/               # CSS, JS, hình ảnh tĩnh
│   └── templates/
│       ├── guest_wallet.html # Giao diện web tạo ví & nạp tiền VietQR cho khách
│       ├── guest_share.html  # Trang tải ảnh khách kèm đồng hồ đếm ngược 24h
│       ├── guest_expired.html# Trang thông báo khi ảnh đã hết hạn lưu trữ
│       ├── admin_login.html  # Cổng đăng nhập quản trị viên với 2FA TOTP
│       └── admin_dash.html   # Bảng điều khiển quản trị toàn diện
├── storage/                  # Thư mục lưu trữ ảnh và video
├── tests/
│   └── test_server.py        # Bộ kiểm thử tự động toàn diện (8 test suites)
├── Dockerfile                # Dockerfile triển khai production (user ít quyền)
├── docker-compose.yml        # Docker compose gồm FastAPI, PostgreSQL 16 và Caddy
├── Caddyfile                 # Cấu hình Caddy reverse proxy và tự động HTTPS
├── .env.example              # Mẫu biến môi trường
├── init_admin.py             # Script khởi tạo tài khoản Admin & Device ban đầu
└── run.py                    # Script chạy server phát triển
```

---

## 3. Hướng Dẫn Cài Đặt & Chạy Server

### 3.1 Chạy trực tiếp với Python (Phát triển / Thử nghiệm)

1. Cài đặt các thư viện cần thiết:
   ```bash
   pip install -r requirements.txt
   ```
2. Khởi tạo tài khoản Quản trị viên (Admin Owner) và thiết bị mặc định:
   ```bash
   python init_admin.py
   ```
   *Script sẽ tạo admin `admin`, mật khẩu `admin123456`, mã bí mật TOTP và in ra mã OTP hiện tại.*
3. Khởi chạy server:
   ```bash
   python run.py
   ```
   Server sẽ lắng nghe tại: `http://localhost:8000`

### 3.2 Triển khai Production trên VPS (Docker & Caddy)

Theo đúng các bước trong **Mục 16** của tài liệu thiết kế:

1. Sao chép file cấu hình môi trường:
   ```bash
   cp .env.example .env
   # Chỉnh sửa DOMAIN, bí mật AES, JWT, và SEPAY_API_KEY trong file .env
   ```
2. Khởi chạy toàn bộ hệ thống bằng Docker Compose:
   ```bash
   docker compose up -d --build
   ```
   Hệ thống gồm 3 container độc lập:
   - `photobooth-db`: PostgreSQL 16
   - `photobooth-api`: FastAPI backend & background worker
   - `photobooth-caddy`: Caddy server tự động cấp phát chứng chỉ HTTPS Let's Encrypt

---

## 4. Giao Diện Người Dùng & Quản Trị Thân Thiện

### 4.1 Trang Ví Khách Hàng Web (`/guest`)
- Dành cho khách không cài app.
- Tự động tạo và lưu mã ví 8 ký tự (thuộc bảng 31 ký tự không gây nhầm lẫn).
- Nạp tiền nhanh (1 lượt 2.000đ, 3 lượt 6.000đ, 5 lượt 10.000đ) qua **VietQR**.
- Tự động cập nhật số dư tức thì khi có biến động tiền về.

### 4.2 Trang Tải Ảnh & Video Cho Khách (`/d/{share_token}`)
- Khách quét mã QR tại máy photobooth sau khi chụp xong để mở trang này.
- Hiển thị trực tiếp ảnh ghép strip, lưới từng tấm ảnh chụp và video hậu trường (MP4 H.264).
- Nút tải về 1-chạm (`?download=1`).
- Đồng hồ đếm ngược trực quan hiển thị thời gian còn lại trước khi ảnh tự động xóa (chính sách lưu 24 giờ).
- Ghi chú bảo vệ quyền riêng tư theo Luật Bảo vệ dữ liệu cá nhân 91/2025/QH15.

### 4.3 Cổng Quản Trị Hệ Thống (`/admin` & `/admin/login`)
- Đăng nhập bảo mật với mật khẩu Argon2 và mã xác thực 2 bước (TOTP 2FA).
- **Tab Doanh thu & Thống kê:** Doanh thu hôm nay, tuần này, tháng này, biểu đồ theo giờ, tổng nạp, tổng lượt chụp, số dư nợ khách, xuất báo cáo CSV.
- **Tab Điều khiển máy Pi:** Trạng thái online/offline thời gian thực, nhiệt độ CPU, dung lượng ổ đĩa, camera, chuyển chế độ Bảo trì, gửi lệnh khởi động lại App / Reboot máy.
- **Tab Người dùng App:** Tra cứu người dùng, số điện thoại (che bảo mật), cộng/trừ số dư thủ công (bắt buộc nhập lý do), khóa/mở khóa tài khoản.
- **Tab Giao dịch SePay chưa khớp:** Hiển thị tiền chuyển vào nhưng ngân hàng lược bỏ mã; cho phép Admin bấm 1 nút để gán tiền vào ví khách hàng.
- **Tab Kho ảnh & Quyền riêng tư:** Kiểm soát dung lượng lưu trữ, bảo mật quyền riêng tư (Staff chỉ xem metadata, xem ảnh phải nhập lý do nghiệp vụ và được ghi vào audit log).
- **Tab Nhật ký kiểm toán (Audit Log):** Lưu lại toàn bộ ai làm gì, lúc nào, từ IP nào.

---

## 5. Chạy Kiểm Thử Tự Động (Unit Tests)

Bộ test bao phủ toàn bộ các kịch bản quan trọng trong **Mục 18** của tài liệu thiết kế:
```bash
python tests/test_server.py
```
Kết quả kiểm thử kiểm tra:
1. Endpoint cấu hình chung (`/config`).
2. Vòng đời ví khách và định dạng chuỗi VietQR chuẩn NAPAS.
3. Webhook SePay (xác thực API Key, chống trùng giao dịch, nội dung viết thường/khoảng trắng).
4. Xác thực số điện thoại OTP (+84), bảo mật chống brute-force, xoay vòng refresh token, phát hiện dùng lại token cũ.
5. Vòng đời phiên chụp tại máy: Tạm giữ 2.000đ, từ chối phiên mở đồng thời khi hết tiền, tải lên ảnh với kiểm tra mã băm SHA256, hoàn tất phiên và trừ tiền thật.
6. Quản trị viên 2FA TOTP và chuyển chế độ bảo trì máy.
7. Ghép máy app, nạp tiền đúng số tiền còn thiếu qua VietQR, cập nhật trạng thái `ready`, và xác thực chữ ký HMAC cho ảnh thư viện.
8. Background worker tự động giải phóng khoản tiền giữ quá 15 phút.
