"""状态机集中定义。

每个实体的合法流转写成迁移集合，所有状态变更走 transition()，非法流转直接抛错。
扩展新状态（如美容行业「已改期」）只需在集合里加一条，不用全库搜 if。
"""
from .models import CampaignStatus, ReservationStatus, VoucherStatus

VOUCHER_TRANSITIONS = {
    (VoucherStatus.PENDING, VoucherStatus.USABLE),
    (VoucherStatus.PENDING, VoucherStatus.CANCELLED),
    (VoucherStatus.USABLE, VoucherStatus.REDEEMED),
    (VoucherStatus.USABLE, VoucherStatus.CANCELLED),
    (VoucherStatus.USABLE, VoucherStatus.EXPIRED),
    # 冲正：管理员纠错时把已核销恢复为可用
    (VoucherStatus.REDEEMED, VoucherStatus.USABLE),
}

RESERVATION_TRANSITIONS = {
    (ReservationStatus.PENDING, ReservationStatus.CONFIRMED),
    (ReservationStatus.PENDING, ReservationStatus.CANCELLED),
    (ReservationStatus.CONFIRMED, ReservationStatus.COMPLETED),
    (ReservationStatus.CONFIRMED, ReservationStatus.CANCELLED),
    (ReservationStatus.CONFIRMED, ReservationStatus.NO_SHOW),
}

CAMPAIGN_TRANSITIONS = {
    (CampaignStatus.DRAFT, CampaignStatus.RUNNING),
    (CampaignStatus.RUNNING, CampaignStatus.PAUSED),
    (CampaignStatus.PAUSED, CampaignStatus.RUNNING),
    (CampaignStatus.RUNNING, CampaignStatus.ENDED),
    (CampaignStatus.PAUSED, CampaignStatus.ENDED),
    (CampaignStatus.ENDED, CampaignStatus.ARCHIVED),
}


class InvalidTransition(Exception):
    pass


def can_transition(transitions: set, frm: str, to: str) -> bool:
    return frm == to or (frm, to) in transitions


def transition(obj, to_status: str, transitions: set):
    """在内存对象上执行状态流转；非法则抛 InvalidTransition。调用方负责 commit。"""
    frm = obj.status
    if frm == to_status:
        return
    if (frm, to_status) not in transitions:
        raise InvalidTransition(f"非法状态流转: {frm} -> {to_status}")
    obj.status = to_status
