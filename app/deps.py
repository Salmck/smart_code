"""认证与授权依赖 —— 多租户数据隔离的执行点。

双通道认证：优先读 Authorization: Bearer（小程序 wx.request），回退读 Cookie（H5）。
这样同一套 service 既服务 SSR 页面又服务未来的小程序 JSON API。

数据隔离铁律：老板/店员的 store_id 一律取自 token（AuthUser.store_id），
绝不取 URL/表单参数 —— 从根上杜绝「改参数看别人门店」。
"""
from dataclasses import dataclass

from fastapi import Cookie, Depends, Header, HTTPException, Request

from .db import get_db
from .models import Role, StoreMember, User
from .security import decode_token

# Cookie 名
CUSTOMER_COOKIE = "ordinex_customer"
STAFF_COOKIE = "ordinex_staff"


@dataclass
class AuthUser:
    id: int
    role: str
    store_id: int | None


def _token_from_request(authorization: str, cookie_val: str | None) -> str | None:
    if authorization and authorization.startswith("Bearer "):
        return authorization[7:]
    return cookie_val


# ---------- 后台用户（admin/owner/manager/staff）----------
def current_staff(
    authorization: str = Header(default=""),
    ordinex_staff: str | None = Cookie(default=None),
    db=Depends(get_db),
) -> AuthUser:
    token = _token_from_request(authorization, ordinex_staff)
    payload = decode_token(token) if token else None
    if not payload or payload.get("role") == Role.CUSTOMER:
        raise HTTPException(status_code=401, detail="未登录")
    user = db.get(User, int(payload["sub"]))
    if not user or user.status != "active":
        raise HTTPException(status_code=401, detail="账号不可用")
    # 员工的门店成员关系需有效（离职即时失效）
    store_id = payload.get("store_id")
    if user.role != Role.ADMIN:
        member = (
            db.query(StoreMember)
            .filter_by(user_id=user.id, store_id=store_id, status="active")
            .first()
        )
        if not member:
            raise HTTPException(status_code=401, detail="门店成员关系已失效")
    return AuthUser(id=user.id, role=user.role, store_id=store_id)


def require_admin(user: AuthUser = Depends(current_staff)) -> AuthUser:
    if user.role != Role.ADMIN:
        raise HTTPException(status_code=403, detail="需要平台管理员权限")
    return user


def require_owner(user: AuthUser = Depends(current_staff)) -> AuthUser:
    """老板或店长（可管理本店）。"""
    if user.role not in (Role.ADMIN, Role.OWNER, Role.MANAGER):
        raise HTTPException(status_code=403, detail="需要门店管理权限")
    return user


def require_staff(user: AuthUser = Depends(current_staff)) -> AuthUser:
    """任意后台角色（店员及以上），用于核销端。"""
    return user


def assert_store_access(user: AuthUser, store_id: int):
    """校验用户可访问指定门店：管理员可访问所有，其余只能访问自己的门店。"""
    if user.role == Role.ADMIN:
        return
    if user.store_id != store_id:
        raise HTTPException(status_code=403, detail="无权访问该门店数据")


def scoped_store_id(user: AuthUser, requested: int | None = None) -> int:
    """返回用户实际可用的 store_id：非管理员强制用 token 里的门店，忽略入参。"""
    if user.role == Role.ADMIN:
        if requested is None:
            raise HTTPException(status_code=400, detail="缺少门店参数")
        return requested
    return user.store_id


# ---------- 顾客 ----------
def current_customer(
    authorization: str = Header(default=""),
    ordinex_customer: str | None = Cookie(default=None),
) -> int | None:
    """返回 customer_id，未登录返回 None（顾客端多数页面不强制登录）。"""
    token = _token_from_request(authorization, ordinex_customer)
    payload = decode_token(token) if token else None
    if not payload or payload.get("role") != Role.CUSTOMER:
        return None
    return int(payload["sub"])


def require_customer(customer_id: int | None = Depends(current_customer)) -> int:
    if customer_id is None:
        raise HTTPException(status_code=401, detail="请先验证手机号")
    return customer_id


def client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
