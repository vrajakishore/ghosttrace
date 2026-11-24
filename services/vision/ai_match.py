"""AI vision matching on simulated camera frames.

Step 1, AI vision: a vision model reads every labelled frame (the ghost and each time-feasible
candidate) into a fingerprint: body, colour, plate condition and distinctive marks.
Step 2, comparison: fixed code compares the ghost fingerprint with each candidate fingerprint
and names a vehicle only when exactly one candidate agrees and none of its marks conflict.

The model only receives pixels and pipeline data. It never receives truth.
The result is a recommendation for a human reviewer, shown next to the engine's decision.
"""
from __future__ import annotations

import logging
import os
from time import perf_counter

from services.common.config import ROOT
from services.common.llm import ModelAnswerError, complete_json, is_configured

SCHEMA = ROOT / "schemas" / "vision-match.schema.json"
MAX_CANDIDATES = 8
RECOMMEND_SCORE = 0.6
RECOMMEND_MARGIN = 0.25
REVIEW_SCORE = 0.3
SMALL = {"sticker", "dent", "tow_hook"}           # can be missed in one frame
LARGE = {"roof_rack", "spare_wheel", "stripe", "bumper_guard"}
LOW_ON_BODY = {"stripe", "bumper_guard", "dent", "tow_hook"}   # can sit under plate glare
BLOCKED_WORDS = ("fine", "fined", "penalty", "penalties", "offence", "offense", "guilty", "prosecute", "punish")
logger = logging.getLogger("ghosttrace.vision_ai")

SYSTEM = """You read simulated toll-camera frames. Every image is a synthetic rear view marked SIMULATED DATA,
with a label in its top-right corner: GHOST, or CAND 1, CAND 2 and so on.
For every image, report the label exactly as printed, the body type, the body colour, the plate condition
(readable, covered, missing, damaged, glare) and every visible distinctive mark.
Mark kinds: sticker (colour and side), dent (side), roof_rack, spare_wheel, stripe (colour of the body stripe),
bumper_guard (metal bar across the bottom), tow_hook (small dark hook under the bumper centre), other.
Ignore the plate, the window and the banner. Tail lights are the red shapes at both outer edges or a red bar
across the back; every vehicle has them and they are never stickers. Stickers are never red.
Do not invent marks you cannot see.
Plates on candidates are blanked on purpose. Never read or guess a registration number.
In notes, say in one or two sentences which frames were hard to read and why. Never mention penalties."""


def is_enabled() -> bool:
    if not is_configured() or not os.environ.get("MODEL_NAME"):
        return False
    try:
        import jsonschema  # noqa: F401
        import openai  # noqa: F401
    except ImportError:
        return False
    return True


def _key(mark: dict) -> tuple:
    kind = mark["kind"]
    colour = (mark.get("colour") or "").strip().lower() if kind in ("sticker", "stripe") else ""
    side = (mark.get("side") or "") if kind in ("sticker", "dent") else ""
    return kind, colour, side


def _label(kind: str, colour: str, side: str) -> str:
    return " ".join(x for x in (colour, kind.replace("_", " "), f"({side})" if side else "") if x)


def compare(ghost: list[dict], candidate: list[dict], ghost_glare: bool, common: frozenset = frozenset()) -> dict:
    """Score one candidate against the ghost from the AI-read marks. Rule, not model.

    `common` holds marks read on every frame; they cannot tell the candidates apart, so they are ignored.
    """
    g = {_key(m) for m in ghost if m["kind"] != "other"} - common
    c = {_key(m) for m in candidate if m["kind"] != "other"} - common
    matching = sorted(g & c)
    conflicting: list[str] = []
    for kind in {k[0] for k in g | c}:
        gk, ck = [k for k in g if k[0] == kind], [k for k in c if k[0] == kind]
        if gk and ck and set(gk) != set(ck):
            conflicting.append(f"{kind.replace('_', ' ')}: {_label(*gk[0])} vs {_label(*ck[0])}")
        elif kind in LARGE and gk and not ck:
            conflicting.append(f"{_label(*gk[0])} on the ghost only")
        elif kind in LARGE and ck and not gk and not (ghost_glare and kind in LOW_ON_BODY):
            conflicting.append(f"{_label(*ck[0])} on the candidate only")
    unmatched_small = len([k for k in g ^ c if k[0] in SMALL])
    if conflicting:
        score = 0.05
    else:
        evidence = sum(0.35 if k[0] in LARGE else 0.25 for k in matching)
        score = max(0.0, min(0.95, 0.3 + evidence - 0.05 * unmatched_small))
    return {"score": round(score, 2), "matching": [_label(*k) for k in matching], "conflicting": sorted(conflicting)}


def decide(scores: dict[int, float]) -> tuple[str, int | None]:
    """Recommend only one clear winner. The thresholds are fixed in code."""
    if not scores:
        return "unresolved", None
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    top_index, top = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
    if top >= RECOMMEND_SCORE and top - runner_up >= RECOMMEND_MARGIN:
        return "recommend", top_index
    if top >= REVIEW_SCORE:
        return "review", None
    return "unresolved", None


def _frames_by_label(answer: dict, n: int) -> dict[str, dict]:
    want = ["GHOST"] + [f"CAND {i}" for i in range(1, n + 1)]
    got = {" ".join(f["label"].upper().split()): f for f in answer["frames"]}
    if sorted(got) != sorted(want):
        raise ModelAnswerError("Answer must read every labelled frame exactly once.")
    text = " ".join([answer["notes"], *(f[k] for f in answer["frames"] for k in ("body", "colour", "plate"))]).lower()
    if any(w in text.replace(",", " ").replace(".", " ").split() for w in BLOCKED_WORDS):
        raise ModelAnswerError("Answer used penalty language.")
    return got


def _fingerprint(frame: dict) -> dict:
    return {"body": frame["body"], "colour": frame["colour"], "plate": frame["plate"],
            "marks": [_label(*_key(m)) if m["kind"] != "other" else "other mark" for m in frame["marks"]]}


def match(ghost_frame: bytes, lineup: list[dict], frames: list[bytes], ghost_plate_status: str = "") -> dict:
    """Read all frames with the vision model, then compare fingerprints.

    `lineup` items carry vrn, plaza_id, time, offset_minutes and registered (from VAHAN).
    Returns status "ok", or "unavailable" with a reason. Never raises for model faults.
    """
    base = {"source": "simulated camera frames", "model": os.environ.get("MODEL_NAME"),
            "engine_decision_unchanged": True}
    if not lineup:
        return {**base, "status": "ok", "decision": "unresolved", "named_vrn": None, "ghost": None,
                "candidates": [], "notes": "No time-feasible candidate to compare.", "latency_ms": 0}
    if not is_enabled():
        return {**base, "status": "unavailable", "reason": "Vision model is not configured on this host."}
    lineup, frames = lineup[:MAX_CANDIDATES], frames[:MAX_CANDIDATES]
    user = (f"There are {len(lineup) + 1} images: GHOST, then CAND 1 to CAND {len(lineup)}. "
            "Read every frame.")
    started = perf_counter()
    try:
        answer = complete_json(SYSTEM, user, SCHEMA, images=[ghost_frame, *frames])
        read = _frames_by_label(answer, len(lineup))
    except ModelAnswerError as exc:
        logger.warning("vision match fallback reason=%s", exc)
        return {**base, "status": "unavailable", "reason": str(exc)}
    glare = ghost_plate_status == "glare_weather" or "glare" in read["GHOST"]["plate"].lower()
    common: frozenset = frozenset()
    if len(lineup) >= 2:
        common = frozenset.intersection(*(frozenset(_key(m) for m in f["marks"]) for f in read.values()))
    rows = []
    for i, cand in enumerate(lineup, 1):
        r = compare(read["GHOST"]["marks"], read[f"CAND {i}"]["marks"], glare, common)
        rows.append({"index": i, "vrn": cand["vrn"], **r, "fingerprint": _fingerprint(read[f"CAND {i}"])})
    decision, index = decide({r["index"]: r["score"] for r in rows})
    return {**base, "status": "ok", "decision": decision,
            "named_vrn": lineup[index - 1]["vrn"] if index else None,
            "ghost": _fingerprint(read["GHOST"]), "notes": answer["notes"],
            "ignored_marks": sorted(_label(*k) for k in common),
            "candidates": sorted(rows, key=lambda r: (-r["score"], r["index"])),
            "latency_ms": round((perf_counter() - started) * 1000)}
