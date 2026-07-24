"""短信发送。

测试模式（mock）不调任何外部服务，把验证码打印到控制台。
真实服务目前实现了阿里云（aliyun），用标准库直接签名调用其 RPC 接口，
不引入官方 SDK。接口层调用 send_sms(phone, code) 的方式不变。
"""
import base64
import hashlib
import hmac
import json
import logging
import time
import urllib.parse
import urllib.request
import uuid

from .config import settings

logger = logging.getLogger("sms")


class SmsError(Exception):
    """短信发送失败（供上层转成对用户友好的提示）。"""


def send_sms(phone: str, code: str) -> None:
    if settings.SMS_PROVIDER == "mock":
        # 醒目地打印到控制台，方便本地联调直接看到验证码
        logger.info("=" * 40)
        logger.info("【短信测试模式】发往 %s 的验证码是: %s", phone, code)
        logger.info("=" * 40)
        return

    if settings.SMS_PROVIDER == "aliyun":
        _send_aliyun(phone, code)
        return

    if settings.SMS_PROVIDER == "tencent":
        raise NotImplementedError("腾讯云短信未接入")

    if settings.SMS_PROVIDER == "twilio":
        raise NotImplementedError("Twilio 未接入")

    raise NotImplementedError(f"未知的短信提供方: {settings.SMS_PROVIDER}")


# ---------------- 阿里云短信 ----------------
_ALIYUN_ENDPOINT = "https://dysmsapi.aliyuncs.com/"


def _percent_encode(s: str) -> str:
    """阿里云 RPC 签名要求的 RFC3986 编码（~ 不转义，空格转 %20）。"""
    return urllib.parse.quote(str(s), safe="~")


def _send_aliyun(phone: str, code: str) -> None:
    if not (settings.ALIYUN_SMS_ACCESS_KEY_ID and settings.ALIYUN_SMS_ACCESS_KEY_SECRET
            and settings.ALIYUN_SMS_SIGN_NAME and settings.ALIYUN_SMS_TEMPLATE_CODE):
        raise SmsError("阿里云短信未配置完整（AccessKey / 签名 / 模板）")

    params = {
        "AccessKeyId": settings.ALIYUN_SMS_ACCESS_KEY_ID,
        "Action": "SendSms",
        "Format": "JSON",
        "PhoneNumbers": phone,
        "RegionId": settings.ALIYUN_SMS_REGION,
        "SignName": settings.ALIYUN_SMS_SIGN_NAME,
        "SignatureMethod": "HMAC-SHA1",
        "SignatureNonce": uuid.uuid4().hex,
        "SignatureVersion": "1.0",
        "TemplateCode": settings.ALIYUN_SMS_TEMPLATE_CODE,
        "TemplateParam": json.dumps(
            {settings.ALIYUN_SMS_TEMPLATE_PARAM: code}, ensure_ascii=False, separators=(",", ":")),
        "Timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "Version": "2017-05-25",
    }

    # 1) 按 key 排序后拼规范化查询串
    canonical = "&".join(
        f"{_percent_encode(k)}={_percent_encode(v)}" for k, v in sorted(params.items()))
    # 2) 构造待签名串
    string_to_sign = "GET&" + _percent_encode("/") + "&" + _percent_encode(canonical)
    # 3) HMAC-SHA1，密钥为 AccessKeySecret + "&"
    key = (settings.ALIYUN_SMS_ACCESS_KEY_SECRET + "&").encode()
    signature = base64.b64encode(
        hmac.new(key, string_to_sign.encode(), hashlib.sha1).digest()).decode()
    params["Signature"] = signature

    # 4) 用同一套编码拼最终请求串，保证与签名一致
    query = "&".join(f"{_percent_encode(k)}={_percent_encode(v)}" for k, v in params.items())
    url = _ALIYUN_ENDPOINT + "?" + query

    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            body = json.loads(resp.read().decode())
    except Exception as e:  # 网络/超时/解析异常
        logger.warning("阿里云短信请求异常 phone=%s err=%s", phone, e)
        raise SmsError("短信服务暂时不可用，请稍后再试") from e

    if body.get("Code") != "OK":
        # 常见：isv.MOBILE_NUMBER_ILLEGAL / isv.BUSINESS_LIMIT_CONTROL(频控) 等
        logger.warning("阿里云短信失败 phone=%s code=%s msg=%s",
                       phone, body.get("Code"), body.get("Message"))
        raise SmsError(f"短信发送失败：{body.get('Message', '未知错误')}")

    logger.info("阿里云短信已发送 phone=%s bizId=%s", phone, body.get("BizId"))
