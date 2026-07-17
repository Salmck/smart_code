"""统计分析服务。

只读 Event 表（唯一统计数据源），不 join 业务表算指标。
口径：未验证阶段按匿名 visitor_id 去重（近似值）；验证后人数按 customer_id 去重；
总扫码次数与独立访客分列。分母为 0 一律返回 None，由前端显示「暂无数据」。
"""
from sqlalchemy import distinct, func

from ..models import Event, EventType, Redemption


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


def _rate(numerator, denominator):
    """分母为 0 返回 None（前端显示「暂无数据」），否则返回百分比浮点。"""
    if not denominator:
        return None
    return round(numerator / denominator * 100, 1)


def funnel(db, filters: dict) -> dict:
    total_scan = _count(db, filters, EventType.SCAN)
    unique_scan = _count(db, filters, EventType.SCAN, Event.visitor_id)
    view = _count(db, filters, EventType.VIEW_CAMPAIGN, Event.visitor_id)
    click = (_count(db, filters, EventType.CLICK_CLAIM, Event.visitor_id)
             + _count(db, filters, EventType.CLICK_RESERVE, Event.visitor_id))
    verify = _count(db, filters, EventType.VERIFY_PHONE, Event.customer_id)
    claim = _count(db, filters, EventType.CLAIM, Event.customer_id)
    reserve = _count(db, filters, EventType.CREATE_RESERVATION, Event.customer_id)
    confirm = _count(db, filters, EventType.CONFIRM_RESERVATION, Event.customer_id)
    redeem = _count(db, filters, EventType.REDEEM, Event.customer_id)

    # 核销金额：从 Redemption 汇总（非冲正）
    amount_q = db.query(func.coalesce(func.sum(Redemption.amount), 0)).filter(
        Redemption.is_reversal == False  # noqa: E712
    )
    if filters.get("store_id"):
        amount_q = amount_q.filter(Redemption.store_id == filters["store_id"])
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
        "redeem": redeem,
        "redeem_amount": redeem_amount,
        # 转化率（分母 0 → None）
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
