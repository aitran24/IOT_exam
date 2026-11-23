import os
import asyncio
import logging
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.database import SessionLocal, get_setting_int
from app.config import settings
from app.models import (
    Session as PhotoSession, Media, Wallet, Ledger,
    PairingCode, Pairing, PaymentIntent, User, UserIdentity
)
from app.security import ensure_utc

logger = logging.getLogger("worker")

async def run_cleanup_cycle():
    """Execute one periodic cleanup iteration."""
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        price = get_setting_int(db, "price_per_session_vnd", settings.DEFAULT_PRICE_PER_SESSION_VND)
        
        # 1. Release expired holds (Section 6 & 9)
        expired_holds = db.query(PhotoSession).filter(
            PhotoSession.status == "started",
            PhotoSession.hold_expires_at != None,
            PhotoSession.hold_expires_at < now
        ).all()
        
        for sess in expired_holds:
            wallet = sess.wallet
            if wallet and wallet.held_vnd >= price:
                wallet.held_vnd -= price
                led = Ledger(
                    wallet_id=wallet.id,
                    delta_balance_vnd=0,
                    delta_held_vnd=-price,
                    balance_after_vnd=wallet.balance_vnd,
                    held_after_vnd=wallet.held_vnd,
                    reason="release",
                    ref_id=f"timeout_{sess.id}",
                    actor="system:worker",
                    created_at=now
                )
                db.add(led)
            sess.status = "expired"
            logger.info(f"[Worker] Released expired hold for session {sess.id}")
            
        # 2. Cleanup expired guest media (> 24 hours per Section 15.4)
        expired_guest_sessions = db.query(PhotoSession).filter(
            PhotoSession.share_token != None,
            PhotoSession.share_expires_at != None,
            PhotoSession.share_expires_at < now,
            PhotoSession.deleted_at == None
        ).all()
        
        for g_sess in expired_guest_sessions:
            g_sess.deleted_at = now
            media_items = db.query(Media).filter(Media.session_id == g_sess.id).all()
            for m in media_items:
                m.deleted_at = now
                if m.storage_key and os.path.exists(m.storage_key):
                    try:
                        os.remove(m.storage_key)
                        logger.info(f"[Worker] Purged guest media file: {m.storage_key}")
                    except Exception as e:
                        logger.error(f"[Worker] Failed to delete file {m.storage_key}: {e}")
                        
        # 3. Expire pairing codes (> 120s) and idle pairings (> 15m)
        db.query(PairingCode).filter(
            PairingCode.status == "waiting",
            PairingCode.expires_at < now
        ).update({PairingCode.status: "expired"})
        
        db.query(Pairing).filter(
            Pairing.status.in_(["claimed", "awaiting_payment"]),
            Pairing.expires_at < now
        ).update({Pairing.status: "expired"})
        
        # 4. Expire pending topup intents (> 15m)
        db.query(PaymentIntent).filter(
            PaymentIntent.status == "pending",
            PaymentIntent.expires_at < now
        ).update({PaymentIntent.status: "expired"})
        
        # 5. Purge pending deleted user accounts after 7-day grace period (Section 10.3)
        pending_users = db.query(User).filter(
            User.status == "pending_delete",
            User.delete_after != None,
            User.delete_after < now
        ).all()
        
        for u in pending_users:
            logger.info(f"[Worker] Permanently deleting user account: {u.id}")
            # Delete user sessions and photos
            user_sessions = db.query(PhotoSession).filter(PhotoSession.user_id == u.id).all()
            for us in user_sessions:
                us.deleted_at = now
                for m in db.query(Media).filter(Media.session_id == us.id).all():
                    m.deleted_at = now
                    if m.storage_key and os.path.exists(m.storage_key):
                        try:
                            os.remove(m.storage_key)
                        except Exception:
                            pass
            # Anonymize user ledger entries
            wallet = u.wallet
            if wallet:
                wallet.user_id = None
                wallet.status = "locked"
            # Remove identities & tokens
            db.query(UserIdentity).filter(UserIdentity.user_id == u.id).delete()
            db.delete(u)
            
        db.commit()

async def worker_loop(interval_seconds: int = 60):
    """Background loop that runs cleanup tasks periodically."""
    logger.info("Photobooth background cleanup worker started.")
    while True:
        try:
            await run_cleanup_cycle()
        except Exception as e:
            logger.error(f"[Worker] Error during cleanup cycle: {e}")
        await asyncio.sleep(interval_seconds)
