"""共享的 Jinja2 模板环境（含自定义过滤器）。"""
from fastapi.templating import Jinja2Templates

from .config import settings
from .utils import mask_phone

templates = Jinja2Templates(directory="templates")


def _yuan(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    return f"{f:.0f}" if f == int(f) else f"{f:.2f}"


def _dt(v, fmt="%Y-%m-%d %H:%M"):
    if not v:
        return ""
    try:
        return v.strftime(fmt)
    except AttributeError:
        return str(v)


def _pct(v):
    """转化率显示：None → 暂无数据。"""
    return "暂无数据" if v is None else f"{v}%"


templates.env.filters["mask_phone"] = mask_phone
templates.env.filters["yuan"] = _yuan
templates.env.filters["dt"] = _dt
templates.env.filters["pct"] = _pct

# 品牌常量注入所有模板
templates.env.globals.update(
    BRAND_CN="无界序",
    BRAND_EN="Ordinex",
    PRODUCT_NAME="Ordinex 专属增长码",
    PUBLIC_BASE_URL=settings.PUBLIC_BASE_URL,
)
