"""通知 provider（预约确认提醒等）。

测试模式打印到控制台。生产切换为短信/公众号模板消息，业务层调用不变。
"""
import logging

from .config import settings

logger = logging.getLogger("notify")


def notify_customer(phone: str, message: str) -> None:
    if settings.NOTIFY_PROVIDER == "mock":
        logger.info("【通知测试模式】发往 %s: %s", phone, message)
        return
    raise NotImplementedError(f"未实现的通知提供方: {settings.NOTIFY_PROVIDER}")
