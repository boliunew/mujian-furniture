"""Render the sample 360° frames for the walnut media console (public/demo-spin/tv1).

Real products use photos shot on a turntable; this just gives the demo something to turn.
    python tools/render_demo_spin.py
"""
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parent.parent / "public" / "demo-spin" / "tv1"
N, SIZE, SS = 24, 720, 2  # frames, output px, supersampling

WALNUT, WALNUT_D, BRASS, DARK = (104, 66, 42), (86, 53, 33), (196, 158, 82), (40, 30, 24)

# Boxes in cm: (x0, x1, y0, y1, z0, z1, color). Width along x, height y (up), depth z (+z is front).
W, D, H, LEG = 180, 42, 40, 15
BOXES = [
    (-W / 2, W / 2, LEG, LEG + H, -D / 2, D / 2, WALNUT),                    # carcass
    (-W / 2 + 3, -1, LEG + 3, LEG + H - 3, D / 2, D / 2 + 1.2, WALNUT_D),    # left door
    (1, W / 2 - 3, LEG + 3, LEG + H - 3, D / 2, D / 2 + 1.2, WALNUT_D),      # right door
    (-6, -3, LEG + H / 2 - 4, LEG + H / 2 + 4, D / 2 + 1.2, D / 2 + 2.4, BRASS),
    (3, 6, LEG + H / 2 - 4, LEG + H / 2 + 4, D / 2 + 1.2, D / 2 + 2.4, BRASS),
]
for sx in (-1, 1):
    for sz in (-1, 1):
        x, z = sx * (W / 2 - 8), sz * (D / 2 - 6)
        BOXES.append((x - 2, x + 2, 0, LEG, z - 2, z + 2, DARK))

LIGHT = np.array([0.45, 0.8, 0.6]); LIGHT /= np.linalg.norm(LIGHT)


def faces(b):
    x0, x1, y0, y1, z0, z1, col = b
    v = lambda x, y, z: np.array([x, y, z], float)
    return [
        ([v(x0, y0, z1), v(x1, y0, z1), v(x1, y1, z1), v(x0, y1, z1)], (0, 0, 1), col),
        ([v(x1, y0, z0), v(x0, y0, z0), v(x0, y1, z0), v(x1, y1, z0)], (0, 0, -1), col),
        ([v(x1, y0, z1), v(x1, y0, z0), v(x1, y1, z0), v(x1, y1, z1)], (1, 0, 0), col),
        ([v(x0, y0, z0), v(x0, y0, z1), v(x0, y1, z1), v(x0, y1, z0)], (-1, 0, 0), col),
        ([v(x0, y1, z1), v(x1, y1, z1), v(x1, y1, z0), v(x0, y1, z0)], (0, 1, 0), col),
    ]


def render(theta):
    s = SIZE * SS
    img = Image.new("RGB", (s, s), (255, 255, 255))
    # soft floor shadow
    sh = Image.new("L", (s, s), 0)
    ImageDraw.Draw(sh).ellipse([s * 0.14, s * 0.66, s * 0.86, s * 0.76], fill=70)
    sh = sh.filter(ImageFilter.GaussianBlur(s * 0.025))
    img.paste((205, 205, 205), mask=sh)

    ct, st_ = math.cos(theta), math.sin(theta)
    rot = np.array([[ct, 0, st_], [0, 1, 0], [-st_, 0, ct]])
    pitch = math.radians(18)
    cp, sp = math.cos(pitch), math.sin(pitch)
    tilt = np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]])
    M = tilt @ rot
    scale, cx, cy = s / 230, s / 2, s * 0.6

    polys = []
    for bi, b in enumerate(BOXES):
        for pts, n, col in faces(b):
            if 1 <= bi <= 4 and n == (0, 0, -1):   # doors and knobs sit flat on the front panel
                continue
            n_w = rot @ np.array(n, float)
            n_v = M @ np.array(n, float)
            if n_v[2] <= 0:      # back-face cull (camera looks toward -z)
                continue
            P = [M @ (p - np.array([0, LEG + H / 2, 0])) for p in pts]
            depth = sum(p[2] for p in P) / 4
            shade = 0.5 + 0.5 * max(0.0, float(n_w @ LIGHT))
            c = tuple(int(min(255, ch * shade + 12)) for ch in col)
            polys.append((depth, [(cx + p[0] * scale, cy - p[1] * scale) for p in P], c))
    d = ImageDraw.Draw(img)
    for _, pts, c in sorted(polys, key=lambda t: t[0]):
        d.polygon(pts, fill=c)
    return img.resize((SIZE, SIZE), Image.LANCZOS)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for i in range(N):
        render(2 * math.pi * i / N + math.radians(-30)).save(OUT / f"{i:02d}.jpg", quality=84, optimize=True)
    print(f"wrote {N} frames to {OUT}")
