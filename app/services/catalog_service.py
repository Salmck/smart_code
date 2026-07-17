"""配置流程服务：门店 / 增长动作 / 活动 / 二维码投放的创建与管理。"""
from ..models import (
    Campaign,
    CampaignStatus,
    GrowthAction,
    QRPlacement,
    QRStatus,
    Store,
    now_utc,
)
from ..shortcode import gen_shortcode
from ..config import settings
from ..statemachine import CAMPAIGN_TRANSITIONS, transition


def create_store(db, *, name, industry="餐饮", address="", phone="", auto_confirm=False) -> Store:
    s = Store(name=name, industry=industry, address=address, phone=phone,
              auto_confirm=auto_confirm)
    db.add(s)
    db.commit()
    return s


def create_growth_action(db, *, store_id, **fields) -> GrowthAction:
    action = GrowthAction(store_id=store_id, **fields)
    db.add(action)
    db.commit()
    return action


def create_campaign(db, *, store_id, growth_action_id, **fields) -> Campaign:
    c = Campaign(store_id=store_id, growth_action_id=growth_action_id, **fields)
    db.add(c)
    db.commit()
    return c


def create_qr(db, *, store_id, growth_action_id, campaign_id, channel,
              content_no="", material_no="", name="", note="", commit=True) -> QRPlacement:
    """生成专属增长码。短码随机且唯一，永久不变。"""
    for _ in range(10):
        code = gen_shortcode(settings.SHORTCODE_LENGTH)
        if not db.query(QRPlacement).filter_by(short_code=code).first():
            break
    else:
        raise RuntimeError("短码生成冲突，请重试")
    qr = QRPlacement(
        store_id=store_id, growth_action_id=growth_action_id, campaign_id=campaign_id,
        short_code=code, channel=channel, content_no=content_no,
        material_no=material_no, name=name, note=note,
    )
    db.add(qr)
    if commit:
        db.commit()
    return qr


def generate_all_platform_qrs(db, campaign) -> int:
    """为活动一次性生成全部平台的二维码（已存在的平台跳过）。返回新建数量。"""
    from ..platforms import PLATFORMS
    existing = {q.channel for q in
                db.query(QRPlacement).filter_by(campaign_id=campaign.id).all()}
    created = 0
    for platform, _color in PLATFORMS:
        if platform in existing:
            continue
        create_qr(db, store_id=campaign.store_id,
                  growth_action_id=campaign.growth_action_id, campaign_id=campaign.id,
                  channel=platform, name=f"{campaign.package_title or campaign.name}·{platform}",
                  commit=False)
        created += 1
    db.commit()
    return created


def set_campaign_reference(db, campaign, image_bytes: bytes, filename: str) -> dict:
    """保存活动参考图（海报）并识别其中二维码区域，供下载时把真码替换进去。"""
    import os
    from ..qrgen import detect_qr_box

    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    ext = os.path.splitext(filename)[1].lower() or ".png"
    if ext not in (".png", ".jpg", ".jpeg", ".webp"):
        raise ValueError("仅支持 PNG/JPG/WEBP 图片")
    safe_name = f"ref_c{campaign.id}{ext}"
    path = os.path.join(settings.UPLOAD_DIR, safe_name)
    with open(path, "wb") as f:
        f.write(image_bytes)

    box = detect_qr_box(image_bytes)
    campaign.ref_image = safe_name
    campaign.ref_qr_box = list(box) if box else None
    db.commit()
    return {"saved": safe_name, "detected": box is not None, "box": box}


def set_campaign_status(db, campaign: Campaign, to_status: str):
    transition(campaign, to_status, CAMPAIGN_TRANSITIONS)
    db.commit()


def campaign_is_open(campaign: Campaign) -> tuple[bool, str]:
    """活动当前是否可参与（顾客侧）。返回 (是否可用, 不可用原因)。"""
    now = now_utc()
    if campaign.status == CampaignStatus.PAUSED:
        return False, "活动已暂停"
    if campaign.status in (CampaignStatus.ENDED, CampaignStatus.ARCHIVED):
        return False, "活动已结束"
    if campaign.status == CampaignStatus.DRAFT:
        return False, "活动未开始"
    if campaign.starts_at and now < campaign.starts_at:
        return False, "活动尚未开始"
    if campaign.ends_at and now > campaign.ends_at:
        return False, "活动已结束"
    if campaign.claimed >= campaign.stock:
        return False, "活动名额已抢完"
    return True, ""
