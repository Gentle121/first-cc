# -*- coding: utf-8 -*-
"""生成圆环区的打底图 assets/ring_bg.png。

开发期工具：改了色卡、或想调光晕浓淡时，跑 `python tools/make_ring_bg.py` 重新生成。
只管生成那个 PNG，**运行时用不到本脚本** —— 程序是用 Tk 自带的 PhotoImage 直接读 PNG 的，
所以产品仍然零依赖。本脚本自身需要 Pillow（`pip install pillow`），仅开发时用。

设计：一张 340×340 的浅色柔光图，垫在圆环底下制造纵深。四边用椭圆遮罩强制淡出到
窗口背景色 COL_BG，否则方图叠上去会露出一个可见的方形边界。
"""
import math
import pathlib

from PIL import Image, ImageDraw, ImageFilter

W = H = 340              # 对齐 pomodoro.RING_SIZE
SS = 4                   # 超采样倍数，圆环/光晕边缘才干净
BG = (247, 242, 236, 255)  # == COL_BG #F7F2EC
OUT = pathlib.Path(__file__).resolve().parent.parent / "assets" / "ring_bg.png"

img = Image.new("RGBA", (W * SS, H * SS), BG)
cx = cy = W / 2          # 圆环画布中心


def blend(draw_fn, blur=0.0):
    """在独立图层上画，可选高斯模糊，再压回主画布——这样半透明才会真正与背景混合。"""
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw_fn(ImageDraw.Draw(layer))
    if blur:
        layer = layer.filter(ImageFilter.GaussianBlur(blur * SS))
    img.alpha_composite(layer)


def ellipse(d, box, **kw):
    d.ellipse([box[0] * SS, box[1] * SS, (box[0] + box[2]) * SS, (box[1] + box[3]) * SS], **kw)


def halo(ecx, ecy, r, color, blur):
    blend(lambda d: ellipse(d, (ecx - r, ecy - r, 2 * r, 2 * r), fill=color), blur=blur)


# 居中圆台：中性灰，制造纵深
halo(cx, cy, 152, (217, 210, 204, 58), 44)
# 叠一层更淡的暖色，给一点温度（仍居中，不碰四角）
halo(cx, cy, 186, (198, 90, 74, 12), 60)
# 极淡同心环，呼应计时
blend(lambda d: [d.ellipse([(cx - r) * SS, (cy - r) * SS, (cx + r) * SS, (cy + r) * SS],
                            outline=(217, 210, 204, 66), width=int(1.2 * SS))
                 for r in (124, 158)])

# 四边强制淡出到背景色：归一化椭圆距离，中心 1、四边中点 0、四角 0
mask = Image.new("L", (W, H), 0)
_px = mask.load()
_hx, _hy = (W - 1) / 2.0, (H - 1) / 2.0
for _y in range(H):
    for _x in range(W):
        d = math.hypot((_x - _hx) / _hx, (_y - _hy) / _hy)
        v = 1.0 - d
        _px[_x, _y] = 0 if v <= 0 else int(min(1.0, v ** 0.85) * 255)
mask = mask.resize(img.size, Image.BICUBIC)

pure = Image.new("RGBA", img.size, BG)
final = Image.composite(img, pure, mask).resize((W, H), Image.LANCZOS)

OUT.parent.mkdir(parents=True, exist_ok=True)
final.save(str(OUT))
print("wrote", OUT, final.size)
