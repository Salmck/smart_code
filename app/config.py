"""应用配置。

所有可调参数集中在这里，方便从测试模式切换到真实短信/验证码服务时只改这一处。
生产环境请通过环境变量注入，尤其是 JWT_SECRET。
"""
import os


class Settings:
    # ---- 运行模式 ----
    # DEBUG=True 时：验证码会打印到控制台，并在接口响应里回显，方便本地联调。
    # 上线务必设为 False，否则任何人都能从响应里直接读到验证码。
    DEBUG: bool = os.getenv("DEBUG", "true").lower() == "true"

    # ---- 短信 / 人机验证的提供方 ----
    # "mock"  -> 测试模式，不依赖任何外部服务
    # "aliyun"/"tencent"/"twilio" -> 预留，接真实服务时在 sms.py / captcha.py 里实现
    SMS_PROVIDER: str = os.getenv("SMS_PROVIDER", "mock")
    CAPTCHA_PROVIDER: str = os.getenv("CAPTCHA_PROVIDER", "mock")

    # ---- 验证码规则 ----
    CODE_LENGTH: int = 6
    CODE_TTL_SECONDS: int = 300          # 验证码有效期 5 分钟
    CODE_MAX_VERIFY_ATTEMPTS: int = 5    # 同一验证码最多尝试校验次数，防暴力

    # ---- 防刷 / 频率限制 ----
    SEND_COOLDOWN_SECONDS: int = 60      # 同一手机号两次发送最短间隔
    SEND_MAX_PER_PHONE_PER_DAY: int = 10 # 同一手机号每日发送上限
    SEND_MAX_PER_IP_PER_HOUR: int = 20   # 同一 IP 每小时发送上限

    # ---- 人机验证（测试模式为算术题）----
    CAPTCHA_TTL_SECONDS: int = 120       # 验证码题目有效期

    # ---- 登录凭证 JWT ----
    JWT_SECRET: str = os.getenv("JWT_SECRET", "dev-secret-change-me-in-production")
    JWT_ALG: str = "HS256"
    JWT_TTL_SECONDS: int = 7 * 24 * 3600  # 登录态 7 天


settings = Settings()
