"""Synthetic rear-view camera frames as PNG, using the standard library only.

Pure drawing code: it is given a vehicle description and never reads the database.
Frames carry a "SIMULATED DATA" banner and never contain plate text.
"""
from __future__ import annotations

import random
import struct
import zlib

W, H = 320, 240

FONT = {
    "S": (" ####", "#    ", "#    ", " ### ", "    #", "    #", "#### "),
    "I": ("#####", "  #  ", "  #  ", "  #  ", "  #  ", "  #  ", "#####"),
    "M": ("#   #", "## ##", "# # #", "#   #", "#   #", "#   #", "#   #"),
    "U": ("#   #", "#   #", "#   #", "#   #", "#   #", "#   #", " ### "),
    "L": ("#    ", "#    ", "#    ", "#    ", "#    ", "#    ", "#####"),
    "A": (" ### ", "#   #", "#   #", "#####", "#   #", "#   #", "#   #"),
    "T": ("#####", "  #  ", "  #  ", "  #  ", "  #  ", "  #  ", "  #  "),
    "E": ("#####", "#    ", "#    ", "#### ", "#    ", "#    ", "#####"),
    "D": ("#### ", "#   #", "#   #", "#   #", "#   #", "#   #", "#### "),
    "G": (" ####", "#    ", "#    ", "#  ##", "#   #", "#   #", " ####"),
    "H": ("#   #", "#   #", "#   #", "#####", "#   #", "#   #", "#   #"),
    "O": (" ### ", "#   #", "#   #", "#   #", "#   #", "#   #", " ### "),
    "C": (" ####", "#    ", "#    ", "#    ", "#    ", "#    ", " ####"),
    "N": ("#   #", "##  #", "# # #", "#  ##", "#   #", "#   #", "#   #"),
    "1": ("  #  ", " ##  ", "  #  ", "  #  ", "  #  ", "  #  ", " ### "),
    "2": (" ### ", "#   #", "    #", "   # ", "  #  ", " #   ", "#####"),
    "3": ("#### ", "    #", "    #", " ### ", "    #", "    #", "#### "),
    "4": ("#   #", "#   #", "#   #", "#####", "    #", "    #", "    #"),
    "5": ("#####", "#    ", "#### ", "    #", "    #", "#   #", " ### "),
    "6": (" ### ", "#    ", "#    ", "#### ", "#   #", "#   #", " ### "),
    "7": ("#####", "    #", "   # ", "  #  ", " #   ", " #   ", " #   "),
    "8": (" ### ", "#   #", "#   #", " ### ", "#   #", "#   #", " ### "),
    "9": (" ### ", "#   #", "#   #", " ####", "    #", "    #", " ### "),
}

COLOURS = {
    "white": (236, 238, 240), "silver": (190, 195, 202), "grey": (120, 126, 134), "black": (32, 34, 38),
    "red": (178, 40, 38), "blue": (40, 90, 170), "brown": (115, 78, 50), "green": (50, 120, 70),
    "orange": (220, 120, 40), "yellow": (225, 190, 40),
}
# body -> (width, height, has rear window)
SHAPES = {
    "hatchback": (170, 92, True), "sedan": (180, 84, True), "suv": (186, 112, True), "muv": (184, 116, True),
    "pickup": (184, 96, False), "van": (180, 140, True), "bus": (210, 175, True),
    "two_axle_truck": (200, 160, False), "three_axle_truck": (210, 168, False), "trailer": (220, 175, False),
}
RED = (210, 30, 30)
SMALL_MARKS = {"sticker", "dent", "tow_hook"}   # can be missed in one frame; large marks are always seen


class Canvas:
    def __init__(self, w: int, h: int, rgb: tuple[int, int, int]):
        self.w, self.h = w, h
        self.px = bytearray(bytes(rgb) * (w * h))

    def rect(self, x0: float, y0: float, x1: float, y1: float, rgb: tuple[int, int, int]) -> None:
        x0, x1 = max(0, int(x0)), min(self.w, int(x1))
        y0, y1 = max(0, int(y0)), min(self.h, int(y1))
        if x0 >= x1:
            return
        row = bytes(rgb) * (x1 - x0)
        for y in range(y0, y1):
            i = (y * self.w + x0) * 3
            self.px[i:i + len(row)] = row

    def ellipse(self, cx: float, cy: float, rx: float, ry: float, rgb: tuple[int, int, int], alpha: float = 1.0) -> None:
        for y in range(max(0, int(cy - ry)), min(self.h, int(cy + ry) + 1)):
            dy = (y - cy) / ry
            if abs(dy) > 1:
                continue
            half = rx * (1 - dy * dy) ** 0.5
            for x in range(max(0, int(cx - half)), min(self.w, int(cx + half) + 1)):
                i = (y * self.w + x) * 3
                for k in range(3):
                    self.px[i + k] = int(self.px[i + k] * (1 - alpha) + rgb[k] * alpha)

    def text(self, x: int, y: int, s: str, rgb: tuple[int, int, int], scale: int = 2) -> None:
        for ch in s:
            for r, row in enumerate(FONT.get(ch, ())):
                for c, bit in enumerate(row):
                    if bit == "#":
                        self.rect(x + c * scale, y + r * scale, x + (c + 1) * scale, y + (r + 1) * scale, rgb)
            x += 6 * scale

    def adjust(self, rng: random.Random, brightness: float, noise: int) -> None:
        px, rand = self.px, rng.random
        for i in range(len(px)):
            v = px[i] * brightness + (rand() * 2 - 1) * noise
            px[i] = 0 if v < 0 else 255 if v > 255 else int(v)

    def png(self) -> bytes:
        stride = self.w * 3
        raw = b"".join(b"\x00" + bytes(self.px[y * stride:(y + 1) * stride]) for y in range(self.h))

        def chunk(tag: bytes, data: bytes) -> bytes:
            return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", self.w, self.h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def mark_text(mark: dict) -> str:
    kind = mark["kind"]
    if kind == "sticker":
        return f"{mark['colour']} sticker, upper {mark['side']}"
    if kind == "dent":
        return f"dent, lower {mark['side']}"
    if kind == "stripe":
        return f"{mark['colour']} body stripe"
    return {"roof_rack": "roof rack", "spare_wheel": "spare wheel cover", "bumper_guard": "metal bumper guard",
            "tow_hook": "tow hook"}[kind]


def _taillights(c: Canvas, model: str, x0: float, x1: float, y0: float, bh: float) -> None:
    shape = sum(map(ord, model)) % 4
    for left in (True, False):
        if shape == 0:
            a = x0 + 6 if left else x1 - 26
            c.rect(a, y0 + bh * 0.48, a + 20, y0 + bh * 0.62, RED)
        elif shape == 1:
            a = x0 + 4 if left else x1 - 14
            c.rect(a, y0 + bh * 0.2, a + 10, y0 + bh * 0.7, RED)
        elif shape == 3:
            c.ellipse(x0 + 18 if left else x1 - 18, y0 + bh * 0.55, 9, 9, RED)
    if shape == 2:
        c.rect(x0 + 6, y0 + bh * 0.5, x1 - 6, y0 + bh * 0.55, RED)


def _plate(c: Canvas, status: str, px: float, py: float, rng: random.Random, body: tuple[int, int, int]) -> None:
    if status == "missing":
        for x0, y0, x1, y1 in ((px, py, px + 60, py + 2), (px, py + 16, px + 60, py + 18),
                               (px, py, px + 2, py + 18), (px + 58, py, px + 60, py + 18)):
            c.rect(x0, y0, x1, y1, (40, 40, 40))
        return
    c.rect(px, py, px + 60, py + 18, (245, 245, 240))
    if status == "obstructed_deliberate":
        c.rect(px - 3, py - 2, px + 63, py + 20, (120, 90, 55))
        c.rect(px - 3, py + 6, px + 63, py + 8, (95, 70, 42))
    elif status == "damaged":
        for k in range(10):
            c.rect(px + 8 + k * 4, py + 2 + k * 1.4, px + 12 + k * 4, py + 5 + k * 1.4, (40, 40, 40))
        c.rect(px + 44, py, px + 60, py + 9, body)
    else:
        # Abstract marks, never real or derived plate text.
        for k in range(7):
            top = py + rng.randint(3, 6)
            c.rect(px + 5 + k * 7.5, top, px + 10 + k * 7.5, py + 15, (30, 30, 30))


def _mark(c: Canvas, m: dict, x0: float, x1: float, y0: float, bottom: float, bw: float, bh: float,
          window: bool) -> None:
    kind = m["kind"]
    if kind == "sticker":
        top = y0 + bh * (0.14 if window else 0.08)
        a = x0 + bw * 0.18 if m["side"] == "left" else x1 - bw * 0.18 - 16
        c.rect(a, top, a + 16, top + 11, COLOURS[m["colour"]])
    elif kind == "dent":
        c.ellipse(x0 + 30 if m["side"] == "left" else x1 - 30, bottom - bh * 0.22, 12, 7, (25, 25, 25), 0.55)
    elif kind == "roof_rack":
        c.rect(x0 + bw * 0.18, y0 - 9, x1 - bw * 0.18, y0 - 5, (30, 30, 32))
        c.rect(x0 + bw * 0.24, y0 - 6, x0 + bw * 0.24 + 4, y0, (30, 30, 32))
        c.rect(x1 - bw * 0.24 - 4, y0 - 6, x1 - bw * 0.24, y0, (30, 30, 32))
    elif kind == "spare_wheel":
        c.ellipse(x1 - bw * 0.28, y0 + bh * 0.64, 15, 15, (28, 28, 30))
        c.ellipse(x1 - bw * 0.28, y0 + bh * 0.64, 7, 7, (90, 90, 95))
    elif kind == "bumper_guard":
        c.rect(x0 + 10, bottom - 10, x1 - 10, bottom - 4, (160, 162, 168))
    elif kind == "stripe":
        c.rect(x0, y0 + bh * 0.66, x1, y0 + bh * 0.66 + 5, COLOURS[m["colour"]])
    elif kind == "tow_hook":
        c.rect((x0 + x1) / 2 - 4, bottom - 2, (x0 + x1) / 2 + 4, bottom + 6, (25, 25, 25))


def render_frame(vehicle: dict, plate_status: str, rng: random.Random, drop: float = 0.2, label: str = "") -> bytes:
    """Rear view of `vehicle` ({model, colour, body, marks}). Each small mark is missed with probability `drop`.

    `label` (for example "GHOST" or "CAND 3") is printed in the top-right corner.
    """
    c = Canvas(W, H, (150, 170, 190))
    c.rect(0, 120, W, H, (74, 76, 80))
    for x in range(10, W, 60):
        c.rect(x, 225, x + 30, 229, (210, 210, 210))
    bw, bh, window = SHAPES.get(vehicle["body"], (180, 100, True))
    s = rng.uniform(0.94, 1.06)
    bw, bh = bw * s, bh * s
    cx, bottom = W / 2 + rng.uniform(-12, 12), 208.0
    x0, x1, y0 = cx - bw / 2, cx + bw / 2, bottom - bh
    body = COLOURS.get(vehicle["colour"], (150, 150, 150))
    dark = tuple(int(v * 0.55) for v in body)
    c.rect(x0 + 8, bottom - 4, x0 + 38, bottom + 14, (20, 20, 22))
    c.rect(x1 - 38, bottom - 4, x1 - 8, bottom + 14, (20, 20, 22))
    c.rect(x0, y0, x1, bottom, body)
    c.rect(x0, y0, x1, y0 + 3, dark)
    if window:
        c.rect(x0 + bw * 0.14, y0 + bh * 0.1, x1 - bw * 0.14, y0 + bh * 0.42, (45, 55, 68))
    else:
        for k in range(3):
            y = y0 + bh * (0.15 + 0.12 * k)
            c.rect(x0 + 6, y, x1 - 6, y + 2, dark)
    _taillights(c, vehicle["model"], x0, x1, y0, bh)
    py = bottom - bh * 0.32
    _plate(c, plate_status, cx - 30, py, rng, body)
    for m in vehicle["marks"]:
        if m["kind"] not in SMALL_MARKS or rng.random() >= drop:
            _mark(c, m, x0, x1, y0, bottom, bw, bh, window)
    if plate_status == "glare_weather":
        c.ellipse(cx + rng.uniform(-30, 30), py + 8, 70, 34, (255, 255, 250), 0.85)
    c.rect(0, 0, W, 22, (15, 18, 24))
    c.text(6, 4, "SIMULATED DATA", (255, 210, 90))
    c.adjust(rng, rng.uniform(0.8, 1.12), 10)
    if label:
        c.rect(W - 12 * len(label) - 10, 0, W, 22, (255, 255, 255))
        c.text(W - 12 * len(label) - 4, 4, label, (10, 10, 10))
    return c.png()
