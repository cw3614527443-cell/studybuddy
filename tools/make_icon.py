"""生成应用图标 assets/icon.ico。

纯几何绘制（不依赖字体），避免不同机器渲染不一致。
设计：深青底圆角方块 + 白色对勾，右下角一枚琥珀色小圆点（呼应「学习/打卡」）。
"""
import os

from PIL import Image, ImageDraw

BG = (30, 95, 116)        # 深青 #1E5F74
BG_DARK = (22, 72, 88)    # 底边稍深，做出一点立体
FG = (255, 255, 255)
ACCENT = (240, 173, 78)   # 琥珀 #F0AD4E

SIZES = [16, 24, 32, 48, 64, 128, 256]


def draw(size):
    s = size * 8  # 超采样，缩小时边缘更干净
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 圆角方块
    pad = s * 0.06
    r = s * 0.22
    d.rounded_rectangle([pad, pad, s - pad, s - pad], radius=r, fill=BG)
    # 底部一条稍深的横向渐层，模拟立体
    d.rounded_rectangle([pad, s * 0.62, s - pad, s - pad], radius=r, fill=BG_DARK)
    d.rounded_rectangle([pad, pad, s - pad, s * 0.70], radius=r, fill=BG)

    # 对勾
    w = max(2, int(s * 0.085))
    pts = [(s * 0.26, s * 0.52), (s * 0.44, s * 0.70), (s * 0.75, s * 0.33)]
    d.line(pts, fill=FG, width=w, joint="curve")
    # 圆头端点
    for x, y in (pts[0], pts[-1]):
        d.ellipse([x - w / 2, y - w / 2, x + w / 2, y + w / 2], fill=FG)

    # 右下角琥珀点
    cx, cy, cr = s * 0.755, s * 0.755, s * 0.062
    d.ellipse([cx - cr, cy - cr, cx + cr, cy + cr], fill=ACCENT)

    return img.resize((size, size), Image.LANCZOS)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    out_dir = os.path.join(here, "assets")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "icon.ico")
    imgs = [draw(n) for n in SIZES]
    imgs[-1].save(out, format="ICO", sizes=[(n, n) for n in SIZES])
    png = os.path.join(out_dir, "icon.png")
    imgs[-1].save(png)
    print("生成:", out, os.path.getsize(out), "字节")
    print("预览:", png)


if __name__ == "__main__":
    main()
