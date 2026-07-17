"""通用小工具：手机号哈希/脱敏、时间格式化。"""
import hashlib
import re

from .config import settings

PHONE_RE = re.compile(r"^1[3-9]\d{9}$")


def valid_phone(phone: str) -> bool:
    return bool(PHONE_RE.match(phone or ""))


def phone_hash(phone: str) -> str:
    """手机号唯一识别值（加盐哈希）。生产可换成更强的密钥派生。"""
    return hashlib.sha256((settings.JWT_SECRET + phone).encode()).hexdigest()


def mask_phone(phone: str) -> str:
    """脱敏展示：138****8000。"""
    if not phone or len(phone) != 11:
        return phone or ""
    return f"{phone[:3]}****{phone[7:]}"


def sanitize_text(s: str, max_len: int = 500) -> str:
    """基础输入清洗：去除控制字符、截断，防止恶意内容。"""
    if not s:
        return ""
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", s)
    return s.strip()[:max_len]
