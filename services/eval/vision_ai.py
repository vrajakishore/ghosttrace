"""Measure the AI vision matcher against simulated truth on a sample of test ghosts.

python -m services.eval.vision_ai --n 30

Needs MODEL_BASE_URL and MODEL_NAME (a vision-capable deployment) and `az login`.
Each case is one model call with up to nine small images. Results go to docs/results/ai-vision-v0.*.
"""
from __future__ import annotations

import argparse
import json
import os
import random
from datetime import datetime, timezone

from services.api.server import App
from services.common.config import ROOT
from services.vision import ai_match

OUT = ROOT / "docs" / "results"


def run(n: int, seed: int) -> dict:
    app = App()
    if not ai_match.is_enabled():
        raise SystemExit("Set MODEL_BASE_URL and MODEL_NAME, and install requirements-foundry.txt.")
    rows = app.q("SELECT k.case_id, k.decision, k.top_vrn, v.vrn FROM cases k "
                 "JOIN crossings c ON c.event_id = k.ghost_event_id "
                 "JOIN vehicles v ON v.vehicle_id = c.truth_vehicle_id "
                 "JOIN pipeline_crossings p ON p.event_id = k.ghost_event_id WHERE p.ts >= 86400")
    rng = random.Random(seed)
    rng.shuffle(rows)
    picked, per_case = [], []
    for case_id, engine, engine_vrn, truth_vrn in rows:
        lineup = app.vision_lineup(case_id)
        if len(lineup) < 2:          # one or zero candidates is not a visual question
            continue
        picked.append(case_id)
        result = app.vision_match(case_id)
        in_lineup = truth_vrn in [c["vrn"] for c in lineup]
        item = {"case_id": case_id, "lineup": len(lineup), "truth_in_lineup": in_lineup,
                "engine": engine, "engine_correct": engine == "recommend" and engine_vrn == truth_vrn,
                "ai_status": result["status"], "ai": result.get("decision"),
                "ai_correct": result.get("decision") == "recommend" and result.get("named_vrn") == truth_vrn,
                "ai_top_is_truth": bool(result.get("candidates")) and result["candidates"][0]["vrn"] == truth_vrn,
                "latency_ms": result.get("latency_ms")}
        per_case.append(item)
        print(f"case {case_id}: line-up {item['lineup']}, truth in line-up {in_lineup}, "
              f"engine {engine}{' ok' if item['engine_correct'] else ''}, AI {item['ai']}{' ok' if item['ai_correct'] else ''}")
        if len(picked) >= n:
            break

    def count(key: str, value) -> int:
        return sum(1 for r in per_case if r[key] == value)

    ok = [r for r in per_case if r["ai_status"] == "ok"]
    ai_rec = [r for r in ok if r["ai"] == "recommend"]
    eng_rec = [r for r in per_case if r["engine"] == "recommend"]
    summary = {
        "simulated_data": True,
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": os.environ.get("MODEL_NAME"), "seed": seed,
        "cases": len(per_case), "model_answers": len(ok), "truth_in_lineup": count("truth_in_lineup", True),
        "ai": {"recommend": len(ai_rec), "correct": sum(r["ai_correct"] for r in ai_rec),
               "review": count("ai", "review"), "unresolved": count("ai", "unresolved"),
               "top_ranked_is_truth": sum(r["ai_top_is_truth"] for r in ok)},
        "engine_same_cases": {"recommend": len(eng_rec), "correct": sum(r["engine_correct"] for r in eng_rec),
                              "review": count("engine", "review"), "unresolved": count("engine", "unresolved")},
        "rule": {"recommend_score": ai_match.RECOMMEND_SCORE, "recommend_margin": ai_match.RECOMMEND_MARGIN,
                 "review_score": ai_match.REVIEW_SCORE, "max_candidates": ai_match.MAX_CANDIDATES},
        "per_case": per_case,
    }
    return summary


def write(summary: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "ai-vision-v0.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    a, e = summary["ai"], summary["engine_same_cases"]
    prec = f"{a['correct']}/{a['recommend']}" if a["recommend"] else "n/a"
    rows = summary["per_case"]
    wrong_absent = sum(1 for r in rows if r["ai"] == "recommend" and not r["ai_correct"] and not r["truth_in_lineup"])
    extra = sum(1 for r in rows if r["ai_correct"] and r["engine"] != "recommend")
    lines = [
        "# AI vision matching, first measurement (simulated data)",
        "",
        "Simulated data. Synthetic camera frames rendered by `services/vision/raster.py`; no real images.",
        "",
        f"Model: `{summary['model']}`. Sample: {summary['cases']} test ghosts with two or more time-feasible "
        f"look-alike candidates (seed {summary['seed']}). Valid model answers: {summary['model_answers']}.",
        f"The true vehicle was in the AI line-up in {summary['truth_in_lineup']} of {summary['cases']} cases.",
        "",
        "| | Named a vehicle | Named correctly | Several candidates | Unresolved |",
        "|---|---:|---:|---:|---:|",
        f"| AI vision match | {a['recommend']} | {prec} | {a['review']} | {a['unresolved']} |",
        f"| Engine (simulated similarity) | {e['recommend']} | {e['correct']}/{e['recommend']} | {e['review']} | {e['unresolved']} |",
        "",
        f"AI ranked the true vehicle first in {a['top_ranked_is_truth']} of {summary['model_answers']} answers.",
        f"Wrong AI names where the true vehicle was not in the line-up: {wrong_absent} of {a['recommend'] - a['correct']}.",
        f"Correct AI names on cases the engine did not name: {extra}.",
        "",
        "The engine's image similarity is an assumed score built from simulation truth (central preset); "
        "the AI reads pixels. The two rows are not a like-for-like comparison of image quality.",
        "",
        "This is a small sample on cartoon-like renders. It tests the workflow and the decision rule, "
        "not performance on real toll-camera footage.",
    ]
    (OUT / "ai-vision-v0.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    summary = run(args.n, args.seed)
    write(summary)
    print(json.dumps({k: v for k, v in summary.items() if k != "per_case"}, indent=2))


if __name__ == "__main__":
    main()
