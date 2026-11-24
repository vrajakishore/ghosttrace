"""Simulate traffic on a fictional toll corridor and write it to the database.

Everything here is invented. Registration numbers use the unissued state code "XX".
The same seed always gives the same output.
"""
from __future__ import annotations

import bisect
import itertools
import random
import sqlite3

from services.common import fleet
from services.common.db import INDEXES, put


def _cumulative(weights: list[float]) -> list[float]:
    total = float(sum(weights))
    return list(itertools.accumulate(w / total for w in weights))


def _pick(rng: random.Random, cum: list[float]) -> int:
    return min(bisect.bisect_left(cum, rng.random()), len(cum) - 1)


def trip_pairs(cfg: dict) -> dict[str, list[tuple[float, float]]]:
    """Group entry/exit pairs by how many legacy plazas the trip crosses."""
    legacy = [p["km"] for p in cfg["plazas"] if p["type"] == "legacy"]
    allp = [p["km"] for p in cfg["plazas"]]
    groups: dict[str, list[tuple[float, float]]] = {"through": [], "one_legacy": [], "no_legacy": []}
    for a, b in itertools.combinations(sorted(cfg["nodes_km"]), 2):
        if not any(a < km < b for km in allp):
            continue
        n = sum(1 for km in legacy if a < km < b)
        groups["through" if n >= 2 else "one_legacy" if n == 1 else "no_legacy"].append((a, b))
    return groups


def trips_per_day(cfg: dict) -> int:
    """Scale the number of trips so that the busiest MLFF gantry sees `vehicles_per_day`."""
    groups, mix = trip_pairs(cfg), cfg["traffic"]["trip_mix"]
    best = 0.0
    for p in cfg["plazas"]:
        if p["type"] != "mlff":
            continue
        share = sum(mix[g] * sum(1 for a, b in pairs if a < p["km"] < b) / len(pairs)
                    for g, pairs in groups.items() if pairs)
        best = max(best, share)
    return round(cfg["traffic"]["vehicles_per_day"] / best)


def simulate(cfg: dict, con: sqlite3.Connection) -> dict:
    rng = random.Random(cfg["seed"])
    tr, ev, cam = cfg["traffic"], cfg["evasion"], cfg["camera"]
    plazas = cfg["plazas"]

    models = fleet.model_table()
    model_cum = _cumulative([m[5] for m in models])
    by_body: dict[str, list[int]] = {}
    for i, m in enumerate(models):
        by_body.setdefault(m[2], []).append(i)
    colour_cum = {sc: _cumulative([w for _, w in fleet.colours_for(sc)]) for sc in ("car", "lcv", "truck")}
    hour_cum = _cumulative(tr["hourly_profile"])
    groups = trip_pairs(cfg)
    group_names = [g for g in groups if groups[g]]
    group_cum = _cumulative([tr["trip_mix"][g] for g in group_names])
    status_names = list(ev["plate_status_mix"])
    status_cum = _cumulative([ev["plate_status_mix"][s] for s in status_names])

    p_deliberate = ev["ghost_share"] * (1 - ev["honest_failure_share"])
    p_honest = ev["ghost_share"] * ev["honest_failure_share"]
    n_trips = trips_per_day(cfg)
    letters = "ABCDEFGHJKLMNPQRSTUVWXYZ"

    def observe(mi: int, colour: str) -> tuple[str, str, str, str]:
        """What the gantry camera reports. It is sometimes wrong."""
        make, model, body = models[mi][0], models[mi][1], models[mi][2]
        if rng.random() < cam["p_body_wrong"]:
            make, model, body = models[_pick(rng, model_cum)][:3]
        elif rng.random() < cam["p_model_wrong"] and len(by_body[body]) > 1:
            other = rng.choice([i for i in by_body[body] if i != mi])
            make, model = models[other][0], models[other][1]
        if rng.random() < cam["p_colour_wrong"]:
            colour = rng.choice(fleet.CONFUSION[colour])
        return make, model, colour, body

    vehicles, vahan, trips, crossings = [], [], [], []
    vid = eid = 0
    for day in range(tr["days"]):
        for _ in range(n_trips):
            vid += 1
            mi = _pick(rng, model_cum)
            make, model, body, speed_class, axles, _w = models[mi]
            colour = fleet.colours_for(speed_class)[_pick(rng, colour_cum[speed_class])][0]
            vrn = f"XX{rng.randrange(1, 100):02d}{rng.choice(letters)}{rng.choice(letters)}{vid % 10000:04d}{vid // 10000:02d}"
            tag = f"TAG{vid:08d}"
            deliberate = rng.random() < p_deliberate
            vehicles.append((vid, vrn, make, model, colour, body, axles, tag,
                             "deliberate" if deliberate else "none"))
            vahan.append((vrn, make, model, colour, body, axles))

            a, b = rng.choice(groups[group_names[_pick(rng, group_cum)]])
            direction = 1 if rng.random() < 0.5 else -1
            start_km, end_km = (a, b) if direction == 1 else (b, a)
            lo, hi = tr["speed_kmph"][speed_class]
            cruise = rng.triangular(lo, hi)
            t = day * 86400 + _pick(rng, hour_cum) * 3600 + rng.random() * 3600
            start_ts, pos = t, start_km
            route = [p for p in plazas if a < p["km"] < b]
            if direction == -1:
                route.reverse()
            for p in route:
                leg_speed = cruise * min(1.15, max(0.85, rng.gauss(1.0, 0.04)))
                t += abs(p["km"] - pos) / leg_speed * 3600
                if rng.random() < tr["stop_probability_per_leg"]:
                    t += rng.uniform(*tr["stop_minutes"]) * 60
                pos = p["km"]
                eid += 1
                if p["type"] == "legacy":
                    rate = tr["evader_legacy_identified_rate"] if deliberate else tr["legacy_identified_rate"]
                    seen = rng.random() < rate          # otherwise: paid by cash, no digital identity
                    crossings.append((eid, p["id"], "legacy", direction, t, tag if seen else None,
                                      vrn if seen else None, "readable", None, None, None, None,
                                      "normal", vid, vid, None))
                    continue
                om, omod, ocol, obody = observe(mi, colour)
                if deliberate:
                    status = status_names[_pick(rng, status_cum)]
                    crossings.append((eid, p["id"], "mlff", direction, t, None, None, status,
                                      om, omod, ocol, obody, "unidentified", vid, vid, "deliberate"))
                elif rng.random() < p_honest:
                    crossings.append((eid, p["id"], "mlff", direction, t, None, None, "glare_weather",
                                      om, omod, ocol, obody, "unidentified", vid, vid, "honest"))
                else:
                    crossings.append((eid, p["id"], "mlff", direction, t, tag, vrn, "readable",
                                      om, omod, ocol, obody, "normal", vid, vid, None))
            t += abs(end_km - pos) / cruise * 3600
            trips.append((vid, vid, day, direction, start_ts, t, start_km, end_km))

    con.executemany("INSERT INTO vehicles VALUES (?,?,?,?,?,?,?,?,?)", vehicles)
    con.executemany("INSERT INTO vahan VALUES (?,?,?,?,?,?)", vahan)
    con.executemany("INSERT INTO trips VALUES (?,?,?,?,?,?,?,?)", trips)
    con.executemany("INSERT INTO crossings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", crossings)
    con.executescript(INDEXES)
    con.commit()
    stats = {"days": tr["days"], "trips": len(trips), "crossings": len(crossings),
             "trips_per_day": n_trips,
             "ghost_crossings": sum(1 for c in crossings if c[12] == "unidentified")}
    put(con, "sim_stats", __import__("json").dumps(stats))
    return stats
