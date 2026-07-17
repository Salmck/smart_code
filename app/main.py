"""手机号验证码登录 —— FastAPI 后端。

登录流程：
1) GET  /api/captcha       前端取一道人机验证挑战
2) POST /api/send-code     校验人机验证 + 频率限制 -> 生成验证码 -> 发短信
3) POST /api/login         校验验证码 -> 签发 JWT 登录凭证
4) GET  /api/me            用 Bearer Token 访问受保护资源，演示登录态
"""
import logging
import re
import secrets

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import captcha, sms
from .config import settings
from .security import decode_token, issue_token
from .store import store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="手机号验证码登录 Demo")

# 中国大陆手机号：1 开头，第二位 3-9，共 11 位。按需换成你自己的号段/国际号规则。
PHONE_RE = re.compile(r"^1[3-9]\d{9}$")


# ---------- 请求体模型 ----------
class SendCodeReq(BaseModel):
    phone: str = Field(..., description="手机号")
    captcha_id: str
    captcha_answer: str


class LoginReq(BaseModel):
    phone: str
    code: str


# ---------- 工具 ----------
def client_ip(request: Request) -> str:
    # 生产环境若在反向代理后，应信任 X-Forwarded-For 的最左 IP
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def generate_code() -> str:
    # 用 secrets 保证不可预测；补零到固定长度
    upper = 10 ** settings.CODE_LENGTH
    return str(secrets.randbelow(upper)).zfill(settings.CODE_LENGTH)


# ---------- 接口 ----------
@app.get("/api/captcha")
def get_captcha():
    """下发一道人机验证挑战。"""
    return captcha.issue_challenge()


@app.post("/api/send-code")
def send_code(req: SendCodeReq, request: Request):
    # 1. 手机号格式校验
    if not PHONE_RE.match(req.phone):
        raise HTTPException(status_code=400, detail="手机号格式不正确")

    # 2. 人机验证（后端校验，前端结果不可信）
    if not captcha.verify(req.captcha_id, req.captcha_answer):
        raise HTTPException(status_code=400, detail="人机验证失败，请重试")

    # 3. 频率限制（防短信轰炸 / 防刷，非常重要，否则真实短信会被刷爆花钱）
    ok, msg = store.record_send(
        phone=req.phone,
        ip=client_ip(request),
        phone_window=86400, phone_limit=settings.SEND_MAX_PER_PHONE_PER_DAY,
        cooldown=settings.SEND_COOLDOWN_SECONDS,
        ip_window=3600, ip_limit=settings.SEND_MAX_PER_IP_PER_HOUR,
    )
    if not ok:
        raise HTTPException(status_code=429, detail=msg)

    # 4. 生成验证码 -> 存储 -> 发送
    code = generate_code()
    store.save_code(req.phone, code, settings.CODE_TTL_SECONDS)
    sms.send_sms(req.phone, code)

    resp = {"ok": True, "expires_in": settings.CODE_TTL_SECONDS}
    # DEBUG 模式下回显验证码，纯为本地联调方便；生产环境 DEBUG=False 时不会返回
    if settings.DEBUG:
        resp["debug_code"] = code
    return resp


@app.post("/api/login")
def login(req: LoginReq):
    rec = store.get_code(req.phone)
    if rec is None:
        raise HTTPException(status_code=400, detail="验证码不存在或已过期，请重新获取")

    # 限制单个验证码的尝试次数，防暴力猜码
    if rec.attempts >= settings.CODE_MAX_VERIFY_ATTEMPTS:
        store.delete_code(req.phone)
        raise HTTPException(status_code=429, detail="尝试次数过多，请重新获取验证码")

    if req.code.strip() != rec.code:
        store.incr_code_attempt(req.phone)
        raise HTTPException(status_code=400, detail="验证码错误")

    # 校验通过：验证码一次性作废，签发登录凭证
    store.delete_code(req.phone)
    token = issue_token(req.phone)
    return {"ok": True, "token": token, "phone": req.phone}


def current_user(authorization: str = Header(default="")) -> str:
    """依赖：从 Authorization: Bearer <token> 解析当前登录手机号。"""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    payload = decode_token(authorization[7:])
    if not payload:
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录")
    return payload["sub"]


@app.get("/api/me")
def me(phone: str = Depends(current_user)):
    """受保护接口，演示登录态。"""
    return {"phone": phone, "message": "已登录，这是受保护的资源"}


# ---------- 前端静态页 ----------
@app.get("/")
def index():
    return FileResponse("static/index.html")


app.mount("/static", StaticFiles(directory="static"), name="static")
