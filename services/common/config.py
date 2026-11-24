"""Load the corridor config. Environment variables override a few values.

GT_CONFIG  path to the config file      (default: config/corridor.json)
GT_DB      path to the SQLite database  (default: data/ghosttrace.db)
GT_DAYS    number of simulated days     (default: from config)
GT_SEED    random seed                  (default: from config)
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_config() -> dict:
    path = Path(os.environ.get("GT_CONFIG", ROOT / "config" / "corridor.json"))
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if os.environ.get("GT_DAYS"):
        cfg["traffic"]["days"] = int(os.environ["GT_DAYS"])
    if os.environ.get("GT_SEED"):
        cfg["seed"] = int(os.environ["GT_SEED"])
    cfg["plazas"] = sorted(cfg["plazas"], key=lambda p: p["km"])
    return cfg


def db_path() -> Path:
    return Path(os.environ.get("GT_DB", ROOT / "data" / "ghosttrace.db"))


def typical_speed(cfg: dict, speed_class: str) -> float:
    lo, hi = cfg["traffic"]["speed_kmph"][speed_class]
    return (lo + hi) / 2.0
