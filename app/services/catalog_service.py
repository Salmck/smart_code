"""配置流程服务：门店 / 增长策略 / 套餐 / 活动 / 二维码 的创建与审核。

角色分工：
- 管理员：编辑增长策略并下发到门店；审核活动（不编辑活动/套餐）。
- 老板：管理套餐（选增长策略）与活动（选套餐）；提交活动送审。
"""
from ..models import (
    Campaign,
    CampaignStatus,
    GrowthAction,
    Package,
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


def update_growth_action(db, action: GrowthAction, **fields) -> GrowthAction:
    for k, v in fields.items():
        if hasattr(action, k):
            setattr(action, k, v)
    db.commit()
    return action


# ---------- 套餐（老板管理）----------
def create_package(db, *, store_id, growth_action_id, **fields) -> Package:
    p = Package(store_id=store_id, growth_action_id=growth_action_id, **fields)
    db.add(p)
    db.commit()
    return p


def update_package(db, package: Package, **fields) -> Package:
    for k, v in fields.items():
        if hasattr(package, k):
            setattr(package, k, v)
    db.commit()
    return package


# ---------- 活动（老板创建、管理员审核）----------
def create_campaign(db, *, store_id, package_id, **fields) -> Campaign:
    package = db.get(Package, package_id)
    if not package or package.store_id != store_id:
        raise ValueError("套餐不存在或不属于本门店")
    c = Campaign(store_id=store_id, package_id=package_id,
                 growth_action_id=package.growth_action_id,
                 status=CampaignStatus.DRAFT, **fields)
    db.add(c)
    db.commit()
    return c


def update_campaign(db, campaign: Campaign, **fields) -> Campaign:
    for k, v in fields.items():
        if hasattr(Campaign, k) and not isinstance(getattr(Campaign, k, None), property):
            setattr(campaign, k, v)
    db.commit()
    return campaign


def submit_campaign(db, campaign: Campaign):
    """老板提交送审：草稿/驳回 → 待审核。"""
    transition(campaign, CampaignStatus.PENDING, CAMPAIGN_TRANSITIONS)
    campaign.reject_reason = ""
    db.commit()


def approve_campaign(db, campaign: Campaign, reviewer_id: int) -> int:
    """管理员审核通过：待审核 → 进行中，并生成全部平台二维码。返回新建二维码数。"""
    transition(campaign, CampaignStatus.RUNNING, CAMPAIGN_TRANSITIONS)
    campaign.reviewed_by = reviewer_id
    if not campaign.starts_at:
        campaign.starts_at = now_utc()
    n = generate_all_platform_qrs(db, campaign)  # 通过后二维码才出现在两端页面
    db.commit()
    return n


def reject_campaign(db, campaign: Campaign, reviewer_id: int, reason: str):
    transition(campaign, CampaignStatus.REJECTED, CAMPAIGN_TRANSITIONS)
    campaign.reviewed_by = reviewer_id
    campaign.reject_reason = reason
    db.commit()


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

    # 统一按 EXIF 矫正方向、转 RGB、长边压到 2000px、存 JPEG——
    # 手机原图转 PNG 动辄十几 MB，隧道加载易超时（表现为预览/图片加载失败）
    import io as _io
    from PIL import Image, ImageOps
    try:
        pil = ImageOps.exif_transpose(Image.open(_io.BytesIO(image_bytes))).convert("RGB")
    except Exception:
        raise ValueError("图片无法解析，请换一张")
    MAX_SIDE = 2000
    if max(pil.size) > MAX_SIDE:
        r = MAX_SIDE / max(pil.size)
        pil = pil.resize((max(int(pil.size[0] * r), 1), max(int(pil.size[1] * r), 1)))
    buf = _io.BytesIO()
    pil.save(buf, format="JPEG", quality=88)
    image_bytes = buf.getvalue()

    safe_name = f"ref_c{campaign.id}.jpg"
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
    if campaign.status in (CampaignStatus.DRAFT, CampaignStatus.PENDING,
                           CampaignStatus.REJECTED):
        return False, "活动未开始"
    if campaign.starts_at and now < campaign.starts_at:
        return False, "活动尚未开始"
    if campaign.ends_at and now > campaign.ends_at:
        return False, "活动已结束"
    if campaign.claimed >= campaign.stock:
        return False, "活动名额已抢完"
    return True, ""
