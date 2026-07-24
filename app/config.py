"""应用配置。

所有可调参数集中在这里，切换测试模式↔真实服务、SQLite↔PostgreSQL 时只改这一处。
生产环境请通过环境变量注入，尤其是 JWT_SECRET / DYNCODE_SECRET。
"""
import os


class Settings:
    # ---- 运行模式 ----
    # DEBUG=True 时：验证码打印到控制台并在接口响应里回显，方便本地联调。
    # 上线务必设为 False。
    DEBUG: bool = os.getenv("DEBUG", "true").lower() == "true"

    # ---- 数据库 ----
    # 默认 SQLite（零配置）。换 PostgreSQL 只需设 DATABASE_URL=postgresql+psycopg://...
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./ordinex.db")

    # ---- 基础设施 provider（按需切换实现，业务层无感）----
    SMS_PROVIDER: str = os.getenv("SMS_PROVIDER", "mock")          # mock/aliyun/tencent/twilio
    CAPTCHA_PROVIDER: str = os.getenv("CAPTCHA_PROVIDER", "mock")  # mock/aliyun/tencent/turnstile
    STORAGE_PROVIDER: str = os.getenv("STORAGE_PROVIDER", "local") # local/oss/cos
    NOTIFY_PROVIDER: str = os.getenv("NOTIFY_PROVIDER", "mock")    # mock/sms/wechat

    # ---- 阿里云短信（SMS_PROVIDER=aliyun 时生效）----
    # 控制台申请：短信签名 SignName、验证码模板 TemplateCode、AccessKey。
    ALIYUN_SMS_ACCESS_KEY_ID: str = os.getenv("ALIYUN_SMS_ACCESS_KEY_ID", "")
    ALIYUN_SMS_ACCESS_KEY_SECRET: str = os.getenv("ALIYUN_SMS_ACCESS_KEY_SECRET", "")
    ALIYUN_SMS_SIGN_NAME: str = os.getenv("ALIYUN_SMS_SIGN_NAME", "")       # 如「无界序」
    ALIYUN_SMS_TEMPLATE_CODE: str = os.getenv("ALIYUN_SMS_TEMPLATE_CODE", "")  # 如 SMS_123456789
    # 模板里的变量名：模板内容「您的验证码是${code}」→ 填 code
    ALIYUN_SMS_TEMPLATE_PARAM: str = os.getenv("ALIYUN_SMS_TEMPLATE_PARAM", "code")
    ALIYUN_SMS_REGION: str = os.getenv("ALIYUN_SMS_REGION", "cn-hangzhou")

    # ---- 验证码规则（PRD 流程六）----
    CODE_LENGTH: int = 6
    CODE_TTL_SECONDS: int = 300              # 验证码有效期 5 分钟
    CODE_MAX_VERIFY_ATTEMPTS: int = 5        # 连续输错 5 次锁定
    CODE_LOCK_SECONDS: int = 15 * 60         # 锁定 15 分钟
    SEND_COOLDOWN_SECONDS: int = 60          # 同手机号两次发送最短间隔
    SEND_MAX_PER_PHONE_PER_HOUR: int = 5     # 同手机号每小时发送上限
    SEND_MAX_PER_IP_PER_HOUR: int = 20       # 同 IP 每小时发送上限

    # ---- 人机验证 ----
    CAPTCHA_TTL_SECONDS: int = 120

    # ---- 凭证 / 核销 ----
    VOUCHER_CODE_LENGTH: int = 8             # 对外随机编号长度（base62）
    BACKUP_CODE_LENGTH: int = 8              # 备用数字核销码长度
    DYNCODE_WINDOW_SECONDS: int = 60         # 动态核销码刷新窗口
    DYNCODE_SECRET: str = os.getenv("DYNCODE_SECRET", "dev-dyncode-secret-change-me")

    # ---- 短码 ----
    SHORTCODE_LENGTH: int = 8                # 二维码随机短码长度

    # ---- 对外访问地址 ----
    # 设为公网地址（如 cloudflared 隧道 https://xxx.trycloudflare.com）后，
    # 生成的二维码会指向此地址而非 localhost，手机扫码才能打开。留空则用请求 Host。
    PUBLIC_BASE_URL: str = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")

    # ---- 登录凭证 JWT ----
    JWT_SECRET: str = os.getenv("JWT_SECRET", "dev-secret-change-me-in-production")
    JWT_ALG: str = "HS256"
    JWT_TTL_SECONDS: int = 7 * 24 * 3600     # 登录态 7 天

    # ---- 文件存储 ----
    UPLOAD_DIR: str = os.getenv("UPLOAD_DIR", "./data/uploads")


settings = Settings()
