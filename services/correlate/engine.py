"""The matching engine.

Step 1 (build_pools): for every ghost crossing, find every identified crossing at a
legacy plaza that (a) is in the same direction, (b) falls near the expected travel time,
and (c) belongs to a registered vehicle whose make, model, colour and body agree with what
the gantry camera saw. Store each one with its time offset and image similarity.

Step 2 (decide): apply a window, a similarity threshold and a margin to the stored pool.
Keeping the two steps apart lets the reliability dial try any setting without a re-run.

This module reads `pipeline_crossings` and `vahan` only. It never reads truth.
"""
from __future__ import annotations

import bisect
import sqlite3
from dataclasses import dataclass

from services.common.config import typical_speed
from services.common.fleet import speed_class_of
from services.vision.simulated import cosine

PRESETS = ("optimistic", "central", "pessimistic")


@dataclass
class Candidate:
    vrn: str
    plaza_id: str
    offset: float              # minutes from the expected arrival time; 0 is exactly on time
    sims: tuple[float, float, float]   # image similarity under each preset, same order as PRESETS


@dataclass
class Decision:
    decision: str              # recommend | review | unresolved
    top: Candidate | None
    runner_up: float | None
    n_candidates: int          # distinct registrations that passed every gate


def build_pools(con: sqlite3.Connection, cfg: dict, vision) -> int:
    km = {p["id"]: p["km"] for p in cfg["plazas"]}
    legacy = [p["id"] for p in cfg["plazas"] if p["type"] == "legacy"]
    half = cfg["matching"]["max_window_minutes"] * 60 / 2

    index: dict[tuple, tuple[list[float], list[tuple[int, str]]]] = {}
    for r in con.execute(
            "SELECT c.event_id, c.plaza_id, c.direction, c.ts, c.anpr_text, v.make, v.model, v.colour, v.body "
            "FROM pipeline_crossings c JOIN vahan v ON v.vrn = c.anpr_text "
            "WHERE c.plaza_type = 'legacy' AND c.anpr_text IS NOT NULL ORDER BY c.ts"):
        times, rows = index.setdefault((r[1], r[2], r[5], r[6], r[7], r[8]), ([], []))
        times.append(r[3])
        rows.append((r[0], r[4]))

    ghosts = con.execute(
        "SELECT event_id, plaza_id, direction, ts, obs_make, obs_model, obs_colour, obs_body "
        "FROM pipeline_crossings WHERE plaza_type = 'mlff' AND system_class = 'unidentified' "
        "ORDER BY ts").fetchall()
    con.execute("DELETE FROM candidates")
    out = []
    for g in ghosts:
        event_id, plaza, direction, ts = g[0], g[1], g[2], g[3]
        speed = typical_speed(cfg, speed_class_of(g[7]))
        ghost_vec = None
        for lp in legacy:
            ahead = (km[lp] - km[plaza]) * direction > 0     # is the legacy plaza further along the trip?
            expected = ts + (1 if ahead else -1) * abs(km[lp] - km[plaza]) / speed * 3600
            entry = index.get((lp, direction, g[4], g[5], g[6], g[7]))
            if not entry:
                continue
            times, rows = entry
            for i in range(bisect.bisect_left(times, expected - half), bisect.bisect_right(times, expected + half)):
                if ghost_vec is None:
                    ghost_vec = vision.embed(event_id)
                anchor_vec = vision.embed(rows[i][0])
                sims = [cosine(ghost_vec[p], anchor_vec[p]) for p in PRESETS]
                out.append((event_id, rows[i][0], rows[i][1], lp, abs(times[i] - ts) / 60,
                            (times[i] - expected) / 60, *sims))
    con.executemany("INSERT INTO candidates VALUES (?,?,?,?,?,?,?,?,?)", out)
    con.commit()
    return len(ghosts)


def decide(pool: list[Candidate], window: float, method: str, threshold: float = 0.0,
           margin: float = 0.0, preset: int = 1, max_review: int = 5) -> Decision:
    """Apply the matching rule to one ghost's pool.

    method "reported": time window + attributes only (the method described in the news report).
    method "full":     adds the image gate and the margin over the runner-up.
    A registration seen at both legacy plazas counts once; its best similarity is used.
    """
    best: dict[str, Candidate] = {}
    for c in pool:
        if abs(c.offset) <= window / 2:
            cur = best.get(c.vrn)
            if cur is None or c.sims[preset] > cur.sims[preset]:
                best[c.vrn] = c
    cands = sorted(best.values(), key=lambda c: -c.sims[preset])
    if method == "reported":
        n = len(cands)
        if n == 1:
            return Decision("recommend", cands[0], None, 1)
        return Decision("review" if 2 <= n <= max_review else "unresolved", None, None, n)
    passing = [c for c in cands if c.sims[preset] >= threshold]
    n = len(passing)
    if n == 0 or n > max_review:
        return Decision("unresolved", None, None, n)
    runner = cands[1].sims[preset] if len(cands) > 1 else None
    if n == 1 and (runner is None or passing[0].sims[preset] - runner >= margin):
        return Decision("recommend", passing[0], runner, 1)
    return Decision("review", passing[0], runner, n)


def load_pools(con: sqlite3.Connection) -> dict[int, list[Candidate]]:
    pools: dict[int, list[Candidate]] = {}
    for r in con.execute("SELECT case_id, vrn, plaza_id, offset_minutes, sim_optimistic, sim_central, "
                         "sim_pessimistic FROM candidates"):
        pools.setdefault(r[0], []).append(Candidate(r[1], r[2], r[3], (r[4], r[5], r[6])))
    return pools


def write_cases(con: sqlite3.Connection, settings: dict) -> dict[str, int]:
    """Decide every ghost with the tuned settings and store the result."""
    pools = load_pools(con)
    ghosts = [r[0] for r in con.execute(
        "SELECT event_id FROM pipeline_crossings WHERE plaza_type = 'mlff' AND system_class = 'unidentified'")]
    con.execute("DELETE FROM cases")
    counts = {"recommend": 0, "review": 0, "unresolved": 0}
    rows = []
    for event_id in ghosts:
        d = decide(pools.get(event_id, []), settings["window"], "full", settings["threshold"],
                   settings["margin"], PRESETS.index(settings["preset"]))
        counts[d.decision] += 1
        rows.append((event_id, event_id, d.decision, d.top.vrn if d.top else None,
                     d.top.sims[PRESETS.index(settings["preset"])] if d.top else None,
                     d.runner_up, d.n_candidates, d.top.plaza_id if d.top else None, None, None, None))
    con.executemany("INSERT INTO cases VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    return counts
