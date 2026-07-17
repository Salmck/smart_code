"""统一事件埋点。

在业务节点调用 log_event() 写入 Event 表。统计模块只读 Event，不 join 业务表。
关键结果（领取/核销）由服务端事务写入，不依赖前端埋点。
"""
from sqlalchemy.exc import IntegrityError

from .models import Event


def log_event(db, *, event_type: str, store_id=None, growth_action_id=None,
              campaign_id=None, qr_id=None, customer_id=None, visitor_id=None,
              session_id=None, channel="", content_no="", material_no="",
              dedup_key=None, extra=None, commit=True):
    """写一条行为事件。dedup_key 非空时靠唯一约束去重（重复静默跳过）。"""
    ev = Event(
        event_type=event_type, store_id=store_id, growth_action_id=growth_action_id,
        campaign_id=campaign_id, qr_id=qr_id, customer_id=customer_id,
        visitor_id=visitor_id, session_id=session_id, channel=channel,
        content_no=content_no, material_no=material_no, dedup_key=dedup_key,
        extra=extra or {},
    )
    db.add(ev)
    if commit:
        try:
            db.commit()
        except IntegrityError:
            db.rollback()  # dedup_key 冲突：该事件已记录，忽略
            return None
    return ev


def log_from_ctx(db, event_type, ctx, **kwargs):
    """用 AttributionContext 便捷写事件。"""
    return log_event(
        db, event_type=event_type,
        store_id=ctx.store_id, growth_action_id=ctx.growth_action_id,
        campaign_id=ctx.campaign_id, qr_id=ctx.qr_id,
        channel=ctx.channel, content_no=ctx.content_no, material_no=ctx.material_no,
        **kwargs,
    )
