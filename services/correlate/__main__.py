"""Run:  python -m services.correlate pools    build the candidate pool for every ghost
         python -m services.correlate decide   decide every case with the tuned settings"""
import json
import sys
import time

from services.common.config import db_path, load_config
from services.common.db import connect, get
from services.correlate.engine import build_pools, write_cases
from services.vision.simulated import SimulatedVision

if __name__ == "__main__":
    step = sys.argv[1] if len(sys.argv) > 1 else "pools"
    cfg, con, t0 = load_config(), connect(db_path()), time.time()
    if step == "pools":
        n = build_pools(con, cfg, SimulatedVision(con, cfg))
        rows = con.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
        print(f"[correlate] {n} ghost crossings, {rows} candidate rows  ({time.time() - t0:.1f}s)")
    elif step == "decide":
        settings = json.loads(get(con, "tuned_settings") or "null")
        if not settings:
            sys.exit("No tuned settings found. Run `python -m services.eval tune` first.")
        print(f"[correlate] decisions with {settings}: {write_cases(con, settings)}  ({time.time() - t0:.1f}s)")
    else:
        sys.exit(__doc__)
