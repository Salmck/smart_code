"""顾客端（H5 + 可返回 JSON，供未来小程序复用同一 service）。

流程：/q/{short_code} 扫码落地 → 套餐页 → /act 验证手机号 → 领取/预约 →
凭证页（动态核销码）。归因上下文全程由短码解析并冗余落库。
"""
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from .. import captcha
from ..attribution import AttributionContext
from ..deps import CUSTOMER_COOKIE, client_ip, current_customer, require_customer
from ..db import get_db
from ..events import log_event, log_from_ctx
from ..models import (
    Campaign,
    Customer,
    EventType,
    QRPlacement,
    QRStatus,
    Reservation,
    Store,
    Voucher,
    VoucherStatus,
)
from ..render import templates
from ..services import auth_service, claim_service
from ..services.catalog_service import campaign_is_open
from ..dyncode import make_token
from ..utils import sanitize_text

router = APIRouter()

VID_COOKIE = "ordinex_vid"
SID_COOKIE = "ordinex_sid"


def _resolve(db, short_code: str):
    qr = db.query(QRPlacement).filter_by(short_code=short_code).first()
    if not qr:
        return None, None
    campaign = db.get(Campaign, qr.campaign_id)
    return qr, campaign


# ---------- 扫码落地 + 套餐页 ----------
@router.get("/q/{short_code}")
def scan_landing(short_code: str, request: Request, db=Depends(get_db),
                 customer_id: int | None = Depends(current_customer)):
    qr, campaign = _resolve(db, short_code)
    if not qr or not campaign:
        return templates.TemplateResponse(
            "customer/invalid.html",
            {"request": request, "title": "无效二维码", "message": "该二维码不存在或已失效"},
            status_code=404,
        )

    store = db.get(Store, qr.store_id)
    ctx = AttributionContext.from_qr(qr)

    # 匿名访客 / 会话 ID
    vid = request.cookies.get(VID_COOKIE) or uuid.uuid4().hex
    sid = request.cookies.get(SID_COOKIE) or uuid.uuid4().hex

    # 二维码暂停：友好提示（短码永久有效，不报错）
    if qr.status != QRStatus.ACTIVE:
        resp = templates.TemplateResponse(
            "customer/invalid.html",
            {"request": request, "title": "活动暂停", "message": "该活动入口已暂停，请稍后再来"},
        )
        resp.set_cookie(VID_COOKIE, vid, max_age=31536000, httponly=True)
        return resp

    # 记录扫码 + 浏览
    log_from_ctx(db, EventType.SCAN, ctx, visitor_id=vid, session_id=sid)
    log_from_ctx(db, EventType.VIEW_CAMPAIGN, ctx, visitor_id=vid, session_id=sid)

    open_ok, reason = campaign_is_open(campaign)
    # 已验证过手机号的回头客：显示「查看我的凭证」，避免找不到已领/已约的码
    my_voucher = (claim_service.latest_active_voucher(db, campaign.id, customer_id)
                  if customer_id else None)
    resp = templates.TemplateResponse(
        "customer/campaign.html",
        {
            "request": request, "campaign": campaign, "store": store,
            "short_code": short_code, "open_ok": open_ok, "reason": reason,
            "remaining": max(campaign.stock - campaign.claimed, 0),
            "my_voucher": my_voucher,
        },
    )
    resp.set_cookie(VID_COOKIE, vid, max_age=31536000, httponly=True)
    resp.set_cookie(SID_COOKIE, sid, max_age=86400, httponly=True)
    return resp


# ---------- 领取/预约操作页 ----------
@router.get("/q/{short_code}/act")
def action_page(short_code: str, request: Request, db=Depends(get_db),
                customer_id: int | None = Depends(current_customer)):
    qr, campaign = _resolve(db, short_code)
    if not qr or not campaign:
        raise HTTPException(status_code=404, detail="无效二维码")
    store = db.get(Store, qr.store_id)
    ctx = AttributionContext.from_qr(qr)
    vid = request.cookies.get(VID_COOKIE)

    open_ok, reason = campaign_is_open(campaign)
    if not open_ok:
        return templates.TemplateResponse(
            "customer/invalid.html",
            {"request": request, "title": "无法参与", "message": reason},
        )

    ev_type = EventType.CLICK_RESERVE if campaign.need_reservation else EventType.CLICK_CLAIM
    log_from_ctx(db, ev_type, ctx, visitor_id=vid, customer_id=customer_id)

    # 已有凭证的回头客：直接带去凭证页，避免重复领取/预约
    if customer_id:
        existing = claim_service.latest_active_voucher(db, campaign.id, customer_id)
        if existing:
            return RedirectResponse(f"/v/{existing.code}", status_code=302)

    return templates.TemplateResponse(
        "customer/action.html",
        {"request": request, "campaign": campaign, "store": store,
         "short_code": short_code, "logged_in": customer_id is not None},
    )


# ---------- 人机验证挑战 ----------
@router.get("/api/captcha")
def get_captcha():
    return captcha.issue_challenge()


# ---------- 顾客手机号验证 ----------
@router.post("/api/customer/send-code")
def send_code(request: Request, db=Depends(get_db), short_code: str = Form(...),
              phone: str = Form(...), captcha_id: str = Form(...),
              captcha_answer: str = Form(...)):
    qr, campaign = _resolve(db, short_code)
    if not qr:
        raise HTTPException(status_code=404, detail="无效二维码")
    try:
        resp = auth_service.send_customer_code(
            db, phone, client_ip(request), captcha_id, captcha_answer)
    except auth_service.AuthError as e:
        return JSONResponse({"detail": e.message}, status_code=e.status)
    ctx = AttributionContext.from_qr(qr)
    log_from_ctx(db, EventType.SEND_CODE, ctx,
                 visitor_id=request.cookies.get(VID_COOKIE))
    return resp


@router.post("/api/customer/verify")
def verify_code(request: Request, db=Depends(get_db), short_code: str = Form(...),
                phone: str = Form(...), code: str = Form(...)):
    qr, campaign = _resolve(db, short_code)
    if not qr:
        raise HTTPException(status_code=404, detail="无效二维码")
    try:
        result = auth_service.verify_customer_code(db, phone, code)
    except auth_service.AuthError as e:
        return JSONResponse({"detail": e.message}, status_code=e.status)

    ctx = AttributionContext.from_qr(qr)
    log_from_ctx(db, EventType.VERIFY_PHONE, ctx,
                 visitor_id=request.cookies.get(VID_COOKIE),
                 customer_id=result["customer_id"])

    # 验证后若该顾客已在本活动有凭证，直接带去凭证页（找回已领/已约的码）
    existing = claim_service.latest_active_voucher(db, campaign.id, result["customer_id"])
    resp = JSONResponse({"ok": True, "token": result["token"],
                         "need_reservation": campaign.need_reservation,
                         "existing_voucher": f"/v/{existing.code}" if existing else None})
    # 双通道：H5 用 Cookie；小程序可从 body 取 token 走 Bearer
    resp.set_cookie(CUSTOMER_COOKIE, result["token"], max_age=604800, httponly=True)
    return resp


# ---------- 领取 ----------
@router.post("/api/customer/claim")
def claim(short_code: str = Form(...), db=Depends(get_db),
          customer_id: int = Depends(require_customer)):
    qr, campaign = _resolve(db, short_code)
    if not qr or not campaign:
        raise HTTPException(status_code=404, detail="无效二维码")
    ctx = AttributionContext.from_qr(qr)
    try:
        voucher = claim_service.claim_voucher(
            db, campaign=campaign, customer_id=customer_id, ctx=ctx)
    except claim_service.ClaimError as e:
        return JSONResponse({"detail": e.message}, status_code=e.status)
    return {"ok": True, "voucher_code": voucher.code, "redirect": f"/v/{voucher.code}"}


# ---------- 预约 ----------
@router.post("/api/customer/reserve")
def reserve(short_code: str = Form(...), db=Depends(get_db),
            customer_id: int = Depends(require_customer),
            name: str = Form(""), date: str = Form(...), time: str = Form(...),
            people: int = Form(1), need_room: str = Form(""), note: str = Form("")):
    qr, campaign = _resolve(db, short_code)
    if not qr or not campaign:
        raise HTTPException(status_code=404, detail="无效二维码")
    ctx = AttributionContext.from_qr(qr)
    try:
        reservation, voucher = claim_service.create_reservation(
            db, campaign=campaign, customer_id=customer_id, ctx=ctx,
            name=sanitize_text(name, 64), date=date, time=time, people=int(people),
            need_room=bool(need_room), note=sanitize_text(note, 256))
    except claim_service.ClaimError as e:
        return JSONResponse({"detail": e.message}, status_code=e.status)
    return {"ok": True, "voucher_code": voucher.code,
            "reservation_status": reservation.status, "redirect": f"/v/{voucher.code}"}


# ---------- 凭证页 ----------
@router.get("/v/{voucher_code}")
def voucher_page(voucher_code: str, request: Request, db=Depends(get_db)):
    voucher = db.query(Voucher).filter_by(code=voucher_code).first()
    if not voucher:
        return templates.TemplateResponse(
            "customer/invalid.html",
            {"request": request, "title": "凭证不存在", "message": "未找到该核销凭证"},
            status_code=404,
        )
    campaign = db.get(Campaign, voucher.campaign_id)
    store = db.get(Store, voucher.store_id)
    customer = db.get(Customer, voucher.customer_id)
    reservation = db.get(Reservation, voucher.reservation_id) if voucher.reservation_id else None

    log_event(db, event_type=EventType.OPEN_VOUCHER, store_id=voucher.store_id,
              campaign_id=voucher.campaign_id, qr_id=voucher.qr_id,
              customer_id=voucher.customer_id, channel=voucher.channel)

    # 已核销/过期/取消：显示对应状态页信息（同一模板按 status 渲染）
    return templates.TemplateResponse(
        "customer/voucher.html",
        {"request": request, "voucher": voucher, "campaign": campaign,
         "store": store, "customer": customer, "reservation": reservation,
         "token": make_token(voucher.code) if voucher.status == VoucherStatus.USABLE else "",
         "status_label": _status_label(voucher.status)},
    )


@router.get("/api/voucher/{voucher_code}/token")
def voucher_token(voucher_code: str, db=Depends(get_db)):
    """凭证页每 60 秒轮询获取新动态令牌（小程序端可直接取此令牌自绘二维码）。"""
    voucher = db.query(Voucher).filter_by(code=voucher_code).first()
    if not voucher:
        raise HTTPException(status_code=404, detail="凭证不存在")
    if voucher.status != VoucherStatus.USABLE:
        return {"status": voucher.status, "token": "", "label": _status_label(voucher.status)}
    return {"status": voucher.status, "token": make_token(voucher.code)}


@router.get("/api/voucher/{voucher_code}/qr.png")
def voucher_qr(voucher_code: str, db=Depends(get_db)):
    """把当前动态令牌渲染成二维码图片（H5 端 <img> 每 60 秒刷新）。"""
    from fastapi.responses import Response
    from ..qrgen import png_bytes
    voucher = db.query(Voucher).filter_by(code=voucher_code).first()
    if not voucher or voucher.status != VoucherStatus.USABLE:
        raise HTTPException(status_code=404, detail="凭证不可用")
    return Response(png_bytes(make_token(voucher.code), box_size=8),
                    media_type="image/png", headers={"Cache-Control": "no-store"})


# ---------- 我的凭证 / 预约 ----------
@router.get("/my/vouchers")
def my_vouchers(request: Request, db=Depends(get_db),
                customer_id: int = Depends(require_customer)):
    vouchers = (db.query(Voucher).filter_by(customer_id=customer_id)
                .order_by(Voucher.created_at.desc()).all())
    rows = []
    for v in vouchers:
        c = db.get(Campaign, v.campaign_id)
        rows.append({"v": v, "title": c.package_title if c else "",
                     "label": _status_label(v.status)})
    return templates.TemplateResponse(
        "customer/my_vouchers.html", {"request": request, "rows": rows})


@router.post("/api/reservation/{reservation_id}/cancel")
def cancel_reservation(reservation_id: int, db=Depends(get_db),
                       customer_id: int = Depends(require_customer)):
    reservation = db.get(Reservation, reservation_id)
    if not reservation or reservation.customer_id != customer_id:
        raise HTTPException(status_code=404, detail="预约不存在")
    ctx_qr = db.get(QRPlacement, reservation.qr_id) if reservation.qr_id else None
    try:
        claim_service.cancel_reservation(db, reservation)
    except claim_service.ClaimError as e:
        return JSONResponse({"detail": e.message}, status_code=e.status)
    log_event(db, event_type=EventType.CANCEL_RESERVATION, store_id=reservation.store_id,
              campaign_id=reservation.campaign_id, customer_id=customer_id,
              channel=reservation.channel)
    return {"ok": True}


def _status_label(status: str) -> str:
    return {
        VoucherStatus.PENDING: "待生效",
        VoucherStatus.USABLE: "可使用",
        VoucherStatus.REDEEMED: "已核销",
        VoucherStatus.EXPIRED: "已过期",
        VoucherStatus.CANCELLED: "已取消",
    }.get(status, status)
