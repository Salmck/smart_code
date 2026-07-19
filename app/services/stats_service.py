"""统计分析服务。

只读 Event 表（唯一统计数据源），不 join 业务表算指标。
口径：未验证阶段按匿名 visitor_id 去重（近似值）；验证后人数按 customer_id 去重；
总扫码次数与独立访客分列。分母为 0 一律返回 None，由前端显示「暂无数据」。
"""
from sqlalchemy import distinct, func

from ..models import Event, EventType, Redemption, Voucher


def _apply_filters(q, filters: dict):
    if filters.get("store_id"):
        q = q.filter(Event.store_id == filters["store_id"])
    if filters.get("growth_action_id"):
        q = q.filter(Event.growth_action_id == filters["growth_action_id"])
    if filters.get("campaign_id"):
        q = q.filter(Event.campaign_id == filters["campaign_id"])
    if filters.get("qr_id"):
        q = q.filter(Event.qr_id == filters["qr_id"])
    if filters.get("channel"):
        q = q.filter(Event.channel == filters["channel"])
    if filters.get("content_no"):
        q = q.filter(Event.content_no == filters["content_no"])
    if filters.get("material_no"):
        q = q.filter(Event.material_no == filters["material_no"])
    if filters.get("date_from"):
        q = q.filter(Event.created_at >= filters["date_from"])
    if filters.get("date_to"):
        q = q.filter(Event.created_at <= filters["date_to"])
    return q


def _count(db, filters, event_type, distinct_col=None):
    if distinct_col is not None:
        q = db.query(func.count(distinct(distinct_col)))
    else:
        q = db.query(func.count(Event.id))
    q = q.filter(Event.event_type == event_type)
    q = _apply_filters(q, filters)
    return q.scalar() or 0


def _count_in(db, filters, event_types, distinct_col):
    """多事件类型取并集去重（如「领取或预约」按顾客去重只算一次）。"""
    q = db.query(func.count(distinct(distinct_col)))
    q = q.filter(Event.event_type.in_(event_types))
    q = _apply_filters(q, filters)
    return q.scalar() or 0


def _rate(numerator, denominator):
    """分母为 0 返回 None（前端显示「暂无数据」），否则返回百分比浮点。"""
    if not denominator:
        return None
    return round(numerator / denominator * 100, 1)


def funnel(db, filters: dict) -> dict:
    """漏斗口径（避免「领取0夹在中间」「浏览=扫码」这类看着不对的展示）：
    扫码(独立访客) → 点击参与(领取/预约按钮，访客并集去重) → 验证手机号(顾客)
    → 提交(领取∪预约，顾客并集) → 凭证生效(领取∪确认预约) → 实际核销
    """
    total_scan = _count(db, filters, EventType.SCAN)
    unique_scan = _count(db, filters, EventType.SCAN, Event.visitor_id)
    view = _count(db, filters, EventType.VIEW_CAMPAIGN, Event.visitor_id)
    click = _count_in(db, filters, [EventType.CLICK_CLAIM, EventType.CLICK_RESERVE],
                      Event.visitor_id)
    verify = _count(db, filters, EventType.VERIFY_PHONE, Event.customer_id)
    claim = _count(db, filters, EventType.CLAIM, Event.customer_id)
    reserve = _count(db, filters, EventType.CREATE_RESERVATION, Event.customer_id)
    confirm = _count(db, filters, EventType.CONFIRM_RESERVATION, Event.customer_id)
    submit = _count_in(db, filters, [EventType.CLAIM, EventType.CREATE_RESERVATION],
                       Event.customer_id)
    effective = _count_in(db, filters, [EventType.CLAIM, EventType.CONFIRM_RESERVATION],
                          Event.customer_id)
    redeem = _count(db, filters, EventType.REDEEM, Event.customer_id)

    # 核销金额：Redemption 联 Voucher 取归因字段，与其余指标同口径过滤
    amount_q = (db.query(func.coalesce(func.sum(Redemption.amount), 0))
                .join(Voucher, Voucher.id == Redemption.voucher_id)
                .filter(Redemption.is_reversal == False))  # noqa: E712
    if filters.get("store_id"):
        amount_q = amount_q.filter(Voucher.store_id == filters["store_id"])
    if filters.get("growth_action_id"):
        amount_q = amount_q.filter(Voucher.growth_action_id == filters["growth_action_id"])
    if filters.get("campaign_id"):
        amount_q = amount_q.filter(Voucher.campaign_id == filters["campaign_id"])
    if filters.get("qr_id"):
        amount_q = amount_q.filter(Voucher.qr_id == filters["qr_id"])
    if filters.get("channel"):
        amount_q = amount_q.filter(Voucher.channel == filters["channel"])
    redeem_amount = amount_q.scalar() or 0

    return {
        "total_scan": total_scan,
        "unique_scan": unique_scan,
        "repeat_scan": max(total_scan - unique_scan, 0),
        "view": view,
        "click": click,
        "verify": verify,
        "claim": claim,
        "reserve": reserve,
        "confirm": confirm,
        "submit": submit,          # 提交领取/预约（并集去重）
        "effective": effective,    # 凭证生效（领取 + 已确认预约）
        "redeem": redeem,
        "redeem_amount": redeem_amount,
        # 转化率（分母 0 → None）
        "rate_scan_to_submit": _rate(submit, unique_scan),
        "rate_submit_to_redeem": _rate(redeem, submit),
        "rate_scan_to_reserve": _rate(reserve, unique_scan),
        "rate_reserve_to_redeem": _rate(redeem, reserve),
        "rate_scan_to_redeem": _rate(redeem, unique_scan),
    }


def breakdown_by(db, filters: dict, dimension: str) -> list[dict]:
    """按维度（channel / content_no / material_no）分组统计扫码/预约/核销。"""
    col = {"channel": Event.channel, "content_no": Event.content_no,
           "material_no": Event.material_no}[dimension]

    def grouped(event_type, distinct_col):
        q = (db.query(col, func.count(distinct(distinct_col)))
             .filter(Event.event_type == event_type, col != ""))
        q = _apply_filters(q, filters)
        return dict(q.group_by(col).all())

    scans = grouped(EventType.SCAN, Event.visitor_id)
    reserves = grouped(EventType.CREATE_RESERVATION, Event.customer_id)
    redeems = grouped(EventType.REDEEM, Event.customer_id)

    keys = set(scans) | set(reserves) | set(redeems)
    rows = []
    for k in keys:
        s = scans.get(k, 0)
        rows.append({
            "key": k,
            "scan": s,
            "reserve": reserves.get(k, 0),
            "redeem": redeems.get(k, 0),
            "rate_scan_to_redeem": _rate(redeems.get(k, 0), s),
        })
    rows.sort(key=lambda r: r["scan"], reverse=True)
    return rows
