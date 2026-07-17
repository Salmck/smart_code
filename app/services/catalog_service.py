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
              content_no="", material_no="", name="", note="") -> QRPlacement:
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
    db.commit()
    return qr


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
