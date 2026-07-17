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

    seed_demo_traffic(db, store, action, campaign)
    logger.info("演示数据就绪：门店=%s 活动=%s", store.name, campaign.name)


def seed_demo_traffic(db, store, action, campaign):
    """生成演示流量（扫码/浏览/预约/核销事件 + 少量真实凭证），让仪表盘有真实数据。"""
    import random
    from datetime import timedelta

    from .models import (
        Customer, Event, EventType, QRPlacement, Redemption, Role, User, Voucher,
        VoucherStatus,
    )
    from .shortcode import gen_digits, gen_shortcode
    from .utils import phone_hash

    staff_user = db.query(User).filter_by(role=Role.STAFF).first()
    staff_id = staff_user.id if staff_user else 1
    qrs = db.query(QRPlacement).filter_by(campaign_id=campaign.id).all()
    # 每个渠道的独立扫码量与逐级转化率（条件概率，制造渠道差异）
    plan = {
        "抖音": dict(scans=156, view=.86, click=.44, verify=.52, reserve=.50, redeem=.62),
        "微信朋友圈": dict(scans=234, view=.82, click=.40, verify=.48, reserve=.46, redeem=.60),
        "微信社群": dict(scans=89, view=.90, click=.55, verify=.62, reserve=.64, redeem=.75),
    }
    now = now_utc()

    def ev(t, qr, vid=None, cid=None):
        db.add(Event(event_type=t, store_id=store.id, growth_action_id=action.id,
                     campaign_id=campaign.id, qr_id=qr.id, visitor_id=vid, customer_id=cid,
                     channel=qr.channel, content_no=qr.content_no, material_no=qr.material_no,
                     created_at=now - timedelta(hours=random.randint(0, 240))))

    cust_seq = 0
    for qr in qrs:
        p = plan.get(qr.channel)
        if not p:
            continue
        for i in range(p["scans"]):
            vid = f"v{qr.id}_{i}"
            ev(EventType.SCAN, qr, vid=vid)
            if random.random() < 0.18:          # 部分重复扫码
                ev(EventType.SCAN, qr, vid=vid)
            if random.random() >= p["view"]:
                continue
            ev(EventType.VIEW_CAMPAIGN, qr, vid=vid)
            if random.random() >= p["click"]:
                continue
            ev(EventType.CLICK_RESERVE, qr, vid=vid)
            if random.random() >= p["verify"]:
                continue
            # 验证手机号 → 建演示顾客
            cust_seq += 1
            phone = "138" + str(10000000 + cust_seq)
            customer = Customer(name="", phone=phone, phone_hash=phone_hash(phone),
                                first_seen=now, last_seen=now)
            db.add(customer)
            db.flush()
            ev(EventType.VERIFY_PHONE, qr, vid=vid, cid=customer.id)
            if random.random() >= p["reserve"]:
                continue
            ev(EventType.CREATE_RESERVATION, qr, vid=vid, cid=customer.id)
            ev(EventType.CONFIRM_RESERVATION, qr, vid=vid, cid=customer.id)
            if random.random() >= p["redeem"]:
                continue
            # 真实核销：建凭证 + 核销记录（让核销金额/核销记录页有数据）
            voucher = Voucher(
                code=gen_shortcode(8), backup_code=gen_digits(8), store_id=store.id,
                campaign_id=campaign.id, customer_id=customer.id, qr_id=qr.id,
                growth_action_id=action.id, channel=qr.channel, content_no=qr.content_no,
                status=VoucherStatus.REDEEMED, expires_at=now + timedelta(days=14),
                redeemed_at=now, redeemed_by=None)
            db.add(voucher)
            db.flush()
            voucher.redeemed_by = staff_id
            db.add(Redemption(voucher_id=voucher.id, store_id=store.id, staff_id=staff_id,
                              amount=campaign.price, request_id=f"seed-{voucher.id}"))
            ev(EventType.REDEEM, qr, vid=vid, cid=customer.id)
    db.commit()
