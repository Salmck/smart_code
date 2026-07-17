"""后台用户登录（管理员 / 老板 / 店员共用账号密码登录）。"""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

from ..db import get_db
from ..deps import STAFF_COOKIE
from ..models import Role
from ..render import templates
from ..services import auth_service

router = APIRouter()


@router.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse("console/login.html", {"request": request})


@router.post("/api/staff/login")
def staff_login(db=Depends(get_db), username: str = Form(...), password: str = Form(...)):
    try:
        result = auth_service.staff_login(db, username, password)
    except auth_service.AuthError as e:
        return JSONResponse({"detail": e.message}, status_code=e.status)

    # 按角色决定落地页
    home = {
        Role.ADMIN: "/admin",
        Role.OWNER: "/owner",
        Role.MANAGER: "/owner",
        Role.STAFF: "/staff",
    }.get(result["role"], "/staff")

    resp = JSONResponse({"ok": True, "token": result["token"], "redirect": home,
                         "role": result["role"]})
    resp.set_cookie(STAFF_COOKIE, result["token"], max_age=604800, httponly=True)
    return resp


@router.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie(STAFF_COOKIE)
    return resp
