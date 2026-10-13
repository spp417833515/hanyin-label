# -*- coding: utf-8 -*-
"""生成程序图标 web/favicon.ico (16~256 多尺寸) 和 web/icon.png: 蓝底圆角方块 + 白色吊牌标签."""
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "..", "web")
BLUE, DARK = (31, 95, 191, 255), (23, 74, 148, 255)


def draw(size):
    s = 4  # 4 倍超采样再缩小, 边缘平滑
    S = size * s
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = int(S * 0.22)
    d.rounded_rectangle([0, 0, S - 1, S - 1], radius=r, fill=BLUE)
    # 吊牌: 左边尖角的长方形
    x0, x1, y0, y1 = S * 0.16, S * 0.86, S * 0.28, S * 0.72
    tip = S * 0.14
    d.polygon([(x0, (y0 + y1) / 2), (x0 + tip, y0), (x1, y0), (x1, y1), (x0 + tip, y1)], fill=(255, 255, 255, 255))
    # 穿绳孔
    cx, cy, hr = x0 + tip * 0.95, (y0 + y1) / 2, S * 0.045
    d.ellipse([cx - hr, cy - hr, cx + hr, cy + hr], fill=BLUE)
    # 标签上的两行"字"
    if size >= 24:
        lx = x0 + tip * 1.9
        d.rounded_rectangle([lx, S * 0.39, x1 - S * 0.08, S * 0.46], radius=S * 0.02, fill=DARK)
        d.rounded_rectangle([lx, S * 0.54, x1 - S * 0.2, S * 0.61], radius=S * 0.02, fill=DARK)
    return img.resize((size, size), Image.Resampling.LANCZOS)


if __name__ == "__main__":
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    imgs = [draw(n) for n in sizes]
    imgs[-1].save(os.path.join(WEB, "favicon.ico"), sizes=[(n, n) for n in sizes], append_images=imgs[:-1])
    draw(192).save(os.path.join(WEB, "icon.png"))
    print("已生成 web/favicon.ico, web/icon.png")
