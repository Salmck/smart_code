"""初始化基础数据（幂等）。

按用户要求保持干净：只创建测试账号、演示门店和一条管理员下发的增长策略。
套餐/活动/二维码/流量数据不预置——由老板端、管理员端按真实流程创建：
  老板建套餐（选策略）→ 建活动（选套餐）→ 提交审核 → 管理员通过 → 自动生成二维码
"""
import logging
from datetime import timedelta

from .models import (
    ActionStatus,
    GrowthAction,
    Role,
    Store,
    StoreMember,
    User,
    now_utc,
)
from .security import hash_password

logger = logging.getLogger("seed")

# 演示账号（README 有说明）：密码 = 用户名 + "-666"
DEMO_ACCOUNTS = [
    ("admin", "admin-666", "平台管理员", Role.ADMIN),
    ("boss", "boss-666", "炳焱私房菜老板", Role.OWNER),
    ("staff", "staff-666", "前厅店员小李", Role.STAFF),
]

# 旧默认密码（用于把已建库中仍是旧默认的账号平滑升级到新密码）
_LEGACY_PASSWORDS = {"admin": "admin123", "boss": "boss123", "staff": "staff123"}


def reconcile_demo_passwords(db):
    """把 admin/boss/staff 的密码统一为「用户名-666」。

    仅当账号当前仍是旧默认密码时才重置，避免覆盖用户后来自行修改过的密码。
    每次启动幂等执行，兼顾全新库与已上线的旧库。
    """
    from .security import verify_password
    changed = []
    for username, new_pwd, _name, _role in DEMO_ACCOUNTS:
        u = db.query(User).filter_by(username=username).first()
        if not u:
            continue
        if verify_password(new_pwd, u.password_hash):
            continue  # 已是新密码
        legacy = _LEGACY_PASSWORDS.get(username)
        if legacy and verify_password(legacy, u.password_hash):
            u.password_hash = hash_password(new_pwd)
            changed.append(username)
    if changed:
        db.commit()
        logger.info("演示账号密码已升级为「用户名-666」：%s", ", ".join(changed))


def seed_all(db):
    if db.query(Store).first():
        return  # 已初始化

    logger.info("首次启动：创建基础账号与门店 …")

    store = Store(name="炳焱私房菜", industry="餐饮",
                  address="无锡市惠山区前洲街道锦绣南路81-82", phone="0510-88888888",
                  auto_confirm=False)
    db.add(store)
    db.flush()

    for username, pwd, name, role in DEMO_ACCOUNTS:
        u = User(username=username, password_hash=hash_password(pwd),
                 display_name=name, role=role)
        db.add(u)
        db.flush()
        if role in (Role.OWNER, Role.STAFF):
            db.add(StoreMember(store_id=store.id, user_id=u.id, role=role))

    # 管理员下发的增长策略（老板建套餐时选用）
    starts = now_utc()
    db.add(GrowthAction(
        store_id=store.id, name="家庭聚餐场景验证",
        problem_type="场景验证", target_customer="周边家庭顾客", target_scene="周末家庭聚餐",
        hypothesis="突出太平湖胖头鱼、家庭包间和家庭套餐，可以提高家庭聚餐预约",
        success_metric="redeem", success_threshold=5, min_sample=20, observe_days=14,
        starts_at=starts, ends_at=starts + timedelta(days=14), status=ActionStatus.RUNNING))

    db.commit()
    logger.info("基础数据就绪：门店=%s（无预置活动，请按流程创建）", store.name)
