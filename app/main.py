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


@app.on_event("startup")
def _startup():
    init_db()
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    # 首次启动自动灌入演示数据（幂等）
    from .seed import seed_all
    from .db import SessionLocal
    db = SessionLocal()
    try:
        seed_all(db)
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


@app.get("/healthz")
def healthz():
    return {"ok": True}
