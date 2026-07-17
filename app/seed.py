"""初始化演示数据（幂等）。

按 PRD 第十五节：炳焱私房菜 + 家庭聚餐场景验证 + 胖头鱼套餐 + 3 个二维码 + 3 测试账号。
每次启动调用，已存在则跳过，不重复插入。
"""
import logging
from datetime import timedelta

from .models import (
    ActionStatus,
    Campaign,
    CampaignStatus,
    GrowthAction,
    Role,
    Store,
    StoreMember,
    User,
    now_utc,
)
from .security import hash_password
from .services.catalog_service import create_qr

logger = logging.getLogger("seed")

# 演示账号（README 会写明）
DEMO_ACCOUNTS = [
    ("admin", "admin123", "平台管理员", Role.ADMIN),
    ("boss", "boss123", "炳焱私房菜老板", Role.OWNER),
    ("staff", "staff123", "前厅店员小李", Role.STAFF),
]


def seed_all(db):
    if db.query(Store).first():
        return  # 已初始化

    logger.info("首次启动：灌入演示数据 …")

    # 门店（人工确认预约，演示 pending 凭证流程）
    store = Store(name="炳焱私房菜", industry="餐饮",
                  address="无锡市惠山区前洲街道锦绣南路81-82", phone="0510-88888888",
                  auto_confirm=False)
    db.add(store)
    db.flush()

    # 账号
    users = {}
    for username, pwd, name, role in DEMO_ACCOUNTS:
        u = User(username=username, password_hash=hash_password(pwd),
                 display_name=name, role=role)
        db.add(u)
        db.flush()
        users[role] = u
        if role in (Role.OWNER, Role.STAFF):
            db.add(StoreMember(store_id=store.id, user_id=u.id, role=role))

    # 增长动作
    starts = now_utc()
    action = GrowthAction(
        store_id=store.id, name="家庭聚餐场景验证",
        problem_type="场景验证", target_customer="周边家庭顾客", target_scene="周末家庭聚餐",
        hypothesis="突出太平湖胖头鱼、家庭包间和家庭套餐，可以提高家庭聚餐预约",
        success_metric="redeem", success_threshold=5, min_sample=20, observe_days=14,
        starts_at=starts, ends_at=starts + timedelta(days=14), status=ActionStatus.RUNNING)
    db.add(action)
    db.flush()

    # 活动（需预约，人工确认）
    campaign = Campaign(
        store_id=store.id, growth_action_id=action.id,
        name="太平湖胖头鱼家庭聚餐套餐", package_title="太平湖胖头鱼家庭聚餐套餐",
        package_desc="招牌太平湖胖头鱼 + 时令家常菜，适合 6-8 人周末家庭聚餐",
        package_content="太平湖胖头鱼一条(约3斤) · 招牌红烧肉 · 时蔬2份 · 主食 · 例汤 · 果盘",
        people="6-8人", original_price=368, price=298, stock=50, claimed=0,
        per_person_limit=1, need_reservation=True,
        usage_rules="每桌限用一张；节假日通用；需提前预约；不与其他优惠同享",
        reservable_dates=[], reservable_times=["午市 11:00-14:00", "晚市 17:00-21:00"],
        voucher_valid_days=14, starts_at=starts, status=CampaignStatus.RUNNING,
        extra={"supports_room": True})
    db.add(campaign)
    db.flush()

    # 三个渠道二维码
    for name, channel, content, material in [
        ("抖音视频01", "抖音", "video_01", "douyin_v01"),
        ("微信社群海报01", "微信社群", "poster_01", "wx_group_p01"),
        ("朋友圈海报02", "微信朋友圈", "poster_02", "moments_p02"),
    ]:
        create_qr(db, store_id=store.id, growth_action_id=action.id,
                  campaign_id=campaign.id, channel=channel, name=name,
                  content_no=content, material_no=material)

    db.commit()
    logger.info("演示数据就绪：门店=%s 活动=%s", store.name, campaign.name)
