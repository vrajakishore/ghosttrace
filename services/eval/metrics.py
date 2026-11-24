"""Evaluation. This is the only part of the system that reads truth.

Day 1 of the simulation is the validation set: thresholds are chosen on it.
All later days are the test set: every reported number comes from them.
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass

from services.correlate.engine import PRESETS, Candidate, decide, load_pools

WINDOWS = [5, 10, 20, 30, 45, 60]
THRESHOLDS = [round(0.30 + 0.05 * i, 2) for i in range(14)]      # 0.30 .. 0.95
MARGINS = [0.0, 0.05, 0.10, 0.15, 0.20]


@dataclass
class Case:
    case_id: int
    day: int
    true_vrn: str
    reachable: bool            # the vehicle was identified at a legacy plaza on the same trip
    pool: list[Candidate]
    plaza_id: str = ""         # the gantry where the ghost crossed
    nearest: str = ""          # the legacy plaza closest to that gantry
    reachable_nearest: bool = False


def nearest_legacy(cfg: dict) -> dict[str, str]:
    legacy = [p for p in cfg["plazas"] if p["type"] == "legacy"]
    return {p["id"]: min(legacy, key=lambda l: abs(l["km"] - p["km"]))["id"] for p in cfg["plazas"]}


def load_cases(con: sqlite3.Connection, cfg: dict) -> list[Case]:
    pools, near = load_pools(con), nearest_legacy(cfg)
    seen: dict[int, set[str]] = {}
    for r in con.execute("SELECT truth_vehicle_id, plaza_id FROM crossings "
                         "WHERE plaza_type = 'legacy' AND anpr_text IS NOT NULL"):
        seen.setdefault(r[0], set()).add(r[1])
    return [Case(r[0], int(r[1] // 86400), r[2], r[3] in seen, pools.get(r[0], []), r[4], near[r[4]],
                 near[r[4]] in seen.get(r[3], ()))
            for r in con.execute(
                "SELECT c.event_id, c.ts, v.vrn, v.vehicle_id, c.plaza_id FROM crossings c "
                "JOIN vehicles v ON v.vehicle_id = c.truth_vehicle_id "
                "WHERE c.plaza_type = 'mlff' AND c.system_class = 'unidentified'")]


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """95% confidence interval for a proportion."""
    if n == 0:
        return None
    p, d = k / n, 1 + z * z / n
    centre, half = (p + z * z / (2 * n)) / d, z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, centre - half), min(1.0, centre + half)


def evaluate(cases: list[Case], window: float, method: str, threshold: float = 0.0,
             margin: float = 0.0, preset: str = "central", scope: str = "both") -> dict:
    """scope "both": search both legacy plazas. scope "nearest": search only the closest one."""
    pi, nearest = PRESETS.index(preset), scope == "nearest"
    out = {"ghosts": len(cases),
           "reachable": sum(c.reachable_nearest if nearest else c.reachable for c in cases),
           "recommend": 0, "correct": 0, "wrong": 0, "review": 0, "unresolved": 0}
    for c in cases:
        pool = [k for k in c.pool if k.plaza_id == c.nearest] if nearest else c.pool
        d = decide(pool, window, method, threshold, margin, pi)
        out[d.decision] += 1
        if d.decision == "recommend":
            out["correct" if d.top.vrn == c.true_vrn else "wrong"] += 1
    out["precision"] = out["correct"] / out["recommend"] if out["recommend"] else None
    out["precision_ci"] = wilson(out["correct"], out["recommend"])
    out["resolution"] = out["correct"] / out["reachable"] if out["reachable"] else None
    out.update(window=window, method=method, threshold=threshold, margin=margin, preset=preset, scope=scope)
    return out


def tune(validation: list[Case], preset: str, target: float, min_recommend: int = 100) -> dict | None:
    """Pick the setting with the highest resolution that still meets the precision target."""
    best = None
    for w in WINDOWS:
        for t in THRESHOLDS:
            for m in MARGINS:
                r = evaluate(validation, w, "full", t, m, preset)
                if r["recommend"] < min_recommend or r["precision"] < target:
                    continue
                if best is None or r["resolution"] > best["resolution"] + 1e-9:
                    best = r
    return best and {"preset": preset, "window": best["window"], "threshold": best["threshold"],
                     "margin": best["margin"]}


def candidate_histogram(cases: list[Case], window: float) -> dict[str, int]:
    """How many registrations pass the time and attribute gates at the nearest legacy plaza, per ghost."""
    hist = {"0": 0, "1": 0, "2-5": 0, "6+": 0}
    for c in cases:
        n = decide([k for k in c.pool if k.plaza_id == c.nearest], window, "reported").n_candidates
        hist["0" if n == 0 else "1" if n == 1 else "2-5" if n <= 5 else "6+"] += 1
    return hist
