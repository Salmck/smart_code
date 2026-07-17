"""人机验证。

测试模式（mock）用一道简单算术题当作人机挑战——完全离线、可跑通全流程，
又能真实拦住"直接调发送接口"的裸机器人（必须先取题、答对才能发码）。

接真实服务时（阿里云验证码 / 腾讯天御 / Cloudflare Turnstile / reCAPTCHA）：
前端拿到的是各家 SDK 生成的 token，这里改成调对应服务端校验接口即可，
send-code 接口的调用方式不变。
"""
import random
import uuid

from .config import settings
from .store import store


def issue_challenge() -> dict:
    """生成一道人机验证挑战，返回给前端展示。"""
    if settings.CAPTCHA_PROVIDER == "mock":
        a, b = random.randint(1, 9), random.randint(1, 9)
        captcha_id = uuid.uuid4().hex
        store.save_captcha(captcha_id, str(a + b), settings.CAPTCHA_TTL_SECONDS)
        return {"captcha_id": captcha_id, "question": f"{a} + {b} = ?"}

    # 真实服务分支：通常前端直接向验证码厂商取 token，无需后端下发题目
    raise NotImplementedError(f"未实现的验证码提供方: {settings.CAPTCHA_PROVIDER}")


def verify(captcha_id: str, answer: str) -> bool:
    """校验人机验证是否通过。务必在后端做，前端结果不可信。"""
    if settings.CAPTCHA_PROVIDER == "mock":
        rec = store.pop_captcha(captcha_id)  # 一次性使用
        if rec is None:
            return False
        return answer.strip() == rec.answer

    raise NotImplementedError(f"未实现的验证码提供方: {settings.CAPTCHA_PROVIDER}")
