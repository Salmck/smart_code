"""内存存储：验证码、人机验证题目、频率限制计数。

演示用内存字典即可跑通全流程。生产环境应替换为 Redis：
- 天然支持过期（SETEX），省去手动清理
- 多实例部署时共享状态
替换时只需把这里的读写换成 Redis 命令，接口层无需改动。
"""
import threading
import time
from dataclasses import dataclass, field


@dataclass
class CodeRecord:
    code: str
    expires_at: float
    attempts: int = 0          # 已尝试校验次数


@dataclass
class CaptchaRecord:
    answer: str
    expires_at: float


@dataclass
class RateRecord:
    # 时间戳列表，用于滑动窗口统计
    timestamps: list = field(default_factory=list)
    last_sent_at: float = 0.0


class MemoryStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._codes: dict[str, CodeRecord] = {}          # phone -> CodeRecord
        self._captchas: dict[str, CaptchaRecord] = {}    # captcha_id -> CaptchaRecord
        self._phone_rate: dict[str, RateRecord] = {}     # phone -> RateRecord
        self._ip_rate: dict[str, RateRecord] = {}        # ip -> RateRecord

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

    def incr_code_attempt(self, phone: str):
        with self._lock:
            rec = self._codes.get(phone)
            if rec:
                rec.attempts += 1

    def delete_code(self, phone: str):
        with self._lock:
            self._codes.pop(phone, None)

    # ---------- 人机验证 ----------
    def save_captcha(self, captcha_id: str, answer: str, ttl: int):
        with self._lock:
            self._captchas[captcha_id] = CaptchaRecord(
                answer=answer, expires_at=time.time() + ttl
            )

    def pop_captcha(self, captcha_id: str) -> CaptchaRecord | None:
        """取出并删除（一次性使用，防止一个题目多次通过）。"""
        with self._lock:
            rec = self._captchas.pop(captcha_id, None)
            if rec and rec.expires_at < time.time():
                return None
            return rec

    # ---------- 频率限制 ----------
    def _check_and_record(self, table: dict, key: str, window: int, limit: int,
                          cooldown: int = 0) -> tuple[bool, str]:
        now = time.time()
        rec = table.setdefault(key, RateRecord())
        # 冷却：两次发送最短间隔
        if cooldown and now - rec.last_sent_at < cooldown:
            wait = int(cooldown - (now - rec.last_sent_at))
            return False, f"发送过于频繁，请 {wait} 秒后再试"
        # 滑动窗口清理
        rec.timestamps = [t for t in rec.timestamps if now - t < window]
        if len(rec.timestamps) >= limit:
            return False, "已达发送次数上限，请稍后再试"
        return True, ""

    def record_send(self, phone: str, ip: str,
                    phone_window: int, phone_limit: int, cooldown: int,
                    ip_window: int, ip_limit: int) -> tuple[bool, str]:
        with self._lock:
            ok, msg = self._check_and_record(
                self._phone_rate, phone, phone_window, phone_limit, cooldown
            )
            if not ok:
                return False, msg
            ok, msg = self._check_and_record(
                self._ip_rate, ip, ip_window, ip_limit
            )
            if not ok:
                return False, msg
            # 两项都通过，落库计数
            now = time.time()
            self._phone_rate[phone].timestamps.append(now)
            self._phone_rate[phone].last_sent_at = now
            self._ip_rate[ip].timestamps.append(now)
            return True, ""


store = MemoryStore()
