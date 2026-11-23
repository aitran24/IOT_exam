from datetime import datetime
from typing import Optional, List, Dict, Any, Union
from pydantic import BaseModel, Field

# ----------------- COMMON / ERROR -----------------
class ErrorDetail(BaseModel):
    code: str
    message: str
    details: Optional[Dict[str, Any]] = None
    request_id: str

class ErrorResponse(BaseModel):
    error: ErrorDetail

# ----------------- CONFIG -----------------
class ConfigAuth(BaseModel):
    providers: List[str] = ["phone", "google"]
    otp_length: int = 6
    otp_ttl_s: int = 300
    resend_after_s: int = 60

class ConfigTopup(BaseModel):
    min_vnd: int = 2000
    max_vnd: int = 1000000

class ConfigPairing(BaseModel):
    code_length: int = 6
    code_ttl_s: int = 120
    idle_timeout_min: int = 15

class ConfigResponse(BaseModel):
    price_per_session_vnd: int = 2000
    max_sessions_per_pairing: int = 3
    topup: ConfigTopup = ConfigTopup()
    pairing: ConfigPairing = ConfigPairing()
    auth: ConfigAuth = ConfigAuth()
    min_app_version: str = "1.0.0"
    terms_version: str = "2026-11"
    terms_url: str = "https://booth.tenclb.vn/terms"
    privacy_url: str = "https://booth.tenclb.vn/privacy"

# ----------------- AUTH -----------------
class DeviceInfo(BaseModel):
    id: str
    platform: Optional[str] = "unknown"
    model: Optional[str] = None
    app_version: Optional[str] = "1.0.0"
    push_token: Optional[str] = None

class OTPRequestBody(BaseModel):
    email: str
    device: Optional[DeviceInfo] = None
    attestation: Optional[str] = None

class OTPRequestResponse(BaseModel):
    otp_id: str
    expires_in_s: int
    resend_after_s: int

class OTPVerifyBody(BaseModel):
    email: Optional[str] = None
    otp_id: Optional[str] = None
    code: str
    device: Optional[DeviceInfo] = None

class GoogleAuthBody(BaseModel):
    id_token: str
    nonce: Optional[str] = None
    device: Optional[DeviceInfo] = None

class UserBrief(BaseModel):
    id: str
    display_name: Optional[str] = None
    phone_masked: Optional[str] = None
    email_masked: Optional[str] = None
    terms_accepted: bool = False

class AuthResponse(BaseModel):
    token_type: str = "Bearer"
    access_token: str
    expires_in_s: int = 900
    refresh_token: str
    refresh_expires_at: str
    is_new_user: bool = False
    user: UserBrief

class RefreshBody(BaseModel):
    refresh_token: str

class RefreshResponse(BaseModel):
    token_type: str = "Bearer"
    access_token: str
    expires_in_s: int = 900
    refresh_token: str
    refresh_expires_at: str

class LogoutBody(BaseModel):
    refresh_token: Optional[str] = None

class StepUpOTPVerifyBody(BaseModel):
    otp_id: str
    code: str

class StepUpGoogleBody(BaseModel):
    id_token: str
    nonce: Optional[str] = None

class StepUpResponse(BaseModel):
    step_up_token: str
    expires_in_s: int = 300

# ----------------- USER PROFILE -----------------
class UserIdentityItem(BaseModel):
    provider: str
    linked_at: str

class UserProfileResponse(BaseModel):
    id: str
    display_name: Optional[str] = None
    phone_masked: Optional[str] = None
    email_masked: Optional[str] = None
    identities: List[UserIdentityItem] = []
    terms_accepted: bool = False
    created_at: str

class UserPatchBody(BaseModel):
    display_name: str

class TermsAcceptBody(BaseModel):
    version: str

class SessionItem(BaseModel):
    id: str
    device_name: Optional[str] = None
    platform: Optional[str] = None
    created_at: str
    last_used_at: Optional[str] = None
    current: bool = False

class PushTokenBody(BaseModel):
    token: str
    platform: str

# ----------------- WALLET & TOPUP -----------------
class WalletResponse(BaseModel):
    wallet_id: str
    wallet_code: Optional[str] = None
    balance_vnd: int
    held_vnd: int
    available_vnd: int
    price_per_session_vnd: int
    sessions_available: int
    leftover_vnd: int
    updated_at: str

class GuestWalletResponse(BaseModel):
    wallet_code: str
    balance_vnd: int
    held_vnd: int
    available_vnd: int
    sessions_available: int
    leftover_vnd: int

class TransactionItem(BaseModel):
    id: str
    type: str # topup | session | refund | adjust
    delta_vnd: int
    balance_after_vnd: int
    description: str
    ref: Optional[str] = None
    created_at: str

class TransactionsResponse(BaseModel):
    items: List[TransactionItem]
    next_cursor: Optional[str] = None

class TopupCreateBody(BaseModel):
    mode: str = Field(..., description="sessions or amount")
    sessions: Optional[int] = None
    amount_vnd: Optional[int] = None

class PaymentDetails(BaseModel):
    bank_name: str
    account_number: str
    account_name: str
    amount_vnd: int
    transfer_content: str
    qr_payload: str
    qr_image_url: Optional[str] = None

class TopupResponse(BaseModel):
    id: str
    status: str # pending | partial | paid | expired | cancelled
    amount_vnd: int
    sessions: Optional[int] = None
    expires_at: str
    payment: PaymentDetails

class TopupStatusResponse(BaseModel):
    id: str
    status: str
    amount_vnd: int
    credited_vnd: int
    remaining_vnd: int
    expires_at: str
    paid_at: Optional[str] = None
    wallet: Dict[str, Any]

# ----------------- PAIRING -----------------
class PairingCreateBody(BaseModel):
    method: str = Field(..., description="qr or code")
    token: Optional[str] = None
    code: Optional[str] = None

class MachineBrief(BaseModel):
    id: str
    name: Optional[str] = None

class PairingCreateResponse(BaseModel):
    id: str
    status: str
    machine: MachineBrief
    max_sessions: int
    price_per_session_vnd: int
    expires_at: str
    wallet: Dict[str, Any]

class PairingPlanBody(BaseModel):
    sessions: int

class PairingPlanResponse(BaseModel):
    id: str
    status: str
    planned_sessions: int
    remaining_sessions: int
    total_vnd: int
    available_vnd: int
    shortfall_vnd: int
    topup: Optional[Dict[str, Any]] = None

class PairingDetailResponse(BaseModel):
    id: str
    status: str
    planned_sessions: Optional[int] = None
    completed_sessions: int
    remaining_sessions: int
    shortfall_vnd: int
    topup_id: Optional[str] = None
    machine: MachineBrief
    expires_at: str
    updated_at: str

# ----------------- LIBRARY -----------------
class LibraryItemBrief(BaseModel):
    id: str
    created_at: str
    layout: Optional[str] = None
    filter: Optional[str] = None
    frame_color: Optional[str] = None
    color_filter: Optional[str] = None
    machine: MachineBrief
    has_video: bool
    bytes: int
    thumbnail_url: Optional[str] = None

class LibraryListResponse(BaseModel):
    items: List[LibraryItemBrief]
    next_cursor: Optional[str] = None
    usage: Dict[str, int]

class MediaFile(BaseModel):
    id: str
    kind: str # strip | shot | video
    index: Optional[int] = None
    content_type: str
    bytes: int
    width: Optional[int] = None
    height: Optional[int] = None
    duration_s: Optional[float] = None
    url: str
    url_expires_at: str

class LibraryDetailResponse(BaseModel):
    id: str
    created_at: str
    layout: Optional[str] = None
    filter: Optional[str] = None
    frame_color: Optional[str] = None
    color_filter: Optional[str] = None
    media: List[MediaFile]

class DeleteBatchBody(BaseModel):
    ids: List[str]

# ----------------- DEVICE API -----------------
class HeartbeatBody(BaseModel):
    state: str
    cpu_temp_c: Optional[float] = None
    disk_free_mb: Optional[int] = None
    camera_ok: Optional[bool] = None
    app_version: Optional[str] = None
    uptime_s: Optional[int] = None

class HeartbeatResponse(BaseModel):
    server_time: str
    mode: str
    config: Dict[str, Any]

class DeviceCommandAck(BaseModel):
    result: str = "ok"

class PairingCodeResponse(BaseModel):
    id: str
    code: str
    token: str
    qr_url: str
    expires_at: str
    ttl_s: int

class DeviceVerifyBody(BaseModel):
    wallet_code: str

class DeviceSessionCreateBody(BaseModel):
    client_session_id: str
    source: Dict[str, Any] # {"type": "pairing", "id": "pr_..."} or {"type": "wallet_code", "code": "..."} or {"type": "direct_pay", "id": "..."}
    layout: Optional[str] = "strip"
    filter: Optional[str] = "cat"

class DeviceSessionMediaItem(BaseModel):
    kind: str
    bytes: int
    sha256: str

class DeviceSessionCompleteBody(BaseModel):
    layout: Optional[str] = None
    filter: Optional[str] = None
    frame_color: Optional[str] = None
    color_filter: Optional[str] = None
    shots: Optional[int] = 4
    captured_at: Optional[str] = None
    duration_s: Optional[float] = None
    media: List[DeviceSessionMediaItem]

class DeviceSessionCancelBody(BaseModel):
    reason: str = "user_cancel"

# ----------------- SEPAY WEBHOOK -----------------
class SePayWebhookPayload(BaseModel):
    id: Union[int, str]
    gateway: Optional[str] = None
    transactionDate: Optional[str] = None
    accountNumber: Optional[str] = None
    subAccount: Optional[str] = None
    code: Optional[str] = None
    content: Optional[str] = None
    transferType: str = "in"
    description: Optional[str] = None
    transferAmount: int
    referenceCode: Optional[str] = None
    accumulated: Optional[int] = None

# ----------------- ADMIN -----------------
class AdminLoginBody(BaseModel):
    username: str
    password: str
    totp_code: str

class AdminAdjustBalanceBody(BaseModel):
    delta_vnd: int
    reason: str

class AdminMatchPaymentBody(BaseModel):
    wallet_code: Optional[str] = None
    user_id: Optional[str] = None
    note: Optional[str] = None

class AdminCreateCommandBody(BaseModel):
    device_id: str
    command_type: str # lock | unlock | restart_app | reboot | update_config
    params: Optional[Dict[str, Any]] = None

class AdminViewSessionMediaBody(BaseModel):
    reason: str

