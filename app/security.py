"""登录凭证：签发 / 校验 JWT。"""
import time

import jwt

from .config import settings


def issue_token(phone: str) -> str:
    now = int(time.time())
    payload = {
        "sub": phone,                              # 登录主体：手机号
        "iat": now,
        "exp": now + settings.JWT_TTL_SECONDS,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALG)


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALG])
    except jwt.PyJWTError:
        return None
