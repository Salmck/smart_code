"""核销服务 —— 系统风险最高的模块。

两步式：verify（只查询展示）→ redeem（原子核销）。
一次性核销由三重机制共同保证：
  1. 条件 UPDATE：仅当 status='usable' 且未过期时命中，rowcount 才为 1
  2. Redemption.voucher_id 唯一约束：一凭证一条成功核销
  3. request_id 唯一约束 + 幂等重放：同一请求重复提交返回首次结果
即使两名店员同时扫、连点确认、网络重发、顾客截图转发，也只成功一次。
"""
from dataclasses import dataclass

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from ..audit import audit
from ..dyncode import parse_token
from ..events import log_event
from ..models import (
    Campaign,
    EventType,
    Redemption,
    Reservation,
    ReservationStatus,
    Role,
    Voucher,
    VoucherStatus,
    now_utc,
)


class RedeemError(Exception):
    def __init__(self, message, status=400, code=""):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


def _find_voucher(db, raw: str) -> Voucher | None:
    """按扫码令牌 / 对外编号 / 备用数字码定位凭证。"""
    raw = (raw or "").strip()
    if not raw:
        return None
    # 动态令牌
    if raw.startswith("ORDX:"):
        code = parse_token(raw)
        if not code:
            raise RedeemError("核销码已失效，请让顾客刷新凭证页", 400, "invalid_token")
        return db.query(Voucher).filter_by(code=code).first()
    # 完整短链
    if "/q/" not in raw:
        v = db.query(Voucher).filter_by(code=raw).first()
        if v:
            return v
        return db.query(Voucher).filter_by(backup_code=raw).first()
    return None


def verify_voucher(db, *, raw_code: str, staff_store_id: int, staff_role: str,
                   backup_phone_last4: str = "") -> dict:
    """第一步：查询凭证并做前置校验，仅返回展示信息，不改状态。"""
    voucher = _find_voucher(db, raw_code)
    if not voucher:
        raise RedeemError("未找到该凭证", 404, "not_found")

    # 门店归属校验（管理员例外）
    if staff_role != Role.ADMIN and voucher.store_id != staff_store_id:
        raise RedeemError("该凭证不属于本门店", 403, "wrong_store")

    campaign = db.get(Campaign, voucher.campaign_id)
    reservation = (
        db.get(Reservation, voucher.reservation_id) if voucher.reservation_id else None
    )
    info = {
        "voucher_id": voucher.id,
        "code": voucher.code,
        "status": voucher.status,
        "campaign_title": campaign.package_title if campaign else "",
        "price": campaign.price if campaign else 0,
        "expires_at": voucher.expires_at.strftime("%Y-%m-%d %H:%M") if voucher.expires_at else "",
        "reservation": None,
        "redeemable": False,
        "reason": "",
    }
    if reservation:
        info["reservation"] = {
            "name": reservation.name, "date": reservation.date,
            "time": reservation.time, "people": reservation.people,
            "need_room": reservation.need_room, "status": reservation.status,
        }

    # 备用码场景可用手机后4位辅助核对
    if backup_phone_last4:
        from ..models import Customer
        customer = db.get(Customer, voucher.customer_id)
        if not customer or customer.phone[-4:] != backup_phone_last4:
            raise RedeemError("手机号后四位不匹配", 400, "phone_mismatch")

    now = now_utc()
    if voucher.status == VoucherStatus.REDEEMED:
        when = voucher.redeemed_at.strftime("%Y年%m月%d日 %H:%M") if voucher.redeemed_at else ""
        info["reason"] = f"该凭证已于 {when} 核销，不可重复使用"
    elif voucher.status == VoucherStatus.PENDING:
        info["reason"] = "凭证待门店确认后才可核销"
    elif voucher.status == VoucherStatus.CANCELLED:
        info["reason"] = "凭证已取消"
    elif voucher.status == VoucherStatus.EXPIRED or (voucher.expires_at and voucher.expires_at < now):
        info["reason"] = "凭证已过期"
    elif voucher.status == VoucherStatus.USABLE:
        info["redeemable"] = True

    log_event(db, event_type=EventType.VERIFY_VOUCHER, store_id=voucher.store_id,
              campaign_id=voucher.campaign_id, qr_id=voucher.qr_id,
              customer_id=voucher.customer_id, channel=voucher.channel,
              content_no=voucher.content_no, material_no=voucher.material_no)
    return info


def redeem(db, *, voucher_id: int, staff_id: int, staff_store_id: int,
           staff_role: str, request_id: str, amount: float | None = None) -> dict:
    """第二步：原子核销。幂等——同 request_id 重放返回首次结果。"""
    # 幂等：请求已处理过则直接返回既有结果
    existing = db.query(Redemption).filter_by(request_id=request_id).first()
    if existing:
        v = db.get(Voucher, existing.voucher_id)
        return {"ok": True, "already": True, "voucher_code": v.code if v else "",
                "redeemed_at": existing.created_at.strftime("%Y年%m月%d日 %H:%M")}

    voucher = db.get(Voucher, voucher_id)
    if not voucher:
        raise RedeemError("未找到该凭证", 404, "not_found")
    if staff_role != Role.ADMIN and voucher.store_id != staff_store_id:
        raise RedeemError("该凭证不属于本门店", 403, "wrong_store")

    now = now_utc()
    campaign = db.get(Campaign, voucher.campaign_id)
    redeem_amount = amount if amount is not None else (campaign.price if campaign else 0)

    # 核心：条件原子 UPDATE。只有实际更新一行才算核销成功。
    result = db.execute(
        update(Voucher)
        .where(
            Voucher.id == voucher_id,
            Voucher.status == VoucherStatus.USABLE,
            Voucher.expires_at > now,
        )
        .values(status=VoucherStatus.REDEEMED, redeemed_at=now, redeemed_by=staff_id)
    )
    if result.rowcount != 1:
        # 未命中：重新读取给出准确原因
        db.rollback()
        v = db.get(Voucher, voucher_id)
        if v and v.status == VoucherStatus.REDEEMED:
            when = v.redeemed_at.strftime("%Y年%m月%d日 %H:%M") if v.redeemed_at else ""
            raise RedeemError(f"该凭证已于 {when} 核销，不可重复使用", 409, "already_redeemed")
        if v and v.expires_at and v.expires_at < now:
            raise RedeemError("凭证已过期，无法核销", 400, "expired")
        raise RedeemError("凭证状态不可核销", 400, "not_usable")

    # 写核销记录（voucher_id / request_id 双唯一约束兜底防并发）
    rec = Redemption(voucher_id=voucher_id, store_id=voucher.store_id, staff_id=staff_id,
                     amount=redeem_amount, request_id=request_id, is_reversal=False)
    db.add(rec)

    # 关联预约转「已完成」
    if voucher.reservation_id:
        reservation = db.get(Reservation, voucher.reservation_id)
        if reservation and reservation.status == ReservationStatus.CONFIRMED:
            reservation.status = ReservationStatus.COMPLETED

    log_event(db, event_type=EventType.REDEEM, store_id=voucher.store_id,
              campaign_id=voucher.campaign_id, qr_id=voucher.qr_id,
              customer_id=voucher.customer_id, channel=voucher.channel,
              content_no=voucher.content_no, material_no=voucher.material_no,
              extra={"amount": redeem_amount}, commit=False)
    audit(db, user_id=staff_id, role=staff_role, store_id=voucher.store_id,
          action="redeem", target=f"voucher:{voucher.code}",
          before={"status": VoucherStatus.USABLE},
          after={"status": VoucherStatus.REDEEMED}, commit=False)

    try:
        db.commit()
    except IntegrityError:
        # 并发下另一个请求已抢先插入核销记录：本次视为重复
        db.rollback()
        v = db.get(Voucher, voucher_id)
        when = v.redeemed_at.strftime("%Y年%m月%d日 %H:%M") if v and v.redeemed_at else ""
        raise RedeemError(f"该凭证已于 {when} 核销，不可重复使用", 409, "already_redeemed")

    return {"ok": True, "already": False, "voucher_code": voucher.code,
            "redeemed_at": now.strftime("%Y年%m月%d日 %H:%M"), "amount": redeem_amount}


def reverse_redemption(db, *, voucher_id: int, admin_id: int, reason: str) -> None:
    """管理员冲正：不删除原记录，插入冲正记录并恢复凭证可用。"""
    voucher = db.get(Voucher, voucher_id)
    if not voucher or voucher.status != VoucherStatus.REDEEMED:
        raise RedeemError("该凭证非已核销状态，无法冲正")
    import uuid
    rec = Redemption(voucher_id=voucher_id, store_id=voucher.store_id, staff_id=admin_id,
                     amount=0, request_id=f"reversal-{uuid.uuid4().hex}",
                     is_reversal=True, note=reason)
    db.add(rec)
    voucher.status = VoucherStatus.USABLE
    voucher.redeemed_at = None
    voucher.redeemed_by = None
    audit(db, user_id=admin_id, role=Role.ADMIN, store_id=voucher.store_id,
          action="reverse_redemption", target=f"voucher:{voucher.code}",
          before={"status": VoucherStatus.REDEEMED},
          after={"status": VoucherStatus.USABLE, "reason": reason}, commit=False)
    db.commit()
