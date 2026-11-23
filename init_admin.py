import sys
import os
import pyotp
from app.database import init_db, SessionLocal
from app.models import Admin, Device, Setting
from app.security import hash_password, encrypt_str, hmac_hash

def main():
    print("==================================================")
    print("   PHOTOBOOTH SYSTEM - ADMIN & DEVICE SETUP")
    print("==================================================")
    init_db()
    
    db = SessionLocal()
    try:
        # 1. Setup Admin Account
        admin_user = "admin"
        admin_pass = "admin123456"
        
        existing_admin = db.query(Admin).filter(Admin.username == admin_user).first()
        totp_secret = pyotp.random_base32()
        
        if existing_admin:
            existing_admin.password_hash = hash_password(admin_pass)
            existing_admin.totp_secret_enc = encrypt_str(totp_secret)
            existing_admin.role = "owner"
            db.commit()
            print(f"[*] Updated existing admin '{admin_user}'")
        else:
            new_admin = Admin(
                username=admin_user,
                password_hash=hash_password(admin_pass),
                totp_secret_enc=encrypt_str(totp_secret),
                role="owner"
            )
            db.add(new_admin)
            db.commit()
            print(f"[+] Created new owner admin '{admin_user}'")
            
        totp = pyotp.TOTP(totp_secret)
        current_otp = totp.now()
        
        print("\n---------------- ADMIN CREDENTIALS ----------------")
        print(f"Username   : {admin_user}")
        print(f"Password   : {admin_pass}")
        print(f"Role       : owner")
        print(f"TOTP Secret: {totp_secret}")
        print(f"Current OTP: {current_otp} (valid for ~30s)")
        print(f"TOTP URI   : {totp.provisioning_uri(name=admin_user, issuer_name='Photobooth QNV&AT')}")
        print("---------------------------------------------------\n")
        
        # 2. Setup Default Photobooth Device
        dev_id = "dev_01"
        dev_token = "photobooth_pi4_secure_token_2026"
        
        existing_dev = db.query(Device).filter(Device.id == dev_id).first()
        tok_hash = hmac_hash(dev_token)
        
        if existing_dev:
            existing_dev.token_hash = tok_hash
            existing_dev.name = "Photobooth QNV&AT - Sảnh A"
            existing_dev.mode = "online"
            db.commit()
            print(f"[*] Updated device '{dev_id}'")
        else:
            new_dev = Device(
                id=dev_id,
                name="Photobooth QNV&AT - Sảnh A",
                token_hash=tok_hash,
                mode="online"
            )
            db.add(new_dev)
            db.commit()
            print(f"[+] Created device '{dev_id}'")
            
        print("----------------- DEVICE CONFIG -------------------")
        print(f"Device ID    : {dev_id}")
        print(f"Device Token : {dev_token}")
        print("For Raspberry Pi (.env):")
        print(f"DEVICE_ID={dev_id}")
        print(f"DEVICE_TOKEN={dev_token}")
        print("---------------------------------------------------\n")
        print("[✓] Setup completed successfully!")
    finally:
        db.close()

if __name__ == "__main__":
    main()
