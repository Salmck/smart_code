"""动态核销码。

凭证页展示的二维码内容是一个带时间窗口的 HMAC 签名令牌，每 60 秒刷新：
    ORDX:{voucher_code}:{window}:{sig}
截图转发后旧令牌很快失效（容忍上一窗口以防时钟偏差）。

重要：动态码只降低「截图长期传播」风险，无法识别现场出示者是否本人。
真正防止重复核销的核心是数据库原子核销（见 services/redemption）。
"""
import hmac
import hashlib
import time

from .config import settings


def _sign(voucher_code: str, window: int) -> str:
    msg = f"{voucher_code}:{window}".encode()
    return hmac.new(settings.DYNCODE_SECRET.encode(), msg, hashlib.sha256).hexdigest()[:12]


def current_window() -> int:
    return int(time.time()) // settings.DYNCODE_WINDOW_SECONDS


def make_token(voucher_code: str) -> str:
    w = current_window()
    return f"ORDX:{voucher_code}:{w}:{_sign(voucher_code, w)}"


def parse_token(token: str) -> str | None:
    """校验令牌并返回 voucher_code；无效返回 None。容忍当前及上一个窗口。"""
    try:
        prefix, code, w_str, sig = token.split(":")
        if prefix != "ORDX":
            return None
        w = int(w_str)
    except (ValueError, AttributeError):
        return None
    now = current_window()
    if w not in (now, now - 1):
        return None
    if not hmac.compare_digest(sig, _sign(code, w)):
        return None
    return code
