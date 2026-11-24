"""A stand-in for the vision service. It processes no images.

It answers the same question the real service will answer ("what does this vehicle look
like, as a vector?") by building a vector from the simulated truth:

    identity    = mix of a vector shared by every vehicle of that model and colour
                  and a vector unique to the vehicle
    observation = identity + random noise, different at every crossing

Two numbers per preset set how hard the problem is:
    lookalike_cos  how similar two different vehicles of the same model and colour look
    noise          how much two photos of the same vehicle differ
These are assumptions. Measurements on real photos would replace them.

This module may read truth columns, because it plays the part of the camera.
The matching engine only sees the vectors it returns.
"""
from __future__ import annotations

import math
import random
import sqlite3


def _unit(rng: random.Random, dim: int) -> list[float]:
    v = [rng.gauss(0, 1) for _ in range(dim)]
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))        # both are unit vectors


class SimulatedVision:
    def __init__(self, con: sqlite3.Connection, cfg: dict):
        self.seed, self.dim = cfg["seed"], cfg["camera"]["embedding_dim"]
        self.presets = cfg["camera"]["presets"]
        self._event = {r[0]: (r[1], r[2], r[3]) for r in con.execute(
            "SELECT c.event_id, v.vehicle_id, v.model, v.colour FROM crossings c "
            "JOIN vehicles v ON v.vehicle_id = c.truth_vehicle_id "
            "WHERE c.plaza_type = 'legacy' OR c.system_class = 'unidentified'")}
        self._class: dict[tuple[str, str], list[float]] = {}

    def embed(self, event_id: int) -> dict[str, list[float]]:
        """Return one unit vector per preset for this crossing."""
        vehicle_id, model, colour = self._event[event_id]
        key = (model, colour)
        if key not in self._class:
            self._class[key] = _unit(random.Random(f"{self.seed}|class|{model}|{colour}"), self.dim)
        shared = self._class[key]
        unique = _unit(random.Random(f"{self.seed}|vehicle|{vehicle_id}"), self.dim)
        noise = _unit(random.Random(f"{self.seed}|event|{event_id}"), self.dim)
        out = {}
        for name, p in self.presets.items():
            a, b = math.sqrt(p["lookalike_cos"]), math.sqrt(1 - p["lookalike_cos"])
            v = [a * s + b * u + p["noise"] * n for s, u, n in zip(shared, unique, noise)]
            norm = math.sqrt(sum(x * x for x in v))
            out[name] = [x / norm for x in v]
        return out


MARK_KINDS = {
    "car": ["sticker", "dent", "roof_rack", "stripe", "tow_hook"],
    "suv": ["sticker", "dent", "roof_rack", "stripe", "tow_hook", "spare_wheel"],
    "commercial": ["sticker", "dent", "stripe", "bumper_guard"],
}
STICKER_COLOURS = ["yellow", "blue", "green", "orange", "white"]   # never red: tail lights are red


def vehicle_marks(seed: int, vehicle_id: int, body: str, colour: str) -> list[dict]:
    """Stable visible marks for one simulated vehicle: 0 to 3, most vehicles have 1 or 2."""
    rng = random.Random(f"{seed}|marks|{vehicle_id}")
    group = "suv" if body in ("suv", "muv") else "car" if body in ("hatchback", "sedan") else "commercial"
    n = rng.choices([0, 1, 2, 3], [0.15, 0.35, 0.35, 0.15])[0]
    marks = []
    for kind in rng.sample(MARK_KINDS[group], n):
        m = {"kind": kind}
        if kind in ("sticker", "dent"):
            m["side"] = rng.choice(["left", "right"])
        if kind in ("sticker", "stripe"):
            m["colour"] = rng.choice([c for c in STICKER_COLOURS if c != colour])
        marks.append(m)
    return marks


class SimulatedCamera:
    """Renders a synthetic rear-view frame for a crossing. It plays the camera, so it may read truth.

    The frame shows the vehicle that really passed (body, colour, model-specific tail lights,
    the vehicle's own marks), the plate condition and per-frame noise. It never draws plate text.
    """

    def __init__(self, con: sqlite3.Connection, seed: int):
        self.con, self.seed = con, seed

    def frame(self, event_id: int, label: str = "") -> bytes | None:
        from services.vision.raster import render_frame
        r = self.con.execute(
            "SELECT v.vehicle_id, v.model, v.colour, v.body, c.plate_status FROM crossings c "
            "JOIN vehicles v ON v.vehicle_id = c.truth_vehicle_id WHERE c.event_id = ?", (event_id,)).fetchone()
        if r is None:
            return None
        vehicle = {"model": r[1], "colour": r[2], "body": r[3],
                   "marks": vehicle_marks(self.seed, r[0], r[3], r[2])}
        return render_frame(vehicle, r[4] or "readable", random.Random(f"{self.seed}|frame|{event_id}"), label=label)
