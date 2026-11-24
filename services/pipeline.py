"""Run the whole pipeline once:  python -m services.pipeline
Skips the work if the database already holds results (set GT_FORCE=1 to rebuild)."""
import os
import subprocess
import sys

from services.common.config import db_path

STEPS = [["services.sim"], ["services.correlate", "pools"], ["services.eval", "tune"],
         ["services.correlate", "decide"], ["services.eval", "report"]]

if __name__ == "__main__":
    if db_path().exists() and not os.environ.get("GT_FORCE"):
        import sqlite3
        try:
            done = sqlite3.connect(str(db_path())).execute("SELECT 1 FROM kv WHERE k = 'results'").fetchone()
        except sqlite3.Error:
            done = None
        if done:
            print("[pipeline] results already exist; nothing to do (GT_FORCE=1 rebuilds)")
            sys.exit(0)
    for step in STEPS:
        subprocess.run([sys.executable, "-m", *step], check=True)
    print("[pipeline] done")
