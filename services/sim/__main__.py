"""Run:  python -m services.sim   (deletes any existing database first)"""
import time

from services.common.config import db_path, load_config
from services.common.db import connect
from services.sim.simulate import simulate

if __name__ == "__main__":
    cfg, path = load_config(), db_path()
    for suffix in ("", "-wal", "-shm"):
        p = path.with_name(path.name + suffix)
        if p.exists():
            p.unlink()
    t0 = time.time()
    con = connect(path, create=True)
    stats = simulate(cfg, con)
    con.close()
    print(f"[sim] {stats}  ({time.time() - t0:.1f}s)  -> {path}")
