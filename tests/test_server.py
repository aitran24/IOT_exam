import sys
import os
import hashlib
import json
import secrets
import unittest
from datetime import datetime, timezone
import pyotp

# Add server dir to sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from fastapi.testclient import TestClient
from app.main import app
from app.config import settings
from app.database import SessionLocal, init_db
from app.models import (
    Admin, Device, Wallet, User, Payment, PaymentIntent,
    Session as PhotoSession, Media, Pairing
)
from app.security import hash_password, encrypt_str, hmac_hash, gen_ulid, gen_code

client = TestClient(app)

class PhotoboothServerTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        db = SessionLocal()
        # Seed test admin
        cls.admin_user = "test_owner"
        cls.admin_pass = "secret123456"
        cls.totp_secret = pyotp.random_base32()
        
        adm = db.query(Admin).filter(Admin.username == cls.admin_user).first()
        if not adm:
            adm = Admin(
                username=cls.admin_user,
                password_hash=hash_password(cls.admin_pass),
                totp_secret_enc=encrypt_str(cls.totp_secret),
                role="owner"
            )
            db.add(adm)
        else:
            adm.password_hash = hash_password(cls.admin_pass)
            adm.totp_secret_enc = encrypt_str(cls.totp_secret)
            adm.role = "owner"
            
        # Seed test device
        cls.device_id = "test_dev_01"
        cls.device_token = "device_test_secret_tok"
        dev = db.query(Device).filter(Device.id == cls.device_id).first()
        if not dev:
            dev = Device(
                id=cls.device_id,
                name="Booth Test 1",
                token_hash=hmac_hash(cls.device_token),
                mode="online"
            )
            db.add(dev)
            
        db.commit()
        db.close()

    def test_01_config_endpoint(self):
        """GET /api/v1/config returns system settings and business limits."""
        res = client.get("/api/v1/config")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["price_per_session_vnd"], 2000)
        self.assertEqual(data["max_sessions_per_pairing"], 3)
        self.assertEqual(data["topup"]["min_vnd"], 2000)
        self.assertIn("phone", data["auth"]["providers"])
        self.assertIn("google", data["auth"]["providers"])

    def test_02_guest_wallet_creation_and_balance(self):
        """Guest wallet lifecycle: create wallet, get balance, VietQR payload."""
        res = client.post("/api/v1/wallets")
        self.assertEqual(res.status_code, 201)
        data = res.json()
        code = data["wallet_code"]
        self.assertEqual(len(code), 8)
        self.assertEqual(data["balance_vnd"], 0)
        self.assertEqual(data["sessions_available"], 0)

        # Get wallet info
        res2 = client.get(f"/api/v1/wallets/{code}")
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(res2.json()["wallet_code"], code)

        # Get VietQR for wallet
        res3 = client.get(f"/api/v1/wallets/{code}/qr?amount=10000")
        self.assertEqual(res3.status_code, 200)
        qr_data = res3.json()
        self.assertEqual(qr_data["transfer_content"], f"PBW{code}")
        self.assertTrue(qr_data["qr_payload"].startswith("000201"))
        self.assertTrue(qr_data["qr_image_url"].startswith("data:image/png;base64,"))

    def test_03_sepay_webhook_processing(self):
        """SePay webhook: auth check, idempotency, regex parsing, crediting."""
        # 1. Invalid API Key
        res1 = client.post(
            "/api/v1/webhooks/sepay",
            headers={"Authorization": "Apikey wrong_key"},
            json={"id": 100, "transferAmount": 10000, "transferType": "in"}
        )
        self.assertEqual(res1.status_code, 401)

        # 2. Valid payment to Guest Wallet (create new guest wallet)
        w_res = client.post("/api/v1/wallets").json()
        g_code = w_res["wallet_code"]

        tx_id_1 = secrets.randbelow(1_000_000_000)
        webhook_payload = {
            "id": tx_id_1,
            "gateway": "MBBank",
            "transactionDate": "2026-11-15 10:30:00",
            "accountNumber": "0123456789",
            "subAccount": None,
            "code": f"PBW{g_code}",
            "content": f"PBW{g_code} nap tien photobooth",
            "transferType": "in",
            "description": "NGUYEN VAN A",
            "transferAmount": 10000,
            "referenceCode": "FT25015ABC123",
            "accumulated": 5000000
        }
        res2 = client.post(
            "/api/v1/webhooks/sepay",
            headers={"Authorization": f"Apikey {settings.SEPAY_API_KEY}"},
            json=webhook_payload
        )
        self.assertEqual(res2.status_code, 200)
        self.assertTrue(res2.json()["success"])

        # Verify guest balance: 10,000đ = 5 sessions, 0 leftover
        w_check = client.get(f"/api/v1/wallets/{g_code}").json()
        self.assertEqual(w_check["balance_vnd"], 10000)
        self.assertEqual(w_check["sessions_available"], 5)
        self.assertEqual(w_check["leftover_vnd"], 0)

        # 3. Anti-duplicate: same SePay transaction ID resubmitted
        res_dup = client.post(
            "/api/v1/webhooks/sepay",
            headers={"Authorization": f"Apikey {settings.SEPAY_API_KEY}"},
            json=webhook_payload
        )
        self.assertEqual(res_dup.status_code, 200)
        # Balance must NOT be doubled
        w_check_dup = client.get(f"/api/v1/wallets/{g_code}").json()
        self.assertEqual(w_check_dup["balance_vnd"], 10000)

        # 4. Bank content format with lowercase / spaces
        tx_id_2 = secrets.randbelow(1_000_000_000)
        webhook_payload_messy = {
            "id": tx_id_2,
            "gateway": "MBBank",
            "transferType": "in",
            "code": None, # Bank stripped code field
            "content": f"chuyen tien pbw {g_code.lower()} ung ho",
            "transferAmount": 1000,
            "referenceCode": "FT25015ABC124"
        }
        res_messy = client.post(
            "/api/v1/webhooks/sepay",
            headers={"Authorization": f"Apikey {settings.SEPAY_API_KEY}"},
            json=webhook_payload_messy
        )
        self.assertEqual(res_messy.status_code, 200)
        # Balance should now be 11,000đ (5 sessions, 1,000đ leftover)
        w_check3 = client.get(f"/api/v1/wallets/{g_code}").json()
        self.assertEqual(w_check3["balance_vnd"], 11000)
        self.assertEqual(w_check3["sessions_available"], 5)
        self.assertEqual(w_check3["leftover_vnd"], 1000)

    def test_04_auth_otp_and_refresh_rotation(self):
        """OTP validation, rate limits, token issuance, and reuse revocation."""
        from unittest.mock import patch
        
        # 1. Non-+84 phone rejected
        res_bad_phone = client.post("/api/v1/auth/otp/request", json={"phone": "0901234567"})
        self.assertEqual(res_bad_phone.status_code, 422)

        # 2. Valid +84 phone accepted (mock OTP to '654321')
        test_phone = "+84909876543"
        with patch("secrets.randbelow", return_value=654321):
            res_otp = client.post("/api/v1/auth/otp/request", json={"phone": test_phone})
        self.assertEqual(res_otp.status_code, 202)
        otp_id = res_otp.json()["otp_id"]

        # 3. Wrong code -> 400 with attempts_left
        res_wrong = client.post("/api/v1/auth/otp/verify", json={"otp_id": otp_id, "code": "000000"})
        self.assertEqual(res_wrong.status_code, 400)
        self.assertEqual(res_wrong.json()["error"]["code"], "invalid_code")

        # 4. Correct code -> login & token issue
        res_auth = client.post("/api/v1/auth/otp/verify", json={"otp_id": otp_id, "code": "654321"})
        self.assertEqual(res_auth.status_code, 200)
        auth_data = res_auth.json()
        access_tok = auth_data["access_token"]
        refresh_tok_1 = auth_data["refresh_token"]

        # 5. Rotate refresh token
        res_refresh = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_tok_1})
        self.assertEqual(res_refresh.status_code, 200)
        refresh_tok_2 = res_refresh.json()["refresh_token"]
        self.assertNotEqual(refresh_tok_1, refresh_tok_2)

        # 6. TOKEN REUSE DETECTED: attempting to reuse old refresh_tok_1
        res_reuse = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_tok_1})
        self.assertEqual(res_reuse.status_code, 401)
        self.assertEqual(res_reuse.json()["error"]["code"], "token_reuse_detected")

    def test_05_device_shooting_hold_and_complete_cycle(self):
        """Pi opens session -> holds 2000đ -> uploads media -> completes -> deducts."""
        # Ensure device is online
        db = SessionLocal()
        dev = db.query(Device).filter(Device.id == self.device_id).first()
        if dev:
            dev.mode = "online"
            db.commit()
        db.close()

        # 1. Create guest wallet with 2000đ
        w_res = client.post("/api/v1/wallets").json()
        g_code = w_res["wallet_code"]

        # Credit 2000đ via SePay webhook
        tx_id_5 = secrets.randbelow(1_000_000_000)
        client.post(
            "/api/v1/webhooks/sepay",
            headers={"Authorization": f"Apikey {settings.SEPAY_API_KEY}"},
            json={
                "id": tx_id_5,
                "transferType": "in",
                "code": f"PBW{g_code}",
                "transferAmount": 2000
            }
        )

        dev_headers = {"Authorization": f"Bearer {self.device_token}"}

        # 2. Pi opens session with guest wallet code
        open_res = client.post(
            "/api/v1/device/sessions",
            headers=dev_headers,
            json={
                "client_session_id": "client_ses_uuid_1",
                "source": {"type": "wallet_code", "code": g_code},
                "layout": "strip",
                "filter": "cat"
            }
        )
        self.assertEqual(open_res.status_code, 201)
        sess_data = open_res.json()
        sess_id = sess_data["session_id"]
        self.assertEqual(sess_data["hold_vnd"], 2000)

        # Check wallet: balance is still 2000, held is 2000, available is 0
        w_after_hold = client.get(f"/api/v1/wallets/{g_code}").json()
        self.assertEqual(w_after_hold["balance_vnd"], 2000)
        self.assertEqual(w_after_hold["held_vnd"], 2000)
        self.assertEqual(w_after_hold["available_vnd"], 0)

        # 3. Pi attempts opening a 2nd session -> insufficient balance (402)
        open_fail = client.post(
            "/api/v1/device/sessions",
            headers=dev_headers,
            json={
                "client_session_id": "client_ses_uuid_2",
                "source": {"type": "wallet_code", "code": g_code}
            }
        )
        self.assertEqual(open_fail.status_code, 402)
        self.assertEqual(open_fail.json()["error"]["code"], "insufficient_balance")

        # 4. Upload required media (strip + 4 shots)
        dummy_img = b"\xFF\xD8\xFF\xE0\x00\x10JFIF" + b"\x00" * 100
        img_sha = hashlib.sha256(dummy_img).hexdigest()

        for kind in ["strip", "shot_1", "shot_2", "shot_3", "shot_4"]:
            up_res = client.put(
                f"/api/v1/device/sessions/{sess_id}/media/{kind}",
                headers={
                    **dev_headers,
                    "Content-Type": "image/jpeg",
                    "X-Content-SHA256": img_sha
                },
                content=dummy_img
            )
            self.assertEqual(up_res.status_code, 200)
            self.assertTrue(up_res.json()["stored"])

        # 5. Complete session
        complete_res = client.post(
            f"/api/v1/device/sessions/{sess_id}/complete",
            headers=dev_headers,
            json={
                "layout": "strip",
                "filter": "cat",
                "shots": 4,
                "media": [{"kind": "strip", "bytes": len(dummy_img), "sha256": img_sha}]
            }
        )
        self.assertEqual(complete_res.status_code, 200)
        comp_data = complete_res.json()
        self.assertEqual(comp_data["status"], "completed")
        self.assertEqual(comp_data["charged_vnd"], 2000)
        self.assertIn("/d/", comp_data["share_url"])

        # Verify wallet balance is now 0 (deducted!)
        w_final = client.get(f"/api/v1/wallets/{g_code}").json()
        self.assertEqual(w_final["balance_vnd"], 0)
        self.assertEqual(w_final["held_vnd"], 0)
        self.assertEqual(w_final["available_vnd"], 0)

    def test_06_admin_login_and_operations(self):
        """Admin 2FA login, stats retrieval, and device mode switch."""
        totp = pyotp.TOTP(self.totp_secret)
        code = totp.now()

        # Login
        login_res = client.post("/api/v1/admin/login", json={
            "username": self.admin_user,
            "password": self.admin_pass,
            "totp_code": code
        })
        self.assertEqual(login_res.status_code, 200)
        admin_token = login_res.json()["token"]

        headers = {"Authorization": f"Bearer {admin_token}"}

        # Stats
        stats_res = client.get("/api/v1/admin/stats", headers=headers)
        self.assertEqual(stats_res.status_code, 200)
        self.assertEqual(stats_res.json()["role"], "owner")

        # Toggle device mode to maintenance
        dev_res = client.post(f"/api/v1/admin/devices/{self.device_id}/mode?mode=maintenance", headers=headers)
        self.assertEqual(dev_res.status_code, 200)
        self.assertEqual(dev_res.json()["mode"], "maintenance")

        # Device heartbeat reflects maintenance mode
        hb_res = client.post(
            "/api/v1/device/heartbeat",
            headers={"Authorization": f"Bearer {self.device_token}"},
            json={"state": "BAO_TRI", "cpu_temp_c": 50.0}
        )
        self.assertEqual(hb_res.status_code, 200)
        self.assertEqual(hb_res.json()["mode"], "maintenance")

    def test_07_pairing_shortfall_and_library_flow(self):
        """User pairing with machine, exact shortfall VietQR, completion and HMAC signed URLs."""
        # Ensure device online and free of active pairings
        db = SessionLocal()
        dev = db.query(Device).filter(Device.id == self.device_id).first()
        if dev:
            dev.mode = "online"
        db.query(Pairing).filter(
            Pairing.device_id == self.device_id,
            Pairing.status.in_(["claimed", "awaiting_payment", "ready", "in_use"])
        ).update({Pairing.status: "finished"})
        db.commit()
        db.close()

        # 1. Login user
        phone = "+84912345678"
        from unittest.mock import patch
        with patch("secrets.randbelow", return_value=112233):
            client.post("/api/v1/auth/otp/request", json={"phone": phone})
        auth_res = client.post("/api/v1/auth/otp/verify", json={"otp_id": "any", "code": "112233"})
        # Grab actual otp_id from db
        db = SessionLocal()
        from app.models import OTPRequest
        latest_otp = db.query(OTPRequest).order_by(OTPRequest.created_at.desc()).first()
        db.close()
        auth_res = client.post("/api/v1/auth/otp/verify", json={"otp_id": latest_otp.id, "code": "112233"})
        user_tok = auth_res.json()["access_token"]
        user_headers = {"Authorization": f"Bearer {user_tok}"}

        # Accept terms
        client.post("/api/v1/me/terms", headers=user_headers, json={"version": "2026-11"})

        # Credit 1,000đ to user wallet
        db = SessionLocal()
        user_obj = db.query(User).filter(User.id == auth_res.json()["user"]["id"]).first()
        user_obj.wallet.balance_vnd = 1000
        db.commit()
        db.close()

        # 2. Device creates pairing code
        dev_headers = {"Authorization": f"Bearer {self.device_token}"}
        pc_res = client.post("/api/v1/device/pairing-codes", headers=dev_headers)
        self.assertEqual(pc_res.status_code, 200)
        pc_data = pc_res.json()
        pair_code = pc_data["code"]

        # 3. User claims pairing with 6-char code
        claim_res = client.post("/api/v1/pairings", headers=user_headers, json={
            "method": "code",
            "code": pair_code
        })
        self.assertEqual(claim_res.status_code, 201)
        pairing_id = claim_res.json()["id"]

        # 4. User sets plan: 2 sessions (cost 4,000đ). Wallet has 1,000đ -> shortfall is 3,000đ!
        plan_res = client.post(f"/api/v1/pairings/{pairing_id}/plan", headers=user_headers, json={"sessions": 2})
        self.assertEqual(plan_res.status_code, 200)
        plan_data = plan_res.json()
        self.assertEqual(plan_data["status"], "awaiting_payment")
        self.assertEqual(plan_data["shortfall_vnd"], 3000)
        self.assertIsNotNone(plan_data["topup"])
        topup_ref = plan_data["topup"]["payment"]["transfer_content"]
        self.assertTrue(topup_ref.startswith("PBI"))

        # 5. Customer pays 3,000đ via SePay
        sepay_tx = secrets.randbelow(1_000_000_000)
        sp_res = client.post(
            "/api/v1/webhooks/sepay",
            headers={"Authorization": f"Apikey {settings.SEPAY_API_KEY}"},
            json={
                "id": sepay_tx,
                "transferType": "in",
                "code": topup_ref,
                "transferAmount": 3000
            }
        )
        self.assertEqual(sp_res.status_code, 200)

        # 6. Pairing auto-transitions to 'ready'
        p_check = client.get(f"/api/v1/pairings/{pairing_id}", headers=user_headers).json()
        self.assertEqual(p_check["status"], "ready")

        # 7. Pi opens shooting session
        # Ensure device online
        db = SessionLocal()
        dev = db.query(Device).filter(Device.id == self.device_id).first()
        dev.mode = "online"
        db.commit()
        db.close()

        open_res = client.post(
            "/api/v1/device/sessions",
            headers=dev_headers,
            json={
                "client_session_id": "ses_plan_1",
                "source": {"type": "pairing", "id": pairing_id},
                "layout": "strip"
            }
        )
        self.assertEqual(open_res.status_code, 201)
        p_sess_id = open_res.json()["session_id"]

        # 8. Upload media
        dummy_img = b"\xFF\xD8\xFF\xE0\x00\x10JFIF" + b"\x01" * 80
        img_sha = hashlib.sha256(dummy_img).hexdigest()
        for kind in ["strip", "shot_1", "shot_2", "shot_3", "shot_4"]:
            client.put(
                f"/api/v1/device/sessions/{p_sess_id}/media/{kind}",
                headers={**dev_headers, "Content-Type": "image/jpeg", "X-Content-SHA256": img_sha},
                content=dummy_img
            )

        # 9. Complete session
        comp_res = client.post(
            f"/api/v1/device/sessions/{p_sess_id}/complete",
            headers=dev_headers,
            json={
                "layout": "strip",
                "shots": 4,
                "media": [{"kind": "strip", "bytes": len(dummy_img), "sha256": img_sha}]
            }
        )
        self.assertEqual(comp_res.status_code, 200)
        self.assertEqual(comp_res.json()["owner"], "user")
        self.assertEqual(comp_res.json()["remaining_sessions"], 1)

        # 10. Check User Library
        lib_res = client.get("/api/v1/library", headers=user_headers)
        self.assertEqual(lib_res.status_code, 200)
        lib_data = lib_res.json()
        self.assertGreaterEqual(len(lib_data["items"]), 1)
        first_item = lib_data["items"][0]
        self.assertIsNotNone(first_item["thumbnail_url"])

        # 11. Test signed media URL verification
        det_res = client.get(f"/api/v1/library/{first_item['id']}", headers=user_headers)
        self.assertEqual(det_res.status_code, 200)
        media_files = det_res.json()["media"]
        self.assertGreaterEqual(len(media_files), 1)
        strip_file = media_files[0]
        signed_url = strip_file["url"]

        # Fetch with valid signature
        get_media_res = client.get(signed_url)
        self.assertEqual(get_media_res.status_code, 200)
        self.assertEqual(get_media_res.headers["content-type"], "image/jpeg")

        # Fetch with forged/tampered signature -> 403 Forbidden
        tampered_url = signed_url.replace("sig=", "sig=tampered_hex_")
        bad_sig_res = client.get(tampered_url)
        self.assertEqual(bad_sig_res.status_code, 403)
        self.assertEqual(bad_sig_res.json()["error"]["code"], "signature_invalid")

    def test_08_background_worker_cleanup(self):
        """Worker releases expired holds and deletes expired guest media."""
        import asyncio
        from datetime import timedelta
        from app.worker import run_cleanup_cycle
        from app.models import Session as PhotoSession, Wallet

        w_id = gen_ulid("wal_")
        sess_id = gen_ulid("ses_")
        past_time = datetime.now(timezone.utc) - timedelta(minutes=20)

        db = SessionLocal()
        try:
            w = Wallet(id=w_id, kind="guest", code=gen_code(8), balance_vnd=2000, held_vnd=2000)
            db.add(w)
            db.flush()

            sess = PhotoSession(
                id=sess_id,
                wallet_id=w.id,
                device_id=self.device_id,
                status="started",
                hold_expires_at=past_time,
                created_at=past_time
            )
            db.add(sess)
            db.commit()
        finally:
            db.close()

        # Run worker cycle
        asyncio.run(run_cleanup_cycle())

        # Verify hold was released back to wallet
        db2 = SessionLocal()
        try:
            db_w = db2.query(Wallet).filter(Wallet.id == w_id).first()
            db_sess = db2.query(PhotoSession).filter(PhotoSession.id == sess_id).first()
            self.assertEqual(db_w.held_vnd, 0)
            self.assertEqual(db_sess.status, "expired")
        finally:
            db2.close()

    def test_09_html_portals(self):
        """Test rendering of all HTML web interfaces (Guest portal, Admin login, Admin dashboard, Share page)"""
        r_guest = client.get("/guest")
        self.assertEqual(r_guest.status_code, 200)
        self.assertIn("Ví Khách Không Cần App", r_guest.text)

        r_login = client.get("/admin/login")
        self.assertEqual(r_login.status_code, 200)
        self.assertIn("Cổng Quản Trị Photobooth", r_login.text)

        r_dash = client.get("/admin")
        self.assertEqual(r_dash.status_code, 200)
        self.assertIn("Bảng Điều Khiển Quản Trị", r_dash.text)

        r_expired = client.get("/d/non_existent_token")
        self.assertEqual(r_expired.status_code, 404)
        self.assertIn("hết hạn", r_expired.text.lower())

if __name__ == "__main__":
    unittest.main()
