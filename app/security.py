"""安全工具：JWT 签发/校验 + 密码哈希。

密码用标准库 pbkdf2_hmac（不加依赖）。JWT 携带 role 与 store_id，供多租户隔离使用。
"""
import hashlib
import hmac
import os
import time

import jwt

from .config import settings

_PBKDF2_ROUNDS = 200_000


# ---------- 密码哈希 ----------
def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2${_PBKDF2_ROUNDS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt_hex, dk_hex = stored.split("$")
        if algo != "pbkdf2":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds)
        )
        return hmac.compare_digest(dk.hex(), dk_hex)
    except (ValueError, AttributeError):
        return False


# ---------- JWT ----------
def issue_token(*, sub: str, role: str, store_id: int | None = None,
                extra: dict | None = None) -> str:
    now = int(time.time())
    payload = {
        "sub": sub,               # 后台用户为 user_id；顾客为 customer_id
        "role": role,
        "store_id": store_id,     # 员工所属门店，用于数据隔离
        "iat": now,
        "exp": now + settings.JWT_TTL_SECONDS,
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALG)


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALG])
    except jwt.PyJWTError:
        return None
