"""Ordinex 专属增长码系统 —— FastAPI 应用入口。

模块化单体：路由层（routers/）只做 HTTP，业务在 services/，基础设施走 provider。
"""
import logging
import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db import init_db
from .routers import admin, auth, owner, public, qr, staff

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

app = FastAPI(title="Ordinex 专属增长码系统")


@app.middleware("http")
async def https_redirect(request, call_next):
    """公网 http 访问自动 301 到 https（本机开发地址除外）。

    需 uvicorn --proxy-headers（run_local 脚本已带），使 request.url.scheme
    反映隧道/反代传来的 X-Forwarded-Proto。
    """
    host = request.url.hostname or ""
    if (request.url.scheme == "http"
            and host not in ("localhost", "127.0.0.1", "0.0.0.0")):
        from fastapi.responses import RedirectResponse as _RR
        return _RR(str(request.url.replace(scheme="https")), status_code=301)
    return await call_next(request)


@app.on_event("startup")
def _startup():
    init_db()
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    # 首次启动自动灌入演示数据（幂等）
    from .seed import reconcile_demo_passwords, seed_all
    from .db import SessionLocal
    db = SessionLocal()
    try:
        seed_all(db)
        reconcile_demo_passwords(db)  # 已上线的旧库也把演示账号密码升级为「用户名-666」
    finally:
        db.close()


app.include_router(public.router)
app.include_router(auth.router)
app.include_router(staff.router)
app.include_router(owner.router)
app.include_router(admin.router)
app.include_router(qr.router)

app.mount("/static", StaticFiles(directory="static"), name="static")

# 上传文件（套餐图/参考海报）对外访问
os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=settings.UPLOAD_DIR), name="uploads")


@app.get("/", include_in_schema=False)
def root():
    """根路径无独立首页，直接跳到后台登录页。"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/login", status_code=302)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    from fastapi.responses import FileResponse
    return FileResponse("static/favicon.png", media_type="image/png")
