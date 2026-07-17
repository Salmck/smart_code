"""二维码文件下载（PNG / SVG / 透明底 / 带文字版），管理员或本店管理者可用。"""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from ..db import get_db
from ..deps import AuthUser, assert_store_access, require_staff
from ..models import QRPlacement
from ..qrgen import png_bytes, png_with_caption, qr_url, svg_bytes

router = APIRouter(prefix="/qr")


@router.get("/{qr_id}/{fmt}")
def download(qr_id: int, fmt: str, request: Request,
             user: AuthUser = Depends(require_staff), db=Depends(get_db)):
    qr = db.get(QRPlacement, qr_id)
    if not qr:
        raise HTTPException(status_code=404, detail="二维码不存在")
    assert_store_access(user, qr.store_id)

    url = qr_url(str(request.base_url), qr.short_code)
    fname = f"ordinex_{qr.short_code}"

    if fmt == "svg":
        return Response(svg_bytes(url), media_type="image/svg+xml",
                        headers={"Content-Disposition": f'attachment; filename="{fname}.svg"'})
    if fmt == "png":
        data = png_bytes(url)
    elif fmt == "transparent":
        data = png_bytes(url, transparent=True)
    elif fmt == "caption":
        data = png_with_caption(url)
    else:
        raise HTTPException(status_code=400, detail="不支持的格式")
    return Response(data, media_type="image/png",
                    headers={"Content-Disposition": f'attachment; filename="{fname}_{fmt}.png"'})
