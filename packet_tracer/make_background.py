#!/usr/bin/env python3
"""
Draws the isometric data-centre picture used as the Logical-workspace background
of the AIDC-Net Packet Tracer files, and the canvas position of every device so
each icon sits in front of the rack it stands for.

    python3 packet_tracer/make_background.py
        -> packet_tracer/aidc_background.png
        -> packet_tracer/visual_layout.json   {device: [x, y]} in image pixels

Rooms: NOC (controller) | Spine core | Power & cooling   (top row)
       Pod 1            | Pod 2      | Pod 3             (bottom row)
"""

import json
import math
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
SS = 2                                  # supersampling factor
IMG_W, IMG_H = 2160, 1300
BG = (245, 247, 250)
C30 = math.cos(math.radians(30))
if sys.platform == "win32":
    _F = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    FONT_B, FONT_R, FONT_N = (os.path.join(_F, n) for n in ("arialbd.ttf", "arial.ttf", "ARIALNB.TTF"))
    if not os.path.exists(FONT_N):
        FONT_N = FONT_B
else:
    FONT_B = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
    FONT_R = "/System/Library/Fonts/Supplemental/Arial.ttf"
    FONT_N = "/System/Library/Fonts/Supplemental/Arial Narrow Bold.ttf"

POD_ACCENT = [(74, 144, 226), (38, 166, 154), (149, 117, 205)]


def font(path, px):
    return ImageFont.truetype(path, int(px))


def shade(c, f):
    return tuple(max(0, min(255, int(v * f))) for v in c[:3])


def mix(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


class Room:
    """One isometric room. World metres: x along the right-back wall, y along the
    left-back wall, z up. (0,0) is the far corner, (W,D) the near one."""

    def __init__(self, canvas, cx, oy, W=7.0, D=7.0, H=3.0, s=52):
        self.img, self.cx, self.oy, self.W, self.D, self.H, self.s = canvas, cx, oy, W, D, H, s
        self.d = ImageDraw.Draw(canvas)

    def P(self, x, y, z=0.0):
        return ((self.cx + (x - y) * C30 * self.s) * SS,
                (self.oy + (x + y) * 0.5 * self.s - z * self.s) * SS)

    def screen(self, x, y, z=0.0):
        """Unsupersampled image coordinate (what PT sees)."""
        px, py = self.P(x, y, z)
        return round(px / SS), round(py / SS)

    def poly(self, pts, fill, outline=None, w=1):
        self.d.polygon([self.P(*p) for p in pts], fill=fill)
        if outline:
            q = [self.P(*p) for p in pts]
            self.d.line(q + [q[0]], fill=outline, width=w * SS, joint="curve")

    def line(self, pts, fill, w=1):
        self.d.line([self.P(*p) for p in pts], fill=fill, width=max(1, int(w * SS)), joint="curve")

    # ---------------------------------------------------------------- boxes
    def box(self, x0, y0, z0, dx, dy, dz, col, edge=None, top=None, left=None, right=None):
        x1, y1, z1 = x0 + dx, y0 + dy, z0 + dz
        edge = edge or shade(col, 0.62)
        self.poly([(x0, y1, z0), (x1, y1, z0), (x1, y1, z1), (x0, y1, z1)], left or shade(col, 0.88), edge)
        self.poly([(x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)], right or shade(col, 0.72), edge)
        self.poly([(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)], top or shade(col, 1.08), edge)

    @staticmethod
    def face_y(x0, y1, z0):
        """Plane y = y1 (faces the viewer's left). u -> +x, v -> +z."""
        return lambda u, v: (x0 + u, y1, z0 + v)

    @staticmethod
    def face_x(x1, y1, z0):
        """Plane x = x1 (faces the viewer's right). u -> -y (screen right), v -> +z."""
        return lambda u, v: (x1, y1 - u, z0 + v)

    def fquad(self, f, u0, v0, u1, v1, fill, outline=None):
        self.poly([f(u0, v0), f(u1, v0), f(u1, v1), f(u0, v1)], fill, outline)

    def fline(self, f, u0, v0, u1, v1, fill, w=1):
        self.line([f(u0, v0), f(u1, v1)], fill, w)

    def fdot(self, f, u, v, r, fill):
        x, y = self.P(*f(u, v))
        r *= SS
        self.d.ellipse([x - r, y - r, x + r, y + r], fill=fill)

    def ftext(self, f, u, v, text, h, color, fpath=FONT_B, anchor_center=False):
        """Text painted onto a plane: (u, v) is its top-left corner, h its height in metres."""
        fnt = font(fpath, 64)
        l, t, r, b = fnt.getbbox(text)
        tw, th = r - l + 8, b - t + 8
        timg = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
        ImageDraw.Draw(timg).text((4 - l, 4 - t), text, font=fnt, fill=color)
        k = h / th                                       # metres per text pixel
        if anchor_center:
            u -= tw * k / 2
        o = np.array(self.P(*f(u, v)))
        ax = np.array(self.P(*f(u + k, v))) - o
        ay = np.array(self.P(*f(u, v - k))) - o
        M = np.column_stack([ax, ay])
        corners = [o, o + ax * tw, o + ay * th, o + ax * tw + ay * th]
        bx0, by0 = np.floor(np.min(corners, axis=0)).astype(int)
        bx1, by1 = np.ceil(np.max(corners, axis=0)).astype(int)
        Mi = np.linalg.inv(M)
        off = Mi @ (np.array([bx0, by0]) - o)
        data = (Mi[0, 0], Mi[0, 1], off[0], Mi[1, 0], Mi[1, 1], off[1])
        out = timg.transform((bx1 - bx0, by1 - by0), Image.AFFINE, data, resample=Image.BICUBIC)
        self.img.alpha_composite(out, (int(bx0), int(by0)))

    # ------------------------------------------------------------ the shell
    def shell(self, floor, wall_l, wall_r, slab=(120, 126, 136), tile=None, plank=None):
        W, D, H, t = self.W, self.D, self.H, 0.22
        # soft shadow under the room
        sh = Image.new("RGBA", self.img.size, (0, 0, 0, 0))
        sd = ImageDraw.Draw(sh)
        sd.polygon([self.P(-t + 0.3, -t + 0.6, -0.3), self.P(W + 0.5, 0.6, -0.3),
                    self.P(W + 0.5, D + 0.6, -0.3), self.P(0.3, D + 0.6, -0.3)], fill=(30, 40, 60, 60))
        self.img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(14 * SS)))
        self.d = ImageDraw.Draw(self.img)
        # floor slab
        self.box(-t, -t, -0.28, W + t, D + t, 0.28, slab, top=floor)
        if tile:
            for i in range(1, int(W / 0.6) + 1):
                self.line([(i * 0.6, 0, 0), (i * 0.6, D, 0)], tile, 1)
            for j in range(1, int(D / 0.6) + 1):
                self.line([(0, j * 0.6, 0), (W, j * 0.6, 0)], tile, 1)
        if plank:
            for j in range(1, int(D / 0.35) + 1):
                self.line([(0, j * 0.35, 0), (W, j * 0.35, 0)], plank, 1)
            for j in range(int(D / 0.35)):
                for i in range(int(W / 1.4) + 1):
                    xo = i * 1.4 + (0.7 if j % 2 else 0)
                    if 0 < xo < W:
                        self.line([(xo, j * 0.35, 0), (xo, (j + 1) * 0.35, 0)], plank, 1)
        # back walls (thick), drawn as boxes so their tops show
        self.box(-t, -t, 0, t, D + t, H, wall_l, right=wall_l, top=(250, 250, 250), left=shade(wall_l, 0.8))
        self.box(0, -t, 0, W, t, H, wall_r, left=wall_r, top=(250, 250, 250), right=shade(wall_r, 0.8))
        # skirting
        self.poly([(0, 0, 0), (W, 0, 0), (W, 0, 0.12), (0, 0, 0.12)], shade(wall_r, 0.82))
        self.poly([(0, 0, 0), (0, D, 0), (0, D, 0.12), (0, 0, 0.12)], shade(wall_l, 0.82))

    def wall_r_face(self):
        return self.face_y(0, 0, 0)

    def wall_l_face(self):
        return lambda u, v: (0, self.D - u, v)   # u -> -y (screen right-up), v -> +z

    def sign(self, f, u, v, w, h, bg, text, sub=None, fg=(255, 255, 255)):
        self.fquad(f, u, v - h, u + w, v, bg, shade(bg, 0.7))
        self.ftext(f, u + w / 2, v - 0.06, text, h * (0.48 if sub else 0.62), fg, anchor_center=True)
        if sub:
            self.ftext(f, u + w / 2, v - h * 0.64, sub, h * 0.26, fg, FONT_R, anchor_center=True)


# --------------------------------------------------------------- furniture
def rack(R, x0, y0, kind, accent, w=0.8, d=1.0, h=2.15):
    """A 42U rack against the right-back wall, door facing +y (viewer's left)."""
    body = (44, 49, 58)
    R.box(x0, y0, 0, w, d, h, body, edge=(24, 27, 33), left=(52, 58, 69), right=(36, 40, 48),
          top=(70, 76, 88))
    f = R.face_y(x0, y0 + d, 0)
    R.fquad(f, 0.05, 0.08, w - 0.05, h - 0.06, (30, 34, 41))
    R.fquad(f, 0.05, h - 0.16, w - 0.05, h - 0.06, accent)            # coloured header strip
    if kind == "gpu":
        for i in range(4):                                          # four 4U GPU nodes
            z = 0.22 + i * 0.44
            R.fquad(f, 0.08, z, w - 0.08, z + 0.38, (58, 64, 76), (20, 22, 27))
            for k in range(5):
                R.fdot(f, 0.16 + k * 0.12, z + 0.2, 1.9, (24, 26, 31))
                R.fdot(f, 0.16 + k * 0.12, z + 0.2, 1.1, (118, 214, 92))
            R.fline(f, 0.1, z + 0.04, w - 0.1, z + 0.04, (118, 214, 92), 1.2)
            R.fdot(f, w - 0.13, z + 0.3, 1.3, (80, 200, 255))
    elif kind == "net":
        for i in range(7):                                          # switches + patch panels
            z = 0.35 + i * 0.22
            R.fquad(f, 0.08, z, w - 0.08, z + 0.15, (70, 78, 92) if i % 2 else (210, 214, 220),
                    (20, 22, 27))
            for k in range(10):
                c = (255, 196, 0) if (k + i) % 3 == 0 else (90, 220, 120)
                R.fdot(f, 0.14 + k * 0.058, z + 0.075, 1.0, c if i % 2 else (40, 44, 52))
            if i % 2 == 0:                                          # patch-cable loops
                for k in range(0, 10, 2):
                    cc = [(232, 72, 85), (66, 135, 245), (255, 196, 0), (90, 200, 120)][k // 2 % 4]
                    R.fline(f, 0.14 + k * 0.058, z, 0.1 + k * 0.03, z - 0.12, cc, 1.1)
    elif kind == "storage":
        for i in range(10):
            z = 0.25 + i * 0.17
            R.fquad(f, 0.08, z, w - 0.08, z + 0.14, (64, 70, 82), (20, 22, 27))
            for k in range(8):
                R.fdot(f, 0.13 + k * 0.075, z + 0.07, 0.9, (80, 200, 255) if (k * i) % 4 else (255, 160, 60))
    elif kind == "ups":
        R.fquad(f, 0.15, 1.45, w - 0.15, 1.85, (24, 30, 38), (10, 10, 12))
        R.fquad(f, 0.2, 1.5, w - 0.2, 1.8, (60, 200, 140))
        for i in range(6):
            R.fline(f, 0.12, 0.3 + i * 0.16, w - 0.12, 0.3 + i * 0.16, (66, 72, 84), 1.4)
    # side vents on the visible right side
    g = R.face_x(x0 + w, y0 + d, 0)
    for i in range(12):
        R.fline(g, 0.15, 0.3 + i * 0.13, d - 0.15, 0.3 + i * 0.13, (30, 34, 40), 1)


def cable_tray(R, x0, x1, y, z, col=(255, 196, 0)):
    R.box(x0, y, z, x1 - x0, 0.3, 0.06, (150, 156, 166))
    for i in range(int((x1 - x0) / 0.25)):
        R.line([(x0 + i * 0.25, y + 0.05, z + 0.07), (x0 + i * 0.25 + 0.2, y + 0.25, z + 0.07)], col, 1.6)


def crac(R, x, y):
    """Floor-standing cooling unit against the left-back wall."""
    R.box(x, y, 0, 0.9, 1.6, 2.0, (232, 236, 240))
    g = R.face_x(x + 0.9, y + 1.6, 0)
    for i in range(14):
        R.fline(g, 0.12, 0.25 + i * 0.1, 1.48, 0.25 + i * 0.1, (178, 186, 196), 1.2)
    R.fquad(g, 0.2, 1.75, 0.6, 1.92, (30, 40, 50))
    R.fdot(g, 0.3, 1.835, 1.4, (60, 200, 255))
    R.ftext(g, 0.8, 1.93, "CRAC", 0.14, (90, 100, 115))


def cold_aisle(R, x0, x1, y0, y1):
    ov = Image.new("RGBA", R.img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    od.polygon([R.P(x0, y0), R.P(x1, y0), R.P(x1, y1), R.P(x0, y1)], fill=(90, 170, 255, 55))
    R.img.alpha_composite(ov)
    R.d = ImageDraw.Draw(R.img)
    f = lambda u, v: (x0 + u, y1 - v, 0.0)        # text lying on the floor
    R.ftext(f, (x1 - x0) / 2, 0.42, "COLD AISLE", 0.26, (60, 120, 200, 210), anchor_center=True)


def extinguisher(R, x, y):
    R.box(x, y, 0, 0.18, 0.18, 0.55, (220, 50, 50))
    R.box(x + 0.04, y + 0.04, 0.55, 0.1, 0.1, 0.1, (40, 40, 40))


def plant(R, x, y):
    R.box(x, y, 0, 0.4, 0.4, 0.5, (240, 240, 240))
    R.box(x - 0.05, y - 0.05, 0.5, 0.5, 0.5, 0.55, (96, 176, 86))


def desk_monitor(R, x, y, z, w, screen):
    R.box(x, y, z, w, 0.06, 0.42, (40, 44, 52))
    f = R.face_y(x, y + 0.06, z)
    R.fquad(f, 0.03, 0.04, w - 0.03, 0.38, screen)
    pts = [(0.06 + i * (w - 0.12) / 7, 0.1 + 0.2 * abs(math.sin(i * 1.3 + x))) for i in range(8)]
    for (u0, v0), (u1, v1) in zip(pts, pts[1:]):
        R.fline(f, u0, v0, u1, v1, (120, 230, 140), 1.3)
    R.box(x + w / 2 - 0.04, y + 0.02, z - 0.0, 0.08, 0.08, 0.02, (40, 44, 52))


def chair(R, x, y):
    R.box(x + 0.22, y + 0.22, 0, 0.06, 0.06, 0.42, (60, 60, 66))
    R.box(x, y, 0.42, 0.5, 0.5, 0.08, (52, 90, 150))
    R.box(x, y + 0.42, 0.5, 0.5, 0.08, 0.6, (52, 90, 150))


# ------------------------------------------------------------------- rooms
def pod_room(R, l, layout):
    acc = POD_ACCENT[l - 1]
    R.shell(floor=(222, 226, 232), wall_l=(236, 240, 245), wall_r=mix((246, 248, 251), acc, 0.18),
            tile=(198, 204, 212))
    fr = R.wall_r_face()
    R.fquad(fr, 0, 2.55, R.W, 2.7, acc)
    R.fquad(R.wall_l_face(), 0, 2.55, R.D, 2.7, acc)
    R.sign(R.wall_l_face(), 3.2, 2.48, 3.2, 0.5, shade(acc, 0.8), f"POD {l}", f"10.0.{l}.0/24  ·  leaf{l} / tor{l}")
    cable_tray(R, 0.4, 6.6, 0.25, 2.35)
    crac(R, 0.15, 3.6)
    extinguisher(R, 0.25, 6.3)
    cold_aisle(R, 0.6, 6.4, 1.35, 2.7)
    kinds = [("net", f"leaf{l}"), ("gpu", f"h{l}1"), ("gpu", f"h{l}2"), ("storage", None)]
    for i, (kind, name) in enumerate(kinds):
        x0 = 0.8 + i * 1.45
        rack(R, x0, 0.25, kind, acc)
        R.ftext(R.face_y(x0, 1.25, 0), 0.4, 2.06, ["NET", "GPU-A", "GPU-B", "NVMe"][i], 0.07,
                (255, 255, 255), anchor_center=True)
        if kind == "gpu":
            layout[name] = R.screen(x0 + 0.75, 2.3, 0)
    layout[f"leaf{l}"] = R.screen(1.0, 1.6, 0)
    layout[f"tor{l}"] = R.screen(1.0, 3.6, 0)
    # floor label in front of the room
    f = lambda u, v: (u, R.D + 0.01, v)
    R.ftext(f, R.W / 2, -0.04, f"POD {l}", 0.16, (255, 255, 255), anchor_center=True)


def spine_room(R, layout):
    R.shell(floor=(78, 84, 96), wall_l=(238, 240, 244), wall_r=(255, 226, 204), slab=(60, 64, 74),
            tile=(64, 70, 80))
    fr = R.wall_r_face()
    R.fquad(fr, 0, 2.55, R.W, 2.7, (240, 132, 76))
    R.fquad(R.wall_l_face(), 0, 2.55, R.D, 2.7, (240, 132, 76))
    R.sign(R.wall_l_face(), 3.2, 2.48, 3.2, 0.5, (214, 104, 56), "SPINE CORE", "OSPF area 0  ·  172.16.0.0/16")
    cable_tray(R, 0.4, 6.6, 0.25, 2.35, col=(255, 210, 60))
    crac(R, 0.15, 4.4)
    for i, x0 in enumerate([1.3, 2.3, 4.1, 5.1]):
        kind = "net" if i in (0, 3) else "storage"
        rack(R, x0, 0.25, kind, (240, 132, 76))
    # yellow fibre runs dropping from the tray into the spine racks
    for x in (1.7, 5.5):
        R.line([(x, 0.4, 2.35), (x, 0.4, 2.15)], (255, 210, 60), 2.5)
    layout["spine1"] = R.screen(1.7, 2.3, 0)
    layout["spine2"] = R.screen(5.5, 2.3, 0)
    f = lambda u, v: (u, R.D + 0.01, v)
    R.ftext(f, R.W / 2, -0.04, "SPINE CORE", 0.16, (255, 255, 255), anchor_center=True)


def noc_room(R, layout):
    R.shell(floor=(201, 167, 124), wall_l=(246, 240, 230), wall_r=(206, 232, 196), slab=(150, 118, 84),
            plank=(180, 146, 104))
    fr = R.wall_r_face()
    # big wall screen with the ECMP vs AIDC-Net link-load bars
    R.fquad(fr, 1.2, 1.1, 5.4, 2.6, (28, 32, 40), (14, 16, 20))
    R.fquad(fr, 1.3, 1.2, 5.3, 2.5, (18, 30, 48))
    R.ftext(fr, 3.3, 2.44, "AIDC-Net controller  ·  link load", 0.13, (200, 220, 255), anchor_center=True)
    ecmp = [0.9, 0.25, 0.75, 0.2, 0.85, 0.3]
    aidc = [0.55, 0.5, 0.52, 0.48, 0.55, 0.5]
    for i, (a, b) in enumerate(zip(ecmp, aidc)):
        u = 1.55 + i * 0.62
        R.fquad(fr, u, 1.32, u + 0.22, 1.32 + a * 0.9, (240, 110, 90))
        R.fquad(fr, u + 0.26, 1.32, u + 0.48, 1.32 + b * 0.9, (90, 200, 140))
    # framed pictures on the left wall
    fl = R.wall_l_face()
    R.fquad(fl, 1.6, 1.3, 2.6, 2.1, (250, 250, 250), (150, 120, 90))
    R.fquad(fl, 1.7, 1.4, 2.5, 2.0, (100, 170, 220))
    R.fquad(fl, 3.0, 1.3, 3.8, 2.1, (250, 250, 250), (150, 120, 90))
    R.fquad(fl, 3.1, 1.4, 3.7, 2.0, (240, 160, 80))
    plant(R, 0.4, 0.4)
    # operator desk with three monitors
    R.box(1.8, 2.6, 0, 3.4, 1.0, 0.75, (236, 232, 224))
    for i in range(3):
        desk_monitor(R, 2.05 + i * 1.05, 2.75, 0.77, 0.95, (16, 26, 40))
    R.box(3.1, 3.3, 0.75, 0.6, 0.2, 0.03, (60, 60, 66))     # keyboard
    chair(R, 3.15, 4.2)
    layout["controller"] = R.screen(2.2, 4.4, 0)
    # sofa corner
    R.box(5.6, 4.0, 0, 1.0, 2.4, 0.45, (232, 120, 72))
    R.box(6.4, 4.0, 0.45, 0.2, 2.4, 0.45, (220, 104, 60))
    plant(R, 6.3, 0.4)
    f = lambda u, v: (u, R.D + 0.01, v)
    R.ftext(f, R.W / 2, -0.04, "NOC  ·  CONTROLLER", 0.16, (255, 255, 255), anchor_center=True)


def power_room(R):
    R.shell(floor=(186, 190, 196), wall_l=(238, 240, 242), wall_r=(255, 241, 201), slab=(128, 132, 140))
    fr = R.wall_r_face()
    # hazard stripe on the floor along the cabinets
    for i in range(22):
        x = 0.4 + i * 0.28
        R.poly([(x, 1.35, 0), (x + 0.14, 1.35, 0), (x + 0.24, 1.5, 0), (x + 0.1, 1.5, 0)], (30, 30, 30))
    R.poly([(0.4, 1.35, 0), (6.6, 1.35, 0), (6.6, 1.5, 0), (0.4, 1.5, 0)], None, (230, 190, 0))
    R.sign(R.wall_l_face(), 3.2, 2.48, 3.2, 0.5, (60, 64, 72), "POWER & COOLING", "UPS · PDU · CRAC", fg=(255, 214, 0))
    # electrical panel with warning triangle
    R.fquad(fr, 0.6, 1.0, 1.6, 2.3, (200, 204, 210), (120, 124, 130))
    R.poly([(0.85, 0, 1.9), (1.35, 0, 1.9), (1.1, 0, 2.2)], (255, 200, 0), (30, 30, 30))
    R.ftext(fr, 1.1, 2.12, "!", 0.2, (30, 30, 30), anchor_center=True)
    for i, x0 in enumerate([1.9, 2.75, 3.6, 4.45]):
        rack(R, x0, 0.25, "ups" if i < 2 else "storage", (255, 196, 0), w=0.8)
    crac(R, 0.15, 2.2)
    crac(R, 0.15, 4.2)
    for k in range(3):                          # fire-suppression cylinders
        R.box(5.6 + k * 0.32, 0.4, 0, 0.26, 0.26, 1.3, (214, 48, 48))
        R.box(5.66 + k * 0.32, 0.46, 1.3, 0.14, 0.14, 0.12, (60, 60, 60))
    extinguisher(R, 6.4, 6.2)
    f = lambda u, v: (u, R.D + 0.01, v)
    R.ftext(f, R.W / 2, -0.04, "POWER & COOLING", 0.16, (255, 255, 255), anchor_center=True)


def how_to_panel(d):
    """The three steps of the demo, top right, so the .pkt explains itself."""
    x0, y0, x1, y1 = 990, 16, 2130, 124
    d.rounded_rectangle([x0 * SS, y0 * SS, x1 * SS, y1 * SS], radius=12 * SS, fill=(255, 255, 255),
                        outline=(205, 212, 222), width=2 * SS)
    rows = [((46, 160, 90), "1", "Controller ON (default):", "AIDC-Net places every flow  ·  h31→h12 crosses spine2, every fabric link carries one elephant"),
            ((214, 69, 69), "2", "Click controller → Physical → power OFF:", "leaves fall back to the ECMP hash  ·  h31→h12 and h32→h11 collide on spine1→leaf1"),
            ((42, 120, 214), "3", "Check on h31:  tracert 10.0.1.2", "hop 2 = 172.16.2.9 (spine2, AIDC-Net)  or  172.16.1.9 (spine1, ECMP)")]
    fb, fr = font(FONT_B, 15 * SS), font(FONT_R, 15 * SS)
    for i, (col, n, head, rest) in enumerate(rows):
        y = (y0 + 22 + i * 33) * SS
        cx = (x0 + 26) * SS
        d.ellipse([cx - 11 * SS, y - 11 * SS, cx + 11 * SS, y + 11 * SS], fill=col)
        d.text((cx, y), n, font=fb, fill="white", anchor="mm")
        d.text(((x0 + 46) * SS, y), head, font=fb, fill=(32, 40, 56), anchor="lm")
        w = d.textlength(head, font=fb)
        d.text(((x0 + 54) * SS + w, y), rest, font=fr, fill=(90, 100, 118), anchor="lm")


def main():
    img = Image.new("RGBA", (IMG_W * SS, IMG_H * SS), BG + (255,))
    d = ImageDraw.Draw(img)
    # subtle dot grid
    for y in range(0, IMG_H * SS, 40 * SS):
        for x in range(0, IMG_W * SS, 40 * SS):
            d.ellipse([x - 2, y - 2, x + 2, y + 2], fill=(222, 226, 233))
    d.text((40 * SS, 26 * SS), "AIDC-Net  ·  AI Data Centre Fabric", font=font(FONT_B, 34 * SS), fill=(32, 40, 56))
    d.text((40 * SS, 70 * SS), "2 spines · 3 leaves · 6 GPU servers  —  adaptive load balancing of all-reduce "
           "elephant flows vs static ECMP", font=font(FONT_R, 18 * SS), fill=(96, 106, 124))

    how_to_panel(d)

    cols = [365, 1080, 1795]
    top_oy, bot_oy = 290, 860
    layout = {}
    noc_room(Room(img, cols[0], top_oy), layout)
    spine_room(Room(img, cols[1], top_oy), layout)
    power_room(Room(img, cols[2], top_oy))
    for l in (1, 2, 3):
        pod_room(Room(img, cols[l - 1], bot_oy), l, layout)

    out = img.resize((IMG_W, IMG_H), Image.LANCZOS).convert("RGB")
    png = os.path.join(HERE, "aidc_background.png")
    out.save(png, optimize=True)
    with open(os.path.join(HERE, "visual_layout.json"), "w") as fh:
        json.dump(dict(image="aidc_background.png", size=[IMG_W, IMG_H], devices=layout), fh, indent=1)
    print(png, json.dumps(layout))


if __name__ == "__main__":
    main()
