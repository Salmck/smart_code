"""内存存储：验证码、人机验证题目、频率限制、错误锁定。

演示用内存字典即可跑通全流程。生产环境应替换为 Redis（天然过期 + 多实例共享），
接口层无需改动。
"""
import threading
import time
from dataclasses import dataclass, field


@dataclass
class CodeRecord:
    code: str
    expires_at: float
    attempts: int = 0


@dataclass
class CaptchaRecord:
    answer: str
    expires_at: float


@dataclass
class RateRecord:
    timestamps: list = field(default_factory=list)
    last_sent_at: float = 0.0
    locked_until: float = 0.0


class MemoryStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._codes: dict[str, CodeRecord] = {}
        self._captchas: dict[str, CaptchaRecord] = {}
        self._phone_rate: dict[str, RateRecord] = {}
        self._ip_rate: dict[str, RateRecord] = {}

    # ---------- 验证码 ----------
    def save_code(self, phone: str, code: str, ttl: int):
        with self._lock:
            self._codes[phone] = CodeRecord(code=code, expires_at=time.time() + ttl)

    def get_code(self, phone: str) -> CodeRecord | None:
        with self._lock:
            rec = self._codes.get(phone)
            if rec and rec.expires_at < time.time():
                self._codes.pop(phone, None)
                return None
            return rec

    def incr_code_attempt(self, phone: str) -> int:
        with self._lock:
            rec = self._codes.get(phone)
            if rec:
                rec.attempts += 1
                return rec.attempts
            return 0

    def delete_code(self, phone: str):
        with self._lock:
            self._codes.pop(phone, None)

    # ---------- 错误锁定 ----------
    def is_locked(self, phone: str) -> int:
        """返回剩余锁定秒数，0 表示未锁定。"""
        with self._lock:
            rec = self._phone_rate.get(phone)
            if rec and rec.locked_until > time.time():
                return int(rec.locked_until - time.time())
            return 0

    def lock_phone(self, phone: str, seconds: int):
        with self._lock:
            rec = self._phone_rate.setdefault(phone, RateRecord())
            rec.locked_until = time.time() + seconds

    # ---------- 人机验证 ----------
    def save_captcha(self, captcha_id: str, answer: str, ttl: int):
        with self._lock:
            self._captchas[captcha_id] = CaptchaRecord(
                answer=answer, expires_at=time.time() + ttl
            )

    def pop_captcha(self, captcha_id: str) -> CaptchaRecord | None:
        with self._lock:
            rec = self._captchas.pop(captcha_id, None)
            if rec and rec.expires_at < time.time():
                return None
            return rec

    # ---------- 频率限制 ----------
    def _check(self, table, key, window, limit, cooldown=0):
        now = time.time()
        rec = table.setdefault(key, RateRecord())
        if cooldown and now - rec.last_sent_at < cooldown:
            return False, f"发送过于频繁，请 {int(cooldown - (now - rec.last_sent_at))} 秒后再试"
        rec.timestamps = [t for t in rec.timestamps if now - t < window]
        if len(rec.timestamps) >= limit:
            return False, "已达发送次数上限，请稍后再试"
        return True, ""

    def record_send(self, phone, ip, phone_window, phone_limit, cooldown,
                    ip_window, ip_limit) -> tuple[bool, str]:
        with self._lock:
            ok, msg = self._check(self._phone_rate, phone, phone_window, phone_limit, cooldown)
            if not ok:
                return False, msg
            ok, msg = self._check(self._ip_rate, ip, ip_window, ip_limit)
            if not ok:
                return False, msg
            now = time.time()
            self._phone_rate[phone].timestamps.append(now)
            self._phone_rate[phone].last_sent_at = now
            self._ip_rate[ip].timestamps.append(now)
            return True, ""


store = MemoryStore()
