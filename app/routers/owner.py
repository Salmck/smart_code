"""门店老板端：数据首页、活动/二维码/预约/核销/顾客/店员管理、渠道/内容效果、导出。

所有查询的 store_id 一律取自 token（user.store_id），忽略任何入参 —— 防越权。
"""
import csv
import io

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse

from ..audit import audit
from ..db import get_db
from ..deps import AuthUser, require_owner
from ..models import (
    Campaign,
    CampaignStatus,
    Customer,
    QRPlacement,
    Redemption,
    Reservation,
    ReservationStatus,
    Role,
    Store,
    StoreMember,
    User,
    now_utc,
)
from ..render import templates
from ..security import hash_password
from ..services import claim_service
from ..services.catalog_service import set_campaign_status
from ..services.stats_service import breakdown_by, funnel
from ..statemachine import RESERVATION_TRANSITIONS, transition
from ..utils import mask_phone, sanitize_text

router = APIRouter(prefix="/owner")


def _sid(user: AuthUser) -> int:
    if user.store_id is None:
        raise HTTPException(status_code=400, detail="账号未关联门店")
    return user.store_id


@router.get("")
def dashboard(request: Request, user: AuthUser = Depends(require_owner), db=Depends(get_db)):
    sid = _sid(user)
    store = db.get(Store, sid)
    f = funnel(db, {"store_id": sid})
    channels = breakdown_by(db, {"store_id": sid}, "channel")
    return templates.TemplateResponse(
        "console/owner_dashboard.html",
        {"request": request, "user": user, "store": store, "f": f, "channels": channels})


@router.get("/campaigns")
def campaigns(request: Request, user: AuthUser = Depends(require_owner), db=Depends(get_db)):
    sid = _sid(user)
    rows = db.query(Campaign).filter_by(store_id=sid).order_by(Campaign.created_at.desc()).all()
    return templates.TemplateResponse(
        "console/owner_campaigns.html", {"request": request, "user": user, "rows": rows})


@router.post("/campaigns/{campaign_id}/status")
def campaign_status(campaign_id: int, user: AuthUser = Depends(require_owner),
                    db=Depends(get_db), action: str = Form(...)):
    sid = _sid(user)
    c = db.get(Campaign, campaign_id)
    if not c or c.store_id != sid:
        raise HTTPException(status_code=404, detail="活动不存在")
    to = {"pause": CampaignStatus.PAUSED, "resume": CampaignStatus.RUNNING,
          "end": CampaignStatus.ENDED}.get(action)
    if not to:
        raise HTTPException(status_code=400, detail="非法操作")
    before = c.status
    set_campaign_status(db, c, to)
    audit(db, user_id=user.id, role=user.role, store_id=sid, action=f"campaign_{action}",
          target=f"campaign:{c.id}", before={"status": before}, after={"status": to})
    return RedirectResponse("/owner/campaigns", status_code=302)


@router.get("/qrs")
def qrs(request: Request, user: AuthUser = Depends(require_owner), db=Depends(get_db)):
    sid = _sid(user)
    qlist = db.query(QRPlacement).filter_by(store_id=sid).all()
    rows = [{"qr": q, "campaign": db.get(Campaign, q.campaign_id)} for q in qlist]
    return templates.TemplateResponse(
        "console/owner_qrs.html", {"request": request, "user": user, "rows": rows})


@router.get("/reservations")
def reservations(request: Request, user: AuthUser = Depends(require_owner), db=Depends(get_db)):
    sid = _sid(user)
    rlist = (db.query(Reservation).filter_by(store_id=sid)
             .order_by(Reservation.created_at.desc()).limit(200).all())
    rows = [{"r": r, "customer": db.get(Customer, r.customer_id)} for r in rlist]
    return templates.TemplateResponse(
        "console/owner_reservations.html", {"request": request, "user": user, "rows": rows})


@router.post("/reservations/{reservation_id}/action")
def reservation_action(reservation_id: int, user: AuthUser = Depends(require_owner),
                       db=Depends(get_db), op: str = Form(...)):
    sid = _sid(user)
    r = db.get(Reservation, reservation_id)
    if not r or r.store_id != sid:
        raise HTTPException(status_code=404, detail="预约不存在")
    before = r.status
    try:
        if op == "confirm":
            claim_service.confirm_reservation(db, r, user.id)
        elif op == "cancel":
            claim_service.cancel_reservation(db, r)
        elif op == "no_show":
            transition(r, ReservationStatus.NO_SHOW, RESERVATION_TRANSITIONS)
            db.commit()
        else:
            raise HTTPException(status_code=400, detail="非法操作")
    except claim_service.ClaimError as e:
        return JSONResponse({"detail": e.message}, status_code=e.status)
    audit(db, user_id=user.id, role=user.role, store_id=sid, action=f"reservation_{op}",
          target=f"reservation:{r.id}", before={"status": before}, after={"status": r.status})
    return RedirectResponse("/owner/reservations", status_code=302)


@router.get("/redemptions")
def redemptions(request: Request, user: AuthUser = Depends(require_owner), db=Depends(get_db)):
    sid = _sid(user)
    recs = (db.query(Redemption).filter_by(store_id=sid, is_reversal=False)
            .order_by(Redemption.created_at.desc()).limit(200).all())
    rows = []
    for rec in recs:
        staff = db.get(User, rec.staff_id)
        rows.append({"rec": rec, "staff": staff.display_name if staff else ""})
    return templates.TemplateResponse(
        "console/owner_redemptions.html", {"request": request, "user": user, "rows": rows})


@router.get("/customers")
def customers(request: Request, user: AuthUser = Depends(require_owner), db=Depends(get_db)):
    sid = _sid(user)
    # 只列出与本店有交易关系的顾客（通过核销/预约），手机号脱敏
    cids = {r.customer_id for r in db.query(Reservation.customer_id).filter_by(store_id=sid)}
    from ..models import Voucher
    cids |= {v.customer_id for v in db.query(Voucher.customer_id).filter_by(store_id=sid)}
    rows = [db.get(Customer, cid) for cid in cids if cid]
    return templates.TemplateResponse(
        "console/owner_customers.html", {"request": request, "user": user, "rows": rows})


@router.get("/channels")
def channels(request: Request, user: AuthUser = Depends(require_owner), db=Depends(get_db)):
    sid = _sid(user)
    rows = breakdown_by(db, {"store_id": sid}, "channel")
    return templates.TemplateResponse(
        "console/owner_breakdown.html",
        {"request": request, "user": user, "rows": rows, "title": "渠道效果", "col": "渠道"})


@router.get("/contents")
def contents(request: Request, user: AuthUser = Depends(require_owner), db=Depends(get_db)):
    sid = _sid(user)
    rows = breakdown_by(db, {"store_id": sid}, "content_no")
    return templates.TemplateResponse(
        "console/owner_breakdown.html",
        {"request": request, "user": user, "rows": rows, "title": "内容效果", "col": "内容编号"})


# ---------- 店员管理 ----------
@router.get("/staff")
def staff_page(request: Request, user: AuthUser = Depends(require_owner), db=Depends(get_db)):
    sid = _sid(user)
    members = db.query(StoreMember).filter_by(store_id=sid).all()
    rows = [{"m": m, "user": db.get(User, m.user_id)} for m in members]
    return templates.TemplateResponse(
        "console/owner_staff.html", {"request": request, "user": user, "rows": rows})


@router.post("/staff")
def create_staff(user: AuthUser = Depends(require_owner), db=Depends(get_db),
                 username: str = Form(...), password: str = Form(...),
                 display_name: str = Form("")):
    sid = _sid(user)
    if db.query(User).filter_by(username=username).first():
        return JSONResponse({"detail": "用户名已存在"}, status_code=400)
    u = User(username=sanitize_text(username, 64), password_hash=hash_password(password),
             display_name=sanitize_text(display_name, 64) or username, role=Role.STAFF)
    db.add(u)
    db.flush()
    db.add(StoreMember(store_id=sid, user_id=u.id, role=Role.STAFF))
    audit(db, user_id=user.id, role=user.role, store_id=sid, action="create_staff",
          target=f"user:{u.id}", after={"username": username}, commit=False)
    db.commit()
    return RedirectResponse("/owner/staff", status_code=302)


@router.post("/staff/{member_id}/toggle")
def toggle_staff(member_id: int, user: AuthUser = Depends(require_owner), db=Depends(get_db)):
    sid = _sid(user)
    m = db.get(StoreMember, member_id)
    if not m or m.store_id != sid:
        raise HTTPException(status_code=404, detail="店员不存在")
    before = m.status
    m.status = "disabled" if m.status == "active" else "active"
    audit(db, user_id=user.id, role=user.role, store_id=sid, action="toggle_staff",
          target=f"member:{m.id}", before={"status": before}, after={"status": m.status})
    return RedirectResponse("/owner/staff", status_code=302)


# ---------- 数据导出 ----------
@router.get("/export/{kind}")
def export(kind: str, user: AuthUser = Depends(require_owner), db=Depends(get_db)):
    sid = _sid(user)
    buf = io.StringIO()
    w = csv.writer(buf)
    if kind == "redemptions":
        w.writerow(["核销ID", "凭证ID", "店员ID", "金额", "核销时间"])
        for r in db.query(Redemption).filter_by(store_id=sid, is_reversal=False):
            w.writerow([r.id, r.voucher_id, r.staff_id, r.amount,
                        r.created_at.strftime("%Y-%m-%d %H:%M")])
    elif kind == "reservations":
        w.writerow(["预约ID", "姓名", "手机(脱敏)", "日期", "时间", "人数", "状态"])
        for r in db.query(Reservation).filter_by(store_id=sid):
            c = db.get(Customer, r.customer_id)
            w.writerow([r.id, r.name, mask_phone(c.phone) if c else "", r.date, r.time,
                        r.people, r.status])
    else:
        raise HTTPException(status_code=400, detail="不支持的导出类型")
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{kind}.csv"'})
