"""门店老板端：数据首页、活动/二维码/预约/核销/顾客/店员管理、渠道/内容效果、导出。

所有查询的 store_id 一律取自 token（user.store_id），忽略任何入参 —— 防越权。
"""
import csv
import io

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse

from ..audit import audit
from ..db import get_db
from ..deps import AuthUser, require_owner
from ..models import (
    CAMPAIGN_STATUS_LABEL,
    Campaign,
    CampaignStatus,
    Customer,
    GrowthAction,
    Package,
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
from ..services import catalog_service, claim_service
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


# ---------- 套餐管理（选增长策略，显示策略详情辅助编辑）----------
@router.get("/packages")
def packages(request: Request, user: AuthUser = Depends(require_owner), db=Depends(get_db)):
    sid = _sid(user)
    plist = db.query(Package).filter_by(store_id=sid).order_by(Package.created_at.desc()).all()
    # 下发到本店的增长策略（管理员创建）
    actions = db.query(GrowthAction).filter_by(store_id=sid).all()
    rows = [{"p": p, "action": db.get(GrowthAction, p.growth_action_id)} for p in plist]
    return templates.TemplateResponse(
        "console/owner_packages.html",
        {"request": request, "user": user, "rows": rows, "actions": actions})


@router.get("/packages/strategy/{action_id}")
def strategy_detail(action_id: int, user: AuthUser = Depends(require_owner), db=Depends(get_db)):
    """返回增长策略详情 JSON，供套餐编辑页展示辅助信息。"""
    sid = _sid(user)
    a = db.get(GrowthAction, action_id)
    if not a or a.store_id != sid:
        raise HTTPException(status_code=404, detail="策略不存在")
    return {
        "name": a.name, "hypothesis": a.hypothesis, "target_customer": a.target_customer,
        "target_scene": a.target_scene, "problem_type": a.problem_type,
        "success_metric": a.success_metric, "success_threshold": a.success_threshold,
        "min_sample": a.min_sample, "observe_days": a.observe_days,
    }


@router.post("/packages")
async def create_package(user: AuthUser = Depends(require_owner), db=Depends(get_db),
                         growth_action_id: int = Form(...), package_title: str = Form(...),
                         package_content: str = Form(...), people: str = Form(""),
                         original_price: float = Form(0), price: float = Form(...),
                         package_desc: str = Form(""), usage_rules: str = Form(""),
                         supports_room: str = Form(""),
                         image: UploadFile | None = File(None)):
    sid = _sid(user)
    a = db.get(GrowthAction, int(growth_action_id))
    if not a or a.store_id != sid:
        raise HTTPException(status_code=404, detail="请选择本店的增长策略")
    if float(price) <= 0:
        return JSONResponse({"detail": "活动价必须大于 0"}, status_code=400)
    people = people.strip()
    if people.isdigit():
        people += "人"          # 「适合4」→「适合4人」
    p = catalog_service.create_package(
        db, store_id=sid, growth_action_id=a.id,
        package_title=sanitize_text(package_title, 128),
        package_content=sanitize_text(package_content), people=people,
        original_price=float(original_price), price=float(price),
        package_desc=sanitize_text(package_desc), usage_rules=sanitize_text(usage_rules),
        extra={"supports_room": bool(supports_room)})
    # 套餐图（选填）：保存后展示在顾客扫码看到的套餐页
    if image and image.filename:
        import os
        from ..config import settings as _s
        ext = os.path.splitext(image.filename)[1].lower() or ".png"
        if ext in (".png", ".jpg", ".jpeg", ".webp"):
            data = await image.read()
            if len(data) <= 8 * 1024 * 1024:
                os.makedirs(_s.UPLOAD_DIR, exist_ok=True)
                import time as _t
                fname = f"pkg_{p.id}_{int(_t.time())}{ext}"
                with open(os.path.join(_s.UPLOAD_DIR, fname), "wb") as f:
                    f.write(data)
                p.main_image = f"/uploads/{fname}"
                db.commit()
    audit(db, user_id=user.id, role=user.role, store_id=sid, action="create_package",
          target=f"package:{p.id}", after={"title": p.package_title})
    return RedirectResponse("/owner/packages", status_code=302)


@router.post("/packages/{package_id}/edit")
async def edit_package(package_id: int, user: AuthUser = Depends(require_owner),
                       db=Depends(get_db),
                       package_title: str = Form(...), package_content: str = Form(...),
                       people: str = Form(""), original_price: float = Form(0),
                       price: float = Form(...), package_desc: str = Form(""),
                       usage_rules: str = Form(""), supports_room: str = Form(""),
                       image: UploadFile | None = File(None)):
    sid = _sid(user)
    p = db.get(Package, package_id)
    if not p or p.store_id != sid:
        raise HTTPException(status_code=404, detail="套餐不存在")
    if float(price) <= 0:
        return JSONResponse({"detail": "活动价必须大于 0"}, status_code=400)
    people = people.strip()
    if people.isdigit():
        people += "人"
    before = {"title": p.package_title, "price": p.price}
    catalog_service.update_package(
        db, p, package_title=sanitize_text(package_title, 128),
        package_content=sanitize_text(package_content), people=people,
        original_price=float(original_price), price=float(price),
        package_desc=sanitize_text(package_desc), usage_rules=sanitize_text(usage_rules),
        extra={"supports_room": bool(supports_room)})
    if image and image.filename:
        import os
        from ..config import settings as _s
        ext = os.path.splitext(image.filename)[1].lower() or ".png"
        if ext in (".png", ".jpg", ".jpeg", ".webp"):
            data = await image.read()
            if len(data) <= 8 * 1024 * 1024:
                os.makedirs(_s.UPLOAD_DIR, exist_ok=True)
                import time as _t
                fname = f"pkg_{p.id}_{int(_t.time())}{ext}"
                with open(os.path.join(_s.UPLOAD_DIR, fname), "wb") as f:
                    f.write(data)
                p.main_image = f"/uploads/{fname}"
                db.commit()
    audit(db, user_id=user.id, role=user.role, store_id=sid, action="edit_package",
          target=f"package:{p.id}", before=before,
          after={"title": p.package_title, "price": p.price})
    return RedirectResponse("/owner/packages", status_code=302)


# ---------- 活动管理（选套餐，不重复编辑套餐）----------
@router.get("/campaigns")
def campaigns(request: Request, user: AuthUser = Depends(require_owner), db=Depends(get_db)):
    sid = _sid(user)
    rows = db.query(Campaign).filter_by(store_id=sid).order_by(Campaign.created_at.desc()).all()
    packages = db.query(Package).filter_by(store_id=sid).all()
    return templates.TemplateResponse(
        "console/owner_campaigns.html",
        {"request": request, "user": user, "rows": rows, "packages": packages,
         "status_label": CAMPAIGN_STATUS_LABEL})


@router.post("/campaigns")
def create_campaign(user: AuthUser = Depends(require_owner), db=Depends(get_db),
                    package_id: int = Form(...), name: str = Form(...),
                    stock: int = Form(...), per_person_limit: int = Form(1),
                    voucher_valid_days: int = Form(14), need_reservation: str = Form(""),
                    reservable_times: str = Form("")):
    sid = _sid(user)
    if int(stock) <= 0:
        return JSONResponse({"detail": "库存必须大于 0"}, status_code=400)
    times = [t.strip() for t in reservable_times.split(",") if t.strip()]
    try:
        c = catalog_service.create_campaign(
            db, store_id=sid, package_id=int(package_id), name=sanitize_text(name, 128),
            stock=int(stock), per_person_limit=int(per_person_limit),
            voucher_valid_days=int(voucher_valid_days),
            need_reservation=bool(need_reservation), reservable_times=times)
    except ValueError as e:
        return JSONResponse({"detail": str(e)}, status_code=400)
    audit(db, user_id=user.id, role=user.role, store_id=sid, action="create_campaign",
          target=f"campaign:{c.id}", after={"name": c.name})
    return RedirectResponse("/owner/campaigns", status_code=302)


@router.post("/campaigns/{campaign_id}/submit")
def submit_campaign(campaign_id: int, user: AuthUser = Depends(require_owner), db=Depends(get_db)):
    sid = _sid(user)
    c = db.get(Campaign, campaign_id)
    if not c or c.store_id != sid:
        raise HTTPException(status_code=404, detail="活动不存在")
    try:
        catalog_service.submit_campaign(db, c)
    except Exception as e:
        return JSONResponse({"detail": f"无法提交：{e}"}, status_code=400)
    audit(db, user_id=user.id, role=user.role, store_id=sid, action="submit_campaign",
          target=f"campaign:{c.id}")
    return RedirectResponse("/owner/campaigns", status_code=302)


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
    campaigns = db.query(Campaign).filter_by(store_id=sid).order_by(Campaign.created_at.desc()).all()
    groups = []
    for c in campaigns:
        qrs = db.query(QRPlacement).filter_by(campaign_id=c.id).all()
        if qrs:  # 仅显示已审核通过（已生成二维码）的活动
            groups.append({"campaign": c, "qrs": qrs})
    return templates.TemplateResponse(
        "console/owner_qrs.html", {"request": request, "user": user, "groups": groups})


@router.post("/qrs/reference")
async def upload_reference(user: AuthUser = Depends(require_owner), db=Depends(get_db),
                           campaign_id: int = Form(...), image: UploadFile = File(...)):
    """老板端上传/更换活动参考图（海报），识别二维码区域后两端均可下载海报版。"""
    sid = _sid(user)
    campaign = db.get(Campaign, int(campaign_id))
    if not campaign or campaign.store_id != sid:
        raise HTTPException(status_code=404, detail="活动不存在")
    data = await image.read()
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="图片过大（上限 8MB）")
    from ..services.catalog_service import set_campaign_reference
    try:
        result = set_campaign_reference(db, campaign, data, image.filename or "ref.png")
    except ValueError as e:
        return JSONResponse({"detail": str(e)}, status_code=400)
    except Exception as e:  # 兜底：不让上传流程 500，把原因带回页面便于排查
        import logging
        import traceback
        logging.getLogger("upload").error("参考图处理失败:\n%s", traceback.format_exc())
        return JSONResponse({"detail": f"参考图处理失败：{type(e).__name__}: {e}"}, status_code=400)
    audit(db, user_id=user.id, role=user.role, store_id=sid, action="upload_reference",
          target=f"campaign:{campaign.id}", after={"detected": result["detected"]})
    return RedirectResponse("/owner/qrs", status_code=302)


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
        # 导出数据不脱敏（用户要求），页面展示仍脱敏
        w.writerow(["预约ID", "姓名", "手机号", "日期", "时间", "人数", "状态"])
        for r in db.query(Reservation).filter_by(store_id=sid):
            c = db.get(Customer, r.customer_id)
            w.writerow([r.id, r.name, c.phone if c else "", r.date, r.time,
                        r.people, r.status])
    else:
        raise HTTPException(status_code=400, detail="不支持的导出类型")
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{kind}.csv"'})
