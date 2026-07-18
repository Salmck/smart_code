"""二维码文件下载（PNG / SVG / 透明底 / 带文字版），管理员或本店管理者可用。"""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

import os

from ..config import settings
from ..db import get_db
from ..deps import AuthUser, assert_store_access, require_staff
from ..models import Campaign, QRPlacement
from ..platforms import color_of
from ..qrgen import (
    composite_into_reference,
    png_bytes,
    png_with_caption,
    png_with_platform,
    qr_url,
    svg_bytes,
)

router = APIRouter(prefix="/qr")


# ---------- 参考图二维码区域校准（须在 /{qr_id}/{fmt} 之前注册）----------
@router.get("/ref/{campaign_id}")
def ref_edit_page(campaign_id: int, request: Request,
                  user: AuthUser = Depends(require_staff), db=Depends(get_db)):
    """手动校准海报中二维码替换区域：拖动/缩放选框，保证完整覆盖原码。"""
    from ..render import templates
    campaign = db.get(Campaign, campaign_id)
    if not campaign or not campaign.ref_image:
        raise HTTPException(status_code=404, detail="该活动未上传参考图")
    assert_store_access(user, campaign.store_id)
    first_qr = db.query(QRPlacement).filter_by(campaign_id=campaign.id).first()
    back = "/admin/qrs" if user.role == "admin" else "/owner/qrs"
    return templates.TemplateResponse(
        "console/qr_ref_edit.html",
        {"request": request, "user": user, "campaign": campaign,
         "box": campaign.ref_qr_box or None,
         "first_qr_id": first_qr.id if first_qr else None, "back": back})


@router.post("/ref/{campaign_id}/box")
def ref_save_box(campaign_id: int, user: AuthUser = Depends(require_staff),
                 db=Depends(get_db), x: int = 0, y: int = 0, w: int = 0, h: int = 0):
    from ..audit import audit
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="活动不存在")
    assert_store_access(user, campaign.store_id)
    if w < 20 or h < 20:
        raise HTTPException(status_code=400, detail="区域太小")
    before = campaign.ref_qr_box
    # 第 5 位标记 manual：手动框已含白边，合成时只做小幅外扩
    campaign.ref_qr_box = [int(x), int(y), int(w), int(h), "manual"]
    db.commit()
    audit(db, user_id=user.id, role=user.role, store_id=campaign.store_id,
          action="adjust_ref_box", target=f"campaign:{campaign.id}",
          before={"box": before}, after={"box": campaign.ref_qr_box})
    return {"ok": True, "box": campaign.ref_qr_box}


@router.get("/{qr_id}/{fmt}")
def download(qr_id: int, fmt: str, request: Request,
             user: AuthUser = Depends(require_staff), db=Depends(get_db)):
    qr = db.get(QRPlacement, qr_id)
    if not qr:
        raise HTTPException(status_code=404, detail="二维码不存在")
    assert_store_access(user, qr.store_id)

    # 优先用配置的公网地址（隧道/域名），否则回退到请求 Host；
    # 非本机访问一律升级为 https（微信/摄像头扫码均要求 https）
    base = settings.PUBLIC_BASE_URL or str(request.base_url)
    if base.startswith("http://") and "localhost" not in base and "127.0.0.1" not in base:
        base = "https://" + base[len("http://"):]
    url = qr_url(base, qr.short_code)
    # 文件名只用 ASCII 短码（HTTP 头是 latin-1，中文渠道名会报错）
    fname = f"ordinex_{qr.short_code}"

    if fmt == "svg":
        return Response(svg_bytes(url), media_type="image/svg+xml",
                        headers={"Content-Disposition": f'attachment; filename="{fname}.svg"',
                                 "Cache-Control": "no-store"})
    if fmt == "png":
        data = png_bytes(url)
    elif fmt == "transparent":
        data = png_bytes(url, transparent=True)
    elif fmt == "caption":
        data = png_with_caption(url)
    elif fmt == "marked":
        # 带平台标（平台名 + 平台色横幅）
        data = png_with_platform(url, qr.channel, color_of(qr.channel))
    elif fmt == "poster":
        # 贴进活动参考图中识别到的二维码区域
        campaign = db.get(Campaign, qr.campaign_id)
        if not campaign or not campaign.ref_image or not campaign.ref_qr_box:
            raise HTTPException(status_code=400, detail="该活动未上传参考图或未识别到二维码区域")
        ref_path = os.path.join(settings.UPLOAD_DIR, campaign.ref_image)
        if not os.path.exists(ref_path):
            raise HTTPException(status_code=404, detail="参考图文件丢失")
        with open(ref_path, "rb") as f:
            ref_bytes = f.read()
        data = composite_into_reference(ref_bytes, campaign.ref_qr_box, url,
                                        qr.channel, color_of(qr.channel))
    else:
        raise HTTPException(status_code=400, detail="不支持的格式")
    return Response(data, media_type="image/png",
                    headers={"Content-Disposition": f'attachment; filename="{fname}_{fmt}.png"',
                             "Cache-Control": "no-store"})
