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


def png_with_platform(url: str, platform: str, color: str) -> bytes:
    """中间签 logo 式平台码：码中心放平台色圆角徽章 + 平台名（类似微信中心 logo）。

    用最高容错等级 H（可遮挡约 30%），中心徽章控制在边长 ~26%，遮挡面积 ~7%，
    扫码识别不受影响。
    """
    qr = qrcode.QRCode(box_size=10, border=2,
                       error_correction=qrcode.constants.ERROR_CORRECT_H)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    w, h = img.size
    draw = ImageDraw.Draw(img)

    badge = int(w * 0.26)
    pad = int(badge * 0.10)           # 白色安全边，把徽章和码点隔开
    x0 = (w - badge) // 2
    y0 = (h - badge) // 2
    r = int(badge * 0.22)
    # 白底垫层（略大）+ 平台色圆角徽章
    draw.rounded_rectangle([x0 - pad, y0 - pad, x0 + badge + pad, y0 + badge + pad],
                           radius=r + pad, fill="white")
    draw.rounded_rectangle([x0, y0, x0 + badge, y0 + badge],
                           radius=r, fill=_hex_to_rgb(color))

    # 平台名：≤2 字单行大字；3 字单行；≥4 字拆两行
    name = platform.strip()
    if len(name) <= 3:
        lines = [name]
        fsize = int(badge * (0.40 if len(name) <= 2 else 0.28))
    else:
        mid = (len(name) + 1) // 2
        lines = [name[:mid], name[mid:]]
        fsize = int(badge * 0.26)
    font = _cjk_font(max(fsize, 10))
    line_hs = []
    for ln in lines:
        bb = draw.textbbox((0, 0), ln, font=font)
        line_hs.append((bb[2] - bb[0], bb[3] - bb[1], bb[1]))
    total_h = sum(hh for _, hh, _ in line_hs) + (len(lines) - 1) * int(fsize * 0.25)
    cy = y0 + (badge - total_h) / 2
    for ln, (tw, th, off) in zip(lines, line_hs):
        draw.text((x0 + (badge - tw) / 2, cy - off), ln, fill="white", font=font)
        cy += th + int(fsize * 0.25)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def detect_qr_box(image_bytes: bytes):
    """识别图中二维码区域，返回原图坐标 (x, y, w, h)；未识别返回 None。

    加固点（避免大图/特殊格式导致 OpenCV 崩溃）：
    - 用 PIL 解码并按 EXIF 矫正方向、统一转 RGB（排除 CMYK/调色板等奇异格式）
    - 检测前把长边压到 ≤1280（大图是 QRCodeDetector 崩溃的主要诱因），
      识别后按比例换算回原图坐标
    - 全程 try/except，识别失败只返回 None，不让上传接口 500
    """
    try:
        import cv2
        import numpy as np
        from PIL import ImageOps

        pil = Image.open(io.BytesIO(image_bytes))
        pil = ImageOps.exif_transpose(pil).convert("RGB")
        ow, oh = pil.size
        scale = 1.0
        MAX_SIDE = 1280
        if max(ow, oh) > MAX_SIDE:
            scale = MAX_SIDE / max(ow, oh)
            pil = pil.resize((max(int(ow * scale), 1), max(int(oh * scale), 1)))

        img = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
        detector = cv2.QRCodeDetector()
        ok, points = detector.detect(img)
        if not ok or points is None:
            return None
        pts = points[0]
        xs = [float(p[0]) for p in pts]
        ys = [float(p[1]) for p in pts]
        # 还原到原图坐标
        x = max(int(min(xs) / scale), 0)
        y = max(int(min(ys) / scale), 0)
        w = int((max(xs) - min(xs)) / scale)
        h = int((max(ys) - min(ys)) / scale)
        if w < 10 or h < 10:
            return None
        return (x, y, w, h)
    except Exception:
        return None


def composite_into_reference(ref_bytes: bytes, box, code_url: str,
                             platform: str = "", color: str = "") -> bytes:
    """把真码贴到参考图中识别出的二维码区域，替换占位码。

    区域够大（≥180px）时贴中心平台标版本；过小则贴纯码保证可扫。
    """
    ref = Image.open(io.BytesIO(ref_bytes)).convert("RGB")
    x, y, w, h = box
    if platform and min(w, h) >= 180:
        code_png = png_with_platform(code_url, platform, color)
    else:
        code_png = png_bytes(code_url)
    code_img = Image.open(io.BytesIO(code_png)).convert("RGB").resize((w, h))
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
