"""数据模型 —— Ordinex 全部数据表。

设计要点（对应讨论定稿）：
- 归因字段（store/action/campaign/qr/channel/content/material）在 Voucher / Reservation /
  Event 上冗余存储，不靠 join 回溯，保证「归因链不能断」。
- 状态一律用字符串常量（非 DB Enum），便于跨行业扩展和 SQLite/PG 通用。
- 全部软删除：用 status 字段表达失效，不物理删除。
- 行业差异走 extra(JSON)，Campaign 只保留通用字段。
"""
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from .db import Base


def now_utc() -> datetime:
    # 返回 naive UTC：SQLite 的 DateTime 列不保留时区，统一用 naive 避免
    # aware/naive 混比报错（尤其核销时的 expires_at > now 判断）。
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ===================== 状态常量 =====================
class Role:
    ADMIN = "admin"      # 平台管理员
    OWNER = "owner"      # 门店老板
    MANAGER = "manager"  # 店长
    STAFF = "staff"      # 店员
    CUSTOMER = "customer"  # 顾客（消费者身份，不进 User 表）


class ActionStatus:      # 增长动作·执行状态
    DRAFT = "draft"
    RUNNING = "running"
    OBSERVED = "observed"     # 观察期结束
    ARCHIVED = "archived"


class ActionResult:      # 增长动作·实验结果（与执行状态分开）
    UNDECIDED = "undecided"
    SUCCESS = "success"
    FAILED = "failed"
    INSUFFICIENT = "insufficient"  # 样本不足


class CampaignStatus:
    DRAFT = "draft"          # 草稿（老板编辑中）
    PENDING = "pending"      # 待审核（老板已提交，等管理员审核）
    REJECTED = "rejected"    # 已驳回
    RUNNING = "running"      # 进行中（审核通过并生效）
    PAUSED = "paused"
    ENDED = "ended"
    ARCHIVED = "archived"


CAMPAIGN_STATUS_LABEL = {
    "draft": "草稿", "pending": "待审核", "rejected": "已驳回",
    "running": "进行中", "paused": "已暂停", "ended": "已结束", "archived": "已归档",
}


class QRStatus:
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class ReservationStatus:
    PENDING = "pending"        # 待确认
    CONFIRMED = "confirmed"    # 已确认
    COMPLETED = "completed"    # 已完成（到店核销后）
    CANCELLED = "cancelled"    # 已取消
    NO_SHOW = "no_show"        # 未到店


class VoucherStatus:
    PENDING = "pending"        # 待生效（人工确认预约前）
    USABLE = "usable"          # 可使用
    REDEEMED = "redeemed"      # 已核销
    EXPIRED = "expired"        # 已过期
    CANCELLED = "cancelled"    # 已取消


class EventType:
    SCAN = "scan"                      # 扫描二维码
    VIEW_CAMPAIGN = "view_campaign"    # 查看套餐页
    CLICK_CLAIM = "click_claim"        # 点击领取
    CLICK_RESERVE = "click_reserve"    # 点击预约
    SEND_CODE = "send_code"            # 发送验证码
    VERIFY_PHONE = "verify_phone"      # 验证手机号
    CLAIM = "claim"                    # 领取优惠
    CREATE_RESERVATION = "create_reservation"  # 创建预约
    CONFIRM_RESERVATION = "confirm_reservation"  # 确认预约
    OPEN_VOUCHER = "open_voucher"      # 打开核销凭证
    VERIFY_VOUCHER = "verify_voucher"  # 验证核销凭证
    REDEEM = "redeem"                  # 完成核销
    CANCEL_RESERVATION = "cancel_reservation"  # 取消预约


# 渠道选项（PRD 流程三）
CHANNELS = [
    "抖音", "小红书", "微信朋友圈", "微信社群", "公众号", "视频号",
    "美团", "大众点评", "线下海报", "桌贴", "老顾客转介绍", "其他",
]


# ===================== 组织与身份 =====================
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, nullable=False)
    password_hash = Column(String(256), nullable=False)
    display_name = Column(String(64), nullable=False, default="")
    role = Column(String(16), nullable=False)          # admin/owner/manager/staff
    status = Column(String(16), nullable=False, default="active")  # active/disabled
    created_at = Column(DateTime, default=now_utc)


class Store(Base):
    __tablename__ = "stores"
    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False)
    industry = Column(String(32), nullable=False, default="餐饮")
    address = Column(String(256), default="")
    phone = Column(String(256), default="")            # 可多个，逗号/换行分隔
    auto_confirm = Column(Boolean, default=False)      # 预约是否自动确认
    status = Column(String(16), nullable=False, default="active")  # active/suspended
    created_at = Column(DateTime, default=now_utc)

    @property
    def phone_list(self) -> list[str]:
        """拆出所有联系电话（顾客端逐个渲染呼叫按钮）。"""
        import re
        return [p.strip() for p in re.split(r"[,，;；\n]+", self.phone or "") if p.strip()]


class StoreMember(Base):
    __tablename__ = "store_members"
    __table_args__ = (UniqueConstraint("store_id", "user_id", name="uq_store_user"),)
    id = Column(Integer, primary_key=True)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    role = Column(String(16), nullable=False)          # owner/manager/staff
    status = Column(String(16), nullable=False, default="active")  # active/disabled
    created_at = Column(DateTime, default=now_utc)


class Customer(Base):
    """顾客消费者身份。手机号加密存储 + 哈希用于唯一识别与查询，后台默认脱敏展示。"""
    __tablename__ = "customers"
    id = Column(Integer, primary_key=True)
    name = Column(String(64), default="")
    phone = Column(String(32), nullable=False)         # 明文存储，展示时脱敏；生产可加密
    phone_hash = Column(String(64), unique=True, nullable=False)  # 唯一识别值
    first_seen = Column(DateTime, default=now_utc)
    last_seen = Column(DateTime, default=now_utc)
    created_at = Column(DateTime, default=now_utc)


# ===================== 策略与活动 =====================
class GrowthAction(Base):
    __tablename__ = "growth_actions"
    id = Column(Integer, primary_key=True)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False)
    name = Column(String(128), nullable=False)
    problem_type = Column(String(128), default="")     # 增长问题类型
    hypothesis = Column(Text, default="")              # 核心假设
    target_customer = Column(String(128), default="")  # 目标顾客
    target_scene = Column(String(128), default="")     # 消费场景
    success_metric = Column(String(32), default="redeem")  # 成功指标：scan/reserve/redeem
    success_threshold = Column(Integer, default=0)     # 成功阈值
    min_sample = Column(Integer, default=0)            # 最低有效样本量（独立扫码）
    observe_days = Column(Integer, default=14)         # 观察周期
    starts_at = Column(DateTime, nullable=True)
    ends_at = Column(DateTime, nullable=True)
    status = Column(String(16), default=ActionStatus.DRAFT)   # 执行状态
    result = Column(String(16), default=ActionResult.UNDECIDED)  # 实验结果（分开）
    created_at = Column(DateTime, default=now_utc)


class Package(Base):
    """套餐（由门店老板管理）。绑定一个增长策略，承载套餐内容与价格。

    一个套餐可被多个活动复用；活动只引用套餐、不重复编辑套餐内容。
    """
    __tablename__ = "packages"
    id = Column(Integer, primary_key=True)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False)
    growth_action_id = Column(Integer, ForeignKey("growth_actions.id"), nullable=False)
    package_title = Column(String(128), default="")    # 套餐标题
    package_desc = Column(Text, default="")            # 套餐介绍
    package_content = Column(Text, default="")         # 套餐包含内容
    main_image = Column(String(256), default="")       # 套餐主图 URL
    detail_images = Column(JSON, default=list)         # 详情图 URL 列表
    people = Column(String(32), default="")            # 适用人数（文本，如 6-8人）
    original_price = Column(Float, default=0)          # 原价
    price = Column(Float, default=0)                   # 活动价
    usage_rules = Column(Text, default="")             # 使用规则
    extra = Column(JSON, default=dict)                 # 行业特有字段（如是否包间）
    created_at = Column(DateTime, default=now_utc)


class Campaign(Base):
    """活动（由门店老板创建、管理员审核）。引用一个套餐 + 活动级规则 + 审核状态。"""
    __tablename__ = "campaigns"
    id = Column(Integer, primary_key=True)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False)
    package_id = Column(Integer, ForeignKey("packages.id"), nullable=False)
    growth_action_id = Column(Integer, ForeignKey("growth_actions.id"), nullable=False)  # 冗余便于归因
    name = Column(String(128), nullable=False)
    stock = Column(Integer, default=0)                 # 库存总量
    claimed = Column(Integer, default=0)               # 已领取/已占用数量
    per_person_limit = Column(Integer, default=1)      # 每人领取限制
    need_reservation = Column(Boolean, default=False)  # 是否需要预约（决定唯一入口）
    reservable_dates = Column(JSON, default=list)      # 可预约日期
    reservable_times = Column(JSON, default=list)      # 可预约时间段
    voucher_valid_days = Column(Integer, default=14)   # 凭证有效期（领取后 N 天）
    starts_at = Column(DateTime, nullable=True)
    ends_at = Column(DateTime, nullable=True)
    status = Column(String(16), default=CampaignStatus.DRAFT)
    reject_reason = Column(String(256), default="")    # 审核驳回原因
    reviewed_by = Column(Integer, nullable=True)       # 审核人 user_id
    ref_image = Column(String(256), default="")        # 参考图（海报）文件名
    ref_qr_box = Column(JSON, nullable=True)           # 识别到的二维码区域 [x,y,w,h]
    created_at = Column(DateTime, default=now_utc)

    package = relationship("Package", lazy="joined")

    # ---- 代理属性：让顾客端模板/服务沿用 campaign.价格/套餐字段，无需改动 ----
    @property
    def package_title(self):
        return self.package.package_title if self.package else ""

    @property
    def package_desc(self):
        return self.package.package_desc if self.package else ""

    @property
    def package_content(self):
        return self.package.package_content if self.package else ""

    @property
    def main_image(self):
        return self.package.main_image if self.package else ""

    @property
    def detail_images(self):
        return self.package.detail_images if self.package else []

    @property
    def people(self):
        return self.package.people if self.package else ""

    @property
    def original_price(self):
        return self.package.original_price if self.package else 0

    @property
    def price(self):
        return self.package.price if self.package else 0

    @property
    def usage_rules(self):
        return self.package.usage_rules if self.package else ""

    @property
    def extra(self):
        return self.package.extra if self.package else {}


class QRPlacement(Base):
    """二维码投放记录。本质是投放记录，二维码图片只是其视觉载体。短码永久不变。"""
    __tablename__ = "qr_placements"
    id = Column(Integer, primary_key=True)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False)
    growth_action_id = Column(Integer, ForeignKey("growth_actions.id"), nullable=False)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=False)
    short_code = Column(String(16), unique=True, nullable=False)  # 随机短码，不含业务ID
    channel = Column(String(32), nullable=False)       # 渠道
    content_no = Column(String(64), default="")        # 内容编号
    material_no = Column(String(64), default="")       # 素材编号
    name = Column(String(128), default="")             # 二维码名称
    note = Column(String(256), default="")             # 投放说明
    status = Column(String(16), default=QRStatus.ACTIVE)
    created_at = Column(DateTime, default=now_utc)


# ===================== 交易式业务 =====================
class Reservation(Base):
    __tablename__ = "reservations"
    id = Column(Integer, primary_key=True)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=False)
    qr_id = Column(Integer, ForeignKey("qr_placements.id"), nullable=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    # 归因冗余
    growth_action_id = Column(Integer, nullable=True)
    channel = Column(String(32), default="")
    content_no = Column(String(64), default="")
    material_no = Column(String(64), default="")
    # 预约内容
    name = Column(String(64), default="")
    date = Column(String(16), default="")              # 到店日期 YYYY-MM-DD
    time = Column(String(16), default="")              # 到店时间
    people = Column(Integer, default=1)                # 用餐人数
    need_room = Column(Boolean, default=False)         # 是否需要包间
    note = Column(String(256), default="")             # 顾客备注
    status = Column(String(16), default=ReservationStatus.PENDING)
    confirmed_by = Column(Integer, nullable=True)      # 确认人 user_id
    created_at = Column(DateTime, default=now_utc)


class Voucher(Base):
    __tablename__ = "vouchers"
    id = Column(Integer, primary_key=True)
    code = Column(String(32), unique=True, nullable=False)      # 对外随机编号
    backup_code = Column(String(16), nullable=False)           # 备用数字核销码
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=False)
    reservation_id = Column(Integer, ForeignKey("reservations.id"), nullable=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    qr_id = Column(Integer, ForeignKey("qr_placements.id"), nullable=True)
    # 归因冗余（末触归因）
    growth_action_id = Column(Integer, nullable=True)
    channel = Column(String(32), default="")
    content_no = Column(String(64), default="")
    material_no = Column(String(64), default="")
    status = Column(String(16), default=VoucherStatus.USABLE)
    expires_at = Column(DateTime, nullable=False)
    redeemed_at = Column(DateTime, nullable=True)
    redeemed_by = Column(Integer, nullable=True)               # 核销店员 user_id
    created_at = Column(DateTime, default=now_utc)


class Redemption(Base):
    """核销记录。voucher_id 与 request_id 双唯一约束 → 数据库层保证一凭证一条成功核销。"""
    __tablename__ = "redemptions"
    __table_args__ = (
        UniqueConstraint("voucher_id", "is_reversal", name="uq_voucher_redemption"),
        UniqueConstraint("request_id", name="uq_request_id"),
    )
    id = Column(Integer, primary_key=True)
    voucher_id = Column(Integer, ForeignKey("vouchers.id"), nullable=False)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False)
    staff_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    amount = Column(Float, default=0)                  # 核销金额（= 活动价）
    request_id = Column(String(64), unique=True, nullable=False)  # 幂等标识
    is_reversal = Column(Boolean, default=False)       # 是否冲正记录（管理员纠错用）
    note = Column(String(256), default="")
    created_at = Column(DateTime, default=now_utc)


# ===================== 归因事件 =====================
class Event(Base):
    """行为事件表 —— 增长数据闭环的核心，统计模块唯一数据源。"""
    __tablename__ = "events"
    id = Column(Integer, primary_key=True)
    event_type = Column(String(32), nullable=False)
    store_id = Column(Integer, nullable=True)
    growth_action_id = Column(Integer, nullable=True)
    campaign_id = Column(Integer, nullable=True)
    qr_id = Column(Integer, nullable=True)
    customer_id = Column(Integer, nullable=True)
    visitor_id = Column(String(64), nullable=True)     # 匿名访客 ID
    session_id = Column(String(64), nullable=True)     # 会话 ID
    channel = Column(String(32), default="")
    content_no = Column(String(64), default="")
    material_no = Column(String(64), default="")
    dedup_key = Column(String(128), unique=True, nullable=True)  # 去重标识（可空）
    extra = Column(JSON, default=dict)
    created_at = Column(DateTime, default=now_utc)


# ===================== 系统治理 =====================
class AuditLog(Base):
    """审计日志 —— 只记录后台人员的管理操作（与顾客行为 Event 分离）。"""
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=True)
    role = Column(String(16), default="")
    store_id = Column(Integer, nullable=True)
    action = Column(String(64), nullable=False)        # 操作类型
    target = Column(String(128), default="")           # 操作对象
    before = Column(JSON, nullable=True)               # 修改前
    after = Column(JSON, nullable=True)                # 修改后
    created_at = Column(DateTime, default=now_utc)
