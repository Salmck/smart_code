"""审计日志。

记录后台人员的管理操作（核销、改库存/活动/价格、暂停活动、改预约、禁用店员、冲正等）。
与顾客行为 Event 严格分离。
"""
from .models import AuditLog


def audit(db, *, user_id, role, store_id, action, target="",
          before=None, after=None, commit=True):
    log = AuditLog(
        user_id=user_id, role=role, store_id=store_id, action=action,
        target=target, before=before, after=after,
    )
    db.add(log)
    if commit:
        db.commit()
    return log
