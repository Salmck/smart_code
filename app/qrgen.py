"""二维码生成：PNG / SVG / 透明底 / 带「扫码预约」文字版。

二维码内容是短链 URL（https://域名/q/{short_code}），永久不变。
"""
import io
import os

import qrcode
import qrcode.image.svg
from PIL import Image, ImageDraw, ImageFont

from .config import settings

# 常见中文字体路径（Linux / Windows / macOS），用于在二维码上写中文平台名
_CJK_FONT_PATHS = [
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJKsc-Regular.otf",
    "/etc/alternatives/fonts-japanese-gothic.ttf",
    "C:/Windows/Fonts/msyh.ttc",       # 微软雅黑
    "C:/Windows/Fonts/simhei.ttf",     # 黑体
    "C:/Windows/Fonts/simsun.ttc",     # 宋体
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
]
_font_path = next((p for p in _CJK_FONT_PATHS if os.path.exists(p)), None)


def _cjk_font(size: int):
    """加载中文字体；找不到则回退默认字体（中文会显示为方块）。"""
    if _font_path:
        try:
            return ImageFont.truetype(_font_path, size)
        except Exception:
            pass
    return ImageFont.load_default()


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


def _hex_to_rgb(h: str) -> tuple:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def png_with_platform(url: str, platform: str, color: str, caption: str = "") -> bytes:
    """在二维码下方加一条平台色横幅 + 平台名文字（区分不同平台的码）。"""
    qr = qrcode.QRCode(box_size=10, border=2,
                       error_correction=qrcode.constants.ERROR_CORRECT_H)
    qr.add_data(url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    w, h = qr_img.size
    band = 60
    canvas = Image.new("RGB", (w, h + band), "white")
    canvas.paste(qr_img, (0, 0))
    draw = ImageDraw.Draw(canvas)
    rgb = _hex_to_rgb(color)
    draw.rectangle([0, h, w, h + band], fill=rgb)
    text = f"{platform}" + (f" · {caption}" if caption else " · 扫码预约")
    font = _cjk_font(24)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((w - tw) / 2, h + (band - th) / 2 - bbox[1]), text, fill="white", font=font)
    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()


def detect_qr_box(image_bytes: bytes):
    """用 OpenCV 识别图中二维码区域，返回 (x, y, w, h)；未识别返回 None。"""
    import cv2
    import numpy as np
    arr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return None
    detector = cv2.QRCodeDetector()
    ok, points = detector.detect(img)
    if not ok or points is None:
        return None
    pts = points[0]
    xs = [float(p[0]) for p in pts]
    ys = [float(p[1]) for p in pts]
    x, y = max(int(min(xs)), 0), max(int(min(ys)), 0)
    w = int(max(xs) - min(xs))
    h = int(max(ys) - min(ys))
    if w < 10 or h < 10:
        return None
    return (x, y, w, h)


def composite_into_reference(ref_bytes: bytes, box, code_url: str,
                             platform: str = "", color: str = "") -> bytes:
    """把（带平台标的）真码贴到参考图中识别出的二维码区域，替换占位码。"""
    ref = Image.open(io.BytesIO(ref_bytes)).convert("RGB")
    x, y, w, h = box
    # 生成高容错真码（贴图后仍可扫），略放大覆盖占位码边缘
    if platform:
        code_png = png_with_platform(code_url, platform, color)
    else:
        code_png = png_bytes(code_url)
    code_img = Image.open(io.BytesIO(code_png)).convert("RGB")
    # 只取二维码主体区域缩放到检测框（平台标横幅可能超出，故用纯码贴入）
    pure = png_bytes(code_url)
    code_img = Image.open(io.BytesIO(pure)).convert("RGB").resize((w, h))
    ref.paste(code_img, (x, y))
    buf = io.BytesIO()
    ref.save(buf, format="PNG")
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
    font = _cjk_font(22)
    bbox = draw.textbbox((0, 0), caption, font=font)
    tw = bbox[2] - bbox[0]
    draw.text(((w - tw) / 2, h + 12), caption, fill="black", font=font)
    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()
