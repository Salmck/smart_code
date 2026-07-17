"""归因上下文值对象。

扫码时从短码解析出完整归因上下文，之后领取/预约/每条事件整体携带并冗余落库，
不靠 join 回溯——这是「归因链不能断」的结构性保证。
归因规则（末触归因）如需调整，只改此处的解析逻辑，下游全部不动。
"""
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class AttributionContext:
    store_id: int
    growth_action_id: int | None = None
    campaign_id: int | None = None
    qr_id: int | None = None
    channel: str = ""
    content_no: str = ""
    material_no: str = ""

    @classmethod
    def from_qr(cls, qr) -> "AttributionContext":
        return cls(
            store_id=qr.store_id,
            growth_action_id=qr.growth_action_id,
            campaign_id=qr.campaign_id,
            qr_id=qr.id,
            channel=qr.channel,
            content_no=qr.content_no or "",
            material_no=qr.material_no or "",
        )

    def as_dict(self) -> dict:
        return asdict(self)
