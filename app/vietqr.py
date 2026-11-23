import io
import base64
from typing import Optional
import qrcode
from app.config import settings

def crc16_ccitt(data: str) -> str:
    """Calculate CRC-16/CCITT-FALSE checksum for EMVCo QR."""
    crc = 0xFFFF
    for ch in data.encode("ascii"):
        crc ^= (ch << 8)
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return f"{crc:04X}"

def tlv(tag: str, value: str) -> str:
    """Format Tag-Length-Value."""
    val_bytes = value.encode("utf-8")
    length = f"{len(val_bytes):02d}"
    return f"{tag}{length}{value}"

def build_vietqr_payload(
    bank_bin: Optional[str] = None,
    account_number: Optional[str] = None,
    amount_vnd: Optional[int] = None,
    transfer_content: Optional[str] = None
) -> str:
    """
    Construct dynamic VietQR string compliant with NAPAS / EMVCo standard.
    """
    bank_bin = bank_bin or settings.BANK_BIN
    account_number = account_number or settings.ACCOUNT_NUMBER
    
    # Tag 38: Beneficiary info
    # Subtag 00: GUID "A000000727"
    # Subtag 01: 00 (bank_bin) + 01 (account_number)
    # Subtag 02: "QRIBFTTA"
    sub_00 = tlv("00", "A000000727")
    sub_bank = tlv("00", bank_bin) + tlv("01", account_number)
    sub_01 = tlv("01", sub_bank)
    sub_02 = tlv("02", "QRIBFTTA")
    tag_38 = tlv("38", sub_00 + sub_01 + sub_02)
    
    payload = ""
    payload += tlv("00", "01") # Payload Format Indicator
    payload += tlv("01", "12") # Dynamic QR (12)
    payload += tag_38
    payload += tlv("53", "704") # VND Currency
    
    if amount_vnd and amount_vnd > 0:
        payload += tlv("54", str(amount_vnd))
        
    payload += tlv("58", "VN") # Country Code
    
    if transfer_content:
        # Tag 62: Additional Data Field -> Subtag 08: Purpose of transaction
        tag_62_sub08 = tlv("08", transfer_content)
        payload += tlv("62", tag_62_sub08)
        
    # Tag 63: CRC calculation
    raw_for_crc = payload + "6304"
    crc = crc16_ccitt(raw_for_crc)
    full_payload = raw_for_crc + crc
    return full_payload

def generate_qr_base64(payload: str) -> str:
    """Generate base64 encoded PNG for QR code."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("utf-8")

def generate_qr_data_url(payload: str) -> str:
    return f"data:image/png;base64,{generate_qr_base64(payload)}"
