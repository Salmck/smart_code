"""二维码生成：PNG / SVG / 透明底 / 带「扫码预约」文字版。

二维码内容是短链 URL（https://域名/q/{short_code}），永久不变。
"""
import io

import qrcode
import qrcode.image.svg
from PIL import Image, ImageDraw

from .config import settings


def qr_url(base_url: str, short_code: str) -> str:
    return f"{base_url.rstrip('/')}/q/{short_code}"


def png_bytes(url: str, box_size: int = 10, transparent: bool = False) -> bytes:
    qr = qrcode.QRCode(box_size=box_size, border=2,
                       error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(url)
    qr.make(fit=True)
    if transparent:
        img = qr.make_image(fill_color="black", back_color=(255, 255, 255, 0)).convert("RGBA")
    else:
        img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def svg_bytes(url: str) -> bytes:
    factory = qrcode.image.svg.SvgPathImage
    img = qrcode.make(url, image_factory=factory, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue()


def png_with_caption(url: str, caption: str = "扫码预约") -> bytes:
    """在二维码下方加一行文字，用于线下海报/桌贴。"""
    qr = qrcode.QRCode(box_size=10, border=2,
                       error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    w, h = qr_img.size
    band = 46
    canvas = Image.new("RGB", (w, h + band), "white")
    canvas.paste(qr_img, (0, 0))
    draw = ImageDraw.Draw(canvas)
    # 用内置默认字体（无需外部字体文件），居中绘制
    try:
        bbox = draw.textbbox((0, 0), caption)
        tw = bbox[2] - bbox[0]
    except Exception:
        tw = len(caption) * 6
    draw.text(((w - tw) / 2, h + 14), caption, fill="black")
    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()
