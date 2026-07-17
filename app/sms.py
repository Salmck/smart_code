"""短信发送。

测试模式（mock）不调任何外部服务，把验证码打印到控制台。
换真实服务时只需实现对应分支（阿里云 / 腾讯云 / Twilio），
接口层调用 send_sms(phone, code) 的方式不变。
"""
import logging

from .config import settings

logger = logging.getLogger("sms")


def send_sms(phone: str, code: str) -> None:
    if settings.SMS_PROVIDER == "mock":
        # 醒目地打印到控制台，方便本地联调直接看到验证码
        logger.info("=" * 40)
        logger.info("【短信测试模式】发往 %s 的验证码是: %s", phone, code)
        logger.info("=" * 40)
        return

    if settings.SMS_PROVIDER == "aliyun":
        # TODO: 接入阿里云短信 SDK（dysmsapi），需要 AccessKey、签名、模板 CODE
        raise NotImplementedError("阿里云短信未接入")

    if settings.SMS_PROVIDER == "tencent":
        # TODO: 接入腾讯云短信 SDK
        raise NotImplementedError("腾讯云短信未接入")

    if settings.SMS_PROVIDER == "twilio":
        # TODO: 接入 Twilio
        raise NotImplementedError("Twilio 未接入")

    raise NotImplementedError(f"未知的短信提供方: {settings.SMS_PROVIDER}")
