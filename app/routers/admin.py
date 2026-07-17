"""平台管理端：门店 / 增长动作 / 活动 / 二维码创建，全局仪表盘，策略分析，审计。"""
from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from ..audit import audit
from ..db import get_db
from ..deps import AuthUser, require_admin
from ..models import (
    ActionStatus,
    AuditLog,
    Campaign,
    CampaignStatus,
    GrowthAction,
    QRPlacement,
    Store,
    Voucher,
    CHANNELS,
    now_utc,
)
from ..render import templates
from ..services import catalog_service, redemption_service
from ..services.growth_service import evaluate
from ..services.stats_service import funnel
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
    return templates.TemplateResponse(
        "console/admin_dashboard.html",
        {"request": request, "user": user, "stores": stores, "f": overall})


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


# ---------- 活动 ----------
@router.get("/campaigns")
def campaigns_page(request: Request, user: AuthUser = Depends(require_admin), db=Depends(get_db)):
    campaigns = db.query(Campaign).order_by(Campaign.created_at.desc()).all()
    actions = db.query(GrowthAction).all()
    rows = [{"c": c, "store": db.get(Store, c.store_id)} for c in campaigns]
    return templates.TemplateResponse(
        "console/admin_campaigns.html",
        {"request": request, "user": user, "rows": rows, "actions": actions})


@router.post("/campaigns")
def create_campaign(user: AuthUser = Depends(require_admin), db=Depends(get_db),
                    growth_action_id: int = Form(...), name: str = Form(...),
                    package_title: str = Form(""), package_desc: str = Form(""),
                    package_content: str = Form(""), people: str = Form(""),
                    original_price: float = Form(0), price: float = Form(0),
                    stock: int = Form(0), per_person_limit: int = Form(1),
                    need_reservation: str = Form(""), usage_rules: str = Form(""),
                    voucher_valid_days: int = Form(14)):
    action = db.get(GrowthAction, int(growth_action_id))
    if not action:
        raise HTTPException(status_code=404, detail="增长动作不存在")
    c = catalog_service.create_campaign(
        db, store_id=action.store_id, growth_action_id=action.id,
        name=sanitize_text(name, 128), package_title=sanitize_text(package_title, 128),
        package_desc=sanitize_text(package_desc), package_content=sanitize_text(package_content),
        people=people, original_price=float(original_price), price=float(price),
        stock=int(stock), per_person_limit=int(per_person_limit),
        need_reservation=bool(need_reservation), usage_rules=sanitize_text(usage_rules),
        voucher_valid_days=int(voucher_valid_days), status=CampaignStatus.RUNNING,
        starts_at=now_utc())
    audit(db, user_id=user.id, role=user.role, store_id=c.store_id,
          action="create_campaign", target=f"campaign:{c.id}", after={"name": c.name})
    return RedirectResponse("/admin/campaigns", status_code=302)


# ---------- 二维码 ----------
@router.get("/qrs")
def qrs_page(request: Request, user: AuthUser = Depends(require_admin), db=Depends(get_db)):
    qrs = db.query(QRPlacement).order_by(QRPlacement.created_at.desc()).all()
    campaigns = db.query(Campaign).all()
    rows = [{"qr": q, "store": db.get(Store, q.store_id),
             "campaign": db.get(Campaign, q.campaign_id)} for q in qrs]
    return templates.TemplateResponse(
        "console/admin_qrs.html",
        {"request": request, "user": user, "rows": rows,
         "campaigns": campaigns, "channels": CHANNELS})


@router.post("/qrs")
def create_qr(user: AuthUser = Depends(require_admin), db=Depends(get_db),
              campaign_id: int = Form(...), channel: str = Form(...),
              name: str = Form(""), content_no: str = Form(""),
              material_no: str = Form(""), note: str = Form("")):
    campaign = db.get(Campaign, int(campaign_id))
    if not campaign:
        raise HTTPException(status_code=404, detail="活动不存在")
    qr = catalog_service.create_qr(
        db, store_id=campaign.store_id, growth_action_id=campaign.growth_action_id,
        campaign_id=campaign.id, channel=channel, content_no=sanitize_text(content_no, 64),
        material_no=sanitize_text(material_no, 64), name=sanitize_text(name, 128),
        note=sanitize_text(note, 256))
    audit(db, user_id=user.id, role=user.role, store_id=qr.store_id,
          action="create_qr", target=f"qr:{qr.short_code}", after={"channel": channel})
    return RedirectResponse("/admin/qrs", status_code=302)


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
