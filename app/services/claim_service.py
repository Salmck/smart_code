"""领取与预约服务。

一活动一路径：need_reservation=False 只走领取；True 只走预约。
库存在「凭证生效时」扣减：
  - 领取型：领取成功即扣，凭证 usable
  - 自动确认预约：提交即扣，凭证 usable
  - 人工确认预约：提交时不扣，凭证 pending；门店确认时才扣并转 usable
每人限领：同顾客同活动的有效凭证数（不含已取消）合并计数。
"""
from datetime import timedelta

from sqlalchemy import update

from ..config import settings
from ..events import log_from_ctx
from ..models import (
    Campaign,
    EventType,
    Reservation,
    ReservationStatus,
    Store,
    Voucher,
    VoucherStatus,
    now_utc,
)
from ..shortcode import gen_digits, gen_shortcode
from ..statemachine import RESERVATION_TRANSITIONS, VOUCHER_TRANSITIONS, transition
from .catalog_service import campaign_is_open


class ClaimError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


def latest_active_voucher(db, campaign_id: int, customer_id: int):
    """返回该顾客在此活动下最近的有效凭证（用于「找回我的码」）。"""
    return (
        db.query(Voucher)
        .filter(Voucher.campaign_id == campaign_id,
                Voucher.customer_id == customer_id,
                Voucher.status != VoucherStatus.CANCELLED)
        .order_by(Voucher.created_at.desc())
        .first()
    )


def _check_per_person_limit(db, campaign: Campaign, customer_id: int):
    active = (
        db.query(Voucher)
        .filter(
            Voucher.campaign_id == campaign.id,
            Voucher.customer_id == customer_id,
            Voucher.status != VoucherStatus.CANCELLED,
        )
        .count()
    )
    if active >= campaign.per_person_limit:
        word = "预约" if campaign.need_reservation else "领取"
        raise ClaimError(
            f"同一手机号每个活动限{word} {campaign.per_person_limit} 次（所有渠道合计），"
            f"您已参与过，请在「我的凭证」中查看")


def _decrement_stock(db, campaign_id: int) -> bool:
    """原子扣库存：仅当 claimed < stock 时 +1。返回是否成功。"""
    result = db.execute(
        update(Campaign)
        .where(Campaign.id == campaign_id, Campaign.claimed < Campaign.stock)
        .values(claimed=Campaign.claimed + 1)
    )
    return result.rowcount == 1


def _new_voucher(db, *, campaign, customer_id, ctx, status, reservation_id=None) -> Voucher:
    expires_at = now_utc() + timedelta(days=campaign.voucher_valid_days or 14)
    for _ in range(10):
        code = gen_shortcode(settings.VOUCHER_CODE_LENGTH)
        if not db.query(Voucher).filter_by(code=code).first():
            break
    else:
        raise ClaimError("凭证编号生成冲突，请重试")
    v = Voucher(
        code=code,
        backup_code=gen_digits(settings.BACKUP_CODE_LENGTH),
        store_id=campaign.store_id,
        campaign_id=campaign.id,
        reservation_id=reservation_id,
        customer_id=customer_id,
        qr_id=ctx.qr_id,
        growth_action_id=ctx.growth_action_id,
        channel=ctx.channel,
        content_no=ctx.content_no,
        material_no=ctx.material_no,
        status=status,
        expires_at=expires_at,
    )
    db.add(v)
    return v


def claim_voucher(db, *, campaign: Campaign, customer_id: int, ctx) -> Voucher:
    """领取型活动：直接生成可用凭证。"""
    if campaign.need_reservation:
        raise ClaimError("该活动需要预约")
    ok, reason = campaign_is_open(campaign)
    if not ok:
        raise ClaimError(reason)
    _check_per_person_limit(db, campaign, customer_id)

    if not _decrement_stock(db, campaign.id):
        raise ClaimError("活动名额已抢完")
    voucher = _new_voucher(db, campaign=campaign, customer_id=customer_id,
                           ctx=ctx, status=VoucherStatus.USABLE)
    log_from_ctx(db, EventType.CLAIM, ctx, customer_id=customer_id, commit=False)
    db.commit()
    return voucher


def create_reservation(db, *, campaign: Campaign, customer_id: int, ctx,
                       name, date, time, people, need_room, note) -> tuple[Reservation, Voucher]:
    """预约型活动：生成预约 + 凭证。

    自动确认 → 预约 confirmed、凭证 usable、提交即扣库存。
    人工确认 → 预约 pending、凭证 pending、暂不扣库存（确认时才扣）。
    """
    if not campaign.need_reservation:
        raise ClaimError("该活动无需预约，请直接领取")
    ok, reason = campaign_is_open(campaign)
    if not ok:
        raise ClaimError(reason)
    _check_per_person_limit(db, campaign, customer_id)

    store = db.get(Store, campaign.store_id)
    auto = bool(store and store.auto_confirm)

    if auto:
        if not _decrement_stock(db, campaign.id):
            raise ClaimError("活动名额已抢完")
        res_status = ReservationStatus.CONFIRMED
        vou_status = VoucherStatus.USABLE
    else:
        res_status = ReservationStatus.PENDING
        vou_status = VoucherStatus.PENDING

    reservation = Reservation(
        store_id=campaign.store_id, campaign_id=campaign.id, qr_id=ctx.qr_id,
        customer_id=customer_id, growth_action_id=ctx.growth_action_id,
        channel=ctx.channel, content_no=ctx.content_no, material_no=ctx.material_no,
        name=name, date=date, time=time, people=people, need_room=need_room,
        note=note, status=res_status,
    )
    db.add(reservation)
    db.flush()  # 拿到 reservation.id

    voucher = _new_voucher(db, campaign=campaign, customer_id=customer_id, ctx=ctx,
                           status=vou_status, reservation_id=reservation.id)
    log_from_ctx(db, EventType.CREATE_RESERVATION, ctx, customer_id=customer_id, commit=False)
    if auto:
        log_from_ctx(db, EventType.CONFIRM_RESERVATION, ctx, customer_id=customer_id, commit=False)
    db.commit()
    return reservation, voucher


def confirm_reservation(db, reservation: Reservation, confirmed_by: int) -> Voucher | None:
    """门店确认预约（人工确认场景）：扣库存 + 凭证转 usable + 写确认事件。"""
    if reservation.status != ReservationStatus.PENDING:
        raise ClaimError("该预约无需确认或状态不允许")
    if not _decrement_stock(db, reservation.campaign_id):
        raise ClaimError("库存不足，无法确认")
    transition(reservation, ReservationStatus.CONFIRMED, RESERVATION_TRANSITIONS)
    reservation.confirmed_by = confirmed_by
    voucher = db.query(Voucher).filter_by(reservation_id=reservation.id).first()
    if voucher and voucher.status == VoucherStatus.PENDING:
        transition(voucher, VoucherStatus.USABLE, VOUCHER_TRANSITIONS)
    # 此前人工确认漏写事件 → 漏斗「确认/凭证生效」偏少（自动确认路径有写）
    from ..events import log_event
    log_event(db, event_type=EventType.CONFIRM_RESERVATION,
              store_id=reservation.store_id,
              growth_action_id=reservation.growth_action_id,
              campaign_id=reservation.campaign_id, qr_id=reservation.qr_id,
              customer_id=reservation.customer_id, channel=reservation.channel,
              content_no=reservation.content_no, material_no=reservation.material_no,
              commit=False)
    db.commit()
    return voucher


def cancel_reservation(db, reservation: Reservation) -> None:
    """取消预约：返还库存（若已扣）、凭证作废。"""
    if reservation.status in (ReservationStatus.COMPLETED, ReservationStatus.CANCELLED):
        raise ClaimError("该预约无法取消")
    voucher = db.query(Voucher).filter_by(reservation_id=reservation.id).first()
    # 已确认（库存已扣）才返还
    stock_was_taken = reservation.status == ReservationStatus.CONFIRMED
    transition(reservation, ReservationStatus.CANCELLED, RESERVATION_TRANSITIONS)
    if voucher and voucher.status in (VoucherStatus.USABLE, VoucherStatus.PENDING):
        transition(voucher, VoucherStatus.CANCELLED, VOUCHER_TRANSITIONS)
    if stock_was_taken:
        db.execute(
            update(Campaign)
            .where(Campaign.id == reservation.campaign_id, Campaign.claimed > 0)
            .values(claimed=Campaign.claimed - 1)
        )
    db.commit()
