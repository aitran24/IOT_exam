import smtplib
import asyncio
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from app.config import settings

logger = logging.getLogger("mailer")

def _send_smtp_sync(to_email: str, subject: str, html_body: str, text_body: str):
    """Gửi email qua SMTP Brevo bằng TLS (đồng bộ, chạy trong thread pool)."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    from_name = settings.SMTP_FROM_NAME or "ZenApp"
    from_email = settings.SMTP_FROM_EMAIL or settings.SMTP_USER
    msg["From"] = f"{from_name} <{from_email}>"
    msg["To"] = to_email

    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    server = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15)
    try:
        server.ehlo()
        if settings.SMTP_USE_TLS:
            server.starttls()
            server.ehlo()
        if settings.SMTP_USER and settings.SMTP_PASSWORD:
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        server.sendmail(from_email, [to_email], msg.as_string())
    finally:
        try:
            server.quit()
        except Exception:
            pass

async def send_otp_email(to_email: str, otp_code: str, expires_in_minutes: int = 5) -> bool:
    """Gửi email chứa mã xác thực OTP bất đồng bộ (không block event loop)."""
    app_name = settings.SMTP_FROM_NAME or "ZenApp"
    subject = f"[{app_name}] Mã xác thực đăng nhập: {otp_code}"
    
    text_body = f"""Xin chào,

Mã xác thực đăng nhập (OTP) của bạn là: {otp_code}

Mã này có hiệu lực trong vòng {expires_in_minutes} phút.
LƯU Ý BẢO MẬT: Tuyệt đối không chia sẻ mã này với bất kỳ ai (kể cả nhân viên hỗ trợ).

Trân trọng,
Đội ngũ {app_name}
"""

    html_body = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Mã xác thực OTP</title>
</head>
<body style="margin: 0; padding: 0; background-color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
  <table width="100%" border="0" cellpadding="0" cellspacing="0" style="padding: 40px 10px;">
    <tr>
      <td align="center">
        <table width="480" border="0" cellpadding="0" cellspacing="0" style="background-color: #ffffff; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.05); padding: 36px; border: 1px solid #e2e8f0;">
          <tr>
            <td align="center" style="padding-bottom: 20px;">
              <h2 style="margin: 0; color: #0f172a; font-size: 26px; font-weight: 800; letter-spacing: -0.5px;">{app_name}</h2>
            </td>
          </tr>
          <tr>
            <td style="color: #334155; font-size: 15px; line-height: 1.6; padding-bottom: 20px;">
              Xin chào,<br><br>
              Bạn vừa gửi yêu cầu đăng nhập tài khoản. Dưới đây là mã xác thực dùng một lần (OTP) của bạn:
            </td>
          </tr>
          <tr>
            <td align="center" style="padding: 12px 0 24px 0;">
              <div style="background-color: #f1f5f9; border: 2px dashed #94a3b8; border-radius: 10px; padding: 16px 28px; display: inline-block;">
                <span style="font-family: 'Courier New', Courier, monospace; font-size: 34px; font-weight: 800; letter-spacing: 8px; color: #1e293b; margin-left: 8px;">{otp_code}</span>
              </div>
            </td>
          </tr>
          <tr>
            <td style="color: #64748b; font-size: 13px; line-height: 1.6; padding-bottom: 24px; border-bottom: 1px solid #e2e8f0;">
              ⏱️ Mã có hiệu lực trong vòng <strong>{expires_in_minutes} phút</strong>.<br>
              🔒 <strong>Cảnh báo bảo mật:</strong> Tuyệt đối không chia sẻ mã này với bất kỳ ai.
            </td>
          </tr>
          <tr>
            <td style="padding-top: 20px; color: #94a3b8; font-size: 12px; text-align: center; line-height: 1.5;">
              Nếu bạn không yêu cầu mã này, có thể ai đó đã nhập nhầm email của bạn. Bạn hãy an tâm bỏ qua email này.
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""

    try:
        await asyncio.to_thread(_send_smtp_sync, to_email, subject, html_body, text_body)
        logger.info(f"Successfully sent OTP email to {to_email}")
        return True
    except Exception as e:
        logger.error(f"Failed to send OTP email to {to_email}: {e}")
        return False
