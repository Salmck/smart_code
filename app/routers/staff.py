"""店员端：扫码核销、备用码核销、今日预约、本人核销记录。"""
import uuid

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse

from ..db import get_db
from ..deps import AuthUser, require_staff
from ..models import (
    Campaign,
    Customer,
    Redemption,
    Reservation,
    ReservationStatus,
    Voucher,
    now_utc,
)
from ..render import templates
from ..services import redemption_service

router = APIRouter(prefix="/staff")


@router.get("")
def staff_home(request: Request, user: AuthUser = Depends(require_staff)):
    return templates.TemplateResponse("console/staff_scan.html",
                                      {"request": request, "user": user})


@router.post("/api/verify")
def verify(user: AuthUser = Depends(require_staff), db=Depends(get_db),
           raw_code: str = Form(""), backup_code: str = Form(""),
           phone_last4: str = Form("")):
    """第一步查询：扫码令牌 / 对外编号 / 备用数字码。"""
    code = raw_code or backup_code
    try:
        info = redemption_service.verify_voucher(
            db, raw_code=code, staff_store_id=user.store_id,
            staff_role=user.role, backup_phone_last4=phone_last4)
    except redemption_service.RedeemError as e:
        return JSONResponse({"detail": e.message, "code": e.code}, status_code=e.status)
    return info


@router.post("/api/redeem")
def redeem(user: AuthUser = Depends(require_staff), db=Depends(get_db),
           voucher_id: int = Form(...), request_id: str = Form("")):
    """第二步确认核销（原子 + 幂等）。"""
    rid = request_id or uuid.uuid4().hex
    try:
        result = redemption_service.redeem(
            db, voucher_id=int(voucher_id), staff_id=user.id,
            staff_store_id=user.store_id, staff_role=user.role, request_id=rid)
    except redemption_service.RedeemError as e:
        return JSONResponse({"detail": e.message, "code": e.code}, status_code=e.status)
    return result


@router.get("/reservations")
def today_reservations(request: Request, user: AuthUser = Depends(require_staff),
                       db=Depends(get_db)):
    today = now_utc().strftime("%Y-%m-%d")
    q = db.query(Reservation).filter(Reservation.date == today)
    if user.store_id:
        q = q.filter(Reservation.store_id == user.store_id)
    reservations = q.order_by(Reservation.time).all()
    rows = [{"r": r, "customer": db.get(Customer, r.customer_id)} for r in reservations]
    return templates.TemplateResponse(
        "console/staff_reservations.html",
        {"request": request, "rows": rows, "today": today, "user": user})


@router.get("/records")
def my_records(request: Request, user: AuthUser = Depends(require_staff), db=Depends(get_db)):
    records = (db.query(Redemption).filter_by(staff_id=user.id, is_reversal=False)
               .order_by(Redemption.created_at.desc()).limit(100).all())
    rows = []
    for rec in records:
        v = db.get(Voucher, rec.voucher_id)
        c = db.get(Campaign, v.campaign_id) if v else None
        rows.append({"rec": rec, "voucher": v, "title": c.package_title if c else ""})
    return templates.TemplateResponse(
        "console/staff_records.html", {"request": request, "rows": rows, "user": user})
