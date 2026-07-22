"""平台管理端：门店 / 增长动作 / 活动 / 二维码创建，全局仪表盘，策略分析，审计。"""
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse

from ..audit import audit
from ..db import get_db
from ..deps import AuthUser, require_admin
from ..models import (
    CAMPAIGN_STATUS_LABEL,
    ActionStatus,
    AuditLog,
    Campaign,
    CampaignStatus,
    GrowthAction,
    QRPlacement,
    Store,
    Voucher,
    now_utc,
)
from ..render import templates
from ..services import catalog_service, redemption_service
from ..services.growth_service import evaluate
from ..services.stats_service import breakdown_by, funnel
from ..utils import sanitize_text

router = APIRouter(prefix="/admin")


def _parse_dt(s: str):
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


# ---------- 全局仪表盘 ----------
@router.get("")
def dashboard(request: Request, user: AuthUser = Depends(require_admin), db=Depends(get_db)):
    stores = db.query(Store).all()
    overall = funnel(db, {})
    channels = breakdown_by(db, {}, "channel")
    return templates.TemplateResponse(
        "console/admin_dashboard.html",
        {"request": request, "user": user, "stores": stores, "f": overall, "channels": channels})


# ---------- 门店 ----------
@router.get("/stores")
def stores_page(request: Request, user: AuthUser = Depends(require_admin), db=Depends(get_db)):
    stores = db.query(Store).all()
    return templates.TemplateResponse(
        "console/admin_stores.html", {"request": request, "user": user, "stores": stores})


@router.post("/stores")
def create_store(user: AuthUser = Depends(require_admin), db=Depends(get_db),
                 name: str = Form(...), industry: str = Form("餐饮"),
                 address: str = Form(""), phone: str = Form(""),
                 auto_confirm: str = Form("")):
    s = catalog_service.create_store(
        db, name=sanitize_text(name, 128), industry=industry,
        address=sanitize_text(address, 256), phone=phone, auto_confirm=bool(auto_confirm))
    audit(db, user_id=user.id, role=user.role, store_id=s.id,
          action="create_store", target=f"store:{s.id}", after={"name": s.name})
    return RedirectResponse("/admin/stores", status_code=302)


@router.get("/stores/{store_id}")
def store_detail(store_id: int, request: Request,
                 user: AuthUser = Depends(require_admin), db=Depends(get_db)):
    """门店详情：资料 + 数据概况 + 成员，可编辑资料、暂停/恢复。"""
    from ..models import Package, QRPlacement, StoreMember, User
    store = db.get(Store, store_id)
    if not store:
        raise HTTPException(status_code=404, detail="门店不存在")
    f = funnel(db, {"store_id": store_id})
    counts = {
        "actions": db.query(GrowthAction).filter_by(store_id=store_id).count(),
        "packages": db.query(Package).filter_by(store_id=store_id).count(),
        "campaigns": db.query(Campaign).filter_by(store_id=store_id).count(),
        "qrs": db.query(QRPlacement).filter_by(store_id=store_id).count(),
    }
    members = db.query(StoreMember).filter_by(store_id=store_id).all()
    member_rows = [{"m": m, "user": db.get(User, m.user_id)} for m in members]
    return templates.TemplateResponse(
        "console/admin_store_detail.html",
        {"request": request, "user": user, "store": store, "f": f,
         "counts": counts, "members": member_rows})


@router.post("/stores/{store_id}/edit")
def edit_store(store_id: int, user: AuthUser = Depends(require_admin), db=Depends(get_db),
               name: str = Form(...), industry: str = Form("餐饮"),
               address: str = Form(""), phone: str = Form(""),
               auto_confirm: str = Form("")):
    store = db.get(Store, store_id)
    if not store:
        raise HTTPException(status_code=404, detail="门店不存在")
    before = {"name": store.name, "phone": store.phone, "auto_confirm": store.auto_confirm}
    store.name = sanitize_text(name, 128)
    store.industry = industry
    store.address = sanitize_text(address, 256)
    store.phone = sanitize_text(phone, 32)
    store.auto_confirm = bool(auto_confirm)
    db.commit()
    audit(db, user_id=user.id, role=user.role, store_id=store.id, action="edit_store",
          target=f"store:{store.id}", before=before,
          after={"name": store.name, "phone": store.phone, "auto_confirm": store.auto_confirm})
    return RedirectResponse(f"/admin/stores/{store_id}", status_code=302)


@router.post("/stores/{store_id}/status")
def toggle_store(store_id: int, user: AuthUser = Depends(require_admin), db=Depends(get_db)):
    store = db.get(Store, store_id)
    if not store:
        raise HTTPException(status_code=404, detail="门店不存在")
    before = store.status
    store.status = "suspended" if store.status == "active" else "active"
    db.commit()
    audit(db, user_id=user.id, role=user.role, store_id=store.id, action="toggle_store",
          target=f"store:{store.id}", before={"status": before}, after={"status": store.status})
    return RedirectResponse(f"/admin/stores/{store_id}", status_code=302)


# ---------- 增长动作 ----------
@router.get("/actions")
def actions_page(request: Request, user: AuthUser = Depends(require_admin), db=Depends(get_db)):
    actions = db.query(GrowthAction).order_by(GrowthAction.created_at.desc()).all()
    stores = db.query(Store).all()
    rows = [{"a": a, "store": db.get(Store, a.store_id)} for a in actions]
    return templates.TemplateResponse(
        "console/admin_actions.html",
        {"request": request, "user": user, "rows": rows, "stores": stores})


@router.post("/actions")
def create_action(user: AuthUser = Depends(require_admin), db=Depends(get_db),
                  store_id: int = Form(...), name: str = Form(...),
                  hypothesis: str = Form(""), target_customer: str = Form(""),
                  target_scene: str = Form(""), success_metric: str = Form("redeem"),
                  success_threshold: int = Form(0), min_sample: int = Form(0),
                  observe_days: int = Form(14)):
    starts = now_utc()
    from datetime import timedelta
    action = catalog_service.create_growth_action(
        db, store_id=int(store_id), name=sanitize_text(name, 128),
        hypothesis=sanitize_text(hypothesis), target_customer=sanitize_text(target_customer, 128),
        target_scene=sanitize_text(target_scene, 128), success_metric=success_metric,
        success_threshold=int(success_threshold), min_sample=int(min_sample),
        observe_days=int(observe_days), starts_at=starts,
        ends_at=starts + timedelta(days=int(observe_days)), status=ActionStatus.RUNNING)
    audit(db, user_id=user.id, role=user.role, store_id=action.store_id,
          action="create_action", target=f"action:{action.id}", after={"name": action.name})
    return RedirectResponse("/admin/actions", status_code=302)


@router.get("/actions/{action_id}")
def action_detail(action_id: int, request: Request,
                  user: AuthUser = Depends(require_admin), db=Depends(get_db)):
    action = db.get(GrowthAction, action_id)
    if not action:
        raise HTTPException(status_code=404, detail="增长动作不存在")
    store = db.get(Store, action.store_id)
    campaigns = db.query(Campaign).filter_by(growth_action_id=action.id).all()
    qrs = db.query(QRPlacement).filter_by(growth_action_id=action.id).all()
    ev = evaluate(db, action)
    action.result = ev["result"]
    db.commit()
    return templates.TemplateResponse(
        "console/admin_action_detail.html",
        {"request": request, "user": user, "action": action, "store": store,
         "campaigns": campaigns, "qrs": qrs, "ev": ev})


@router.post("/actions/{action_id}/edit")
def edit_action(action_id: int, user: AuthUser = Depends(require_admin), db=Depends(get_db),
                name: str = Form(...), hypothesis: str = Form(""),
                target_customer: str = Form(""), target_scene: str = Form(""),
                success_metric: str = Form("redeem"), success_threshold: int = Form(0),
                min_sample: int = Form(0), observe_days: int = Form(14)):
    """管理员编辑增长策略并下发（更新即对该门店老板生效）。"""
    action = db.get(GrowthAction, action_id)
    if not action:
        raise HTTPException(status_code=404, detail="增长动作不存在")
    catalog_service.update_growth_action(
        db, action, name=sanitize_text(name, 128), hypothesis=sanitize_text(hypothesis),
        target_customer=sanitize_text(target_customer, 128),
        target_scene=sanitize_text(target_scene, 128), success_metric=success_metric,
        success_threshold=int(success_threshold), min_sample=int(min_sample),
        observe_days=int(observe_days))
    audit(db, user_id=user.id, role=user.role, store_id=action.store_id,
          action="edit_action", target=f"action:{action.id}", after={"name": action.name})
    return RedirectResponse(f"/admin/actions/{action.id}", status_code=302)


# ---------- 活动 ----------
@router.get("/campaigns")
def campaigns_page(request: Request, user: AuthUser = Depends(require_admin), db=Depends(get_db)):
    campaigns = db.query(Campaign).order_by(Campaign.created_at.desc()).all()
    # 待审核置顶
    order = {CampaignStatus.PENDING: 0, CampaignStatus.REJECTED: 1}
    campaigns.sort(key=lambda c: order.get(c.status, 2))
    rows = [{"c": c, "store": db.get(Store, c.store_id)} for c in campaigns]
    pending = sum(1 for c in campaigns if c.status == CampaignStatus.PENDING)
    return templates.TemplateResponse(
        "console/admin_campaigns.html",
        {"request": request, "user": user, "rows": rows, "pending": pending,
         "status_label": CAMPAIGN_STATUS_LABEL})


@router.get("/campaigns/{campaign_id}")
def campaign_review(campaign_id: int, request: Request,
                    user: AuthUser = Depends(require_admin), db=Depends(get_db)):
    """审核详情：同时展示活动规则与套餐内容、关联增长策略。"""
    c = db.get(Campaign, campaign_id)
    if not c:
        raise HTTPException(status_code=404, detail="活动不存在")
    return templates.TemplateResponse(
        "console/admin_campaign_review.html",
        {"request": request, "user": user, "c": c, "package": c.package,
         "store": db.get(Store, c.store_id),
         "action": db.get(GrowthAction, c.growth_action_id),
         "status_label": CAMPAIGN_STATUS_LABEL})


@router.post("/campaigns/{campaign_id}/approve")
def approve(campaign_id: int, user: AuthUser = Depends(require_admin), db=Depends(get_db)):
    c = db.get(Campaign, campaign_id)
    if not c:
        raise HTTPException(status_code=404, detail="活动不存在")
    try:
        n = catalog_service.approve_campaign(db, c, user.id)
    except Exception as e:
        return JSONResponse({"detail": f"无法通过：{e}"}, status_code=400)
    audit(db, user_id=user.id, role=user.role, store_id=c.store_id, action="approve_campaign",
          target=f"campaign:{c.id}", after={"qrs_created": n})
    return RedirectResponse("/admin/campaigns", status_code=302)


@router.post("/campaigns/{campaign_id}/reject")
def reject(campaign_id: int, user: AuthUser = Depends(require_admin), db=Depends(get_db),
           reason: str = Form("")):
    c = db.get(Campaign, campaign_id)
    if not c:
        raise HTTPException(status_code=404, detail="活动不存在")
    try:
        catalog_service.reject_campaign(db, c, user.id, sanitize_text(reason, 256))
    except Exception as e:
        return JSONResponse({"detail": f"无法驳回：{e}"}, status_code=400)
    audit(db, user_id=user.id, role=user.role, store_id=c.store_id, action="reject_campaign",
          target=f"campaign:{c.id}", after={"reason": reason})
    return RedirectResponse("/admin/campaigns", status_code=302)


# ---------- 二维码 ----------
@router.get("/qrs")
def qrs_page(request: Request, user: AuthUser = Depends(require_admin), db=Depends(get_db)):
    campaigns = db.query(Campaign).order_by(Campaign.created_at.desc()).all()
    groups = []
    for c in campaigns:
        qrs = db.query(QRPlacement).filter_by(campaign_id=c.id).all()
        if qrs:  # 审核通过后才有二维码
            groups.append({"campaign": c, "store": db.get(Store, c.store_id), "qrs": qrs})
    return templates.TemplateResponse(
        "console/admin_qrs.html",
        {"request": request, "user": user, "groups": groups})


@router.post("/qrs/reference")
async def upload_reference(user: AuthUser = Depends(require_admin), db=Depends(get_db),
                           campaign_id: int = Form(...), image: UploadFile = File(...)):
    campaign = db.get(Campaign, int(campaign_id))
    if not campaign:
        raise HTTPException(status_code=404, detail="活动不存在")
    data = await image.read()
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="图片过大（上限 8MB）")
    try:
        result = catalog_service.set_campaign_reference(db, campaign, data, image.filename or "ref.png")
    except ValueError as e:
        return JSONResponse({"detail": str(e)}, status_code=400)
    except Exception as e:  # 兜底：不让上传流程 500，把原因带回页面便于排查
        import logging
        import traceback
        logging.getLogger("upload").error("参考图处理失败:\n%s", traceback.format_exc())
        return JSONResponse({"detail": f"参考图处理失败：{type(e).__name__}: {e}"}, status_code=400)
    audit(db, user_id=user.id, role=user.role, store_id=campaign.store_id,
          action="upload_reference", target=f"campaign:{campaign.id}",
          after={"detected": result["detected"]})
    return RedirectResponse("/admin/qrs", status_code=302)


# ---------- 数据导出（全部门店，手机号脱敏）----------
@router.get("/export/{kind}")
def export(kind: str, user: AuthUser = Depends(require_admin), db=Depends(get_db)):
    import csv
    import io

    from fastapi.responses import StreamingResponse

    from ..models import Customer, Event, Redemption, Reservation

    buf = io.StringIO()
    w = csv.writer(buf)
    if kind == "redemptions":
        w.writerow(["核销ID", "门店ID", "凭证ID", "店员ID", "金额", "是否冲正", "核销时间"])
        for r in db.query(Redemption).order_by(Redemption.created_at.desc()):
            w.writerow([r.id, r.store_id, r.voucher_id, r.staff_id, r.amount,
                        "是" if r.is_reversal else "否",
                        r.created_at.strftime("%Y-%m-%d %H:%M")])
    elif kind == "reservations":
        # 导出数据不脱敏（用户要求），页面展示仍脱敏
        w.writerow(["预约ID", "门店ID", "姓名", "手机号", "日期", "时间", "人数", "包间", "状态"])
        for r in db.query(Reservation).order_by(Reservation.created_at.desc()):
            c = db.get(Customer, r.customer_id)
            w.writerow([r.id, r.store_id, r.name, c.phone if c else "",
                        r.date, r.time, r.people, "是" if r.need_room else "否", r.status])
    elif kind == "events":
        w.writerow(["事件ID", "类型", "门店ID", "增长动作ID", "活动ID", "二维码ID",
                    "渠道", "访客ID", "顾客ID", "时间"])
        for e in db.query(Event).order_by(Event.created_at.desc()).limit(10000):
            w.writerow([e.id, e.event_type, e.store_id, e.growth_action_id, e.campaign_id,
                        e.qr_id, e.channel, e.visitor_id or "", e.customer_id or "",
                        e.created_at.strftime("%Y-%m-%d %H:%M:%S")])
    elif kind == "customers":
        w.writerow(["顾客ID", "姓名", "手机号", "首次访问", "最近访问"])
        for c in db.query(Customer).order_by(Customer.created_at.desc()):
            w.writerow([c.id, c.name, c.phone,
                        c.first_seen.strftime("%Y-%m-%d %H:%M"),
                        c.last_seen.strftime("%Y-%m-%d %H:%M")])
    else:
        raise HTTPException(status_code=400, detail="不支持的导出类型")
    audit(db, user_id=user.id, role=user.role, store_id=None, action="export",
          target=f"export:{kind}")
    buf.seek(0)
    return StreamingResponse(
        iter(["﻿" + buf.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{kind}.csv"'})


# ---------- 审计日志 ----------
@router.get("/audit")
def audit_page(request: Request, user: AuthUser = Depends(require_admin), db=Depends(get_db)):
    logs = db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(200).all()
    return templates.TemplateResponse(
        "console/admin_audit.html", {"request": request, "user": user, "logs": logs})


# ---------- 冲正 ----------
@router.post("/voucher/{voucher_id}/reverse")
def reverse(voucher_id: int, user: AuthUser = Depends(require_admin), db=Depends(get_db),
            reason: str = Form("")):
    try:
        redemption_service.reverse_redemption(
            db, voucher_id=voucher_id, admin_id=user.id, reason=sanitize_text(reason, 256))
    except redemption_service.RedeemError as e:
        return JSONResponse({"detail": e.message}, status_code=e.status)
    return {"ok": True}
