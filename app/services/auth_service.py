"""认证服务（纯业务，不 import FastAPI）。

- 后台用户：用户名 + 密码登录
- 顾客：手机号 + 短信验证码（复用 sms/captcha/store 基础设施）
两套身份最终都签发 JWT，但角色不同、隔离规则不同。
"""
from dataclasses import dataclass

from .. import captcha, sms
from ..config import settings
from ..models import Customer, Role, StoreMember, User, now_utc
from ..security import issue_token, verify_password
from ..shortcode import gen_digits
from ..store import store
from ..utils import phone_hash, valid_phone


class AuthError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


# ---------- 后台用户登录 ----------
def staff_login(db, username: str, password: str) -> dict:
    user = db.query(User).filter_by(username=username).first()
    if not user or not verify_password(password, user.password_hash):
        raise AuthError("用户名或密码错误", 401)
    if user.status != "active":
        raise AuthError("账号已被禁用", 403)

    store_id = None
    if user.role != Role.ADMIN:
        member = db.query(StoreMember).filter_by(user_id=user.id, status="active").first()
        if not member:
            raise AuthError("账号未关联有效门店", 403)
        store_id = member.store_id

    token = issue_token(sub=str(user.id), role=user.role, store_id=store_id)
    return {"token": token, "user_id": user.id, "role": user.role,
            "store_id": store_id, "display_name": user.display_name}


# ---------- 顾客手机号验证 ----------
def send_customer_code(db, phone: str, ip: str, captcha_id: str, captcha_answer: str) -> dict:
    if not valid_phone(phone):
        raise AuthError("手机号格式不正确")
    locked = store.is_locked(phone)
    if locked:
        raise AuthError(f"操作过于频繁，请 {locked // 60 + 1} 分钟后再试", 429)
    if not captcha.verify(captcha_id, captcha_answer):
        raise AuthError("人机验证失败，请重试")

    ok, msg = store.record_send(
        phone=phone, ip=ip,
        phone_window=3600, phone_limit=settings.SEND_MAX_PER_PHONE_PER_HOUR,
        cooldown=settings.SEND_COOLDOWN_SECONDS,
        ip_window=3600, ip_limit=settings.SEND_MAX_PER_IP_PER_HOUR,
    )
    if not ok:
        raise AuthError(msg, 429)

    code = gen_digits(settings.CODE_LENGTH)
    store.save_code(phone, code, settings.CODE_TTL_SECONDS)
    try:
        sms.send_sms(phone, code)
    except sms.SmsError as e:
        # 发送失败：清掉刚存的验证码，让用户可立即重试
        store.delete_code(phone)
        raise AuthError(str(e), 502)

    resp = {"ok": True, "expires_in": settings.CODE_TTL_SECONDS}
    if settings.DEBUG:
        resp["debug_code"] = code
    return resp


def verify_customer_code(db, phone: str, code: str) -> dict:
    """校验验证码，成功则 get-or-create 顾客并签发顾客 token。"""
    if not valid_phone(phone):
        raise AuthError("手机号格式不正确")
    locked = store.is_locked(phone)
    if locked:
        raise AuthError(f"操作过于频繁，请 {locked // 60 + 1} 分钟后再试", 429)

    rec = store.get_code(phone)
    if rec is None:
        raise AuthError("验证码不存在或已过期，请重新获取")
    if code.strip() != rec.code:
        attempts = store.incr_code_attempt(phone)
        if attempts >= settings.CODE_MAX_VERIFY_ATTEMPTS:
            store.delete_code(phone)
            store.lock_phone(phone, settings.CODE_LOCK_SECONDS)
            raise AuthError("错误次数过多，账号已临时锁定 15 分钟", 429)
        raise AuthError("验证码错误")

    store.delete_code(phone)
    customer = get_or_create_customer(db, phone)
    token = issue_token(sub=str(customer.id), role=Role.CUSTOMER)
    return {"token": token, "customer_id": customer.id}


def get_or_create_customer(db, phone: str, name: str = "") -> Customer:
    h = phone_hash(phone)
    customer = db.query(Customer).filter_by(phone_hash=h).first()
    if customer:
        customer.last_seen = now_utc()
        if name and not customer.name:
            customer.name = name
        db.commit()
        return customer
    customer = Customer(name=name, phone=phone, phone_hash=h)
    db.add(customer)
    db.commit()
    return customer
